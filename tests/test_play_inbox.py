"""Tests for nullpoint.play.inbox — writer + watcher + crash recovery.

Coverage:
  - InboxWriter: atomic write (tmp-then-rename), filename format, payload wrap
  - resolve_inbox_dir: precedence (arg > env > default)
  - _parse: accepts well-formed, rejects malformed (JSON / shape / schema)
  - Drain: existing files replayed in ts-order on watcher start
  - Live: files created after start are dispatched
  - Quarantine: malformed JSON, unknown type, handler exception → .quarantine/
  - Consume: file deleted after successful handle (default)
  - No consume: file stays on disk when consume=False

The observer runs on its own thread — we use poll-until-satisfied with a
bounded timeout instead of sleeps, so tests stay fast and deterministic.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from nullpoint.play.inbox import (
    DEFAULT_INBOX_DIR,
    InboxEvent,
    InboxWatcher,
    InboxWriter,
    QUARANTINE_SUBDIR,
    TMP_SUFFIX,
    _parse,
    _InboxError,
    ensure_inbox,
    resolve_inbox_dir,
)


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

def _wait_for(predicate, timeout: float = 2.0, poll: float = 0.02) -> bool:
    """Poll predicate until true or timeout. Returns whether it became true."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return False


class _CollectingHandler:
    """Test handler that records events it receives. Thread-safe."""

    def __init__(self):
        self.events: list[InboxEvent] = []
        self.lock = threading.Lock()

    def __call__(self, event: InboxEvent) -> None:
        with self.lock:
            self.events.append(event)

    def count(self) -> int:
        with self.lock:
            return len(self.events)


# ---------------------------------------------------------------------------
# resolve_inbox_dir / ensure_inbox
# ---------------------------------------------------------------------------

class TestResolvePath:
    def test_default_is_xdg(self, monkeypatch):
        monkeypatch.delenv("NULLPOINT_INBOX", raising=False)
        assert resolve_inbox_dir() == DEFAULT_INBOX_DIR

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NULLPOINT_INBOX", str(tmp_path))
        assert resolve_inbox_dir() == tmp_path.resolve()

    def test_explicit_arg_wins_over_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NULLPOINT_INBOX", "/env/path/that/should/not/win")
        other = tmp_path / "explicit"
        assert resolve_inbox_dir(other) == other.resolve()

    def test_ensure_inbox_creates_dirs(self, tmp_path):
        inbox = tmp_path / "a" / "b" / "inbox"
        ensure_inbox(inbox)
        assert inbox.is_dir()
        assert (inbox / QUARANTINE_SUBDIR).is_dir()


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

class TestInboxWriter:
    def test_write_creates_well_named_file(self, tmp_path):
        w = InboxWriter(tmp_path)
        path = w.write("match", {"match_id": "f4e2", "hello": 1})
        assert path.exists()
        assert path.parent == tmp_path.resolve()
        assert path.name.startswith("match_")
        assert path.name.endswith(".json")
        # shape: match_<ts>_<shortid>.json
        parts = path.stem.split("_")
        assert len(parts) == 3
        assert parts[0] == "match"
        assert parts[1].isdigit()
        assert len(parts[2]) == 8

    def test_write_payload_contents(self, tmp_path):
        w = InboxWriter(tmp_path)
        path = w.write("pull", {"card_id": "v1.blade_arm"})
        body = json.loads(path.read_text())
        assert body["event_type"] == "pull"
        assert body["schema_version"] == 1
        assert body["card_id"] == "v1.blade_arm"

    def test_write_schema_version_can_be_overridden(self, tmp_path):
        w = InboxWriter(tmp_path)
        path = w.write("match", {"schema_version": 2, "foo": "bar"})
        body = json.loads(path.read_text())
        # Caller override wins over the default (this matters for forward-compat)
        assert body["schema_version"] == 2

    def test_write_is_atomic_no_tmp_left_behind(self, tmp_path):
        w = InboxWriter(tmp_path)
        for _ in range(5):
            w.write("match", {"x": 1})
        # No .tmp files should remain post-rename
        assert not any(p.name.endswith(TMP_SUFFIX) for p in tmp_path.iterdir())

    def test_write_rejects_bad_event_type(self, tmp_path):
        w = InboxWriter(tmp_path)
        with pytest.raises(ValueError, match="alphanumeric"):
            w.write("match-with-dash", {})
        with pytest.raises(ValueError):
            w.write("../../etc/passwd", {})

    def test_write_creates_inbox_if_missing(self, tmp_path):
        nested = tmp_path / "does" / "not" / "exist"
        assert not nested.exists()
        w = InboxWriter(nested)
        w.write("match", {})
        assert nested.is_dir()


