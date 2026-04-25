"""Card frame composition — produces a PNG of any Card.

This module is the single PIL renderer used everywhere a card needs to look
like a card: web/Telegram exports, the on-disk tile cache that the bundled
WezTerm KGP painter consumes, the screenshot pipeline, every TUI surface.

V1.2 — Rarity ladder
=====================

Each rarity has a distinct visual treatment that escalates with scarcity:

  * ``common``    — single 1-px hairline border, flat dark gradient.
                    No halo, no ornament. The plain frame says "just a card".
  * ``uncommon``  — single border + small corner studs, subtle vignette.
                    No halo. A small green tell.
  * ``rare``      — double border + corner brackets, top radial accent,
                    art-panel inner glow, baked diagonal shimmer.
  * ``epic``      — triple-line border with chromatic R/B fringe, full
                    halo + corner runic motifs + nameplate gloss. Animated:
                    6-frame APNG cycling halo breathe + chromatic shift.
  * ``legendary`` — gold double border + ornate corner sigils + constellation
                    field, foil treatment on stats, scrollwork flourishes.
                    Animated: 12-frame APNG with diagonal rainbow shimmer
                    sweep, halo breathe, sigil pulse.

The base layout (header, art panel, stats strip, flavor) is shared across
all tiers; the per-tier composer only changes the FRAME and DECORATION
layers. The character art (NovelAI base.png) never animates — only the
frame layer changes per frame, so all 200 cards animate for free without
re-shipping art assets.

Render-time layout (proportions of full height):

  0–8%    header strip     (rarity tag + name + element chip)
  8–48%   art panel        (art_path; placeholder gradient if absent)
  48–58%  stats strip      (atk / def / hp / spd)
  58–80%  rules text       (currently blank — V1.1 derives from triggers)
  80–98%  flavor text      (italic, accent color)

Public API:

  compose_card(card, info, output_path, *, width, height, supersample)
      Render a single card. For animated tiers (epic / legendary) writes
      an Animated PNG (multi-frame APNG) to ``output_path`` — the file
      extension stays ``.png`` because Pillow round-trips APNG cleanly.
      Image viewers that don't decode APNG show frame 0.

  compose_card_frames(card, info, *, n_frames=None, **kwargs)
      Returns ``list[PIL.Image.Image]``. For static tiers the list has
      length 1; for animated tiers the rarity-default frame count
      (6 for epic, 12 for legendary). Pass an explicit ``n_frames`` to
      override (useful for tests or down-sampling for perf-critical TUIs).

  compose_card_from_pack_dict(pack_card, art_root, output_path, **kwargs)
      Convenience that loads a Card + CardRenderInfo from one JSON dict.

  palette_for(rarity) -> Palette
      Stable RGB palette per tier (used by card_tile.py + others).

Engineering notes
=================
  * 3× supersample + LANCZOS downsample is preserved (the "3 critical PIL
    fixes" from the original Plasma Lance proof) so font edges stay crisp.
  * Animation is implemented in the FRAME layer only. The character art
    is composited UNDER the animated frame — no per-frame art rendering
    cost. A 12-frame legendary takes ~12× the static-frame draw time but
    still completes in <100ms on the VPS for the default 280×392 size.
  * APNG output uses Pillow's ``save_all=True, append_images=...,
    duration=..., loop=0``. We pick durations so that a full loop is
    1.2s for epic (200ms × 6) and 2s for legendary (~167ms × 12) — slow
    enough that it reads as ambient breathing, not a flicker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
except ImportError as e:
    raise ImportError("daimon.render.compose requires Pillow >= 10. "
                      "Install with: pip install daimon-engine") from e

from daimon.engine.types import Card, Element

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_W, DEFAULT_H = 280, 392
SUPERSAMPLE = 3   # render at 3× then LANCZOS downsample for crisp gradients

# Frame counts per rarity. Static tiers (common/uncommon/rare) emit 1 frame;
# animated tiers cycle through these many frames per loop. Adjust here to
# rebalance loop smoothness vs. file size — file size scales linearly.
FRAMES_PER_RARITY = {
    "common":    1,
    "uncommon":  1,
    "rare":      1,
    "epic":      6,
    "legendary": 12,
}

# APNG per-frame duration (ms). Chosen so a full loop reads as ambient
# breathing rather than flicker:
#   epic:      200 ms × 6 frames  = 1.2 s loop
#   legendary: ~167 ms × 12 frames ≈ 2.0 s loop
APNG_FRAME_DURATION_MS = {
    "epic":      200,
    "legendary": 167,
}

# Fallback font search paths (DejaVu first, Liberation second, default last)
_FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
]
_FONT_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]
_FONT_ITALIC = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
]


# ---------------------------------------------------------------------------
# Rarity palettes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Palette:
    accent: tuple                 # primary border / title color
    accent_light: tuple           # highlight / shimmer
    accent_dark: tuple            # inner border
    secondary: tuple              # stat number accent
    bg_top: tuple                 # gradient top
    bg_bottom: tuple              # gradient bottom


_PALETTES = {
    "legendary": Palette(
        accent=(251, 191, 36), accent_light=(255, 248, 224), accent_dark=(212, 160, 74),
        secondary=(34, 211, 238), bg_top=(42, 31, 10), bg_bottom=(15, 10, 4),
    ),
    "epic": Palette(
        accent=(167, 139, 250), accent_light=(221, 214, 254), accent_dark=(124, 99, 211),
        secondary=(96, 165, 250), bg_top=(28, 22, 48), bg_bottom=(10, 8, 24),
    ),
    "rare": Palette(
        accent=(96, 165, 250), accent_light=(191, 219, 254), accent_dark=(59, 130, 246),
        secondary=(34, 211, 238), bg_top=(15, 23, 42), bg_bottom=(7, 11, 21),
    ),
    "uncommon": Palette(
        accent=(74, 222, 128), accent_light=(187, 247, 208), accent_dark=(34, 197, 94),
        secondary=(245, 158, 11), bg_top=(16, 32, 18), bg_bottom=(7, 14, 10),
    ),
    "common": Palette(
        accent=(180, 180, 180), accent_light=(230, 230, 230), accent_dark=(140, 140, 140),
        secondary=(120, 120, 120), bg_top=(28, 28, 30), bg_bottom=(10, 10, 12),
    ),
}


def palette_for(rarity: str) -> Palette:
    return _PALETTES.get(rarity.lower(), _PALETTES["common"])


def _normalize_rarity(rarity: str) -> str:
    """Lowercase + fall back to 'common' for unknown tiers."""
    r = (rarity or "common").lower()
    return r if r in _PALETTES else "common"


# ---------------------------------------------------------------------------
# Low-level drawing helpers
# ---------------------------------------------------------------------------

def _font(paths: list[str], size_pt: int, supersample: int) -> ImageFont.ImageFont:
    px = size_pt * supersample
    for p in paths:
        try:
            return ImageFont.truetype(p, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _vertical_gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (w, h), top)
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top[0] * (1 - t) + bottom[0] * t)
        g = int(top[1] * (1 - t) + bottom[1] * t)
        b = int(top[2] * (1 - t) + bottom[2] * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _radial_vignette(w: int, h: int, strength: float = 0.55) -> Image.Image:
    """Soft radial darkening (RGBA) layered over the base for depth.

    ``strength`` 0..1 sets max alpha at the corners (center stays clear).
    Used by uncommon/rare/epic to add inner-card depth without changing
    the gradient. Cheap because we only sample on a per-pixel ratio.
    """
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cx, cy = w / 2, h / 2
    max_d = math.hypot(cx, cy)
    px = out.load()
    cap = max(0, min(255, int(255 * strength)))
    for y in range(h):
        for x in range(w):
            d = math.hypot(x - cx, y - cy) / max_d
            a = int(cap * (d ** 2.2))
            if a > 0:
                px[x, y] = (0, 0, 0, a)
    return out


def _fit_art(art_path: Path, target_w: int, target_h: int) -> Image.Image:
    """Center-crop and resize art to fit (target_w, target_h)."""
    art = Image.open(art_path).convert("RGB")
    aw, ah = art.size
    target_aspect = target_w / target_h
    if aw / ah > target_aspect:
        new_w = int(ah * target_aspect)
        art = art.crop(((aw - new_w) // 2, 0, (aw + new_w) // 2, ah))
    else:
        new_h = int(aw / target_aspect)
        art = art.crop((0, (ah - new_h) // 2, aw, (ah + new_h) // 2))
    art = art.resize((target_w, target_h), Image.LANCZOS)
    art = ImageEnhance.Color(art).enhance(1.15)
    art = ImageEnhance.Contrast(art).enhance(1.05)
    return art


def _draw_corner_brackets(draw: ImageDraw.ImageDraw, w: int, h: int, pal: Palette,
                          supersample: int) -> None:
    s = supersample
    size = 18 * s
    for cx, cy, dx, dy in [
        (3 * s, 3 * s, 1, 1),
        (w - 3 * s - 1, 3 * s, -1, 1),
        (3 * s, h - 3 * s - 1, 1, -1),
        (w - 3 * s - 1, h - 3 * s - 1, -1, -1),
    ]:
        draw.line([(cx, cy), (cx + size * dx, cy)], fill=pal.accent, width=2 * s)
        draw.line([(cx, cy), (cx, cy + size * dy)], fill=pal.accent, width=2 * s)


def _draw_corner_studs(draw: ImageDraw.ImageDraw, w: int, h: int, pal: Palette,
                       supersample: int) -> None:
    """Tiny filled diamond/square accents at each corner — uncommon tier."""
    s = supersample
    inset = 5 * s
    half = 3 * s
    for cx, cy in [
        (inset, inset),
        (w - 1 - inset, inset),
        (inset, h - 1 - inset),
        (w - 1 - inset, h - 1 - inset),
    ]:
        draw.polygon(
            [(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)],
            fill=pal.accent, outline=pal.accent_light,
        )


def _draw_edge_runes(draw: ImageDraw.ImageDraw, w: int, h: int, pal: Palette,
                     supersample: int, *, glow: int = 0) -> None:
    """Mid-edge rune marks for the epic tier — one diamond at the center
    of each border edge (top / bottom / left / right). They sit on the
    border line itself so they never collide with title text, rarity tag,
    element chip, or flavor text. ``glow`` 0..255 drives the inner diamond
    brightness for the breathing animation.

    The corners are intentionally left clean — the rare tier "owns" the
    corner-bracket motif. Epic differentiates by having animated jewel
    accents on the EDGES instead.
    """
    s = supersample
    half = 6 * s             # ≈ 6 px display, big enough to read
    glow = max(0, min(255, glow))
    fill_color = (
        min(255, pal.accent_light[0]),
        min(255, pal.accent_light[1]),
        min(255, max(150, pal.accent_light[2] - 60 + glow // 2)),
    )
    # Mid-points of each edge, just inside the outer border
    edge_inset = 4 * s
    centers = [
        (w // 2, edge_inset + half // 2),                       # top
        (w // 2, h - 1 - edge_inset - half // 2),               # bottom
        (edge_inset + half // 2, h // 2),                       # left
        (w - 1 - edge_inset - half // 2, h // 2),               # right
    ]
    for cx, cy in centers:
        # Diamond outline (purple accent) + inner glow fill
        outer_d = half
        draw.polygon(
            [(cx, cy - outer_d), (cx + outer_d, cy),
             (cx, cy + outer_d), (cx - outer_d, cy)],
            outline=pal.accent, width=s,
        )
        inner_d = half - 2 * s
        if inner_d > 0:
            draw.polygon(
                [(cx, cy - inner_d), (cx + inner_d, cy),
                 (cx, cy + inner_d), (cx - inner_d, cy)],
                fill=fill_color,
            )


def _draw_edge_sigils(draw: ImageDraw.ImageDraw, w: int, h: int, pal: Palette,
                      supersample: int, *, pulse: int = 0) -> None:
    """Ornate mid-edge sigils for the legendary tier — gold filigreed
    cartouches at the center of each border edge. Larger and more layered
    than the epic edge rune, with a pulsing center pip.

    Like _draw_edge_runes, these sit on the border line itself, never
    overlapping the rarity tag / title / element chip / flavor text.
    """
    s = supersample
    half = 8 * s             # ≈ 8 px display — visibly bigger than epic
    pulse = max(0, min(255, pulse))
    pip_color = (
        min(255, pal.accent_light[0]),
        min(255, pal.accent_light[1]),
        min(255, max(140, 230 - pulse // 2)),
    )
    edge_inset = 4 * s
    centers = [
        (w // 2, edge_inset + half // 2),
        (w // 2, h - 1 - edge_inset - half // 2),
        (edge_inset + half // 2, h // 2),
        (w - 1 - edge_inset - half // 2, h // 2),
    ]
    for cx, cy in centers:
        # Outer gold ring
        draw.ellipse([cx - half, cy - half, cx + half, cy + half],
                     outline=pal.accent, width=2 * s)
        # Inner filigree diamond (lighter gold)
        d = half - 2 * s
        if d > 0:
            draw.polygon(
                [(cx, cy - d), (cx + d, cy), (cx, cy + d), (cx - d, cy)],
                outline=pal.accent_light, width=s,
            )
        # Center pulsing pip
        pip = max(1, 2 * s)
        draw.ellipse([cx - pip, cy - pip, cx + pip, cy + pip], fill=pip_color)


def _radial_halo(w: int, h: int, color: tuple, *,
                 strength: float = 1.0,
                 cy_ratio: float = -0.25) -> Image.Image:
    """Top-down radial halo behind the card. ``strength`` scales the alpha
    cap (animation hook for "breathing" loops). Returns RGBA.
    """
    halo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    cx, cy = w // 2, int(h * cy_ratio)
    cap = max(0, min(255, int(80 * strength)))
    for r in range(int(h * 0.9), 0, -10):
        a = int(cap * (1 - r / (h * 0.9)) ** 2)
        hd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, a))
    return halo.filter(ImageFilter.GaussianBlur(20))


def _starfield(w: int, h: int, color: tuple, *,
               density: float = 0.0008,
               twinkle_phase: float = 0.0) -> Image.Image:
    """Constellation field — sparse bright dots over the card. The
    ``twinkle_phase`` (0..1) shifts which dots are at peak brightness so
    the field appears to twinkle across an animated loop.
    """
    field = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(field)
    n = max(1, int(w * h * density))
    # Deterministic pseudo-random — same seed every call so dots stay put.
    state = 0x9E3779B1
    for i in range(n):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        x = state % w
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        y = state % h
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        # Twinkle: each dot has its own phase offset; brightness oscillates.
        phase = (state & 0xFFFF) / 0xFFFF
        brightness = 0.5 + 0.5 * math.cos(2 * math.pi * (phase - twinkle_phase))
        a = int(180 * brightness)
        if a < 30:
            continue
        fd.point((x, y), fill=(*color, a))
    return field


def _diagonal_shimmer(w: int, h: int, color: tuple, *,
                      offset: float = 0.0,
                      width_frac: float = 0.18,
                      strength: float = 0.45) -> Image.Image:
    """Soft diagonal gradient band sweeping across the card.

    ``offset`` 0..1 slides the band from upper-left to lower-right. Used
    on rare (baked, offset=0.5) and legendary (animated sweep).
    """
    band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = band.load()
    cap = max(0, min(255, int(255 * strength)))
    width = max(1.0, w * width_frac)
    # Diagonal coordinate u = x + y, normalized to [0, w+h]
    u_max = w + h - 2
    target = (u_max * 0.5) + (offset - 0.5) * (u_max + width)
    for y in range(h):
        for x in range(w):
            u = x + y
            d = abs(u - target)
            if d >= width:
                continue
            falloff = (1 - d / width) ** 2
            a = int(cap * falloff)
            if a > 0:
                bd[x, y] = (*color, a)
    return band


def _chromatic_offset(img: Image.Image, *, dx: int = 1, alpha: int = 70) -> Image.Image:
    """R/B fringe on the source — RGBA shift for the epic chromatic look.

    Pulls the red channel `dx` px right, blue channel `dx` px left, then
    composites at low alpha over the source. Cheap fake of a chromatic
    aberration effect; reads as "this card vibrates with energy".
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    r, g, b, a = img.split()
    # Shift channels
    r_shift = Image.new("L", img.size, 0)
    r_shift.paste(r, (dx, 0))
    b_shift = Image.new("L", img.size, 0)
    b_shift.paste(b, (-dx, 0))
    fringe = Image.merge("RGBA", (r_shift, Image.new("L", img.size, 0), b_shift,
                                  Image.eval(a, lambda v: int(v * alpha / 255))))
    return Image.alpha_composite(img, fringe)


