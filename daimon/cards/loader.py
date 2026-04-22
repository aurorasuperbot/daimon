"""Card loader.

Reads card JSON files from disk and produces Card objects. The loader is
responsible for VALIDATING and DROPPING all flavor text — only mechanical
fields ever enter the engine.

Card JSON schema (V2 — monster pivot):
{
  "card_id": "string",         # unique within pack
  "species": "string",         # family identifier; rarity tiers of same
                               #   creature share species (e.g. "embercub")
  "element": "FIRE|WATER|NATURE|VOLT|VOID|NORMAL",
  "atk": int >= 0,
  "def": int >= 0,
  "hp":  int >= 0,
  "spd": int >= 0,
  "triggers": [
      {
        "when":  "ON_BATTLE_START|ON_ROUND_START|ON_ATTACK|ON_TAKE_DAMAGE|"
                 "ON_DEATH|ON_ALLY_DEATH|ON_TURN_END|ON_KILL|ON_LOW_HP|"
                 "ON_OPENING_ATTACK",
        "op":    "BUFF_ATK|DEBUFF_ATK|BUFF_DEF|DEBUFF_DEF|HEAL|DAMAGE|"
                 "ADD_SHIELD|BUFF_SPD|APPLY_BURN|APPLY_STUN|APPLY_SILENCE|"
                 "APPLY_TAUNT|APPLY_POISON|LIFESTEAL",
        "target":"SELF|ALL_ALLIES|ALL_ENEMIES|LOWEST_HP_ENEMY|HIGHEST_HP_ENEMY|RANDOM_ENEMY|RANDOM_ALLY",
        "value": int,
        "condition": "string?"   # optional DSL expression — see daimon/engine/conditions.py
                                 # parsed + validated at load time, evaluated at fire time
      }
  ],

  // Render-only fields (NEVER read by engine):
  "name":   "string",
  "flavor": "string",
  "rarity": "common|uncommon|rare|epic|legendary",
  "art":    "string",          # path or hash; render-layer only
  "moves": [                   # optional display-only named abilities
      {"name": "string", "when": "ON_ATTACK|..."}
  ]
}

V1 catalogs (with `slot` field) are rejected with a clear migration hint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from daimon.engine.conditions import ConditionError, parse as parse_condition
from daimon.engine.types import (
    Card,
    EffectOp,
    Element,
    TargetFilter,
    Trigger,
    TriggerWhen,
)


# Valid rarity values accepted by the schema's LoadoutCard.
_VALID_RARITIES = ("common", "uncommon", "rare", "epic", "legendary")

# Stat bounds — schema-level defense against malformed cards.
MAX_STAT = 999
MAX_TRIGGER_VALUE = 999
MAX_TRIGGERS_PER_CARD = 8
MAX_CONDITION_LEN = 256  # defensive cap on condition expression length


def _parse_enum(enum_cls: type, value: Any, field: str) -> int:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be string enum name, got {type(value).__name__}")
    try:
        return enum_cls[value]
    except KeyError:
        valid = ", ".join(m.name for m in enum_cls)
        raise ValueError(f"{field}={value!r} invalid; expected one of: {valid}")


def _parse_stat(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be int, got {type(value).__name__}")
    if value < 0 or value > MAX_STAT:
        raise ValueError(f"{field}={value} out of range [0, {MAX_STAT}]")
    return value


def _parse_trigger(d: Dict[str, Any], idx: int) -> Trigger:
    if not isinstance(d, dict):
        raise ValueError(f"trigger[{idx}] must be object")
    when = _parse_enum(TriggerWhen, d.get("when"), f"trigger[{idx}].when")
    op = _parse_enum(EffectOp, d.get("op"), f"trigger[{idx}].op")
    target = _parse_enum(TargetFilter, d.get("target"), f"trigger[{idx}].target")
    value = d.get("value")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"trigger[{idx}].value must be int")
    if abs(value) > MAX_TRIGGER_VALUE:
        raise ValueError(f"trigger[{idx}].value={value} out of range")

    # Optional `condition` DSL string. Validated at LOAD time so a bad
    # expression breaks catalog load (not mid-match — engine determinism
    # depends on conditions never raising at fire-time). The compiled callable
    # is rebuilt at fire-time via lru_cache in combat.py; we discard the AST
    # here and only retain the source string on the Trigger.
    condition_raw = d.get("condition")
    condition: Optional[str] = None
    if condition_raw is not None:
        if not isinstance(condition_raw, str):
            raise ValueError(
                f"trigger[{idx}].condition must be string, got "
                f"{type(condition_raw).__name__}"
            )
        if len(condition_raw) > MAX_CONDITION_LEN:
            raise ValueError(
                f"trigger[{idx}].condition length {len(condition_raw)} "
                f"exceeds cap {MAX_CONDITION_LEN}"
            )
        try:
            parse_condition(condition_raw)
        except ConditionError as e:
            raise ValueError(
                f"trigger[{idx}].condition invalid: {e}"
            ) from e
        condition = condition_raw

    return Trigger(when=when, op=op, target=target, value=value, condition=condition)


def load_card_dict(d: Dict[str, Any]) -> Card:
    """Load a Card from a parsed dict. Drops all flavor fields."""
    if not isinstance(d, dict):
        raise ValueError("card must be a JSON object")

    # V1 catalog detection — clear migration error.
    if "slot" in d and "element" not in d:
        raise ValueError(
            "legacy V1 card detected (has 'slot', missing 'element'). "
            "Run scripts/migrate_v1_catalog.py or drop the slot field and add "
            "'element' + 'species' (see cards/loader.py docstring)."
        )

    card_id = d.get("card_id")
    if not isinstance(card_id, str) or not card_id:
        raise ValueError("card_id must be non-empty string")

    species = d.get("species")
    if not isinstance(species, str) or not species:
        raise ValueError("species must be non-empty string")

    element = _parse_enum(Element, d.get("element"), "element")
    atk = _parse_stat(d.get("atk"), "atk")
    defense = _parse_stat(d.get("def"), "def")
    hp = _parse_stat(d.get("hp"), "hp")
    spd = _parse_stat(d.get("spd"), "spd")

    triggers_raw = d.get("triggers", [])
    if not isinstance(triggers_raw, list):
        raise ValueError("triggers must be array")
    if len(triggers_raw) > MAX_TRIGGERS_PER_CARD:
        raise ValueError(
            f"too many triggers ({len(triggers_raw)} > {MAX_TRIGGERS_PER_CARD})"
        )
    triggers = tuple(_parse_trigger(t, i) for i, t in enumerate(triggers_raw))

    return Card(
        card_id=card_id,
        species=species,
        element=element,
        atk=atk,
        defense=defense,
        hp=hp,
        spd=spd,
        triggers=triggers,
    )


def load_card_json(text: str) -> Card:
    return load_card_dict(json.loads(text))


def load_card(path: Path | str) -> Card:
    p = Path(path)
    return load_card_json(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Display metadata extraction
#
# The engine never sees these fields — they're dropped by `load_card_dict`.
# But the play-layer renderer needs them (real card name, rarity for border
# color, art path for artwork). This helper pulls them out of the same raw
# card dict so MCP can thread them into the adapter.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CardDisplayFields:
    """Render-only metadata pulled from a card JSON. All fields optional —
    the adapter synthesizes defaults (titlecased species, "common" rarity)
    when a field is missing."""
    name: Optional[str] = None
    short_name: Optional[str] = None
    rarity: Optional[str] = None
    art_path: Optional[str] = None
    flavor: Optional[str] = None


def _derive_short_name(full_name: str) -> str:
    """6–8 char label for a grid cell; matches the fixture convention.

    Rules (picked to match existing fixture short_names like "Voltc" /
    "Tide" / "Boar" / "Fox L"):
      * Single-word names → first 5 chars.
      * Multi-word names → first word truncated to 5 chars + first char of
        the remainder (e.g. "Voltcat Apex" → "Voltc A"), capped at 8 chars.
    """
    if not full_name:
        return ""
    parts = full_name.split()
    if len(parts) == 1:
        return parts[0][:5]
    head = parts[0][:5]
    tail = parts[1][:1].upper()
    return f"{head} {tail}"[:8]


def extract_display_fields(d: Dict[str, Any]) -> CardDisplayFields:
    """Extract the render-only fields from a raw card JSON dict.

    Returns a CardDisplayFields with None for every missing field. Rarity is
    validated against `_VALID_RARITIES` — an unknown value becomes None
    (caller falls back to "common" via the adapter default).
    """
    if not isinstance(d, dict):
        return CardDisplayFields()

    name = d.get("name") if isinstance(d.get("name"), str) else None
    flavor = d.get("flavor") if isinstance(d.get("flavor"), str) else None
    art = d.get("art") if isinstance(d.get("art"), str) else None

    rarity_raw = d.get("rarity")
    rarity = rarity_raw if (
        isinstance(rarity_raw, str) and rarity_raw.lower() in _VALID_RARITIES
    ) else None
    if rarity is not None:
        rarity = rarity.lower()

    # short_name can be user-supplied or derived from the full name.
    short_raw = d.get("short_name")
    if isinstance(short_raw, str) and short_raw:
        short_name: Optional[str] = short_raw
    elif name is not None:
        short_name = _derive_short_name(name)
    else:
        short_name = None

    return CardDisplayFields(
        name=name,
        short_name=short_name,
        rarity=rarity,
        art_path=art,
        flavor=flavor,
    )
