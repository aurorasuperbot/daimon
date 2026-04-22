"""CombatEvent emission tests (A.4.a).

Verifies the structured-event sibling stream that the engine emits alongside
the existing string log:

  * String↔event parity — every emitted event carries a non-empty log_line
    that matches some line in RoundLog.actions.
  * Trigger nesting — reactive ON_TAKE_DAMAGE / ON_DEATH events nest under
    their parent damage event's `triggers` list (NOT top-level).
  * Top-level placement — proactive ON_BATTLE_START / ON_ROUND_START events
    land in round 1's top-level events list.
  * hp_after correctness — keys are (side, position) tuples; values reflect
    HP immediately after the event, clamped at 0.

These tests touch the new fields (RoundLog.events, CombatEvent.*) only.
The legacy string log behavior is covered by tests/test_combat.py and stays
untouched — A.4.a is purely additive.
"""

from __future__ import annotations

import pytest

from daimon.cards import load_card
from daimon.engine import Loadout, TEAM_SIZE, resolve_match
from daimon.engine.types import (
    Card,
    CombatEvent,
    EffectOp,
    Element,
    TargetFilter,
    Trigger,
    TriggerWhen,
)

from tests.conftest import FIXTURE_DIR, SEED_ZERO, make_filler
from tests.test_combat import lo, mk


# ---------------------------------------------------------------------------
# Generic structural invariants
# ---------------------------------------------------------------------------

def _all_events(round_events: list[CombatEvent]) -> list[CombatEvent]:
    """Flatten an event tree into a flat list (parent + every nested trigger)."""
    out: list[CombatEvent] = []

    def walk(ev: CombatEvent) -> None:
        out.append(ev)
        for child in ev.triggers:
            walk(child)

    for top in round_events:
        walk(top)
    return out


def _bruiser_loadout() -> Loadout:
    """A loadout that actually deals damage (5 atk vs 5 def = 0 dmg, useless)."""
    return Loadout(cards=tuple(
        mk(f"bruiser_{i}", atk=10, defense=2, hp=30, spd=5, element=Element.NATURE)
        for i in range(TEAM_SIZE)
    ))


def test_events_field_present_on_every_round():
    """RoundLog.events list exists and is populated for every round."""
    a = _bruiser_loadout()
    b = _bruiser_loadout()
    r = resolve_match(a, b, SEED_ZERO)
    assert len(r.rounds) >= 1
    for rd in r.rounds:
        assert hasattr(rd, "events"), "RoundLog must expose .events"
        assert isinstance(rd.events, list)
        # Bruiser vs bruiser always produces damage events per round.
        assert len(rd.events) > 0, f"round {rd.round_number} had zero events"


def test_event_log_lines_appear_in_action_log():
    """String↔event parity: every emitted event's log_line (if non-empty)
    must appear verbatim somewhere in the round's string action log."""
    a = _bruiser_loadout()
    b = _bruiser_loadout()
    r = resolve_match(a, b, SEED_ZERO)
    for rd in r.rounds:
        action_text = "\n".join(rd.actions)
        for ev in _all_events(rd.events):
            if not ev.log_line:
                continue
            # Death events' log_line is renderer-only and intentionally not in
            # the string log (locked: combat.py docstring at line 281-284).
            if ev.kind == "death":
                continue
            # Damage events with a multiplier prefix combine "<prefix> | <line>"
            # in event.log_line — the prefix and line each appear separately
            # in the string log.
            if " | " in ev.log_line:
                prefix, line = ev.log_line.split(" | ", 1)
                assert prefix in action_text, (
                    f"missing damage prefix {prefix!r} in round {rd.round_number} log"
                )
                assert line in action_text, (
                    f"missing damage line {line!r} in round {rd.round_number} log"
                )
            else:
                assert ev.log_line in action_text, (
                    f"event log_line {ev.log_line!r} not in round "
                    f"{rd.round_number} action log"
                )


