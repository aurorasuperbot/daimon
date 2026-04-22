"""Engine → Match adapter tests (A.4.b).

Verifies `daimon.play.adapter.match_result_to_match` produces a Match
instance that:

  * Validates against the pydantic schema (extra="forbid" everywhere).
  * Renames sides 0/1 ↔ player/opponent at every seam (CardRef.side,
    Round.first_player, hp_after keys, Outcome.winner).
  * Renames engine Element (IntEnum) to schema Element (str) on every
    LoadoutCard.
  * Carries hp_after entries with "side/pos" string keys built via
    `play.schema.hp_key`.
  * Nests reactive triggers under their parent Action's `triggers` list,
    with action_ids "r{R}_a{A}_t{N}".
  * Synthesizes default display metadata when no overrides are provided.
  * Counts deaths correctly into Outcome.stats.cards_killed.
"""

from __future__ import annotations

import json

import pytest

from daimon.engine import Loadout, TEAM_SIZE, resolve_match
from daimon.engine.types import (
    Card,
    EffectOp,
    Element,
    TargetFilter,
    Trigger,
    TriggerWhen,
)
from daimon.play.adapter import (
    CardDisplay,
    ParticipantInfo,
    engine_element_to_schema,
    match_result_to_match,
    side_int_to_schema,
)
from daimon.play.schema import (
    ActionKind,
    Element as SchemaElement,
    Match,
    Side,
    hp_key,
)

from tests.conftest import SEED_ZERO
from tests.test_combat import lo, mk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bruisers() -> Loadout:
    return Loadout(cards=tuple(
        mk(f"a_{i}", atk=10, defense=2, hp=30, spd=5, element=Element.NATURE)
        for i in range(TEAM_SIZE)
    ))


def _other_bruisers() -> Loadout:
    return Loadout(cards=tuple(
        mk(f"b_{i}", atk=10, defense=2, hp=30, spd=5, element=Element.NATURE)
        for i in range(TEAM_SIZE)
    ))


def _default_player_info() -> ParticipantInfo:
    return ParticipantInfo(name="santiago", rank="Veteran #18", card_displays=())


def _default_opponent_info() -> ParticipantInfo:
    return ParticipantInfo(name="Champion Lyra", rank="Champion", card_displays=())


