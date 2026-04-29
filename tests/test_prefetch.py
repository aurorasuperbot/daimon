"""Tests for the background prefetcher (``daimon.update.prefetch``)."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Optional

import pytest

from daimon.update import fetcher
from daimon.update.fetcher import ArtUpdateError
from daimon.update.manifest import (
    SCHEMA_VERSION,
    CardEntry,
    Manifest,
    write_manifest,
)
from daimon.update.paths import art_pack_dir, prefetch_state_path
from daimon.update.prefetch import (
    PREFETCH_LOCK_NAME,
    PrefetchState,
    _existing_prefetch_alive,
    _prefetch_lock_path,
    acquire_prefetch_lock,
    read_state,
    release_prefetch_lock,
    run_prefetch,
    write_state as write_prefetch_state,
)


# ---------------------------------------------------------------------------
# Fixtures (mirrors test_lazy_art.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def art_dir(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("DAIMON_ART_DIR", str(tmp_path))
    monkeypatch.delenv("DAIMON_NO_AUTO_UPDATE", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return tmp_path


class FakeHTTPResponse:
    def __init__(self, payload: bytes, headers: Optional[dict] = None):
        self._buf = io.BytesIO(payload)
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n) if n > 0 else self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._buf.close()
        return False


def _build_card_tarball(card_id: str) -> tuple[bytes, str]:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        png = b"\x89PNG\r\n\x1a\n" + b"X" * 16
        info = tarfile.TarInfo(name="base.png")
        info.size = len(png)
        tf.addfile(info, io.BytesIO(png))
    raw = buf.getvalue()
    return raw, hashlib.sha256(raw).hexdigest()


def _install_manifest(card_ids: list[str], pack_version: str = "art-v1.0"):
    """Install a manifest with deterministic-sha cards and return (manifest, payloads)."""
    cards: dict[str, CardEntry] = {}
    payloads: dict[str, bytes] = {}
    for cid in card_ids:
        raw, digest = _build_card_tarball(cid)
        payloads[cid] = raw
        cards[cid] = CardEntry(
            asset_name=f"card_{cid}.tar.gz",
            sha256=digest,
            size_bytes=len(raw),
        )
    manifest = Manifest(
        schema_version=SCHEMA_VERSION,
        pack_version=pack_version,
        pack_name="v1_alpha",
        asset_base_url="https://example.invalid/dl/",
        starter_card_ids=tuple(card_ids[:1]),
        cards=cards,
    )
    write_manifest(manifest)
    return manifest, payloads


# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------

class TestPrefetchState:
    def test_round_trip(self):
        s = PrefetchState(
            manifest_version="art-v1.0",
            pack_name="v1_alpha",
            started_at=1714050000,
            completed_at=1714050100,
            total=10,
            fetched_count=8,
            skipped_count=1,
            failed=[["card_x", "boom"]],
        )
        roundtrip = PrefetchState.from_dict(s.to_dict())
        assert roundtrip == s
        assert roundtrip.failed_count == 1
        assert roundtrip.is_complete

    def test_failed_count(self):
        s = PrefetchState(
            manifest_version="art-v1.0",
            pack_name="v1_alpha",
            started_at=0,
            failed=[["a", "x"], ["b", "y"]],
        )
        assert s.failed_count == 2
        assert not s.is_complete

    def test_from_dict_skips_malformed_failure_entries(self):
        s = PrefetchState.from_dict({
            "manifest_version": "art-v1.0",
            "pack_name": "v1_alpha",
            "started_at": 0,
            "failed": [["good", "ok"], "garbage", ["x"], ["a", "b", "c"]],
        })
        assert s.failed == [["good", "ok"]]


class TestStateIO:
    def test_read_returns_none_when_absent(self, art_dir: Path):
        assert read_state() is None

    def test_write_then_read_round_trip(self, art_dir: Path):
        s = PrefetchState(
            manifest_version="art-v1.0",
            pack_name="v1_alpha",
            started_at=42,
            total=5,
            fetched_count=2,
        )
        write_prefetch_state(s)
        assert prefetch_state_path().is_file()
        loaded = read_state()
        assert loaded is not None
        assert loaded == s

    def test_read_returns_none_on_corrupt(self, art_dir: Path):
        prefetch_state_path().parent.mkdir(parents=True, exist_ok=True)
        prefetch_state_path().write_text("not json")
        assert read_state() is None


# ---------------------------------------------------------------------------
# run_prefetch — happy path / mixed / failure / opt-out
# ---------------------------------------------------------------------------

class TestRunPrefetch:
    def test_no_manifest_raises(self, art_dir: Path):
        with pytest.raises(ArtUpdateError, match="no manifest"):
            run_prefetch(workers=1, log_stream=io.StringIO())

    def test_happy_path_fetches_all(self, art_dir: Path, monkeypatch):
        manifest, payloads = _install_manifest(["alpha", "beta", "gamma"])

        def fake_http_open(url: str, *, octet_stream: bool = False):
            asset_name = url.rsplit("/", 1)[-1]
            cid = asset_name.removeprefix("card_").removesuffix(".tar.gz")
            return FakeHTTPResponse(payloads[cid])

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)
        log = io.StringIO()
        state = run_prefetch(workers=2, log_stream=log)

        assert state.fetched_count == 3
        assert state.skipped_count == 0
        assert state.failed == []
        assert state.is_complete

        for cid in ("alpha", "beta", "gamma"):
            assert (art_pack_dir() / cid / "base.png").is_file()

        # State persisted.
        loaded = read_state()
        assert loaded == state

    def test_skips_already_cached(self, art_dir: Path, monkeypatch):
        manifest, payloads = _install_manifest(["alpha", "beta"])

        # Pre-cache alpha.
        cached = art_pack_dir() / "alpha"
        cached.mkdir(parents=True)
        (cached / "base.png").write_bytes(b"\x89PNG")

        # _http_open is allowed but should only be called for beta.
        called: list[str] = []

        def fake_http_open(url: str, *, octet_stream: bool = False):
            asset_name = url.rsplit("/", 1)[-1]
            cid = asset_name.removeprefix("card_").removesuffix(".tar.gz")
            called.append(cid)
            return FakeHTTPResponse(payloads[cid])

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)
        state = run_prefetch(workers=1, log_stream=io.StringIO())

        assert state.fetched_count == 1
        assert state.skipped_count == 1
        assert called == ["beta"]
        assert state.is_complete

    def test_skip_card_ids_param(self, art_dir: Path, monkeypatch):
        manifest, payloads = _install_manifest(["alpha", "beta", "gamma"])

        called: list[str] = []

        def fake_http_open(url: str, *, octet_stream: bool = False):
            asset_name = url.rsplit("/", 1)[-1]
            cid = asset_name.removeprefix("card_").removesuffix(".tar.gz")
            called.append(cid)
            return FakeHTTPResponse(payloads[cid])

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)
        state = run_prefetch(
            workers=1, log_stream=io.StringIO(),
            skip_card_ids=("alpha",),
        )

        assert state.fetched_count == 2
        assert state.skipped_count == 1
        assert "alpha" not in called

    def test_records_failures_and_continues(self, art_dir: Path, monkeypatch):
        manifest, payloads = _install_manifest(["alpha", "beta"])

        # alpha responds with bad bytes that fail sha256; beta is fine.
        def fake_http_open(url: str, *, octet_stream: bool = False):
            asset_name = url.rsplit("/", 1)[-1]
            cid = asset_name.removeprefix("card_").removesuffix(".tar.gz")
            if cid == "alpha":
                return FakeHTTPResponse(b"corrupt bytes that won't sha-match")
            return FakeHTTPResponse(payloads[cid])

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)
        state = run_prefetch(workers=1, log_stream=io.StringIO())

        assert state.fetched_count == 1
        assert state.failed_count == 1
        assert state.failed[0][0] == "alpha"
        assert "sha256" in state.failed[0][1]
        # beta still landed.
        assert (art_pack_dir() / "beta" / "base.png").is_file()
        # alpha did not.
        assert not (art_pack_dir() / "alpha").exists()

    def test_idempotent_when_all_cached(self, art_dir: Path, monkeypatch):
        manifest, payloads = _install_manifest(["alpha"])

        # Pre-cache.
        cached = art_pack_dir() / "alpha"
        cached.mkdir(parents=True)
        (cached / "base.png").write_bytes(b"\x89PNG")

        def boom(*_a, **_kw):
            pytest.fail("network called when nothing to fetch")

        monkeypatch.setattr(fetcher, "_http_open", boom)
        state = run_prefetch(workers=1, log_stream=io.StringIO())

        assert state.fetched_count == 0
        assert state.skipped_count == 1
        assert state.is_complete

    def test_logs_to_stream(self, art_dir: Path, monkeypatch):
        manifest, payloads = _install_manifest(["alpha"])
        monkeypatch.setattr(
            fetcher, "_http_open",
            lambda url, *, octet_stream=False: FakeHTTPResponse(payloads["alpha"]),
        )
        log = io.StringIO()
        run_prefetch(workers=1, log_stream=log)
        log_text = log.getvalue()
        assert "starting" in log_text
        assert "ok   alpha" in log_text
        assert "done" in log_text


# ---------------------------------------------------------------------------
# spawn_prefetch_subprocess — opt-out behaviour
# ---------------------------------------------------------------------------

class TestSpawnPrefetchSubprocess:
    def test_opt_out_returns_none_without_spawning(self, art_dir: Path, monkeypatch):
        from daimon.update.prefetch import spawn_prefetch_subprocess
        monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "1")

        def boom(*_a, **_kw):
            pytest.fail("Popen called when opted out")

        import subprocess as _sp
        monkeypatch.setattr(_sp, "Popen", boom)
        result = spawn_prefetch_subprocess()
        assert result is None

    def test_existing_alive_lock_blocks_spawn(self, art_dir: Path, monkeypatch):
        """If a live prefetcher already holds the lock, don't fork a second one."""
        from daimon.update.prefetch import spawn_prefetch_subprocess

        # Acquire the lock as if another prefetcher were running. Our own
        # PID is alive (we're the test process), so _existing_prefetch_alive
        # will report True.
        fd = acquire_prefetch_lock()
        assert fd is not None
        try:
            def boom(*_a, **_kw):
                pytest.fail("Popen called while singleton lock is held")

            import subprocess as _sp
            monkeypatch.setattr(_sp, "Popen", boom)
            result = spawn_prefetch_subprocess()
            assert result is None
        finally:
            release_prefetch_lock(fd)