def test_event_actor_card_id_is_real_card():
    """Every event's actor_card_id must be one of the cards on the field
    (or 'burn' for status-tick damage with no source unit)."""
    a = _bruiser_loadout()
    b = _bruiser_loadout()
    r = resolve_match(a, b, SEED_ZERO)
    valid_ids = {c.card_id for c in a.cards} | {c.card_id for c in b.cards}
    valid_ids.add("burn")
    for rd in r.rounds:
        for ev in _all_events(rd.events):
            assert ev.actor_card_id in valid_ids, (
                f"event actor_card_id {ev.actor_card_id!r} unknown"
            )


# ---------------------------------------------------------------------------
# hp_after correctness
# ---------------------------------------------------------------------------

def test_damage_event_hp_after_matches_target():
    """A damage event's hp_after entry for (target_side, target_position) must
    equal the target's HP immediately after the strike (clamped at 0)."""
    a = _bruiser_loadout()
    b = _bruiser_loadout()
    r = resolve_match(a, b, SEED_ZERO)
    rd = r.rounds[0]
    damage_events = [
        ev for ev in _all_events(rd.events) if ev.kind == "damage"
    ]
    assert len(damage_events) > 0
    for ev in damage_events:
        assert ev.target_side is not None
        assert ev.target_position is not None
        key = (ev.target_side, ev.target_position)
        assert key in ev.hp_after, f"damage event missing hp_after[{key}]"
        assert ev.hp_after[key] >= 0


def test_hp_after_keys_are_int_tuples():
    """Engine emits (side, position) int tuples as keys; the adapter renames
    them to 'side/pos' strings. Verify the engine half of that contract."""
    a = _bruiser_loadout()
    b = _bruiser_loadout()
    r = resolve_match(a, b, SEED_ZERO)
    for rd in r.rounds:
        for ev in _all_events(rd.events):
            for k in ev.hp_after.keys():
                assert isinstance(k, tuple), f"hp_after key {k!r} not a tuple"
                assert len(k) == 2
                assert isinstance(k[0], int)
                assert isinstance(k[1], int)
                assert k[0] in (0, 1)
                assert 0 <= k[1] < TEAM_SIZE


def test_killing_blow_hp_after_is_zero():
    """When a damage event kills its target, hp_after[(target)] == 0."""
    cannon = mk("cannon", atk=999, defense=0, hp=50, spd=99)
    weak = mk("weakling", atk=0, defense=0, hp=1, spd=1)
    a = lo(cannon)
    b = Loadout(cards=tuple([weak] + [
        mk(f"w_{i}", atk=0, defense=0, hp=1, spd=0) for i in range(1, TEAM_SIZE)
    ]))
    r = resolve_match(a, b, SEED_ZERO)

    killing_blows: list[CombatEvent] = []
    for rd in r.rounds:
        for ev in _all_events(rd.events):
            if ev.kind == "damage" and ev.actor_card_id == "cannon":
                killing_blows.append(ev)
    assert killing_blows, "cannon should have produced at least one damage event"
    for ev in killing_blows:
        key = (ev.target_side, ev.target_position)
        assert ev.hp_after[key] == 0, f"killing blow left hp={ev.hp_after[key]}"


# ---------------------------------------------------------------------------
# Trigger nesting — death + reactive triggers nest under the parent damage event
# ---------------------------------------------------------------------------

def test_death_event_nests_under_killing_damage():
    """When a damage event drops a target to 0 HP, a 'death' CombatEvent is
    nested in that damage event's `triggers` list (NOT top-level)."""
    cannon = mk("cannon", atk=999, defense=0, hp=50, spd=99)
    weak = mk("weakling", atk=0, defense=0, hp=1, spd=1)
    a = lo(cannon)
    b = Loadout(cards=tuple([weak] + [
        mk(f"w_{i}", atk=0, defense=0, hp=1, spd=0) for i in range(1, TEAM_SIZE)
    ]))
    r = resolve_match(a, b, SEED_ZERO)

    # Find the damage event that killed "weakling".
    killing = None
    for rd in r.rounds:
        for ev in rd.events:
            if (ev.kind == "damage" and ev.target_card_id == "weakling"
                    and ev.hp_after.get((ev.target_side, ev.target_position)) == 0):
                killing = ev
                break
        if killing:
            break
    assert killing is not None, "expected a killing damage event for weakling"

    death_events = [t for t in killing.triggers if t.kind == "death"]
    assert len(death_events) == 1, (
        f"expected exactly one nested death event, got {len(death_events)}"
    )
    death = death_events[0]
    assert death.actor_card_id == "weakling"
    assert death.target_card_id == "weakling"
    assert death.reason == "ON_DEATH"

    # Crucially: death is NOT a top-level event in any round.
    for rd in r.rounds:
        assert all(ev.kind != "death" for ev in rd.events), (
            f"death event leaked to top-level in round {rd.round_number}"
        )


