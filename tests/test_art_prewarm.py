"""Tests for ``daimon.play.art_render.prewarm_card_art``.

The pre-warm helper exists to amortise the first-frame stall in the
match render loop: when a battle starts on a fresh install, we already
know the 12 card_ids that will appear in every frame, so we can fan out
the per-card tarball downloads in a thread pool *before* the render
loop iterates milestone-by-milestone. After pre-warm, the loop's
``resolve_card_art`` calls are pure stat hits.

These tests verify the pre-warm contract:

  * Every card_id in the input gets at least one resolve attempt.
  * Duplicate ids are de-duplicated (don't fetch the same tarball twice).
  * Empty input is a no-op (returns empty mapping, doesn't spawn threads).
  * A resolver exception on one card doesn't crash the whole batch.
  * Returned mapping reflects what each card's resolve call returned,
    including ``None`` for cards that failed.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import pytest

import daimon.play.art_render as art_render
from daimon.play.art_render import prewarm_card_art


class TestPrewarmCardArt:
    def test_empty_input_is_noop(self, monkeypatch):
        """No card_ids → empty mapping, no resolver invocations."""
        called: list[str] = []

        def fake_resolve(cid: str, *, skin_slug=None) -> Optional[Path]:
            called.append(cid)
            return None

        monkeypatch.setattr(art_render, "resolve_card_art", fake_resolve)
        result = prewarm_card_art([])
        assert result == {}
        assert called == []

    def test_resolves_every_unique_id(self, monkeypatch):
        called: list[str] = []
        lock = threading.Lock()

        def fake_resolve(cid: str, *, skin_slug=None) -> Optional[Path]:
            with lock:
                called.append(cid)
            return Path(f"/fake/{cid}.png")

        monkeypatch.setattr(art_render, "resolve_card_art", fake_resolve)
        ids = ["scoutling", "iron_boar", "thornling"]
        result = prewarm_card_art(ids, workers=2)
        assert sorted(called) == sorted(ids)
        for cid in ids:
            assert result[cid] == Path(f"/fake/{cid}.png")

    def test_dedupes_repeated_ids(self, monkeypatch):
        """Loadouts can mirror species across sides. Don't fetch twice."""
        called: list[str] = []
        lock = threading.Lock()

        def fake_resolve(cid: str, *, skin_slug=None) -> Optional[Path]:
            with lock:
                called.append(cid)
            return Path(f"/fake/{cid}.png")

        monkeypatch.setattr(art_render, "resolve_card_art", fake_resolve)
        # 3 unique ids, 9 entries total (mirror loadouts).
        ids = ["scoutling", "iron_boar", "thornling"] * 3
        result = prewarm_card_art(ids, workers=4)
        assert sorted(called) == ["iron_boar", "scoutling", "thornling"]
        assert len(result) == 3

    def test_resolver_exception_is_swallowed(self, monkeypatch):
        """A failing resolve on one card must not crash the batch."""
        def fake_resolve(cid: str, *, skin_slug=None) -> Optional[Path]:
            if cid == "broken":
                raise RuntimeError("simulated registry 500")
            return Path(f"/fake/{cid}.png")

        monkeypatch.setattr(art_render, "resolve_card_art", fake_resolve)
        result = prewarm_card_art(
            ["scoutling", "broken", "iron_boar"], workers=2
        )
        # The failing card resolves to None; the others land normally.
        assert result["broken"] is None
        assert result["scoutling"] == Path("/fake/scoutling.png")
        assert result["iron_boar"] == Path("/fake/iron_boar.png")

    def test_returns_none_for_unresolved_card(self, monkeypatch):
        """When the resolver itself returns None, the mapping reflects it."""
        def fake_resolve(cid: str, *, skin_slug=None) -> Optional[Path]:
            return None

        monkeypatch.setattr(art_render, "resolve_card_art", fake_resolve)
        result = prewarm_card_art(["never_cached"])
        assert result == {"never_cached": None}

    def test_workers_clamped_to_at_least_one(self, monkeypatch):
        """workers=0 must not raise — clamped to a usable count."""
        def fake_resolve(cid: str, *, skin_slug=None) -> Optional[Path]:
            return Path(f"/fake/{cid}.png")

        monkeypatch.setattr(art_render, "resolve_card_art", fake_resolve)
        result = prewarm_card_art(["alpha"], workers=0)
        assert result == {"alpha": Path("/fake/alpha.png")}

    def test_concurrent_execution_uses_threadpool(self, monkeypatch):
        """Validate that workers actually run in parallel.

        Without parallelism, 4 cards × 50ms sleep = 200ms minimum.
        With workers>=4, they run concurrently in ~50ms. We allow
        generous wiggle for CI noise but still detect serial behaviour.
        """
        import time

        def slow_resolve(cid: str, *, skin_slug=None) -> Optional[Path]:
            time.sleep(0.05)
            return Path(f"/fake/{cid}.png")

        monkeypatch.setattr(art_render, "resolve_card_art", slow_resolve)
        ids = ["a", "b", "c", "d"]
        t0 = time.monotonic()
        prewarm_card_art(ids, workers=4)
        elapsed = time.monotonic() - t0
        # Serial would be >=0.20s; concurrent should be <0.15s even on
        # a slow CI box. Anything between is suspicious but tolerated.
        assert elapsed < 0.18, (
            f"prewarm took {elapsed:.3f}s for 4 cards × 50ms — looks serial"
        )

    def test_preserves_input_order_in_result(self, monkeypatch):
        """Result mapping iteration order matches dedup-preserving input order."""
        def fake_resolve(cid: str, *, skin_slug=None) -> Optional[Path]:
            return Path(f"/fake/{cid}.png")

        monkeypatch.setattr(art_render, "resolve_card_art", fake_resolve)
        result = prewarm_card_art(["z", "a", "m", "a", "z"])
        assert list(result.keys()) == ["z", "a", "m"]
