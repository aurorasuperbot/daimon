"""Engine kernel — deterministic 6-slot autobattler.

Pure math. Integer-only. Never reads strings from cards.
Combat is a function: (loadout_a, loadout_b, seed) -> MatchResult.
"""

from nullpoint.engine.combat import resolve_match
from nullpoint.engine.loadout import Loadout, validate_loadout
from nullpoint.engine.types import (
    EffectOp,
    MatchResult,
    RoundLog,
    Slot,
    TriggerWhen,
)

__all__ = [
    "EffectOp",
    "Loadout",
    "MatchResult",
    "RoundLog",
    "Slot",
    "TriggerWhen",
    "resolve_match",
    "validate_loadout",
]
