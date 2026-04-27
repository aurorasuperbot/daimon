"""HUD auto-spawn — detached background launcher for ``daimon play``.

The spectator HUD (``daimon play``) is a long-lived process that watches
``state.json`` for new match / pull events and animates them. Historically
it had to be started by the user manually before any agent calls
``dm_match`` / ``dm_pull``. That breaks the "single onboarding step, then
forget about it" UX we want.

This module owns the detached spawn so that the very first
``dm_match`` / ``dm_pull`` after onboarding pops a HUD window. The user
sees the match animate; the agent's MCP tool returns immediately because
spawning is best-effort and never blocks the response.

Cross-platform detached-spawn semantics:

  * **POSIX** — ``Popen(..., start_new_session=True)`` puts the child in
    its own session+process group. Closing the parent terminal won't
    SIGHUP the HUD; SIGINT to the parent shell won't tear it down.
  * **Windows** — ``CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`` plus
    ``close_fds=True``. The child gets its own console (which WezTerm
    immediately replaces with its own window) and the parent process
    can exit while the HUD keeps running.

PID handling: we drop a PID file at ``<config>/play.pid`` so a second
``dm_match`` call doesn't double-spawn. Stale files (process gone) are
treated as "no HUD running" and overwritten on the next spawn. The HUD
itself does not write the PID file — the spawner does, atomically, with
the spawned PID before returning.

Opt-outs:

  * ``DAIMON_NO_AUTO_HUD=1`` — environment-level kill switch. Lets a
    headless agent run with the MCP tools without ever spawning a
    window. The auto-spawn hook is also a no-op when the env var is
    set, even if the PID file is missing.
  * ``DAIMON_INSIDE_TERMINAL=1`` — already running inside our bundled
    WezTerm. Spawning another HUD would either steal the user's focus
    or spawn a duplicate window; skip it.
  * No TTY — ``sys.stdout.isatty()`` is False (e.g. piped, agent shell).
    Caller controls this via the ``require_tty`` arg, which defaults
    to True for the auto-spawn case.
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional


_OPTOUT_ENV = "DAIMON_NO_AUTO_HUD"
_INSIDE_TERMINAL_ENV = "DAIMON_INSIDE_TERMINAL"


# ---------------------------------------------------------------------------
# PID file paths
# ---------------------------------------------------------------------------

def play_pid_path() -> Path:
    """Path to the play HUD PID file.

    Resolved on every call (not at import time) so tests that
    monkeypatch ``daimon.identity.keys.CONFIG_DIR`` see the override.
    """
    from daimon.identity.keys import CONFIG_DIR
    return CONFIG_DIR / "play.pid"


# ---------------------------------------------------------------------------
# Liveness check
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    """Cross-platform 'is this PID still running' check.

    POSIX: ``os.kill(pid, 0)`` raises ``ProcessLookupError`` if the
    process is gone, ``PermissionError`` if it exists but we can't
    signal it (treated as alive — only happens on a multi-user box
    when the HUD is owned by another user; we shouldn't double-spawn
    in that case).

    Windows: there's no signal-0 equivalent, so we shell out to a
    syscall via ``ctypes`` — ``OpenProcess(PROCESS_QUERY_LIMITED_INFO)``
    succeeds iff the PID exists.
    """
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            import ctypes
            from ctypes import wintypes  # noqa: F401
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            # Make sure it isn't a zombie waiting on close.
            STILL_ACTIVE = 259
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            )
            kernel32.CloseHandle(handle)
            return bool(ok) and exit_code.value == STILL_ACTIVE
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def read_play_pid(pid_path: Optional[Path] = None) -> Optional[int]:
    """Read and parse the recorded PID; return None on missing or malformed file."""
    p = pid_path or play_pid_path()
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def is_play_running(pid_path: Optional[Path] = None) -> bool:
    """True iff the recorded PID is alive.

    Best-effort: a False positive (PID file with a live, unrelated PID
    that happens to match) is harmless — the worst case is one skipped
    auto-spawn, and the HUD itself is idempotent (the next state.json
    write picks up a stale HUD because both processes watch the same
    file).
    """
    pid = read_play_pid(pid_path)
    if pid is None:
        return False
    return _pid_alive(pid)


# ---------------------------------------------------------------------------
# Spawn
# ---------------------------------------------------------------------------

def _build_spawn_command(state_path: Optional[Path] = None) -> List[str]:
    """argv for the child process — invokes ``daimon play``.

    We don't shell out — the child is launched with the same Python
    interpreter that's running us so the venv / frozen-binary case Just
    Works without a PATH lookup. ``-m daimon.cli`` is the canonical
    entry point on a source install; for frozen builds we use
    ``sys.executable`` directly with a ``daimon`` argv0 so Click sees
    the ``play`` subcommand.
    """
    argv: List[str]
    if getattr(sys, "frozen", False):
        # Frozen binary — daimon[.exe] is the executable, no -m needed.
        argv = [sys.executable, "play"]
    else:
        argv = [sys.executable, "-m", "daimon.cli", "play"]
    if state_path is not None:
        argv.extend(["--state", str(state_path)])
    return argv


def spawn_play_hud(
    *,
    state_path: Optional[Path] = None,
    require_tty: bool = True,
    pid_path: Optional[Path] = None,
    env_overrides: Optional[dict] = None,
) -> Optional[int]:
    """Spawn ``daimon play`` detached from the parent process.

    Returns the spawned PID on success, ``None`` when we deliberately
    opted out (env-var kill switch, no TTY, already-inside-terminal,
    HUD already running). Raises only on programmer error — every IO
    / OS failure path is caught and reported by returning ``None``.

    Args:
        state_path: forward to ``daimon play --state``. ``None`` lets
            the HUD resolve its own default (DAIMON_STATE env / XDG).
        require_tty: when True (default), refuse to spawn if stdout
            isn't a TTY. Stops the auto-spawn from popping a window in
            CI / piped agent shells. Set False from the explicit
            ``daimon onboard`` orchestrator.
        pid_path: override for the PID file. Default
            ``<config>/play.pid``.
        env_overrides: extra env vars to set in the child. Used by
            tests to inject ``DAIMON_HOME`` / ``DAIMON_STATE``.
    """
    if os.environ.get(_OPTOUT_ENV) == "1":
        return None
    if os.environ.get(_INSIDE_TERMINAL_ENV) == "1":
        # Already running inside our terminal — the HUD command is
        # already this process's parent or sibling. Don't double-spawn.
        return None
    if require_tty and not sys.stdout.isatty():
        return None

    target_pid_path = pid_path or play_pid_path()
    if is_play_running(target_pid_path):
        # HUD already up — return the existing PID rather than spawn a duplicate.
        return read_play_pid(target_pid_path)

    argv = _build_spawn_command(state_path)
    child_env = os.environ.copy()
    if env_overrides:
        child_env.update({k: str(v) for k, v in env_overrides.items()})

    try:
        if platform.system() == "Windows":
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            DETACHED_PROCESS = 0x00000008
            proc = subprocess.Popen(
                argv,
                creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
                close_fds=True,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            proc = subprocess.Popen(
                argv,
                start_new_session=True,
                close_fds=True,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except (OSError, ValueError):
        # Spawn failed (binary missing, env var weirdness on Windows,
        # /dev/null absent). Auto-spawn is best-effort — the agent's
        # MCP response shouldn't fail because of a window-popping side
        # effect. Caller can detect None and skip the "HUD spawned"
        # affordance.
        return None

    _write_pid_atomic(target_pid_path, proc.pid)
    return proc.pid


def _write_pid_atomic(path: Path, pid: int) -> None:
    """Tempfile + rename so a crash mid-write never leaves a half-baked PID file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(f"{pid}\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def clear_pid_file(pid_path: Optional[Path] = None) -> bool:
    """Remove the PID file. Returns True iff a file was removed.

    Called by the HUD on graceful shutdown, and by the doctor command
    when it detects a stale entry. Safe to call when the file is
    already absent.
    """
    p = pid_path or play_pid_path()
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def stop_play_hud(
    *,
    pid_path: Optional[Path] = None,
    timeout: float = 2.0,
) -> Optional[int]:
    """Send SIGTERM to a recorded HUD PID and clear the PID file.

    Used by the upcoming ``daimon play stop`` CLI command. Returns the
    PID that was signalled, or ``None`` if no live HUD was found.
    """
    target = pid_path or play_pid_path()
    pid = read_play_pid(target)
    if pid is None or not _pid_alive(pid):
        clear_pid_file(target)
        return None
    try:
        if platform.system() == "Windows":
            # SIGTERM on Windows maps to TerminateProcess. CTRL_BREAK_EVENT
            # would only work for processes in our own console group.
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        clear_pid_file(target)
        return None
    clear_pid_file(target)
    return pid
