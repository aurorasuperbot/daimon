"""Match wire protocol — JSON schema (v2, monster-pivot).

The engine resolves a full match deterministically, then emits one JSON file per
event to `~/.daimon/inbox/`. The battle UI reads the file and controls the
*visual* pacing (30-60s) even though the underlying resolution took ms.

V2 pivot (from V1 slot-based):
  - positions 0..5 replace the Slot enum (HEAD/TORSO/ARM_L/ARM_R/LEGS/CORE)
  - LoadoutCard gains `element` (FIRE/WATER/NATURE/VOLT/VOID) + `species`
  - hp_after keys change from "player/arm_l" to "player/0"
  - schema_version bumped 1 → 2; parsers refuse anything else

Forward-compatibility hooks (from V1 spec, retained):
  - `schema_version`  — bump when the wire changes; unknown versions refuse
  - `render_hints`    — optional view-layer knobs (reveal mode, pacing, profile)
  - `vis_overrides` on each action — override color/icon/log for weird cards

Renderers (PIL, Textual, HTML) all consume Match unchanged.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

TEAM_POSITIONS = 6  # team has positions 0..5


class Side(str, Enum):
    PLAYER = "player"
    OPPONENT = "opponent"
    # DRAW is only valid as `Outcome.winner` (no surviving side claimed the
    # match). It MUST NOT appear on CardRef.side, Round.first_player, or any
    # actor/target field — those are participant-side identifiers, and a
    # "draw" actor is meaningless. Adapter enforces this asymmetry.
    DRAW = "draw"


class Element(str, Enum):
    """Monster elemental types. Mirrors engine.types.Element names.

    NORMAL is outside the type-effectiveness ring — see
    `daimon.engine.elements` for the affinity rules. Render layer treats it
    as a neutral / utility tint (white in primitives, WHITE in HUD).
    """
    FIRE = "fire"
    WATER = "water"
    NATURE = "nature"
    VOLT = "volt"
    VOID = "void"
    NORMAL = "normal"


class ActionKind(str, Enum):
    """Drives the default animation primitives (color + icon)."""
    DAMAGE = "damage"
    HEAL = "heal"
    BUFF = "buff"
    DEBUFF = "debuff"
    SHIELD = "shield"
    STATUS = "status"
    DEATH = "death"
    PASSIVE = "passive"


class CardRef(BaseModel):
    """A runtime reference to a card in a specific team position on a specific side."""
    model_config = ConfigDict(extra="forbid")

    side: Side
    position: int = Field(ge=0, lt=TEAM_POSITIONS)
    card: str                         # display name, e.g. "Scoutling"


class LoadoutCard(BaseModel):
    """Full card definition as rendered in the grid."""
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=0, lt=TEAM_POSITIONS)
    species: str                      # e.g. "scoutling" — the monster family
    element: Element                  # drives header chip color + icon
    name: str                         # display name (usually titlecase of species)
    short_name: Optional[str] = None  # 6-8 char label for grid cell
    hp_max: int
    hp: int                           # starting HP (usually == hp_max but buffs shift)
    rarity: Literal["common", "uncommon", "rare", "epic", "legendary"] = "common"
    art_path: Optional[str] = None    # relative to daimon-cards/art/ for chafa/PIL


class Participant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    rank: str                         # e.g. "Veteran #18", "Champion"
    loadout: list[LoadoutCard]        # must have 6 entries covering positions 0..5


# ---------------------------------------------------------------------------
# Actions + rounds
# ---------------------------------------------------------------------------

class VisOverrides(BaseModel):
    """Escape hatch for cards whose visual hint doesn't match their kind."""
    model_config = ConfigDict(extra="forbid")

    color: Optional[str] = None       # override color flash
    icon: Optional[str] = None        # override overlay icon
    suppress_line: bool = False       # skip connection line
    log_override: Optional[str] = None  # custom log line text


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str                    # "r1_a1" — unique within the match
    actor: CardRef
    target: Optional[CardRef] = None  # None for untargeted passives
    kind: ActionKind
    amount: Optional[int] = None      # dmg/heal amount; None for qualitative
    hp_after: dict[str, int] = Field(default_factory=dict)  # "player/0": 6
    status_applied: Optional[str] = None  # e.g. "BURN", "CHILL" — V2 status plumbing
    triggers: list["Action"] = Field(default_factory=list)   # reactive cascade (recursive)
    reason: Optional[str] = None      # e.g. "ON_DAMAGE", "PRE_ROUND"
    log_line: str = ""                # human-readable (renderer types it out)
    vis_overrides: Optional[VisOverrides] = None


# Support recursive triggers
Action.model_rebuild()


class Round(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round: int
    first_player: Side                # round-alternating per locked rule #30
    actions: list[Action]


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

class MatchStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards_killed: dict[str, int] = Field(default_factory=dict)      # {"player": 2, "opponent": 4}
    biggest_hit: Optional[dict] = None                               # {"by": "Scoutling", "amount": 11}
    longest_survivor: Optional[dict] = None                          # {"card": "Iron Boar", "hp_remaining": 15}
    round_count: int = 0


class Rewards(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: int = 0
    rank_delta: str = "+0"            # "+2", "-1", "+0"


class Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winner: Side
    player_hp_remaining: int
    opponent_hp_remaining: int
    stats: MatchStats
    rewards: Rewards


# ---------------------------------------------------------------------------
# Forward-compat hooks
# ---------------------------------------------------------------------------

class RenderHints(BaseModel):
    """View-layer knobs. Renderer respects; engine ignores. Optional."""
    model_config = ConfigDict(extra="forbid")

    reveal_mode: Literal["full", "on_act"] = "full"
    animation_profile: Literal["standard", "fast", "slow"] = "standard"
    pacing_multiplier: float = 1.0


# ---------------------------------------------------------------------------
# Top-level Match
# ---------------------------------------------------------------------------

class Match(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    event_type: Literal["match"] = "match"
    match_id: str
    kind: Literal["pve", "pvp_async"] = "pve"
    timestamp: str                    # ISO8601 UTC
    participants: dict[str, Participant]   # keys: "player", "opponent"
    seed: Optional[str] = None
    rounds: list[Round]
    outcome: Outcome
    render_hints: RenderHints = Field(default_factory=RenderHints)
    capabilities_required: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hp_key(side: Side, position: int) -> str:
    """Canonical hp_after key format: e.g. Side.PLAYER, 0 → 'player/0'."""
    return f"{side.value}/{position}"
