"""Animation primitives — the V1 default vocabulary + extensibility seam.

Architecture invariant (per locked spec, 2026-04-22):

Renderers (PIL, Textual, HTML) all read the same BattleFrame. The *primitives*
tell the renderer what to paint on each card at time t — color flash, overlay
icon, connection line, HP tick, plus the V1 expansion (intent telegraph, zap
element pulse, shake, pulse, glow, hit-pause, sound cues).

See `docs/animation_design.md` for research synthesis + design rationale.

This module defines:
  - The `Primitive` base class with a uniform interface
  - 10 default primitives (intent, color_flash, connection_line, overlay_icon,
    hp_tick, zap, shake, pulse, glow, hit_pause)
  - The `Cue` enum for sound spec (audio backend deferred)
  - The `PrimitiveRegistry` — pluggable, iteration-order stable

The `Animator` (in animator.py) iterates registered primitives, asks each
whether it `applies_to(action)`, and if so consults `window(action)` for the
[start_ms, end_ms) interval, then decorates the frame's ActiveEffects / conn
line list. This is the ONLY place that knows about primitive semantics.

Adding a new primitive later (e.g. "crit_zoom") = subclass `Primitive`,
register via `registry.add(CritZoomPrimitive())`, ship. No frame.py changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from daimon.play.schema import Action, ActionKind, CardRef, Element, Side


# ---------------------------------------------------------------------------
# Sound cue spec (audio backend deferred — see docs/animation_design.md)
# ---------------------------------------------------------------------------

class Cue(str, Enum):
    """Discrete sound events emitted alongside visual primitives.

    The terminal backend may opt to play the BEL char (`\\a`) for any cue when
    `DAIMON_AUDIO=bell` is set. Future audio backends (oggs, MIDI, TTS) plug
    into the same emission stream.
    """
    HIT = "hit"
    KO = "ko"
    BUFF = "buff"
    DEBUFF = "debuff"
    ROUND = "round"
    OUTCOME = "outcome"


# ---------------------------------------------------------------------------
# Timing windows (re-exported from frame.py constants — kept here too so
# primitive code can reference them without importing frame)
# ---------------------------------------------------------------------------

ACTION_BEAT_MS = 600

INTENT_WINDOW = (0, 200)              # actor highlights BEFORE damage applies
ACTOR_FLASH_WINDOW = (0, 300)
TARGET_FLASH_WINDOW = (100, 400)
CONNECTION_WINDOW = (0, 450)
OVERLAY_WINDOW = (0, 400)
HP_TICK_WINDOW = (200, 550)
ZAP_WINDOW = (0, 300)                 # element-color cycle
PULSE_WINDOW = (0, 500)
SHAKE_WINDOW = (100, 350)
GLOW_WINDOW = (0, ACTION_BEAT_MS)    # persistent across the beat
HIT_PAUSE_WINDOW = (0, 1)             # one-shot at t=0; emits pause_ms hint

# Damage thresholds (relative)
SHAKE_DMG_THRESHOLD = 8
HIT_PAUSE_DMG_PCT_THRESHOLD = 0.20    # hit pauses if dmg ≥ 20% of target hp_max
HIT_PAUSE_MS_RANGE = (60, 180)        # min..max pause stretch


# ---------------------------------------------------------------------------
# Kind → color / icon table (shared across primitives)
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


def resolve_color(action: Action) -> str:
    if action.vis_overrides and action.vis_overrides.color:
        return action.vis_overrides.color
    return KIND_COLOR.get(action.kind, "white")


def resolve_icon(action: Action) -> str:
    if action.vis_overrides and action.vis_overrides.icon:
        return action.vis_overrides.icon
    return KIND_ICON.get(action.kind, "·")


# ---------------------------------------------------------------------------
# Emissions — what a primitive tells the frame builder to attach
# ---------------------------------------------------------------------------

@dataclass
class CardEmission:
    """A primitive's instruction: attach this effect to this card."""
    side: Side
    position: int                      # 0..5 — team position (V2)
    kind: str                          # "color_flash" | "overlay_icon" | "hp_tick" | "shake" | "intent" | "zap" | "pulse" | "glow"
    color: Optional[str] = None
    icon: Optional[str] = None
    intensity: float = 1.0
    # Free-form extension — primitive can pass renderer-specific hints
    extra: dict = field(default_factory=dict)


@dataclass
class ConnectionEmission:
    """A primitive's instruction: draw a connection line across the grid."""
    actor_side: Side
    actor_position: int                # 0..5 — team position (V2)
    target_side: Side
    target_position: int               # 0..5 — team position (V2)
    color: str = "red"
    intensity: float = 1.0
    style: str = "solid"               # "solid" | "dashed" | "arc" (future)