# ---------------------------------------------------------------------------
# _parse
# ---------------------------------------------------------------------------

class TestParse:
    def test_accepts_well_formed(self, tmp_path):
        f = tmp_path / "match_1234567_abcd1234.json"
        f.write_text(json.dumps({"event_type": "match", "schema_version": 1, "foo": 42}))
        ev = _parse(f)
        assert ev.event_type == "match"
        assert ev.schema_version == 1
        assert ev.ts_ns == 1234567
        assert ev.payload["foo"] == 42

    def test_rejects_malformed_json(self, tmp_path):
        f = tmp_path / "match_1_a.json"
        f.write_text("this is { not json")
        with pytest.raises(_InboxError, match="json"):
            _parse(f)

    def test_rejects_non_object(self, tmp_path):
        f = tmp_path / "match_1_a.json"
        f.write_text("[1, 2, 3]")
        with pytest.raises(_InboxError, match="object"):
            _parse(f)

    def test_rejects_missing_event_type(self, tmp_path):
        f = tmp_path / "match_1_a.json"
        f.write_text(json.dumps({"no": "event_type"}))
        with pytest.raises(_InboxError, match="event_type"):
            _parse(f)

    def test_rejects_bad_schema_version(self, tmp_path):
        f = tmp_path / "match_1_a.json"
        f.write_text(json.dumps({"event_type": "match", "schema_version": "one"}))
        with pytest.raises(_InboxError, match="schema_version"):
            _parse(f)

    def test_ts_extraction_fallback(self, tmp_path):
        f = tmp_path / "weirdname.json"
        f.write_text(json.dumps({"event_type": "match"}))
        ev = _parse(f)
        assert ev.ts_ns == 0


# ---------------------------------------------------------------------------
# Watcher — drain + live dispatch
# ---------------------------------------------------------------------------

class TestDrain:
    def test_existing_files_replayed_on_start(self, tmp_path):
        w = InboxWriter(tmp_path)
        w.write("match", {"seq": 1})
        w.write("match", {"seq": 2})
        w.write("pull", {"seq": 3})

        collector = _CollectingHandler()
        pull_collector = _CollectingHandler()
        watcher = InboxWatcher(
            inbox_dir=tmp_path,
            handlers={"match": collector, "pull": pull_collector},
        )
        watcher.start()
        try:
            assert _wait_for(lambda: collector.count() == 2 and pull_collector.count() == 1)
        finally:
            watcher.stop()

    def test_drain_order_matches_filename_ordering(self, tmp_path):
        w = InboxWriter(tmp_path)
        paths = [w.write("match", {"seq": i}) for i in range(4)]

        received_order = []

        def handler(ev: InboxEvent) -> None:
            received_order.append(ev.payload["seq"])

        watcher = InboxWatcher(inbox_dir=tmp_path, handlers={"match": handler})
        watcher.start()
        try:
            assert _wait_for(lambda: len(received_order) == 4)
            assert received_order == [0, 1, 2, 3]
        finally:
            watcher.stop()

    def test_replay_on_start_disabled(self, tmp_path):
        w = InboxWriter(tmp_path)
        w.write("match", {"seq": 1})

        collector = _CollectingHandler()
        watcher = InboxWatcher(
            inbox_dir=tmp_path,
            handlers={"match": collector},
            replay_on_start=False,
        )
        watcher.start()
        # give watcher a beat to fully initialize observer thread
        time.sleep(0.1)
        try:
            assert collector.count() == 0
        finally:
            watcher.stop()


class TestLiveDispatch:
    def test_file_created_after_start_is_dispatched(self, tmp_path):
        collector = _CollectingHandler()
        writer = InboxWriter(tmp_path)

        with InboxWatcher(inbox_dir=tmp_path, handlers={"match": collector}) as _:
            # Small settle — ensures observer is registered before write
            time.sleep(0.05)
            writer.write("match", {"seq": 1})
            assert _wait_for(lambda: collector.count() == 1, timeout=3.0)

    def test_multiple_live_events(self, tmp_path):
        collector = _CollectingHandler()
        writer = InboxWriter(tmp_path)

        with InboxWatcher(inbox_dir=tmp_path, handlers={"match": collector}) as _:
            time.sleep(0.05)
            for i in range(5):
                writer.write("match", {"seq": i})
            assert _wait_for(lambda: collector.count() == 5, timeout=3.0)


# ---------------------------------------------------------------------------
# Consume / retain semantics
# ---------------------------------------------------------------------------

