"""Mining ticker buffer — bounded rolling stream of HUD events.

The state.json scheme (see ``daimon/play/state.py``) carries the *current
view*: which match is playing, which pull just landed, etc. It is "last
write wins", one file, and the webview reader polls it for changes.

But the agent also generates a stream of small events — every productive
tool call mints currency, every 100 ¤ is a milestone — that should scroll
through a ticker pane *regardless of which view is up*. State.json is the
wrong place: writing a mining tick would clobber a match-in-progress.

This module owns the second stream. Locked design from
``daimon/play/state.py`` lines 67-70:

    Mining ticker — Separate stream. ``~/.config/daimon/mine_buffer.jsonl``
    (rolling append) continues to carry mining ticks that always show as
    HUD chrome, regardless of which view is active.

## File format

JSONL at ``~/.config/daimon/mine_buffer.jsonl``. One JSON object per line:

    {
      "ts": "2026-04-26T14:33:21.123456+00:00",
      "kind": "mine" | "milestone" | "match" | "pull",
      "tool": "Edit",          # for kind=mine; tool that earned the reward
      "amount": 3,             # currency delta for THIS event (>=0 for mine)
      "balance_after": 247,    # running total after this event
      "note": "100¤ earned",   # optional human-readable
    }

## Bounded rolling

This is a UI buffer, not an audit log — the ledger (``mining/ledger.py``)
is the source of truth for balances. We keep a rolling window so the file
never grows without bound:

  - on each ``append``, if the line count exceeds ``MAX_ENTRIES`` (500),
    the file is rewritten with only the last ``KEEP_ENTRIES`` (250)
  - the rewrite is atomic (write to .tmp, os.replace)
  - HUD readers tolerate truncation transparently — they only ever
    ``tail()`` the last N lines anyway

## Concurrency

Single-writer assumption (one Claude Code session per identity per host),
same as the ledger. We use O_APPEND for normal writes (atomic on POSIX
for sub-PIPE_BUF byte writes), os.replace for truncation rewrites.
Multiple readers (HUD + scripts) are safe — they only read.

## Writer NEVER raises

This is HUD chrome — failing to emit a ticker event must NOT break the
mining hook or any other caller. ``append`` swallows + logs all errors.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from daimon.identity.keys import CONFIG_DIR

logger = logging.getLogger(__name__)

# Path lives next to ledger + state.json.
BUFFER_PATH = CONFIG_DIR / "mine_buffer.jsonl"

# Bounded rolling: keep file from growing unbounded over weeks of mining.
# 500 entries × ~150 bytes ≈ 75 KB ceiling. Compaction halves to 250
# (also ~37 KB) so we don't compact on every write.
MAX_ENTRIES = 500
KEEP_ENTRIES = 250

# Known event kinds. Writers must declare one of these. Closed set so
# typos surface at the seam, like state.py's KNOWN_VIEWS.
KNOWN_KINDS: frozenset[str] = frozenset({
    "mine",        # agent earned currency from a productive tool call
    "milestone",   # crossed a 100¤ boundary (or other threshold)
    "match",       # a match just resolved (mirrors state.json match writes)
    "pull",        # a gacha pull just landed
})


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public reader API
# ---------------------------------------------------------------------------

def tail(n: int = 20, *, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return the last ``n`` events, oldest first. Empty list if no buffer.

    Parses lazily — bad lines are skipped (logged at DEBUG) so a single
    corrupted append doesn't blind the HUD to the rest of the stream.
    """
    if path is None:
        path = BUFFER_PATH
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("mine_buffer unreadable: %s", e)
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return []
    out: List[Dict[str, Any]] = []
    for ln in lines[-n:]:
        try:
            entry = json.loads(ln)
        except json.JSONDecodeError as e:
            logger.debug("mine_buffer skipping bad line: %s", e)
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def mtime_ns(*, path: Optional[Path] = None) -> int:
    """Return the file mtime in ns, or 0 if it doesn't exist.

    HUD uses this for cheap change-detection between ticks — only re-tail
    when the file has changed since the last poll.
    """
    if path is None:
        path = BUFFER_PATH
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Public writer API
# ---------------------------------------------------------------------------

def append(
    kind: str,
    *,
    amount: int = 0,
    balance_after: int = 0,
    tool: str = "",
    note: str = "",
    path: Optional[Path] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Append a single event to the mine buffer. Best-effort.

    Returns the written entry, or ``None`` on any failure (logged). Callers
    must treat this as fire-and-forget — the buffer is HUD chrome, never
    a source of truth.

    Triggers a tail-truncation when line count exceeds ``MAX_ENTRIES``.
    """
    if kind not in KNOWN_KINDS:
        logger.warning("mine_buffer.append: unknown kind %r (skipping)", kind)
        return None
    if path is None:
        path = BUFFER_PATH

    entry: Dict[str, Any] = {
        "ts": _now_iso(),
        "kind": kind,
        "amount": int(amount),
        "balance_after": int(balance_after),
    }
    if tool:
        entry["tool"] = tool
    if note:
        entry["note"] = note
    if extra:
        for k, v in extra.items():
            if k in entry:
                continue
            entry[k] = v

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("mine_buffer parent mkdir failed: %s", e)
        return None

    try:
        line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n"
    except (TypeError, ValueError) as e:
        logger.warning("mine_buffer encode failed: %s", e)
        return None

    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.warning("mine_buffer append failed: %s", e)
        return None

    # Lazy tail-truncate. Counting lines is cheap (<100 KB ceiling) and
    # only triggers when we've drifted past MAX_ENTRIES.
    try:
        _maybe_truncate(path)
    except OSError as e:
        logger.warning("mine_buffer truncate failed (non-fatal): %s", e)

    return entry


def _maybe_truncate(path: Path) -> None:
    """If the buffer exceeds MAX_ENTRIES, rewrite to the last KEEP_ENTRIES.

    Atomic via .tmp + os.replace. No-op when under the threshold so the
    cost on the typical write path is one ``stat()`` + a line read.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return
    # Cheap early-exit: if the file is well under the byte ceiling, skip
    # the line count entirely. ~150 bytes/entry × 500 = ~75 KB; we use
    # 100 KB as a safe upper bound that still triggers compaction in time.
    if size < 100_000:
        return

    with path.open("r", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    if len(lines) <= MAX_ENTRIES:
        return

    keep = lines[-KEEP_ENTRIES:]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(keep), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Convenience: filter helpers
# ---------------------------------------------------------------------------

def by_kind(entries: Iterable[Dict[str, Any]], kind: str) -> List[Dict[str, Any]]:
    """Filter a tail-result down to one event kind. Order preserved."""
    return [e for e in entries if e.get("kind") == kind]