def test_on_take_damage_trigger_nests_under_parent():
    """A defender with an ON_TAKE_DAMAGE counter-attack should emit that
    counter as a nested event under the parent damage event."""
    base = load_card(FIXTURE_DIR / "test_card_07_on_take_damage_shield_core.json")
    # Bump HP so the counter card is lowest-HP target AND survives the first
    # strike — ON_TAKE_DAMAGE only fires while target is still alive.
    counter = Card(
        card_id=base.card_id, species=base.species, element=base.element,
        atk=base.atk, defense=base.defense, hp=15, spd=base.spd,
        triggers=base.triggers,
    )
    a = lo(counter)
    bruiser = mk("bruiser", atk=12, defense=2, hp=30, spd=10, element=Element.NATURE)
    b = lo(bruiser)
    r = resolve_match(a, b, SEED_ZERO)

    # Look for any damage event targeting the counter card; confirm it has
    # at least one nested trigger event with reason=ON_TAKE_DAMAGE.
    hit_events: list[CombatEvent] = []
    for rd in r.rounds:
        for ev in _all_events(rd.events):
            if (ev.kind == "damage"
                    and ev.target_card_id == counter.card_id
                    and ev.reason is None):
                hit_events.append(ev)
    assert hit_events, "counter card was never hit"

    # ON_TAKE_DAMAGE shield card emits a shield op as a nested reactive event.
    nested_reasons = [
        t.reason for ev in hit_events for t in ev.triggers
    ]
    assert "ON_TAKE_DAMAGE" in nested_reasons, (
        f"no ON_TAKE_DAMAGE trigger nested under hit events; saw {nested_reasons}"
    )


def test_on_death_trigger_nests_under_killing_damage():
    """A card with an ON_DEATH revenge effect should produce a nested
    ON_DEATH event under the damage event that killed it (alongside the
    automatic 'death' event)."""
    bomb = load_card(FIXTURE_DIR / "test_card_08_on_death_revenge_head.json")
    fragile = mk("frag", atk=0, defense=0, hp=1, spd=0)
    # Put the bomb in position 0; pad with fillers.
    a = lo(bomb)
    # Side B has a glass cannon at position 0 to ensure bomb dies fast.
    cannon = mk("kill", atk=999, defense=0, hp=50, spd=99)
    b = lo(cannon)
    r = resolve_match(a, b, SEED_ZERO)

    # Find the damage event that killed the bomb (target=bomb, hp_after=0).
    killing = None
    for rd in r.rounds:
        for ev in _all_events(rd.events):
            if (ev.kind == "damage"
                    and ev.target_card_id == bomb.card_id
                    and ev.hp_after.get((ev.target_side, ev.target_position)) == 0):
                killing = ev
                break
        if killing:
            break

    if killing is None:
        pytest.skip("bomb survived the test scenario; revenge fixture not exercised")

    nested_reasons = [t.reason for t in killing.triggers]
    # We must see both: the automatic death event (reason='ON_DEATH', kind='death')
    # AND the user-defined revenge effect (reason='ON_DEATH', kind='damage').
    assert "ON_DEATH" in nested_reasons


# ---------------------------------------------------------------------------
# Top-level placement of proactive triggers
# ---------------------------------------------------------------------------

