"""Animator — runs the primitive registry against an action at time t.

The Animator is the seam between Match data and BattleFrame. It knows NOTHING
about rendering (PIL, Textual, HTML); it only knows how to apply a list of
primitives to an action and collect their emissions.

This is the extensibility contract promised in the locked spec (2026-04-22):
adding a primitive = subclass + register, zero frame.py changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from nullpoint.play.primitives import (
    CardEmission,
    ConnectionEmission,
    Primitive,
    PrimitiveRegistry,
)
from nullpoint.play.schema import Action, Side


@dataclass
class AnimationSnapshot:
    """Everything the primitive registry emitted for a given (action, t_ms) pair.

    The frame builder consumes this and attaches the effects to the right
    CardState entries. The renderer paints from CardState.effects, NOT from
    the snapshot directly — keeps the rendering side clean.
    """
    card_emissions: list[CardEmission] = field(default_factory=list)
    connection: Optional[ConnectionEmission] = None

    def for_card(self, side: Side, position: int) -> list[CardEmission]:
        """Return emissions targeting a specific card, in registry order."""
        return [e for e in self.card_emissions if e.side == side and e.position == position]


class Animator:
    """Runs primitives against an action at a given t_ms.

    Usage:
        animator = Animator()                       # default 4 primitives
        snapshot = animator.tick(action, t_ms=250)
        # snapshot.card_emissions / snapshot.connection → attach to frame

    Custom registries:
        custom = PrimitiveRegistry([MyPrimitive(), ColorFlashPrimitive()])
        animator = Animator(registry=custom)
    """

    def __init__(self, registry: Optional[PrimitiveRegistry] = None):
        self.registry = registry if registry is not None else PrimitiveRegistry.default()

    def tick(self, action: Action, t_ms: int) -> AnimationSnapshot:
        """Run every primitive against the action at t_ms. Collect emissions."""
        snapshot = AnimationSnapshot()
        for prim in self.registry:
            if not prim.applies_to(action):
                continue
            card_emits, conn_emit = prim.emit(action, t_ms)
            snapshot.card_emissions.extend(card_emits)
            # Last-write-wins for connection line (one line per beat, by convention)
            if conn_emit is not None:
                snapshot.connection = conn_emit
        return snapshot

    def describe(self) -> str:
        """Debug — return a compact string of registered primitive names."""
        return "[" + ", ".join(self.registry.names()) + "]"