def _build_match(loadout_a: Loadout, loadout_b: Loadout, seed=SEED_ZERO) -> Match:
    result = resolve_match(loadout_a, loadout_b, seed)
    return match_result_to_match(
        result, loadout_a, loadout_b,
        match_id="testmatch",
        player=_default_player_info(),
        opponent=_default_opponent_info(),
        timestamp="2026-04-22T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def test_side_int_to_schema():
    assert side_int_to_schema(0) is Side.PLAYER
    assert side_int_to_schema(1) is Side.OPPONENT
    with pytest.raises(ValueError):
        side_int_to_schema(2)


def test_engine_element_to_schema_round_trip():
    for elem in Element:
        sch = engine_element_to_schema(elem)
        assert sch.name == elem.name
        assert isinstance(sch, SchemaElement)


# ---------------------------------------------------------------------------
# Schema validity
# ---------------------------------------------------------------------------

def test_match_validates_via_json_round_trip():
    """Adapter output must serialize to JSON and re-parse via Match.model_validate.

    This is the strongest check that we respect every `extra="forbid"` config
    in the schema — a stray field anywhere would blow up here.
    """
    m = _build_match(_bruisers(), _other_bruisers())
    payload = json.loads(m.model_dump_json())
    rebuilt = Match.model_validate(payload)
    assert rebuilt.match_id == "testmatch"
    assert rebuilt.schema_version == 2
    assert rebuilt.event_type == "match"


def test_participants_have_six_loadout_cards_each():
    m = _build_match(_bruisers(), _other_bruisers())
    assert set(m.participants.keys()) == {"player", "opponent"}
    assert len(m.participants["player"].loadout) == TEAM_SIZE
    assert len(m.participants["opponent"].loadout) == TEAM_SIZE
    # Positions cover the full 0..5 range without gaps.
    for side_key in ("player", "opponent"):
        positions = sorted(c.position for c in m.participants[side_key].loadout)
        assert positions == list(range(TEAM_SIZE))


# ---------------------------------------------------------------------------
# Side and key renaming
# ---------------------------------------------------------------------------

def test_round_first_player_rename():
    """Round 1 → side A first → 'player'; Round 2 → side B first → 'opponent'."""
    m = _build_match(_bruisers(), _other_bruisers())
    assert len(m.rounds) >= 2
    assert m.rounds[0].first_player == Side.PLAYER
    assert m.rounds[1].first_player == Side.OPPONENT


def test_action_actor_target_sides_renamed():
    m = _build_match(_bruisers(), _other_bruisers())
    for rd in m.rounds:
        for action in rd.actions:
            assert action.actor.side in (Side.PLAYER, Side.OPPONENT)
            if action.target is not None:
                assert action.target.side in (Side.PLAYER, Side.OPPONENT)


def test_hp_after_keys_use_side_slash_position_format():
    """Keys must be exactly 'player/N' or 'opponent/N' with N ∈ 0..5."""
    m = _build_match(_bruisers(), _other_bruisers())
    for rd in m.rounds:
        for action in rd.actions:
            for k in action.hp_after.keys():
                assert k.startswith(("player/", "opponent/"))
                side, pos = k.split("/")
                assert side in ("player", "opponent")
                assert pos.isdigit()
                assert 0 <= int(pos) < TEAM_SIZE


def test_hp_key_helper_round_trip():
    """Spot-check the canonical key helper format (sanity check on schema)."""
    assert hp_key(Side.PLAYER, 0) == "player/0"
    assert hp_key(Side.OPPONENT, 5) == "opponent/5"


# ---------------------------------------------------------------------------
# Trigger nesting
# ---------------------------------------------------------------------------

def test_nested_triggers_get_t_suffix_action_ids():
    """A damage Action that kills its target should carry a nested 'death'
    Action with action_id ending in '_t1'."""
    cannon = mk("cannon", atk=999, defense=0, hp=50, spd=99,
                element=Element.NATURE)
    weak = mk("weakling", atk=0, defense=0, hp=1, spd=1,
              element=Element.NATURE)
    a = lo(cannon)
    b = Loadout(cards=tuple([weak] + [
        mk(f"w_{i}", atk=0, defense=0, hp=1, spd=0) for i in range(1, TEAM_SIZE)
    ]))
    m = _build_match(a, b)

    # Find the killing damage action against weakling.
    killing = None
    for rd in m.rounds:
        for action in rd.actions:
            if (action.kind == ActionKind.DAMAGE
                    and action.target is not None
                    and action.target.card.lower().startswith("weakling")):
                killing = action
                break
        if killing:
            break
    assert killing is not None
    death_triggers = [t for t in killing.triggers if t.kind == ActionKind.DEATH]
    assert len(death_triggers) == 1
    death = death_triggers[0]
    assert death.action_id.endswith("_t1")
    assert death.action_id.startswith(killing.action_id + "_t")
    assert death.reason == "ON_DEATH"


def test_top_level_action_ids_have_no_t_suffix():
    """Top-level action IDs are 'r{round}_a{idx}' — never with '_t'."""
    m = _build_match(_bruisers(), _other_bruisers())
    for rd in m.rounds:
        for idx, action in enumerate(rd.actions, start=1):
            assert action.action_id == f"r{rd.round}_a{idx}"
            assert "_t" not in action.action_id


# ---------------------------------------------------------------------------
# Display metadata
# ---------------------------------------------------------------------------

def test_default_display_synthesizes_titlecased_name():
    """No overrides → species 'a_0' becomes display name 'A 0'."""
    m = _build_match(_bruisers(), _other_bruisers())
    player_card_0 = m.participants["player"].loadout[0]
    # bruisers() species is "a_0", "a_1", ...
    assert player_card_0.species == "a_0"
    assert player_card_0.name == "A 0"


def test_display_overrides_take_precedence():
    a = _bruisers()
    b = _other_bruisers()
    result = resolve_match(a, b, SEED_ZERO)
    player_info = ParticipantInfo(
        name="santiago", rank="Veteran #18",
        card_displays=tuple(
            CardDisplay(name=f"Custom {i}", short_name=f"Cust{i}", rarity="rare")
            for i in range(TEAM_SIZE)
        ),
    )
    m = match_result_to_match(
        result, a, b,
        match_id="ovr",
        player=player_info,
        opponent=_default_opponent_info(),
        timestamp="2026-04-22T00:00:00Z",
    )
    pl = m.participants["player"].loadout
    for i, card in enumerate(pl):
        assert card.name == f"Custom {i}"
        assert card.short_name == f"Cust{i}"
        assert card.rarity == "rare"


def test_loadout_card_element_renamed_to_schema_str_enum():
    m = _build_match(_bruisers(), _other_bruisers())
    for card in m.participants["player"].loadout:
        assert isinstance(card.element, SchemaElement)
        assert card.element == SchemaElement.NATURE


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

def test_winner_player_when_side_a_wins():
    """Side A is six cannons vs side B six weaklings — A wins by total wipe."""
    a = Loadout(cards=tuple(
        mk(f"a_{i}", atk=999, defense=0, hp=50, spd=99) for i in range(TEAM_SIZE)
    ))
    b = Loadout(cards=tuple(
        mk(f"b_{i}", atk=0, defense=0, hp=1, spd=0) for i in range(TEAM_SIZE)
    ))
    m = _build_match(a, b)
    assert m.outcome.winner == Side.PLAYER
    # All 6 opponent cards died (one per cannon strike).
    assert m.outcome.stats.cards_killed["opponent"] == TEAM_SIZE


def test_winner_opponent_when_side_b_wins():
    a = Loadout(cards=tuple(
        mk(f"a_{i}", atk=0, defense=0, hp=1, spd=0) for i in range(TEAM_SIZE)
    ))
    b = Loadout(cards=tuple(
        mk(f"b_{i}", atk=999, defense=0, hp=50, spd=99) for i in range(TEAM_SIZE)
    ))
    m = _build_match(a, b)
    assert m.outcome.winner == Side.OPPONENT
    assert m.outcome.stats.cards_killed["player"] == TEAM_SIZE


def test_winner_draw_does_not_collapse_to_player():
    """Regression: draws used to be silently rewritten as PLAYER wins because
    the schema required a Side. The schema now has Side.DRAW; the adapter
    routes engine winner=None there. Without this fix every draw inflated
    the player's win column."""
    # Two identical bruiser teams + the deterministic seed = mirror match,
    # which the engine resolves as a draw (HP-equal, winner=None).
    a = _bruisers()
    b = _bruisers()
    m = _build_match(a, b)
    # If the engine resolved this as a true draw, the adapter MUST emit
    # Side.DRAW — never Side.PLAYER as a placeholder.
    if m.outcome.player_hp_remaining == m.outcome.opponent_hp_remaining:
        assert m.outcome.winner == Side.DRAW, (
            f"draw was silently relabeled as {m.outcome.winner!r} — "
            "this is the bug we're regressing against"
        )
        # Draw outcomes must not pay rewards.
        assert m.outcome.rewards.currency == 0
        assert m.outcome.rewards.rank_delta == "+0"


def test_outcome_round_count_matches_engine():
    m = _build_match(_bruisers(), _other_bruisers())
    assert m.outcome.stats.round_count == len(m.rounds)


def test_seed_serialized_as_hex():
    m = _build_match(_bruisers(), _other_bruisers())
    assert m.seed == SEED_ZERO.hex()
    assert len(m.seed) == 64


def test_default_rewards_are_neutral():
    m = _build_match(_bruisers(), _other_bruisers())
    assert m.outcome.rewards.currency == 0
    assert m.outcome.rewards.rank_delta == "+0"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_adapter_output_is_deterministic():
    """Same engine inputs → identical Match (modulo timestamp)."""
    a, b = _bruisers(), _other_bruisers()
    m1 = _build_match(a, b)
    m2 = _build_match(a, b)
    # Fix: compare excluding timestamp (we passed a fixed string above so it
    # already matches, but be explicit)
    d1 = m1.model_dump()
    d2 = m2.model_dump()
    assert d1 == d2
