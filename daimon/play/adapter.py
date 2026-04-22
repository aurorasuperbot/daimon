"""Engine → Match adapter (A.4.b).

The engine resolves a match into a `MatchResult` (engine-native types: int sides,
IntEnum elements, (side, position) tuples for hp_after, CombatEvent records).
The play renderer consumes a `play.schema.Match` (string sides, str-enum elements,
"side/pos" string keys, recursive Action models).

This module does the seam translation in one direction: MatchResult → Match.

Design rules:
  - Pure function: same input → same output. No I/O, no time calls.
  - Display metadata (card display name, rarity, art_path) is OPTIONAL — if
    absent, we synthesize sensible defaults from engine fields (species ⇒
    titlecased name, rarity ⇒ "common"). This lets the MCP tool ship engine-
    driven matches today without a catalog wiring step; richer metadata is a
    pure follow-up.
  - Rewards / outcome stats are OPTIONAL for the same reason — defaults are
    safe but minimal so the schema still validates.
  - Action IDs follow the V2 fixture convention: "r{round}_a{idx}" for top-
    level actions, "r{round}_a{idx}_t{n}" for nested triggers.

The reverse direction (Match → engine) is intentionally NOT implemented. The
engine is the source of truth; the wire format is a pure derivative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from daimon.engine.types import (
    Card,
    CombatEvent,
    Element as EngineElement,
    MatchResult,
)
from daimon.engine.loadout import Loadout
from daimon.play.schema import (
    Action,
    ActionKind,
    CardRef,
    Element as SchemaElement,
    LoadoutCard,
    Match,
    MatchStats,
    Outcome,
    Participant,
    RenderHints,
    Rewards,
    Round,
    Side,
    hp_key,
)


# ---------------------------------------------------------------------------
# Element + side conversion
# ---------------------------------------------------------------------------

# Engine Element (IntEnum) -> Schema Element (str enum). Names match by design
# (FIRE/WATER/NATURE/VOLT/VOID), so we walk the names rather than int values to
# survive future additions to either enum.
_ELEMENT_BY_NAME: dict[str, SchemaElement] = {
    e.name: SchemaElement(e.value) for e in SchemaElement
}


def engine_element_to_schema(elem: EngineElement) -> SchemaElement:
    """Convert engine Element (IntEnum) to schema Element (str enum)."""
    try:
        return _ELEMENT_BY_NAME[elem.name]
    except KeyError as err:
        raise ValueError(f"unknown engine element: {elem!r}") from err


def side_int_to_schema(side: int) -> Side:
    """Engine side (0/1) → schema Side ('player'/'opponent')."""
    if side == 0:
        return Side.PLAYER
    if side == 1:
        return Side.OPPONENT
    raise ValueError(f"side must be 0 or 1, got {side!r}")


# ---------------------------------------------------------------------------
# Per-card display metadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardDisplay:
    """Optional per-card display overrides. All fields default to None and
    the adapter synthesizes from the engine Card when missing."""
    name: Optional[str] = None              # default: titlecased species
    short_name: Optional[str] = None        # default: first 6 chars of species
    rarity: Optional[str] = None            # default: "common"
    art_path: Optional[str] = None          # default: None


@dataclass(frozen=True)
class ParticipantInfo:
    """Display info for one side of the match."""
    name: str
    rank: str
    # Per-position display overrides. Position N maps to loadout.cards[N].
    # Missing entries fall back to defaults.
    card_displays: tuple[Optional[CardDisplay], ...] = ()


def _default_display_name(card: Card) -> str:
    """Titlecase species, replacing underscores with spaces."""
    return card.species.replace("_", " ").title()


def _default_short_name(card: Card) -> str:
    """First 6 chars of the species, titlecased; matches fixture convention."""
    base = card.species.replace("_", "").title()
    return base[:6]


def _resolve_display(card: Card, override: Optional[CardDisplay]) -> CardDisplay:
    """Merge an override CardDisplay with engine-derived defaults."""
    if override is None:
        override = CardDisplay()
    return CardDisplay(
        name=override.name or _default_display_name(card),
        short_name=override.short_name or _default_short_name(card),
        rarity=override.rarity or "common",
        art_path=override.art_path,
    )


def _build_loadout_cards(
    loadout: Loadout,
    info: ParticipantInfo,
) -> list[LoadoutCard]:
    """Build the schema's per-side loadout list from engine Cards + display info."""
    out: list[LoadoutCard] = []
    for i, card in enumerate(loadout.cards):
        override = (
            info.card_displays[i] if i < len(info.card_displays) else None
        )
        disp = _resolve_display(card, override)
        out.append(LoadoutCard(
            position=i,
            species=card.species,
            element=engine_element_to_schema(card.element),
            name=disp.name,
            short_name=disp.short_name,
            hp_max=card.hp,
            hp=card.hp,
            rarity=disp.rarity,  # type: ignore[arg-type]
            art_path=disp.art_path,
        ))
    return out


