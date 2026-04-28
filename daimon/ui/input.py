"""Cross-platform input reader — keyboard + SGR mouse.

Provides :class:`InputReader` as a context manager that:

  1. Switches the terminal into raw / virtual-terminal-input mode so
     escape sequences arrive uninterpreted.
  2. Enables SGR mouse reporting (CSI ?1000h ?1006h) so clicks + scrolls
     come through as parseable escape sequences.
  3. Spawns a background thread reading bytes from stdin, decoding them
     into :class:`KeyEvent` / :class:`MouseEvent`, and pushing them onto
     a queue.
  4. Exposes :meth:`drain` (non-blocking) and :meth:`poll` (with timeout)
     so the main event loop can pull events without blocking on I/O.

POSIX uses ``termios`` + ``select`` (same approach as the existing HUD
keyboard reader). Windows uses ``ctypes`` to call ``SetConsoleMode`` and
enable virtual terminal input — modern Windows + WezTerm/ConPTY then
deliver ANSI escape sequences through stdin just like POSIX, so the
decoder is shared.

If the underlying terminal doesn't support raw mode (e.g., not a TTY,
testing environment), the reader degrades to no-op silently — callers
get an empty ``drain()`` and the app stays alive.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from typing import List, Optional

from daimon.ui.events import Event, KeyEvent, MouseEvent, MouseKind


# ---------------------------------------------------------------------------
# Mouse mode — SGR (1006) gives row/col without the limits of legacy
# X10 mouse mode (which capped at column 223). All modern terminals
# (xterm, WezTerm, kitty, alacritty) support 1006.
# ---------------------------------------------------------------------------

_ENABLE_MOUSE = "\x1b[?1000h\x1b[?1003h\x1b[?1006h"
_DISABLE_MOUSE = "\x1b[?1006l\x1b[?1003l\x1b[?1000l"


# ---------------------------------------------------------------------------
# Decoder — bytes → Event
# ---------------------------------------------------------------------------


def _decode_csi_sgr_mouse(seq: bytes) -> Optional[MouseEvent]:
    """Decode an SGR mouse escape: ``ESC [ < button ; col ; row M|m``.

    ``M`` = press / move, ``m`` = release. The button code carries
    modifiers in its upper bits (bit 4 = motion, bit 5 = wheel, bit 6
    = unused, bit 7 = wheel direction) per xterm protocol.
    """
    if not seq.startswith(b"\x1b[<"):
        return None
    final = seq[-1:]
    if final not in (b"M", b"m"):
        return None
    body = seq[3:-1].decode("ascii", errors="replace")
    parts = body.split(";")
    if len(parts) != 3:
        return None
    try:
        b_code = int(parts[0])
        col = int(parts[1])
        row = int(parts[2])
    except ValueError:
        return None

    is_release = final == b"m"
    is_motion = bool(b_code & 0x20)
    is_wheel = bool(b_code & 0x40)
    button_id = b_code & 0x03  # 0=left, 1=middle, 2=right

    if is_wheel:
        kind = MouseKind.SCROLL_DOWN if button_id & 0x01 else MouseKind.SCROLL_UP
        return MouseEvent(kind=kind, row=row - 1, col=col - 1, button=0)
    if is_motion:
        return MouseEvent(
            kind=MouseKind.MOVE, row=row - 1, col=col - 1, button=button_id + 1
        )
    if is_release:
        return MouseEvent(
            kind=MouseKind.RELEASE, row=row - 1, col=col - 1,
            button=button_id + 1,
        )
    return MouseEvent(
        kind=MouseKind.PRESS, row=row - 1, col=col - 1, button=button_id + 1
    )


def _decode_key(seq: bytes) -> Optional[KeyEvent]:
    """Decode a single keyboard escape sequence or printable byte."""
    if not seq:
        return None
    # Single byte
    if len(seq) == 1:
        b = seq[0]
        if b == 0x0d or b == 0x0a:
            return KeyEvent("enter")
        if b == 0x20:
            return KeyEvent("space")
        if b == 0x09:
            return KeyEvent("tab")
        if b == 0x7f or b == 0x08:
            return KeyEvent("backspace")
        if b == 0x1b:
            return KeyEvent("esc")
        # Ctrl+letter (1..26 maps to ctrl+a..z) but skip values already
        # claimed above (enter=13, tab=9).
        if 1 <= b <= 26 and b not in (0x09, 0x0a, 0x0d):
            return KeyEvent(f"ctrl+{chr(b + 0x60)}")
        if 0x20 < b < 0x7f:
            return KeyEvent(chr(b).lower())
        return None

    # CSI escape: ESC [ X
    if seq[:2] == b"\x1b[":
        # Arrows + named keys
        tail = seq[2:]
        if tail == b"A": return KeyEvent("up")
        if tail == b"B": return KeyEvent("down")
        if tail == b"C": return KeyEvent("right")
        if tail == b"D": return KeyEvent("left")
        if tail == b"H": return KeyEvent("home")
        if tail == b"F": return KeyEvent("end")
        if tail == b"5~": return KeyEvent("pgup")
        if tail == b"6~": return KeyEvent("pgdn")
        if tail == b"3~": return KeyEvent("delete")
        # F-keys (xterm style: ESC [ 1 1 ~ … ESC [ 2 4 ~ for F1..F12 mostly)
        if tail.endswith(b"~"):
            num = tail[:-1].decode("ascii", errors="replace")
            try:
                code = int(num)
                fmap = {15: 5, 17: 6, 18: 7, 19: 8, 20: 9, 21: 10,
                        23: 11, 24: 12}
                if code in fmap:
                    return KeyEvent(f"f{fmap[code]}")
            except ValueError:
                pass

    # SS3 escape (some terminals): ESC O X
    if seq[:2] == b"\x1bO":
        tail = seq[2:]
        if tail == b"P": return KeyEvent("f1")
        if tail == b"Q": return KeyEvent("f2")
        if tail == b"R": return KeyEvent("f3")
        if tail == b"S": return KeyEvent("f4")

    return None


def decode_sequence(seq: bytes) -> Optional[Event]:
    """Top-level decoder — try mouse first, then key."""
    if seq.startswith(b"\x1b[<"):
        return _decode_csi_sgr_mouse(seq)
    return _decode_key(seq)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class _Backend:
    """Abstract input backend — POSIX / Windows variants below."""

    def __enter__(self) -> "_Backend":
        raise NotImplementedError

    def __exit__(self, *exc) -> None:
        raise NotImplementedError

    def read_bytes(self, max_n: int = 64,
                   timeout_s: float = 0.05) -> bytes:
        """Read up to max_n bytes, blocking up to timeout_s. b'' on timeout."""
        raise NotImplementedError


class _PosixBackend(_Backend):
    """termios cbreak + select-based read."""

    def __init__(self) -> None:
        self._fd = sys.stdin.fileno()
        self._old_attrs = None

    def __enter__(self) -> "_PosixBackend":
        try:
            import termios
            import tty
            self._old_attrs = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        except (ImportError, OSError):
            self._old_attrs = None
        return self

    def __exit__(self, *exc) -> None:
        if self._old_attrs is None:
            return
        try:
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attrs)
        except (ImportError, OSError):
            pass
        self._old_attrs = None

    def read_bytes(self, max_n: int = 64,
                   timeout_s: float = 0.05) -> bytes:
        try:
            import select
            ready, _, _ = select.select([self._fd], [], [], timeout_s)
            if not ready:
                return b""
            return os.read(self._fd, max_n)
        except (OSError, ImportError):
            return b""


class _WindowsBackend(_Backend):
    """ConPTY-aware Windows backend.

    Uses ctypes to enable ENABLE_VIRTUAL_TERMINAL_INPUT on the stdin handle
    so escape sequences (arrows, mouse, etc.) flow through as bytes — the
    same bytes a POSIX terminal would produce. ENABLE_LINE_INPUT and
    ENABLE_ECHO_INPUT are turned off so each keystroke is delivered
    immediately without buffering.

    Reads happen via msvcrt.kbhit() polling — works on the same handle that
    Python's stdin uses without spawning a separate IO thread for the
    reads themselves (the reader-loop thread polls in a tight cycle).
    """

    # GetStdHandle id for stdin
    _STD_INPUT_HANDLE = -10

    # Console mode flags (from wincon.h)
    _ENABLE_PROCESSED_INPUT = 0x0001
    _ENABLE_LINE_INPUT = 0x0002
    _ENABLE_ECHO_INPUT = 0x0004
    _ENABLE_WINDOW_INPUT = 0x0008
    _ENABLE_MOUSE_INPUT = 0x0010
    _ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

    def __init__(self) -> None:
        self._stdin_handle = None
        self._old_mode = None

    def __enter__(self) -> "_WindowsBackend":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            self._stdin_handle = kernel32.GetStdHandle(self._STD_INPUT_HANDLE)
            old = wintypes.DWORD()
            if not kernel32.GetConsoleMode(self._stdin_handle, ctypes.byref(old)):
                self._stdin_handle = None
                return self
            self._old_mode = old.value
            new_mode = (
                self._ENABLE_VIRTUAL_TERMINAL_INPUT
                | self._ENABLE_WINDOW_INPUT
                # Keep mouse input flag OFF — we get mouse via VT escapes once
                # SGR mouse mode is enabled by the higher-level reader. Mixing
                # the two delivers events twice on some Windows builds.
            )
            kernel32.SetConsoleMode(self._stdin_handle, new_mode)
        except (OSError, AttributeError, ImportError):
            self._stdin_handle = None
            self._old_mode = None
        return self

    def __exit__(self, *exc) -> None:
        if self._stdin_handle is None or self._old_mode is None:
            return
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(self._stdin_handle, self._old_mode)
        except (OSError, AttributeError, ImportError):
            pass
        self._stdin_handle = None
        self._old_mode = None

    def read_bytes(self, max_n: int = 64,
                   timeout_s: float = 0.05) -> bytes:
        # Use msvcrt.kbhit + getwch loop for non-blocking reads. We loop until
        # timeout drains nothing OR we collect up to max_n bytes (whichever
        # comes first). Multi-byte sequences arrive byte-at-a-time on Windows
        # so we batch them into a single bytes object for the decoder.
        try:
            import msvcrt
        except ImportError:
            return b""
        deadline = time.monotonic() + timeout_s
        out = bytearray()
        while time.monotonic() < deadline and len(out) < max_n:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                # ch is bytes of length 1
                out.extend(ch)
                # If we just got an ESC, drain a tiny bit more aggressively
                # so we capture the rest of the sequence before a tick passes.
                if ch == b"\x1b":
                    deadline = max(deadline, time.monotonic() + 0.01)
            else:
                time.sleep(0.005)
        return bytes(out)


# ---------------------------------------------------------------------------
# InputReader — public entry point
# ---------------------------------------------------------------------------


class InputReader:
    """Cross-platform input reader (context manager + queue API).

    Lifecycle:
        with InputReader(enable_mouse=True) as reader:
            while running:
                for event in reader.drain():
                    handle(event)
                tick()

    Spawns a daemon thread on enter, joins it on exit. The queue is
    unbounded; if the consumer falls way behind we still keep events
    (memory hit only, no event loss).
    """

    def __init__(self,
                 *,
                 enable_mouse: bool = True,
                 sink: Optional[object] = None,
                 ) -> None:
        self._enable_mouse = enable_mouse
        self._sink = sink or sys.stdout
        self._queue: "queue.Queue[Event]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._backend: Optional[_Backend] = None
        self._paused = False

    def __enter__(self) -> "InputReader":
        self._backend = (_WindowsBackend() if sys.platform == "win32"
                         else _PosixBackend())
        self._backend.__enter__()
        self._enable_mouse_reporting()
        self._start_thread()
        return self

    def __exit__(self, *exc) -> None:
        self._stop_thread()
        self._disable_mouse_reporting()
        if self._backend is not None:
            self._backend.__exit__(*exc)
            self._backend = None

    def pause(self) -> None:
        """Release terminal input control so a child process can take over.

        Safe to call from any thread. Idempotent.
        """
        if self._paused or self._backend is None:
            return
        self._paused = True
        self._stop_thread()
        self._disable_mouse_reporting()
        self._backend.__exit__(None, None, None)

    def resume(self) -> None:
        """Re-acquire terminal input after a pause(). Idempotent."""
        if not self._paused or self._backend is None:
            return
        self._paused = False
        self._backend.__enter__()
        self._enable_mouse_reporting()
        # Drop any stale events accumulated during the pause window.
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._start_thread()

    def _start_thread(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._reader_loop, name="daimon-ui-input", daemon=True
        )
        self._thread.start()

    def _stop_thread(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None

    def _enable_mouse_reporting(self) -> None:
        if not self._enable_mouse:
            return
        try:
            self._sink.write(_ENABLE_MOUSE)
            self._sink.flush()
        except OSError:
            pass

    def _disable_mouse_reporting(self) -> None:
        if not self._enable_mouse:
            return
        try:
            self._sink.write(_DISABLE_MOUSE)
            self._sink.flush()
        except OSError:
            pass

    def drain(self) -> List[Event]:
        """Return all events available right now (no blocking)."""
        events: List[Event] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def poll(self, timeout_ms: int = 50) -> List[Event]:
        """Wait up to ``timeout_ms`` for an event; return it + any siblings."""
        events: List[Event] = []
        try:
            ev = self._queue.get(timeout=timeout_ms / 1000.0)
            events.append(ev)
        except queue.Empty:
            return events
        # Drain any events that arrived together
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _reader_loop(self) -> None:
        """Background thread: read bytes, decode, enqueue events."""
        if self._backend is None:
            return
        # Buffer for incomplete escape sequences across reads.
        buf = bytearray()
        while not self._stop.is_set():
            data = self._backend.read_bytes(max_n=64, timeout_s=0.05)
            if not data:
                # If we've been holding a partial sequence for a while
                # (no more bytes coming), flush as a bare ESC press.
                if buf and buf[0] == 0x1b and len(buf) == 1:
                    self._enqueue(KeyEvent("esc"))
                    buf.clear()
                continue
            buf.extend(data)
            self._extract_events(buf)

    def _extract_events(self, buf: bytearray) -> None:
        """Parse complete events out of buf, enqueue them, leave the rest."""
        while buf:
            consumed = self._extract_one(buf)
            if consumed == 0:
                # Incomplete sequence — wait for more bytes.
                return
            del buf[:consumed]

    def _extract_one(self, buf: bytearray) -> int:
        """Try to consume one event from the start of buf.

        Returns the number of bytes consumed, or 0 if more bytes are needed.
        """
        b0 = buf[0]
        # Non-escape bytes → single key event.
        if b0 != 0x1b:
            ev = _decode_key(bytes(buf[:1]))
            if ev is not None:
                self._enqueue(ev)
            return 1

        # Escape sequence — try to find the end.
        if len(buf) == 1:
            # Lone ESC; might be the start of a sequence. Wait briefly.
            return 0

        # ESC alone (followed by no continuation in time) is handled in
        # _reader_loop by the timeout flush. Here we assume more bytes follow.
        if buf[1] == ord("["):
            # CSI sequence — terminator is a byte in 0x40..0x7e
            for i in range(2, len(buf)):
                b = buf[i]
                if 0x40 <= b <= 0x7e:
                    seq = bytes(buf[: i + 1])
                    ev = decode_sequence(seq)
                    if ev is not None:
                        self._enqueue(ev)
                    return i + 1
            return 0  # not yet complete

        if buf[1] == ord("O"):
            # SS3 sequence — single-byte payload
            if len(buf) < 3:
                return 0
            seq = bytes(buf[:3])
            ev = decode_sequence(seq)
            if ev is not None:
                self._enqueue(ev)
            return 3

        # ESC + printable → alt+<key>; flush as alt-modified keypress
        ev = _decode_key(bytes(buf[1:2]))
        if ev is not None and len(ev.key) == 1:
            self._enqueue(KeyEvent(f"alt+{ev.key}"))
        return 2

    def _enqueue(self, event: Event) -> None:
        self._queue.put(event)
