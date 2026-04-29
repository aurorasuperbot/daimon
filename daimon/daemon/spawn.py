"""Detached spawn for the daimon daemon process.

Per refactor.md §7. ``daimon menu`` calls :func:`spawn_detached` to fork
the daemon process in the background; the parent process returns to the
caller's shell immediately. The detached child survives shell exit.

Cross-platform contract:

  * POSIX (Mac/Linux): ``start_new_session=True`` puts the child in a new
    process group + session, detaching from the controlling terminal.
  * Windows: ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` flags.

In both cases stdin/stdout/stderr are routed to ``DEVNULL`` so the child
never blocks on parent I/O.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Optional

from daimon.daemon.lock import LockInfo, read_lock


def _build_command() -> list[str]:
    """Return the argv the spawned child will exec.

    Uses the current Python interpreter (handles venvs and frozen
    bundles correctly) and re-enters the CLI via ``-m daimon.cli`` with
    the hidden ``_daemon_internal`` subcommand.
    """
    return [sys.executable, "-m", "daimon.cli", "_daemon_internal"]


def spawn_detached() -> subprocess.Popen:
    """Fork the daemon process detached from this shell.

    Returns the ``Popen`` handle for the parent's debugging needs;
    callers don't typically need it (the child writes its own lock so
    the canonical handle is the lock file, not this Popen).
    """
    cmd = _build_command()
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        # DETACHED_PROCESS=0x00000008, CREATE_NEW_PROCESS_GROUP=0x00000200
        kwargs["creationflags"] = 0x00000008 | 0x00000200
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
