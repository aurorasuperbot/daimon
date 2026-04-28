"""BattleFrame — the computed renderable state at a specific point in time.

Given a Match + timeline position (round_idx, action_idx, t_ms within the beat),
produce a frame dict that any renderer (PIL, daimon.ui, HTML) can paint.

This is the seam between the *engine* (which produces Match JSON) and the
*renderers* (which paint frames). All three render targets share this.

Animation primitives active at time t are attached to the frame as a list of
active effects per card slot. The renderer reads the effect list and decides
how to paint (border weight, overlay icon, connection line, etc).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from daimon.play.animator import Animator, AnimationSnapshot
from daimon.play.schema import (
    Action,
    ActionKind,
    CardRef,
    LoadoutCard,
    Match,
    Side,
)

# ---------------------------------------------------------------------------
# Timing constants (from locked spec, 2026-04-22)
# ---------------------------------------------------------------------------

ACTION_BEAT_MS = 600          # total time per action including log-line settle
TRANSITION_MS = 800           # P1: HUD → match-incoming banner
LINEUP_MS = 3000              # P2: pre-match lineup fan-in
ROUND_BANNER_MS = 400         # between-round banner hold
ROUND_SUMMARY_MS = 400        # after last action of a round
OUTCOME_MS = 4000             # P4: outcome screen
RETURN_MS = 400               # P5: crossfade back to HUD

# Per-action primitive timing (within one 600ms beat)
PRIM_ACTOR_FLASH_START = 0
PRIM_ACTOR_FLASH_END = 300    # flash fades starting 300, done by 450
PRIM_TARGET_FLASH_START = 100
PRIM_TARGET_FLASH_END = 400
PRIM_CONNECTION_START = 0
PRIM_CONNECTION_END = 450
PRIM_OVERLAY_START = 0
PRIM_OVERLAY_END = 400
PRIM_HP_TICK_START = 200
PRIM_HP_TICK_END = 550
LOG_LINE_START = 500          # log line starts appearing at 500ms


# ---------------------------------------------------------------------------
# Frame dataclass
# ---------------------------------------------------------------------------

@dataclass
class ActiveEffect:
    """One animation primitive currently active on a specific card."""
    kind: str                              # "color_flash" | "overlay_icon" | "connection_line" | "hp_tick"
    color: Optional[str] = None            # "red" | "green" | "blue" | ...
    icon: Optional[str] = None             # "💥" | "✨" | ...
    intensity: float = 1.0                 # 0.0-1.0, for fades


@dataclass
class CardState:
    """Snapshot of one card at frame time."""
    position: int                          # 0..5 — team position (V2)
    side: Side
    name: str
    short_name: str
    hp: int
    hp_max: int
    rarity: str
    species: str = ""
    element: Optional[object] = None       # play.schema.Element; Optional typed loosely
    is_dead: bool = False
    effects: list[ActiveEffect] = field(default_factory=list)


@dataclass
class ConnectionLine:
    """Attacker → target line rendered across the grid."""
    actor_side: Side
    actor_position: int                    # 0..5 — team position (V2)
    target_side: Side
    target_position: int                   # 0..5 — team position (V2)
    color: str = "red"
    intensity: float = 1.0


@dataclass
class BattleFrame:
    """Everything a renderer needs to paint one still image."""

    # Header
    match_id: str
    player_name: str
    player_rank: str
    opponent_name: str
    opponent_rank: str

    # Grid state — keyed by position (0..5)
    player_cards: dict[int, CardState]
    opponent_cards: dict[int, CardState]

    # Totals (bottom HP bar)
    player_total_hp: int
    player_total_hp_max: int
    opponent_total_hp: int
    opponent_total_hp_max: int

    # Active animation
    connection_line: Optional[ConnectionLine] = None
    log_lines: list[str] = field(default_factory=list)       # last N lines of action log, ordered oldest→newest
    log_line_typing: Optional[str] = None                    # partial line currently being typed (cursor shown)
    round_number: int = 1
    round_banner: Optional[str] = None                       # "ROUND 3" overlay if active

    # Phase (P0-P5 for outer renderer logic)
    phase: str = "round"                                     # "transition" | "lineup" | "round" | "outcome" | "return"


# ---------------------------------------------------------------------------
# Kind → color/icon mapping (the default primitive table)
# ---------------------------------------------------------------------------

KIND_COLOR = {
    ActionKind.DAMAGE: "red",
    ActionKind.HEAL: "green",
    ActionKind.BUFF: "blue",
    ActionKind.DEBUFF: "purple",
    ActionKind.SHIELD: "yellow",
    ActionKind.DEATH: "gray",
    ActionKind.STATUS: "cyan",
    ActionKind.PASSIVE: "white",
}

KIND_ICON = {
    ActionKind.DAMAGE: "💥",
    ActionKind.HEAL: "✨",
    ActionKind.BUFF: "⚡",
    ActionKind.DEBUFF: "✦",
    ActionKind.SHIELD: "🛡",
    ActionKind.DEATH: "☠",
    ActionKind.STATUS: "◆",
    ActionKind.PASSIVE: "·",
}


# ---------------------------------------------------------------------------
# Frame computation
# ---------------------------------------------------------------------------

def _build_initial_card_states(match: Match) -> tuple[dict[int, CardState], dict[int, CardState]]:
    def _for(side_name: str, side: Side) -> dict[int, CardState]:
        cards = {}
        for c in match.participants[side_name].loadout:
            cards[c.position] = CardState(
                position=c.position,
                side=side,
                name=c.name,
                short_name=c.short_name or c.name[:7],
                hp=c.hp,
                hp_max=c.hp_max,
                rarity=c.rarity,
                species=c.species,
                element=c.element,
                is_dead=(c.hp <= 0),
            )
        return cards

    return _for("player", Side.PLAYER), _for("opponent", Side.OPPONENT)


def _apply_hp_after(cards_p: dict, cards_o: dict, hp_after: dict[str, int]) -> None:
    for key, value in hp_after.items():
        side_str, pos_str = key.split("/")
        position = int(pos_str)
        card = cards_p[position] if side_str == "player" else cards_o[position]
        card.hp = value
        if value <= 0:
            card.is_dead = True


def _apply_all_actions_through(
    match: Match,
    cards_p: dict[int, CardState],
    cards_o: dict[int, CardState],
    through_round: int,
    through_action: int,
) -> list[str]:
    """Apply every action up to (but not including) (through_round, through_action).
    Returns the log lines accumulated so far (so the action log shows history)."""
    log = []

    def _apply_action(a: Action):
        _apply_hp_after(cards_p, cards_o, a.hp_after)
        if a.log_line:
            log.append(a.log_line)
        for t in a.triggers:
            _apply_action(t)

    for r in match.rounds:
        for i, a in enumerate(r.actions):
            if r.round > through_round:
                return log
            if r.round == through_round and i >= through_action:
                return log
            _apply_action(a)
    return log


def _compute_totals(cards: dict[int, CardState]) -> tuple[int, int]:
    return sum(max(0, c.hp) for c in cards.values()), sum(c.hp_max for c in cards.values())


def _resolve_color(action: Action) -> str:
    if action.vis_overrides and action.vis_overrides.color:
        return action.vis_overrides.color
    return KIND_COLOR.get(action.kind, "white")


def _resolve_icon(action: Action) -> str:
    if action.vis_overrides and action.vis_overrides.icon:
        return action.vis_overrides.icon
    return KIND_ICON.get(action.kind, "·")


def _suppress_line(action: Action) -> bool:
    return bool(action.vis_overrides and action.vis_overrides.suppress_line)


def build_mid_action_frame(
    match: Match,
    round_number: int,
    action_index: int,
    t_ms: int,
    log_tail: int = 6,
    animator: Optional[Animator] = None,
) -> BattleFrame:
    """Compute the frame at t_ms within the action at (round_number, action_index).

    t_ms == 0 is the beat start (actor flash on, connection line draws).
    t_ms == 250 is the "mid-animation" peak used for mockup screenshots.
    t_ms == 450 is after all primitives have cleared.
    t_ms >= 500 is the log-line settle phase.

    The `animator` controls primitive selection. Defaults to the V1 ship set
    (color_flash, connection_line, overlay_icon, hp_tick). Pass a custom
    Animator to test V1.x primitives or renderer-specific effect sets.
    """
    animator = animator or Animator()

    # Find the target action
    round_obj = next(r for r in match.rounds if r.round == round_number)
    action = round_obj.actions[action_index]

    # Apply all actions strictly before this one (history)
    cards_p, cards_o = _build_initial_card_states(match)
    history = _apply_all_actions_through(match, cards_p, cards_o, round_number, action_index)

    # Mid-action HP handling: animation sweeps from pre-action HP to post-action HP
    # in the 200..550ms window. We apply post-action HP once t >= HP_TICK_START,
    # and the HP-tick primitive handles the visual emphasis via ActiveEffect.
    if t_ms >= PRIM_HP_TICK_START:
        _apply_hp_after(cards_p, cards_o, action.hp_after)

    # Ask the animator for this tick's emissions, then attach to the frame
    snapshot = animator.tick(action, t_ms)
    for emit in snapshot.card_emissions:
        target_cards = cards_p if emit.side == Side.PLAYER else cards_o
        if emit.position in target_cards:
            target_cards[emit.position].effects.append(ActiveEffect(
                kind=emit.kind,
                color=emit.color,
                icon=emit.icon,
                intensity=emit.intensity,
            ))

    conn = None
    if snapshot.connection is not None:
        ce = snapshot.connection
        conn = ConnectionLine(
            actor_side=ce.actor_side,
            actor_position=ce.actor_position,
            target_side=ce.target_side,
            target_position=ce.target_position,
            color=ce.color,
            intensity=ce.intensity,
        )

    # Log lines: last `log_tail` lines of history, plus the current line typing in (if past LOG_LINE_START)
    log_lines = history[-log_tail:] if history else []
    log_typing = None
    if t_ms >= LOG_LINE_START and action.log_line:
        # Simple typing effect: percentage of chars revealed between t=500..620
        progress_ms = t_ms - LOG_LINE_START
        typing_duration = 120
        if progress_ms >= typing_duration:
            log_lines = log_lines + [action.log_line]
        else:
            pct = progress_ms / typing_duration
            n_chars = max(1, int(len(action.log_line) * pct))
            log_typing = action.log_line[:n_chars]

    # Totals
    player_hp, player_hp_max = _compute_totals(cards_p)
    opponent_hp, opponent_hp_max = _compute_totals(cards_o)

    return BattleFrame(
        match_id=match.match_id,
        player_name=match.participants["player"].name,
        player_rank=match.participants["player"].rank,
        opponent_name=match.participants["opponent"].name,
        opponent_rank=match.participants["opponent"].rank,
        player_cards=cards_p,
        opponent_cards=cards_o,
        player_total_hp=player_hp,
        player_total_hp_max=player_hp_max,
        opponent_total_hp=opponent_hp,
        opponent_total_hp_max=opponent_hp_max,
        connection_line=conn,
        log_lines=log_lines,
        log_line_typing=log_typing,
        round_number=round_number,
        phase="round",
    )
