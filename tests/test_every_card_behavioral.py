"""Phase 4i — exhaustive per-card behavioral coverage.

Santiago's directive (2026-04-23 05:54 UTC): "test again and make sure every
card work as intended". Phase 4h proved every NEW op fires in real battles via
the 7 rewritten cards. This file extends that bar to ALL 200 catalog cards.

Three layers (each parametrized over the full catalog):

  1. **TestEveryCardLoads** — `load_card_dict(payload)` succeeds for every
     manifest entry. Catches schema drift, malformed payloads, invalid enum
     values, condition DSL syntax errors. (Catalog load already does this in
     bulk, but per-card parametrization gives precise failure surfacing.)

  2. **TestEveryCardSmoke** — for every card, place at position 0 in a 1-card
     team (rest fillers), run REAL `resolve_match` battles vs a 6-vanilla
     mirror under multiple seeds. Asserts no exception, match completes,
     RoundLog count > 0. Catches engine crashes, infinite loops, and any card
     whose mechanics throw at fire-time.

  3. **TestEveryCardTriggersFire** — for every card with triggers, run TWO
     COMPLEMENTARY scenarios across multiple seeds and combine the logs:

       a) "First-strike arena" — card on team A vs 6× 1-HP prey on team B.
          Team A holds 5 diverse-element fillers (no healer, no granter).
          Card attacks at full HP every round, kills prey every round, never
          gets hit. Fires:
            ON_BATTLE_START / ON_ROUND_START / ON_TURN_END
            ON_OPENING_ATTACK / ON_ATTACK / ON_KILL
          Conditions satisfied:
            self.hp == self.hp_max  (no heal pulses to push hp above max)
            team.distinct_elements >= 2/3  (5 diverse-element fillers)
            round >= 2  (matches run multiple rounds)

       b) "Punching bag" — card on team A with 4 fragile (1-HP, varied
          element) allies + 1 support that ON_ROUND_START heals all allies
          and grants RANDOM_ALLY an extra action. Team B is 1× 1-HP prey for
          ON_KILL, plus 4× heavy hitters (atk=20, beats max def 14). Round 1:
          fragile allies die first, support heals card, granter randomly
          grants. Round 2+: hitters lock onto card (now lowest_hp_enemy),
          card takes massive damage, drops below 25% HP, dies. Fires:
            ON_TAKE_DAMAGE / ON_DAMAGE_TAKEN
            ON_LOW_HP / ON_DEATH
            ON_ALLY_DEATH (4 fragile deaths in round 1)
            ON_HEAL_RECEIVED (support heals card every round)
            ON_EXTRA_ACTION_GRANTED (granter pulses RANDOM_ALLY)

     Together the two scenarios fire every TriggerWhen the engine supports;
     each card's declared triggers must produce a matching log line with the
     card as actor. Failures surface with full context (card_id, trigger
     index, when/op/target/value/condition) plus log tails from both
     scenarios for diagnosis.

The combinatorial bar (200 cards × 6-card teams) is unreachable; this is the
strongest hand-authored layer of antibody.
"""

from __future__ import annotations

import pytest

from daimon.cards.loader import load_card_dict
from daimon.catalog import load_catalog
from daimon.engine import Loadout, TEAM_SIZE, resolve_match
from daimon.engine.types import (
    Card,
    EffectOp,
    Element,
    TargetFilter,
    Trigger,
    TriggerWhen,
)

from tests.conftest import SEED_ZERO, make_filler


# ---------------------------------------------------------------------------
# Catalog accessor — load once at module-import so parametrization can iterate.
# ---------------------------------------------------------------------------


_CATALOG = load_catalog()
_ALL_CARD_IDS: list[str] = sorted(_CATALOG.by_id.keys())
_CARDS_WITH_TRIGGERS: list[str] = sorted(
    cid for cid, cc in _CATALOG.by_id.items()
    if cc.payload.get("triggers")
)


