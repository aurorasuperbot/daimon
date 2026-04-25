"""Shared terminal-UI style primitives for DAIMON's interactive TUIs.

Three TUIs share one visual language:

  - ``daimon shop``        (browse + buy daily skins)
  - ``daimon collection``  (sort + filter owned cards, drill into detail)
  - ``daimon loadout edit`` (split-pane deck editor)

This module is the single source of truth for box-drawing chars, ANSI
escapes, color mappings (rarity / element / state), and frame-line
composition. Everything here is pure: no curses, no termios, no I/O.

Why ANSI + raw escapes rather than curses? The spectator HUD already uses
this idiom (see ``daimon/play/hud/render.py`` + ``hud/keyboard.py``), so
sharing it across surfaces gives players a coherent terminal look. ANSI
also makes screenshot rendering trivial — capture a frame string, hand it
to PIL, draw with a monospace font.

Design contract:

  * Frame width is fixed at WIDTH (80) cols by default — overridable
    per-call so the loadout editor can render wider on a 120-col layout.
  * Every line returned is exactly ``width`` visible chars (ANSI excluded)
    so terminals don't reflow on partial repaints.
  * `_strip_ansi` is shared with the HUD renderer's contract — keep
    parity if either side adds new escape forms.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

WIDTH = 80   # default frame width (matches spectator HUD)

# ---------------------------------------------------------------------------
# ANSI escape codes (4-bit + bright) — keep tiny; tests assert on these
# ---------------------------------------------------------------------------

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
UNDERLINE = "\x1b[4m"
INVERSE = "\x1b[7m"

BLACK = "\x1b[30m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
WHITE = "\x1b[37m"

GRAY = "\x1b[90m"
BRIGHT_RED = "\x1b[91m"
BRIGHT_GREEN = "\x1b[92m"
BRIGHT_YELLOW = "\x1b[93m"
BRIGHT_BLUE = "\x1b[94m"
BRIGHT_MAGENTA = "\x1b[95m"
BRIGHT_CYAN = "\x1b[96m"
BRIGHT_WHITE = "\x1b[97m"

# Background colors for selection bars + sold/disabled rows.
BG_BLUE = "\x1b[44m"
BG_GRAY = "\x1b[100m"
BG_GREEN = "\x1b[42m"
BG_YELLOW = "\x1b[43m"

# ---------------------------------------------------------------------------
# Domain → color mapping
# ---------------------------------------------------------------------------

# Rarity colors used everywhere a rarity is shown. The rare-ladder follows
# Magic / Hearthstone convention: white → green → blue → purple → orange.
RARITY_COLOR = {
    "common":     WHITE,
    "uncommon":   GREEN,
    "rare":       BRIGHT_BLUE,
    "epic":       MAGENTA,
    "legendary":  BRIGHT_YELLOW,
    # Shop-tier rarities (skin axis) — share the rare/super_rare ladder.
    "super_rare": BRIGHT_MAGENTA,
}

# Element → color (matches hud/render.py ELEMENT_COLOR).
ELEMENT_COLOR = {
    "fire":   RED,
    "water":  BLUE,
    "nature": GREEN,
    "volt":   YELLOW,
    "void":   MAGENTA,
    "normal": WHITE,
}

# Status colors for [OWNED] / [LOCKED] / etc.
STATE_OWNED = GRAY
STATE_LOCKED = GRAY
STATE_NEW = BRIGHT_GREEN
STATE_INVALID = BRIGHT_RED
STATE_OK = GREEN

# Glyphs — single source of truth. Render layers ONLY use these.
GLYPH_CURSOR = "▶"
GLYPH_BULLET = "·"
GLYPH_CHECK = "✓"
GLYPH_CROSS = "✕"
GLYPH_PLUS = "+"
GLYPH_MINUS = "−"
GLYPH_ARROW_R = "→"
GLYPH_ARROW_L = "←"


# ---------------------------------------------------------------------------
# Box-drawing primitives — heavy double-ruled to match the spectator HUD.
# ---------------------------------------------------------------------------

CORNER_TL = "╔"
CORNER_TR = "╗"
CORNER_BL = "╚"
CORNER_BR = "╝"
EDGE_H = "═"
EDGE_V = "║"
TEE_L = "╠"
TEE_R = "╣"


def box_top(width: int = WIDTH) -> str:
    return CORNER_TL + EDGE_H * (width - 2) + CORNER_TR


def box_bottom(width: int = WIDTH) -> str:
    return CORNER_BL + EDGE_H * (width - 2) + CORNER_BR


def divider(width: int = WIDTH) -> str:
    return TEE_L + EDGE_H * (width - 2) + TEE_R


def blank(width: int = WIDTH) -> str:
    return EDGE_V + " " * (width - 2) + EDGE_V


# ---------------------------------------------------------------------------
# ANSI-aware width helpers
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    """Return ``s`` with all CSI SGR escapes removed."""
    return _ANSI_RE.sub("", s)


def visible_len(s: str) -> int:
    """Visible width of ``s`` after stripping ANSI escapes.

    Treats every code point as 1 cell — fine for our box-drawing + ASCII
    payloads. CJK / wide-char support isn't a V1 concern.
    """
    return len(strip_ansi(s))


def pad_visible(s: str, width: int, *, align: str = "left") -> str:
    """Pad ``s`` so its visible width is ``width``. ANSI-aware.

    ``align`` is one of "left" / "right" / "center". If the visible width
    already exceeds ``width``, the string is truncated.
    """
    vlen = visible_len(s)
    if vlen >= width:
        return truncate_visible(s, width)
    pad = width - vlen
    if align == "right":
        return " " * pad + s
    if align == "center":
        l = pad // 2
        r = pad - l
        return " " * l + s + " " * r
    return s + " " * pad


def truncate_visible(s: str, n: int) -> str:
    """Truncate ``s`` to visible width ``n``, preserving ANSI escape pairs.

    Walks the string once, passing CSI sequences through verbatim and
    counting only printable code points toward ``n``. If we cut inside a
    colored span, we append RESET so the next line isn't bleached.
    """
    if visible_len(s) <= n:
        return s
    out: list[str] = []
    count = 0
    i = 0
    in_color = False
    while i < len(s) and count < n:
        if s[i] == "\x1b":
            j = s.find("m", i)
            if j < 0:
                out.append(s[i])
                i += 1
                continue
            seq = s[i:j + 1]
            out.append(seq)
            in_color = seq != RESET and not seq.endswith("[0m")
            i = j + 1
        else:
            out.append(s[i])
            count += 1
            i += 1
    if in_color:
        out.append(RESET)
    return "".join(out)


# ---------------------------------------------------------------------------
# Frame-line composition
# ---------------------------------------------------------------------------

def frame_line(content: str, width: int = WIDTH) -> str:
    """Wrap ``content`` between │ walls, padding/truncating to fit."""
    pad = (width - 2) - visible_len(content)
    if pad < 0:
        content = truncate_visible(content, width - 2)
        pad = 0
    return EDGE_V + content + " " * pad + EDGE_V


def left(text: str, width: int = WIDTH, *,
         bold: bool = False, dim: bool = False,
         color: Optional[str] = None) -> str:
    """Single-line left-justified row inside the frame."""
    body = text
    prefix = ""
    if bold:
        prefix += BOLD
    if dim:
        prefix += DIM
    if color:
        prefix += color
    if prefix:
        body = prefix + body + RESET
    return frame_line(body, width)


def centered(text: str, width: int = WIDTH, *,
             bold: bool = False, dim: bool = False,
             color: Optional[str] = None) -> str:
    """Center ``text`` within the frame interior."""
    pad_each = max(0, ((width - 2) - len(text)) // 2)
    body = " " * pad_each + text
    prefix = ""
    if bold:
        prefix += BOLD
    if dim:
        prefix += DIM
    if color:
        prefix += color
    if prefix:
        body = prefix + body + RESET
    return frame_line(body, width)


def split_row(left_text: str, right_text: str,
              width: int = WIDTH, *,
              gap: int = 1) -> str:
    """Two-column row: left text + right text, padding between them.

    If both don't fit, the left is truncated first (right is more often
    the keyhint / status reading on a deadline).
    """
    rl = visible_len(right_text)
    avail = (width - 2) - rl - gap
    if avail < 1:
        # Right side too long — truncate it.
        right_text = truncate_visible(right_text, max(0, width - 4))
        return frame_line(right_text, width)
    left_clipped = truncate_visible(left_text, avail)
    pad = avail - visible_len(left_clipped)
    return frame_line(left_clipped + " " * (pad + gap) + right_text, width)


def row(cells: List[str], col_widths: List[int],
        width: int = WIDTH, *,
        sep: str = "  ") -> str:
    """Multi-column row. Each cell padded/truncated to its col_width.

    ANSI-aware: cells may contain colored spans; padding uses visible width.
    """
    if len(cells) != len(col_widths):
        raise ValueError(
            f"row: {len(cells)} cells vs {len(col_widths)} col_widths"
        )
    parts = []
    for cell, cw in zip(cells, col_widths):
        clipped = truncate_visible(cell, cw)
        pad = max(0, cw - visible_len(clipped))
        parts.append(clipped + " " * pad)
    body = sep.join(parts)
    return frame_line(body, width)


# ---------------------------------------------------------------------------
# Header / status-bar idiom — every TUI uses these to look the same.
# ---------------------------------------------------------------------------

def header(title: str, *, identity: Optional[str] = None,
           width: int = WIDTH) -> List[str]:
    """Top of the frame: ╔══╗  + centered title  + identity (right-justified).

    Returns a list of frame strings (without trailing newline).
    """
    out: list[str] = [box_top(width)]
    if identity:
        # Title centered, identity dim-right.
        rl = f"  {identity}  "
        title_body = BOLD + f"DAIMON · {title}" + RESET
        # Compose "<title centered>" with right-edge identity overlay.
        # Center first, then splice identity in.
        line = centered(title_body, width)
        # Splice identity onto the same line: replace the right-most chars.
        right_field = DIM + rl + RESET
        # Build a fresh row with split_row idiom for clarity.
        out[-1] = box_top(width)
        out.append(split_row(BOLD + f"  DAIMON · {title}" + RESET,
                             right_field, width))
    else:
        out.append(centered(BOLD + f"DAIMON · {title}" + RESET, width))
    out.append(divider(width))
    return out


def status_bar(left_text: str, key_hints: str,
               width: int = WIDTH) -> List[str]:
    """Bottom of the frame: divider + status-line + ╚══╝.

    ``left_text`` is the page-state line (e.g. "slot 0/5 · weekly 2/5").
    ``key_hints`` is the keybinding strip (e.g. "[←→]select [⏎]buy [q]quit").
    """
    return [
        divider(width),
        split_row(" " + left_text,
                  DIM + key_hints + RESET + " ", width),
        box_bottom(width),
    ]


def section_title(text: str, width: int = WIDTH, *,
                  color: Optional[str] = None) -> str:
    """A bold section label sitting above a list/table."""
    body = "  " + BOLD + text + RESET
    if color:
        body = "  " + BOLD + color + text + RESET
    return frame_line(body, width)


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

def cursor_prefix(selected: bool, *, color: bool = True) -> str:
    """Return the row's leading 2-char prefix: '▶ ' or '  '."""
    if not selected:
        return "  "
    glyph = GLYPH_CURSOR
    if color:
        return BRIGHT_CYAN + glyph + RESET + " "
    return glyph + " "


