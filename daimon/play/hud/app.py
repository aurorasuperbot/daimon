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

from daimon.mining import buffer as _mine_buffer
from daimon.play.hud.keyboard import Key, keyboard_reader_or_dummy
from daimon.play.hud.playback import (
    END_COOLDOWN_MS,
    MatchPlayback,
    PlaybackStatus,
)
from daimon.play.hud.render import render_frame, render_idle, render_mining_strip
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

# How many mine_buffer entries to surface in the idle pane / strip.
MINE_TICKS_SIZE = 8


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
    # Optional override for the mining-buffer path. Defaults to the buffer
    # module's ``BUFFER_PATH`` (resolved at every poll so tests can
    # monkeypatch the module-level path mid-test).
    buffer_path: Optional[Path | str] = None
    # Test seam — return current monotonic ms so tests can advance time.
    clock_ms: Callable[[], int] = field(
        default_factory=lambda: (lambda: int(time.monotonic() * 1000))
    )

    # ----- runtime state -----
    _resolved_state_path: Path = field(init=False)
    _playback: Optional[MatchPlayback] = field(default=None, init=False)
    _last_state_id: Optional[str] = field(default=None, init=False)
    _recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_LOG_SIZE), init=False)
    # Last N mine_buffer entries — refreshed lazily on mtime change.
    _mine_ticks: list = field(default_factory=list, init=False)
    _last_buffer_mtime_ns: int = field(default=0, init=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _last_tick_ms: int = field(default=0, init=False)
    _observer: object = field(default=None, init=False)
    _last_rendered_signature: Optional[tuple] = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._resolved_state_path = resolve_state_path(self.state_path)
        # Force the sink to UTF-8 so the box-drawing chars / arrows / emoji
        # in rendered frames don't crash on Windows where stdout defaults
        # to cp1252. We try reconfigure() first (cheap, stays on the same
        # underlying stream), then fall back to wrapping the raw buffer
        # with a fresh UTF-8 TextIOWrapper. Last resort: leave the sink
        # alone and let _safe_write fall back at write time.
        sink = self.sink
        if hasattr(sink, "reconfigure"):
            try:
                sink.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                buf = getattr(sink, "buffer", None)
                if buf is not None:
                    try:
                        import io as _io
                        self.sink = _io.TextIOWrapper(
                            buf, encoding="utf-8", line_buffering=True
                        )
                    except (OSError, ValueError):
                        pass
        self._reseed_recent_from_buffer()

    def _safe_write(self, text: str) -> None:
        """Write to ``self.sink`` with a UTF-8 byte fallback.

        Last line of defense for Windows consoles where the TextIOWrapper
        encoding can't be flipped via reconfigure (rare, but seen in
        bundled-WezTerm subprocesses). A failed write would propagate as
        a UnicodeEncodeError out of HudApp.run() and exit the HUD with
        rc=1; the byte fallback keeps the loop alive even if a few glyphs
        get mangled.
        """
        try:
            self.sink.write(text)
        except UnicodeEncodeError:
            buf = getattr(self.sink, "buffer", None)
            if buf is not None:
                try:
                    buf.write(text.encode("utf-8", errors="replace"))
                    return
                except OSError:
                    pass
            self.sink.write(text.encode("ascii", errors="replace").decode("ascii"))

    def _reseed_recent_from_buffer(self) -> None:
        """Populate ``_recent`` from the last match/pull entries in mine_buffer.

        Best-effort: any read failure leaves ``_recent`` empty (default for
        a fresh install). Scans further back than ``RECENT_LOG_SIZE`` so we
        don't miss matches behind a flurry of mining ticks.
        """
        path = self._resolved_buffer_path()
        try:
            # Scan ~10x further back than the recent-pane size — match/pull
            # events are sparse compared to mine ticks, so a window of e.g.
            # 80 buffer entries typically yields 5+ matches.
            tail = _mine_buffer.tail(RECENT_LOG_SIZE * 16, path=path)
        except Exception:  # noqa: BLE001
            return
        for entry in tail:
            kind = entry.get("kind")
            if kind == "match":
                # publish.py format: note="vs <opp> (<outcome>)", extra={"opponent","outcome","state_id"}
                opp = entry.get("opponent") or "?"
                outcome = entry.get("outcome") or "?"
                state_id = entry.get("state_id") or ""
                short = state_id.split("_")[-1][:6] if state_id else "?"
                self._recent.appendleft(f"{short}  vs {opp}  ({outcome})")
            elif kind == "pull":
                card_id = entry.get("card_id") or "?"
                rarity = entry.get("rarity") or "?"
                self._recent.appendleft(f"PULL  {card_id}  [{rarity}]")

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
        """One pass: drain new state + mining buffer, advance playback, handle keys, render."""
        # 1) Pick up any new state.json content.
        self._poll_state()

        # 1b) Refresh mining-buffer tail when the file changed.
        self._poll_mining_buffer()

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
        # New state arrived. Dispatch by view:
        #   match  → load into playback (full animation)
        #   pull   → log to recent-activity (V1 reveal-overlay is a TODO;
        #            see docs/animation_design.md). At minimum the user must
        #            see SOMETHING when the agent calls dm_pull while
        #            `daimon play` is open — the recent-activity stream
        #            satisfies the "terminal reflects MCP-driven events"
        #            contract until the reveal-overlay lands.
        #   others (collection, loadout, inspect, leaderboard, rank, idle)
        #          → log only; richer renderers belong to play-render.
        if state.view == "match":
            self._load_match(state)
        elif state.view == "pull":
            self._log_pull(state)
        else:
            self._log_misc(state)
        self._last_state_id = state.id

    # ----- mining-buffer polling -----

    def _resolved_buffer_path(self) -> Path:
        """Resolve the mine_buffer.jsonl path on every call.

        Reads ``_mine_buffer.BUFFER_PATH`` lazily so tests that monkeypatch
        the module-level path mid-test pick up the change without having
        to reconstruct the HudApp.
        """
        if self.buffer_path is not None:
            return Path(self.buffer_path).expanduser()
        return _mine_buffer.BUFFER_PATH

    def _poll_mining_buffer(self) -> None:
        """Refresh ``_mine_ticks`` if the buffer file has changed since last tick.

        Cheap: one stat() per HUD tick (50ms). Only re-tails on mtime change,
        so a 1000-entry buffer only ever costs O(MINE_TICKS_SIZE) on update.
        """
        path = self._resolved_buffer_path()
        cur_mtime = _mine_buffer.mtime_ns(path=path)
        if cur_mtime == 0:
            # No file yet — nothing to do. Don't reset _mine_ticks; if the
            # buffer file is later created, the next tick will see the new
            # mtime and refresh.
            return
        if cur_mtime == self._last_buffer_mtime_ns:
            return
        self._last_buffer_mtime_ns = cur_mtime
        try:
            self._mine_ticks = _mine_buffer.tail(MINE_TICKS_SIZE, path=path)
        except Exception as e:  # noqa: BLE001 — never let HUD die on a chrome read
            logger.warning("mine_buffer tail failed: %s", e)

    def _log_pull(self, state: GameState) -> None:
        """Surface a pull event in the recent-activity log."""
        d = state.data or {}
        card_id = d.get("card_id", "?")
        rarity = d.get("rarity", "?")
        self._recent.appendleft(f"PULL  {card_id}  [{rarity}]")

    def _log_misc(self, state: GameState) -> None:
        """Surface other view types in the recent-activity log."""
        self._recent.appendleft(f"{state.view.upper():5s} {state.id}")

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

        # Mining buffer lives in CONFIG_DIR — usually the same parent dir
        # as state.json. We watch the same dir and dispatch on which file
        # was hit. If they live in different dirs (test override), we add
        # a second schedule below.
        buffer_target = self._resolved_buffer_path()
        buffer_target.parent.mkdir(parents=True, exist_ok=True)

        outer = self

        class _Handler(FileSystemEventHandler):
            def _is_state(self, path: str) -> bool:
                try:
                    return Path(path) == target
                except (ValueError, OSError):
                    return False

            def _is_buffer(self, path: str) -> bool:
                try:
                    return Path(path) == buffer_target
                except (ValueError, OSError):
                    return False

            def _dispatch(self, path: str) -> None:
                if self._is_state(path):
                    outer._poll_state()
                elif self._is_buffer(path):
                    outer._poll_mining_buffer()

            def on_created(self, event) -> None:
                if not event.is_directory:
                    self._dispatch(event.src_path)

            def on_modified(self, event) -> None:
                if not event.is_directory:
                    self._dispatch(event.src_path)

            def on_moved(self, event) -> None:
                if not event.is_directory:
                    self._dispatch(getattr(event, "dest_path", ""))

        obs = Observer()
        handler = _Handler()
        obs.schedule(handler, str(target.parent), recursive=False)
        # If the buffer lives in a different dir (test override), schedule
        # the same handler against that dir too so both files surface events.
        if buffer_target.parent != target.parent:
            obs.schedule(handler, str(buffer_target.parent), recursive=False)
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
        self._safe_write(CURSOR_HIDE + CLEAR_SCREEN)
        self.sink.flush()

    def _exit_screen(self) -> None:
        if not self._is_tty():
            return
        try:
            self._safe_write(RESET_ATTRS + CURSOR_SHOW + "\n")
            self.sink.flush()
        except Exception:
            pass

    def _is_tty(self) -> bool:
        try:
            return self.sink.isatty()
        except Exception:
            return False

    def _render(self) -> None:
        # Stable signature for cheap dedupe — last mine tick id is enough,
        # since ticks always grow monotonically.
        ticks = list(self._mine_ticks)
        last_tick_sig = (
            (ticks[-1].get("ts"), ticks[-1].get("kind"), ticks[-1].get("amount"))
            if ticks else None
        )

        if self._playback is None:
            screen = render_idle(
                recent=list(self._recent),
                mine_ticks=ticks,
                color=self.color,
            )
            sig = ("idle", tuple(self._recent), last_tick_sig)
        else:
            frame = self._playback.snapshot()
            frame_screen = render_frame(frame, color=self.color)
            # Bottom-of-screen mining ticker — one extra row under the box.
            # Always painted (even with no ticks yet) so the terminal layout
            # height stays constant whether mining is active or quiet.
            strip = render_mining_strip(
                ticks[-1] if ticks else None,
                color=self.color,
            )
            screen = frame_screen + "\n" + strip
            sig = (
                "match",
                self._playback.state_id,
                frame.cursor,
                frame.status.value,
                round(frame.speed, 3),
                last_tick_sig,
            )
        # Skip painting if nothing visible has changed since last paint.
        if sig == self._last_rendered_signature:
            return
        self._last_rendered_signature = sig
        if self._is_tty():
            self._safe_write(HOME + screen + "\n")
        else:
            self._safe_write(screen + "\n")
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
