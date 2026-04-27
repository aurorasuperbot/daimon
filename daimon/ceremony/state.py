"""Persistence for tier-up ceremony state — ``~/.config/daimon/tier_progress.json``.

State stored::

    {"version": 1,
     "pubkey_hex": "abc...",
     "claimed_tier": "Veteran",                 # highest tier label ever claimed
     "claim_history": [
        {"tier": "Novice",   "claimed_at": "2026-04-27T12:00:00+00:00",
         "reward": 100,  "wins_at_claim": 3,  "ledger_entry_hash": "..."},
        {"tier": "Veteran",  "claimed_at": "2026-04-27T18:30:00+00:00",
         "reward": 250,  "wins_at_claim": 10, "ledger_entry_hash": "..."},
     ]}

## Atomic writes

Same pattern as ``quests/state.py`` — write to a same-dir tempfile, then
``os.replace`` in. POSIX guarantees the rename is atomic so a crash
mid-write can't corrupt the prior state.

## Why a separate file from the ledger?

The ledger is the source of truth for **balance** (signed, hash-chained,
verifiable). This file is the source of truth for **what the local UI
has shown the player as "claimed"**. They're cross-checked via
``audit_state_against_ledger`` in ``tier_up.py``. The reason for the
split:

  * The ledger doesn't store "claimed_tier" as a queryable field —
    you'd have to scan all entries to derive it. That's fine on a
    20-entry ledger but the read path would scale poorly.
  * Atomic state files give us a snapshot you can ``cat`` to see
    progression, which is friendlier for debugging than walking the
    ledger.
  * The ledger never drops entries; the state file can be safely
    rebuilt from the ledger if it ever goes missing (todo: a
    ``rebuild_state_from_ledger`` helper for support — V1.1).

## Why monotonic claimed_tier?

If wins ever drops (e.g. a successful dispute on an arbiter result
revokes a win), the player's *effective* tier could regress. We do NOT
revoke the ceremony — the user already saw it, the reward already
landed in the ledger. Re-fire-on-reclimb would be a bad surprise too.
So ``claimed_tier`` is one-way: once Veteran, always Veteran from the
ceremony's perspective.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from daimon.identity.keys import _resolve_config_dir


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _config_dir() -> Path:
    return _resolve_config_dir()


# Module-level alias for tests that want to assert on the concrete path.
# Computed at import time, so monkeypatch via
# ``monkeypatch.setattr(state, "CEREMONY_PATH", tmp_path / "tier_progress.json")``
# in test setup.
CEREMONY_PATH = _config_dir() / "tier_progress.json"

# Schema version — bump if the persisted shape changes incompatibly. The
# loader silently re-initializes on a version mismatch (treat as stale).
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_state(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Load the persisted ceremony state. Returns None if missing/malformed/stale.

    The caller is responsible for deciding what to do with None — usually
    that's "treat as a fresh ledger with claimed_tier='Rookie' and an
    empty history".

    Returns:
      ``{"version": int, "pubkey_hex": str, "claimed_tier": str,
         "claim_history": [...]}`` or None.

    Failure modes that return None (silent):
      * File doesn't exist.
      * File exists but JSON is malformed.
      * Version mismatch (treat as stale).
      * Required keys missing or wrong type.
      * ``claim_history`` not a list, or contains malformed entries.

    Anything that returns None is a "needs initialization" signal —
    never raises.
    """
    if path is None:
        path = CEREMONY_PATH
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
    if not isinstance(data.get("pubkey_hex"), str):
        return None
    if not isinstance(data.get("claimed_tier"), str):
        return None
    history = data.get("claim_history")
    if not isinstance(history, list):
        return None
    # Validate each history entry has the minimum shape — anything
    # missing means the file was hand-edited or written by an older
    # schema; in either case a re-init is the safest move.
    for h in history:
        if not isinstance(h, dict):
            return None
        if not isinstance(h.get("tier"), str):
            return None
        if not isinstance(h.get("claimed_at"), str):
            return None
        if not isinstance(h.get("reward"), int):
            return None
        if not isinstance(h.get("wins_at_claim"), int):
            return None
        # ledger_entry_hash is required for audit cross-checks. Allow
        # ``None`` only if the entry was synthesized from an older
        # state file that didn't record it (we wrote one, then bumped
        # the loader to require it; tolerate missing on read, require
        # it on save).
        eh = h.get("ledger_entry_hash")
        if eh is not None and not isinstance(eh, str):
            return None
    return data


def save_state(
    *,
    pubkey_hex: str,
    claimed_tier: str,
    claim_history: List[Dict[str, Any]],
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Persist ceremony state atomically. Returns the saved record.

    Atomic via ``tempfile.NamedTemporaryFile`` + ``os.replace`` in the
    same directory as the target — the rename is atomic on POSIX, so a
    crash mid-write can't leave a half-written file. The directory is
    created with ``mkdir(parents=True, exist_ok=True)``.

    Permissions on the file are left at the default umask — ceremony
    state isn't secret (the leaderboard already exposes the underlying
    wins count), just per-user.
    """
    if path is None:
        path = CEREMONY_PATH
    record = {
        "version": SCHEMA_VERSION,
        "pubkey_hex": pubkey_hex,
        "claimed_tier": claimed_tier,
        "claim_history": claim_history,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        prefix=".tier_progress.",
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
