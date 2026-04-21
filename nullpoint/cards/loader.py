"""Card loader.

Reads card JSON files from disk and produces Card objects. The loader is
responsible for VALIDATING and DROPPING all flavor text — only mechanical
fields ever enter the engine.

Card JSON schema (V1):
{
  "card_id": "string",         # unique within pack
  "slot": "HEAD|TORSO|ARM_L|ARM_R|LEGS|CORE",
  "atk": int >= 0,
  "def": int >= 0,
  "hp": int >= 0,
  "spd": int >= 0,
  "triggers": [
      {
        "when": "ON_BATTLE_START|ON_ROUND_START|ON_ATTACK|ON_TAKE_DAMAGE|ON_DEATH|ON_ALLY_DEATH",
        "op":   "BUFF_ATK|DEBUFF_ATK|BUFF_DEF|DEBUFF_DEF|HEAL|DAMAGE|ADD_SHIELD|BUFF_SPD",
        "target":"SELF|ALL_ALLIES|ALL_ENEMIES|LOWEST_HP_ENEMY|HIGHEST_HP_ENEMY|RANDOM_ENEMY|RANDOM_ALLY",
        "value": int
      }
  ],

  // Render-only fields (NEVER read by engine):
  "name": "string",
  "flavor": "string",
  "rarity": "common|uncommon|rare|epic|legendary",
  "art": "string"               # path or hash; render-layer only
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from nullpoint.engine.types import (
    Card,
    EffectOp,
    Slot,
    TargetFilter,
    Trigger,
    TriggerWhen,
)

# Stat bounds — schema-level defense against malformed cards.
MAX_STAT = 999
MAX_TRIGGER_VALUE = 999
MAX_TRIGGERS_PER_CARD = 8


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
    return Trigger(when=when, op=op, target=target, value=value)


def load_card_dict(d: Dict[str, Any]) -> Card:
    """Load a Card from a parsed dict. Drops all flavor fields."""
    if not isinstance(d, dict):
        raise ValueError("card must be a JSON object")

    card_id = d.get("card_id")
    if not isinstance(card_id, str) or not card_id:
        raise ValueError("card_id must be non-empty string")

    slot = _parse_enum(Slot, d.get("slot"), "slot")
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
        slot=slot,
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
