"""Rate-limited update check + background spawn.

Two entry points:

  * ``ensure_art_available(blocking=False)`` — call from CLI startup. If no
    manifest is installed it does a synchronous, user-visible fetch of the
    pack manifest (small — ~50-100 KB; well under one second on a normal
    connection). If a manifest IS installed, it spawns a detached
    background process to check for newer releases and returns immediately.

  * ``spawn_background_check()`` — fire-and-forget. Spawns
    ``python -m daimon.update --check`` with stdin/stdout/stderr redirected
    to the update log, in its own process group, then returns. The parent
    process exits independently.

Per-card art is fetched lazily by :func:`daimon.update.lazy.ensure_art_for`
on the first render of each card, so this module never blocks the CLI on
a multi-hundred-megabyte download — that pattern died with the monolithic
art-pack flow.

Rate-limiting:
  * Default: at most one network check per 24 hours per machine.
  * State stored in ``cache/last_check.json`` — atomic write via tempfile +
    rename so a crash mid-write doesn't poison the file.
  * Override via ``DAIMON_UPDATE_CHECK_HOURS`` (0 = check every invocation;
    useful for tests).

Opt-out:
  * ``DAIMON_NO_AUTO_UPDATE=1`` short-circuits ``ensure_art_available`` —
    the caller still works (renders placeholders for any cards not on
    disk) but no network call is made.

Why a subprocess for the background check?
  * The check involves ~1 MB JSON of release metadata. The actual updates
    are per-card (~50-500 KB each, fetched on demand by lazy.ensure_art_for)
    so the detach pattern protects the parent CLI from a slow network on
    the *check* itself, not from a giant tarball — the tarball is gone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from daimon._winspawn import windowless_creationflags, windowless_python
from daimon.update.paths import (
    ART_PACK_NAME,
    art_pack_dir,
    auto_update_enabled,
    cache_dir,
    current_version,
    last_check_path,
    manifest_path,
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
# Pack-state predicates
# ---------------------------------------------------------------------------

def is_manifest_installed(pack_name: str = ART_PACK_NAME) -> bool:
    """True iff a manifest is on disk for the given pack.

    This is the *minimum* installed state under the lazy-art model — once
    the manifest is present the runtime can lazily fetch any card on
    demand. ``is_pack_installed`` (below) tracks the *richer* state of
    "any cards have actually been downloaded" — useful for telling a fresh
    onboard from one that has begun materializing art.
    """
    return manifest_path(pack_name).is_file()


def is_pack_installed(pack_name: str = ART_PACK_NAME) -> bool:
    """True iff at least one card has been materialized under the live pack.

    Distinct from :func:`is_manifest_installed`: a fresh onboard installs
    the manifest but no cards (those land lazily on first render). We
    expose both predicates so callers can distinguish "the runtime can
    fetch any card it needs" (manifest installed) from "the user has
    already played at least one match" (cards materialized).
    """
    if current_version(pack_name) is None:
        return False
    pack = art_pack_dir(pack_name)
    if not pack.is_dir():
        return False
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
            [windowless_python(), "-m", "daimon.update", "--check"],
            stdin=devnull,
            stdout=log_fd,
            stderr=log_fd,
            close_fds=True,
            start_new_session=True,
            creationflags=windowless_creationflags(),
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
    """Guarantee a manifest is on disk; spawn an async update check if due.

    Behavior matrix:

        manifest    auto_update   check_due   action
        ----------  ------------  ----------  ---------------------------
        absent      no            *           WARN to stderr, return
        absent      yes           *           SYNCHRONOUS manifest fetch
        present     no            *           no-op
        present     yes           no          no-op
        present     yes           yes         spawn detached check

    With ``blocking=True``, also runs the check synchronously when due
    (used by the explicit ``daimon update`` command — there we WANT to
    block on the network call).

    ``DAIMON_NO_AUTO_UPDATE=1`` is an UNCONDITIONAL opt-out: it suppresses
    the first-run sync fetch as well as background checks. When opted out
    with no manifest installed we emit a one-line stderr warning explaining
    the state (per-card art will fall back to placeholders via
    :func:`daimon.update.lazy.ensure_art_for`'s soft-fail).

    This function never raises on network failure when a manifest is
    already installed. First-run network failures DO propagate when
    auto-update is enabled (the caller asked for the fetch and we
    couldn't deliver).

    Per-card art (PNG bytes) is fetched lazily by callers via
    :func:`daimon.update.lazy.ensure_art_for`. This function only ensures
    the *manifest* — the index that lazy fetching needs to compute
    per-card URLs and digests.
    """
    # Opt-out short-circuit — UNCONDITIONAL. If the user set
    # DAIMON_NO_AUTO_UPDATE=1 they explicitly do not want network calls.
    if not auto_update_enabled():
        if not is_manifest_installed(pack_name):
            sys.stderr.write(
                f"daimon: DAIMON_NO_AUTO_UPDATE=1 is set but no manifest "
                f"is installed at {manifest_path(pack_name)}.\n"
                f"  card art will fall back to placeholders.\n"
                f"  to install: unset DAIMON_NO_AUTO_UPDATE and re-run, "
                f"or run `daimon update` manually.\n"
            )
            sys.stderr.flush()
        return

    if not is_manifest_installed(pack_name):
        # First run, auto-update enabled — synchronous fetch with the
        # user watching. Manifest-only is small (~50-100 KB) so this is
        # the right place to block, unlike the legacy 1.6 GB tarball.
        from daimon.update.manifest import fetch_manifest
        cache_dir().mkdir(parents=True, exist_ok=True)
        sys.stderr.write(
            "daimon: no manifest installed — fetching latest "
            f"({pinned_version() or 'art-v*'})...\n"
        )
        sys.stderr.flush()
        try:
            m = fetch_manifest(show_progress=True, pack_name=pack_name)
            update_last_check(latest_seen=m.pack_version, action="installed")
            sys.stderr.write(
                f"daimon: installed manifest for {m.pack_version} "
                f"({m.card_count} cards) — art will be fetched on demand.\n"
            )
            sys.stderr.flush()
        except Exception as e:
            update_last_check(error=str(e), action="install_failed")
            raise
        return

    if not is_check_due():
        return

    if blocking:
        # Used by `daimon update` — synchronous, but still don't crash on
        # network errors (the caller can run with the existing manifest).
        from daimon.update.fetcher import ArtUpdateError
        from daimon.update.manifest import fetch_manifest
        try:
            m = fetch_manifest(show_progress=True, pack_name=pack_name)
            update_last_check(latest_seen=m.pack_version, action="updated")
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
    "is_manifest_installed",
    "is_pack_installed",
    "read_last_check",
    "write_last_check",
    "update_last_check",
]
