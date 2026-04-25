"""Owned-skins persistence + weekly cap accounting.

Storage: ``~/.config/daimon/owned_skins.json``

Schema:
    {
      "owned": [
        {
          "card_id":   "aegis_lion",
          "skin_slug": "heretic_manuscript",
          "skin_name": "Heretic Manuscript",
          "skin_axis": "cultural",
          "rarity":    "rare",
          "purchased_at": "2026-04-25T14:23:11Z",
          "cost":      300,
          "ledger_entry_hash": "sha256(...)"
        }
      ]
    }

The file is the agent-readable convenience cache. The *authoritative* record
of every purchase lives in the mining ledger (``kind="purchase"`` entries).
If owned_skins.json gets corrupted or lost, ``rebuild_owned_from_ledger``
reconstructs it from the ledger.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from daimon.identity.keys import CONFIG_DIR

OWNED_PATH = CONFIG_DIR / "owned_skins.json"


@dataclass(frozen=True)
class OwnedSkin:
    card_id: str
    skin_slug: str
    skin_name: str
    skin_axis: str
    rarity: str
    purchased_at: str
    cost: int
    ledger_entry_hash: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.card_id, self.skin_slug)


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def load_owned(path: Optional[Path] = None) -> List[OwnedSkin]:
    """Read the owned-skins file. Returns [] if the file doesn't exist or
    is corrupt — callers can recover via ``rebuild_owned_from_ledger``."""
    if path is None:
        path = OWNED_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    raw = data.get("owned", []) if isinstance(data, dict) else []
    out: List[OwnedSkin] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(OwnedSkin(
                card_id=entry["card_id"],
                skin_slug=entry["skin_slug"],
                skin_name=entry.get("skin_name") or entry["skin_slug"],
                skin_axis=entry["skin_axis"],
                rarity=entry["rarity"],
                purchased_at=entry["purchased_at"],
                cost=int(entry.get("cost", 0)),
                ledger_entry_hash=entry.get("ledger_entry_hash", ""),
            ))
        except (KeyError, TypeError, ValueError):
            # Skip malformed entries silently — better than raising.
            continue
    return out


def _save_owned(owned: List[OwnedSkin], path: Optional[Path] = None) -> None:
    if path is None:
        path = OWNED_PATH
    _ensure_dir()
    payload = {"owned": [asdict(s) for s in owned]}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def append_owned(skin: OwnedSkin, path: Optional[Path] = None) -> None:
    """Append an owned-skin entry to disk. Idempotent: if the same
    (card_id, skin_slug) is already present, it's a no-op (the ledger entry
    is the authoritative spend record)."""
    owned = load_owned(path)
    for existing in owned:
        if existing.key == skin.key:
            return
    owned.append(skin)
    _save_owned(owned, path)


def is_owned(card_id: str, skin_slug: str,
             path: Optional[Path] = None) -> bool:
    return any(s.card_id == card_id and s.skin_slug == skin_slug
               for s in load_owned(path))


def list_owned(path: Optional[Path] = None) -> List[OwnedSkin]:
    """Public alias for ``load_owned``. Sorted by purchase time ascending."""
    return sorted(load_owned(path), key=lambda s: s.purchased_at)


# ---------------------------------------------------------------------------
# Weekly cap (Mon–Sun UTC)
# ---------------------------------------------------------------------------

def _iso_week_key(ts_iso: str) -> Optional[tuple[int, int]]:
    """Return (iso_year, iso_week) for a given RFC3339 UTC timestamp.
    None if parsing fails (corrupt timestamp → exclude from cap math)."""
    try:
        # Python's fromisoformat handles +00:00; "Z" needs hand-translation.
        s = ts_iso.replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    iso = d.isocalendar()
    return (iso.year, iso.week)


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def weekly_purchase_count(now: Optional[_dt.datetime] = None,
                          path: Optional[Path] = None) -> int:
    """Count purchases in the current ISO week (Mon 00:00 UTC → Sun 23:59).

    Uses the local owned_skins.json as the count source. The ledger could
    serve here too, but the owned file is faster to scan and is updated in
    the same atomic block as the ledger entry.
    """
    if now is None:
        now = _now_utc()
    week = now.isocalendar()
    target = (week.year, week.week)
    n = 0
    for s in load_owned(path):
        wk = _iso_week_key(s.purchased_at)
        if wk == target:
            n += 1
    return n


def now_iso() -> str:
    """RFC3339 UTC timestamp helper, exported so the purchase flow stays
    consistent with ledger timestamps."""
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
