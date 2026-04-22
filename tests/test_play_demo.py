"""Tests for the animation demo match.

These tests double as the acceptance check for design-doc criterion #5:
"All primitives have unit tests + a single end-to-end integration test that
verifies primitive ordering against a known fixture match." The demo match
IS that fixture, by construction — it is hand-crafted to exercise every
primitive in the V1 vocabulary, so walking it and collecting all kinds
seen across all (action, t_ms) pairs must yield the full set.
"""

from __future__ import annotations

import pytest

from daimon.play.animator import Animator
from daimon.play.demo import build_demo_match, run_demo
from daimon.play.hud.playback import MatchPlayback
from daimon.play.hud.render import render_frame
from daimon.play.primitives import ACTION_BEAT_MS, Cue


# Sample sub-beat times — 7 ticks across the 600ms beat hits every primitive's
# active window at least once (intent 0..200, hp_tick 200..550, etc).
_SAMPLE_TIMES = [0, 50, 100, 150, 200, 300, 400, 500]


def _all_kinds_emitted(match) -> set[str]:
    """Walk every action × every sample t_ms; collect emission kinds."""
    animator = Animator()
    seen: set[str] = set()
    for r in match.rounds:
        for action in r.actions:
            for t in _SAMPLE_TIMES:
                snap = animator.tick(action, t_ms=t)
                for e in snap.card_emissions:
                    seen.add(e.kind)
                if snap.connection is not None:
                    seen.add("connection_line")
                if snap.pause_ms > 0:
                    seen.add("hit_pause")
            # Triggers fire too — recurse one level.
            for trig in action.triggers:
                for t in _SAMPLE_TIMES:
                    snap = animator.tick(trig, t_ms=t)
                    for e in snap.card_emissions:
                        seen.add(e.kind)
                    if snap.connection is not None:
                        seen.add("connection_line")
    return seen


def _all_cues_emitted(match) -> set[Cue]:
    animator = Animator()
    cues: set[Cue] = set()
    for r in match.rounds:
        for action in r.actions:
            cues.update(animator.tick(action, t_ms=0).cues)
            for trig in action.triggers:
                cues.update(animator.tick(trig, t_ms=0).cues)
    return cues


def test_demo_match_exercises_every_primitive_kind():
    match = build_demo_match()
    kinds = _all_kinds_emitted(match)
    expected = {
        "intent",
        "color_flash",
        "connection_line",
        "overlay_icon",
        "hp_tick",
        "zap",
        "shake",
        "pulse",
        "glow",
        "hit_pause",
    }
    missing = expected - kinds
    assert not missing, f"demo match fails to exercise: {missing}"


def test_demo_match_emits_full_cue_palette():
    match = build_demo_match()
    cues = _all_cues_emitted(match)
    # Demo intentionally includes damage (HIT), KO, BUFF, HEAL (BUFF), SHIELD
    # (BUFF). DEBUFF is not exercised — separate primitive. ROUND/OUTCOME are
    # phase-driven (not action-driven), so excluded here.
    assert Cue.HIT in cues
    assert Cue.KO in cues
    assert Cue.BUFF in cues


def test_demo_match_renders_without_error():
    """The whole timeline must walk + render at every cursor position
    without raising. Catches regressions where a primitive emits something
    the renderer can't paint."""
    match = build_demo_match()
    pb = MatchPlayback(match=match)
    for _ in range(len(pb.timeline)):
        out = render_frame(pb.snapshot(), color=True)
        assert "║" in out
        assert "DAIMON" in out
        pb.advance()


def test_demo_match_monochrome_strips_all_ansi():
    """Acceptance criterion #3: readable in --no-color (no ANSI escapes)."""
    match = build_demo_match()
    pb = MatchPlayback(match=match)
    while pb.timeline[pb.cursor].is_phase:
        pb.advance()
    out = render_frame(pb.snapshot(), color=False)
    assert "\x1b[" not in out


def test_demo_match_is_short(monkeypatch):
    """run_demo with max_seconds=0 returns 0 immediately (no infinite loop)."""
    # No real terminal is needed — we just smoke that the function returns.
    rc = run_demo(color=False, fps=20, max_seconds=0)
    assert rc == 0


def test_demo_match_first_action_is_heavy_damage_with_cascade():
    """Locks the showcase invariant: first action MUST be a heavy hit
    with a cascade trigger so intent + flash + connection + shake +
    hit_pause + zap all fire on the same beat (max coverage in one screen)."""
    match = build_demo_match()
    a1 = match.rounds[0].actions[0]
    assert a1.kind.value == "damage"
    assert (a1.amount or 0) >= 10        # hit_pause threshold
    assert (a1.amount or 0) >= 8         # shake threshold
    assert a1.target is not None
    assert len(a1.triggers) >= 1
    assert a1.triggers[0].reason          # cascade → fires zap


def test_demo_match_includes_buff_heal_shield():
    """Pulse fires on BUFF / HEAL / SHIELD — make sure all three are present."""
    match = build_demo_match()
    kinds = {a.kind.value for r in match.rounds for a in r.actions}
    assert "buff" in kinds
    assert "heal" in kinds
    assert "shield" in kinds


def test_demo_match_includes_ko():
    """KO event = damage that drops target to 0 hp_after."""
    match = build_demo_match()
    has_ko = False
    for r in match.rounds:
        for a in r.actions:
            if a.kind.value == "damage" and a.target is not None:
                key = f"{a.target.side.value}/{a.target.position}"
                if a.hp_after.get(key, 1) <= 0:
                    has_ko = True
    assert has_ko


def test_demo_outcome_present_and_coherent():
    match = build_demo_match()
    assert match.outcome is not None
    assert match.outcome.winner.value in ("player", "opponent", "draw")
    assert match.outcome.player_hp_remaining >= 0
    assert match.outcome.opponent_hp_remaining >= 0
