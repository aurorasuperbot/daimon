"""Animation primitives — the 4 default effects + extensibility seam.

Architecture invariant (per locked spec, 2026-04-22):

Renderers (PIL, Textual, HTML) all read the same BattleFrame. The *primitives*
tell the renderer what to paint on each card at time t — color flash, overlay
icon, connection line, HP tick.

This module defines:
  - The `Primitive` base class with a uniform interface
  - The 4 default primitives (color_flash, connection_line, overlay_icon, hp_tick)
  - Pre-registered V1.x hooks (shake, pulse, glow) — subclasses stubbed so the
    registry is forward-compatible
  - The `PrimitiveRegistry` — pluggable, iteration-order stable

The `Animator` (in animator.py) iterates registered primitives, asks each
whether it `applies_to(action)`, and if so consults `window(action)` for the
[start_ms, end_ms) interval, then decorates the frame's ActiveEffects / conn
line list. This is the ONLY place that knows about primitive semantics.

Adding a new primitive later (e.g. "parry_arc") = subclass `Primitive`,
register via `registry.add(ParryArcPrimitive())`, ship. No frame.py changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from daimon.play.schema import Action, ActionKind, CardRef, Side


# ---------------------------------------------------------------------------
# Timing windows (re-exported from frame.py constants — kept here too so
# primitive code can reference them without importing frame)
# ---------------------------------------------------------------------------

ACTION_BEAT_MS = 600

ACTOR_FLASH_WINDOW = (0, 300)
TARGET_FLASH_WINDOW = (100, 400)
CONNECTION_WINDOW = (0, 450)
OVERLAY_WINDOW = (0, 400)
HP_TICK_WINDOW = (200, 550)


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
    kind: str                          # "color_flash" | "overlay_icon" | "hp_tick" | "shake" | ...
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
# V1.x stubbed primitives — registered but opt-in, so the registry is
# forward-compatible. Default `Animator()` constructor does NOT include them.
# ---------------------------------------------------------------------------

class ShakePrimitive(Primitive):
    """Target-card wiggle on big damage (V1.x — stub)."""

    name = "shake"

    def applies_to(self, action: Action) -> bool:
        # Planned: fire when damage >= threshold (say 8)
        return action.kind == ActionKind.DAMAGE and (action.amount or 0) >= 8 and action.target is not None

    def window(self, action: Action) -> tuple[int, int]:
        return (100, 350)

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        if not self.is_active_at(action, t_ms) or action.target is None:
            return [], None
        # Sine-ish offset — renderer reads `extra["offset_px"]` to apply
        import math
        phase = (t_ms - 100) / 50.0
        offset_px = int(3 * math.sin(phase * math.pi))
        return [CardEmission(
            side=action.target.side, position=action.target.position,
            kind="shake", extra={"offset_px": offset_px},
        )], None


class PulsePrimitive(Primitive):
    """Buff/shield expanding ring (V1.x — stub)."""

    name = "pulse"

    def applies_to(self, action: Action) -> bool:
        return action.kind in (ActionKind.BUFF, ActionKind.SHIELD)

    def window(self, action: Action) -> tuple[int, int]:
        return (0, 500)

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        if not self.is_active_at(action, t_ms):
            return [], None
        radius = (t_ms / 500.0)
        return [CardEmission(
            side=action.actor.side, position=action.actor.position,
            kind="pulse", color=resolve_color(action),
            extra={"radius": radius},
        )], None


class GlowPrimitive(Primitive):
    """Persistent legendary-card glow (V1.x — stub, orthogonal to actions)."""

    name = "glow"

    def applies_to(self, action: Action) -> bool:
        return False  # Stub — real behavior: always-on on legendary cards, not action-gated

    def window(self, action: Action) -> tuple[int, int]:
        return (0, ACTION_BEAT_MS)

    def emit(self, action: Action, t_ms: int) -> tuple[list[CardEmission], Optional[ConnectionEmission]]:
        return [], None


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
        return cls([
            ColorFlashPrimitive(),
            ConnectionLinePrimitive(),
            OverlayIconPrimitive(),
            HpTickPrimitive(),
        ])

    @classmethod
    def with_v1x_preview(cls) -> "PrimitiveRegistry":
        """Default + stubbed V1.x primitives — opt-in for testing forward-compat."""
        reg = cls.default()
        reg.add(ShakePrimitive())
        reg.add(PulsePrimitive())
        reg.add(GlowPrimitive())
        return reg