def selection_wrap(text: str, *, selected: bool, color: bool = True) -> str:
    """Optionally wrap ``text`` with a highlight background when selected.

    Only applies when ``color`` is True — monochrome relies on the cursor
    prefix glyph instead.
    """
    if not selected or not color:
        return text
    return BG_BLUE + WHITE + text + RESET


# ---------------------------------------------------------------------------
# ANSI screen control (for the interactive runners — pure passthrough)
# ---------------------------------------------------------------------------

CURSOR_HIDE = "\x1b[?25l"
CURSOR_SHOW = "\x1b[?25h"
CLEAR_SCREEN = "\x1b[2J\x1b[H"
HOME = "\x1b[H"


# ---------------------------------------------------------------------------
# Color shortcuts the shop / collection / editor reach for repeatedly
# ---------------------------------------------------------------------------

def rarity_color(rarity: str) -> str:
    return RARITY_COLOR.get(rarity, WHITE)


def element_color(element: str) -> str:
    return ELEMENT_COLOR.get(element.lower(), WHITE)


def colorize(text: str, color: Optional[str], *, bold: bool = False) -> str:
    """Wrap ``text`` in color/bold, or return unchanged if color is None."""
    if not color and not bold:
        return text
    prefix = ""
    if bold:
        prefix += BOLD
    if color:
        prefix += color
    return prefix + text + RESET


# ---------------------------------------------------------------------------
# Convenience: build a complete frame from header + body + status-bar.
# ---------------------------------------------------------------------------

def compose_frame(*,
                  title: str,
                  identity: Optional[str],
                  body_lines: Iterable[str],
                  status_left: str,
                  status_keys: str,
                  width: int = WIDTH) -> str:
    """Stitch header + body + status_bar into a single frame string.

    Body lines must already be ``frame_line``-formatted (i.e. ╔..╣ walls).
    Returns one '\\n'-joined string with no trailing newline.
    """
    lines: list[str] = []
    lines.extend(header(title, identity=identity, width=width))
    lines.extend(body_lines)
    lines.extend(status_bar(status_left, status_keys, width=width))
    return "\n".join(lines)
