"""Tests for :mod:`daimon.play.spawn`.

Covers:

  * PID-file lifecycle (read / write atomic / clear).
  * ``is_play_running`` against live + dead PIDs.
  * ``spawn_play_hud`` opt-outs (env kill switch, inside-terminal,
    no-TTY guard).
  * No-double-spawn when an existing live PID is recorded.
  * Detached spawn invokes the right argv (Popen is mocked — we don't
    actually spin up a HUD).
  * ``stop_play_hud`` signals + clears.
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from daimon.play import spawn


@pytest.fixture
def tty_stdout(monkeypatch):
    """Pretend stdout is a TTY so the auto-spawn doesn't bail on the guard.

    Pytest's capture mechanism wraps ``sys.stdout`` in a ``CaptureIO``
    that ``isatty()``-returns False, and a plain ``monkeypatch.setattr``
    races with that wrapping. Patch ``isatty`` on the live object
    instead — that survives whatever pytest is doing underneath.
    """
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)


@pytest.fixture
def fake_config_dir(tmp_path, monkeypatch):
    """Sandbox the play.pid path."""
    monkeypatch.setattr(
        "daimon.identity.keys.CONFIG_DIR", tmp_path, raising=False
    )
    return tmp_path


@pytest.fixture
def env_clean(monkeypatch):
    """Ensure no opt-out env vars leak between tests."""
    monkeypatch.delenv("DAIMON_NO_AUTO_HUD", raising=False)
    monkeypatch.delenv("DAIMON_INSIDE_TERMINAL", raising=False)


# ---------------------------------------------------------------------------
# PID file primitives
# ---------------------------------------------------------------------------

class TestPidFile:
    def test_read_returns_none_when_missing(self, fake_config_dir):
        assert spawn.read_play_pid() is None

    def test_read_returns_none_for_garbage(self, fake_config_dir):
        spawn.play_pid_path().write_text("not-a-pid\n")
        assert spawn.read_play_pid() is None

    def test_atomic_write_round_trips(self, fake_config_dir):
        spawn._write_pid_atomic(spawn.play_pid_path(), 4242)
        assert spawn.read_play_pid() == 4242

    def test_clear_pid_file(self, fake_config_dir):
        spawn._write_pid_atomic(spawn.play_pid_path(), 4242)
        assert spawn.clear_pid_file() is True
        assert not spawn.play_pid_path().exists()
        # Idempotent — second clear is a no-op.
        assert spawn.clear_pid_file() is False


# ---------------------------------------------------------------------------
# Liveness check
# ---------------------------------------------------------------------------

class TestPidAlive:
    def test_zero_or_negative_is_dead(self):
        assert spawn._pid_alive(0) is False
        assert spawn._pid_alive(-1) is False

    def test_current_process_is_alive(self):
        # The PID of THIS test runner exists by definition.
        assert spawn._pid_alive(os.getpid()) is True

    def test_unlikely_pid_is_dead(self):
        # A PID well above the typical max (Linux: 4_194_304; Windows:
        # 32-bit) should never be in use.
        assert spawn._pid_alive(2_147_000_000) is False

    def test_is_play_running_false_when_no_pid_file(self, fake_config_dir):
        assert spawn.is_play_running() is False

    def test_is_play_running_true_for_self(self, fake_config_dir):
        # Write our own PID — we know we're alive.
        spawn._write_pid_atomic(spawn.play_pid_path(), os.getpid())
        assert spawn.is_play_running() is True

    def test_is_play_running_false_for_dead_pid(self, fake_config_dir):
        spawn._write_pid_atomic(spawn.play_pid_path(), 2_147_000_000)
        assert spawn.is_play_running() is False


# ---------------------------------------------------------------------------
# Spawn — opt-outs
# ---------------------------------------------------------------------------

class TestSpawnOptOuts:
    def test_env_kill_switch_returns_none(
        self, fake_config_dir, env_clean, tty_stdout, monkeypatch
    ):
        monkeypatch.setenv("DAIMON_NO_AUTO_HUD", "1")
        # Popen would have been called — make sure it isn't.
        called: List[Any] = []
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda *a, **kw: called.append((a, kw)) or _FakeProc(123),
        )
        assert spawn.spawn_play_hud() is None
        assert called == []

    def test_inside_terminal_returns_none(
        self, fake_config_dir, env_clean, tty_stdout, monkeypatch
    ):
        monkeypatch.setenv("DAIMON_INSIDE_TERMINAL", "1")
        called: List[Any] = []
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda *a, **kw: called.append((a, kw)) or _FakeProc(123),
        )
        assert spawn.spawn_play_hud() is None
        assert called == []

    def test_no_tty_returns_none(self, fake_config_dir, env_clean, monkeypatch):
        class _NoTty:
            def isatty(self) -> bool:
                return False
        monkeypatch.setattr("sys.stdout", _NoTty())
        called: List[Any] = []
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda *a, **kw: called.append((a, kw)) or _FakeProc(123),
        )
        assert spawn.spawn_play_hud() is None
        assert called == []

    def test_require_tty_false_skips_tty_guard(
        self, fake_config_dir, env_clean, monkeypatch
    ):
        class _NoTty:
            def isatty(self) -> bool:
                return False
        monkeypatch.setattr("sys.stdout", _NoTty())

        captured: List[Any] = []
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda *args, **kw: captured.append((args, kw)) or _FakeProc(987),
        )
        pid = spawn.spawn_play_hud(require_tty=False)
        assert pid == 987
        assert captured  # one call was made


# ---------------------------------------------------------------------------
# Spawn — happy path (Popen mocked)
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class TestSpawnHappyPath:
    def test_records_pid_after_successful_spawn(
        self, fake_config_dir, env_clean, tty_stdout, monkeypatch
    ):
        captured: List[Any] = []

        def fake_popen(args, **kwargs):
            captured.append({"args": list(args), "kwargs": kwargs})
            return _FakeProc(31415)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        # Force "not frozen" branch.
        monkeypatch.delattr("sys.frozen", raising=False)

        pid = spawn.spawn_play_hud()
        assert pid == 31415
        assert spawn.read_play_pid() == 31415
        assert len(captured) == 1
        argv = captured[0]["args"]
        assert argv[0] == sys.executable
        assert argv[1:3] == ["-m", "daimon.cli"]
        assert "play" in argv

    def test_state_path_forwarded(
        self, fake_config_dir, env_clean, tty_stdout, monkeypatch, tmp_path
    ):
        captured: List[Any] = []

        def fake_popen(args, **kwargs):
            captured.append(list(args))
            return _FakeProc(2)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.delattr("sys.frozen", raising=False)
        state = tmp_path / "state.json"
        spawn.spawn_play_hud(state_path=state)
        assert "--state" in captured[0]
        idx = captured[0].index("--state")
        assert captured[0][idx + 1] == str(state)

    def test_frozen_binary_argv_drops_dash_m(
        self, fake_config_dir, env_clean, tty_stdout, monkeypatch
    ):
        captured: List[Any] = []
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda args, **kw: captured.append(list(args)) or _FakeProc(7),
        )
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        spawn.spawn_play_hud()
        assert captured[0][:2] == [sys.executable, "play"]

    def test_no_double_spawn_when_pid_alive(
        self, fake_config_dir, env_clean, tty_stdout, monkeypatch
    ):
        # Pre-populate with our own PID (definitely alive).
        spawn._write_pid_atomic(spawn.play_pid_path(), os.getpid())
        called: List[Any] = []
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda *a, **kw: called.append(True) or _FakeProc(1),
        )
        pid = spawn.spawn_play_hud()
        # Returned the existing PID, did not spawn.
        assert pid == os.getpid()
        assert called == []

    def test_overwrites_stale_pid(
        self, fake_config_dir, env_clean, tty_stdout, monkeypatch
    ):
        # Write a definitely-dead PID.
        spawn._write_pid_atomic(spawn.play_pid_path(), 2_147_000_000)
        captured: List[Any] = []
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda args, **kw: captured.append(list(args)) or _FakeProc(8888),
        )
        monkeypatch.delattr("sys.frozen", raising=False)
        pid = spawn.spawn_play_hud()
        assert pid == 8888
        assert spawn.read_play_pid() == 8888
        assert captured  # spawn happened

    def test_popen_failure_returns_none(
        self, fake_config_dir, env_clean, tty_stdout, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(subprocess, "Popen", boom)
        monkeypatch.delattr("sys.frozen", raising=False)
        assert spawn.spawn_play_hud() is None
        # No PID file should have been written.
        assert not spawn.play_pid_path().exists()


# ---------------------------------------------------------------------------
# Detached-spawn flags
# ---------------------------------------------------------------------------

class TestDetachedFlags:
    def test_posix_uses_start_new_session(
        self, fake_config_dir, env_clean, tty_stdout, monkeypatch
    ):
        if platform.system() == "Windows":
            pytest.skip("POSIX-only flag")
        captured: Dict[str, Any] = {}
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda args, **kw: captured.update(kw) or _FakeProc(1),
        )
        monkeypatch.delattr("sys.frozen", raising=False)
        spawn.spawn_play_hud()
        assert captured.get("start_new_session") is True
        assert captured.get("close_fds") is True

    def test_windows_uses_detached_creationflags(
        self, fake_config_dir, env_clean, tty_stdout, monkeypatch
    ):
        if platform.system() != "Windows":
            pytest.skip("Windows-only flags")
        captured: Dict[str, Any] = {}
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda args, **kw: captured.update(kw) or _FakeProc(1),
        )
        monkeypatch.delattr("sys.frozen", raising=False)
        spawn.spawn_play_hud()
        flags = captured.get("creationflags", 0)
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        assert flags & DETACHED_PROCESS
        assert flags & CREATE_NEW_PROCESS_GROUP
        assert captured.get("close_fds") is True


# ---------------------------------------------------------------------------
# stop_play_hud
# ---------------------------------------------------------------------------

class TestStopPlayHud:
    def test_returns_none_when_no_pid_file(self, fake_config_dir, env_clean):
        assert spawn.stop_play_hud() is None

    def test_returns_none_for_dead_pid_and_clears_file(
        self, fake_config_dir, env_clean
    ):
        spawn._write_pid_atomic(spawn.play_pid_path(), 2_147_000_000)
        assert spawn.stop_play_hud() is None
        assert not spawn.play_pid_path().exists()

    def test_signals_live_pid(self, fake_config_dir, env_clean, monkeypatch):
        spawn._write_pid_atomic(spawn.play_pid_path(), 1234)
        # Pretend the recorded PID is alive AND that os.kill succeeds.
        monkeypatch.setattr(spawn, "_pid_alive", lambda pid: pid == 1234)
        sent: List[int] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
        result = spawn.stop_play_hud()
        assert result == 1234
        assert sent == [(1234, signal.SIGTERM)]
        assert not spawn.play_pid_path().exists()
