"""Tests for daimon.play.terminal — dispatcher + watcher.

Covers the A.2 contract (locked 2026-04-21):
  - DEFAULT_HANDLERS covers every KNOWN_VIEWS entry (structural invariant)
  - dispatch_once reads state, dedupes by id, routes to the right handler
  - unknown-view handling: returns action='error' without crashing
  - missing-state handling: returns action='skipped_no_state'
  - malformed-state handling: returns action='error' (caught at read_state)
  - handler exception isolation: failure doesn't advance last_rendered_id
  - persistent dedupe across restarts via sidecar file
  - match renderer produces a real PNG under renders_dir
  - placeholder renderers produce a PNG named after their view
  - live watcher path: write_state → dispatch fires → render lands on disk
  - path resolution: explicit arg > env > default

The watcher runs on its own thread — we use poll-until-satisfied with a
bounded timeout, matching the pattern used elsewhere in the suite.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from daimon.play.state import (
    GameState,
    KNOWN_VIEWS,
    write_state,
)
from daimon.play.terminal import (
    ANIMATION_FRAME_INTERVAL_MS,
    DEFAULT_HANDLERS,
    DEFAULT_RENDERS_DIR,
    DispatchResult,
    GameTerminal,
    LAST_ID_SIDECAR_NAME,
    MANIFEST_SCHEMA_VERSION,
    _frame_milestones,
    _is_match_payload,
    _read_last_id,
    _render_match_animated,
    _render_placeholder_png,
    _write_last_id,
    render_match,
    resolve_renders_dir,
)


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

def _wait_for(predicate, timeout: float = 3.0, poll: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return False


@pytest.fixture
def iso_paths(tmp_path, monkeypatch):
    """Isolate state + renders under tmp_path via env vars.

    Returns (state_path, renders_dir).
    """
    state_path = tmp_path / "state.json"
    renders_dir = tmp_path / "renders"
    monkeypatch.setenv("DAIMON_STATE", str(state_path))
    monkeypatch.setenv("DAIMON_RENDERS", str(renders_dir))
    return state_path, renders_dir


# ---------------------------------------------------------------------------
# Handler table invariant
# ---------------------------------------------------------------------------

class TestHandlerTableInvariant:
    def test_covers_every_known_view(self):
        missing = KNOWN_VIEWS - set(DEFAULT_HANDLERS.keys())
        assert missing == set(), f"handlers missing for: {missing}"

    def test_no_extra_handlers(self):
        extras = set(DEFAULT_HANDLERS.keys()) - KNOWN_VIEWS
        assert extras == set(), f"handlers present for unknown views: {extras}"

    def test_match_handler_is_the_real_renderer(self):
        # A.2 promises one real renderer — match. The others are placeholders.
        assert DEFAULT_HANDLERS["match"] is render_match


# ---------------------------------------------------------------------------
# resolve_renders_dir
# ---------------------------------------------------------------------------

class TestResolveRendersDir:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("DAIMON_RENDERS", raising=False)
        assert resolve_renders_dir() == DEFAULT_RENDERS_DIR

    def test_env_override(self, monkeypatch, tmp_path):
        target = tmp_path / "r"
        monkeypatch.setenv("DAIMON_RENDERS", str(target))
        assert resolve_renders_dir() == target.resolve()

    def test_explicit_arg_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DAIMON_RENDERS", "/env/must/not/win")
        explicit = tmp_path / "explicit"
        assert resolve_renders_dir(explicit) == explicit.resolve()


# ---------------------------------------------------------------------------
# Sidecar persistence
# ---------------------------------------------------------------------------

class TestSidecar:
    def test_read_missing_is_none(self, tmp_path):
        assert _read_last_id(tmp_path) is None

    def test_roundtrip(self, tmp_path):
        _write_last_id(tmp_path, "abc123")
        assert _read_last_id(tmp_path) == "abc123"

    def test_overwrites(self, tmp_path):
        _write_last_id(tmp_path, "first")
        _write_last_id(tmp_path, "second")
        assert _read_last_id(tmp_path) == "second"

    def test_empty_file_is_none(self, tmp_path):
        (tmp_path / LAST_ID_SIDECAR_NAME).write_text("")
        assert _read_last_id(tmp_path) is None

    def test_sidecar_is_atomic(self, tmp_path):
        # Writer uses tmp + os.replace — verify no tmp file lingers.
        _write_last_id(tmp_path, "xyz")
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == LAST_ID_SIDECAR_NAME


# ---------------------------------------------------------------------------
# dispatch_once — the core routing logic
# ---------------------------------------------------------------------------

class TestDispatchOnce:
    def test_no_state_yields_skipped_no_state(self, iso_paths):
        state_path, _ = iso_paths
        term = GameTerminal()
        result = term.dispatch_once()
        assert result.action == "skipped_no_state"
        assert result.state is None
        assert not state_path.exists()

    def test_malformed_state_yields_error(self, iso_paths):
        state_path, _ = iso_paths
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{not json")

        term = GameTerminal()
        result = term.dispatch_once()
        assert result.action == "error"
        assert result.error is not None
        # Error must not advance last_rendered_id.
        assert term.last_rendered_id is None

    def test_match_renders_and_advances(self, iso_paths):
        _, renders_dir = iso_paths
        gs = write_state("match", _sample_match_data(), id="match_alpha")

        term = GameTerminal()
        result = term.dispatch_once()
        assert result.action == "rendered"
        assert result.state is not None
        assert result.state.id == "match_alpha"
        assert result.out_path is not None
        assert result.out_path.exists()
        assert result.out_path.parent == renders_dir
        assert term.last_rendered_id == "match_alpha"

    def test_dedupe_same_id_skipped(self, iso_paths):
        write_state("match", _sample_match_data(), id="match_beta")
        term = GameTerminal()
        first = term.dispatch_once()
        assert first.action == "rendered"

        second = term.dispatch_once()
        assert second.action == "skipped_dedupe"
        assert term.last_rendered_id == "match_beta"

    def test_different_id_renders_again(self, iso_paths):
        write_state("match", _sample_match_data(), id="id_1")
        term = GameTerminal()
        assert term.dispatch_once().action == "rendered"

        write_state("match", _sample_match_data(), id="id_2")
        result = term.dispatch_once()
        assert result.action == "rendered"
        assert term.last_rendered_id == "id_2"

    def test_placeholder_view_renders_distinct_png(self, iso_paths):
        _, renders_dir = iso_paths
        write_state("pull", {"card_id": "x"}, id="pull_42")
        term = GameTerminal()
        result = term.dispatch_once()
        assert result.action == "rendered"
        assert result.out_path is not None
        assert result.out_path.name == "pull_pull_42.png"
        assert result.out_path.exists()

    def test_every_known_view_dispatches(self, iso_paths):
        """Smoke test — each known view actually routes to a handler."""
        for view in sorted(KNOWN_VIEWS):
            state_id = f"{view}_smoke"
            write_state(view, {"marker": view}, id=state_id)
            term = GameTerminal()
            result = term.dispatch_once()
            assert result.action == "rendered", f"{view} failed: {result}"
            assert result.out_path is not None
            assert result.out_path.exists(), f"{view} produced no file"

    def test_missing_handler_is_error_not_crash(self, iso_paths):
        _, renders_dir = iso_paths
        write_state("match", _sample_match_data(), id="orphan")

        # Construct a terminal with an EMPTY handler table — valid view
        # with no dispatch target. Must return action='error' cleanly.
        term = GameTerminal(handlers={})
        result = term.dispatch_once()
        assert result.action == "error"
        assert "no handler" in (result.error or "")
        assert term.last_rendered_id is None   # no advance on error

    def test_handler_exception_does_not_advance(self, iso_paths):
        def _raising(state: GameState, renders_dir: Path):
            raise RuntimeError("intentional")

        write_state("match", _sample_match_data(), id="crash_test")
        term = GameTerminal(handlers={"match": _raising})
        result = term.dispatch_once()
        assert result.action == "error"
        assert "RuntimeError" in (result.error or "")
        assert term.last_rendered_id is None


# ---------------------------------------------------------------------------
# Dedupe-across-restart — the A.2 contract
# ---------------------------------------------------------------------------

class TestDedupeAcrossRestart:
    def test_restart_does_not_rerender_same_id(self, iso_paths):
        write_state("match", _sample_match_data(), id="persist_1")

        # First "boot" renders.
        term_a = GameTerminal()
        assert term_a.dispatch_once().action == "rendered"

        # Second "boot" — fresh GameTerminal, same on-disk state.
        term_b = GameTerminal()
        assert term_b.last_rendered_id == "persist_1"
        result = term_b.dispatch_once()
        assert result.action == "skipped_dedupe"

    def test_restart_renders_when_id_changed(self, iso_paths):
        write_state("match", _sample_match_data(), id="v1")
        term_a = GameTerminal()
        term_a.dispatch_once()

        # New state arrives between boots.
        write_state("match", _sample_match_data(), id="v2")

        term_b = GameTerminal()
        # Sidecar carried forward the v1 id; v2 is new and should render.
        result = term_b.dispatch_once()
        assert result.action == "rendered"
        assert term_b.last_rendered_id == "v2"

    def test_corrupt_sidecar_is_survivable(self, iso_paths):
        """An unreadable sidecar should not prevent rendering."""
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        # Write garbage bytes so strip() returns something non-id-like.
        (renders_dir / LAST_ID_SIDECAR_NAME).write_text("   \n")

        write_state("match", _sample_match_data(), id="fresh")
        term = GameTerminal()
        # strip()→"" becomes None so the state still renders.
        assert term.last_rendered_id is None
        assert term.dispatch_once().action == "rendered"


# ---------------------------------------------------------------------------
# Live watcher path
# ---------------------------------------------------------------------------

class TestLiveWatcher:
    def test_start_drains_existing_state(self, iso_paths):
        _, renders_dir = iso_paths
        write_state("match", _sample_match_data(), id="drain_me")

        term = GameTerminal()
        term.start()
        try:
            assert _wait_for(
                lambda: (renders_dir / "match_drain_me.png").exists()
            ), "match PNG was not drained on start"
        finally:
            term.stop()

    def test_live_write_triggers_render(self, iso_paths):
        _, renders_dir = iso_paths

        term = GameTerminal()
        term.start()
        try:
            # Start leaves last_id = None (no state existed).
            write_state("match", _sample_match_data(), id="live_1")
            assert _wait_for(
                lambda: (renders_dir / "match_live_1.png").exists(),
                timeout=5.0,
            ), "live write did not produce render"
        finally:
            term.stop()

    def test_second_start_raises(self, iso_paths):
        term = GameTerminal()
        term.start()
        try:
            with pytest.raises(RuntimeError, match="already started"):
                term.start()
        finally:
            term.stop()

    def test_context_manager(self, iso_paths):
        _, renders_dir = iso_paths
        write_state("match", _sample_match_data(), id="ctx_1")

        with GameTerminal() as term:
            assert _wait_for(
                lambda: (renders_dir / "match_ctx_1.png").exists()
            )
            assert term.last_rendered_id == "ctx_1"

    def test_stop_is_idempotent(self, iso_paths):
        term = GameTerminal()
        term.start()
        term.stop()
        term.stop()  # second stop must not raise


# ---------------------------------------------------------------------------
# Renderers produce real files
# ---------------------------------------------------------------------------

class TestRendererOutput:
    def test_match_png_written(self, iso_paths):
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        gs = GameState(
            view="match",
            data=_sample_match_data(),
            id="render_target",
            ts_ns=0,
        )
        out = render_match(gs, renders_dir)
        assert out.exists()
        # Minimum-size sanity: a 960x540 RGB PNG should be > 1 KB.
        assert out.stat().st_size > 1024

    def test_placeholder_png_written(self, tmp_path):
        out = _render_placeholder_png(
            out_path=tmp_path / "test.png",
            title="title",
            subtitle="subtitle",
            lines=["one", "two"],
        )
        assert out.exists()
        assert out.stat().st_size > 256


# ---------------------------------------------------------------------------
# Data helper
# ---------------------------------------------------------------------------

def _sample_match_data() -> dict:
    """Minimal match state payload shaped like what dm_match writes."""
    return {
        "winner": 0,
        "reason": "all_dead",
        "side_a_final_hp": 42,
        "side_b_final_hp": 0,
        "round_count": 3,
        "seed": "00" * 32,
        "rounds": [],
        "loadout_a": [
            {"card_id": "a_1", "species": "scoutling", "element": "FIRE",
             "atk": 5, "def": 5, "hp": 20, "spd": 5, "triggers": []},
        ],
        "loadout_b": [
            {"card_id": "b_1", "species": "iron_boar", "element": "WATER",
             "atk": 5, "def": 5, "hp": 20, "spd": 5, "triggers": []},
        ],
    }


# ---------------------------------------------------------------------------
# A.4.c — Match-payload animator (terminal animator)
# ---------------------------------------------------------------------------

import json as _json
from pathlib import Path as _Path

# Repo-root fixture used by the play module's existing animator tests too.
_FIXTURE_PATH = _Path(__file__).resolve().parent.parent / "daimon" / "play" / "fixtures" / "match_sample.json"


def _load_match_fixture() -> dict:
    """Load the canonical hand-crafted Match payload (V2 wire format)."""
    return _json.loads(_FIXTURE_PATH.read_text())


class TestIsMatchPayload:
    """Shape-detection: schema.Match vs legacy summary dict."""

    def test_legacy_summary_is_not_match(self):
        assert _is_match_payload(_sample_match_data()) is False

    def test_v2_match_fixture_is_match(self):
        assert _is_match_payload(_load_match_fixture()) is True

    def test_non_dict_is_not_match(self):
        assert _is_match_payload(None) is False
        assert _is_match_payload([]) is False
        assert _is_match_payload("match") is False

    def test_missing_fields_is_not_match(self):
        # Missing event_type
        assert _is_match_payload({
            "schema_version": 2, "participants": {}, "rounds": [],
        }) is False
        # Missing rounds list
        assert _is_match_payload({
            "schema_version": 2, "event_type": "match",
            "participants": {}, "rounds": "not a list",
        }) is False
        # Wrong schema_version
        assert _is_match_payload({
            "schema_version": 1, "event_type": "match",
            "participants": {}, "rounds": [],
        }) is False


class TestFrameMilestones:
    """The per-beat sample-point generator."""

    def test_default_50ms_yields_12_frames(self):
        # 600ms beat / 50ms = 12 frames, t=0..550
        ms = _frame_milestones(beat_ms=600, interval_ms=50)
        assert ms == [0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550]

    def test_excludes_beat_end(self):
        # t=600 would be t=0 of the next beat — must not appear here
        ms = _frame_milestones(beat_ms=600, interval_ms=100)
        assert 600 not in ms
        assert ms[-1] == 500

    def test_starts_at_zero(self):
        assert _frame_milestones(600, 200)[0] == 0

    def test_invalid_interval_raises(self):
        with pytest.raises(ValueError):
            _frame_milestones(600, 0)
        with pytest.raises(ValueError):
            _frame_milestones(600, -1)

    def test_interval_larger_than_beat_yields_one_frame(self):
        # Edge: interval > beat. Range yields just [0].
        assert _frame_milestones(600, 1000) == [0]


class TestRenderMatchAnimated:
    """The terminal animator — Match payload → frame stream + manifest."""

    def test_writes_manifest_to_renders_dir(self, iso_paths):
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        gs = GameState(
            view="match",
            data=_load_match_fixture(),
            id="anim_alpha",
            ts_ns=0,
        )
        out = _render_match_animated(gs, renders_dir, interval_ms=100)
        assert out.exists()
        assert out.parent == renders_dir
        assert out.name == "match_anim_alpha_manifest.json"

    def test_manifest_schema(self, iso_paths):
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        gs = GameState(
            view="match",
            data=_load_match_fixture(),
            id="anim_schema",
            ts_ns=0,
        )
        out = _render_match_animated(gs, renders_dir, interval_ms=100)
        manifest = _json.loads(out.read_text())

        assert manifest["manifest_schema_version"] == MANIFEST_SCHEMA_VERSION
        assert manifest["state_id"] == "anim_schema"
        assert manifest["match_id"] == _load_match_fixture()["match_id"]
        assert manifest["frame_interval_ms"] == 100
        assert manifest["frames_dir"] == "match_anim_schema"
        assert manifest["winner"] in {"player", "opponent"}
        # Total duration = total_frames * interval (no double-count of beat-end)
        assert manifest["total_duration_ms"] == manifest["total_frames"] * 100

    def test_frame_count_matches_actions_times_milestones(self, iso_paths):
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        fixture = _load_match_fixture()
        # 5 actions in the fixture (3 in r1 + 2 in r2)
        n_actions = sum(len(r["actions"]) for r in fixture["rounds"])

        gs = GameState(view="match", data=fixture, id="anim_count", ts_ns=0)
        out = _render_match_animated(gs, renders_dir, interval_ms=100)
        manifest = _json.loads(out.read_text())

        # 600ms beat / 100ms interval = 6 frames per beat
        expected = n_actions * 6
        assert manifest["total_frames"] == expected
        assert len(manifest["frames"]) == expected

    def test_every_frame_file_exists(self, iso_paths):
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        gs = GameState(
            view="match",
            data=_load_match_fixture(),
            id="anim_files",
            ts_ns=0,
        )
        out = _render_match_animated(gs, renders_dir, interval_ms=200)
        manifest = _json.loads(out.read_text())

        # Resolve each frame's path and verify it lives on disk + has bytes
        for frame_meta in manifest["frames"]:
            frame_path = renders_dir / frame_meta["path"]
            assert frame_path.exists(), f"missing {frame_path}"
            assert frame_path.stat().st_size > 1024, f"too small: {frame_path}"

    def test_frames_are_ordered_by_index(self, iso_paths):
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        gs = GameState(
            view="match",
            data=_load_match_fixture(),
            id="anim_order",
            ts_ns=0,
        )
        out = _render_match_animated(gs, renders_dir, interval_ms=200)
        manifest = _json.loads(out.read_text())

        indices = [f["index"] for f in manifest["frames"]]
        assert indices == list(range(len(indices)))

        # Also: each frame's elapsed_ms is monotonic
        elapsed = [f["elapsed_ms"] for f in manifest["frames"]]
        assert elapsed == sorted(elapsed)

    def test_frame_metadata_references_real_actions(self, iso_paths):
        """No orphan frames — every (round, action_index) must hit the fixture."""
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        fixture = _load_match_fixture()
        gs = GameState(view="match", data=fixture, id="anim_orphan", ts_ns=0)
        out = _render_match_animated(gs, renders_dir, interval_ms=200)
        manifest = _json.loads(out.read_text())

        valid_pairs = {
            (r["round"], i)
            for r in fixture["rounds"]
            for i in range(len(r["actions"]))
        }
        for frame_meta in manifest["frames"]:
            pair = (frame_meta["round"], frame_meta["action_index"])
            assert pair in valid_pairs, f"orphan frame: {frame_meta}"

    def test_deterministic_same_input_same_manifest(self, iso_paths):
        """Same Match → same manifest (frame count, indices, t_ms milestones)."""
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        fixture = _load_match_fixture()

        gs_a = GameState(view="match", data=fixture, id="anim_det_a", ts_ns=0)
        gs_b = GameState(view="match", data=fixture, id="anim_det_b", ts_ns=0)
        out_a = _render_match_animated(gs_a, renders_dir, interval_ms=200)
        out_b = _render_match_animated(gs_b, renders_dir, interval_ms=200)

        m_a = _json.loads(out_a.read_text())
        m_b = _json.loads(out_b.read_text())

        # Strip state-id-dependent fields to compare structure
        def _strip(m):
            return {k: v for k, v in m.items() if k not in {"state_id", "frames_dir", "frames"}}
        assert _strip(m_a) == _strip(m_b)

        # And the per-frame metadata (round / action_index / t_ms / elapsed_ms)
        # is identical sequence-wise — the only difference is the path field.
        def _frame_logical(f):
            return {k: v for k, v in f.items() if k != "path"}
        assert [_frame_logical(f) for f in m_a["frames"]] == \
               [_frame_logical(f) for f in m_b["frames"]]


class TestRenderMatchDispatch:
    """render_match dispatches by payload shape."""

    def test_legacy_payload_routes_to_summary(self, iso_paths):
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        gs = GameState(
            view="match",
            data=_sample_match_data(),
            id="legacy_route",
            ts_ns=0,
        )
        out = render_match(gs, renders_dir)
        # Summary still emits a single PNG named match_<id>.png (not a manifest)
        assert out.suffix == ".png"
        assert out.name == "match_legacy_route.png"
        # And the per-state animation dir was NOT created
        assert not (renders_dir / "match_legacy_route").exists()

    def test_match_payload_routes_to_animated(self, iso_paths):
        _, renders_dir = iso_paths
        renders_dir.mkdir(parents=True, exist_ok=True)
        gs = GameState(
            view="match",
            data=_load_match_fixture(),
            id="anim_route",
            ts_ns=0,
        )
        out = render_match(gs, renders_dir)
        assert out.suffix == ".json"
        assert out.name == "match_anim_route_manifest.json"
        # And the frames dir is populated
        assert (renders_dir / "match_anim_route").is_dir()
        assert any((renders_dir / "match_anim_route").iterdir())

    def test_dispatcher_routes_match_payload_through_full_terminal(self, iso_paths):
        """End-to-end: Match payload via write_state → GameTerminal → manifest on disk."""
        _, renders_dir = iso_paths
        write_state("match", _load_match_fixture(), id="e2e_anim")

        term = GameTerminal()
        result = term.dispatch_once()
        assert result.action == "rendered"
        assert result.out_path is not None
        assert result.out_path.name == "match_e2e_anim_manifest.json"
        assert result.out_path.exists()
        assert term.last_rendered_id == "e2e_anim"
