"""ANSI-frame → PNG renderer for TUI screenshots.

Used to capture deterministic still frames of ``shop_ui``, ``collection_ui``,
and ``loadout_editor`` for design feedback / docs / chat previews.

Pipeline:
  1. Caller hands us a frame string (the same string the runner would write
     to the terminal — full ANSI escapes intact) PLUS an optional list of
     :class:`ImageOverlay` records describing where to paste real bitmaps.
  2. We parse a minimal subset of CSI SGR escapes (BOLD, DIM, INVERSE, the
     8 standard colors + 8 bright + 8 background colors) into per-char
     attributes.
  3. PIL paints each cell on a dark background with a monospace font.
  4. After the glyph pass, image overlays are pasted on top — the cells
     under each overlay should have been emitted as blank/space by the
     caller (use ``art_render.render_overlay_blank`` for that).

The result is a faithful screenshot of what the user sees in their terminal
PLUS pixel-perfect card art at the tile positions — the live half-block
rendering would compress to ~2-px-per-cell-row, which is acceptable in a
real terminal but reads as muddy in a still PNG.

Why not record a real terminal? `vhs`/`asciinema` chains require a live
TTY and timing rules that aren't worth the dependency for static design
previews. This module is purely deterministic — same input → identical PNG.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Color palette — terminal-native dark theme. Tuned to match a typical
# VS-Code-dark / iTerm2 default scheme so the screenshots feel familiar.
# ---------------------------------------------------------------------------

BG = (16, 18, 24)           # canvas background
FG = (220, 220, 220)        # default foreground (white-ish)

# 4-bit ANSI SGR colors. Index = code-30 (FG) or code-40 (BG).
ANSI_4BIT = {
    0: (40, 42, 54),     # black
    1: (235, 90, 80),    # red
    2: (90, 210, 130),   # green
    3: (230, 198, 90),   # yellow
    4: (80, 160, 235),   # blue
    5: (200, 130, 220),  # magenta
    6: (130, 220, 220),  # cyan
    7: (220, 220, 220),  # white
}

# 4-bit BRIGHT (codes 90-97 / 100-107).
ANSI_BRIGHT = {
    0: (110, 118, 132),  # bright black (gray)
    1: (255, 130, 120),
    2: (130, 240, 160),
    3: (255, 220, 120),
    4: (120, 190, 255),
    5: (220, 160, 240),
    6: (160, 240, 240),
    7: (255, 255, 255),
}


@dataclass
class Cell:
    """One painted character cell."""
    char: str = " "
    fg: Tuple[int, int, int] = FG
    bg: Tuple[int, int, int] = BG
    bold: bool = False
    dim: bool = False
    inverse: bool = False


@dataclass
class Attr:
    """Active SGR attributes while parsing a frame."""
    fg: Optional[Tuple[int, int, int]] = None
    bg: Optional[Tuple[int, int, int]] = None
    bold: bool = False
    dim: bool = False
    inverse: bool = False

    def reset(self) -> None:
        self.fg = None
        self.bg = None
        self.bold = False
        self.dim = False
        self.inverse = False

    def apply(self, codes: List[int]) -> None:
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                self.reset()
            elif c == 1:
                self.bold = True
            elif c == 2:
                self.dim = True
            elif c == 7:
                self.inverse = True
            elif c == 22:
                self.bold = False
                self.dim = False
            elif c == 27:
                self.inverse = False
            elif 30 <= c <= 37:
                self.fg = ANSI_4BIT[c - 30]
            elif 40 <= c <= 47:
                self.bg = ANSI_4BIT[c - 40]
            elif 90 <= c <= 97:
                self.fg = ANSI_BRIGHT[c - 90]
            elif 100 <= c <= 107:
                self.bg = ANSI_BRIGHT[c - 100]
            elif c == 39:
                self.fg = None
            elif c == 49:
                self.bg = None
            # 256-color and truecolor not handled — we don't emit them.
            i += 1


_CSI_RE = re.compile(r"\x1b\[([0-9;]*)m")


def parse_frame(frame: str) -> List[List[Cell]]:
    """Parse an ANSI-colored frame string into a 2D grid of Cells.

    Lines split on newlines. Each cell records its char + active fg/bg/bold/
    dim/inverse. Empty trailing cells stay default (BG/FG).
    """
    grid: List[List[Cell]] = []
    attr = Attr()
    for line in frame.split("\n"):
        cells: List[Cell] = []
        i = 0
        while i < len(line):
            m = _CSI_RE.match(line, i)
            if m is not None:
                params = m.group(1)
                codes = [int(p) for p in params.split(";")] if params else [0]
                attr.apply(codes)
                i = m.end()
                continue
            ch = line[i]
            fg = attr.fg or FG
            bg = attr.bg or BG
            if attr.inverse:
                fg, bg = bg, fg
            if attr.dim:
                fg = tuple(int(c * 0.55) + int(BG[k] * 0.45) for k, c in enumerate(fg))
            cells.append(Cell(char=ch, fg=fg, bg=bg,
                              bold=attr.bold, dim=attr.dim,
                              inverse=attr.inverse))
            i += 1
        grid.append(cells)
    return grid


# ---------------------------------------------------------------------------
# Font loading — DejaVu Sans Mono (already used elsewhere in the repo)
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]
_FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
]


def _load_font(paths: List[str], size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Image overlays — paste real card art on top of the cell-grid render
# ---------------------------------------------------------------------------

@dataclass
class ImageOverlay:
    """One bitmap to paste over the rendered frame.

    Coordinates are in *cells* (rows + columns), not pixels — the renderer
    multiplies by the measured cell size. The caller is expected to have
    already emitted blank cells underneath; we don't erase what's there.

    ``border_color`` (if set) draws a 1-px frame around the pasted image
    in the given RGB tuple — useful for "this tile is selected" highlights.
    ``glow`` (if > 0) blooms the border outward by ``glow`` extra pixels
    (selected-tile emphasis).
    """

    row: int                                # top-left cell row (0-indexed)
    col: int                                # top-left cell col (0-indexed)
    rows: int                               # cell height of the overlay
    cols: int                               # cell width of the overlay
    image_path: Path
    border_color: Optional[Tuple[int, int, int]] = None
    border_width: int = 1
    glow: int = 0
    # Optional caption painted in the bottom strip (e.g. card_id).
    caption: Optional[str] = None
    caption_color: Tuple[int, int, int] = (220, 220, 220)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

@dataclass
class RenderOpts:
    font_size: int = 18
    cell_w: Optional[int] = None     # auto from font metrics if None
    cell_h: Optional[int] = None
    pad: int = 18                    # outer margin (px)
    title: Optional[str] = None      # optional caption above the frame
    caption_color: Tuple[int, int, int] = (170, 175, 190)


def render_to_png(frame: str, out_path: Path, *,
                  opts: Optional[RenderOpts] = None,
                  overlays: Optional[List[ImageOverlay]] = None) -> Path:
    """Render an ANSI-colored frame to a PNG file. Returns the path written.

    Output is sized exactly to fit the grid + padding so screenshots stay
    proportional regardless of frame width. ``overlays`` (if given) paste
    real bitmap art over the cell grid after the glyph pass.
    """
    opts = opts or RenderOpts()
    grid = parse_frame(frame.rstrip("\n"))
    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    if rows == 0 or cols == 0:
        raise ValueError("empty frame")

    font = _load_font(_FONT_CANDIDATES, opts.font_size)
    font_bold = _load_font(_FONT_BOLD_CANDIDATES, opts.font_size)

    # Measure cell size from a representative wide glyph (block char).
    bbox = font.getbbox("█")
    cell_w = opts.cell_w if opts.cell_w else (bbox[2] - bbox[0])
    cell_h = opts.cell_h if opts.cell_h else int((bbox[3] - bbox[1]) * 1.45)

    title_h = 0
    title_font = None
    if opts.title:
        title_font = _load_font(_FONT_BOLD_CANDIDATES, opts.font_size + 2)
        tb = title_font.getbbox(opts.title)
        title_h = (tb[3] - tb[1]) + 16

    canvas_w = cols * cell_w + 2 * opts.pad
    canvas_h = rows * cell_h + 2 * opts.pad + title_h

    img = Image.new("RGB", (canvas_w, canvas_h), BG)
    draw = ImageDraw.Draw(img)

    if opts.title and title_font is not None:
        draw.text((opts.pad, opts.pad),
                  opts.title,
                  font=title_font,
                  fill=opts.caption_color)

    y0 = opts.pad + title_h
    for r, line in enumerate(grid):
        y = y0 + r * cell_h
        for c, cell in enumerate(line):
            x = opts.pad + c * cell_w
            # Always paint the BG cell, then overlay the glyph. This lets
            # selection bars (BG_GRAY) read as continuous swatches.
            if cell.bg != BG:
                draw.rectangle(
                    [(x, y), (x + cell_w, y + cell_h)],
                    fill=cell.bg,
                )
            if cell.char != " ":
                f = font_bold if cell.bold else font
                # Tiny baseline tweak so glyphs sit centred in the cell.
                draw.text((x, y + 1), cell.char, font=f, fill=cell.fg)

    # ----- Image overlays — pasted last so they sit on top of the glyphs -----
    if overlays:
        small_font = _load_font(_FONT_CANDIDATES, max(10, opts.font_size - 4))
        for ov in overlays:
            x = opts.pad + ov.col * cell_w
            y = y0 + ov.row * cell_h
            w = ov.cols * cell_w
            h = ov.rows * cell_h
            try:
                src = Image.open(ov.image_path).convert("RGB")
            except (OSError, FileNotFoundError):
                continue
            # Reserve a thin caption strip if needed.
            cap_strip = 0
            if ov.caption:
                cap_strip = max(14, opts.font_size + 2)
            art_h = max(1, h - cap_strip)
            # Preserve aspect ratio: letter-box rather than stretching.
            scale = min(w / src.width, art_h / src.height)
            new_w = max(1, int(src.width * scale))
            new_h = max(1, int(src.height * scale))
            scaled = src.resize((new_w, new_h), Image.LANCZOS)
            offset_x = x + (w - new_w) // 2
            offset_y = y + (art_h - new_h) // 2
            img.paste(scaled, (offset_x, offset_y))
            # Optional outward glow for selected tiles.
            if ov.glow > 0 and ov.border_color is not None:
                glow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                glow_draw = ImageDraw.Draw(glow_layer)
                br, bg_, bb = ov.border_color
                for k in range(ov.glow, 0, -1):
                    alpha = int(120 * (1 - k / (ov.glow + 1)))
                    glow_draw.rectangle(
                        [(x - k, y - k), (x + w + k, y + art_h + k)],
                        outline=(br, bg_, bb, alpha),
                        width=1,
                    )
                glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=ov.glow / 2))
                img = Image.alpha_composite(img.convert("RGBA"), glow_layer).convert("RGB")
                draw = ImageDraw.Draw(img)
            # Hard border on top of art.
            if ov.border_color is not None:
                for k in range(ov.border_width):
                    draw.rectangle(
                        [(x - k, y - k), (x + w + k, y + art_h + k)],
                        outline=ov.border_color,
                    )
            # Caption strip — drawn under the art.
            if ov.caption:
                cap_y = y + art_h
                draw.rectangle(
                    [(x, cap_y), (x + w, y + h)],
                    fill=(28, 30, 38),
                )
                # Centred caption.
                tb = small_font.getbbox(ov.caption)
                tw = tb[2] - tb[0]
                tx = x + (w - tw) // 2
                draw.text((tx, cap_y + 1), ov.caption,
                          font=small_font, fill=ov.caption_color)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path
