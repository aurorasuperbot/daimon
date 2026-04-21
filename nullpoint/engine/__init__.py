"""Engine kernel — deterministic 6-monster autobattler (V2).

Pure math. Integer-only where possible (element multipliers force float in
damage resolution, then ceil back to int).  Never reads strings from cards.
Combat is a function: (loadout_a, loadout_b, seed) -> MatchResult.
"""

from nullpoint.engine.combat import resolve_match
from nullpoint.engine.elements import element_multiplier
from nullpoint.engine.loadout import Loadout, validate_loadout
from nullpoint.engine.types import (
    Card,
    EffectOp,
    Element,
    MatchResult,
    RoundLog,
    StatusCondition,
    TEAM_SIZE,
    TargetFilter,
    Trigger,
    TriggerWhen,
    UnitState,
)

__all__ = [
    "Card",
    "EffectOp",
    "Element",
    "Loadout",
    "MatchResult",
    "RoundLog",
    "StatusCondition",
    "TEAM_SIZE",
    "TargetFilter",
    "Trigger",
    "TriggerWhen",
    "UnitState",
    "element_multiplier",
    "resolve_match",
    "validate_loadout",
]