# ---------------------------------------------------------------------------
# Element → color table (for ZapPrimitive)
# ---------------------------------------------------------------------------

ELEMENT_COLOR = {
    Element.FIRE: "red",
    Element.WATER: "cyan",
    Element.NATURE: "green",
    Element.VOLT: "yellow",
    Element.VOID: "magenta",
    Element.NORMAL: "white",   # outside the type ring → neutral tint
}


# ---------------------------------------------------------------------------
# Primitive base class
# ---------------------------------------------------------------------------

class Primitive(ABC):
    """One unit of animation behavior. Subclass + register.

    Lifecycle per action:
      1. `applies_to(action)` — gate: should this primitive fire for this action?
      2. `window(action)` — returns (start_ms, end_ms) within the 600ms beat
      3. `emit(action, t_ms)` — at time t (inside the window), return emissions
         to attach to the frame (CardEmission list + optional ConnectionEmission)
    """

    #: Stable short name, used for registry lookup and debugging
    name: str = "primitive"

    @abstractmethod
    def applies_to(self, action: Action) -> bool:
        ...

    @abstractmethod
    def window(self, action: Action) -> tuple[int, int]:
        ...

    def is_active_at(self, action: Action, t_ms: int) -> bool:
        start, end = self.window(action)
        return start <= t_ms < end

    @abstractmethod
    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        ...


# ---------------------------------------------------------------------------
# Four default primitives (V1 ship set)
# ---------------------------------------------------------------------------

class ColorFlashPrimitive(Primitive):
    """Border + frame tint flash on actor and (if present) target.

    Two distinct timing windows — actor flashes first (0..300), target
    flashes slightly later (100..400), producing a cause→effect read.
    """

    name = "color_flash"

    def applies_to(self, action: Action) -> bool:
        # Every action has an actor; fires unconditionally. Target flash handled in emit.
        return True

    def window(self, action: Action) -> tuple[int, int]:
        # Union window for frame-builder gating; per-card windows applied in emit().
        return (0, 400)

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        color = resolve_color(action)
        out: list[CardEmission] = []

        a_start, a_end = ACTOR_FLASH_WINDOW
        if a_start <= t_ms < a_end:
            out.append(CardEmission(
                side=action.actor.side, position=action.actor.position,
                kind="color_flash", color=color,
            ))

        if action.target is not None:
            t_start, t_end = TARGET_FLASH_WINDOW
            if t_start <= t_ms < t_end:
                out.append(CardEmission(
                    side=action.target.side, position=action.target.position,
                    kind="color_flash", color=color,
                ))

        return out, None


class ConnectionLinePrimitive(Primitive):
    """Actor → target straight line (or arc) across the grid."""

    name = "connection_line"

    def applies_to(self, action: Action) -> bool:
        if action.target is None:
            return False
        if action.vis_overrides and action.vis_overrides.suppress_line:
            return False
        return True

    def window(self, action: Action) -> tuple[int, int]:
        return CONNECTION_WINDOW

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        if not self.is_active_at(action, t_ms) or action.target is None:
            return [], None
        conn = ConnectionEmission(
            actor_side=action.actor.side,
            actor_position=action.actor.position,
            target_side=action.target.side,
            target_position=action.target.position,
            color=resolve_color(action),
        )
        return [], conn


class OverlayIconPrimitive(Primitive):
    """Kind-emoji overlay on both actor and target (or actor only if no target)."""

    name = "overlay_icon"

    def applies_to(self, action: Action) -> bool:
        return True

    def window(self, action: Action) -> tuple[int, int]:
        return OVERLAY_WINDOW

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        if not self.is_active_at(action, t_ms):
            return [], None
        icon = resolve_icon(action)
        out = [CardEmission(
            side=action.actor.side, position=action.actor.position,
            kind="overlay_icon", icon=icon,
        )]
        if action.target is not None:
            out.append(CardEmission(
                side=action.target.side, position=action.target.position,
                kind="overlay_icon", icon=icon,
            ))
        return out, None


class HpTickPrimitive(Primitive):
    """HP-bar sweep-down/sweep-up on the target as HP changes."""

    name = "hp_tick"

    def applies_to(self, action: Action) -> bool:
        # Fires whenever hp_after has any entry (damage, heal, death)
        return bool(action.hp_after)

    def window(self, action: Action) -> tuple[int, int]:
        return HP_TICK_WINDOW

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        if not self.is_active_at(action, t_ms):
            return [], None
        color = resolve_color(action)
        out: list[CardEmission] = []
        # Primary target gets HP tick if present; otherwise emit on every card in hp_after
        if action.target is not None:
            out.append(CardEmission(
                side=action.target.side, position=action.target.position,
                kind="hp_tick", color=color,
            ))
        else:
            for key in action.hp_after.keys():
                side_str, pos_str = key.split("/")
                out.append(CardEmission(
                    side=Side(side_str), position=int(pos_str),
                    kind="hp_tick", color=color,
                ))
        return out, None


