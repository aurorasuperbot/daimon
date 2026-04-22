"""Playback state machine for the spectator HUD.

Pure logic — no I/O, no terminal, no keyboard. Builds a flat timeline of
``Action`` events from a ``Match`` payload, then exposes a cursor with
forward/backward/jump primitives plus a snapshot view that the renderer
consumes.

Why a flat timeline:

A ``Match`` is structured as ``rounds[*].actions[*]`` and each action can
carry ``triggers`` (reactive cascade — recursive). The renderer wants to
walk one event at a time so the player sees cause-effect pop in sequence.
We pre-flatten:

    action → trigger → trigger → next action → trigger → ...

depth-first, parents before children. The flat list lets us O(1) seek to
any index, scrub backward, and compute HP snapshots by replaying
``hp_after`` patches up to the current cursor.

State machine:

    IDLE     — no match loaded; waiting on inbox
    PLAYING  — auto-advancing on a tick
    PAUSED   — frozen on current step; user controls
    ENDED    — cursor at last step + outcome reveal

Transitions are driven by `MatchPlayback.advance/back/jump/play/pause`.
The owning loop ticks with `step()` which advances the cursor when state
is PLAYING and enough time has elapsed for the current speed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from daimon.play.schema import Action, Match, Side


# ---------------------------------------------------------------------------
# Speed knob
# ---------------------------------------------------------------------------

# Cycle of speeds the user can flip through with up/down. Center = 1.0x.
# 4x is the practical ceiling — past that the log scrolls too fast to read.
SPEED_LADDER: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0)
DEFAULT_SPEED_INDEX = 2  # 1.0x

# Base ms/step at 1.0x speed. ms-per-step = BASE_TICK_MS / speed.
# 700ms feels like a brisk-but-readable combat beat for a turn-based feel.
BASE_TICK_MS = 700

# Cooldown after the last action before transitioning ENDED → next match.
# Gives the user a beat to see "X wins!" before the screen switches.
END_COOLDOWN_MS = 2500


class PlaybackStatus(str, Enum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    ENDED = "ended"


class Phase(str, Enum):
    """Match-chrome event types woven into the flat timeline.

    LINEUP       — match-start reveal: both teams shown, "match starting".
    ROUND_START  — banner introducing each round (R1, R2, ...).
    OUTCOME      — winner reveal at the end with final HP + stats.

    These coexist with action steps in the same flat timeline so all
    transport controls (pause, scrub, speed, restart) work uniformly on
    chrome too — no separate animation thread, no state duplication.
    """
    LINEUP = "lineup"
    ROUND_START = "round_start"
    OUTCOME = "outcome"


# ---------------------------------------------------------------------------
# Flat-timeline step
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    """One playable event — an action, a nested trigger, or a chrome phase.

    The flat timeline is a list of these. ``depth=0`` is a top-level action
    in some round; ``depth>0`` is a reactive trigger nested under a parent
    action (depth=1 = direct trigger; depth=2 = trigger-of-trigger; etc).

    ``round_number`` and ``action_index`` reference the source location in
    the ``Match.rounds`` tree — useful for the status bar and for renderers
    that want to highlight "round 3, action 5 of 9".

    Either ``action`` or ``phase`` is set, never both:
      - action step  : ``action`` is the schema Action; ``phase`` is None.
      - phase step   : ``phase`` is set; ``action`` is None. Phase steps
                        carry no HP patches and are skipped during HP replay.
    """
    index: int                       # position in flat timeline (0-based)
    round_number: int                # 1-based round (0 for LINEUP)
    action_index: int                # 0-based within the parent action's round
    depth: int                       # 0 = top-level, >0 = nested trigger
    action: Optional[Action] = None  # set on action steps; None on phase steps
    phase: Optional[Phase] = None    # set on chrome steps; None on action steps

    @property
    def is_phase(self) -> bool:
        return self.phase is not None

    @property
    def is_action(self) -> bool:
        return self.action is not None


# ---------------------------------------------------------------------------
# HP snapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HpSnapshot:
    """HP for every (side, position) at a particular cursor index.

    Built from the ``hp_after`` patches accumulated up to (and including)
    the current step. Positions absent from any prior ``hp_after`` patch
    fall back to the loadout's starting ``hp`` value.

    ``key()`` matches ``schema.hp_key`` — `'player/0'`, `'opponent/3'`, etc.
    """
    by_key: dict[str, int]

    def get(self, side: Side, position: int, default: int) -> int:
        key = f"{side.value}/{position}"
        return self.by_key.get(key, default)


# ---------------------------------------------------------------------------
# Frame view (what the renderer sees)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    """Snapshot view of playback at one cursor position.

    Everything a renderer needs to paint the current screen, computed from
    the ``MatchPlayback`` once. Decouples render code from playback's
    mutable state.
    """
    match: Match
    status: PlaybackStatus
    cursor: int                      # current step index (0..len-1)
    total_steps: int
    speed: float                     # current speed multiplier
    hp: HpSnapshot                   # HP for every card at this cursor
    current_step: Optional[Step]     # None only when total_steps==0
    log_tail: tuple[Step, ...]       # last N steps including current (most-recent first)
    elapsed_steps_in_round: dict[int, int]  # round_number → steps shown so far in that round

    @property
    def is_terminal(self) -> bool:
        """True if cursor is on the last step (or past it)."""
        return self.cursor >= self.total_steps - 1


# ---------------------------------------------------------------------------
# Flatten helper
# ---------------------------------------------------------------------------

def flatten_match(match: Match) -> list[Step]:
    """Walk every round → action → triggers (recursive, depth-first).

    Layout:

        LINEUP                    (chrome — match-start reveal)
        ROUND_START round=1       (chrome — round banner)
        action 1.1
          trigger 1.1.a
          trigger 1.1.b
        action 1.2
        ...
        ROUND_START round=2
        action 2.1
        ...
        OUTCOME                   (chrome — winner reveal)

    Order invariants:
      - A parent action is emitted BEFORE its triggers (renderer relies on
        cause-effect ordering).
      - Each ROUND_START sits immediately before the round's first action.
      - LINEUP and OUTCOME bracket the entire timeline.

    Phase steps carry no HP patches and are skipped during HP replay.
    """
    out: list[Step] = []
    out.append(Step(
        index=0, round_number=0, action_index=0, depth=0,
        phase=Phase.LINEUP,
    ))
    for r in match.rounds:
        out.append(Step(
            index=len(out),
            round_number=r.round,
            action_index=0,
            depth=0,
            phase=Phase.ROUND_START,
        ))
        for a_idx, action in enumerate(r.actions):
            _emit_action_recursive(
                out=out,
                action=action,
                round_number=r.round,
                action_index=a_idx,
                depth=0,
            )
    last_round = match.rounds[-1].round if match.rounds else 0
    out.append(Step(
        index=len(out),
        round_number=last_round,
        action_index=0,
        depth=0,
        phase=Phase.OUTCOME,
    ))
    return out


def _emit_action_recursive(
    out: list[Step],
    action: Action,
    round_number: int,
    action_index: int,
    depth: int,
) -> None:
    out.append(Step(
        index=len(out),
        round_number=round_number,
        action_index=action_index,
        depth=depth,
        action=action,
        phase=None,
    ))
    for trig in action.triggers:
        _emit_action_recursive(
            out=out,
            action=trig,
            round_number=round_number,
            action_index=action_index,
            depth=depth + 1,
        )


# ---------------------------------------------------------------------------
# HP replay
# ---------------------------------------------------------------------------

def hp_at(match: Match, timeline: list[Step], cursor: int) -> HpSnapshot:
    """Replay ``hp_after`` patches up to and including ``cursor``.

    A patch is applied per-key — only positions mentioned in ``hp_after``
    are updated. The starting-HP fallback is the loadout's `hp` field
    (post-buff initial HP if the schema carried one, otherwise hp_max).
    """
    by_key: dict[str, int] = {}

    # Seed with starting HP so renderers can paint full lineups even before
    # any action has touched a card.
    for side in (Side.PLAYER, Side.OPPONENT):
        part = match.participants.get(side.value)
        if part is None:
            continue
        for card in part.loadout:
            by_key[f"{side.value}/{card.position}"] = card.hp

    if cursor < 0:
        return HpSnapshot(by_key=by_key)

    upper = min(cursor + 1, len(timeline))
    for i in range(upper):
        step = timeline[i]
        if step.action is None:
            continue   # phase steps carry no HP patches
        for k, v in step.action.hp_after.items():
            by_key[k] = int(v)

    return HpSnapshot(by_key=by_key)


# ---------------------------------------------------------------------------
# Playback engine
# ---------------------------------------------------------------------------

LOG_TAIL_LEN = 8        # how many recent steps the renderer shows in the log


@dataclass
class MatchPlayback:
    """Mutable cursor over a flattened Match timeline.

    Owned by the HUD app; consumed by the renderer via ``snapshot()``.
    """
    match: Match
    timeline: list[Step] = field(default_factory=list)
    cursor: int = 0
    status: PlaybackStatus = PlaybackStatus.PLAYING
    speed_index: int = DEFAULT_SPEED_INDEX
    # ms accumulated since last cursor advance — incremented by step(elapsed_ms).
    _accum_ms: int = 0
    # ms accumulated past the final step in ENDED state.
    _ended_dwell_ms: int = 0
    state_id: Optional[str] = None     # state.json id this match came from

    def __post_init__(self) -> None:
        if not self.timeline:
            self.timeline = flatten_match(self.match)
        # Empty matches go straight to ENDED (renderer shows outcome).
        if not self.timeline:
            self.status = PlaybackStatus.ENDED

    # ----- speed -----

    @property
    def speed(self) -> float:
        return SPEED_LADDER[self.speed_index]

    def speed_up(self) -> None:
        if self.speed_index < len(SPEED_LADDER) - 1:
            self.speed_index += 1

    def speed_down(self) -> None:
        if self.speed_index > 0:
            self.speed_index -= 1

    # ----- transport -----

    def play(self) -> None:
        """Resume from PAUSED. No-op in IDLE/PLAYING/ENDED."""
        if self.status == PlaybackStatus.PAUSED:
            self.status = PlaybackStatus.PLAYING
            self._accum_ms = 0

    def pause(self) -> None:
        """Halt advancement. No-op outside PLAYING."""
        if self.status == PlaybackStatus.PLAYING:
            self.status = PlaybackStatus.PAUSED

    def toggle_pause(self) -> None:
        if self.status == PlaybackStatus.PLAYING:
            self.pause()
        elif self.status == PlaybackStatus.PAUSED:
            self.play()

    def advance(self) -> bool:
        """Step the cursor forward by one. Returns True iff cursor moved.

        At the last step, transitions to ENDED. Calling advance again from
        ENDED does nothing — the loop is responsible for switching matches.
        """
        if self.cursor >= len(self.timeline) - 1:
            if self.status != PlaybackStatus.ENDED:
                self.status = PlaybackStatus.ENDED
                return False
            return False
        self.cursor += 1
        self._accum_ms = 0
        return True

    def back(self) -> bool:
        """Step the cursor backward by one. Resets ENDED → PAUSED if used."""
        if self.cursor <= 0:
            return False
        self.cursor -= 1
        self._accum_ms = 0
        if self.status == PlaybackStatus.ENDED:
            self.status = PlaybackStatus.PAUSED
        return True

    def jump_to_end(self) -> None:
        """Snap to the last step + ENDED. Used by 'skip-to-end' control."""
        if not self.timeline:
            self.status = PlaybackStatus.ENDED
            return
        self.cursor = len(self.timeline) - 1
        self.status = PlaybackStatus.ENDED
        self._accum_ms = 0

    def restart(self) -> None:
        """Snap back to step 0 + PLAYING. Useful after a match ENDS."""
        self.cursor = 0
        self.status = PlaybackStatus.PLAYING
        self._accum_ms = 0
        self._ended_dwell_ms = 0

    # ----- tick -----

    def step(self, elapsed_ms: int) -> int:
        """Advance simulated time by ``elapsed_ms``. Returns # of cursor steps taken.

        Called by the loop on every tick. In PLAYING state, accumulates
        elapsed_ms and pops one cursor step per (BASE_TICK_MS / speed). In
        ENDED state, accumulates dwell time so the loop can decide when
        it's safe to switch matches. PAUSED/IDLE: no time passes.
        """
        if elapsed_ms < 0:
            return 0

        if self.status == PlaybackStatus.PLAYING:
            self._accum_ms += elapsed_ms
            tick = max(1, int(BASE_TICK_MS / max(self.speed, 0.01)))
            advanced = 0
            while self._accum_ms >= tick and self.status == PlaybackStatus.PLAYING:
                self._accum_ms -= tick
                # Inline advance that does NOT reset _accum_ms — leftover ms
                # must carry across the step so 2 * tick_ms truly advances 2.
                if self.cursor >= len(self.timeline) - 1:
                    self.status = PlaybackStatus.ENDED
                    break
                self.cursor += 1
                advanced += 1
            return advanced

        if self.status == PlaybackStatus.ENDED:
            self._ended_dwell_ms += elapsed_ms

        return 0

    @property
    def ended_dwell_ms(self) -> int:
        """How long we've been sitting on the outcome screen (ENDED state)."""
        return self._ended_dwell_ms

    # ----- snapshot -----

    def snapshot(self) -> Frame:
        """Build a renderer-friendly view of the current cursor."""
        total = len(self.timeline)
        cur = max(0, min(self.cursor, total - 1)) if total else 0
        current = self.timeline[cur] if total else None
        snap = hp_at(self.match, self.timeline, cur if total else -1)

        # Tail = up to LOG_TAIL_LEN steps ending at current, oldest first.
        tail_start = max(0, cur - LOG_TAIL_LEN + 1) if total else 0
        tail_end = cur + 1 if total else 0
        tail = tuple(self.timeline[tail_start:tail_end])

        # Per-round action counts up through current. Phase steps are
        # excluded so the status line shows true action progress.
        per_round: dict[int, int] = {}
        for step in self.timeline[:tail_end]:
            if step.action is None:
                continue
            per_round[step.round_number] = per_round.get(step.round_number, 0) + 1

        return Frame(
            match=self.match,
            status=self.status,
            cursor=cur,
            total_steps=total,
            speed=self.speed,
            hp=snap,
            current_step=current,
            log_tail=tail,
            elapsed_steps_in_round=per_round,
        )