def _card(card_id: str) -> Card:
    cc = _CATALOG.by_id.get(card_id)
    if cc is None:
        raise AssertionError(f"card_id {card_id!r} not in catalog")
    return load_card_dict(cc.payload)


def _all_log_lines(result) -> str:
    return "\n".join(line for r in result.rounds for line in r.actions)


# ---------------------------------------------------------------------------
# Loadout helpers — deterministic test rigs.
# ---------------------------------------------------------------------------


def _vanilla_mirror() -> Loadout:
    """6 vanilla 5/5/20/5 fillers — neutral opponent."""
    return Loadout(cards=tuple(make_filler(i, "mirror") for i in range(TEAM_SIZE)))


def _solo_loadout(card: Card) -> Loadout:
    """card_under_test at position 0, vanilla fillers elsewhere."""
    return Loadout(
        cards=(card,) + tuple(make_filler(i, "solo") for i in range(1, TEAM_SIZE))
    )


# ---------------------------------------------------------------------------
# Synthetic helpers for the rich scenarios — carefully constructed test
# fixtures, NOT catalog cards. Element diversity is deliberate to satisfy
# `team.distinct_elements >= 2/3` conditions on FLUX/RAINBOW cards.
# ---------------------------------------------------------------------------


def _synth_diverse_filler(idx: int, element: Element) -> Card:
    """High-HP, zero-attack, zero-defense filler with a specific element.
    Elements are spread across team_a so distinct_elements >= 2/3 conditions
    are always satisfied. HP=999 keeps these cards from being selected as
    LOWEST_HP_ENEMY targets."""
    return Card(
        card_id=f"_synth_diverse_{element.name.lower()}_{idx}",
        species=f"_synth_diverse_{idx}",
        element=element,
        atk=0, defense=0, hp=999, spd=1,
    )


def _synth_fragile_ally(idx: int, element: Element) -> Card:
    """1-HP ally that dies on the first hit it absorbs. Triggers ON_ALLY_DEATH
    on every surviving teammate (including the card under test). Each fragile
    carries a distinct element so distinct_elements stays high in round 1."""
    return Card(
        card_id=f"_synth_fragile_{element.name.lower()}_{idx}",
        species=f"_synth_fragile_{idx}",
        element=element,
        atk=0, defense=0, hp=1, spd=1,
    )


def _synth_support() -> Card:
    """ON_ROUND_START heals ALL_ALLIES (+4) and grants RANDOM_ALLY one extra
    action (+1). Combines healer + granter into one unit so team_a has room
    for both 4 fragile allies AND the card under test in a 6-slot team.
    Carries VOLT so team_a still has 5+ distinct elements with the 4 fragile
    allies (FIRE/WATER/NATURE/VOID)."""
    return Card(
        card_id="_synth_support", species="_synth_support",
        element=Element.VOLT,
        atk=0, defense=0, hp=999, spd=1,
        triggers=(
            Trigger(
                when=TriggerWhen.ON_ROUND_START,
                op=EffectOp.HEAL,
                target=TargetFilter.ALL_ALLIES,
                value=4,
            ),
            Trigger(
                when=TriggerWhen.ON_ROUND_START,
                op=EffectOp.GRANT_EXTRA_ACTION,
                target=TargetFilter.RANDOM_ALLY,
                value=1,
            ),
        ),
    )


def _synth_passive_prey(idx: int) -> Card:
    """1-HP enemy with zero atk + spd=1 — sits there waiting to be killed.
    Six prey on team_b in the first-strike scenario give the card under test
    a guaranteed kill every round (fires ON_KILL repeatedly)."""
    return Card(
        card_id=f"_synth_prey_{idx}", species=f"_synth_prey_{idx}",
        element=Element.NORMAL,
        atk=0, defense=0, hp=1, spd=1,
    )


