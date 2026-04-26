"""Inbox cursor — last-acked-message-id persistence.

Tiny single-file JSON store at ``~/.config/daimon/inbox-cursor.json``.
Used by the watcher loop to dedup messages within (and ideally across)
sessions: ``dm_inbox_wait`` filters out anything ``id <= last_acked``,
``dm_inbox_ack`` updates the cursor.

## Why a separate file (not state.json or mine_buffer.jsonl)

State.json is the spectator-HUD view (locked design, see
``daimon/play/state.py`` lines 67-70). Mine buffer is the rolling
ticker stream (locked design, see ``daimon/mining/buffer.py``).
Inbox cursor is its own concern — agent-runtime bookkeeping that
no other consumer cares about. A separate file means a corrupted
cursor (which would be self-healing — start over from latest) can't
take down the HUD or lose mining receipts.

## File format

```json
{"last_acked_id": 42, "updated_at": "2026-04-26T21:45:00+00:00"}
```

Missing file = "no cursor yet" = treat as -1 (every message is new).
Malformed file = same. The cursor is a hint, not a source of truth —
if it's wrong, worst case is a duplicate ``@daimon`` dispatch; the
mining ledger and match log remain canonical.

## Concurrency

Single-writer (one Claude Code session per identity). Atomic via
``write to .tmp + os.replace``. Multiple readers are safe.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Optional

from daimon.identity.keys import CONFIG_DIR

logger = logging.getLogger(__name__)


CURSOR_PATH = CONFIG_DIR / "inbox-cursor.json"

# Sentinel returned when no cursor exists yet. Chosen as -1 so any
# real (zero-or-positive) message id passes ``id > sentinel``.
NO_CURSOR = -1


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def get_last_acked(*, path: Optional[Path] = None) -> int:
    """Return the highest message id ever passed to ``set_last_acked``.

    Returns ``NO_CURSOR`` (-1) when:
      * the cursor file doesn't exist, or
      * the file is unreadable, or
      * the JSON is malformed, or
      * the ``last_acked_id`` field is missing/non-int.

    All failure modes log at DEBUG (not WARNING) — a missing or
    corrupt cursor is recoverable and shouldn't pollute the user's
    log on every poll.
    """
    if path is None:
        path = CURSOR_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return NO_CURSOR
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.debug("inbox cursor unreadable JSON: %s", e)
        return NO_CURSOR
    val = data.get("last_acked_id") if isinstance(data, dict) else None
    if not isinstance(val, int):
        return NO_CURSOR
    return val


def set_last_acked(message_id: int, *, path: Optional[Path] = None) -> None:
    """Persist ``message_id`` as the high-water mark.

    Monotonic — calling with a value ``<=`` the current cursor is a
    no-op. The cursor only ever moves forward; this prevents a
    misbehaving caller from "un-acking" a batch and replaying it.

    Atomic via .tmp + os.replace. Best-effort: failures log at WARNING
    and swallow the exception (the inbox is HUD chrome, never a source
    of truth).
    """
    if not isinstance(message_id, int):
        logger.warning("set_last_acked: non-int id %r ignored", message_id)
        return
    if path is None:
        path = CURSOR_PATH

    current = get_last_acked(path=path)
    if message_id <= current:
        return

    payload = {
        "last_acked_id": int(message_id),
        "updated_at": _now_iso(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("inbox cursor parent mkdir failed: %s", e)
        return

    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("inbox cursor write failed: %s", e)
