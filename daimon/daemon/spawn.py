"""Detached spawn for the daimon daemon process.

Per refactor.md §7. ``daimon menu`` calls :func:`spawn_detached` to fork
the daemon process in the background; the parent process returns to the
caller's shell immediately. The detached child survives shell exit.

Cross-platform contract:

  * POSIX (Mac/Linux): ``start_new_session=True`` puts the child in a new
    process group + session, detaching from the controlling terminal.
  * Windows: spawn via ``pythonw.exe`` (the GUI-subsystem Python launcher,
    which has no console at all) plus ``CREATE_NO_WINDOW`` +
    ``CREATE_NEW_PROCESS_GROUP``. Using plain ``python.exe`` with
    ``DETACHED_PROCESS`` still pops a console window because python.exe
    is a console-subsystem binary; ``pythonw.exe`` is the canonical fix.

In both cases stdin/stdout/stderr are routed to a log file (or DEVNULL
on POSIX) so the child never blocks on parent I/O and so future debug
doesn't require running in foreground mode.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Optional

from daimon._winspawn import windowless_creationflags, windowless_python
from daimon.bootstrap import daimon_home
from daimon.daemon.lock import LockInfo, read_lock


def _build_command() -> list[str]:
    """Return the argv the spawned child will exec.

    Uses the GUI-subsystem Python on Windows (see
    :func:`daimon._winspawn.windowless_python`) so no console flashes
    during ``daimon menu``. Re-enters the CLI via ``-m daimon.cli``
    with the hidden ``_daemon_internal`` subcommand.
    """
    return [windowless_python(), "-m", "daimon.cli", "_daemon_internal"]


def _open_log() -> "subprocess._FILE":
    """Open ``~/.daimon/log/daemon.log`` for the child's stdout/stderr.

    Falls back to DEVNULL if the log directory can't be created — never
    fatal at the spawn layer (caller still gets a Popen handle).
    """
    try:
        log_dir = daimon_home() / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        return open(log_dir / "daemon.log", "ab", buffering=0)
    except OSError:
        return subprocess.DEVNULL  # type: ignore[return-value]


def spawn_detached() -> subprocess.Popen:
    """Fork the daemon process detached from this shell.

    Returns the ``Popen`` handle for the parent's debugging needs;
    callers don't typically need it (the child writes its own lock so
    the canonical handle is the lock file, not this Popen).
    """
    cmd = _build_command()
    log = _open_log()
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": log,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = windowless_creationflags()
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def wait_for_lock(*, timeout_s: float = 3.0,
                  poll_interval_s: float = 0.05) -> Optional[LockInfo]:
    """Poll for the daemon's lock file. Returns the LockInfo or None on timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        info = read_lock()
        if info is not None:
            return info
        time.sleep(poll_interval_s)
    return None