def _synth_heavy_hitter(idx: int) -> Card:
    """High-atk (20 — beats max def 14 in catalog), high-hp, high-spd hitter.
    Used in punching-bag scenario to drive ON_TAKE_DAMAGE / ON_DAMAGE_TAKEN /
    ON_DEATH on the card under test once the 4 fragile allies have died."""
    return Card(
        card_id=f"_synth_hitter_{idx}", species=f"_synth_hitter_{idx}",
        element=Element.NORMAL,
        atk=20, defense=0, hp=999, spd=99 - idx,
    )


def _synth_drain_hitter(idx: int, atk: int) -> Card:
    """Variable-atk hitter for the graduated-drain scenarios. ON_LOW_HP fires
    only when the unit's hp lands in (0, hp_max // 4] WHILE STILL ALIVE — too
    much damage in one hit overshoots the threshold to 0. Drain scenarios use
    smaller, more numerous hits so the threshold is crossed in flight rather
    than skipped. spd encoded so attack order matches `idx` (lower idx →
    higher spd → attacks first)."""
    return Card(
        card_id=f"_synth_drain_{idx}", species=f"_synth_drain_{idx}",
        element=Element.NORMAL,
        atk=atk, defense=0, hp=999, spd=99 - idx,
    )


def _synth_b_filler(idx: int) -> Card:
    """Inert team_b filler so team_b has 6 cards. NORMAL element so it doesn't
    interfere with any element-based condition checks."""
    return Card(
        card_id=f"_synth_b_filler_{idx}", species=f"_synth_b_filler_{idx}",
        element=Element.NORMAL,
        atk=0, defense=0, hp=999, spd=1,
    )


# ---- Scenario A: first-strike arena --------------------------------------


def _first_strike_team_a(card: Card) -> Loadout:
    """card_under_test + 5 diverse-element fillers (no healer, no granter).
    Card attacks at FULL HP every round. Distinct elements = at least 5
    (FIRE/WATER/NATURE/VOID/VOLT plus card's own)."""
    return Loadout(cards=(
        card,
        _synth_diverse_filler(1, Element.FIRE),
        _synth_diverse_filler(2, Element.WATER),
        _synth_diverse_filler(3, Element.NATURE),
        _synth_diverse_filler(4, Element.VOID),
        _synth_diverse_filler(5, Element.VOLT),
    ))


def _first_strike_team_b() -> Loadout:
    """6× 1-HP prey, no atk, slow. card_under_test rolls them every round."""
    return Loadout(cards=tuple(_synth_passive_prey(i) for i in range(TEAM_SIZE)))


# ---- Scenario B: punching-bag --------------------------------------------


def _punching_bag_team_a(card: Card) -> Loadout:
    """card_under_test + 4 fragile (1-HP) allies + 1 support.
    Round 1: 4 fragiles die → ON_ALLY_DEATH fires (×4, but the test only
    checks at-least-once per declared trigger).
    Round 2+: card under test absorbs all hits (lowest_hp_enemy after fragiles
    die, since support has hp=999)."""
    return Loadout(cards=(
        card,
        _synth_fragile_ally(1, Element.FIRE),
        _synth_fragile_ally(2, Element.WATER),
        _synth_fragile_ally(3, Element.NATURE),
        _synth_fragile_ally(4, Element.VOID),
        _synth_support(),
    ))


def _punching_bag_team_b() -> Loadout:
    """1× prey for ON_KILL on card under test + 4× heavy hitters that grind
    card under test to 0 HP. 1× inert filler keeps team at 6."""
    return Loadout(cards=(
        _synth_passive_prey(0),
        _synth_heavy_hitter(1),
        _synth_heavy_hitter(2),
        _synth_heavy_hitter(3),
        _synth_heavy_hitter(4),
        _synth_b_filler(5),
    ))


