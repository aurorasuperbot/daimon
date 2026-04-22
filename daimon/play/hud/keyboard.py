"""Non-blocking single-key reader for the HUD.

Reads one keystroke at a time from stdin in cbreak (raw-ish) mode so the
event loop can poll without blocking. Arrow keys arrive as multi-byte
escape sequences (`ESC [ A` etc.) which we decode into stable Key names.

Designed for POSIX terminals (Linux, macOS). Windows is out of scope for V1.

Usage:

    with KeyboardReader() as kb:
        while running:
            key = kb.poll(timeout_ms=50)
            if key is not None:
                handle(key)

The context manager flips the terminal to cbreak mode and restores the
original termios attributes on exit (even if the body raises). Always use
the CM — leaving the terminal in cbreak across a crash makes the user's
shell unusable until they `reset`.
"""

from __future__ import annotations

import os
import select
import sys
from contextlib import contextmanager
from enum import Enum
from typing import Iterator, Optional


class Key(str, Enum):
    """Stable named keys the HUD cares about.

    Anything else returned as a single character via `Key.CHAR` callback —
    not modeled here; the reader returns the raw char and the loop ignores
    keys it doesn't bind.
    """
    SPACE = "space"
    ENTER = "enter"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    Q = "q"
    R = "r"
    N = "n"
    P = "p"
    ESC = "esc"


# ---------------------------------------------------------------------------
# Raw key decoding
# ---------------------------------------------------------------------------

def decode_key(seq: bytes) -> Optional[Key | str]:
    """Map a raw byte sequence to a Key or single character.

    Returns:
        - Key enum for known control keys
        - lowercase single char for printable ASCII letters/digits
        - None for anything we don't recognize (loop ignores it)
    """
    if not seq:
        return None

    # Single-byte
    if len(seq) == 1:
        b = seq[0]
        if b == 0x20:
            return Key.SPACE
        if b in (0x0d, 0x0a):
            return Key.ENTER
        if b == 0x1b:
            return Key.ESC
        if 0x20 < b < 0x7f:
            ch = chr(b).lower()
            if ch == "q": return Key.Q
            if ch == "r": return Key.R
            if ch == "n": return Key.N
            if ch == "p": return Key.P
            return ch
        return None

    # CSI escape (ESC [ X) — arrows
    if seq[:2] == b"\x1b[":
        if seq == b"\x1b[A": return Key.UP
        if seq == b"\x1b[B": return Key.DOWN
        if seq == b"\x1b[C": return Key.RIGHT
        if seq == b"\x1b[D": return Key.LEFT

    # Bare ESC at end of read also reaches us as 1-byte; handled above.
    return None


# ---------------------------------------------------------------------------
# Reader (POSIX cbreak mode)
# ---------------------------------------------------------------------------

class KeyboardReader:
    """Polling reader for stdin in cbreak mode. POSIX-only."""

    def __init__(self, fd: Optional[int] = None) -> None:
        self.fd = fd if fd is not None else sys.stdin.fileno()
        self._old_attrs = None

    def __enter__(self) -> "KeyboardReader":
        self._enter_cbreak()
        return self

    def __exit__(self, *exc) -> None:
        self._restore()

    def _enter_cbreak(self) -> None:
        """Switch terminal to cbreak (one-char, no-echo, no-canon) mode.

        Raises ImportError if termios isn't available (Windows).
        """
        import termios
        import tty
        self._old_attrs = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)

    def _restore(self) -> None:
        if self._old_attrs is None:
            return
        import termios
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self._old_attrs)
        self._old_attrs = None

    def poll(self, timeout_ms: int = 50) -> Optional[Key | str]:
        """Wait up to ``timeout_ms`` for a keystroke. None = no input."""
        timeout_s = max(0, timeout_ms) / 1000.0
        ready, _, _ = select.select([self.fd], [], [], timeout_s)
        if not ready:
            return None
        # Read all pending bytes (up to 8) so we capture multi-byte sequences
        # in one decode call.
        try:
            buf = os.read(self.fd, 8)
        except OSError:
            return None
        return decode_key(buf)


@contextmanager
def keyboard_reader_or_dummy(enabled: bool = True) -> Iterator[Optional[KeyboardReader]]:
    """Context manager that yields a real reader or None.

    Lets the app loop write `with keyboard_reader_or_dummy(enabled) as kb:`
    and check `if kb is None: ...` for headless modes (CI, --no-input).
    """
    if not enabled:
        yield None
        return
    try:
        with KeyboardReader() as kb:
            yield kb
    except Exception:
        # If termios fails (e.g. not a tty), degrade to no-input mode silently.
        yield None
