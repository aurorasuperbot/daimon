"""Single-instance lock for the daimon menu daemon.

Per refactor.md §7. The lock file at ``~/.daimon/run/menu.lock`` records
``{pid, port, started_at, version}``. ``daimon menu`` reads it before
spawning a new daemon — if the recorded PID is still alive, no second
daemon is launched.

Stale-PID recovery: a crashed daemon leaves an orphan lock; the next
``daimon menu`` detects the dead PID and overwrites it. This avoids the
"user must rm a file" failure mode classic O_EXCL pidfiles suffer from.

Lock liveness reuses the production ``_pid_alive`` helper from
``daimon.update.prefetch`` — same cross-platform contract (Windows
returns False on unknown PIDs without raising ProcessLookupError).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from daimon import __version__
from daimon.bootstrap import daimon_home
from daimon.update.prefetch import _pid_alive


LOCK_NAME = "menu.lock"


def lock_path() -> Path:
    return daimon_home() / "run" / LOCK_NAME


@dataclass(frozen=True)
class LockInfo:
    pid: int
    port: int
    started_at: str
    version: str


def read_lock() -> Optional[LockInfo]:
    """Return the lock contents, or ``None`` if missing/corrupt."""
    p = lock_path()
    if not p.is_file():
        return None
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    try:
        return LockInfo(
            pid=int(body["pid"]),
            port=int(body["port"]),
            started_at=str(body["started_at"]),
            version=str(body["version"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def write_lock(*, pid: int, port: int) -> LockInfo:
    """Atomically write the lock file. Caller must hold the singleton role."""
    info = LockInfo(
        pid=pid,
        port=port,
        started_at=datetime.now(timezone.utc).isoformat(),
        version=__version__,
    )
    p = lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(info.__dict__, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return info


def remove_lock() -> None:
    """Best-effort lock cleanup. Silent if already gone."""
    try:
        lock_path().unlink()
    except FileNotFoundError:
        pass


def alive_lock() -> Optional[LockInfo]:
    """Return the lock IFF its recorded PID is still running. Else None.

    A stale lock (dead PID) is the same as no lock — caller should spawn
    a fresh daemon. The stale file gets overwritten by the next
    successful ``write_lock``; we don't pre-clean it here so two racing
    callers don't both delete + spawn.
    """
    info = read_lock()
    if info is None:
        return None
    if not _pid_alive(info.pid):
        return None
    return info
