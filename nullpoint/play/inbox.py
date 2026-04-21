"""File-watcher inbox — bridge between MCP tools (writers) and the game UI (watcher).

This is the coordination seam specified in the locked design (2026-04-22).

Problem: MCP tools and the game terminal are **separate processes**. MCP runs
inside the agent's workspace and resolves events (matches, pulls, mining). The
game terminal runs in the player's foreground window and paints them. They share
no memory, no sockets, no IPC channel — only the filesystem.

Solution: atomic-write JSON events to `~/.config/nullpoint/inbox/`. The watcher
dispatches by `event_type`, renders, then (optionally) consumes the file.

Design properties we care about:
  - **Atomicity** — writer writes to `.tmp` then renames; watcher never sees a
    half-written file. Rename is atomic on POSIX filesystems.
  - **Crash recovery** — watcher drains existing files in ctime order at startup
    BEFORE the live observer starts. An event written while the watcher was
    asleep still gets dispatched on next launch.
  - **Fault isolation** — malformed JSON / unknown event_type / handler error
    moves the file to `inbox/.quarantine/` rather than crashing the watcher.
  - **Defaults XDG-compliant** — uses `~/.config/nullpoint/inbox/` (matches
    `collection.json`, `identity.key`, `mining_ledger.jsonl` convention).
    Overridable via `NULLPOINT_INBOX` env var — useful for tests and for
    running multiple identities side-by-side.

File naming: `{event_type}_{ts_ns}_{short_uuid}.json`
  - `event_type` prefix makes `ls` debug-friendly
  - `ts_ns` (nanoseconds since epoch) gives a lexicographic sort that matches
    creation order even when many events are written in a single millisecond.
    Using ms here would break ordering under burst writes (tested — drain
    order failed when 4 events fell inside one ms).
  - `short_uuid` (8 chars) prevents collisions if two processes write in the
    same nanosecond on fast CPUs (uncommon but possible).

Example: `match_1776787123456789012_a1b2c3d4.json`
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_INBOX_DIR = Path.home() / ".config" / "nullpoint" / "inbox"
QUARANTINE_SUBDIR = ".quarantine"
TMP_SUFFIX = ".tmp"


def resolve_inbox_dir(override: Optional[Path | str] = None) -> Path:
    """Resolve the inbox path.

    Precedence: explicit arg > NULLPOINT_INBOX env var > XDG default.
    Does NOT create the directory — callers that need it call `ensure_inbox`.
    """
    if override is not None:
        return Path(override).expanduser().resolve()
    env = os.environ.get("NULLPOINT_INBOX")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_INBOX_DIR


def ensure_inbox(inbox_dir: Path) -> Path:
    """Create inbox + quarantine dirs with tight permissions. Idempotent."""
    inbox_dir.mkdir(parents=True, exist_ok=True)
    (inbox_dir / QUARANTINE_SUBDIR).mkdir(exist_ok=True)
    try:
        os.chmod(inbox_dir, 0o700)
    except PermissionError:
        pass  # Not fatal — works on tmpfs and CI runners with restricted perms.
    return inbox_dir


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InboxEvent:
    """One parsed event file — what a handler receives."""
    path: Path
    event_type: str
    payload: dict[str, Any]
    ts_ns: int                  # nanoseconds since epoch, encoded in filename (NOT file mtime)
    schema_version: int = 1

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return f"InboxEvent(type={self.event_type!r}, path={self.path.name!r})"


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

class InboxWriter:
    """Atomic writer for inbox events.

    Used by MCP tools and any other engine-side code that wants to push a
    renderable event to the player's game terminal.
    """

    def __init__(self, inbox_dir: Optional[Path | str] = None):
        self.inbox_dir = resolve_inbox_dir(inbox_dir)

    def write(self, event_type: str, payload: dict[str, Any]) -> Path:
        """Write one JSON event. Returns the final (post-rename) path.

        Atomic: writes to `<name>.tmp` first, then renames. Observers on the
        inbox dir should filter out `.tmp` files (the default `InboxWatcher`
        does). Rename is atomic on POSIX; on Windows this is best-effort.
        """
        if not event_type.replace("_", "").isalnum():
            raise ValueError(f"event_type must be alphanumeric/_: {event_type!r}")

        ensure_inbox(self.inbox_dir)

        ts_ns = time.time_ns()
        short_id = uuid.uuid4().hex[:8]
        name = f"{event_type}_{ts_ns}_{short_id}.json"

        final_path = self.inbox_dir / name
        tmp_path = self.inbox_dir / (name + TMP_SUFFIX)

        # Enforce the wire invariant: payload MUST include schema_version.
        # If the caller didn't set it, default to 1 (the current locked version).
        body = {"event_type": event_type, "schema_version": 1, **payload}

        tmp_path.write_text(json.dumps(body, indent=2, default=str))
        os.replace(tmp_path, final_path)           # atomic on POSIX
        logger.debug("inbox write: %s", final_path.name)
        return final_path


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------

HandlerFn = Callable[[InboxEvent], None]


class InboxWatcher:
    """Watchdog-based file-creation watcher for the inbox directory.

    Usage — typical `nullpoint play` startup:

        def on_match(event: InboxEvent) -> None:
            # drive the battle-UI state machine with event.payload
            ...

        watcher = InboxWatcher(handlers={"match": on_match, "pull": on_pull})
        watcher.start()      # spawns observer thread, drains existing files
        watcher.wait()       # block until stop()

    Handler contract:
      - Runs on the watchdog dispatch thread. Fast + non-blocking.
        If you need heavy work, post to a queue.
      - Raising propagates to the watcher, which quarantines the file.
      - Normal return = consume (file deleted) unless `consume=False`.
    """

    def __init__(
        self,
        handlers: dict[str, HandlerFn],
        inbox_dir: Optional[Path | str] = None,
        *,
        consume: bool = True,
        replay_on_start: bool = True,
    ):
        self.inbox_dir = resolve_inbox_dir(inbox_dir)
        ensure_inbox(self.inbox_dir)
        self.handlers = dict(handlers)
        self.consume = consume
        self.replay_on_start = replay_on_start

        self._observer: Optional[Observer] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()      # serialize dispatch (handlers don't need thread-safety)

    # ----- lifecycle -----

    def start(self) -> None:
        """Drain existing files, then start the live observer thread."""
        if self._observer is not None:
            raise RuntimeError("watcher already started")

        if self.replay_on_start:
            self._drain_existing()

        handler = _InboxEventHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.inbox_dir), recursive=False)
        self._observer.start()
        logger.info("inbox watcher live: %s", self.inbox_dir)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the observer thread. Idempotent."""
        self._stop_evt.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=timeout)
            self._observer = None

    def wait(self) -> None:
        """Block until stop() is called from another thread (or Ctrl-C)."""
        try:
            self._stop_evt.wait()
        except KeyboardInterrupt:      # pragma: no cover — interactive only
            self.stop()

    def __enter__(self) -> "InboxWatcher":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ----- internal -----

    def _drain_existing(self) -> None:
        """Pick up files that were written while the watcher was offline.

        Ordered by filename (which starts with ts_ms), so processing matches
        creation order even if ctime is unreliable (e.g. NFS, rsync).
        """
        files = sorted(
            p for p in self.inbox_dir.iterdir()
            if p.is_file() and p.suffix == ".json" and not p.name.endswith(TMP_SUFFIX)
        )
        if not files:
            return
        logger.info("inbox replay: %d pending events", len(files))
        for p in files:
            self._dispatch(p)

    def dispatch_path(self, path: Path) -> None:
        """Public entry for live events. Thin wrapper around _dispatch."""
        self._dispatch(path)

    def _dispatch(self, path: Path) -> None:
        """Parse + route one file. Never raises — errors quarantine the file."""
        with self._lock:
            try:
                event = _parse(path)
            except _InboxError as e:
                logger.warning("inbox parse fail: %s — %s", path.name, e)
                self._quarantine(path, f"parse: {e}")
                return

            handler = self.handlers.get(event.event_type)
            if handler is None:
                logger.info("inbox unknown type %r: %s", event.event_type, path.name)
                self._quarantine(path, f"no handler for event_type={event.event_type!r}")
                return

            try:
                handler(event)
            except Exception as e:       # noqa: BLE001 — handler errors never crash watcher
                logger.exception("inbox handler error: %s", path.name)
                self._quarantine(path, f"handler: {type(e).__name__}: {e}")
                return

            if self.consume:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("inbox: could not delete %s", path)

    def _quarantine(self, path: Path, reason: str) -> None:
        """Move a failed file to `.quarantine/` with a sidecar `.err.txt`."""
        q_dir = self.inbox_dir / QUARANTINE_SUBDIR
        q_dir.mkdir(exist_ok=True)
        if not path.exists():
            return
        dest = q_dir / path.name
        try:
            path.rename(dest)
            (q_dir / (path.name + ".err.txt")).write_text(reason)
        except OSError as e:
            logger.error("inbox: quarantine rename failed: %s", e)


