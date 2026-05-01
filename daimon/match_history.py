"""Append-only match history log.

Each completed match (PvE or PvP) is appended as one JSON line. The log
enables per-serial stat queries ("show me every match this serial fought in")
and recent-match browsing.

Storage: ~/.config/daimon/match_history.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from daimon.identity.keys import CONFIG_DIR

MATCH_HISTORY_PATH = CONFIG_DIR / "match_history.jsonl"


def append_match(entry: Dict[str, Any],
                 path: Optional[Path] = None) -> None:
    if path is None:
        path = MATCH_HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_all(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    if path is None:
        path = MATCH_HISTORY_PATH
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def recent_matches(limit: int = 50,
                   path: Optional[Path] = None) -> List[Dict[str, Any]]:
    entries = _load_all(path)
    return entries[-limit:]


def matches_for_serial(serial: str,
                       limit: int = 20,
                       path: Optional[Path] = None) -> List[Dict[str, Any]]:
    entries = _load_all(path)
    matched = [
        e for e in entries
        if serial in (e.get("loadout_serials") or [])
    ]
    return matched[-limit:]
