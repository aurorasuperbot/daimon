"""Spectator HUD app — `daimon play`.

Top-level event loop that ties together:

  - watchdog observer over ``state.json``  (or polling fallback)
  - playback engine (timeline cursor + state machine)
  - ASCII renderer
  - non-blocking keyboard

The app is the only module here that does I/O. ``playback`` and ``render``
are pure logic; this module orchestrates them.

Lifecycle:

    HudApp(state_path, sink=stdout, ...).run()

Exits cleanly on `q`, `ESC`, SIGINT, or when ``stop_event`` is set
externally (used by tests). The terminal is always restored to its prior
state via the keyboard reader's context manager + an unconditional
ANSI cursor-show + screen-attribute-reset on exit.

Headless mode: pass ``poll_only=True`` to skip watchdog. Tests use this
to drive the app via direct ``HudApp.tick()`` calls without spinning the
observer thread.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TextIO

from daimon.play.hud.keyboard import Key, keyboard_reader_or_dummy
from daimon.play.hud.playback import (
    END_COOLDOWN_MS,
    MatchPlayback,
    PlaybackStatus,
)
from daimon.play.hud.render import render_frame, render_idle
from daimon.play.schema import Match
from daimon.play.state import GameState, read_state, resolve_state_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ANSI screen helpers
# ---------------------------------------------------------------------------

CURSOR_HIDE = "\x1b[?25l"
CURSOR_SHOW = "\x1b[?25h"
CLEAR_SCREEN = "\x1b[2J\x1b[H"   # full clear + home
HOME = "\x1b[H"                  # cursor home (top-left)
RESET_ATTRS = "\x1b[0m"


# ---------------------------------------------------------------------------
# Recent-matches log
# ---------------------------------------------------------------------------

RECENT_LOG_SIZE = 5


@dataclass
class HudApp:
    """Spectator HUD event loop.

    Args:
        state_path: Override state.json location.
        sink: Where to write rendered frames (default stdout).
        color: Emit ANSI color in renders. False for tests/CI logs.
        keyboard_enabled: False to disable keyboard reader (CI / pipes).
        tick_ms: Loop tick interval. Lower = smoother but more CPU.
        autoplay: Start matches in PLAYING state. False starts PAUSED.
        poll_only: Skip watchdog; only re-read state.json on tick. Useful
            for tests + non-watchdog environments.
        max_ticks: Run at most this many ticks then return. 0 = forever.
            Test-only.
    """
    state_path: Optional[Path | str] = None
    sink: TextIO = field(default_factory=lambda: sys.stdout)
    color: bool = True
    keyboard_enabled: bool = True
    tick_ms: int = 50
    autoplay: bool = True
    poll_only: bool = False
    max_ticks: int = 0
    # Test seam — return current monotonic ms so tests can advance time.
    clock_ms: Callable[[], int] = field(
        default_factory=lambda: (lambda: int(time.monotonic() * 1000))
    )

    # ----- runtime state -----
    _resolved_state_path: Path = field(init=False)
    _playback: Optional[MatchPlayback] = field(default=None, init=False)
    _last_state_id: Optional[str] = field(default=None, init=False)
    _recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_LOG_SIZE), init=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _last_tick_ms: int = field(default=0, init=False)
    _observer: object = field(default=None, init=False)
    _last_rendered_signature: Optional[tuple] = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._resolved_state_path = resolve_state_path(self.state_path)

    # ----- lifecycle -----

    def request_stop(self) -> None:
        """Signal the loop to exit at next iteration. Idempotent."""
        self._stop_event.set()

    def run(self) -> int:
        """Block until exit. Returns 0 on clean stop, 1 on error.

        Sets up keyboard + watchdog (unless ``poll_only``), drives ticks
        until ``q``/``ESC``/SIGINT/``stop_event``, then tears down.
        """
        # SIGINT handler restores state cleanly. Loop polls `_stop_event`.
        prev_handler = signal.getsignal(signal.SIGINT)

        def _on_sigint(_sig, _frame):
            self.request_stop()

        signal.signal(signal.SIGINT, _on_sigint)
        rc = 0
        try:
            self._enter_screen()
            with keyboard_reader_or_dummy(self.keyboard_enabled) as kb:
                self._spin_watchdog_if_needed()
                self._last_tick_ms = self.clock_ms()
                ticks = 0
                while not self._stop_event.is_set():
                    self._tick_once(kb)
                    ticks += 1
                    if self.max_ticks and ticks >= self.max_ticks:
                        break
                    self._stop_event.wait(timeout=self.tick_ms / 1000.0)
        except Exception:
            logger.exception("HudApp crashed")
            rc = 1
        finally:
            self._teardown_watchdog()
            self._exit_screen()
            signal.signal(signal.SIGINT, prev_handler)
        return rc

    # ----- single tick (also the test entry point) -----

    def _tick_once(self, kb) -> None:
        """One pass: drain new state, advance playback, handle keys, render."""
        # 1) Pick up any new state.json content.
        self._poll_state()

        # 2) Drain keyboard (process all pending keys this tick).
        if kb is not None:
            for _ in range(8):    # cap so a key-flood doesn't stall the loop
                key = kb.poll(timeout_ms=0)
                if key is None:
                    break
                self._handle_key(key)

        # 3) Advance playback by elapsed ms.
        now = self.clock_ms()
        elapsed = max(0, now - self._last_tick_ms)
        self._last_tick_ms = now
        if self._playback is not None:
            self._playback.step(elapsed)
            # When ENDED has held long enough, fall back to IDLE (renders
            # show "waiting for match" until a new state arrives).
            if (self._playback.status == PlaybackStatus.ENDED
                    and self._playback.ended_dwell_ms >= END_COOLDOWN_MS):
                self._unload_playback()

        # 4) Render.
        self._render()

    # ----- keyboard -----

    def _handle_key(self, key) -> None:
        if key in (Key.Q, Key.ESC):
            self.request_stop()
            return
        if self._playback is None:
            # Idle screen — only quit + (eventually) demo are relevant.
            return
        if key == Key.SPACE or key == Key.P:
            self._playback.toggle_pause()
        elif key == Key.RIGHT:
            self._playback.pause()
            self._playback.advance()
        elif key == Key.LEFT:
            self._playback.pause()
            self._playback.back()
        elif key == Key.UP:
            self._playback.speed_up()
        elif key == Key.DOWN:
            self._playback.speed_down()
        elif key == Key.R:
            self._playback.restart()
        elif key == Key.N:
            self._playback.jump_to_end()

    # ----- state polling -----

    def _poll_state(self) -> None:
        try:
            state = read_state(self._resolved_state_path)
        except ValueError as e:
            logger.warning("state.json malformed: %s", e)
            return
        if state is None:
            return
        if state.id == self._last_state_id:
            return
        # New state arrived; if it's a match, swap; else ignore for now (other
        # views aren't part of HUD V1 — they belong to the still-frame
        # GameTerminal renderer).
        if state.view == "match":
            self._load_match(state)
        self._last_state_id = state.id

    def _load_match(self, state: GameState) -> None:
        try:
            match = Match.model_validate(state.data)
        except Exception as e:    # noqa: BLE001 — guard against any pydantic raise
            logger.warning("match payload invalid: %s", e)
            return
        self._playback = MatchPlayback(
            match=match,
            state_id=state.id,
        )
        if not self.autoplay:
            self._playback.pause()
        opp = match.participants.get("opponent")
        opp_name = opp.name if opp else "?"
        winner_value = match.outcome.winner.value
        outcome_str = "draw" if winner_value == "draw" else f"{winner_value} won"
        self._recent.appendleft(
            f"{match.match_id}  vs {opp_name}  ({outcome_str})"
        )

    def _unload_playback(self) -> None:
        self._playback = None

    # ----- watchdog -----

    def _spin_watchdog_if_needed(self) -> None:
        if self.poll_only:
            return
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.info("watchdog unavailable; falling back to poll-only mode")
            return

        target = self._resolved_state_path
        target.parent.mkdir(parents=True, exist_ok=True)

        outer = self

        class _Handler(FileSystemEventHandler):
            def _hit(self, path: str) -> bool:
                try:
                    return Path(path) == target
                except (ValueError, OSError):
                    return False

            def on_created(self, event) -> None:
                if not event.is_directory and self._hit(event.src_path):
                    outer._poll_state()

            def on_modified(self, event) -> None:
                if not event.is_directory and self._hit(event.src_path):
                    outer._poll_state()

            def on_moved(self, event) -> None:
                if not event.is_directory and self._hit(getattr(event, "dest_path", "")):
                    outer._poll_state()

        obs = Observer()
        obs.schedule(_Handler(), str(target.parent), recursive=False)
        obs.start()
        self._observer = obs

    def _teardown_watchdog(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:
                pass
            self._observer = None

    # ----- render -----

    def _enter_screen(self) -> None:
        if not self._is_tty():
            return
        self.sink.write(CURSOR_HIDE + CLEAR_SCREEN)
        self.sink.flush()

    def _exit_screen(self) -> None:
        if not self._is_tty():
            return
        try:
            self.sink.write(RESET_ATTRS + CURSOR_SHOW + "\n")
            self.sink.flush()
        except Exception:
            pass

    def _is_tty(self) -> bool:
        try:
            return self.sink.isatty()
        except Exception:
            return False

    def _render(self) -> None:
        if self._playback is None:
            screen = render_idle(recent=list(self._recent), color=self.color)
            sig = ("idle", tuple(self._recent))
        else:
            frame = self._playback.snapshot()
            screen = render_frame(frame, color=self.color)
            sig = (
                "match",
                self._playback.state_id,
                frame.cursor,
                frame.status.value,
                round(frame.speed, 3),
            )
        # Skip painting if nothing visible has changed since last paint.
        if sig == self._last_rendered_signature:
            return
        self._last_rendered_signature = sig
        if self._is_tty():
            self.sink.write(HOME + screen + "\n")
        else:
            self.sink.write(screen + "\n")
        try:
            self.sink.flush()
        except Exception:
            pass

    # ----- test seams -----

    def force_load_match(self, match: Match, *, state_id: str = "test") -> None:
        """Inject a Match payload directly. Test-only."""
        self._playback = MatchPlayback(match=match, state_id=state_id)
        if not self.autoplay:
            self._playback.pause()

    @property
    def playback(self) -> Optional[MatchPlayback]:
        return self._playback

    @property
    def recent(self) -> tuple[str, ...]:
        return tuple(self._recent)


# ---------------------------------------------------------------------------
# CLI entry helper (called by daimon.cli)
# ---------------------------------------------------------------------------

def run_play(
    *,
    state_path: Optional[Path | str] = None,
    color: bool = True,
    autoplay: bool = True,
    no_input: bool = False,
    tick_ms: int = 50,
) -> int:
    """Wrapper used by `daimon play`. Returns process exit code."""
    app = HudApp(
        state_path=state_path,
        color=color,
        keyboard_enabled=not no_input,
        autoplay=autoplay,
        tick_ms=tick_ms,
    )
    return app.run()
