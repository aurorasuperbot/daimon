"""Rate-limited update check + background spawn.

Two entry points:

  * ``ensure_art_available(blocking=False)`` — call from CLI startup. If no
    pack is installed it does a synchronous, user-visible download (the
    first-run experience). If a pack IS installed, it spawns a detached
    background process to check for newer releases and returns immediately.

  * ``spawn_background_check()`` — fire-and-forget. Spawns
    ``python -m daimon.update --check`` with stdin/stdout/stderr redirected
    to the update log, in its own process group, then returns. The parent
    process exits independently.

Rate-limiting:
  * Default: at most one network check per 24 hours per machine.
  * State stored in ``cache/last_check.json`` — atomic write via tempfile +
    rename so a crash mid-write doesn't poison the file.
  * Override via ``DAIMON_UPDATE_CHECK_HOURS`` (0 = check every invocation;
    useful for tests).

Opt-out:
  * ``DAIMON_NO_AUTO_UPDATE=1`` short-circuits ``ensure_art_available`` —
    the caller still works (uses whatever pack is installed) but no
    network call is made.

Why a subprocess rather than a thread?
  * The check involves ~1MB JSON, but the *update* (if one is found)
    downloads 900MB+. We don't want a 30-min download tied to the lifetime
    of the parent CLI invocation. Detaching is the only honest pattern.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from daimon.update.paths import (
    ART_PACK_NAME,
    art_pack_dir,
    auto_update_enabled,
    cache_dir,
    current_version,
    last_check_path,
    pinned_version,
    update_check_interval_hours,
    update_log_path,
)


# ---------------------------------------------------------------------------
# last_check.json — atomic JSON state
# ---------------------------------------------------------------------------

def read_last_check() -> dict:
    """Load the last-check state. Returns ``{}`` if missing or malformed."""
    p = last_check_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_last_check(state: dict) -> None:
    """Atomic write — tempfile in same dir, fsync, rename."""
    p = last_check_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)


def update_last_check(
    *,
    latest_seen: Optional[str] = None,
    error: Optional[str] = None,
    action: str = "checked",
) -> None:
    """Merge a fresh result into ``last_check.json``."""
    state = read_last_check()
    state["ts"] = int(time.time())
    state["action"] = action
    if latest_seen:
        state["latest_seen"] = latest_seen
    if error:
        state["last_error"] = error
    elif "last_error" in state:
        # Successful check — clear any prior error.
        state.pop("last_error", None)
    write_last_check(state)


def is_check_due(now: Optional[float] = None) -> bool:
    """True if the rate-limit window has elapsed (or no prior check)."""
    interval_h = update_check_interval_hours()
    if interval_h <= 0:
        return True
    state = read_last_check()
    last_ts = state.get("ts")
    if not isinstance(last_ts, (int, float)):
        return True
    now = now if now is not None else time.time()
    return (now - last_ts) >= (interval_h * 3600.0)


# ---------------------------------------------------------------------------
# pack-installed predicate
# ---------------------------------------------------------------------------

def is_pack_installed(pack_name: str = ART_PACK_NAME) -> bool:
    """True iff ``art/<pack>/.version`` exists AND the pack dir is non-empty.

    The version file alone is insufficient — a half-finished install could
    leave the file present but the dir empty. We require at least one
    card-id subdir as a sanity check.
    """
    if current_version(pack_name) is None:
        return False
    pack = art_pack_dir(pack_name)
    if not pack.is_dir():
        return False
    # Cheap non-empty check: any subdir means at least one card landed.
    try:
        for child in pack.iterdir():
            if child.is_dir():
                return True
    except OSError:
        return False
    return False


# ---------------------------------------------------------------------------
# Background spawn
# ---------------------------------------------------------------------------

def _open_log() -> tuple[Path, "Optional[int]"]:
    """Open the update log for append. Returns (path, fd) — caller closes fd."""
    p = update_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    return p, fd


def spawn_background_check() -> Optional[int]:
    """Fire-and-forget ``python -m daimon.update --check``.

    Returns the child PID (informational) or None if the spawn failed
    silently — we never raise from this path because it's called from
    every CLI invocation and must not block normal operation.

    The child:
      * inherits no fds (stdin from devnull, stdout/stderr to update.log)
      * starts a new session (``start_new_session=True``) so a parent
        SIGINT doesn't propagate
      * uses ``sys.executable`` so virtualenvs are honored
    """
    try:
        log_path, log_fd = _open_log()
    except OSError:
        return None

    try:
        devnull = os.open(os.devnull, os.O_RDONLY)
    except OSError:
        os.close(log_fd)
        return None

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "daimon.update", "--check"],
            stdin=devnull,
            stdout=log_fd,
            stderr=log_fd,
            close_fds=True,
            start_new_session=True,
            env=os.environ.copy(),
        )
        return proc.pid
    except (OSError, ValueError):
        return None
    finally:
        # Parent doesn't need these fds; the child has its own copies.
        os.close(devnull)
        os.close(log_fd)


# ---------------------------------------------------------------------------
# CLI-startup entry point
# ---------------------------------------------------------------------------

def ensure_art_available(
    blocking: bool = False,
    pack_name: str = ART_PACK_NAME,
) -> None:
    """Guarantee an art pack is on disk; spawn an async update check if due.

    Behavior matrix:

        installed   auto_update   check_due   action
        ----------  ------------  ----------  ---------------------------
        no          *             *           SYNCHRONOUS download (block)
        yes         no            *           no-op
        yes         yes           no          no-op
        yes         yes           yes         spawn detached check

    With ``blocking=True``, also runs the check synchronously when due
    (used by the explicit ``daimon update`` command — there we WANT to
    block on the network call).

    This function never raises on network failure when a pack is already
    installed — that's the whole point of the rate-limit + background
    pattern. First-run failures DO propagate (the caller can't proceed
    without art).
    """
    if not is_pack_installed(pack_name):
        # First run — synchronous fetch with the user watching.
        from daimon.update.fetcher import do_update
        cache_dir().mkdir(parents=True, exist_ok=True)
        sys.stderr.write(
            "daimon: no art pack installed — fetching latest "
            f"({pinned_version() or 'art-v*'})...\n"
        )
        sys.stderr.flush()
        try:
            rel = do_update(show_progress=True, pack_name=pack_name)
            update_last_check(latest_seen=rel.tag, action="installed")
            sys.stderr.write(
                f"daimon: installed {rel.tag} into {art_pack_dir(pack_name)}\n"
            )
            sys.stderr.flush()
        except Exception as e:
            update_last_check(error=str(e), action="install_failed")
            raise
        return

    if not auto_update_enabled():
        return

    if not is_check_due():
        return

    if blocking:
        # Used by `daimon update` — synchronous, but still don't crash on
        # network errors (the caller can run with the existing pack).
        from daimon.update.fetcher import ArtUpdateError, do_update
        try:
            rel = do_update(show_progress=True, pack_name=pack_name)
            update_last_check(latest_seen=rel.tag, action="updated")
        except ArtUpdateError as e:
            update_last_check(error=str(e), action="update_failed")
            sys.stderr.write(f"daimon: update check failed: {e}\n")
            sys.stderr.flush()
        return

    spawn_background_check()


__all__ = [
    "ensure_art_available",
    "spawn_background_check",
    "is_check_due",
    "is_pack_installed",
    "read_last_check",
    "write_last_check",
    "update_last_check",
]
