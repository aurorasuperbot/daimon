"""Animator — runs the primitive registry against an action at time t.

The Animator is the seam between Match data and BattleFrame. It knows NOTHING
about rendering (PIL, daimon.ui, HTML); it only knows how to apply a list of
primitives to an action and collect their emissions.

This is the extensibility contract promised in the locked spec (2026-04-22):
adding a primitive = subclass + register, zero frame.py changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from daimon.play.primitives import (
    CardEmission,
    ConnectionEmission,
    Cue,
    Primitive,
    PrimitiveRegistry,
)
from daimon.play.schema import Action, ActionKind, Side


@dataclass
class AnimationSnapshot:
    """Everything the primitive registry emitted for a given (action, t_ms) pair.

    The frame builder consumes this and attaches the effects to the right
    CardState entries. The renderer paints from CardState.effects, NOT from
    the snapshot directly — keeps the rendering side clean.

    Two aggregated, non-card fields:
      - `pause_ms`: max of any HitPausePrimitive emissions in this tick. The
        playback loop reads this and stretches its tick budget by this much.
      - `cues`: ordered list of Cue enum values to surface to the audio
        backend. Always discrete events (HIT/KO/BUFF/etc), never continuous.
    """
    card_emissions: list[CardEmission] = field(default_factory=list)
    connection: Optional[ConnectionEmission] = None
    pause_ms: int = 0
    cues: list[Cue] = field(default_factory=list)

    def for_card(self, side: Side, position: int) -> list[CardEmission]:
        """Return emissions targeting a specific card, in registry order."""
        return [e for e in self.card_emissions if e.side == side and e.position == position]


# Map action kind → cue. Used by the animator when collecting per-action cues.
KIND_TO_CUE = {
    ActionKind.DAMAGE: Cue.HIT,
    ActionKind.DEATH: Cue.KO,
    ActionKind.HEAL: Cue.BUFF,
    ActionKind.BUFF: Cue.BUFF,
    ActionKind.SHIELD: Cue.BUFF,
    ActionKind.DEBUFF: Cue.DEBUFF,
    ActionKind.STATUS: Cue.DEBUFF,
}


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
            # Hoist hit_pause emissions into the snapshot's pause_ms field
            # (rather than leaving them on the cards). Renderers never paint
            # them; the playback loop reads them.
            for emit in card_emits:
                if emit.kind == "hit_pause":
                    pms = int(emit.extra.get("pause_ms", 0))
                    if pms > snapshot.pause_ms:
                        snapshot.pause_ms = pms
                else:
                    snapshot.card_emissions.append(emit)
            # Last-write-wins for connection line (one line per beat, by convention)
            if conn_emit is not None:
                snapshot.connection = conn_emit

        # Cues fire once at the start of the beat (t_ms == 0). Map by
        # action.kind. KO supersedes HIT if the target's hp_after went to 0.
        if t_ms == 0:
            cue = KIND_TO_CUE.get(action.kind)
            if cue is not None:
                # Promote HIT → KO if the target died this beat.
                if cue == Cue.HIT and action.target is not None:
                    target_key = f"{action.target.side.value}/{action.target.position}"
                    if action.hp_after.get(target_key, 1) <= 0:
                        cue = Cue.KO
                snapshot.cues.append(cue)
        return snapshot

    def describe(self) -> str:
        """Debug — return a compact string of registered primitive names."""
        return "[" + ", ".join(self.registry.names()) + "]"