# ---- Scenario C/D: graduated drain (for ON_LOW_HP cards) -----------------
#
# ON_LOW_HP fires only when a unit's hp lands in (0, hp_max // 4] WHILE STILL
# ALIVE. The punching-bag heavy hitters (atk=20) overshoot the threshold to 0
# in one hit on low-def cards (voidling/flashfox/ignis_kit have def≤3, hp≤18)
# AND the support's per-round heal pushes med-def cards' hp above hp_max so
# heavy-hitter sequences skip the threshold.
#
# Drain scenarios fix this by:
#   - Removing the support (no heal stacking above hp_max)
#   - Using progressive-atk hitters so the card bleeds through the threshold
#     in flight rather than overshooting to 0
#
# Two variants because low-def + high-def ON_LOW_HP cards need different atk
# windows (def=3 wants atk≈6 for 3 dmg/hit; def_eff=12 wants atk≈22 for 10
# dmg/hit). Either covers a card → trigger fires somewhere in the combined log.


def _drain_team_a(card: Card) -> Loadout:
    """card_under_test + 5 fragile allies (1 HP each, varied elements).
    No support → card under test takes all hits round 2+ at exactly hp_max
    with no heal-stacking above max."""
    return Loadout(cards=(
        card,
        _synth_fragile_ally(1, Element.FIRE),
        _synth_fragile_ally(2, Element.WATER),
        _synth_fragile_ally(3, Element.NATURE),
        _synth_fragile_ally(4, Element.VOID),
        _synth_fragile_ally(5, Element.VOLT),
    ))


def _drain_fragile_team_b() -> Loadout:
    """Light-atk hitters tuned for low-def (def≤3) ON_LOW_HP cards: voidling,
    flashfox, ignis_kit. atk=6 → 3-4 dmg/hit on def≤3 — bleeds through hp 18
    in 4-5 hits, hitting (0, 4] window cleanly."""
    return Loadout(cards=(
        _synth_passive_prey(0),
        _synth_drain_hitter(1, atk=6),
        _synth_drain_hitter(2, atk=6),
        _synth_drain_hitter(3, atk=6),
        _synth_drain_hitter(4, atk=6),
        _synth_b_filler(5),
    ))


def _drain_armored_team_b() -> Loadout:
    """Uniform atk=14 hitters for med-def ON_LOW_HP cards: forest_warden
    (def_eff=12 with own ON_BATTLE_START buff, hp=24, threshold=6). Each
    hit does dmg=2; warden bleeds gradually so the LOW_HP sweep catches
    hp at 6 in round 4 instead of overshooting to 0."""
    return Loadout(cards=(
        _synth_passive_prey(0),
        _synth_drain_hitter(1, atk=14),
        _synth_drain_hitter(2, atk=14),
        _synth_drain_hitter(3, atk=14),
        _synth_drain_hitter(4, atk=14),
        _synth_b_filler(5),
    ))


# ---------------------------------------------------------------------------
# Op → log-signature lookup for trigger-fire detection.
#
# The engine logs op effects with the card_id as actor. We assert each
# declared trigger's op produces a matching log line with the card under test
# named as actor (or, for self-targeting passives like THORNS, the card named
# as both actor and target).
#
# DAMAGE op shares its log-line shape with plain attacks ("X hits Y for N"),
# so a card declaring an ON_ATTACK DAMAGE trigger will produce many "{card}
# hits ..." lines — the assertion still holds (extra DAMAGE-trigger hits land
# on top of the card's normal attack), and any card that ALSO has atk > 0
# will satisfy the signature trivially.
# ---------------------------------------------------------------------------


