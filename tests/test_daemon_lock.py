"""Tests for the singleton menu lock (daimon/daemon/lock.py)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    home = tmp_path / "daimon_home"
    monkeypatch.setenv("DAIMON_HOME", str(home))
    return home


def test_read_lock_returns_none_when_missing(isolated_home: Path):
    from daimon.daemon.lock import read_lock
    assert read_lock() is None


def test_write_then_read_round_trip(isolated_home: Path):
    from daimon.daemon.lock import read_lock, write_lock
    info = write_lock(pid=12345, port=51234)
    again = read_lock()
    assert again is not None
    assert again.pid == info.pid == 12345
    assert again.port == info.port == 51234
    assert again.version == info.version
    assert again.started_at == info.started_at


def test_write_lock_creates_run_dir(isolated_home: Path):
    from daimon.daemon.lock import lock_path, write_lock
    write_lock(pid=1, port=1)
    assert lock_path().is_file()
    assert lock_path().parent.is_dir()


def test_remove_lock_is_silent_when_missing(isolated_home: Path):
    from daimon.daemon.lock import remove_lock
    remove_lock()  # must not raise
    remove_lock()  # idempotent


def test_remove_lock_clears_existing(isolated_home: Path):
    from daimon.daemon.lock import lock_path, remove_lock, write_lock
    write_lock(pid=1, port=1)
    assert lock_path().exists()
    remove_lock()
    assert not lock_path().exists()


def test_alive_lock_returns_lock_when_pid_alive(isolated_home: Path):
    """Use the test process's own PID — guaranteed to be alive."""
    from daimon.daemon.lock import alive_lock, write_lock
    write_lock(pid=os.getpid(), port=51234)
    info = alive_lock()
    assert info is not None
    assert info.pid == os.getpid()


def test_alive_lock_returns_none_for_dead_pid(isolated_home: Path):
    """A lock recording a definitely-dead PID is treated as no lock."""
    from daimon.daemon.lock import alive_lock, write_lock
    from daimon.update.prefetch import _pid_alive
    # Same dead-PID discovery loop as test_prefetch — works on Win + POSIX.
    dead_pid = next(p for p in range(2_000_000, 2_100_000) if not _pid_alive(p))
    write_lock(pid=dead_pid, port=1)
    assert alive_lock() is None


def test_read_lock_returns_none_on_corrupt_json(isolated_home: Path):
    from daimon.daemon.lock import lock_path, read_lock
    lock_path().parent.mkdir(parents=True, exist_ok=True)
    lock_path().write_text("{not json", encoding="utf-8")
    assert read_lock() is None


def test_read_lock_returns_none_on_missing_field(isolated_home: Path):
    from daimon.daemon.lock import lock_path, read_lock
    lock_path().parent.mkdir(parents=True, exist_ok=True)
    lock_path().write_text(json.dumps({"pid": 1}), encoding="utf-8")
    assert read_lock() is None
