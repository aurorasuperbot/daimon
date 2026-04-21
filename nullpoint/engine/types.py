"""Engine type definitions.

All types here are pure data — no methods, no string parsing, no I/O.
Engine consumes these structures and produces RoundLog/MatchResult.

V2 (monster pivot, 2026-04-21):
  - Slot enum REMOVED. A team is 6 monsters with positions 0..5.
  - Positions are mutable strategy choices, not anatomical constraints.
  - Card gains `element` (for type-effectiveness) and `species` (for evolution families).
  - UnitState carries `status: dict[int,int]` for ticking status conditions
    (burn/chill/root/charge — populated in later phases).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Team structure: 6 monsters at positions 0..5. Position is strategy, not anatomy.
# ---------------------------------------------------------------------------

TEAM_SIZE = 6


# ---------------------------------------------------------------------------
# Elements: for type-effectiveness. 5-element rock-paper-scissors loop.
# ---------------------------------------------------------------------------

class Element(IntEnum):
    FIRE = 1
    WATER = 2
    NATURE = 3
    VOLT = 4
    VOID = 5


# ---------------------------------------------------------------------------
# Status conditions: persistent effects that tick across rounds.
# Phase 6 wires the application + tick logic; phase 1 just reserves the ids.
# ---------------------------------------------------------------------------

class StatusCondition(IntEnum):
    BURN = 1     # 3 dmg at round start, ticks down
    CHILL = 2    # spd_mod -3 while active
    ROOT = 3     # skip attack while active
    CHARGE = 4   # next attack +6 atk, consumed on use


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
# Card: pure stat block + element + species + zero-or-more triggers.
# NO TEXT, NO NAME, NO FLAVOR, NO ART. The render layer owns all of that.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Card:
    card_id: str            # opaque identifier, unique within a pack
    species: str            # family identifier (e.g. "embercub"); legendary/rare/uncommon
                            # forms of the same creature share species
    element: Element
    atk: int
    defense: int            # 'def' is a Python keyword
    hp: int
    spd: int
    triggers: tuple[Trigger, ...] = ()

    def __post_init__(self) -> None:
        for v, name in [(self.atk, "atk"), (self.defense, "def"),
                        (self.hp, "hp"), (self.spd, "spd")]:
            if not isinstance(v, int) or v < 0:
                raise ValueError(f"Card.{name} must be non-negative int")
        if not isinstance(self.species, str) or not self.species:
            raise ValueError("Card.species must be non-empty string")
        if not isinstance(self.element, Element):
            raise TypeError("Card.element must be Element enum")


# ---------------------------------------------------------------------------
# Runtime unit state: mutable during a match.
# ---------------------------------------------------------------------------

@dataclass
class UnitState:
    card: Card
    position: int         # 0..5, team ordering (replaces slot)
    side: int             # 0 or 1
    hp: int               # current
    atk_mod: int = 0
    def_mod: int = 0
    spd_mod: int = 0
    shield: int = 0
    alive: bool = True
    # status[StatusCondition.BURN] = remaining rounds. 0/missing = not active.
    status: Dict[int, int] = field(default_factory=dict)

    @property
    def effective_atk(self) -> int:
        base = max(0, self.card.atk + self.atk_mod)
        # Charge consumes on read; combat resolver pops the flag on hit.
        return base

    @property
    def effective_def(self) -> int:
        return max(0, self.card.defense + self.def_mod)

    @property
    def effective_spd(self) -> int:
        chill_penalty = 3 if self.status.get(int(StatusCondition.CHILL), 0) > 0 else 0
        return max(0, self.card.spd + self.spd_mod - chill_penalty)


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
