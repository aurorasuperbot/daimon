"""GameApp — top-level orchestrator: terminal lifecycle + tick loop + screen stack.

A GameApp owns:
  * The terminal screen state (alt buffer, cursor, KGP cleanup)
  * The InputReader (keyboard + mouse)
  * A stack of Screens (modals push, pop on quit)
  * The tick loop (drain events → dispatch → tick → render → sleep)

Subclasses generally aren't needed — instantiate with an initial Screen
and call ``run()``. Push/pop additional screens for modals from inside any
Screen via ``self._app.push_screen(...)``.
"""

from __future__ import annotations

import shutil
import signal
import sys
import threading
import time
from contextlib import contextmanager
from typing import List, Optional

from daimon.ui.events import KeyEvent, ResizeEvent
from daimon.ui.input import InputReader
from daimon.ui.screen import (
    CLEAR_SCREEN,
    CURSOR_HIDE,
    CURSOR_SHOW,
    ENTER_ALT_BUFFER,
    EXIT_ALT_BUFFER,
    RESET,
    Screen,
    _is_tty,
)


class GameApp:
    """Top-level app: owns the terminal + input + screen stack + tick loop."""

    def __init__(self,
                 initial_screen: Screen,
                 *,
                 tick_ms: int = 50,
                 enable_mouse: bool = True,
                 enable_alt_buffer: bool = True,
                 sink: Optional[object] = None,
                 ) -> None:
        self._screens: List[Screen] = [initial_screen]
        self._tick_ms = tick_ms
        self._enable_mouse = enable_mouse
        self._enable_alt_buffer = enable_alt_buffer
        self._sink = sink or sys.stdout
        self._stop = threading.Event()
        self._exit_code = 0
        self._reader: Optional[InputReader] = None
        # Wire the back-reference so screens can push/pop / quit the whole app.
        for s in self._screens:
            s._app = self

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push_screen(self, screen: Screen) -> None:
        """Push a screen onto the stack (modal pattern)."""
        screen._app = self
        if self._screens:
            self._screens[-1].on_unmount()
        self._screens.append(screen)
        screen.on_mount()
        # Force the new screen to repaint immediately.
        screen.refresh()

    def pop_screen(self) -> None:
        """Pop the top screen. If the stack empties, the app exits."""
        if not self._screens:
            return
        top = self._screens.pop()
        top.on_unmount()
        if self._screens:
            self._screens[-1].on_mount()
            self._screens[-1].refresh()
        else:
            self._stop.set()

    def quit(self, code: int = 0) -> None:
        """Stop the app on the next tick."""
        self._exit_code = code
        self._stop.set()

    def run(self) -> int:
        """Block until the app stops. Returns the exit code."""
        if not self._screens:
            return 0
        # Resolve initial size from the terminal.
        self._sync_size()
        # Mount the initial screen.
        self._screens[-1].on_mount()
        self._screens[-1].refresh()
        with self._terminal_session():
            with InputReader(enable_mouse=self._enable_mouse,
                             sink=self._sink) as reader:
                self._reader = reader
                try:
                    self._loop(reader)
                finally:
                    self._reader = None
        return self._exit_code

    @contextmanager
    def suspend(self):
        """Temporarily release the terminal so a child process can take over.

        Restores cooked input + main screen on entry, re-enters alt buffer +
        raw input on exit. Use it to wrap subprocess calls invoked from
        inside a Screen's action handler. Forces a full repaint on resume.
        """
        is_tty = _is_tty(self._sink)
        if self._reader is not None:
            self._reader.pause()
        if is_tty:
            try:
                self._sink.write(RESET + CURSOR_SHOW)
                if self._enable_alt_buffer:
                    self._sink.write(EXIT_ALT_BUFFER)
                self._sink.flush()
            except OSError:
                pass
        try:
            yield
        finally:
            if is_tty:
                try:
                    if self._enable_alt_buffer:
                        self._sink.write(ENTER_ALT_BUFFER)
                    self._sink.write(CURSOR_HIDE + CLEAR_SCREEN)
                    self._sink.flush()
                except OSError:
                    pass
            if self._reader is not None:
                self._reader.resume()
            if self._screens:
                self._screens[-1].refresh()

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def _loop(self, reader: InputReader) -> None:
        last_size_check = 0.0
        size_check_interval = 0.5  # seconds
        while not self._stop.is_set() and self._screens:
            top = self._screens[-1]

            # Drain events.
            for event in reader.drain():
                if isinstance(event, KeyEvent) and event.key == "ctrl+c":
                    self.quit()
                    break
                top.dispatch_event(event)
                if top.needs_quit():
                    break

            if self._stop.is_set():
                break

            # Periodic terminal size resync (cheap; covers manual resize).
            now = time.monotonic()
            if now - last_size_check > size_check_interval:
                last_size_check = now
                if self._sync_size():
                    top.on_resize(self.width, self.height)

            top.on_tick()
            top.render_to_terminal()

            # Pop the screen if it requested quit during this tick.
            if top.needs_quit():
                top._needs_quit = False
                self.pop_screen()
                continue

            # Sleep until the next tick.
            self._stop.wait(timeout=self._tick_ms / 1000.0)

    def _sync_size(self) -> bool:
        """Update self.width/height from the terminal. Return True if changed."""
        try:
            cols, rows = shutil.get_terminal_size(fallback=(150, 42))
        except OSError:
            cols, rows = 150, 42
        prev = getattr(self, "width", None), getattr(self, "height", None)
        self.width = cols
        self.height = rows
        # Propagate to current screen if size changed.
        if prev != (cols, rows):
            for screen in self._screens:
                screen.width = cols
                screen.height = rows
            return True
        return False

    # ------------------------------------------------------------------
    # Terminal lifecycle
    # ------------------------------------------------------------------

    @contextmanager
    def _terminal_session(self):
        """Enter alt buffer + hide cursor; restore on exit (even on crash)."""
        is_tty = _is_tty(self._sink)
        if is_tty:
            try:
                if self._enable_alt_buffer:
                    self._sink.write(ENTER_ALT_BUFFER)
                self._sink.write(CURSOR_HIDE + CLEAR_SCREEN)
                self._sink.flush()
            except OSError:
                pass
        # Make Ctrl+C break the loop instead of killing the process so the
        # finally block runs and restores the terminal.
        prev_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._on_sigint)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, prev_sigint)
            if is_tty:
                try:
                    self._sink.write(RESET + CURSOR_SHOW)
                    if self._enable_alt_buffer:
                        self._sink.write(EXIT_ALT_BUFFER)
                    self._sink.flush()
                except OSError:
                    pass

    def _on_sigint(self, signum, frame) -> None:
        self.quit()