class TestConsume:
    def test_consume_true_deletes_file(self, tmp_path):
        writer = InboxWriter(tmp_path)
        path = writer.write("match", {"seq": 1})

        collector = _CollectingHandler()
        watcher = InboxWatcher(inbox_dir=tmp_path, handlers={"match": collector}, consume=True)
        watcher.start()
        try:
            assert _wait_for(lambda: collector.count() == 1)
            assert _wait_for(lambda: not path.exists())
        finally:
            watcher.stop()

    def test_consume_false_preserves_file(self, tmp_path):
        writer = InboxWriter(tmp_path)
        path = writer.write("match", {"seq": 1})

        collector = _CollectingHandler()
        watcher = InboxWatcher(inbox_dir=tmp_path, handlers={"match": collector}, consume=False)
        watcher.start()
        try:
            assert _wait_for(lambda: collector.count() == 1)
            # small settle so we know the unlink path was definitely skipped
            time.sleep(0.1)
            assert path.exists()
        finally:
            watcher.stop()


# ---------------------------------------------------------------------------
# Quarantine — bad JSON, unknown type, handler exception
# ---------------------------------------------------------------------------

class TestQuarantine:
    def test_malformed_json_quarantines(self, tmp_path):
        # Write bad JSON directly (bypass writer)
        bad = tmp_path / "match_1_a.json"
        bad.write_text("{{{ not json }}}")

        watcher = InboxWatcher(inbox_dir=tmp_path, handlers={})
        watcher.start()
        try:
            q_dir = tmp_path / QUARANTINE_SUBDIR
            assert _wait_for(lambda: (q_dir / bad.name).exists())
            err_file = q_dir / (bad.name + ".err.txt")
            assert err_file.exists()
            assert "parse" in err_file.read_text() or "json" in err_file.read_text()
        finally:
            watcher.stop()

    def test_unknown_event_type_quarantines(self, tmp_path):
        writer = InboxWriter(tmp_path)
        writer.write("unknown_type", {"hello": 1})

        watcher = InboxWatcher(inbox_dir=tmp_path, handlers={"match": lambda e: None})
        watcher.start()
        try:
            q_dir = tmp_path / QUARANTINE_SUBDIR
            assert _wait_for(lambda: any(q_dir.iterdir()))
        finally:
            watcher.stop()

    def test_handler_exception_quarantines_and_doesnt_crash(self, tmp_path):
        writer = InboxWriter(tmp_path)
        writer.write("match", {"seq": 1})
        writer.write("match", {"seq": 2})

        seen = []

        def flaky(ev: InboxEvent) -> None:
            seen.append(ev.payload["seq"])
            if ev.payload["seq"] == 1:
                raise RuntimeError("simulated handler failure")

        watcher = InboxWatcher(inbox_dir=tmp_path, handlers={"match": flaky})
        watcher.start()
        try:
            assert _wait_for(lambda: len(seen) == 2)
            q_dir = tmp_path / QUARANTINE_SUBDIR
            # Exactly one quarantined (the seq=1 one)
            q_files = [p for p in q_dir.iterdir() if p.name.endswith(".json")]
            assert len(q_files) == 1
            err_files = [p for p in q_dir.iterdir() if p.name.endswith(".err.txt")]
            assert len(err_files) == 1
            assert "handler" in err_files[0].read_text()
        finally:
            watcher.stop()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_cannot_start_twice(self, tmp_path):
        watcher = InboxWatcher(inbox_dir=tmp_path, handlers={})
        watcher.start()
        try:
            with pytest.raises(RuntimeError, match="already started"):
                watcher.start()
        finally:
            watcher.stop()

    def test_stop_is_idempotent(self, tmp_path):
        watcher = InboxWatcher(inbox_dir=tmp_path, handlers={})
        watcher.start()
        watcher.stop()
        watcher.stop()  # should not raise

    def test_context_manager(self, tmp_path):
        with InboxWatcher(inbox_dir=tmp_path, handlers={}) as watcher:
            assert watcher._observer is not None
        assert watcher._observer is None


# ---------------------------------------------------------------------------
# Writer+watcher round-trip (integration)
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_writer_and_watcher_agree_on_payload(self, tmp_path):
        collector = _CollectingHandler()
        writer = InboxWriter(tmp_path)

        with InboxWatcher(inbox_dir=tmp_path, handlers={"match": collector}) as _:
            time.sleep(0.05)
            writer.write("match", {"match_id": "abc123", "nested": {"ok": True}})
            assert _wait_for(lambda: collector.count() == 1, timeout=3.0)

        ev = collector.events[0]
        assert ev.event_type == "match"
        assert ev.payload["match_id"] == "abc123"
        assert ev.payload["nested"]["ok"] is True
        assert ev.schema_version == 1
