"""Arena cache — persistent local cache of last-known server state.

Stores snapshots at ``~/.config/daimon/arena_cache/<key>.json`` so the
UI can display server data when offline. Never trusted for competitive
operations — only used for display.

Cache entries are keyed by topic (e.g. "collection", "balance") and
written atomically (write-then-rename).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from daimon.identity.keys import CONFIG_DIR

CACHE_DIR = CONFIG_DIR / "arena_cache"


def save_cache(key: str, data: Dict[str, Any]) -> None:
    """Write a cache entry. Creates the cache dir if needed."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_cache(key: str) -> Optional[Dict[str, Any]]:
    """Read a cache entry. Returns None if not cached."""
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def clear_cache(key: Optional[str] = None) -> None:
    """Remove a specific cache entry, or all entries if key is None."""
    if key:
        path = CACHE_DIR / f"{key}.json"
        path.unlink(missing_ok=True)
    elif CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink(missing_ok=True)