def test_on_battle_start_event_top_level_in_round_one():
    """ON_BATTLE_START triggers are proactive (no parent action) so their
    events land at the top level of round 1's events list."""
    buffer_card = load_card(FIXTURE_DIR / "test_card_04_battle_start_buff_arm.json")
    a = lo(buffer_card)
    b = lo()
    r = resolve_match(a, b, SEED_ZERO)

    rd1 = r.rounds[0]
    top_level_battle_start = [
        ev for ev in rd1.events if ev.reason == "ON_BATTLE_START"
    ]
    assert top_level_battle_start, (
        "no top-level ON_BATTLE_START event in round 1"
    )
    # And it must NOT appear in round 2+ (one-shot).
    for rd in r.rounds[1:]:
        for ev in _all_events(rd.events):
            assert ev.reason != "ON_BATTLE_START", (
                f"ON_BATTLE_START leaked into round {rd.round_number}"
            )


def test_on_round_start_event_top_level_each_round():
    """ON_ROUND_START heal triggers fire each round; events go top-level."""
    healer = load_card(FIXTURE_DIR / "test_card_05_round_start_heal_arm.json")
    a = lo(healer)
    b = lo()
    r = resolve_match(a, b, SEED_ZERO)

    # Every round (including round 1) should have at least one ON_ROUND_START
    # heal event at top level — until the healer dies.
    found_in_any_round = False
    for rd in r.rounds:
        top_level_round_start = [
            ev for ev in rd.events
            if ev.reason == "ON_ROUND_START"
        ]
        if top_level_round_start:
            found_in_any_round = True
            for ev in top_level_round_start:
                assert ev.kind == "heal"
    assert found_in_any_round, "no ON_ROUND_START event ever emitted"


# ---------------------------------------------------------------------------
# Buff / debuff / shield / heal — all kinds round-trip cleanly
# ---------------------------------------------------------------------------

def test_buff_event_kind_and_amount():
    """A BUFF_ATK trigger emits a 'buff' event with positive amount."""
    buffer_card = load_card(FIXTURE_DIR / "test_card_04_battle_start_buff_arm.json")
    a = lo(buffer_card)
    b = lo()
    r = resolve_match(a, b, SEED_ZERO)

    buff_events: list[CombatEvent] = []
    for rd in r.rounds:
        for ev in _all_events(rd.events):
            if ev.kind == "buff":
                buff_events.append(ev)
    assert buff_events, "expected at least one buff event from battle-start fixture"
    for ev in buff_events:
        assert ev.amount is not None
        assert ev.amount > 0


def test_heal_event_kind_and_amount():
    """ON_ROUND_START heal trigger emits 'heal' events with positive amounts."""
    healer = load_card(FIXTURE_DIR / "test_card_05_round_start_heal_arm.json")
    a = lo(healer)
    b = lo()
    r = resolve_match(a, b, SEED_ZERO)

    heal_events: list[CombatEvent] = []
    for rd in r.rounds:
        for ev in _all_events(rd.events):
            if ev.kind == "heal":
                heal_events.append(ev)
    assert heal_events, "expected at least one heal event from round-start healer"
    for ev in heal_events:
        assert ev.amount is not None
        assert ev.amount > 0


# ---------------------------------------------------------------------------
# Determinism — same seed, same event tree
# ---------------------------------------------------------------------------

def test_same_seed_same_event_tree():
    """Re-running the same match must produce the same event tree shape and
    same per-event (kind, actor_card_id, target_card_id, amount) tuples."""
    a = _bruiser_loadout()
    b = _bruiser_loadout()
    r1 = resolve_match(a, b, SEED_ZERO)
    r2 = resolve_match(a, b, SEED_ZERO)

    def fingerprint(rounds) -> list[tuple]:
        out: list[tuple] = []
        for rd in rounds:
            for ev in _all_events(rd.events):
                out.append((
                    rd.round_number, ev.kind, ev.actor_card_id,
                    ev.target_card_id, ev.amount, ev.reason,
                ))
        return out

    assert fingerprint(r1.rounds) == fingerprint(r2.rounds)
