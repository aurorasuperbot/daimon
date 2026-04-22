"""Agent → game-terminal state-file protocol.

Single-file, latest-write-wins coordination between MCP tools (writers) and
the game-terminal renderer (reader). **Locked 2026-04-21.**

This replaces the legacy `play/inbox.py` directory-of-files scheme: one file,
one watched path, dispatch by payload content rather than filename prefix.

## The model

    [ MCP tool (writer) ]                          [ Game terminal (reader) ]
           │                                                 │
           │  write_state("match", {...}, id="m_42")         │
           ├────────────────────────────────────────────►    │
           │                                                 │  watchdog fires
           │                                                 │  read_state()
           │                                                 │  dispatch on view
           │                                                 │  set last_id

    ~/.config/daimon/state.json  (single file, atomically rewritten)

## Schema

    {
        "view": "match" | "pull" | "inspect" | "collection" |
                "loadout" | "leaderboard" | "rank" | "idle",
        "data": {...},               # view-specific payload
        "id":   "<string>",          # dedupe key — renderer skips if equals last rendered
        "ts_ns": <int>,              # nanoseconds since epoch, set by writer
        "schema_version": 1,
    }

`view` values not in the known set are rejected by the writer; the reader
returns them with a `ValueError` so handlers can surface the error cleanly.

## Atomicity

Writer writes to `state.json.tmp` then uses `os.replace()` to swap it in.
POSIX rename is atomic; on Windows it's best-effort (Python's `os.replace`
is the intended cross-platform primitive). Readers never see half-written
files.

## Dedupe

Each write carries an `id`. The renderer tracks the last-rendered id and
skips re-rendering identical ids. This prevents:
  - double-dispatch if watchdog fires twice for one write
  - re-render on renderer restart when `state.json` already reflects a
    state the renderer has already shown
  - replay storms if a buggy writer updates the file without changing content

To force a re-render of the "same" visual content, callers supply a fresh
id (e.g. a UUID). `new_id()` below gives a conventional short one.

## Queue semantics

**Last-write-wins. No queue.** If two tools write back-to-back, the second
overwrites the first and the renderer only observes the second.

Rationale: agents serialize tool calls naturally (one-per-turn in practice),
cross-turn rapid sequences are rare, and a queue adds mechanism we don't
need yet. If a real use case appears (e.g. "auto-pull 10 cards" wanting 10
queued animations), we add a `pending: [...]` field. The schema carries a
`schema_version` so we can evolve without breaking existing readers.

## Mining ticker

Separate stream. `~/.config/daimon/mine_buffer.jsonl` (rolling append)
continues to carry mining ticks that always show as HUD chrome, regardless
of which view is active. Not part of this module — lives in mining/.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_STATE_PATH = Path.home() / ".config" / "daimon" / "state.json"
TMP_SUFFIX = ".tmp"
SCHEMA_VERSION = 1


# Known views — writers must declare one of these. Keeping the set closed
# catches typos at the earliest seam; readers still dispatch by value so
# adding a new view means updating BOTH this set AND the renderer's handler
# table, deliberately.
KNOWN_VIEWS: frozenset[str] = frozenset({
    "match",
    "pull",
    "inspect",
    "collection",
    "loadout",
    "leaderboard",
    "rank",
    "idle",
})


def resolve_state_path(override: Optional[Path | str] = None) -> Path:
    """Resolve the state-file path.

    Precedence: explicit arg > ``DAIMON_STATE`` env > XDG default.
    Does NOT create the parent directory — ``write_state`` does that lazily.
    """
    if override is not None:
        return Path(override).expanduser().resolve()
    env = os.environ.get("DAIMON_STATE")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_STATE_PATH


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GameState:
    """One decoded state snapshot — what ``read_state`` returns."""
    view: str
    data: dict[str, Any]
    id: str
    ts_ns: int
    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# ID helper
# ---------------------------------------------------------------------------

def new_id(prefix: str = "") -> str:
    """Generate a conventional short id (8 hex chars) with an optional prefix.

    Prefixes are descriptive-only — they don't affect dedupe, only debuggability.
    Examples: ``new_id("match") == "match_a1b2c3d4"``.
    """
    tok = uuid.uuid4().hex[:8]
    return f"{prefix}_{tok}" if prefix else tok


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write_state(
    view: str,
    data: dict[str, Any],
    id: Optional[str] = None,
    *,
    state_path: Optional[Path | str] = None,
) -> GameState:
    """Atomically replace the state file with a new snapshot.

    Args:
        view: One of ``KNOWN_VIEWS``. Rejected at the earliest seam to catch
            typos; if we decide to add a new view, it must be registered here
            and in the renderer's handler table (by design).
        data: View-specific payload. Must be JSON-serializable.
        id: Dedupe key. If None, one is generated. Callers that want the
            renderer to re-show the same content pass a fresh id.
        state_path: Override target path (for tests / multi-identity).

    Returns:
        The ``GameState`` that was written — useful for callers that want to
        log or include the ``id`` in their own response to the agent.

    Raises:
        ValueError: unknown ``view``, or payload that fails JSON encoding.
    """
    if view not in KNOWN_VIEWS:
        raise ValueError(
            f"unknown view {view!r}; must be one of {sorted(KNOWN_VIEWS)}"
        )
    if not isinstance(data, dict):
        raise ValueError(
            f"data must be a dict, got {type(data).__name__}"
        )

    effective_id = id if id is not None else new_id(view)
    ts_ns = time.time_ns()

    body = {
        "view": view,
        "data": data,
        "id": effective_id,
        "ts_ns": ts_ns,
        "schema_version": SCHEMA_VERSION,
    }

    # Encode FIRST so we surface serialization errors BEFORE touching disk.
    # default=str lets us pass through dataclasses / Path / etc. without
    # callers having to hand-convert.
    encoded = json.dumps(body, indent=2, default=str)

    final_path = resolve_state_path(state_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_suffix(final_path.suffix + TMP_SUFFIX)

    tmp_path.write_text(encoded)
    os.replace(tmp_path, final_path)  # atomic on POSIX; best-effort on Windows

    return GameState(
        view=view,
        data=data,
        id=effective_id,
        ts_ns=ts_ns,
    )


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def read_state(state_path: Optional[Path | str] = None) -> Optional[GameState]:
    """Read the current state file.

    Returns ``None`` if the file doesn't exist yet (fresh install, renderer
    starts before any writer has run).

    Raises:
        ValueError: file exists but is malformed (unknown view, missing field,
            JSON decode error). Callers should handle and either quarantine
            the file or render an error placeholder.
    """
    path = resolve_state_path(state_path)
    if not path.exists():
        return None

    try:
        raw = path.read_text()
    except OSError as e:
        raise ValueError(f"state file unreadable: {e}") from e

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"state file not JSON: {e}") from e

    if not isinstance(body, dict):
        raise ValueError(
            f"state file top-level must be an object, got {type(body).__name__}"
        )

    view = body.get("view")
    if not isinstance(view, str) or not view:
        raise ValueError("state file missing 'view'")
    if view not in KNOWN_VIEWS:
        raise ValueError(
            f"state file unknown view {view!r}; reader/writer out of sync"
        )

    data = body.get("data", {})
    if not isinstance(data, dict):
        raise ValueError(
            f"state file 'data' must be an object, got {type(data).__name__}"
        )

    state_id = body.get("id")
    if not isinstance(state_id, str) or not state_id:
        raise ValueError("state file missing 'id'")

    ts_ns = body.get("ts_ns", 0)
    if not isinstance(ts_ns, int):
        raise ValueError(f"state file bad ts_ns: {ts_ns!r}")

    schema_version = body.get("schema_version", SCHEMA_VERSION)
    if not isinstance(schema_version, int) or schema_version < 1:
        raise ValueError(f"state file bad schema_version: {schema_version!r}")

    return GameState(
        view=view,
        data=data,
        id=state_id,
        ts_ns=ts_ns,
        schema_version=schema_version,
    )


# ---------------------------------------------------------------------------
# Dedupe helper
# ---------------------------------------------------------------------------

def should_render(state: Optional[GameState], last_rendered_id: Optional[str]) -> bool:
    """Renderer helper: returns True iff this state is not already rendered.

    Encapsulates the dedupe contract so renderers don't have to hand-roll it.
    Both a None state and a state whose id matches the last rendered one
    return False — in both cases there's nothing new to paint.
    """
    if state is None:
        return False
    return state.id != last_rendered_id