# ---------------------------------------------------------------------------
# Singleton lock — race + stale recovery
# ---------------------------------------------------------------------------

class TestPrefetchSingletonLock:
    def test_acquire_then_second_acquire_returns_none(self, art_dir: Path):
        first = acquire_prefetch_lock()
        assert first is not None
        try:
            second = acquire_prefetch_lock()
            assert second is None, (
                "second concurrent acquire must fail — O_EXCL pidfile contract"
            )
        finally:
            release_prefetch_lock(first)

    def test_release_then_reacquire_succeeds(self, art_dir: Path):
        first = acquire_prefetch_lock()
        assert first is not None
        release_prefetch_lock(first)

        second = acquire_prefetch_lock()
        assert second is not None, (
            "after release, the lockfile must be gone and a new acquire must win"
        )
        release_prefetch_lock(second)

    def test_stale_lock_with_dead_pid_is_stolen(self, art_dir: Path):
        """A lockfile referencing a dead PID must be silently recovered.

        This is the SIGKILL/OOM/power-loss case: a previous prefetcher
        died without unlinking its lockfile. Without stale recovery the
        cache would be permanently un-prefetchable until a human ran
        `rm`.
        """
        # Find a PID that is not in use. PID 1 is always alive (init);
        # we want a definitely-dead one. Use the same liveness check the
        # production code uses — handles Windows + POSIX uniformly
        # (Windows raises plain OSError for unknown PIDs, not
        # ProcessLookupError, so this loop works on both).
        import os
        from daimon.update.prefetch import _pid_alive

        dead_pid = None
        for candidate in range(2_000_000, 2_100_000):
            if not _pid_alive(candidate):
                dead_pid = candidate
                break
        assert dead_pid is not None, "couldn't find a definitely-dead PID for the test"

        # Plant a stale lockfile.
        lock_path = _prefetch_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(f"{dead_pid}\n", encoding="utf-8")
        assert _existing_prefetch_alive() is False

        # Acquire should recover (steal) the orphan lock.
        fd = acquire_prefetch_lock()
        assert fd is not None, "stale lock with dead PID must be stolen"
        try:
            # The new lockfile holds *our* PID now, not the dead one.
            contents = lock_path.read_text(encoding="utf-8").strip()
            assert int(contents.splitlines()[0]) == os.getpid()
        finally:
            release_prefetch_lock(fd)

    def test_existing_prefetch_alive_returns_false_when_no_lock(self, art_dir: Path):
        assert _existing_prefetch_alive() is False

    def test_existing_prefetch_alive_returns_true_for_live_owner(self, art_dir: Path):
        fd = acquire_prefetch_lock()
        assert fd is not None
        try:
            assert _existing_prefetch_alive() is True
        finally:
            release_prefetch_lock(fd)

    def test_run_prefetch_bails_cleanly_when_lock_held(self, art_dir: Path, monkeypatch):
        """Contended-lock case: must return a no-op state, not raise, and not network."""
        manifest, payloads = _install_manifest(["alpha", "beta"])

        def boom(*_a, **_kw):
            pytest.fail("network called even though singleton lock was held")

        monkeypatch.setattr(fetcher, "_http_open", boom)

        # Hold the lock as a fake "other prefetcher".
        fd = acquire_prefetch_lock()
        assert fd is not None
        try:
            log = io.StringIO()
            state = run_prefetch(workers=2, log_stream=log)
            assert state.fetched_count == 0
            assert state.skipped_count == 0
            assert state.total == 0
            assert state.is_complete  # started_at == completed_at
            assert "another prefetcher" in log.getvalue()
        finally:
            release_prefetch_lock(fd)

    def test_run_prefetch_releases_lock_on_success(self, art_dir: Path, monkeypatch):
        manifest, payloads = _install_manifest(["alpha"])
        monkeypatch.setattr(
            fetcher, "_http_open",
            lambda url, *, octet_stream=False: FakeHTTPResponse(payloads["alpha"]),
        )
        run_prefetch(workers=1, log_stream=io.StringIO())

        # Lock must be gone (file unlinked) so the next prefetch can run.
        assert not _prefetch_lock_path().exists(), (
            "run_prefetch must release the singleton lock on clean exit"
        )

    def test_run_prefetch_releases_lock_on_failure(self, art_dir: Path):
        """No manifest → ArtUpdateError. Lock must still be released."""
        with pytest.raises(ArtUpdateError):
            run_prefetch(workers=1, log_stream=io.StringIO())
        assert not _prefetch_lock_path().exists(), (
            "run_prefetch must release the singleton lock even when it raises"
        )
