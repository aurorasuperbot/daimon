"""Persistence for the rolled quest list — ``~/.config/daimon/daily_quests.json``.

State stored:

  {"date": "2026-04-26",
   "pubkey_hex": "abc...",
   "version": 1,
   "quests": [{...}, {...}, {...}]}

Progress is NOT stored here — it's re-derived on every read by scanning
the ledger + ticker. This is by design (see ``progress`` module) so we
have a single source of truth (the ledger) and the rolled list never
goes stale relative to actual play.

## Atomic writes

We write the full file in one ``os.replace`` so a partial write can't
corrupt the previous day's state. The temp file lives in the same
directory as the target so the rename is guaranteed atomic on POSIX.

## Why not extend the ledger to hold the rolled quests?

Considered. Decided against because the rolled list is *intent*, not
*history* — it's recreatable from scratch any time via ``roll_today``,
so persisting it is just a perf optimization (skip the HMAC + RNG cost).
The ledger is for things you can't recreate.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from daimon.identity.keys import _resolve_config_dir


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# Canonical location — same parent as identity.key, mining_ledger.jsonl, etc.
# Resolved lazily so tests can monkeypatch DAIMON_HOME / XDG_CONFIG_HOME and
# get the redirected path without re-importing this module.
def _config_dir() -> Path:
    return _resolve_config_dir()


# Module-level alias for tests + diagnostic tools that want to assert on the
# concrete path. Computed at import time, so monkeypatch via
# ``monkeypatch.setattr(state, "QUESTS_PATH", tmp_path / "daily_quests.json")``
# in test setup.
QUESTS_PATH = _config_dir() / "daily_quests.json"

# Schema version — bump if the persisted shape changes incompatibly. The
# loader silently re-rolls on a version mismatch (treat as stale).
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Day helpers
# ---------------------------------------------------------------------------

def today_str(now: Optional[_dt.datetime] = None) -> str:
    """``"YYYY-MM-DD"`` for today's UTC date.

    Mirrors ``daimon.shop.rotation._today_utc().isoformat()`` so quests
    and shop rollover at the same instant (00:00 UTC).
    """
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_quests(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Load the persisted quest record. Returns None if missing/malformed/stale.

    The caller (typically the roller) is responsible for deciding what to
    do with None — usually that's "roll a fresh one and save it".

    Returns:
      ``{"date": "...", "pubkey_hex": "...", "version": int,
         "quests": [...]}`` or None.

    Failure modes that return None (silent):
      * File doesn't exist.
      * File exists but JSON is malformed.
      * Version mismatch (treat as stale).
      * Required keys missing or wrong type.

    Anything that returns None is a "needs re-roll" signal — never raises.
    """
    if path is None:
        path = QUESTS_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != SCHEMA_VERSION:
        return None
    if not isinstance(data.get("date"), str):
        return None
    if not isinstance(data.get("pubkey_hex"), str):
        return None
    quests = data.get("quests")
    if not isinstance(quests, list):
        return None
    # Validate each quest has the minimum shape — anything missing means
    # the file was hand-edited or written by an older schema.
    for q in quests:
        if not isinstance(q, dict):
            return None
        if not isinstance(q.get("id"), str):
            return None
        if not isinstance(q.get("template_id"), str):
            return None
        if not isinstance(q.get("title"), str):
            return None
        if q.get("tier") not in {"easy", "medium", "hard"}:
            return None
        if not isinstance(q.get("reward"), int):
            return None
        if not isinstance(q.get("params"), dict):
            return None
    return data


def save_quests(
    *,
    date: str,
    pubkey_hex: str,
    quests: List[Dict[str, Any]],
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Persist the rolled quest list atomically. Returns the saved record.

    Atomic via ``tempfile.NamedTemporaryFile`` + ``os.replace`` in the
    same directory as the target — the rename is atomic on POSIX, so a
    crash mid-write can't leave a half-written file.

    The directory is created with ``mkdir(parents=True, exist_ok=True)``.
    Permissions on the file are left at the default umask — quests aren't
    secret, just per-user.
    """
    if path is None:
        path = QUESTS_PATH
    record = {
        "version": SCHEMA_VERSION,
        "date": date,
        "pubkey_hex": pubkey_hex,
        "quests": quests,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile in the same dir → os.replace is atomic on POSIX.
    fd = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        prefix=".daily_quests.",
        suffix=".tmp",
    )
    try:
        json.dump(record, fd, indent=2, sort_keys=True)
        fd.write("\n")  # POSIX-friendly trailing newline
        fd.flush()
        os.fsync(fd.fileno())
    finally:
        fd.close()
    os.replace(fd.name, str(path))
    return record
