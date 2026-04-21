"""Engine type definitions.

All types here are pure data — no methods, no string parsing, no I/O.
Engine consumes these structures and produces RoundLog/MatchResult.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional


# ---------------------------------------------------------------------------
# Slots: a loadout has 6 named slots. Slot identity is structural, not flavor.
# ---------------------------------------------------------------------------

class Slot(IntEnum):
    HEAD = 0
    TORSO = 1
    ARM_L = 2
    ARM_R = 3
    LEGS = 4
    CORE = 5


SLOT_COUNT = len(Slot)


# ---------------------------------------------------------------------------
# Triggers and effects: enum-coded so the engine never parses card text.
# ---------------------------------------------------------------------------

class TriggerWhen(IntEnum):
    """When during the match a trigger fires."""
    ON_BATTLE_START = 1
    ON_ROUND_START = 2
    ON_ATTACK = 3
    ON_TAKE_DAMAGE = 4
    ON_DEATH = 5
    ON_ALLY_DEATH = 6


class EffectOp(IntEnum):
    """What the trigger does. All ops take an integer value."""
    BUFF_ATK = 1
    DEBUFF_ATK = 2
    BUFF_DEF = 3
    DEBUFF_DEF = 4
    HEAL = 5
    DAMAGE = 6
    ADD_SHIELD = 7
    BUFF_SPD = 8


class TargetFilter(IntEnum):
    """Which units the effect applies to."""
    SELF = 1
    ALL_ALLIES = 2
    ALL_ENEMIES = 3
    LOWEST_HP_ENEMY = 4
    HIGHEST_HP_ENEMY = 5
    RANDOM_ENEMY = 6  # uses seeded RNG
    RANDOM_ALLY = 7


@dataclass(frozen=True)
class Trigger:
    """A trigger is a (when, op, target, value) 4-tuple. Pure ints."""
    when: TriggerWhen
    op: EffectOp
    target: TargetFilter
    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int):
            raise TypeError("Trigger.value must be int")


# ---------------------------------------------------------------------------
# Card: pure stat block + zero-or-more triggers. NO TEXT, NO NAME, NO FLAVOR.
# Card text/art lives in the card-definitions repo and is loaded only by
# the render layer, never the combat layer.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Card:
    card_id: str          # opaque identifier, never parsed for meaning
    slot: Slot
    atk: int
    defense: int          # 'def' is a Python keyword
    hp: int
    spd: int
    triggers: tuple[Trigger, ...] = ()

    def __post_init__(self) -> None:
        for v, name in [(self.atk, "atk"), (self.defense, "def"),
                        (self.hp, "hp"), (self.spd, "spd")]:
            if not isinstance(v, int) or v < 0:
                raise ValueError(f"Card.{name} must be non-negative int")


# ---------------------------------------------------------------------------
# Runtime unit state: mutable during a match.
# ---------------------------------------------------------------------------

@dataclass
class UnitState:
    card: Card
    slot: Slot
    side: int             # 0 or 1
    hp: int               # current
    atk_mod: int = 0
    def_mod: int = 0
    spd_mod: int = 0
    shield: int = 0
    alive: bool = True

    @property
    def effective_atk(self) -> int:
        return max(0, self.card.atk + self.atk_mod)

    @property
    def effective_def(self) -> int:
        return max(0, self.card.defense + self.def_mod)

    @property
    def effective_spd(self) -> int:
        return max(0, self.card.spd + self.spd_mod)


# ---------------------------------------------------------------------------
# Match outputs.
# ---------------------------------------------------------------------------

@dataclass
class RoundLog:
    round_number: int
    actions: List[str] = field(default_factory=list)  # human-readable trace
    side_a_hp_total: int = 0
    side_b_hp_total: int = 0


@dataclass
class MatchResult:
    seed: bytes
    rounds: List[RoundLog]
    winner: Optional[int]   # 0, 1, or None for draw
    side_a_final_hp: int
    side_b_final_hp: int
    reason: str             # "wipe", "round_cap", "draw"
