"""Tests for daimon.play.state — the agent → game-terminal state-file protocol.

Covers the locked invariants of the single-state-file scheme (2026-04-21):
  - write_state is atomic (no partial reads observable, even under contention)
  - write_state rejects unknown views and non-dict payloads at the earliest seam
  - read_state returns None for a missing file (fresh install)
  - read_state raises ValueError (not KeyError / JSONDecodeError) for malformed
    content, so callers have ONE exception to handle
  - round-trip: write then read returns the same view/data/id
  - dedupe contract: should_render(state, last_id) returns True iff ids differ
  - path resolution: explicit arg > env > default
  - last-write-wins semantics: rapid successive writes preserve only the last
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from daimon.play.state import (
    DEFAULT_STATE_PATH,
    KNOWN_VIEWS,
    SCHEMA_VERSION,
    TMP_SUFFIX,
    GameState,
    new_id,
    read_state,
    resolve_state_path,
    should_render,
    write_state,
)


# ---------------------------------------------------------------------------
# resolve_state_path
# ---------------------------------------------------------------------------

class TestResolveStatePath:
    def test_default_is_xdg(self, monkeypatch):
        monkeypatch.delenv("DAIMON_STATE", raising=False)
        assert resolve_state_path() == DEFAULT_STATE_PATH

    def test_env_override(self, monkeypatch, tmp_path):
        target = tmp_path / "state.json"
        monkeypatch.setenv("DAIMON_STATE", str(target))
        assert resolve_state_path() == target.resolve()

    def test_explicit_arg_wins_over_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DAIMON_STATE", "/env/path/that/must/not/win.json")
        explicit = tmp_path / "explicit.json"
        assert resolve_state_path(explicit) == explicit.resolve()

    def test_accepts_string(self, tmp_path):
        p = tmp_path / "state.json"
        assert resolve_state_path(str(p)) == p.resolve()

    def test_expanduser(self, monkeypatch):
        monkeypatch.setenv("DAIMON_STATE", "~/test_state.json")
        assert "~" not in str(resolve_state_path())


# ---------------------------------------------------------------------------
# new_id
# ---------------------------------------------------------------------------

class TestNewId:
    def test_unique(self):
        assert new_id() != new_id()

    def test_prefix_applied(self):
        assert new_id("match").startswith("match_")

    def test_no_prefix(self):
        # No underscore when prefix empty
        assert "_" not in new_id("")

    def test_short_form(self):
        # 8 hex chars after the optional prefix
        raw = new_id()
        assert len(raw) == 8


# ---------------------------------------------------------------------------
# write_state — happy path and error cases
# ---------------------------------------------------------------------------

class TestWriteState:
    def test_writes_file_at_given_path(self, tmp_path):
        p = tmp_path / "state.json"
        write_state("match", {"foo": "bar"}, state_path=p)
        assert p.exists()

    def test_written_content_is_valid_json(self, tmp_path):
        p = tmp_path / "state.json"
        write_state("pull", {"rarity": "legendary"}, state_path=p)
        body = json.loads(p.read_text(encoding="utf-8"))
        assert body["view"] == "pull"
        assert body["data"]["rarity"] == "legendary"
        assert body["schema_version"] == SCHEMA_VERSION
        assert isinstance(body["ts_ns"], int)
        assert body["ts_ns"] > 0
        assert isinstance(body["id"], str)
        assert len(body["id"]) > 0

    def test_returns_gamestate(self, tmp_path):
        p = tmp_path / "state.json"
        gs = write_state("inspect", {"card_id": "x"}, state_path=p)
        assert isinstance(gs, GameState)
        assert gs.view == "inspect"
        assert gs.data == {"card_id": "x"}
        assert gs.schema_version == SCHEMA_VERSION

    def test_explicit_id_preserved(self, tmp_path):
        p = tmp_path / "state.json"
        gs = write_state("match", {}, id="my_explicit_id", state_path=p)
        assert gs.id == "my_explicit_id"
        body = json.loads(p.read_text(encoding="utf-8"))
        assert body["id"] == "my_explicit_id"

    def test_auto_id_when_omitted(self, tmp_path):
        p = tmp_path / "state.json"
        gs = write_state("loadout", {}, state_path=p)
        assert gs.id.startswith("loadout_")

    def test_creates_parent_dir(self, tmp_path):
        p = tmp_path / "deeply" / "nested" / "state.json"
        write_state("idle", {}, state_path=p)
        assert p.exists()

    def test_rejects_unknown_view(self, tmp_path):
        p = tmp_path / "state.json"
        with pytest.raises(ValueError, match="unknown view"):
            write_state("not_a_real_view", {}, state_path=p)
        assert not p.exists()  # no partial state written

    def test_rejects_non_dict_data(self, tmp_path):
        p = tmp_path / "state.json"
        with pytest.raises(ValueError, match="data must be a dict"):
            write_state("match", ["not", "a", "dict"], state_path=p)  # type: ignore[arg-type]
        assert not p.exists()

    def test_tmp_file_not_left_behind(self, tmp_path):
        p = tmp_path / "state.json"
        write_state("match", {"a": 1}, state_path=p)
        tmp = p.with_suffix(p.suffix + TMP_SUFFIX)
        assert not tmp.exists()

    def test_overwrite_replaces_content(self, tmp_path):
        p = tmp_path / "state.json"
        write_state("match", {"n": 1}, id="id_1", state_path=p)
        write_state("pull",  {"n": 2}, id="id_2", state_path=p)
        body = json.loads(p.read_text(encoding="utf-8"))
        assert body["view"] == "pull"
        assert body["data"] == {"n": 2}
        assert body["id"] == "id_2"


# ---------------------------------------------------------------------------
# read_state — happy path and malformed input
# ---------------------------------------------------------------------------

class TestReadState:
    def test_none_when_missing(self, tmp_path):
        assert read_state(tmp_path / "nope.json") is None

    def test_round_trip(self, tmp_path):
        p = tmp_path / "state.json"
        write_state("collection", {"count": 42}, id="round_trip_1", state_path=p)
        got = read_state(p)
        assert got is not None
        assert got.view == "collection"
        assert got.data == {"count": 42}
        assert got.id == "round_trip_1"
        assert got.schema_version == SCHEMA_VERSION

    def test_malformed_json_raises_value_error(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text("{this is not { json")
        with pytest.raises(ValueError, match="not JSON"):
            read_state(p)

    def test_top_level_not_object_raises(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text('["a", "b"]')
        with pytest.raises(ValueError, match="top-level must be an object"):
            read_state(p)

    def test_missing_view_raises(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"data": {}, "id": "x", "ts_ns": 0}))
        with pytest.raises(ValueError, match="missing 'view'"):
            read_state(p)

    def test_unknown_view_raises(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({
            "view": "mystery_view",
            "data": {},
            "id": "x",
            "ts_ns": 1,
        }))
        with pytest.raises(ValueError, match="unknown view"):
            read_state(p)

    def test_bad_data_type_raises(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({
            "view": "match",
            "data": "not an object",
            "id": "x",
            "ts_ns": 1,
        }))
        with pytest.raises(ValueError, match="'data' must be an object"):
            read_state(p)

    def test_missing_id_raises(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({
            "view": "match",
            "data": {},
            "ts_ns": 1,
        }))
        with pytest.raises(ValueError, match="missing 'id'"):
            read_state(p)

    def test_bad_schema_version_raises(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({
            "view": "match",
            "data": {},
            "id": "x",
            "ts_ns": 1,
            "schema_version": 0,
        }))
        with pytest.raises(ValueError, match="schema_version"):
            read_state(p)


# ---------------------------------------------------------------------------
# Dedupe contract
# ---------------------------------------------------------------------------

class TestShouldRender:
    def test_none_state_returns_false(self):
        assert should_render(None, None) is False
        assert should_render(None, "last_id") is False

    def test_new_state_returns_true(self):
        gs = GameState(view="match", data={}, id="new", ts_ns=0)
        assert should_render(gs, None) is True
        assert should_render(gs, "different_id") is True

    def test_same_id_returns_false(self):
        gs = GameState(view="match", data={}, id="same", ts_ns=0)
        assert should_render(gs, "same") is False

    def test_different_ids_return_true(self):
        first  = GameState(view="match", data={}, id="id_1", ts_ns=0)
        second = GameState(view="match", data={}, id="id_2", ts_ns=0)
        assert should_render(first,  None)  is True
        assert should_render(second, first.id) is True


# ---------------------------------------------------------------------------
# Concurrency — no partial reads, last-write-wins
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_no_partial_reads_under_contention(self, tmp_path):
        """Reader thread never observes a half-written state file.

        We hammer the file from a writer thread while a reader thread
        continuously reads + parses. Every successful read must yield a
        well-formed GameState — never a ValueError — because ``os.replace``
        is atomic. A partial write would manifest as a JSON parse error.
        """
        p = tmp_path / "state.json"

        # Prime the file so the reader always has something to read.
        write_state("match", {"round": 0}, state_path=p)

        errors: list[Exception] = []
        writer_done = threading.Event()
        N_ITERS = 200

        def writer():
            try:
                for i in range(N_ITERS):
                    write_state(
                        "match",
                        {
                            "round": i,
                            # Make the payload big enough that a non-atomic
                            # rename would leave a visible partial file.
                            "filler": "x" * 4096,
                        },
                        id=f"iter_{i}",
                        state_path=p,
                    )
            except Exception as e:
                errors.append(e)
            finally:
                writer_done.set()

        def reader():
            while not writer_done.is_set():
                try:
                    gs = read_state(p)
                    if gs is None:
                        continue
                    # Schema invariant must hold on every read
                    assert gs.view in KNOWN_VIEWS
                    assert isinstance(gs.data, dict)
                except Exception as e:
                    errors.append(e)

        t_w = threading.Thread(target=writer)
        t_r = threading.Thread(target=reader)
        t_r.start()
        t_w.start()
        t_w.join(timeout=30)
        t_r.join(timeout=5)

        assert not errors, f"concurrent access observed: {errors[:3]}"

    def test_last_write_wins(self, tmp_path):
        """Rapid-successive writes preserve only the final state."""
        p = tmp_path / "state.json"
        for i in range(50):
            write_state("match", {"i": i}, id=f"id_{i}", state_path=p)

        got = read_state(p)
        assert got is not None
        assert got.id == "id_49"
        assert got.data == {"i": 49}


# ---------------------------------------------------------------------------
# Sanity — every known view is writable
# ---------------------------------------------------------------------------

class TestAllViewsWritable:
    @pytest.mark.parametrize("view", sorted(KNOWN_VIEWS))
    def test_each_known_view_roundtrips(self, view, tmp_path):
        p = tmp_path / "state.json"
        write_state(view, {"marker": view}, state_path=p)
        got = read_state(p)
        assert got is not None
        assert got.view == view
        assert got.data == {"marker": view}
