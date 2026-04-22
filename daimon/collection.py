"""Local card collection store.

The collection is the agent's owned card inventory: a list of *serials*,
each one a unique UUID instance of a `card_id` from a pack. Storage:

  ~/.config/daimon/collection.json

Schema:
  {
    "pubkey_hex": "...",
    "serials": [
      {
        "serial": "uuid",
        "card_id": "...",
        "pack": "v1_alpha",
        "rarity": "rare",
        "minted_at": "iso-ts",
        "minted_via": "pull" | "starter_grant",
        "ledger_entry_hash": "..."   # tying provenance to a ledger event
      },
      ...
    ]
  }

Why instance-level UUIDs? Each minted card is a tradeable asset (V1.5 trade
protocol uses serials as the primary key). Two pulls of the same card_id
produce two distinct serials.

This module is pure I/O over JSON — pull logic is in `pulls.py`.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Single shared config dir (DAIMON_HOME / XDG_CONFIG_HOME-aware), resolved at
# import time in identity.keys — see _resolve_config_dir there.
from daimon.identity.keys import CONFIG_DIR  # noqa: E402

COLLECTION_PATH = CONFIG_DIR / "collection.json"


@dataclass(frozen=True)
class Serial:
    serial: str
    card_id: str
    pack: str
    rarity: str
    minted_at: str
    minted_via: str
    ledger_entry_hash: Optional[str] = None


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def new_serial(card_id: str,
               pack: str,
               rarity: str,
               minted_via: str = "pull",
               ledger_entry_hash: Optional[str] = None) -> Serial:
    return Serial(
        serial=str(uuid.uuid4()),
        card_id=card_id,
        pack=pack,
        rarity=rarity,
        minted_at=_now_iso(),
        minted_via=minted_via,
        ledger_entry_hash=ledger_entry_hash,
    )


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def load_collection(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load collection from disk. Returns the document; creates none on disk."""
    if path is None:
        path = COLLECTION_PATH
    if not path.exists():
        return {"pubkey_hex": None, "serials": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"corrupt collection at {path}: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"collection root is not an object: {path}")
    data.setdefault("serials", [])
    if not isinstance(data["serials"], list):
        raise RuntimeError("collection.serials is not a list")
    return data


def save_collection(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    if path is None:
        path = COLLECTION_PATH
    _ensure_dir()
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def append_serial(serial: Serial,
                  *,
                  pubkey_hex: Optional[str] = None,
                  path: Optional[Path] = None) -> Dict[str, Any]:
    """Append a serial to the collection. Returns the updated doc."""
    if path is None:
        path = COLLECTION_PATH
    data = load_collection(path)
    if pubkey_hex and not data.get("pubkey_hex"):
        data["pubkey_hex"] = pubkey_hex
    data["serials"].append(asdict(serial))
    save_collection(data, path)
    return data


def list_serials(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    return load_collection(path).get("serials", [])


def count(path: Optional[Path] = None) -> int:
    return len(list_serials(path))