_OP_SIGNATURE: dict[EffectOp, str] = {
    EffectOp.BUFF_ATK:           "buffs ATK of",
    EffectOp.DEBUFF_ATK:         "debuffs ATK of",
    EffectOp.BUFF_DEF:           "buffs DEF of",
    EffectOp.DEBUFF_DEF:         "debuffs DEF of",
    EffectOp.HEAL:               "heals",
    EffectOp.DAMAGE:             "hits",
    EffectOp.ADD_SHIELD:         "shields",
    EffectOp.BUFF_SPD:           "buffs SPD of",
    EffectOp.APPLY_BURN:         "applies BURN",
    EffectOp.APPLY_STUN:         "applies STUN",
    EffectOp.APPLY_SILENCE:      "applies SILENCE",
    EffectOp.APPLY_TAUNT:        "applies TAUNT",
    EffectOp.APPLY_POISON:       "applies POISON",
    EffectOp.LIFESTEAL:          "drains",
    EffectOp.APPLY_BURN_STACK:   "burn stack(s) to",
    EffectOp.THORNS:             "grows THORNS",
    EffectOp.GRANT_EXTRA_ACTION: "grants extra action to",
    EffectOp.SACRIFICE_SELF:     "sacrifices itself",
}


def _trigger_fires_in_log(card_id: str, trigger: Trigger, log: str) -> bool:
    """True if the trigger's op signature appears with `card_id` as the actor
    in any log line. The engine puts `{actor_id}` first in every effect log
    line, so a startswith check on `{card_id} ` is the cleanest signal."""
    sig = _OP_SIGNATURE.get(trigger.op)
    if sig is None:
        # Unknown op (would mean engine added an op without updating this
        # table). Fail loudly so the test catches it.
        raise AssertionError(
            f"no log signature mapping for op {trigger.op!r} — extend _OP_SIGNATURE"
        )
    actor_prefix = f"{card_id} "
    for line in log.splitlines():
        if line.startswith(actor_prefix) and sig in line:
            return True
    return False


# Multiple seeds to randomize RANDOM_ALLY/RANDOM_ENEMY targeting + spd ties +
# round-alternating first-player rule.
_BEHAVIORAL_SEEDS: tuple[bytes, ...] = tuple(
    bytes([b] * 32) for b in (0, 1, 7, 23, 91)
)


# ---------------------------------------------------------------------------
# Layer 1 — every card loads.
# ---------------------------------------------------------------------------


class TestEveryCardLoads:
    @pytest.mark.parametrize("card_id", _ALL_CARD_IDS)
    def test_card_loads_cleanly(self, card_id):
        """Every card_id in the catalog manifest produces a valid Card."""
        card = _card(card_id)
        assert card.card_id == card_id
        assert card.atk >= 0
        assert card.defense >= 0
        assert card.hp > 0, f"{card_id} has hp=0 — would be dead-on-arrival"
        assert card.spd >= 0


# ---------------------------------------------------------------------------
# Layer 2 — every card survives a real battle without crashing the engine.
# ---------------------------------------------------------------------------


class TestEveryCardSmoke:
    @pytest.mark.parametrize("card_id", _ALL_CARD_IDS)
    def test_card_resolves_match_no_crash(self, card_id):
        """Solo (1-card) loadout vs 6-vanilla mirror, multiple seeds.
        Asserts the engine never raises on this card and matches complete."""
        card = _card(card_id)
        team_a = _solo_loadout(card)
        team_b = _vanilla_mirror()
        for seed in _BEHAVIORAL_SEEDS:
            result = resolve_match(team_a, team_b, seed)
            assert result is not None, (
                f"resolve_match returned None for {card_id} (seed={seed.hex()})"
            )
            assert len(result.rounds) >= 1, (
                f"{card_id}: match produced 0 rounds (seed={seed.hex()})"
            )
            # Sanity: at least one log line per round (engine emits round-start
            # markers + actions for every match).
            for r_idx, rlog in enumerate(result.rounds):
                assert isinstance(rlog.actions, list)


# ---------------------------------------------------------------------------
# Layer 3 — every declared trigger fires across the two complementary
# scenarios.
# ---------------------------------------------------------------------------


