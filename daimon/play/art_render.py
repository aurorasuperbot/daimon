"""Card-art rendering primitives for the interactive TUIs.

DAIMON ships its own terminal (the bundled WezTerm — see
:mod:`daimon.render.wezterm_bundle`), so the only live-rendering path is
**Kitty Graphics Protocol** (KGP): full-fidelity PNG bytes streamed via
APC sequences, painted in-place over blank cells in the text frame.

The legacy half-block (▀ truecolor) fallback was removed in Phase E once
the bundled-terminal launcher (Phase C) made every launch land inside our
WezTerm. Users that bypass the auto-relaunch with ``--in-place`` get a
clean error pointing at ``daimon install`` rather than degraded rendering.

Two output paths share this module:

  1. **Live terminal** (bundled WezTerm) — the TUI emits a frame full of
     blank cells via :class:`RenderMode.OVERLAY_ONLY`, then calls
     :func:`paint_overlays_as_kgp` to paint the real bitmaps on top via
     Kitty Graphics Protocol.

  2. **Screenshot** — the PNG renderer in :mod:`daimon.play.screenshot`
     accepts the same :class:`ImageOverlay` records and pastes the real
     PIL bitmaps directly into the canvas at the right cell coordinates.
     Pixel-perfect for design review.

Both paths share one resolver: :func:`resolve_card_art`. It first asks
the lazy fetcher to ensure the card's art directory is on disk (a no-op
hit when the card is already cached, a per-card download on first
render), then defers to the engine's pure :func:`art_path_for` for the
final equipped-skin-aware PNG path. Callers pass in card_id + optional
skin_slug; we return pixels (or ``None`` when the network is gone and
nothing is cached, in which case the renderer falls back to a
placeholder tile).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Asset resolution
# ---------------------------------------------------------------------------


def resolve_card_art(card_id: str, *,
                     skin_slug: Optional[str] = None) -> Optional[Path]:
    """Find the PNG for a card (optionally for a specific skin variant).

    Lazy-fetches the per-card tarball from the active manifest if the
    card's art directory isn't on disk yet — first render of a brand-new
    card pulls ~50-500 KB once and never again. Subsequent renders are
    pure stat calls. A network failure on the lazy fetch falls through
    silently to the cache (which may yield ``None`` if there's nothing
    on disk), and the caller renders a placeholder tile.

    When ``skin_slug`` is given, look it up in the card's manifest first;
    fall through to the canonical art if the slug isn't installed. When
    ``skin_slug`` is None we honor the player's equipped skin via
    ``art_path_for`` so the same card looks the same across all surfaces.

    Returns the absolute path or None if no art is on disk for this card.

    Note: the legacy ``@functools.lru_cache`` on this resolver was
    dropped with the lazy-fetch migration. ``lru_cache`` caches ``None``
    indefinitely, so once a card resolved before its tarball was
    downloaded it would stay broken for the session even after the
    fetch completed. The underlying lookups are stat() calls (sub-μs on
    modern filesystems), so dropping the cache costs negligible CPU.
    """
    from daimon.render.art import (
        _read_manifest,
        _variant_id_for_slug,
        _variant_png,
        art_path_for,
    )
    from daimon.update.lazy import ensure_art_for
    from daimon.update.paths import art_pack_dir

    # JIT-fetch the card's tarball if it isn't cached yet. Soft-fail on
    # network errors — placeholder rendering is the right fallback when
    # we can't reach the registry.
    ensure_art_for(card_id)

    card_dir = art_pack_dir() / card_id

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
    return art_path_for(card_id)


# ---------------------------------------------------------------------------
# Render-mode discriminator
# ---------------------------------------------------------------------------


class RenderMode(str, Enum):
    """How a tile's art region is filled.

    OVERLAY_ONLY: emit blanks; the SCREENSHOT pipeline pastes the PIL
                  image, the LIVE pipeline KGP-paints it. The default
                  and only "real art" mode now that half-block has been
                  retired in favour of the bundled WezTerm.
    PLACEHOLDER:  solid blanks + corner glyphs — for `--no-tui` /
                  no-color / missing-art tiles where there's no PNG to
                  display.
    """

    OVERLAY_ONLY = "overlay_only"
    PLACEHOLDER = "placeholder"


# ---------------------------------------------------------------------------
# Placeholder + overlay-blank glyph rendering
# ---------------------------------------------------------------------------

PLACEHOLDER_FILL = "\u2592"     # ▒ medium shade — reads as "art here"
PLACEHOLDER_CORNERS = ("┌", "┐", "└", "┘")


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
    """Blank cells, used when KGP / the screenshot renderer paints pixels here.

    The frame composer stitches these into the layout; the live pipeline
    then paints the bitmap via :func:`paint_overlays_as_kgp` and the
    screenshot pipeline pastes the PIL image on top.
    """
    blank = " " * cells_w
    return [blank for _ in range(cells_h)]


# ---------------------------------------------------------------------------
# Public façade
# ---------------------------------------------------------------------------


@dataclass
class TileArt:
    """One rendered tile-art block.

    ``lines`` are blank ANSI strings with visible width ``cells_w`` (or
    placeholder glyphs when the card has no art on disk). ``image_path``
    is preserved so the painter / screenshot pipeline can find the source
    bitmap to paint over the blanks.
    """
    lines: List[str]
    cells_w: int
    cells_h: int
    image_path: Optional[Path]
    mode: RenderMode


def render_card_art(card_id: str, cells_w: int, cells_h: int, *,
                    skin_slug: Optional[str] = None,
                    mode: RenderMode = RenderMode.OVERLAY_ONLY,
                    placeholder_label: Optional[str] = None) -> TileArt:
    """High-level entry point: card_id → ready-to-splice art block.

    Resolves art via :func:`resolve_card_art`, then dispatches on ``mode``.
    Falls back to PLACEHOLDER when no art is on disk regardless of mode —
    every tile always has *something* to render.
    """
    img = resolve_card_art(card_id, skin_slug=skin_slug)
    effective_mode = mode if img is not None else RenderMode.PLACEHOLDER

    if effective_mode == RenderMode.OVERLAY_ONLY and img is not None:
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


# ---------------------------------------------------------------------------
# KGP overlay painter — converts ImageOverlay records into Kitty Graphics
# Protocol escapes for in-terminal pixel-perfect rendering.
#
# This is the second-pass complement to ``RenderMode.OVERLAY_ONLY``: the
# tile composer leaves blank cells where the art goes, this paints real
# bitmaps into those cells via KGP. Only works in terminals that implement
# KGP (our bundled WezTerm — see daimon/render/wezterm_bundle.py).
#
# The flow:
#   1. TUI calls render_frame(...) → frame_str + overlays (always OVERLAY_ONLY now)
#   2. TUI prints frame_str (blanks where art goes)
#   3. TUI prints paint_overlays_as_kgp(overlays) — terminal renders bitmaps
#
# Stable image IDs per card (via image_path) let the terminal cache uploads
# across re-renders so cursor moves/selection changes don't re-transmit
# the same bitmap.
# ---------------------------------------------------------------------------


def paint_overlays_as_kgp(overlays, *, clear_first: bool = False) -> str:
    """Convert :class:`ImageOverlay` records into a string of KGP escapes.

    Each overlay is rendered with cursor positioned at its absolute
    (row+1, col+1) — the +1 is for CSI's 1-based addressing. Image IDs
    are stable per ``image_path`` so re-painting the same overlay is a
    no-op transmission (terminal recognises the cached bitmap).

    Wraps the whole batch in save/restore-cursor so the visible cursor
    doesn't drift after the paint pass — important when the TUI has
    additional text to write afterwards (status bar, flash messages).

    When ``clear_first`` is True, prepends a clear-all-images escape —
    useful for full-screen redraws when the layout changed and stale
    bitmaps from the prior frame might still be on-screen.

    Returns an empty string when ``overlays`` is empty (no escapes
    flushed at all, including the save/restore wrapper).
    """
    overlays = [o for o in overlays if getattr(o, "image_path", None) is not None]
    if not overlays:
        return ""

    from daimon.render import kgp

    parts: list[str] = []
    if clear_first:
        parts.append(kgp.encode_clear_all())
    parts.append(kgp.save_cursor())
    for ov in overlays:
        # image_path → stable ID via kgp.image_id_for; encoded inside
        # render_card_art so each call is self-contained (cursor pos +
        # transmit + display).
        parts.append(kgp.render_card_art(
            image_path=ov.image_path,
            card_id=str(ov.image_path),
            skin_slug=None,
            cells_w=ov.cols,
            cells_h=ov.rows,
            cursor_row=ov.row + 1,    # CSI is 1-based
            cursor_col=ov.col + 1,
        ))
    parts.append(kgp.restore_cursor())
    return "".join(parts)
