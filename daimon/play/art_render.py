"""Card-art rendering primitives for the interactive TUIs.

Two output paths:

  1. **Live terminal** — render a card art file as a stack of ANSI half-block
     lines (`▀` glyph with truecolor fg/bg). Universal: works on any modern
     terminal that speaks 24-bit color (Kitty, iTerm2, Alacritty, Ghostty,
     WezTerm, gnome-terminal, foot, mintty…). One char per cell × 2 px per
     cell vertical resolution.

  2. **Screenshot** — *no half-block step*. The PNG renderer in
     ``screenshot.py`` accepts an :class:`ImageOverlay` list and pastes the
     real PIL image directly into the canvas at the right cell coordinates.
     This produces a pixel-perfect screenshot for design review.

Both paths share one resolver: :func:`resolve_card_art`. It wraps the
engine's :func:`daimon.render.art.art_path_for` and adds a couple of
convenience layers (in-memory thumbnail cache, missing-art placeholder).

This module is render-only — it never reads card metadata or makes
decisions about which art belongs to which card. Callers pass in card_id
+ optional skin_slug; we return pixels.

Why no Kitty/iTerm2 graphics protocol path here? V2 ships universal
half-block first; pixel-protocol upgrades land in V3 once we know how the
half-block layout reads in real use. The architecture leaves room for
additional renderers — see :class:`RenderMode`.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional

from PIL import Image

from daimon.play.tui_style import RESET

# ---------------------------------------------------------------------------
# Asset resolution
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1024)
def resolve_card_art(card_id: str, *,
                     skin_slug: Optional[str] = None) -> Optional[Path]:
    """Find the PNG for a card (optionally for a specific skin variant).

    When ``skin_slug`` is given, look it up in the card's manifest first;
    fall through to the canonical art if the slug isn't installed. When
    ``skin_slug`` is None we honor the player's equipped skin via
    ``art_path_for`` so the same card looks the same across all surfaces.

    Returns the absolute path or None if no art exists for this card.
    """
    from daimon.render.art import _read_manifest, _variant_id_for_slug, _variant_png
    from daimon.update.paths import art_pack_dir

    art_root = art_pack_dir()
    card_dir = art_root / card_id

    if skin_slug:
        manifest = _read_manifest(card_dir)
        if manifest:
            vid = _variant_id_for_slug(manifest, skin_slug)
            if vid:
                png = _variant_png(card_dir, vid)
                if png:
                    return png
        # Fall through if the requested skin isn't on disk.

    # No specific slug requested → defer to the equipped-aware resolver.
    from daimon.render.art import art_path_for
    return art_path_for(card_id)


# ---------------------------------------------------------------------------
# Render-mode discriminator
# ---------------------------------------------------------------------------


class RenderMode(str, Enum):
    """How a tile's art region is filled.

    HALFBLOCK: 24-bit color half-block (▀) — universal live-terminal mode.
    PLACEHOLDER: solid blanks + corner glyphs — for `--no-tui` / no-color
                 / missing-art.
    OVERLAY_ONLY: emit blanks; the SCREENSHOT renderer will paste the PIL
                  image over the cells. Used by the PNG render path so we
                  never half-block-rasterize when generating a still image.
    """

    HALFBLOCK = "halfblock"
    PLACEHOLDER = "placeholder"
    OVERLAY_ONLY = "overlay_only"


# ---------------------------------------------------------------------------
# Half-block rendering
# ---------------------------------------------------------------------------

# Upper half-block — fg paints the top half-pixel, bg paints the bottom.
HALF_BLOCK = "\u2580"

# Glyphs for the placeholder mode.
PLACEHOLDER_FILL = "\u2592"     # ▒ medium shade — reads as "art here"
PLACEHOLDER_CORNERS = ("┌", "┐", "└", "┘")


def _load_resized(image_path: Path, w_px: int, h_px: int) -> Image.Image:
    """Load + resize ``image_path`` to (w_px, h_px) RGB. LANCZOS sampler."""
    img = Image.open(image_path).convert("RGB")
    return img.resize((w_px, h_px), Image.LANCZOS)


def render_halfblock(image_path: Path,
                     cells_w: int, cells_h: int) -> List[str]:
    """Render ``image_path`` as ``cells_h`` ANSI lines of ``cells_w`` chars.

    Each line uses upper-half-block (▀) glyphs with truecolor fg + bg so
    each cell encodes a 1×2 pixel block. Result is a true 24-bit colour
    rasterisation — works in any terminal that supports `\\x1b[38;2;...m`
    SGR sequences.

    Lines are RESET-terminated so colour state doesn't bleed into adjacent
    cells when the caller splices them into a wider frame.
    """
    if cells_w <= 0 or cells_h <= 0:
        return []
    img = _load_resized(image_path, cells_w, cells_h * 2)
    px = img.load()
    out: List[str] = []
    for cy in range(cells_h):
        parts: List[str] = []
        last_top: Optional[tuple] = None
        last_bot: Optional[tuple] = None
        for cx in range(cells_w):
            top = px[cx, cy * 2]
            bot = px[cx, cy * 2 + 1]
            # Re-emit SGR only when colour changes — keeps the line short
            # enough that long rows don't blow out terminal buffers.
            if top != last_top:
                parts.append(f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m")
                last_top = top
            if bot != last_bot:
                parts.append(f"\x1b[48;2;{bot[0]};{bot[1]};{bot[2]}m")
                last_bot = bot
            parts.append(HALF_BLOCK)
        parts.append(RESET)
        out.append("".join(parts))
    return out


def render_placeholder(cells_w: int, cells_h: int, *,
                       label: Optional[str] = None) -> List[str]:
    """Solid placeholder block for tiles with no art / no-color terminals.

    Uses a light shade glyph so the tile still reads as "art slot" rather
    than empty space. Optional centered ``label`` is layered on top of the
    middle row (truncated if too wide).
    """
    if cells_w <= 0 or cells_h <= 0:
        return []
    out: List[str] = []
    for cy in range(cells_h):
        out.append(PLACEHOLDER_FILL * cells_w)
    if label:
        text = label[:cells_w]
        mid = cells_h // 2
        pad = (cells_w - len(text)) // 2
        out[mid] = (PLACEHOLDER_FILL * pad
                    + text
                    + PLACEHOLDER_FILL * (cells_w - pad - len(text)))
    return out


def render_overlay_blank(cells_w: int, cells_h: int) -> List[str]:
    """Blank cells, used when a screenshot will paste real PIL pixels here.

    The frame composer stitches these into the layout; the screenshot
    renderer overlays the actual image on top. Live terminals never call
    this path (they'd see nothing), only the PNG pipeline does.
    """
    blank = " " * cells_w
    return [blank for _ in range(cells_h)]


# ---------------------------------------------------------------------------
# Public façade
# ---------------------------------------------------------------------------


@dataclass
class TileArt:
    """One rendered tile-art block.

    ``lines`` are ANSI strings with visible width ``cells_w``. ``image_path``
    is preserved so the PNG renderer can find the source bitmap when the
    mode is OVERLAY_ONLY.
    """
    lines: List[str]
    cells_w: int
    cells_h: int
    image_path: Optional[Path]
    mode: RenderMode


def render_card_art(card_id: str, cells_w: int, cells_h: int, *,
                    skin_slug: Optional[str] = None,
                    mode: RenderMode = RenderMode.HALFBLOCK,
                    placeholder_label: Optional[str] = None) -> TileArt:
    """High-level entry point: card_id → ready-to-splice art block.

    Resolves art via :func:`resolve_card_art`, then dispatches on ``mode``.
    Falls back to PLACEHOLDER when no art is on disk regardless of mode —
    every tile always has *something* to render.
    """
    img = resolve_card_art(card_id, skin_slug=skin_slug)
    effective_mode = mode if img is not None else RenderMode.PLACEHOLDER

    if effective_mode == RenderMode.HALFBLOCK and img is not None:
        lines = render_halfblock(img, cells_w, cells_h)
    elif effective_mode == RenderMode.OVERLAY_ONLY and img is not None:
        lines = render_overlay_blank(cells_w, cells_h)
    else:
        lines = render_placeholder(cells_w, cells_h,
                                   label=placeholder_label or card_id[:cells_w])

    return TileArt(
        lines=lines,
        cells_w=cells_w,
        cells_h=cells_h,
        image_path=img,
        mode=effective_mode,
    )