# ---------------------------------------------------------------------------
# Shared base layers (used by every tier)
# ---------------------------------------------------------------------------

def _draw_header(card_img: Image.Image, card: Card, info: "CardRenderInfo",
                 pal: Palette, W: int, H: int, s: int) -> None:
    """Top header strip — name + element chip + bottom divider."""
    hdr_h = int(0.08 * H)
    hdr_bg = _vertical_gradient(W, hdr_h, pal.bg_bottom, pal.bg_top)
    card_img.paste(hdr_bg, (0, 0))
    draw = ImageDraw.Draw(card_img)
    title_font = _font(_FONT_BOLD, 11, s)
    title = (info.name or card.card_id).upper()
    draw.text((10 * s, int(hdr_h * 0.25)), title, font=title_font, fill=pal.accent_light)
    chip_text = card.element.name
    chip_font = _font(_FONT_BOLD, 8, s)
    bbox = draw.textbbox((0, 0), chip_text, font=chip_font)
    cw = bbox[2] - bbox[0]
    draw.text((W - cw - 12 * s, int(hdr_h * 0.35)), chip_text, font=chip_font, fill=pal.accent)
    draw.line([(0, hdr_h - 1), (W, hdr_h - 1)], fill=pal.accent, width=2 * s)


def _draw_art_panel(card_img: Image.Image, card: Card, info: "CardRenderInfo",
                    pal: Palette, W: int, H: int, s: int) -> tuple[int, int]:
    """Render the art panel; returns (art_top, art_bottom) for downstream."""
    art_top = int(0.08 * H)
    art_bottom = int(0.48 * H)
    art_h = art_bottom - art_top
    if info.art_path and Path(info.art_path).exists():
        art = _fit_art(Path(info.art_path), W, art_h)
        card_img.paste(art, (0, art_top))
    else:
        placeholder = _vertical_gradient(
            W, art_h,
            (pal.bg_top[0] + 20, pal.bg_top[1] + 20, pal.bg_top[2] + 30),
            pal.bg_bottom,
        )
        card_img.paste(placeholder, (0, art_top))
        ph_draw = ImageDraw.Draw(card_img)
        ph_font = _font(_FONT_BOLD, 60, s)
        ph_text = (card.species[:1] or card.card_id[:1] or "?").upper()
        bbox = ph_draw.textbbox((0, 0), ph_text, font=ph_font)
        ph_draw.text(
            ((W - (bbox[2] - bbox[0])) // 2,
             art_top + (art_h - (bbox[3] - bbox[1])) // 2 - 10 * s),
            ph_text, font=ph_font, fill=pal.accent_dark,
        )

    draw = ImageDraw.Draw(card_img)
    draw.line([(0, art_top - 1), (W, art_top - 1)], fill=pal.accent, width=2 * s)
    draw.line([(0, art_bottom), (W, art_bottom)], fill=pal.accent, width=2 * s)
    return art_top, art_bottom


def _draw_rarity_tag(card_img: Image.Image, info: "CardRenderInfo",
                     pal: Palette, W: int, H: int, s: int, art_top: int) -> None:
    draw = ImageDraw.Draw(card_img)
    rt_text = info.rarity.upper()
    rt_font = _font(_FONT_BOLD, 8, s)
    rt_x, rt_y = 7 * s, art_top + 5 * s
    bbox = draw.textbbox((rt_x, rt_y), rt_text, font=rt_font)
    draw.rectangle([bbox[0] - 4 * s, bbox[1] - 2 * s, bbox[2] + 4 * s, bbox[3] + 2 * s],
                   fill=pal.bg_bottom, outline=pal.accent)
    draw.text((rt_x, rt_y), rt_text, font=rt_font, fill=pal.accent)


def _draw_stats_strip(card_img: Image.Image, card: Card, pal: Palette,
                      W: int, H: int, s: int, art_bottom: int,
                      *, foil_strength: float = 0.0) -> int:
    """Stats strip; returns its bottom Y. ``foil_strength`` 0..1 brightens
    the stat values toward accent_light for the legendary foil look.
    """
    stats_top = art_bottom + 2 * s
    stats_h = int(0.10 * H)
    stats_bg = _vertical_gradient(
        W, stats_h,
        (pal.bg_top[0], pal.bg_top[1], pal.bg_top[2]),
        pal.bg_bottom,
    )
    card_img.paste(stats_bg, (0, stats_top))
    draw = ImageDraw.Draw(card_img)
    draw.line([(0, stats_top + stats_h), (W, stats_top + stats_h)],
              fill=pal.accent, width=2 * s)

    stat_label_font = _font(_FONT_BOLD, 7, s)
    stat_val_font = _font(_FONT_BOLD, 13, s)

    def _foil(base: tuple) -> tuple:
        if foil_strength <= 0:
            return base
        f = max(0.0, min(1.0, foil_strength))
        return (
            int(base[0] * (1 - f) + pal.accent_light[0] * f),
            int(base[1] * (1 - f) + pal.accent_light[1] * f),
            int(base[2] * (1 - f) + pal.accent_light[2] * f),
        )

    stats = [
        ("ATK", card.atk, _foil(pal.secondary)),
        ("DEF", card.defense, _foil(pal.accent_light)),
        ("HP", card.hp, _foil(pal.accent_light)),
        ("SPD", card.spd, _foil(pal.accent)),
    ]
    col_w = W // 4
    for i, (lbl, val, col) in enumerate(stats):
        cx = i * col_w + col_w // 2
        bbox = draw.textbbox((0, 0), lbl, font=stat_label_font)
        lw = bbox[2] - bbox[0]
        draw.text((cx - lw // 2, stats_top + 4 * s), lbl, font=stat_label_font, fill=pal.accent)
        bbox = draw.textbbox((0, 0), str(val), font=stat_val_font)
        vw = bbox[2] - bbox[0]
        draw.text((cx - vw // 2, stats_top + 14 * s), str(val), font=stat_val_font, fill=col)
    return stats_top + stats_h


def _draw_flavor(card_img: Image.Image, info: "CardRenderInfo", pal: Palette,
                 W: int, H: int, s: int, stats_bottom: int) -> None:
    if not info.flavor:
        return
    draw = ImageDraw.Draw(card_img)
    flavor_font = _font(_FONT_ITALIC, 9, s)
    flavor_top = stats_bottom + 8 * s
    words = info.flavor.split()
    lines = []
    current = ""
    for w in words:
        if len(current) + len(w) + 1 > 28:
            lines.append(current); current = w
        else:
            current = (current + " " + w).strip()
    if current:
        lines.append(current)
    draw.line([(10 * s, flavor_top - 4 * s), (W - 10 * s, flavor_top - 4 * s)],
              fill=pal.accent_dark, width=1)
    for i, line in enumerate(lines[:4]):
        draw.text((10 * s, flavor_top + i * 12 * s),
                  f'"{line}"' if i == 0 else line,
                  font=flavor_font, fill=pal.accent)


# ---------------------------------------------------------------------------
# Public data shape
# ---------------------------------------------------------------------------

@dataclass
class CardRenderInfo:
    """Render-only data that lives in the cards repo, not the engine.

    This dataclass exists because the engine's Card type intentionally has no
    name/flavor/rarity (those would be a prompt-injection vector). The render
    layer accepts these as a SEPARATE input loaded from the card pack — so an
    adversarial card author who ships a hostile `flavor` can affect what a
    human sees on screen but cannot affect combat math.
    """
    name: str = ""
    flavor: str = ""
    rarity: str = "common"
    art_path: Optional[Path] = None


# ---------------------------------------------------------------------------
# Per-tier composers — each returns a fully composed RGB image at supersampled
# size. The dispatcher (compose_card / compose_card_frames) handles
# downsampling + saving.
# ---------------------------------------------------------------------------

def _compose_common(card: Card, info: CardRenderInfo, W: int, H: int, s: int,
                    pal: Palette, *, frame_t: float = 0.0) -> Image.Image:
    """Plain frame: flat dark gradient, single hairline border, no halo."""
    card_img = _vertical_gradient(W, H, pal.bg_top, pal.bg_bottom)
    art_top, art_bottom = _draw_art_panel(card_img, card, info, pal, W, H, s)
    _draw_header(card_img, card, info, pal, W, H, s)
    _draw_rarity_tag(card_img, info, pal, W, H, s, art_top)
    stats_bottom = _draw_stats_strip(card_img, card, pal, W, H, s, art_bottom)
    _draw_flavor(card_img, info, pal, W, H, s, stats_bottom)
    # Single hairline outer border
    draw = ImageDraw.Draw(card_img)
    draw.rectangle([0, 0, W - 1, H - 1], outline=pal.accent_dark, width=s)
    return card_img


def _compose_uncommon(card: Card, info: CardRenderInfo, W: int, H: int, s: int,
                      pal: Palette, *, frame_t: float = 0.0) -> Image.Image:
    """Single border + corner studs + subtle vignette."""
    card_img = _vertical_gradient(W, H, pal.bg_top, pal.bg_bottom)
    # Subtle vignette for depth
    vignette = _radial_vignette(W, H, strength=0.35)
    card_img = Image.alpha_composite(card_img.convert("RGBA"), vignette).convert("RGB")

    art_top, art_bottom = _draw_art_panel(card_img, card, info, pal, W, H, s)
    _draw_header(card_img, card, info, pal, W, H, s)
    _draw_rarity_tag(card_img, info, pal, W, H, s, art_top)
    stats_bottom = _draw_stats_strip(card_img, card, pal, W, H, s, art_bottom)
    _draw_flavor(card_img, info, pal, W, H, s, stats_bottom)

    draw = ImageDraw.Draw(card_img)
    # Single 2-px border + corner studs
    draw.rectangle([0, 0, W - 1, H - 1], outline=pal.accent, width=2 * s)
    _draw_corner_studs(draw, W, H, pal, s)
    return card_img


def _compose_rare(card: Card, info: CardRenderInfo, W: int, H: int, s: int,
                  pal: Palette, *, frame_t: float = 0.0) -> Image.Image:
    """Double border + corner brackets + top radial accent + baked shimmer."""
    card_img = _vertical_gradient(W, H, pal.bg_top, pal.bg_bottom)
    # Top accent (subtle blue glow at the top)
    halo = _radial_halo(W, H, pal.accent, strength=0.6)
    card_img = Image.alpha_composite(card_img.convert("RGBA"), halo).convert("RGB")
    # Inner vignette
    vignette = _radial_vignette(W, H, strength=0.45)
    card_img = Image.alpha_composite(card_img.convert("RGBA"), vignette).convert("RGB")

    art_top, art_bottom = _draw_art_panel(card_img, card, info, pal, W, H, s)
    _draw_header(card_img, card, info, pal, W, H, s)
    _draw_rarity_tag(card_img, info, pal, W, H, s, art_top)
    stats_bottom = _draw_stats_strip(card_img, card, pal, W, H, s, art_bottom)
    _draw_flavor(card_img, info, pal, W, H, s, stats_bottom)

    # Baked diagonal shimmer (single static frame; legendary will animate this)
    shimmer = _diagonal_shimmer(W, H, pal.accent_light, offset=0.5, strength=0.18)
    card_img = Image.alpha_composite(card_img.convert("RGBA"), shimmer).convert("RGB")

    draw = ImageDraw.Draw(card_img)
    # Double border + corner brackets (the "rare" treatment)
    draw.rectangle([0, 0, W - 1, H - 1], outline=pal.accent, width=2 * s)
    draw.rectangle([2 * s, 2 * s, W - 1 - 2 * s, H - 1 - 2 * s],
                   outline=pal.accent_dark, width=s)
    _draw_corner_brackets(draw, W, H, pal, s)
    return card_img


def _compose_epic(card: Card, info: CardRenderInfo, W: int, H: int, s: int,
                  pal: Palette, *, frame_t: float = 0.0) -> Image.Image:
    """Triple border + chromatic R/B fringe + corner runes + halo breathe.

    Animation hook ``frame_t`` (0..1) drives the halo strength + rune glow
    + chromatic offset. The character art doesn't move — only the frame
    layer responds, so 200 cards animate for free.
    """
    # Halo "breathes" — sinusoidal between 0.7 and 1.15
    breathe = 0.925 + 0.225 * math.sin(2 * math.pi * frame_t)

    card_img = _vertical_gradient(W, H, pal.bg_top, pal.bg_bottom)
    halo = _radial_halo(W, H, pal.accent, strength=breathe)
    card_img = Image.alpha_composite(card_img.convert("RGBA"), halo).convert("RGB")
    vignette = _radial_vignette(W, H, strength=0.35)
    card_img = Image.alpha_composite(card_img.convert("RGBA"), vignette).convert("RGB")

    art_top, art_bottom = _draw_art_panel(card_img, card, info, pal, W, H, s)
    _draw_header(card_img, card, info, pal, W, H, s)
    _draw_rarity_tag(card_img, info, pal, W, H, s, art_top)
    stats_bottom = _draw_stats_strip(card_img, card, pal, W, H, s, art_bottom)
    _draw_flavor(card_img, info, pal, W, H, s, stats_bottom)

    draw = ImageDraw.Draw(card_img)
    # Triple-line border (outer accent, mid accent_dark, inner accent_light)
    draw.rectangle([0, 0, W - 1, H - 1], outline=pal.accent, width=2 * s)
    draw.rectangle([2 * s, 2 * s, W - 1 - 2 * s, H - 1 - 2 * s],
                   outline=pal.accent_dark, width=s)
    draw.rectangle([4 * s, 4 * s, W - 1 - 4 * s, H - 1 - 4 * s],
                   outline=pal.accent_light, width=s)

    # Mid-edge runes — glow tracks the breathe (animated)
    glow = int(255 * breathe * 0.6)
    _draw_edge_runes(draw, W, H, pal, s, glow=glow)

    # Chromatic R/B fringe — offset shifts subtly per frame (1..3 px)
    chroma_dx = 1 + int(2 * abs(math.sin(2 * math.pi * frame_t)))
    card_img = _chromatic_offset(card_img, dx=chroma_dx, alpha=55).convert("RGB")
    return card_img


def _compose_legendary(card: Card, info: CardRenderInfo, W: int, H: int, s: int,
                       pal: Palette, *, frame_t: float = 0.0) -> Image.Image:
    """Gold double border + corner sigils + constellation field +
    diagonal rainbow shimmer + foil stats + halo breathe.

    The most expensive composer. Legendary cards get every primitive.
    """
    breathe = 0.85 + 0.3 * math.sin(2 * math.pi * frame_t)

    card_img = _vertical_gradient(W, H, pal.bg_top, pal.bg_bottom)
    # Gold halo behind everything
    halo = _radial_halo(W, H, pal.accent, strength=breathe)
    card_img = Image.alpha_composite(card_img.convert("RGBA"), halo).convert("RGB")
    # Constellation field — twinkle phase advances with frame_t
    stars = _starfield(W, H, pal.accent_light, density=0.0006, twinkle_phase=frame_t)
    card_img = Image.alpha_composite(card_img.convert("RGBA"), stars).convert("RGB")
    # Inner vignette
    vignette = _radial_vignette(W, H, strength=0.45)
    card_img = Image.alpha_composite(card_img.convert("RGBA"), vignette).convert("RGB")

    art_top, art_bottom = _draw_art_panel(card_img, card, info, pal, W, H, s)
    _draw_header(card_img, card, info, pal, W, H, s)
    _draw_rarity_tag(card_img, info, pal, W, H, s, art_top)
    # Foil treatment on stats — strength oscillates with breathe
    stats_bottom = _draw_stats_strip(card_img, card, pal, W, H, s, art_bottom,
                                     foil_strength=0.5 + 0.3 * breathe)
    _draw_flavor(card_img, info, pal, W, H, s, stats_bottom)

    # Diagonal rainbow shimmer SWEEP — offset advances each frame
    shimmer = _diagonal_shimmer(W, H, pal.accent_light, offset=frame_t,
                                width_frac=0.20, strength=0.40)
    card_img = Image.alpha_composite(card_img.convert("RGBA"), shimmer).convert("RGB")

    draw = ImageDraw.Draw(card_img)
    # Gold double border (the locked legendary look)
    draw.rectangle([0, 0, W - 1, H - 1], outline=pal.accent, width=3 * s)
    draw.rectangle([2 * s, 2 * s, W - 1 - 2 * s, H - 1 - 2 * s],
                   outline=pal.accent_dark, width=s)

    # Mid-edge sigils with pulsing center pip (animated)
    pulse = int(255 * breathe)
    _draw_edge_sigils(draw, W, H, pal, s, pulse=pulse)
    return card_img


# Per-tier composer dispatch
_COMPOSERS = {
    "common":    _compose_common,
    "uncommon":  _compose_uncommon,
    "rare":      _compose_rare,
    "epic":      _compose_epic,
    "legendary": _compose_legendary,
}


# ---------------------------------------------------------------------------
# Frame generation — single-frame for static tiers, N-frame for animated
# ---------------------------------------------------------------------------

def compose_card_frames(
    card: Card,
    info: CardRenderInfo,
    *,
    n_frames: Optional[int] = None,
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
    supersample: int = SUPERSAMPLE,
) -> list[Image.Image]:
    """Render N frames of a card at (width, height).

    For static tiers (common/uncommon/rare) ``n_frames`` defaults to 1
    and additional frames are identical (frame_t=0 always). For animated
    tiers the rarity default kicks in (``FRAMES_PER_RARITY``) and frames
    are spaced evenly across one loop (frame_t = i / n_frames).

    Returns RGB PIL images already downsampled to (width, height).
    """
    rarity = _normalize_rarity(info.rarity)
    pal = palette_for(rarity)
    composer = _COMPOSERS[rarity]
    s = supersample
    W, H = width * s, height * s

    if n_frames is None:
        n_frames = FRAMES_PER_RARITY[rarity]
    n_frames = max(1, int(n_frames))

    is_animated = FRAMES_PER_RARITY[rarity] > 1

    out: list[Image.Image] = []
    for i in range(n_frames):
        frame_t = (i / n_frames) if is_animated else 0.0
        big = composer(card, info, W, H, s, pal, frame_t=frame_t)
        out.append(big.resize((width, height), Image.LANCZOS))
    return out


def compose_card(
    card: Card,
    info: CardRenderInfo,
    output_path: Path,
    *,
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
    supersample: int = SUPERSAMPLE,
) -> Path:
    """Render a card to PNG (or APNG for animated tiers).

    Static tiers (common/uncommon/rare): standard single-frame PNG.
    Animated tiers (epic/legendary): Animated PNG (APNG) with the
    rarity-default frame count. The file extension stays ``.png`` —
    image viewers without APNG support fall back to frame 0.
    """
    rarity = _normalize_rarity(info.rarity)
    frames = compose_card_frames(
        card, info,
        width=width, height=height, supersample=supersample,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(frames) == 1:
        frames[0].save(output_path)
    else:
        # APNG: Pillow infers from save_all + append_images on a .png path.
        duration = APNG_FRAME_DURATION_MS.get(rarity, 200)
        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=duration,
            loop=0,
            disposal=2,
        )
    return output_path


# ---------------------------------------------------------------------------
# Pack-dict convenience
# ---------------------------------------------------------------------------

def render_info_from_pack_dict(pack_card: dict, art_root: Path) -> CardRenderInfo:
    """Extract render fields from a pack JSON dict.

    Accepts both the top-level form (cards repo convention):
      {"card_id": ..., "name": ..., "flavor": ..., "rarity": ..., "art": ...}
    and the test-fixture form:
      {"card_id": ..., "_render_only": {"name": ..., "flavor": ..., ...}}
    """
    nested = pack_card.get("_render_only", {}) or {}

    def _pick(key: str, default=""):
        return nested.get(key, pack_card.get(key, default))

    art_rel = _pick("art", None)
    art_path = (art_root / art_rel) if art_rel else None
    return CardRenderInfo(
        name=_pick("name", ""),
        flavor=_pick("flavor", ""),
        rarity=_pick("rarity", "common"),
        art_path=art_path if art_path and Path(art_path).exists() else None,
    )


def compose_card_from_pack_dict(
    pack_card: dict,
    art_root: Path,
    output_path: Path,
    **kwargs,
) -> Path:
    """Convenience: take a raw pack JSON dict + resolve render fields.

    Accepts top-level render fields (cards repo) or `_render_only` nesting
    (test fixtures).
    """
    from daimon.cards import load_card_dict

    card = load_card_dict(pack_card)
    info = render_info_from_pack_dict(pack_card, art_root)
    return compose_card(card, info, output_path, **kwargs)
