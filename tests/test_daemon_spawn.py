"""Tests for the detached spawn helper (daimon/daemon/spawn.py).

We don't actually spawn the real daemon (that would pop a pywebview
window in the test runner). Instead we verify the spawn helpers
themselves: command construction, lock-polling timeout, and the
flag-set used to detach the child process.
"""

from __future__ import annotations

import sys


def test_build_command_uses_current_interpreter():
    from daimon.daemon.spawn import _build_command
    cmd = _build_command()
    assert cmd[0] == sys.executable
    assert "_daemon_internal" in cmd
    assert "daimon.cli" in " ".join(cmd)


def test_wait_for_lock_returns_none_on_timeout(monkeypatch, tmp_path):
    """No lock file ever appears → wait_for_lock returns None inside budget."""
    monkeypatch.setenv("DAIMON_HOME", str(tmp_path))
    from daimon.daemon.spawn import wait_for_lock
    info = wait_for_lock(timeout_s=0.1, poll_interval_s=0.01)
    assert info is None


def test_wait_for_lock_picks_up_lock_when_written(monkeypatch, tmp_path):
    """Write a lock mid-poll → wait_for_lock picks it up before timeout."""
    monkeypatch.setenv("DAIMON_HOME", str(tmp_path))
    import threading
    import time as _time

    from daimon.daemon.lock import write_lock
    from daimon.daemon.spawn import wait_for_lock

    def writer():
        _time.sleep(0.05)
        write_lock(pid=4242, port=51234)

    threading.Thread(target=writer, daemon=True).start()
    info = wait_for_lock(timeout_s=2.0, poll_interval_s=0.01)
    assert info is not None
    assert info.pid == 4242
    assert info.port == 51234


def test_spawn_passes_platform_detach_flags(monkeypatch):
    """Verify spawn_detached passes the right detach flags for the host OS.

    Windows: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP.
    POSIX:   start_new_session=True.

    Either branch is correct; we assert on whichever one applies. CI
    matrix runners cover the other branch.
    """
    from daimon.daemon import spawn as spawn_mod
    captured: dict = {}

    class _FakeProc:
        pid = 7777

    def fake_popen(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(spawn_mod.subprocess, "Popen", fake_popen)
    spawn_mod.spawn_detached()

    # Common contract: the child must be detached from parent I/O.
    import subprocess as _sp
    assert captured["kwargs"]["stdin"] == _sp.DEVNULL
    assert captured["kwargs"]["stdout"] == _sp.DEVNULL
    assert captured["kwargs"]["stderr"] == _sp.DEVNULL

    if sys.platform == "win32":
        flags = captured["kwargs"]["creationflags"]
        assert flags & 0x00000008  # DETACHED_PROCESS
        assert flags & 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        assert captured["kwargs"]["start_new_session"] is True