# ---------------------------------------------------------------------------
# V1 expansion primitives — promoted to default-on, fully implemented.
# ---------------------------------------------------------------------------

class IntentPrimitive(Primitive):
    """Telegraph the upcoming action — actor brightens BEFORE damage applies.

    Slay-the-Spire-style intent. The actor tile gets a bold/highlight effect
    in the first 200ms of the beat, *before* hp_tick fires at 200ms, giving
    the eye a half-frame to register "Atlas is about to strike Bramble."
    """

    name = "intent"

    def applies_to(self, action: Action) -> bool:
        # Fire on any active-ish action with a target (the cause→effect read).
        # Skip pure passives that have no target.
        return action.target is not None

    def window(self, action: Action) -> tuple[int, int]:
        return INTENT_WINDOW

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        if not self.is_active_at(action, t_ms):
            return [], None
        # Linear fade from 1.0 → 0.0 across the window, so the highlight
        # is most intense at t=0 and tapers as the strike resolves.
        start, end = self.window(action)
        intensity = 1.0 - ((t_ms - start) / max(1, end - start))
        return [CardEmission(
            side=action.actor.side, position=action.actor.position,
            kind="intent", color=resolve_color(action), intensity=intensity,
        )], None


class ShakePrimitive(Primitive):
    """Target-card wiggle on big damage. Default-on as of V1."""

    name = "shake"

    def applies_to(self, action: Action) -> bool:
        return (
            action.kind == ActionKind.DAMAGE
            and (action.amount or 0) >= SHAKE_DMG_THRESHOLD
            and action.target is not None
        )

    def window(self, action: Action) -> tuple[int, int]:
        return SHAKE_WINDOW

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        if not self.is_active_at(action, t_ms) or action.target is None:
            return [], None
        # Sine offset in cells — renderer reads `extra["offset_cells"]` to
        # shift the target tile horizontally for a few frames.
        import math
        start, _ = self.window(action)
        phase = (t_ms - start) / 50.0
        offset_cells = int(round(math.sin(phase * math.pi)))  # -1, 0, +1
        return [CardEmission(
            side=action.target.side, position=action.target.position,
            kind="shake", extra={"offset_cells": offset_cells},
        )], None


class PulsePrimitive(Primitive):
    """Buff/shield expanding ring on the actor. Default-on as of V1."""

    name = "pulse"

    def applies_to(self, action: Action) -> bool:
        return action.kind in (ActionKind.BUFF, ActionKind.SHIELD, ActionKind.HEAL)

    def window(self, action: Action) -> tuple[int, int]:
        return PULSE_WINDOW

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        if not self.is_active_at(action, t_ms):
            return [], None
        start, end = self.window(action)
        radius = (t_ms - start) / max(1, end - start)
        return [CardEmission(
            side=action.actor.side, position=action.actor.position,
            kind="pulse", color=resolve_color(action),
            extra={"radius": radius},
        )], None


class ZapPrimitive(Primitive):
    """Element-color cycle on the target — visual language for elemental triggers.

    Each element has a signature color (FIRE=red, WATER=cyan, NATURE=green,
    VOLT=yellow, VOID=magenta). The target tile flashes the actor's element
    color so chains read as "this is a FIRE trigger" at a glance.
    """

    name = "zap"

    def applies_to(self, action: Action) -> bool:
        # Fires on any cascade trigger (reactive action with `reason` set).
        # Top-level actions are covered by color_flash; zap is the chain
        # read.
        return action.target is not None and bool(action.reason)

    def window(self, action: Action) -> tuple[int, int]:
        return ZAP_WINDOW

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        if not self.is_active_at(action, t_ms) or action.target is None:
            return [], None
        # We don't carry Element on the action wire — derive from a hint or
        # default to the kind color. Adapter sets vis_overrides.color to
        # element color for cascade triggers; respect that.
        color = resolve_color(action)
        return [CardEmission(
            side=action.target.side, position=action.target.position,
            kind="zap", color=color, intensity=0.6,
        )], None