# ---------------------------------------------------------------------------
# Event → Action
# ---------------------------------------------------------------------------

def _card_id_to_display_name(
    side_loadouts: dict[Side, list[LoadoutCard]],
    side: Side,
    position: int,
) -> str:
    """Look up the display name for an actor/target by (side, position).

    The engine guarantees position is always 0..5, but burn ticks emit
    actor_card_id="burn" with a side derived from the affected unit. Caller
    handles the burn case before invoking this lookup.
    """
    cards = side_loadouts.get(side, [])
    for card in cards:
        if card.position == position:
            return card.name
    # Defensive fallback — should not happen for engine-emitted events.
    return f"position_{position}"


def _event_hp_after_to_str(hp_after: dict[tuple[int, int], int]) -> dict[str, int]:
    """Convert engine (side, position) tuple keys to schema 'side/pos' strings."""
    return {
        hp_key(side_int_to_schema(side), pos): hp
        for (side, pos), hp in hp_after.items()
    }


def _event_to_action(
    event: CombatEvent,
    action_id: str,
    side_loadouts: dict[Side, list[LoadoutCard]],
) -> Action:
    """Recursive: convert a CombatEvent (and its nested triggers) to an Action."""
    actor_side = side_int_to_schema(event.actor_side)
    actor = CardRef(
        side=actor_side,
        position=event.actor_position,
        card=_card_id_to_display_name(
            side_loadouts, actor_side, event.actor_position,
        ),
    )

    target: Optional[CardRef] = None
    if event.target_side is not None and event.target_position is not None:
        tgt_side = side_int_to_schema(event.target_side)
        target = CardRef(
            side=tgt_side,
            position=event.target_position,
            card=_card_id_to_display_name(
                side_loadouts, tgt_side, event.target_position,
            ),
        )

    # Recursively convert nested trigger events. Use the parent action_id as
    # prefix and number nested triggers from 1.
    nested_actions: list[Action] = []
    for n, child in enumerate(event.triggers, start=1):
        nested_actions.append(_event_to_action(
            child, f"{action_id}_t{n}", side_loadouts,
        ))

    return Action(
        action_id=action_id,
        actor=actor,
        target=target,
        kind=ActionKind(event.kind),
        amount=event.amount,
        hp_after=_event_hp_after_to_str(event.hp_after),
        status_applied=event.status_applied,
        triggers=nested_actions,
        reason=event.reason,
        log_line=event.log_line,
    )


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