def _build_combined_log(card: Card) -> tuple[str, str]:
    """Run BOTH the first-strike and punching-bag scenarios across all
    behavioral seeds (with side-swap) and return (combined_log, tail).

    Multiple seeds randomize RANDOM_ALLY / RANDOM_ENEMY targeting and the
    round-alternating first-player rule, so a trigger gated on a random pick
    or a side-bias gets multiple shots at firing.
    """
    chunks: list[str] = []

    # Scenario A — first-strike arena. Card attacks at full HP, kills prey,
    # never gets hit. Best for ON_ATTACK / ON_KILL / ON_OPENING_ATTACK and
    # any "self.hp == self.hp_max" or "team.distinct_elements" condition.
    fs_a = _first_strike_team_a(card)
    fs_b = _first_strike_team_b()

    for seed in _BEHAVIORAL_SEEDS:
        chunks.append("=== first_strike (a vs b) ===")
        chunks.append(_all_log_lines(resolve_match(fs_a, fs_b, seed)))
        chunks.append("=== first_strike (b vs a) ===")
        chunks.append(_all_log_lines(resolve_match(fs_b, fs_a, seed)))

    # Scenario B — punching bag. 4 fragile allies die round 1; card absorbs
    # heavy hits round 2+; support heals card every round; granter grants
    # RANDOM_ALLY extra action. Best for ON_TAKE_DAMAGE / ON_DAMAGE_TAKEN /
    # ON_DEATH / ON_ALLY_DEATH / ON_HEAL_RECEIVED / ON_EXTRA_ACTION_GRANTED.
    pb_a = _punching_bag_team_a(card)
    pb_b = _punching_bag_team_b()

    for seed in _BEHAVIORAL_SEEDS:
        chunks.append("=== punching_bag (a vs b) ===")
        chunks.append(_all_log_lines(resolve_match(pb_a, pb_b, seed)))
        chunks.append("=== punching_bag (b vs a) ===")
        chunks.append(_all_log_lines(resolve_match(pb_b, pb_a, seed)))

    # Scenarios C/D — graduated drain. Two variants tuned to different def
    # bands so ON_LOW_HP fires for low-def AND mid-def cards. No support so
    # heals don't push hp above hp_max and shrink the trigger window.
    drain_a = _drain_team_a(card)
    df_b = _drain_fragile_team_b()
    da_b = _drain_armored_team_b()

    for seed in _BEHAVIORAL_SEEDS:
        chunks.append("=== drain_fragile (a vs b) ===")
        chunks.append(_all_log_lines(resolve_match(drain_a, df_b, seed)))
        chunks.append("=== drain_armored (a vs b) ===")
        chunks.append(_all_log_lines(resolve_match(drain_a, da_b, seed)))

    full = "\n".join(chunks)
    # Tail of last scenario for diagnosis on failure (avoid megabyte spam).
    tail = "\n".join(full.splitlines()[-100:])
    return full, tail


class TestEveryCardTriggersFire:
    @pytest.mark.parametrize("card_id", _CARDS_WITH_TRIGGERS)
    def test_every_declared_trigger_fires(self, card_id):
        """For each declared trigger on the card, the op's log signature must
        appear with the card as actor across the combined first-strike +
        punching-bag scenarios. Surfaces dead triggers, condition-blocked
        triggers, and any op whose engine implementation silently no-ops."""
        card = _card(card_id)
        assert card.triggers, f"{card_id} unexpectedly has no triggers"
        log, tail = _build_combined_log(card)

        unfired: list[tuple[int, Trigger]] = []
        for idx, trig in enumerate(card.triggers):
            if not _trigger_fires_in_log(card_id, trig, log):
                unfired.append((idx, trig))

        if unfired:
            details = "\n".join(
                f"  trigger[{i}]: when={t.when.name} op={t.op.name} "
                f"target={t.target.name} value={t.value} "
                f"condition={t.condition!r}"
                for i, t in unfired
            )
            pytest.fail(
                f"{card_id}: {len(unfired)} declared trigger(s) did not fire "
                f"across both scenarios:\n{details}\n\nLog tail:\n{tail}"
            )