# ---------------------------------------------------------------------------
# Watchdog glue
# ---------------------------------------------------------------------------

class _InboxEventHandler(FileSystemEventHandler):
    """Bridge watchdog events → InboxWatcher.dispatch_path.

    We care about:
      - `on_created` for files written directly (rare — MCP uses rename)
      - `on_moved`   for temp-rename writes (the atomic path)

    We deliberately IGNORE:
      - files ending in `.tmp` (half-written)
      - directories
      - files in subdirs (watchdog recursive=False, but defensive)
    """

    def __init__(self, watcher: InboxWatcher):
        self.watcher = watcher

    def _interested(self, path: Path) -> bool:
        return (
            path.is_file()
            and path.suffix == ".json"
            and not path.name.endswith(TMP_SUFFIX)
            and path.parent == self.watcher.inbox_dir
        )

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        p = Path(event.src_path)
        if self._interested(p):
            self.watcher.dispatch_path(p)

    def on_moved(self, event: FileMovedEvent) -> None:
        if event.is_directory:
            return
        p = Path(event.dest_path)
        if self._interested(p):
            self.watcher.dispatch_path(p)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class _InboxError(Exception):
    """Internal — raised by _parse, caught + quarantined by the watcher."""


def _parse(path: Path) -> InboxEvent:
    try:
        raw = path.read_text()
    except OSError as e:
        raise _InboxError(f"read: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise _InboxError(f"json: {e}") from e

    if not isinstance(data, dict):
        raise _InboxError(f"top-level not object: {type(data).__name__}")

    event_type = data.get("event_type")
    if not isinstance(event_type, str) or not event_type:
        raise _InboxError("missing event_type")

    schema_version = data.get("schema_version", 1)
    if not isinstance(schema_version, int) or schema_version < 1:
        raise _InboxError(f"bad schema_version: {schema_version!r}")

    # Extract ts_ns from filename: <type>_<ts_ns>_<uuid>.json
    ts_ns = 0
    try:
        stem = path.stem                         # drop .json
        parts = stem.rsplit("_", 2)
        if len(parts) >= 2:
            ts_ns = int(parts[-2])
    except (ValueError, IndexError):
        pass  # fallback to 0; not fatal

    return InboxEvent(
        path=path,
        event_type=event_type,
        payload=data,
        ts_ns=ts_ns,
        schema_version=schema_version,
    )
