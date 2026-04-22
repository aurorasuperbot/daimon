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
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Team structure: 6 monsters at positions 0..5. Position is strategy, not anatomy.
# ---------------------------------------------------------------------------

TEAM_SIZE = 6


# ---------------------------------------------------------------------------
# Elements: for type-effectiveness. Six total — five form a closed
# rock-paper-scissors-plus-void ring (FIRE→NATURE→WATER→VOLT→VOID→FIRE),
# and NORMAL stands deliberately OUTSIDE that ring: it never gives a bonus,
# never receives a bonus, and always resolves to 1.0× damage. NORMAL is the
# "splashable support" element — the home of utility monsters that slot into
# any archetype-aligned deck without skewing the matchup math.
# ---------------------------------------------------------------------------

class Element(IntEnum):
    FIRE = 1
    WATER = 2
    NATURE = 3
    VOLT = 4
    VOID = 5
    NORMAL = 6   # outside the type ring; always neutral (1.0×) vs everything


# ---------------------------------------------------------------------------
# Status conditions: persistent effects that tick across rounds.
# Phase 6 wires the application + tick logic; phase 1 just reserves the ids.
# ---------------------------------------------------------------------------

class StatusCondition(IntEnum):
    BURN = 1      # 3 dmg at round start, ticks down
    CHILL = 2     # spd_mod -3 while active
    ROOT = 3      # skip attack while active
    CHARGE = 4    # next attack +6 atk, consumed on use
    # Phase-2 additions (V1 vocab expansion, 2026-04-22):
    STUN = 5      # skip next action; ticks down once per round
    SILENCE = 6   # all triggers on this unit suppressed; ticks down at round-start
    TAUNT = 7     # enemies must target this unit first (priority override)
    POISON = 8    # alternative DOT (2 dmg / round, distinct from BURN's 3 dmg)


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
    # Phase-2 additions:
    ON_TURN_END = 7          # fires on the unit AFTER its action this round
    ON_KILL = 8              # fires on attacker when its attack KOs the target
    ON_LOW_HP = 9            # fires once when self.hp drops below 25% of card.hp
    ON_OPENING_ATTACK = 10   # fires on a unit's first attack of the match


class EffectOp(IntEnum):
    """What the trigger does. All ops take an integer value.

    The integer-value semantics vary by op:
      - BUFF_*/DEBUFF_*/HEAL/DAMAGE/ADD_SHIELD: magnitude
      - APPLY_BURN/STUN/SILENCE/TAUNT/POISON: duration in rounds
      - LIFESTEAL: damage dealt (heal-back is half of dealt-damage, ceil-rounded)
    """
    BUFF_ATK = 1
    DEBUFF_ATK = 2
    BUFF_DEF = 3
    DEBUFF_DEF = 4
    HEAL = 5
    DAMAGE = 6
    ADD_SHIELD = 7
    BUFF_SPD = 8
    # Phase-2 additions:
    APPLY_BURN = 9       # value = duration in rounds
    APPLY_STUN = 10      # value = duration in rounds (1 = next action only)
    APPLY_SILENCE = 11   # value = duration in rounds
    APPLY_TAUNT = 12     # value = duration in rounds
    APPLY_POISON = 13    # value = duration in rounds
    LIFESTEAL = 14       # value = damage dealt; attacker heals ceil(value/2)


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
    """A trigger is a (when, op, target, value) 4-tuple plus optional condition.

    `condition`, when not None, is a DSL string evaluated at fire-time against
    the unit + match context. The trigger fires only if the condition evaluates
    truthy. Grammar lives in `daimon/engine/conditions.py`. Examples:
      "team.distinct_elements >= 2"
      "self.hp < self.hp_max * 0.5"
      "enemies.alive_count <= 2"
    The string is parsed-and-validated at card-load time (not at fire-time);
    invalid conditions raise during catalog load, not mid-match.
    """
    when: TriggerWhen
    op: EffectOp
    target: TargetFilter
    value: int
    condition: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.value, int):
            raise TypeError("Trigger.value must be int")
        if self.condition is not None and not isinstance(self.condition, str):
            raise TypeError("Trigger.condition must be string or None")


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
    # Phase-2 lifecycle flags (one-shot bookkeeping, not part of status):
    low_hp_fired: bool = False         # ON_LOW_HP fires at most once per match
    has_attacked: bool = False         # set after the unit's first attack;
                                       # gates ON_OPENING_ATTACK

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

# ---------------------------------------------------------------------------
# Structured combat event — emitted alongside the human string log.
#
# A.4.a (2026-04-21): the engine has historically emitted ONLY string traces
# into RoundLog.actions. The renderer (play/schema.py::Action) needs structured
# data — actor/target sides + positions, kind, hp_after deltas, nested
# reactive triggers. CombatEvent IS that structured form, in engine-native
# types (int sides, IntEnum elements, tuple keys for hp_after). The adapter
# (play/adapter.py) maps CombatEvent 1:1 to play.schema.Action, doing the
# enum-string and side-encoding renames at the seam.
#
# Design notes:
#   - Every existing log.append() in combat.py gets a sibling event.append().
#   - String emission is kept exactly as-is; events are purely additive — the
#     17 existing combat tests must remain green untouched.
#   - Reactive triggers (ON_TAKE_DAMAGE counter, ON_DEATH effect, etc.) nest
#     under the parent action's `triggers` list, not into the round's top-level
#     events. ON_BATTLE_START / ON_ROUND_START events are top-level (no parent
#     action exists for those — they're proactive, not reactive).
# ---------------------------------------------------------------------------

@dataclass
class CombatEvent:
    """One structured combat event. Mirrors `play.schema.Action` shape, in
    engine-native types. The adapter renames sides (0/1 -> "player"/"opponent"),
    elements (IntEnum -> str enum), and hp_after keys (tuple -> "side/pos" str).

    `kind` matches `play.schema.ActionKind` values (lowercase string):
      "damage" | "heal" | "buff" | "debuff" | "shield" | "death" | "status" | "passive"

    `reason` records the trigger context when the event was emitted as a
    reactive cascade — one of "ON_BATTLE_START" | "ON_ROUND_START" |
    "ON_ATTACK" | "ON_TAKE_DAMAGE" | "ON_DEATH" | "ON_ALLY_DEATH" |
    "STATUS_TICK" | "PRE_ROUND" | None (for primary actor actions).

    `hp_after` keys are (side, position) tuples; the adapter formats them as
    "player/0" / "opponent/3" strings per the schema spec.
    """
    kind: str
    actor_side: int
    actor_position: int
    actor_card_id: str
    target_side: Optional[int] = None
    target_position: Optional[int] = None
    target_card_id: Optional[str] = None
    amount: Optional[int] = None
    hp_after: Dict[Tuple[int, int], int] = field(default_factory=dict)
    reason: Optional[str] = None
    status_applied: Optional[str] = None
    log_line: str = ""
    triggers: List["CombatEvent"] = field(default_factory=list)


@dataclass
class RoundLog:
    round_number: int
    # Round-alternating first-player for trigger ordering & tie-breaking
    # (locked design rule #30, 2026-04-21). Round 1 -> side 0, round 2 -> side 1, ...
    first_player: int = 0
    actions: List[str] = field(default_factory=list)  # human-readable trace
    # Structured event stream — additive sibling of `actions` (A.4.a).
    # Same logical content as `actions` but as nested CombatEvent records.
    # Renderers consume this; legacy text consumers keep using `actions`.
    events: List["CombatEvent"] = field(default_factory=list)
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
