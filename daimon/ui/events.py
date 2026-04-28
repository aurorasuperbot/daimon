"""Event types for the daimon.ui input system.

A unified event model that wraps both keyboard and mouse activity. The
InputReader produces these; Screen.dispatch_event consumes them.

Modeled as plain dataclasses (not enum-of-classes or PEP 695 unions) so
isinstance checks stay readable and mypy is happy without excessive
Annotated/Union juggling.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MouseKind(str, Enum):
    PRESS = "press"
    RELEASE = "release"
    MOVE = "move"
    SCROLL_UP = "scroll_up"
    SCROLL_DOWN = "scroll_down"


@dataclass(frozen=True)
class KeyEvent:
    """A keyboard event.

    ``key`` is a stable string identifier:
      * Single ASCII char (lowercase) for printable keys: ``"a"`` … ``"z"``,
        ``"0"`` … ``"9"``, punctuation.
      * ``"enter"``, ``"space"``, ``"tab"``, ``"esc"``, ``"backspace"``.
      * Arrow keys: ``"up"``, ``"down"``, ``"left"``, ``"right"``.
      * Page navigation: ``"home"``, ``"end"``, ``"pgup"``, ``"pgdn"``.
      * Function keys: ``"f1"`` … ``"f12"``.

    Modifier keys aren't decoded individually — modified keys like Ctrl+C
    arrive as ``"ctrl+c"`` (lowercase + plus-separated).
    """

    key: str

    @property
    def is_printable(self) -> bool:
        return len(self.key) == 1 and self.key.isprintable()


@dataclass(frozen=True)
class MouseEvent:
    """A mouse event.

    Coordinates are 0-indexed cells (matching how Frame addresses things).
    Note that terminal SGR mouse reporting uses 1-indexed coords; the
    decoder converts. ``button`` is 1=left, 2=middle, 3=right; for scroll
    events it's 0 (use ``kind`` instead).
    """

    kind: MouseKind
    row: int
    col: int
    button: int = 0
    modifiers: int = 0  # bitmask: 1=shift, 2=alt, 4=ctrl

    @property
    def is_left_click(self) -> bool:
        return self.kind == MouseKind.PRESS and self.button == 1


@dataclass(frozen=True)
class ResizeEvent:
    """The terminal window changed size."""

    width: int
    height: int


# Sentinel for "no event" — callers check for None instead.
Event = KeyEvent | MouseEvent | ResizeEvent