def _build_outcome(
    result: MatchResult,
    rewards: Optional[Rewards],
) -> Outcome:
    """Engine winner (0/1/None) → schema Outcome.

    None winner (engine-side draw, MatchResult.reason in {"draw","double_wipe"})
    maps to Side.DRAW so the renderer / HUD / payout logic can distinguish a
    real PLAYER win from "nobody won". Rewards default to zero on a draw."""
    if result.winner == 0:
        winner_side = Side.PLAYER
    elif result.winner == 1:
        winner_side = Side.OPPONENT
    else:
        winner_side = Side.DRAW

    return Outcome(
        winner=winner_side,
        player_hp_remaining=result.side_a_final_hp,
        opponent_hp_remaining=result.side_b_final_hp,
        stats=MatchStats(
            cards_killed={"player": 0, "opponent": 0},  # populated below
            biggest_hit=None,
            longest_survivor=None,
            round_count=len(result.rounds),
        ),
        rewards=rewards or Rewards(currency=0, rank_delta="+0"),
    )


def _populate_cards_killed(outcome: Outcome, result: MatchResult) -> None:
    """Walk events and count death events per side. Mutates outcome.stats."""
    killed = {"player": 0, "opponent": 0}
    for rd in result.rounds:
        for ev in rd.events:
            for child in ev.triggers:
                if child.kind == "death":
                    side = side_int_to_schema(child.actor_side).value
                    killed[side] = killed.get(side, 0) + 1
    outcome.stats.cards_killed = killed


# ---------------------------------------------------------------------------
# Top-level adapter
# ---------------------------------------------------------------------------

def match_result_to_match(
    result: MatchResult,
    loadout_a: Loadout,
    loadout_b: Loadout,
    *,
    match_id: str,
    player: ParticipantInfo,
    opponent: ParticipantInfo,
    timestamp: Optional[str] = None,
    kind: str = "pve",
    rewards: Optional[Rewards] = None,
    render_hints: Optional[RenderHints] = None,
) -> Match:
    """Convert an engine MatchResult into a schema-validated Match payload.

    Args:
      result: engine output from `resolve_match(loadout_a, loadout_b, seed)`.
      loadout_a / loadout_b: the same loadouts that went into resolve_match.
        Side A maps to "player", side B to "opponent" — a convention shared
        with the existing dm_match state-file payload.
      match_id: opaque short id; e.g. UUID4 hex prefix.
      player / opponent: name + rank + per-card display overrides.
      timestamp: ISO8601 UTC; defaults to now() if omitted.
      kind: "pve" or "pvp_async".
      rewards: optional Rewards block. Defaults to zero-currency, +0 rank.
      render_hints: optional RenderHints. Defaults to standard pacing.

    Returns:
      A `play.schema.Match` instance, ready to dump as JSON for the inbox.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    player_loadout = _build_loadout_cards(loadout_a, player)
    opponent_loadout = _build_loadout_cards(loadout_b, opponent)
    side_loadouts: dict[Side, list[LoadoutCard]] = {
        Side.PLAYER: player_loadout,
        Side.OPPONENT: opponent_loadout,
    }

    rounds: list[Round] = []
    for rd in result.rounds:
        first_player_side = side_int_to_schema(rd.first_player)
        actions: list[Action] = []
        for idx, event in enumerate(rd.events, start=1):
            action_id = f"r{rd.round_number}_a{idx}"
            actions.append(_event_to_action(event, action_id, side_loadouts))
        rounds.append(Round(
            round=rd.round_number,
            first_player=first_player_side,
            actions=actions,
        ))

    outcome = _build_outcome(result, rewards)
    _populate_cards_killed(outcome, result)

    seed_hex: Optional[str] = result.seed.hex() if result.seed else None

    return Match(
        match_id=match_id,
        kind=kind,  # type: ignore[arg-type]
        timestamp=timestamp,
        participants={
            "player": Participant(
                name=player.name,
                rank=player.rank,
                loadout=player_loadout,
            ),
            "opponent": Participant(
                name=opponent.name,
                rank=opponent.rank,
                loadout=opponent_loadout,
            ),
        },
        seed=seed_hex,
        rounds=rounds,
        outcome=outcome,
        render_hints=render_hints or RenderHints(),
        capabilities_required=[],
    )