class GlowPrimitive(Primitive):
    """Persistent legendary-card glow — orthogonal to actions, always-on for
    legendary cards on the team.

    Unlike action-driven primitives, glow doesn't need an Action to fire — it
    paints whenever the registry asks. We achieve this by claiming `applies_to`
    for every action and emitting a glow on every legendary card on the field.
    The renderer dedupes by (side, position, kind).
    """

    name = "glow"

    def applies_to(self, action: Action) -> bool:
        # We need a hook into the frame to know which positions are legendary;
        # since primitives don't see the frame, we emit on the actor + target
        # if their species/rarity is encoded in the action `reason`. Practical
        # impl: the frame builder calls `glow.emit_persistent(card_states)`
        # outside the per-action tick. So this Primitive only fires the
        # action-bound glow trail (a brief sparkle on legendary actors).
        return True

    def window(self, action: Action) -> tuple[int, int]:
        return GLOW_WINDOW

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        # Action-bound glow: the renderer adds the persistent legendary glow
        # via emit_persistent (below). The action tick contributes a faint
        # action-trail glow on the actor for the first 200ms.
        if t_ms >= 200:
            return [], None
        return [CardEmission(
            side=action.actor.side, position=action.actor.position,
            kind="glow", color="white", intensity=0.3,
        )], None

    @staticmethod
    def emit_persistent(side: Side, position: int) -> CardEmission:
        """Frame-builder helper — emit a steady glow for one legendary card."""
        return CardEmission(
            side=side, position=position,
            kind="glow", color="yellow", intensity=0.4,
            extra={"persistent": True},
        )


class HitPausePrimitive(Primitive):
    """One-shot pause-the-loop hint scaled by damage% of target HP.

    Doesn't paint anything; emits an `extra["pause_ms"]` hint at t=0 that the
    playback loop reads and uses to stretch the next frame. Big hits feel
    weighty because the world freezes for a beat.
    """

    name = "hit_pause"

    def applies_to(self, action: Action) -> bool:
        if action.kind != ActionKind.DAMAGE or action.target is None:
            return False
        amount = action.amount or 0
        if amount <= 0:
            return False
        # Need to compare against target hp_max — we don't have it on the wire,
        # but we can use absolute amount as a proxy: ≥10 damage = pause.
        # When the frame builder consumes this it can refine using hp_max.
        return amount >= 10

    def window(self, action: Action) -> tuple[int, int]:
        return HIT_PAUSE_WINDOW

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        if t_ms != 0 or action.target is None:
            return [], None
        amount = action.amount or 0
        # Map 10..30 damage → 60..180 ms pause (linear, clamped).
        lo_dmg, hi_dmg = 10, 30
        lo_ms, hi_ms = HIT_PAUSE_MS_RANGE
        clamped = max(lo_dmg, min(hi_dmg, amount))
        pct = (clamped - lo_dmg) / (hi_dmg - lo_dmg) if hi_dmg > lo_dmg else 0.0
        pause_ms = int(lo_ms + pct * (hi_ms - lo_ms))
        return [CardEmission(
            side=action.target.side, position=action.target.position,
            kind="hit_pause", extra={"pause_ms": pause_ms},
        )], None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class PrimitiveRegistry:
    """Ordered list of primitives. Iteration order = paint order.

    `default()` returns the V1 ship set (4 primitives). Add V1.x primitives
    via `.add()` or pass a custom list to the constructor.
    """

    def __init__(self, primitives: Optional[list[Primitive]] = None):
        self._primitives: list[Primitive] = list(primitives) if primitives is not None else []

    def add(self, primitive: Primitive) -> None:
        self._primitives.append(primitive)

    def remove(self, name: str) -> None:
        self._primitives = [p for p in self._primitives if p.name != name]

    def __iter__(self):
        return iter(self._primitives)

    def __len__(self) -> int:
        return len(self._primitives)

    def names(self) -> list[str]:
        return [p.name for p in self._primitives]

    @classmethod
    def default(cls) -> "PrimitiveRegistry":
        """V1 ship set — 10 primitives, ordered for paint correctness.

        Order matters: intent (telegraph) paints first so the actor highlight
        sits behind the strike effects. hit_pause emits a hint and paints
        nothing, so its position is cosmetic. glow goes last so its persistent
        border doesn't get overdrawn by transient flashes.
        """
        return cls([
            IntentPrimitive(),
            ColorFlashPrimitive(),
            ConnectionLinePrimitive(),
            OverlayIconPrimitive(),
            HpTickPrimitive(),
            ZapPrimitive(),
            ShakePrimitive(),
            PulsePrimitive(),
            HitPausePrimitive(),
            GlowPrimitive(),
        ])

    @classmethod
    def minimal(cls) -> "PrimitiveRegistry":
        """Just the original 4 — useful for snapshot tests that pre-date the
        V1 expansion. Don't use this in production playback."""
        return cls([
            ColorFlashPrimitive(),
            ConnectionLinePrimitive(),
            OverlayIconPrimitive(),
            HpTickPrimitive(),
        ])
