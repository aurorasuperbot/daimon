"""Screen base class — replaces the per-Runner boilerplate from each TUI.

A Screen is one full-screen UI: it owns a View dataclass (state), assembles
a root Widget tree on each render, and dispatches input events through a
BindingTable + hit regions.

The Screen pattern lets us extract the boilerplate that was copy-pasted
across collection_ui / shop_ui / loadout_editor / hud:

  * Enter / exit terminal screen state (cursor hide, alt buffer, KGP clear)
  * Signature-based render dedupe
  * KGP overlay paint after each text frame
  * Keyboard / mouse event dispatch
  * Quit + refresh + lifecycle hooks

Subclasses implement:
  * ``view``        — a dataclass holding the screen's reactive state
  * ``compose()``   — return the root Widget tree based on current view
  * ``signature()`` — a hashable that changes when render output should change
  * ``on_action(name, *, source)`` — handle resolved actions

Subclasses MAY override:
  * ``on_tick()``   — called every loop tick; useful for refreshes
  * ``on_mount()``  — called once after entering the screen
  * ``on_unmount()`` — called once before leaving the screen
"""

from __future__ import annotations

import sys
from typing import Any, List, Optional

from daimon.play.art_render import paint_overlays_as_kgp
from daimon.ui.bindings import BindingTable
from daimon.ui.events import Event, KeyEvent, MouseEvent, MouseKind, ResizeEvent
from daimon.ui.frame import Frame, HitRegion
from daimon.ui.widget import Widget


# ANSI control sequences — single source of truth.
HOME = "\x1b[H"
CLEAR_SCREEN = "\x1b[2J\x1b[H"
CURSOR_HIDE = "\x1b[?25l"
CURSOR_SHOW = "\x1b[?25h"
RESET = "\x1b[0m"
ENTER_ALT_BUFFER = "\x1b[?1049h"
EXIT_ALT_BUFFER = "\x1b[?1049l"


class Screen:
    """Base class for full-screen UIs.

    Subclasses set ``bindings`` as a class attribute (BindingTable) and
    implement ``compose()`` returning the root Widget. The framework
    handles event dispatch, render dedupe, terminal lifecycle.
    """

    bindings: BindingTable = BindingTable({})

    def __init__(self,
                 *,
                 width: int = 150,
                 height: int = 42,
                 sink: Optional[object] = None) -> None:
        self.width = width
        self.height = height
        self._sink = sink or sys.stdout
        self._last_signature: Any = object()  # sentinel — guaranteed mismatch
        self._last_hit_regions: List[HitRegion] = []
        self._needs_quit = False
        self._app: Optional["GameApp"] = None  # forward ref; GameApp sets this

    # ------------------------------------------------------------------
    # Subclass hooks (override in your screen)
    # ------------------------------------------------------------------

    def compose(self) -> Widget:
        """Return the root Widget. Called every render — keep it fast."""
        raise NotImplementedError(
            f"{type(self).__name__} must override compose()"
        )

    def signature(self) -> Any:
        """Hashable that changes when the rendered output should change.

        Default returns a fresh object on every call → renders every tick.
        Override with a tuple of view fields to enable signature-based
        dedupe (matching the pattern in collection_ui.py:738–743).
        """
        return object()

    def on_action(self,
                  name: str,
                  *,
                  source: Optional[HitRegion] = None) -> None:
        """Handle a resolved action (from binding lookup or hit region click)."""
        # Built-in actions — subclasses can override but must call super().
        if name == "quit":
            self.quit()

    def on_tick(self) -> None:
        """Called every loop tick. Override for animations, polling, etc."""
        pass

    def on_mount(self) -> None:
        """Called once when the screen first becomes active."""
        pass

    def on_unmount(self) -> None:
        """Called once when the screen leaves the stack."""
        pass

    def on_resize(self, width: int, height: int) -> None:
        """Called when the terminal is resized."""
        self.width = width
        self.height = height
        self._last_signature = object()  # force redraw

    # ------------------------------------------------------------------
    # Public API for subclasses
    # ------------------------------------------------------------------

    def quit(self) -> None:
        """Request that this screen be popped (or the whole app exit)."""
        self._needs_quit = True

    def refresh(self) -> None:
        """Force a redraw on the next tick."""
        self._last_signature = object()

    def notify(self, message: str) -> None:
        """Subclasses can override to flash a message; default is no-op."""
        pass

    # ------------------------------------------------------------------
    # Framework methods (called by GameApp; don't override)
    # ------------------------------------------------------------------

    def needs_quit(self) -> bool:
        return self._needs_quit

    def dispatch_event(self, event: Event) -> None:
        if isinstance(event, KeyEvent):
            binding = self.bindings.lookup(event.key)
            if binding is not None:
                self.on_action(binding.action)
            else:
                # Subclasses can override on_unhandled_key for custom logic.
                self.on_unhandled_key(event)
        elif isinstance(event, MouseEvent):
            if event.kind == MouseKind.PRESS and event.button == 1:
                # Find the topmost hit region containing the click.
                for hr in reversed(self._last_hit_regions):
                    if hr.contains(event.row, event.col):
                        self.on_action(hr.action, source=hr)
                        return
                self.on_unhandled_click(event)
            else:
                self.on_mouse(event)
        elif isinstance(event, ResizeEvent):
            self.on_resize(event.width, event.height)

    def on_unhandled_key(self, event: KeyEvent) -> None:
        """Override for keys not in the BindingTable."""
        pass

    def on_unhandled_click(self, event: MouseEvent) -> None:
        """Override for clicks outside any HitRegion."""
        pass

    def on_mouse(self, event: MouseEvent) -> None:
        """Override for non-press mouse events (move, scroll, release)."""
        pass

    def render_to_terminal(self, *, force: bool = False) -> bool:
        """Render the screen if its signature changed. Returns True if drawn."""
        sig = self.signature()
        if not force and sig == self._last_signature:
            return False
        self._last_signature = sig
        root = self.compose()
        frame = root.render(self.width, self.height)
        # Defensive — never let a bad widget produce a wrong-size frame.
        from daimon.ui.layout import _coerce_size
        frame = _coerce_size(frame, self.width, self.height)
        self._last_hit_regions = list(frame.hit_regions)
        text = HOME + frame.render_text()
        if _is_tty(self._sink):
            text += paint_overlays_as_kgp(frame.overlays)
        try:
            self._sink.write(text)
            self._sink.flush()
        except OSError:
            pass
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_tty(sink) -> bool:
    try:
        return bool(sink.isatty())
    except Exception:
        return False
