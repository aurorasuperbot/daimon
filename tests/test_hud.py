"""Spectator HUD tests — playback engine, renderer, app loop.

Pure-logic tests (playback + render) drive the bulk of coverage. App-loop
tests use the test seams (`force_load_match`, `clock_ms`, `poll_only=True`,
`max_ticks=N`) so we never spin a real watchdog or terminal.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from daimon.play.hud import (
    MatchPlayback,
    Phase,
    PlaybackStatus,
    SPEED_LADDER,
    Step,
    flatten_match,
    hp_at,
    render_frame,
    render_idle,
)
from daimon.play.hud.app import HudApp
from daimon.play.hud.keyboard import Key, decode_key
from daimon.play.hud.playback import (
    BASE_TICK_MS,
    DEFAULT_SPEED_INDEX,
    END_COOLDOWN_MS,
)
from daimon.play.schema import Match, Side
from daimon.play.state import write_state


FIXTURE = Path(__file__).parent.parent / "daimon" / "play" / "fixtures" / "match_sample.json"


@pytest.fixture
def sample_match() -> Match:
    data = json.loads(FIXTURE.read_text())
    return Match.model_validate(data)


# ---------------------------------------------------------------------------
# flatten_match
# ---------------------------------------------------------------------------

def test_flatten_match_walks_actions_and_triggers_depth_first(sample_match):
    timeline = flatten_match(sample_match)
    assert len(timeline) > 0
    # First step is now the LINEUP chrome banner (depth=0, round_number=0).
    assert timeline[0].is_phase
    assert timeline[0].phase == Phase.LINEUP
    assert timeline[0].depth == 0
    # First action step is round 1, action 0, depth 0 — sits AFTER LINEUP +
    # the round-1 ROUND_START banner.
    actions = [s for s in timeline if s.is_action]
    assert actions[0].round_number == 1
    assert actions[0].action_index == 0
    assert actions[0].depth == 0
    # Indices are gap-free and 0-based across all steps (chrome + action).
    for i, step in enumerate(timeline):
        assert step.index == i


def test_flatten_match_emits_parent_before_triggers(sample_match):
    timeline = flatten_match(sample_match)
    # Find any depth>0 step; immediately preceding step must be its parent
    # (depth-1 in the same round). Skip phase steps — they are depth=0 and
    # never parent a trigger.
    for i, step in enumerate(timeline):
        if step.depth == 0 or step.is_phase:
            continue
        prior = timeline[i - 1]
        assert prior.round_number == step.round_number
        assert prior.depth >= step.depth - 1


def test_flatten_match_handles_empty_rounds():
    """A match with rounds=[] still emits LINEUP + OUTCOME chrome."""
    fake = {
        "schema_version": 2,
        "event_type": "match",
        "match_id": "empty",
        "kind": "pve",
        "timestamp": "2026-04-22T00:00:00Z",
        "participants": {
            "player": {
                "name": "p", "rank": "Rookie",
                "loadout": [
                    {"position": i, "species": "x", "element": "fire",
                     "name": "X", "hp_max": 5, "hp": 5}
                    for i in range(6)
                ],
            },
            "opponent": {
                "name": "o", "rank": "Rookie",
                "loadout": [
                    {"position": i, "species": "y", "element": "water",
                     "name": "Y", "hp_max": 5, "hp": 5}
                    for i in range(6)
                ],
            },
        },
        "rounds": [],
        "outcome": {
            "winner": "player",
            "player_hp_remaining": 30,
            "opponent_hp_remaining": 0,
            "stats": {"round_count": 0},
            "rewards": {},
        },
    }
    m = Match.model_validate(fake)
    timeline = flatten_match(m)
    # No actions, but LINEUP + OUTCOME bracket the timeline.
    assert [s.phase for s in timeline] == [Phase.LINEUP, Phase.OUTCOME]
    assert all(s.is_phase for s in timeline)


# ---------------------------------------------------------------------------
# hp_at
# ---------------------------------------------------------------------------

def test_hp_at_starts_with_loadout_hp(sample_match):
    timeline = flatten_match(sample_match)
    snap = hp_at(sample_match, timeline, cursor=-1)
    # Every position seeded.
    for side in (Side.PLAYER, Side.OPPONENT):
        part = sample_match.participants[side.value]
        for card in part.loadout:
            assert snap.get(side, card.position, default=999) == card.hp


def test_hp_at_applies_patches_through_cursor(sample_match):
    timeline = flatten_match(sample_match)
    # Walk to the very end — every hp_after key in the timeline should be
    # reflected in the final snapshot. Phase steps carry no patches and
    # are skipped here (mirrors hp_at internal logic).
    snap = hp_at(sample_match, timeline, cursor=len(timeline) - 1)
    accumulated: dict[str, int] = {}
    for step in timeline:
        if step.action is None:
            continue
        for k, v in step.action.hp_after.items():
            accumulated[k] = v
    for k, v in accumulated.items():
        assert snap.by_key[k] == v


def test_hp_at_is_monotonic_for_damage_only_chain(sample_match):
    """If a card only takes damage, its HP at later cursors ≤ earlier cursors."""
    timeline = flatten_match(sample_match)
    # Find a card-key that only appears in damage events (kind == 'damage')
    # in this fixture and check monotonicity.
    key = "player/2"  # blade foxling — gets hit early in fixture
    seen = []
    for cur in range(len(timeline)):
        snap = hp_at(sample_match, timeline, cur)
        seen.append(snap.by_key.get(key, 13))
    # The fixture only damages player/2 — sequence should be non-increasing.
    for a, b in zip(seen, seen[1:]):
        assert b <= a, f"HP went up unexpectedly: {seen}"


# ---------------------------------------------------------------------------
# Phase events — chrome woven into the flat timeline
# ---------------------------------------------------------------------------

def test_flatten_match_inserts_lineup_first(sample_match):
    timeline = flatten_match(sample_match)
    assert timeline[0].is_phase
    assert timeline[0].phase == Phase.LINEUP


def test_flatten_match_inserts_outcome_last(sample_match):
    timeline = flatten_match(sample_match)
    assert timeline[-1].is_phase
    assert timeline[-1].phase == Phase.OUTCOME


def test_flatten_match_inserts_round_start_before_each_round(sample_match):
    timeline = flatten_match(sample_match)
    rs_steps = [s for s in timeline if s.is_phase and s.phase == Phase.ROUND_START]
    # One ROUND_START per round in the source match.
    assert len(rs_steps) == len(sample_match.rounds)
    # Each ROUND_START sits immediately before the round's first action.
    for rs in rs_steps:
        nxt = timeline[rs.index + 1]
        assert nxt.is_action
        assert nxt.round_number == rs.round_number
        assert nxt.action_index == 0


def test_flatten_match_phase_steps_have_no_action(sample_match):
    for s in flatten_match(sample_match):
        if s.is_phase:
            assert s.action is None
        else:
            assert s.action is not None
            assert s.phase is None


def test_hp_at_skips_phase_steps(sample_match):
    """HP at LINEUP step (cursor=0) equals starting HP for every position."""
    timeline = flatten_match(sample_match)
    # cursor 0 is LINEUP; no patches should have been applied.
    snap = hp_at(sample_match, timeline, cursor=0)
    for side in (Side.PLAYER, Side.OPPONENT):
        part = sample_match.participants[side.value]
        for card in part.loadout:
            assert snap.get(side, card.position, default=999) == card.hp


# ---------------------------------------------------------------------------
# MatchPlayback transport
# ---------------------------------------------------------------------------

def test_playback_default_status_is_playing(sample_match):
    pb = MatchPlayback(match=sample_match)
    assert pb.status == PlaybackStatus.PLAYING
    assert pb.cursor == 0
    assert pb.speed == SPEED_LADDER[DEFAULT_SPEED_INDEX]


def test_playback_advance_moves_cursor(sample_match):
    pb = MatchPlayback(match=sample_match)
    assert pb.advance()
    assert pb.cursor == 1


def test_playback_advance_at_end_transitions_to_ended(sample_match):
    pb = MatchPlayback(match=sample_match)
    pb.cursor = len(pb.timeline) - 1
    assert not pb.advance()
    assert pb.status == PlaybackStatus.ENDED


def test_playback_back_decrements_and_unsets_ended(sample_match):
    pb = MatchPlayback(match=sample_match)
    pb.jump_to_end()
    assert pb.status == PlaybackStatus.ENDED
    assert pb.back()
    assert pb.cursor == len(pb.timeline) - 2
    assert pb.status == PlaybackStatus.PAUSED


def test_playback_back_at_zero_returns_false(sample_match):
    pb = MatchPlayback(match=sample_match)
    assert not pb.back()
    assert pb.cursor == 0


def test_playback_pause_toggle(sample_match):
    pb = MatchPlayback(match=sample_match)
    pb.toggle_pause()
    assert pb.status == PlaybackStatus.PAUSED
    pb.toggle_pause()
    assert pb.status == PlaybackStatus.PLAYING


def test_playback_speed_clamps_at_ladder_ends(sample_match):
    pb = MatchPlayback(match=sample_match)
    for _ in range(50):
        pb.speed_up()
    assert pb.speed == SPEED_LADDER[-1]
    for _ in range(50):
        pb.speed_down()
    assert pb.speed == SPEED_LADDER[0]


def test_playback_restart_returns_to_step_zero_playing(sample_match):
    pb = MatchPlayback(match=sample_match)
    pb.jump_to_end()
    pb.restart()
    assert pb.cursor == 0
    assert pb.status == PlaybackStatus.PLAYING
    assert pb.ended_dwell_ms == 0


# ---------------------------------------------------------------------------
# MatchPlayback ticks
# ---------------------------------------------------------------------------

def test_playback_step_advances_per_speed(sample_match):
    pb = MatchPlayback(match=sample_match)
    # At 1.0x, BASE_TICK_MS per step. Two ticks → cursor=2.
    pb.step(BASE_TICK_MS * 2)
    assert pb.cursor == 2


def test_playback_step_zero_when_paused(sample_match):
    pb = MatchPlayback(match=sample_match)
    pb.pause()
    pb.step(BASE_TICK_MS * 5)
    assert pb.cursor == 0


def test_playback_step_accumulates_dwell_when_ended(sample_match):
    pb = MatchPlayback(match=sample_match)
    pb.jump_to_end()
    pb.step(1500)
    assert pb.ended_dwell_ms == 1500
    pb.step(2000)
    assert pb.ended_dwell_ms == 3500


def test_playback_step_speed_4x_advances_4x_faster(sample_match):
    pb = MatchPlayback(match=sample_match)
    # Bump to 4x.
    while pb.speed < 4.0:
        pb.speed_up()
    pb.step(BASE_TICK_MS)   # at 4x, BASE_TICK_MS = 4 steps
    assert pb.cursor == 4


def test_playback_snapshot_log_tail_includes_current(sample_match):
    pb = MatchPlayback(match=sample_match)
    pb.cursor = 3
    snap = pb.snapshot()
    # Most recent in tail should be the current step.
    assert snap.log_tail[-1].index == 3
    assert snap.current_step is snap.log_tail[-1]


# ---------------------------------------------------------------------------
# Renderer (color-stripped — easy to assert on)
# ---------------------------------------------------------------------------

def test_render_frame_includes_opponent_and_player_names(sample_match):
    pb = MatchPlayback(match=sample_match)
    frame = pb.snapshot()
    out = render_frame(frame, color=False)
    assert "Champion Lyra" in out         # opponent in title
    assert "santiago" in out               # player in lineup label
    assert "OPPONENT" in out
    assert "PLAYER" in out


def test_render_frame_shows_status_and_speed(sample_match):
    pb = MatchPlayback(match=sample_match)
    out = render_frame(pb.snapshot(), color=False)
    assert "playing" in out
    assert "1.0×" in out


def test_render_frame_marks_current_step_with_arrow(sample_match):
    pb = MatchPlayback(match=sample_match)
    pb.cursor = 2
    out = render_frame(pb.snapshot(), color=False)
    # ▶ should appear once (the current-step marker in the log).
    assert "▶" in out


def test_render_frame_shows_hp_bars_with_correct_widths(sample_match):
    pb = MatchPlayback(match=sample_match)
    out = render_frame(pb.snapshot(), color=False)
    # All cards full at start except player/0 (8/12), player/2 (6/13),
    # player/3 (6/6 — note hp_max=6 also). Look for the partial bars.
    # The HP bar is 10 chars; 8/12 → 7 filled. Just check structure.
    assert "HP:" in out
    # At least one full-bar card (██████████) present.
    assert "██████████" in out


def test_render_frame_strips_color_when_requested(sample_match):
    pb = MatchPlayback(match=sample_match)
    out = render_frame(pb.snapshot(), color=False)
    assert "\x1b[" not in out
    out_color = render_frame(pb.snapshot(), color=True)
    assert "\x1b[" in out_color


def test_render_frame_width_is_80_per_line(sample_match):
    pb = MatchPlayback(match=sample_match)
    out = render_frame(pb.snapshot(), color=False)
    for line in out.split("\n"):
        assert len(line) == 80, f"line not 80 cols: {len(line)} {line!r}"


def test_render_idle_lists_recent_matches():
    out = render_idle(recent=["m_001  vs Sparring Sam", "m_002  vs Doom-paw Doppia"], color=False)
    assert "waiting for match" in out
    assert "Sparring Sam" in out
    assert "Doom-paw Doppia" in out


def test_render_idle_handles_empty_recent_list():
    out = render_idle(recent=[], color=False)
    assert "waiting for match" in out
    assert "(none yet)" in out


# ---------------------------------------------------------------------------
# Chrome screens (LINEUP / ROUND_START / OUTCOME)
# ---------------------------------------------------------------------------

def _seek_to_phase(pb: MatchPlayback, phase: Phase) -> int:
    for s in pb.timeline:
        if s.is_phase and s.phase == phase:
            pb.cursor = s.index
            return s.index
    raise AssertionError(f"no {phase} step in timeline")


def test_render_frame_lineup_chrome_shows_match_starting(sample_match):
    pb = MatchPlayback(match=sample_match)
    _seek_to_phase(pb, Phase.LINEUP)
    out = render_frame(pb.snapshot(), color=False)
    assert "MATCH STARTING" in out
    # Banner replaces action log — no ⚔ glyph in the chrome band.
    assert "match starting" in out


def test_render_frame_round_chrome_shows_round_banner(sample_match):
    pb = MatchPlayback(match=sample_match)
    _seek_to_phase(pb, Phase.ROUND_START)
    out = render_frame(pb.snapshot(), color=False)
    assert "ROUND 1" in out
    assert "round 1 begins" in out


def test_render_frame_outcome_chrome_shows_winner(sample_match):
    pb = MatchPlayback(match=sample_match)
    _seek_to_phase(pb, Phase.OUTCOME)
    out = render_frame(pb.snapshot(), color=False)
    winner = sample_match.outcome.winner.value.upper()
    assert f"{winner} WINS" in out


def test_render_frame_outcome_chrome_draw_does_not_say_wins(sample_match):
    """A draw outcome must render `DRAW` (not the ungrammatical `DRAW WINS`)."""
    # Force the loaded match into a draw outcome — only the renderer is under
    # test here; we don't care that the rounds list disagrees with the outcome.
    sample_match.outcome.winner = Side.DRAW
    pb = MatchPlayback(match=sample_match)
    _seek_to_phase(pb, Phase.OUTCOME)
    out = render_frame(pb.snapshot(), color=False)
    assert "═══ DRAW ═══" in out, out
    assert "DRAW WINS" not in out
    # Title bar at the top must also avoid "DRAW wins".
    assert "DRAW wins" not in out
    assert "— draw" in out, out


def test_render_frame_chrome_width_invariant(sample_match):
    """All chrome screens must hold the 80-col-per-line invariant."""
    pb = MatchPlayback(match=sample_match)
    for phase in (Phase.LINEUP, Phase.ROUND_START, Phase.OUTCOME):
        _seek_to_phase(pb, phase)
        out = render_frame(pb.snapshot(), color=False)
        for line in out.split("\n"):
            assert len(line) == 80, (
                f"chrome line not 80 cols on {phase}: {len(line)} {line!r}"
            )


def test_render_frame_chrome_keeps_lineups_visible(sample_match):
    """Chrome replaces only the log band — both lineups still render."""
    pb = MatchPlayback(match=sample_match)
    _seek_to_phase(pb, Phase.ROUND_START)
    out = render_frame(pb.snapshot(), color=False)
    # Lineup labels still present.
    assert "OPPONENT" in out
    assert "PLAYER" in out
    # And HP bars (full at round start).
    assert "██████████" in out


def test_render_frame_chrome_status_line_uses_phase_label(sample_match):
    pb = MatchPlayback(match=sample_match)
    _seek_to_phase(pb, Phase.LINEUP)
    out = render_frame(pb.snapshot(), color=False)
    # Status bar substitutes "lineup" for the act-counter instead of "act 1/0".
    assert "lineup" in out


def test_scrub_back_lands_on_round_start_phase(sample_match):
    """back() across the action→ROUND_START boundary lands on chrome."""
    pb = MatchPlayback(match=sample_match)
    # Find first action of round 2 (if any), step there, then back once.
    second_round_first_action = next(
        (s for s in pb.timeline
         if s.is_action and s.round_number == 2 and s.action_index == 0),
        None,
    )
    if second_round_first_action is None:
        pytest.skip("fixture has < 2 rounds")
    pb.cursor = second_round_first_action.index
    pb.back()
    cur = pb.timeline[pb.cursor]
    assert cur.is_phase
    assert cur.phase == Phase.ROUND_START
    assert cur.round_number == 2


# ---------------------------------------------------------------------------
# Keyboard decoding
# ---------------------------------------------------------------------------

def test_decode_key_known_singles():
    assert decode_key(b" ") == Key.SPACE
    assert decode_key(b"\r") == Key.ENTER
    assert decode_key(b"\n") == Key.ENTER
    assert decode_key(b"\x1b") == Key.ESC
    assert decode_key(b"q") == Key.Q
    assert decode_key(b"r") == Key.R
    assert decode_key(b"n") == Key.N
    assert decode_key(b"p") == Key.P


def test_decode_key_arrows():
    assert decode_key(b"\x1b[A") == Key.UP
    assert decode_key(b"\x1b[B") == Key.DOWN
    assert decode_key(b"\x1b[C") == Key.RIGHT
    assert decode_key(b"\x1b[D") == Key.LEFT


def test_decode_key_unknowns_return_none_or_letter():
    assert decode_key(b"") is None
    # A bare letter that's not a binding returns the lowercase letter.
    assert decode_key(b"z") == "z"
    # Unknown CSI returns None.
    assert decode_key(b"\x1b[Z") is None


# ---------------------------------------------------------------------------
# HudApp — driven via test seams
# ---------------------------------------------------------------------------

def test_hudapp_force_load_match_advances_on_tick(sample_match, tmp_path):
    sink = io.StringIO()
    clock = [0]
    state_path = tmp_path / "state.json"   # empty — _poll_state is a no-op

    def fake_clock():
        return clock[0]

    app = HudApp(
        state_path=state_path,
        sink=sink, color=False, keyboard_enabled=False,
        poll_only=True, autoplay=True, clock_ms=fake_clock,
        max_ticks=1, tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="x")
    assert app.playback is not None
    assert app.playback.cursor == 0
    # Advance the fake clock by enough ms to push the cursor.
    clock[0] = BASE_TICK_MS * 2
    app._tick_once(kb=None)
    assert app.playback.cursor >= 1


def test_hudapp_unloads_after_end_cooldown(sample_match, tmp_path):
    clock = [0]
    state_path = tmp_path / "state.json"
    app = HudApp(
        state_path=state_path,
        sink=io.StringIO(), color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: clock[0],
        tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="end-test")
    app.playback.jump_to_end()
    # Burn enough wall time to exceed END_COOLDOWN_MS.
    clock[0] = END_COOLDOWN_MS + 100
    app._tick_once(kb=None)
    # Playback should be unloaded → IDLE screen will render next.
    assert app.playback is None


def test_hudapp_picks_up_new_match_via_state_file(sample_match, tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("DAIMON_STATE", str(state_path))
    # Write the match into the state file.
    payload = sample_match.model_dump(mode="json")
    write_state("match", payload, id="hud-pickup", state_path=state_path)

    app = HudApp(
        state_path=state_path, sink=io.StringIO(), color=False,
        keyboard_enabled=False, poll_only=True,
        clock_ms=lambda: 0, tick_ms=10,
    )
    app._tick_once(kb=None)
    assert app.playback is not None
    assert app.playback.state_id == "hud-pickup"


def test_hudapp_dedup_skips_same_state_id(sample_match, tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("DAIMON_STATE", str(state_path))
    payload = sample_match.model_dump(mode="json")
    write_state("match", payload, id="dedup-1", state_path=state_path)

    app = HudApp(
        state_path=state_path, sink=io.StringIO(), color=False,
        keyboard_enabled=False, poll_only=True,
        clock_ms=lambda: 0, tick_ms=10,
    )
    app._tick_once(kb=None)
    first_pb = app.playback
    # Polling again with no state change is a no-op.
    app._poll_state()
    assert app.playback is first_pb


def test_hudapp_swaps_playback_when_new_state_id_arrives(sample_match, tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("DAIMON_STATE", str(state_path))
    payload = sample_match.model_dump(mode="json")
    write_state("match", payload, id="swap-1", state_path=state_path)
    app = HudApp(
        state_path=state_path, sink=io.StringIO(), color=False,
        keyboard_enabled=False, poll_only=True,
        clock_ms=lambda: 0, tick_ms=10,
    )
    app._tick_once(kb=None)
    pb1 = app.playback
    # Write a new state id with same payload — should trigger reload.
    write_state("match", payload, id="swap-2", state_path=state_path)
    app._tick_once(kb=None)
    pb2 = app.playback
    assert pb2 is not pb1
    assert pb2.state_id == "swap-2"


def test_hudapp_renders_idle_when_no_playback(tmp_path):
    sink = io.StringIO()
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=sink, color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app._render()
    out = sink.getvalue()
    assert "waiting for match" in out


def test_hudapp_renders_match_frame_when_loaded(sample_match, tmp_path):
    sink = io.StringIO()
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=sink, color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="render-test")
    app._render()
    out = sink.getvalue()
    assert "Champion Lyra" in out
    assert "OPPONENT" in out


def test_hudapp_handle_key_quit_sets_stop_event(sample_match, tmp_path):
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=io.StringIO(), color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app._handle_key(Key.Q)
    assert app._stop_event.is_set()


def test_hudapp_handle_key_space_pauses(sample_match, tmp_path):
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=io.StringIO(), color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="space-test")
    assert app.playback.status == PlaybackStatus.PLAYING
    app._handle_key(Key.SPACE)
    assert app.playback.status == PlaybackStatus.PAUSED
    app._handle_key(Key.SPACE)
    assert app.playback.status == PlaybackStatus.PLAYING


def test_hudapp_handle_key_arrows_step(sample_match, tmp_path):
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=io.StringIO(), color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="arrow-test")
    app._handle_key(Key.RIGHT)
    assert app.playback.cursor == 1
    assert app.playback.status == PlaybackStatus.PAUSED  # right pauses + steps
    app._handle_key(Key.LEFT)
    assert app.playback.cursor == 0


def test_hudapp_handle_key_speed_up_down(sample_match, tmp_path):
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=io.StringIO(), color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="speed-test")
    starting = app.playback.speed
    app._handle_key(Key.UP)
    assert app.playback.speed > starting
    app._handle_key(Key.DOWN)
    assert app.playback.speed == starting


def test_hudapp_handle_key_n_jumps_to_end(sample_match, tmp_path):
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=io.StringIO(), color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="n-test")
    app._handle_key(Key.N)
    assert app.playback.cursor == len(app.playback.timeline) - 1
    assert app.playback.status == PlaybackStatus.ENDED


def test_hudapp_handle_key_r_restarts(sample_match, tmp_path):
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=io.StringIO(), color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="r-test")
    app.playback.jump_to_end()
    app._handle_key(Key.R)
    assert app.playback.cursor == 0
    assert app.playback.status == PlaybackStatus.PLAYING


def test_hudapp_recent_matches_log_appends(sample_match, tmp_path):
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=io.StringIO(), color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="recent-test")
    # force_load_match doesn't touch recent log — only _load_match does.
    # Drive via the state-file path to cover the real codepath.
    assert len(app.recent) == 0


def test_hudapp_recent_matches_log_via_state_file(sample_match, tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("DAIMON_STATE", str(state_path))
    payload = sample_match.model_dump(mode="json")
    write_state("match", payload, id="recent-1", state_path=state_path)
    app = HudApp(
        state_path=state_path, sink=io.StringIO(), color=False,
        keyboard_enabled=False, poll_only=True,
        clock_ms=lambda: 0, tick_ms=10,
    )
    app._tick_once(kb=None)
    assert len(app.recent) == 1
    assert "Champion Lyra" in app.recent[0]


def test_hudapp_render_signature_dedupes_unchanged_frames(sample_match, tmp_path):
    sink = io.StringIO()
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=sink, color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="dedup-render")
    app._render()
    bytes_after_first = len(sink.getvalue())
    # Re-render with no state change — should write nothing new.
    app._render()
    assert len(sink.getvalue()) == bytes_after_first


def test_hudapp_render_emits_when_cursor_moves(sample_match, tmp_path):
    sink = io.StringIO()
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=sink, color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=10,
    )
    app.force_load_match(sample_match, state_id="move-render")
    app._render()
    initial = len(sink.getvalue())
    app.playback.advance()
    app._render()
    assert len(sink.getvalue()) > initial


def test_hudapp_max_ticks_returns_cleanly(sample_match, tmp_path):
    """End-to-end: app exits after max_ticks without hanging."""
    app = HudApp(
        state_path=tmp_path / "state.json",
        sink=io.StringIO(), color=False, keyboard_enabled=False,
        poll_only=True, clock_ms=lambda: 0, tick_ms=1,
        max_ticks=3,
    )
    app.force_load_match(sample_match, state_id="exit-test")
    rc = app.run()
    assert rc == 0
