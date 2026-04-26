"""Spectator HUD — `daimon play`.

Live ASCII window that watches `~/.config/daimon/state.json` and walks
through any new match payload action-by-action with player controls
(pause / scrub / speed / restart / skip-to-end / quit).

Modules:
    playback : pure timeline + cursor state machine
    render   : pure ASCII frame renderer
    keyboard : POSIX cbreak non-blocking key reader
    app      : I/O orchestration — watchdog + render loop + keyboard

Public entry point: ``run_play`` (used by ``daimon play`` CLI).
"""

from daimon.play.hud.app import HudApp, run_play
from daimon.play.hud.playback import (
    MatchPlayback,
    Phase,
    PlaybackStatus,
    SPEED_LADDER,
    Step,
    flatten_match,
    hp_at,
)
from daimon.play.hud.render import render_frame, render_idle, render_mining_strip

__all__ = [
    "HudApp",
    "run_play",
    "MatchPlayback",
    "Phase",
    "PlaybackStatus",
    "SPEED_LADDER",
    "Step",
    "flatten_match",
    "hp_at",
    "render_frame",
    "render_idle",
    "render_mining_strip",
]
