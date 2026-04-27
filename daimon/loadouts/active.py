"""Active-loadout pointer — which user-saved loadout is the default opponent.

Persists a single small JSON file at ``~/.config/daimon/loadout_meta.json``:

    {"version": 1, "active_loadout": "aggro_volt"}

## Why a separate file (not a flag inside each loadout)

User-saved loadouts live at ``~/.config/daimon/loadouts/<name>.json``. We
COULD encode "is this the active one" as a per-file boolean, but then:

  * Setting a new active = touching every file (or worse, scanning to
    find the previous active and clearing it). Atomicity becomes hard.
  * Two loadouts could end up flagged active at once (race / partial
    write). Single source of truth wins.

A separate pointer file is one ``json.dumps`` call; the active flag in
:func:`daimon.mcp.server._saved_loadouts_summary` is computed on read.

## Pointer-by-name vs pointer-by-cards

We persist the **name** of the active loadout, not a deep copy of its
cards. Editing the named loadout via ``dm_loadout_save`` propagates
automatically — no "your active loadout is stale" footgun. If the
referenced loadout disappears (user deleted the file by hand), the
``validate_exists=True`` reader returns ``None`` and the pointer file
is silently self-corrected on the next ``set_active_loadout_name`` /
``clear_active_loadout`` call.

## Atomicity

All writes go through ``daimon.persist.atomic_write_text`` (same helper
the ceremony state and quests state use) — write to ``<path>.tmp``,
rename over the target. A crash mid-write leaves either the old file
intact OR the new file fully written; never a half-flushed JSON.

## Test surface

See ``tests/test_active_loadout.py`` for the per-function unit tests
plus the round-trip + missing-file + corrupt-JSON cases.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Bumped whenever the on-disk schema changes. Stale-version reads are
# treated as "no active set" + the file is overwritten on the next set
# call — same conservative-reset policy quests/ceremony state use.
ACTIVE_META_VERSION = 1


# ---------------------------------------------------------------------------
# Path resolution — every call re-reads CONFIG_DIR so test monkeypatches
# take effect (mirrors the pattern in collection.py / mining/buffer.py).
# ---------------------------------------------------------------------------

def _meta_path() -> Path:
    """Return the active-loadout meta file path under the current CONFIG_DIR."""
    from daimon.identity.keys import CONFIG_DIR
    return CONFIG_DIR / "loadout_meta.json"


def _saved_loadout_path(name: str) -> Path:
    """Return the file path for a named user-saved loadout.

    Mirrors ``daimon.mcp.server._loadout_path`` — kept duplicated rather
    than imported because the engine layer must not depend on the MCP
    layer (which is the outermost adapter).
    """
    from daimon.identity.keys import CONFIG_DIR
    return CONFIG_DIR / "loadouts" / f"{name}.json"


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def _read_meta() -> Dict[str, Any]:
    """Load the meta doc; return an empty doc on any read/parse failure.

    A corrupt file is silently treated as "no active set" — the next
    write call overwrites it cleanly. The conservative interpretation
    matches what quests/ceremony do: a partial-write incident from a
    previous run shouldn't strand the user with an unparseable state.
    """
    path = _meta_path()
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("loadout_meta read failed: %s", e)
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("loadout_meta corrupt JSON: %s", e)
        return {}
    if not isinstance(data, dict):
        logger.warning("loadout_meta root is not an object")
        return {}
    # Stale-version reads are treated as empty — the next set call will
    # overwrite the file with a current-version doc.
    if data.get("version") != ACTIVE_META_VERSION:
        logger.info(
            "loadout_meta version mismatch (got %r, want %d) — "
            "treating as unset",
            data.get("version"),
            ACTIVE_META_VERSION,
        )
        return {}
    return data


def get_active_loadout_name(*, validate_exists: bool = True) -> Optional[str]:
    """Return the active loadout's name, or ``None`` if not set or missing.

    Args:
      validate_exists: If True (default), require the underlying saved
        loadout file to still exist. If the user manually deleted the
        loadout file via ``rm`` we treat the pointer as dead and return
        ``None``. Pass False when you want to inspect the raw pointer
        independent of the saved-loadouts state (e.g. for diagnostics).

    Never raises — every failure path returns None and logs a warning.
    """
    data = _read_meta()
    name = data.get("active_loadout")
    if not isinstance(name, str) or not name:
        return None
    if validate_exists and not _saved_loadout_path(name).is_file():
        return None
    return name


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _atomic_write_meta(doc: Dict[str, Any]) -> None:
    """Atomic JSON write: <path>.tmp + os.replace."""
    path = _meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    tmp.replace(path)


def set_active_loadout_name(name: str) -> None:
    """Persist ``name`` as the active loadout pointer.

    The caller is responsible for ensuring ``name`` corresponds to an
    existing saved loadout — this writer doesn't validate, so pointing
    at a non-existent loadout is allowed (callers like
    :func:`daimon.mcp.server.dm_loadout_set` validate up-front so the
    error surface is on their side, not here).

    Idempotent: writing the same name twice is a no-op for behavior.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"name must be a non-empty string, got {name!r}")
    _atomic_write_meta({
        "version": ACTIVE_META_VERSION,
        "active_loadout": name,
    })


def clear_active_loadout() -> None:
    """Explicitly unset the active loadout pointer.

    After this call, :func:`get_active_loadout_name` returns ``None``
    until the next ``set_active_loadout_name``. The file remains on
    disk (with ``active_loadout: null``) — easier to inspect than a
    missing file when debugging.
    """
    _atomic_write_meta({
        "version": ACTIVE_META_VERSION,
        "active_loadout": None,
    })


__all__ = [
    "ACTIVE_META_VERSION",
    "get_active_loadout_name",
    "set_active_loadout_name",
    "clear_active_loadout",
]
