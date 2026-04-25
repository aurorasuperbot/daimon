"""Equipped-skin persistence — which skin is mounted on which card.

Storage: ``~/.config/daimon/equipped_skins.json``

Schema:
    {
      "equipped": {
        "aegis_lion": "heretic_manuscript",
        "void_orchid": "ukiyoe_scroll"
      }
    }

A card with no entry uses its canonical base art (manifest.canonical →
base.png). The render layer's ``art_path_for(card_id)`` helper checks
this map and falls back when no skin is equipped.

Equip/unequip are pure pointer mutations — no currency cost, no ledger
event. They're free actions like changing your loadout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

from daimon.identity.keys import CONFIG_DIR
from daimon.shop.errors import NotOwnedError, SkinNotFoundError
from daimon.shop.owned import is_owned

EQUIPPED_PATH = CONFIG_DIR / "equipped_skins.json"


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def load_equipped(path: Optional[Path] = None) -> Dict[str, str]:
    """Return {card_id → skin_slug}. Empty dict if file missing/corrupt."""
    if path is None:
        path = EQUIPPED_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    eq = data.get("equipped", {}) if isinstance(data, dict) else {}
    if not isinstance(eq, dict):
        return {}
    # Sanitize: only keep string→string entries.
    return {k: v for k, v in eq.items()
            if isinstance(k, str) and isinstance(v, str)}


def _save_equipped(eq: Dict[str, str], path: Optional[Path] = None) -> None:
    if path is None:
        path = EQUIPPED_PATH
    _ensure_dir()
    payload = {"equipped": eq}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True,
                              ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def get_equipped(card_id: str, path: Optional[Path] = None) -> Optional[str]:
    """The skin_slug equipped on `card_id`, or None for canonical base."""
    return load_equipped(path).get(card_id)


def equip_skin(card_id: str, skin_slug: str,
               path: Optional[Path] = None,
               owned_path: Optional[Path] = None) -> Dict[str, str]:
    """Equip a skin on a card. Verifies ownership first.

    Returns the updated equipped map. Raises:
      - SkinNotFoundError: card_id/skin_slug doesn't reference any owned skin.
        (We don't validate against the live shop catalog — equipping a skin
        whose PNG was removed from the art-pack should still work; the render
        layer falls back to canonical when the PNG is gone.)
      - NotOwnedError: the skin isn't in the owned ledger.
    """
    if not card_id or not isinstance(card_id, str):
        raise SkinNotFoundError(f"invalid card_id: {card_id!r}")
    if not skin_slug or not isinstance(skin_slug, str):
        raise SkinNotFoundError(f"invalid skin_slug: {skin_slug!r}")
    if not is_owned(card_id, skin_slug, owned_path):
        raise NotOwnedError(
            f"you don't own skin {skin_slug!r} for card {card_id!r}"
        )
    eq = load_equipped(path)
    eq[card_id] = skin_slug
    _save_equipped(eq, path)
    return eq


def unequip_skin(card_id: str,
                 path: Optional[Path] = None) -> Dict[str, str]:
    """Revert a card to its canonical base art. No-op if no skin equipped."""
    eq = load_equipped(path)
    if card_id in eq:
        del eq[card_id]
        _save_equipped(eq, path)
    return eq
