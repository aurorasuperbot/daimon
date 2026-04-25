"""Card-tile primitive for the image-aware TUIs.

A *tile* is one card visualised as an image-bearing rectangle that can be
laid out next to other tiles in a grid (shop's 2×3 offering, collection's
N×M browse, loadout editor's catalog grid + 6 slot frames).

Visual structure of one tile (composited mode — the Phase F path):

    ╔════════════════╗     <- outer ASCII chrome (selection + dim/ghost state)
    ║ ┌────────────┐ ║
    ║ │ FULL CARD  │ ║     <- composited PIL tile (gold rarity border, name,
    ║ │ COMPOSITE  │ ║        element chip, stats strip, flavor text — all
    ║ │            │ ║        baked in by daimon.play.card_tile.render_card_tile)
    ║ │            │ ║
    ║ └────────────┘ ║
    ╠════════════════╣
    ║ [N] aegis_lion ║     <- caption_lines[0] — TUI-specific (slot index, etc.)
    ║ rare    300¤   ║     <- caption_lines[1] — TUI-specific (price, state)
    ╚════════════════╝

The composited tile is produced by ``card_tile.render_card_tile`` (or its
disk-cache helper :func:`card_tile.compose_tile_to_path`) so EVERY surface
that paints a card — battle UI, shop, collection, loadout editor — sees
the same chrome, art, and stats. Captions stay slim: they carry the only
state the composited tile DOESN'T (slot index, price, sold/equipped flag,
collection count).

The tile renderer returns both:

  * ``lines`` — list of ANSI strings, ``height`` rows of ``width`` cells —
    suitable for splicing into a frame string for both live TTY display
    and the ANSI-frame → PNG pipeline.
  * ``local_overlay`` — the art area is blank cells in ``lines``; this
    :class:`ImageOverlay` record points at the composited-tile PNG so the
    KGP painter (live terminal) and the screenshot pipeline (PNG export)
    both paint the same bitmap. The (row, col) on the overlay are LOCAL
    to the tile (0,0 = top-left of the art area). Composition helpers
    translate them to absolute frame coords.

Legacy raw-art mode is still wired (``composited_info=None`` + ``card_id``
set) so existing screenshot fixtures and bare-bones tests keep passing —
but every TUI surface in the code base now passes ``composited_info`` and
gets the full chrome.

Tiles are stateless data classes — call :func:`render_tile` with the card
state for the tick, throw the result away when the next tick comes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from daimon.play.art_render import (
    RenderMode,
    TileArt,
    render_card_art,
)
from daimon.play.card_tile import CardTileInfo, compose_tile_to_path
from daimon.play.screenshot import ImageOverlay
from daimon.play.tui_style import (
    BG_GRAY,
    BOLD,
    BRIGHT_CYAN,
    BRIGHT_YELLOW,
    DIM,
    GRAY,
    RESET,
    colorize,
    pad_visible,
    visible_len,
)

# ---------------------------------------------------------------------------
# Tile chrome — corners + edges, single-rule (lighter than the frame's
# double-rule so tiles read as nested inside the outer ╔══╗ chrome).
# ---------------------------------------------------------------------------

CORNER_TL = "┌"
CORNER_TR = "┐"
CORNER_BL = "└"
CORNER_BR = "┘"
EDGE_H = "─"
EDGE_V = "│"
TEE_L = "├"
TEE_R = "┤"


# Selected tile gets a heavy double-rule border so the focus reads
# instantly across the grid.
SELECTED_TL = "╔"
SELECTED_TR = "╗"
SELECTED_BL = "╚"
SELECTED_BR = "╝"
SELECTED_H = "═"
SELECTED_V = "║"
SELECTED_TEE_L = "╠"
SELECTED_TEE_R = "╣"


# Empty-slot ghost frame uses dotted glyphs.
GHOST_TL = "┌"
GHOST_TR = "┐"
GHOST_BL = "└"
GHOST_BR = "┘"
GHOST_H = "╌"
GHOST_V = "╎"


# ---------------------------------------------------------------------------
# Tile model
# ---------------------------------------------------------------------------


@dataclass
class Tile:
    """One rendered card tile.

    ``lines`` is the assembled tile (rows of ANSI text). ``local_overlay``
    (if any) gives the screenshot renderer a way to paste the real PIL
    bitmap on top of the (intentionally blank) art region.
    """

    lines: List[str]
    width: int
    height: int
    local_overlay: Optional[ImageOverlay] = None
    # Footer text used by the detail panel ("aegis_lion · rare · 300 ¤" etc.)
    # when the tile is selected. Lets a TUI render a separate detail block
    # without re-deriving the metadata.
    title: str = ""
    subtitle: str = ""


# ---------------------------------------------------------------------------
# Tile composition
# ---------------------------------------------------------------------------


def render_tile(*,
                card_id: str,
                width: int,
                art_h: int,
                caption_lines: Sequence[str],
                skin_slug: Optional[str] = None,
                selected: bool = False,
                dim: bool = False,
                ghost: bool = False,
                mode: RenderMode = RenderMode.OVERLAY_ONLY,
                border_color_rgb: Optional[Tuple[int, int, int]] = None,
                color: bool = True,
                composited_info: Optional[CardTileInfo] = None) -> Tile:
    """Render one card tile.

    Parameters
    ----------
    card_id:
        The card identifier; resolved to art via :func:`art_render.resolve_card_art`
        when ``composited_info`` is None. When ``composited_info`` is given,
        ``card_id`` is used only for legacy placeholder labels.
    width:
        Total cell width of the tile (including borders).
    art_h:
        Cell height of the art region (inside borders).
    caption_lines:
        Lines printed under the art region. Empty strings render as blanks
        so layout stays aligned across tiles.
    skin_slug:
        Optional skin slug to render a specific variant. Ignored when
        ``composited_info`` is given (skin selection is baked into the
        :class:`CardTileInfo`'s ``art_path`` field upstream).
    selected, dim, ghost:
        Visual state flags. ``selected`` swaps to a double-rule border;
        ``ghost`` emits a dotted frame (for empty loadout slots, where
        ``card_id`` may be ``""``); ``dim`` greys the chrome (sold/owned).
    mode:
        Live TTY vs OVERLAY_ONLY (screenshot pipeline). KGP painting
        always happens via OVERLAY_ONLY; live TTY no longer has a
        non-overlay path after Phase E.
    border_color_rgb:
        Used by the screenshot renderer to colour the overlay border.
        TTY mode applies this to the chrome via the closest ANSI bright
        code — selected = bright_cyan, dirty = bright_yellow, etc.
    composited_info:
        When set, the art region is filled with the FULL composited card
        tile (gold rarity border, element chip, name, stats strip, flavor
        text) instead of just the raw card-art image. The composited
        bitmap is produced via :func:`card_tile.compose_tile_to_path` and
        cached on disk; the overlay points at that cached PNG so KGP's
        image_id stays stable across redraws. This is the Phase F path —
        every TUI surface should pass it. Leaving it None keeps the
        legacy raw-art path alive for existing screenshot fixtures and
        bare-bones tests.

    Returns
    -------
    Tile
        Includes ``lines`` (ANSI rows) + optional ``local_overlay``.
    """
    inner_w = width - 2  # excluding the side borders
    if inner_w < 4 or art_h < 1:
        raise ValueError(f"tile too small: width={width} art_h={art_h}")

    # Pick the glyph set.
    if ghost:
        tl, tr, bl, br, h, v = (GHOST_TL, GHOST_TR, GHOST_BL, GHOST_BR,
                                GHOST_H, GHOST_V)
        cap_v = v
    elif selected:
        tl, tr, bl, br, h, v = (SELECTED_TL, SELECTED_TR, SELECTED_BL,
                                SELECTED_BR, SELECTED_H, SELECTED_V)
        cap_v = v
    else:
        tl, tr, bl, br, h, v = (CORNER_TL, CORNER_TR, CORNER_BL, CORNER_BR,
                                EDGE_H, EDGE_V)
        cap_v = v

    chrome_color = None
    if color:
        if selected:
            chrome_color = BRIGHT_CYAN + BOLD
        elif ghost:
            chrome_color = DIM
        elif dim:
            chrome_color = GRAY
    elif False:
        chrome_color = None  # keep monochrome

    def _wrap(c: str) -> str:
        if chrome_color is None:
            return c
        return chrome_color + c + RESET

    top_border = _wrap(tl + h * inner_w + tr)
    bot_border = _wrap(bl + h * inner_w + br)

    # Art region.
    if ghost or not card_id:
        # Empty slot: dotted interior with a centred "(empty)" marker.
        art_lines: List[str] = []
        for i in range(art_h):
            content = " " * inner_w
            if i == art_h // 2:
                marker = "(empty)"
                pad = (inner_w - len(marker)) // 2
                content = " " * pad + marker + " " * (inner_w - pad - len(marker))
                if color:
                    content = DIM + content + RESET
            art_lines.append(_wrap(v) + content + _wrap(v))
        local_overlay = None
    elif composited_info is not None:
        # Phase F path: the overlay PNG is the FULL composited tile (gold
        # border + stats strip + flavor text + element chip + rarity halo
        # all baked in by daimon.play.card_tile). The TUI's outer ASCII
        # chrome (corners + selection border) just frames the composited
        # image — captions sit below the tile as before so TUI-specific
        # info (price, OWNED state, slot index) stays legible.
        art_lines = [_wrap(v) + " " * inner_w + _wrap(v) for _ in range(art_h)]
        composited_path = compose_tile_to_path(composited_info)
        local_overlay = ImageOverlay(
            row=1,           # skip top border
            col=1,           # skip left border
            rows=art_h,
            cols=inner_w,
            image_path=composited_path,
            border_color=border_color_rgb,
            border_width=2 if selected else 0,
            glow=4 if selected else 0,
        )
    else:
        art = render_card_art(card_id, inner_w, art_h,
                              skin_slug=skin_slug, mode=mode,
                              placeholder_label=card_id[:inner_w])
        art_lines = []
        for line in art.lines:
            # Each art line is exactly inner_w visible cells; just splice.
            art_lines.append(_wrap(v) + line + _wrap(v))
        # OVERLAY_ONLY mode → emit a local ImageOverlay.
        local_overlay = None
        if mode == RenderMode.OVERLAY_ONLY and art.image_path is not None:
            local_overlay = ImageOverlay(
                row=1,           # skip top border
                col=1,           # skip left border
                rows=art_h,
                cols=inner_w,
                image_path=art.image_path,
                border_color=border_color_rgb,
                border_width=2 if selected else 0,
                glow=4 if selected else 0,
            )

    # Caption rows.
    caption_rows: List[str] = []
    for cap in caption_lines:
        body = pad_visible(cap, inner_w)
        if dim and color:
            body = DIM + body + RESET
        caption_rows.append(_wrap(cap_v) + body + _wrap(cap_v))

    # Stack: top + art + bottom + (no extra divider — captions sit below the
    # bottom edge so the tile reads like a card on a table with a tag).
    lines: List[str] = []
    lines.append(top_border)
    lines.extend(art_lines)
    lines.append(bot_border)
    lines.extend(caption_rows)

    title = caption_lines[0] if caption_lines else card_id
    subtitle = caption_lines[1] if len(caption_lines) > 1 else ""

    return Tile(
        lines=lines,
        width=width,
        height=len(lines),
        local_overlay=local_overlay,
        title=title,
        subtitle=subtitle,
    )


# ---------------------------------------------------------------------------
# Row composition — splice tiles side-by-side, return both rows + abs
# coords for relocating local overlays.
# ---------------------------------------------------------------------------


@dataclass
class ComposedRow:
    """A row of tiles laid out side-by-side."""

    lines: List[str]
    height: int
    width: int
    # (col_offset_in_cells, tile) so callers can re-anchor any tile.local_overlay
    # to absolute (row, col) coords in the full frame.
    placements: List[Tuple[int, Tile]] = field(default_factory=list)


def compose_row(tiles: Sequence[Tile], *,
                gap: int = 2,
                left_pad: int = 0) -> ComposedRow:
    """Stack ``tiles`` side-by-side with ``gap`` blank cells between them.

    All tiles must have equal ``height``; raise otherwise. Returns lines +
    a placement list mapping each tile to its column offset (so the caller
    can translate local overlays to absolute frame coords).
    """
    if not tiles:
        return ComposedRow(lines=[], height=0, width=0, placements=[])
    h = tiles[0].height
    for t in tiles:
        if t.height != h:
            raise ValueError(f"tile height mismatch: {t.height} vs {h}")

    placements: List[Tuple[int, Tile]] = []
    total_w = left_pad
    rows: List[List[str]] = [[] for _ in range(h)]
    for i, t in enumerate(tiles):
        if i == 0:
            for r in range(h):
                rows[r].append(" " * left_pad)
        else:
            for r in range(h):
                rows[r].append(" " * gap)
            total_w += gap
        placements.append((total_w, t))
        for r in range(h):
            rows[r].append(t.lines[r])
        total_w += t.width

    lines = ["".join(parts) for parts in rows]
    return ComposedRow(lines=lines, height=h, width=total_w, placements=placements)


def overlays_for_row(row: ComposedRow, base_row: int,
                     base_col: int = 0) -> List[ImageOverlay]:
    """Translate each tile's local overlay to absolute frame coords.

    ``base_row`` / ``base_col`` are where the row's top-left cell sits in
    the larger frame (the screenshot pipeline expects coordinates relative
    to the FULL frame, not the row).
    """
    out: List[ImageOverlay] = []
    for col_off, tile in row.placements:
        if tile.local_overlay is None:
            continue
        lo = tile.local_overlay
        out.append(ImageOverlay(
            row=base_row + lo.row,
            col=base_col + col_off + lo.col,
            rows=lo.rows,
            cols=lo.cols,
            image_path=lo.image_path,
            border_color=lo.border_color,
            border_width=lo.border_width,
            glow=lo.glow,
            caption=lo.caption,
            caption_color=lo.caption_color,
        ))
    return out
