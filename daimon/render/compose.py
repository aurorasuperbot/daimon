"""Card frame composition — produces a PNG of any Card.

This is the parameterized refactor of `scripts/render_legacy/compose_legendary.py`.
That legacy script hard-coded the Plasma Lance content into a single 280×392
proof image; this module takes a Card (or any compatible stat block) and
produces frames at any rarity, with the same supersample-and-downsample
discipline that gave the proof its print quality.

What stayed the same:
  - 3× supersample, LANCZOS downsample (the 3 critical PIL fixes)
  - Hybrid layer composition: gradient base → halo → art → flair → frame
  - Rarity-driven palette (gold/silver/bronze; legendary = full effects)

What's parameterized:
  - Card stats (atk/def/hp/spd/slot) → stats panel
  - Card display name + flavor + rarity → header / flavor / rarity tag
  - Art path → art panel (any aspect ratio, cropped to fit)
  - Output dimensions (defaults to 280×392, V0 proof spec)

What was DROPPED:
  - Lens flare burst, 4-axis ray, sparkle stars, chromatic sheen, gem inlays.
    These are visual-fidelity polish from the proof — they shipped to lock the
    design language. The parameterized renderer keeps the SHAPE (header/art/
    stats/rules/flavor with gold double border) but emits a frame that any
    asset team can decorate by extending `_decorate_legendary()`.
  - All hand-tuned coordinates moved to the `LAYOUT` dict so a designer can
    rebalance without touching code.

The legacy script remains in `scripts/render_legacy/` as a reference for the
visual ceiling. This module is the ground floor every card builds on.
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


# ---------------------------------------------------------------------------
# Helpers
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


# ---------------------------------------------------------------------------
# Main entry point
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


def compose_card(
    card: Card,
    info: CardRenderInfo,
    output_path: Path,
    *,
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
    supersample: int = SUPERSAMPLE,
) -> Path:
    """Render a single card to PNG.

    Layout (all proportions of full height):
      0–8%    header strip     (rarity + name)
      8–48%   art panel        (art_path; placeholder gradient if absent)
      48–58%  stats strip      (atk / def / hp / spd)
      58–80%  rules text       (currently blank — V1.1 derives from triggers)
      80–98%  flavor text      (italic, accent color)
      double gold/rarity border around everything
    """
    pal = palette_for(info.rarity)
    s = supersample
    W, H = width * s, height * s

    # Base gradient
    card_img = _vertical_gradient(W, H, pal.bg_top, pal.bg_bottom)

    # Top radial halo (only on legendary/epic for visual hierarchy)
    if info.rarity.lower() in ("legendary", "epic"):
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        cx, cy = W // 2, -H // 4
        for r in range(int(H * 0.9), 0, -10):
            a = int(80 * (1 - r / (H * 0.9)) ** 2)
            hd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*pal.accent, a))
        halo = halo.filter(ImageFilter.GaussianBlur(20))
        card_img = Image.alpha_composite(card_img.convert("RGBA"), halo).convert("RGB")

    # ART PANEL ---------------------------------------------------------------
    art_top = int(0.08 * H)
    art_bottom = int(0.48 * H)
    art_h = art_bottom - art_top
    if info.art_path and Path(info.art_path).exists():
        art = _fit_art(Path(info.art_path), W, art_h)
        card_img.paste(art, (0, art_top))
    else:
        # Placeholder: dark gradient with species/element initial
        placeholder = _vertical_gradient(W, art_h,
                                         (pal.bg_top[0] + 20, pal.bg_top[1] + 20, pal.bg_top[2] + 30),
                                         pal.bg_bottom)
        card_img.paste(placeholder, (0, art_top))
        ph_draw = ImageDraw.Draw(card_img)
        ph_font = _font(_FONT_BOLD, 60, s)
        ph_text = (card.species[:1] or card.card_id[:1] or "?").upper()
        bbox = ph_draw.textbbox((0, 0), ph_text, font=ph_font)
        ph_draw.text(
            ((W - (bbox[2] - bbox[0])) // 2, art_top + (art_h - (bbox[3] - bbox[1])) // 2 - 10 * s),
            ph_text, font=ph_font, fill=pal.accent_dark,
        )

    draw = ImageDraw.Draw(card_img)
    # Art panel top + bottom borders
    draw.line([(0, art_top - 1), (W, art_top - 1)], fill=pal.accent, width=2 * s)
    draw.line([(0, art_bottom), (W, art_bottom)], fill=pal.accent, width=2 * s)

    # HEADER ------------------------------------------------------------------
    hdr_h = int(0.08 * H)
    hdr_bg = _vertical_gradient(W, hdr_h, pal.bg_bottom, pal.bg_top)
    card_img.paste(hdr_bg, (0, 0))
    draw = ImageDraw.Draw(card_img)
    title_font = _font(_FONT_BOLD, 11, s)
    title = (info.name or card.card_id).upper()
    draw.text((10 * s, int(hdr_h * 0.25)), title, font=title_font, fill=pal.accent_light)
    # element chip on right (replaces slot chip in V2)
    chip_text = card.element.name
    chip_font = _font(_FONT_BOLD, 8, s)
    bbox = draw.textbbox((0, 0), chip_text, font=chip_font)
    cw = bbox[2] - bbox[0]
    draw.text((W - cw - 12 * s, int(hdr_h * 0.35)), chip_text, font=chip_font, fill=pal.accent)
    draw.line([(0, hdr_h - 1), (W, hdr_h - 1)], fill=pal.accent, width=2 * s)

    # Rarity tag overlay on art top-left
    rt_text = info.rarity.upper()
    rt_font = _font(_FONT_BOLD, 8, s)
    rt_x, rt_y = 7 * s, art_top + 5 * s
    bbox = draw.textbbox((rt_x, rt_y), rt_text, font=rt_font)
    draw.rectangle([bbox[0] - 4 * s, bbox[1] - 2 * s, bbox[2] + 4 * s, bbox[3] + 2 * s],
                   fill=pal.bg_bottom, outline=pal.accent)
    draw.text((rt_x, rt_y), rt_text, font=rt_font, fill=pal.accent)

    # STATS STRIP -------------------------------------------------------------
    stats_top = art_bottom + 2 * s
    stats_h = int(0.10 * H)
    stats_bg = _vertical_gradient(W, stats_h,
                                  (pal.bg_top[0], pal.bg_top[1], pal.bg_top[2]),
                                  pal.bg_bottom)
    card_img.paste(stats_bg, (0, stats_top))
    draw = ImageDraw.Draw(card_img)
    draw.line([(0, stats_top + stats_h), (W, stats_top + stats_h)],
              fill=pal.accent, width=2 * s)

    stat_label_font = _font(_FONT_BOLD, 7, s)
    stat_val_font = _font(_FONT_BOLD, 13, s)
    stats = [
        ("ATK", card.atk, pal.secondary),
        ("DEF", card.defense, pal.accent_light),
        ("HP", card.hp, pal.accent_light),
        ("SPD", card.spd, pal.accent),
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

    # FLAVOR TEXT -------------------------------------------------------------
    if info.flavor:
        flavor_font = _font(_FONT_ITALIC, 9, s)
        flavor_top = stats_top + stats_h + 8 * s
        # Naive word-wrap to ~28 chars/line
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
        # Top divider
        draw.line([(10 * s, flavor_top - 4 * s), (W - 10 * s, flavor_top - 4 * s)],
                  fill=pal.accent_dark, width=1)
        for i, line in enumerate(lines[:4]):
            draw.text((10 * s, flavor_top + i * 12 * s), f'"{line}"' if i == 0 else line,
                      font=flavor_font, fill=pal.accent)

    # OUTER DOUBLE BORDER -----------------------------------------------------
    draw.rectangle([0, 0, W - 1, H - 1], outline=pal.accent, width=3 * s)
    draw.rectangle([2 * s, 2 * s, W - 1 - 2 * s, H - 1 - 2 * s],
                   outline=pal.accent_dark, width=s)

    # Corner brackets
    _draw_corner_brackets(draw, W, H, pal, s)

    # Downsample to display resolution (the "3 critical PIL fixes" lock-in)
    final = card_img.resize((width, height), Image.LANCZOS)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final.save(output_path)
    return output_path


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
