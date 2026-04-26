"""Card frame composition — produces a PNG of any Card.

This module is the single PIL renderer used everywhere a card needs to look
like a card: web/Telegram exports, the on-disk tile cache that the bundled
WezTerm KGP painter consumes, the screenshot pipeline, every TUI surface.

V1.3 — Full-art rarity ladder
==============================

Design pivot 2026-04-25: the earlier panel layout (header strip / 40 % art
panel / stats strip / flavor strip) wasted ~60 % of the NovelAI character
art via center-crop because the art is portrait (832×1216 ≈ 0.684) and the
panel was landscape (280×157 ≈ 1.78). Solution: **full-art cards**. The art
fills the entire 560×784 card surface (aspect 0.714, near-identical to the
art aspect), and chrome (title, element chip, rarity badge, stats, flavor,
border) overlays on top inside translucent gradient bands.

The canonical render resolution is 560×784 (5:7 portrait). We render at this
size — not the legacy 280×392 — because flavor text (italic serif, the
hardest glyph class to keep crisp through downsampling) becomes unreadable
at 280-wide. Callers that want a smaller tile pass explicit
``width=``/``height=`` and the LANCZOS downsample produces a sharp result
from the higher-resolution source.

Each rarity has a distinct visual treatment that escalates with scarcity:

  * ``common``    — single 1-px hairline border, plain dark overlays.
                    No halo, no ornament. The plain frame says "just a card".
  * ``uncommon``  — single border + small corner studs, subtle vignette
                    overlay on the art. A small green tell.
  * ``rare``      — double border + corner brackets, top radial accent over
                    art, baked diagonal shimmer across the art.
  * ``epic``      — triple-line border with chromatic R/B fringe, full
                    halo over art + mid-edge runic motifs. Animated:
                    6-frame APNG cycling halo breathe + chromatic shift +
                    rune glow.
  * ``legendary`` — gold double border + ornate mid-edge sigils + sparkle
                    starfield over art, foil treatment on stats, sweeping
                    diagonal shimmer. Animated: 12-frame APNG with shimmer
                    sweep, halo breathe, sigil pulse, starfield twinkle.

The art layer is identical for every rarity; only the OVERLAY layers
(background effects, chrome, decoration, border) change. Same character
art ships across all 5 rarity rerolls without re-rendering.

Render-time layout (full-art overlays on top of art):

  0 – 14 %  top overlay band  (gradient fade-in dark, holds title + element
                              chip + rarity badge)
  14 – 64 % pure art           (no overlay; full character visibility)
  64 – 100 % bottom overlay band (gradient fade-in dark, holds stats strip
                              + divider + flavor lines)
  Border + mid-edge ornaments draw on top of everything.

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
  * Animation is implemented in the OVERLAY layer only. The character art
    is composited UNDER the animated overlays — no per-frame art rendering
    cost. A 12-frame legendary takes ~12× the static-frame draw time but
    still completes well under a second on the VPS at 560×784.
  * APNG output uses Pillow's ``save_all=True, append_images=...,
    duration=..., loop=0``. We pick durations so that a full loop is
    1.2 s for epic (200 ms × 6) and 2 s for legendary (~167 ms × 12) —
    slow enough that it reads as ambient breathing, not a flicker.
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

from daimon.engine.types import Card, EffectOp, Element, TargetFilter, TriggerWhen

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_W, DEFAULT_H = 560, 784
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

# Overlay band proportions (fraction of card height)
TOP_BAND_FRAC = 0.18        # top fade band height (holds badge + chip + title)
# 0.56 budgets enough vertical room for worst-case bottom content:
#   stats strip (42 design-px) + 2 wrapped trigger lines (48 design-px) +
#   3-line flavor (51 design-px) + gaps. Gives ~922 supersampled-px in the
#   solid (post-fade) sub-zone at s=6, which fits the worst-case 200-card
#   catalog without overflow.
BOTTOM_BAND_FRAC = 0.56     # bottom fade band height (holds stats strip + triggers + flavor)

# Font fallback search paths.
#
# IMPORTANT — bundled fonts come FIRST. The engine ships its own copy of every
# typeface it draws because the previous "system fonts only" approach silently
# fell through to PIL's `load_default()` 10-px bitmap on any host that didn't
# have the exact serif italic installed (Ubuntu's `fonts-dejavu-core` ships
# Sans + Serif regular/bold but NOT italic — italic lives in the optional
# `fonts-dejavu-extra` package). When that fall-through happened the renderer
# kept producing cards but the flavor text rendered as illegible 10-px
# bitmap glyphs instead of the requested 13-pt italic. We bundle the actual
# TTF files in `daimon/render/fonts/` so render is environment-portable.
#
# System paths stay in the chain as a fallback for development environments
# that haven't pulled the bundled assets, but we hard-fail at import time
# if NONE of the paths resolve — see `_resolve_font_paths` below.
_BUNDLED_FONTS_DIR = Path(__file__).parent / "fonts"

_FONT_BOLD = [
    str(_BUNDLED_FONTS_DIR / "DejaVuSans-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_FONT_REGULAR = [
    str(_BUNDLED_FONTS_DIR / "DejaVuSans.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_FONT_ITALIC = [
    str(_BUNDLED_FONTS_DIR / "DejaVuSerif-Italic.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
]


def _resolve_font_paths() -> None:
    """Verify every required font has at least one resolvable path.

    Failing fast at module import is much friendlier than silently rendering
    cards with a 10-px bitmap fallback (see `_FONT_BOLD` block comment for the
    war story). If a bundled font is missing entirely we raise rather than
    soldier on — the engine wheel must ship its own typefaces.
    """
    missing: list[tuple[str, list[str]]] = []
    for name, paths in (("BOLD", _FONT_BOLD),
                        ("REGULAR", _FONT_REGULAR),
                        ("ITALIC", _FONT_ITALIC)):
        if not any(Path(p).exists() for p in paths):
            missing.append((name, paths))
    if missing:
        details = "\n".join(f"  {n}: tried {ps}" for n, ps in missing)
        raise RuntimeError(
            "daimon.render.compose: required font(s) not found on disk. "
            "The engine ships bundled TTFs in daimon/render/fonts/ — verify "
            "they survived the install/wheel build. Missing:\n" + details
        )


_resolve_font_paths()


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
    """Load the first TTF in `paths` that PIL can open, at the requested size.

    Raises RuntimeError if NO path in the chain works. We deliberately do NOT
    fall through to `ImageFont.load_default()` — that returns a fixed ~10-px
    bitmap font that produces unreadable microtext at the renderer's
    intended sizes. A render that would silently degrade like that is worse
    than a render that fails loud, because the bitmap fallback looks like a
    rendering bug ("flavor is tiny") instead of a font-resolution bug, and
    debugging takes hours (ask me how I know — burned this one 2026-04-26).
    """
    px = size_pt * supersample
    last_err: Optional[OSError] = None
    for p in paths:
        try:
            return ImageFont.truetype(p, px)
        except OSError as e:
            last_err = e
            continue
    raise RuntimeError(
        f"daimon.render.compose._font: no usable TTF in {paths} at size {px}px "
        f"(last error: {last_err}). Bundled fonts live in "
        f"daimon/render/fonts/ — verify they ship with the wheel."
    )


# ---------------------------------------------------------------------------
# Trigger humanization — render the engine's enum-coded triggers as short
# scannable English phrases for the bottom-panel effects block.
#
# These tables are renderer-LOCAL (not in engine/types.py) on purpose: changing
# a phrase is purely cosmetic and must never require an engine change. New ops
# / triggers added to engine/types.py without an entry here render via the
# fallback paths below — never crashes, just shows the raw enum name.
# ---------------------------------------------------------------------------

_WHEN_LABEL = {
    TriggerWhen.ON_BATTLE_START:        "Battle start",
    TriggerWhen.ON_ROUND_START:         "Round start",
    TriggerWhen.ON_ATTACK:              "On attack",
    TriggerWhen.ON_TAKE_DAMAGE:         "When hit",
    TriggerWhen.ON_DEATH:               "On KO",
    TriggerWhen.ON_ALLY_DEATH:          "Ally KO",
    TriggerWhen.ON_TURN_END:            "Turn end",
    TriggerWhen.ON_KILL:                "On kill",
    TriggerWhen.ON_LOW_HP:              "Low HP",
    TriggerWhen.ON_OPENING_ATTACK:      "First attack",
    TriggerWhen.ON_HEAL_RECEIVED:       "When healed",
    TriggerWhen.ON_DAMAGE_TAKEN:        "Damaged",
    TriggerWhen.ON_EXTRA_ACTION_GRANTED: "Extra action",
}

_TARGET_LABEL = {
    TargetFilter.SELF:               "self",
    TargetFilter.ALL_ALLIES:         "allies",
    TargetFilter.ALL_ENEMIES:        "enemies",
    TargetFilter.LOWEST_HP_ENEMY:    "lowest-HP",
    TargetFilter.HIGHEST_HP_ENEMY:   "highest-HP",
    TargetFilter.RANDOM_ENEMY:       "random foe",
    TargetFilter.RANDOM_ALLY:        "random ally",
}

# Legendary rule-change descriptions. Source-of-truth lives in
# daimon/engine/combat.py (the _MUT_L1..L6 constants). These are the
# render-side English versions; keep them in sync if the mechanics change.
# Kept SHORT so the trigger line fits the card width without wrapping;
# the renderer falls through to wrap-to-two-lines if overflow happens
# anyway, but short labels avoid that path for the common case.
_RULE_CHANGE_LABEL = {
    "L1": "Damage adds +1 burn stack",
    "L2": "All allies +2 thorns",
    "L3": "Heals trickle +1 to allies",
    "L4": "Extra-action cap +1",
    "L5": "Ally-KO triggers fire ×2",
    "L6": "Syncretic team +2 elements",
}


def _humanize_op(op: int, target: int, value: int) -> str:
    """Render an effect op as a short English phrase like '+3 ATK to allies'.

    Unknown ops fall through to a generic '<OP_NAME> {value} -> {target}' so
    we never crash — a new op added to the engine without a label here will
    still render as legible-if-ugly fallback text.
    """
    tgt = _TARGET_LABEL.get(target, "target")
    if op == EffectOp.BUFF_ATK:           return f"+{value} ATK to {tgt}"
    if op == EffectOp.DEBUFF_ATK:         return f"\u2212{value} ATK to {tgt}"
    if op == EffectOp.BUFF_DEF:           return f"+{value} DEF to {tgt}"
    if op == EffectOp.DEBUFF_DEF:         return f"\u2212{value} DEF to {tgt}"
    if op == EffectOp.BUFF_SPD:           return f"+{value} SPD to {tgt}"
    if op == EffectOp.HEAL:               return f"heal {tgt} +{value}"
    if op == EffectOp.DAMAGE:             return f"deal {value} dmg to {tgt}"
    if op == EffectOp.ADD_SHIELD:         return f"shield {tgt} +{value}"
    if op == EffectOp.LIFESTEAL:          return f"lifesteal {value} vs {tgt}"
    if op == EffectOp.APPLY_BURN:         return f"burn {tgt} {value}R"
    if op == EffectOp.APPLY_STUN:         return f"stun {tgt} {value}R"
    if op == EffectOp.APPLY_SILENCE:      return f"silence {tgt} {value}R"
    if op == EffectOp.APPLY_TAUNT:        return f"taunt {tgt} {value}R"
    if op == EffectOp.APPLY_POISON:       return f"poison {tgt} {value}R"
    if op == EffectOp.APPLY_BURN_STACK:   return f"+{value} burn stacks on {tgt}"
    if op == EffectOp.THORNS:             return f"thorns {value} on {tgt}"
    if op == EffectOp.GRANT_EXTRA_ACTION: return f"extra action to {tgt}"
    if op == EffectOp.SACRIFICE_SELF:     return "sacrifice self"
    # Fallback: enum name + raw target
    op_name = EffectOp(op).name if op in (e.value for e in EffectOp) else str(op)
    return f"{op_name.lower()} {value} -> {tgt}"


def _humanize_trigger(when: int, op: int, target: int, value: int) -> str:
    """Render a Trigger as 'When-clause: effect-clause'.

    Example: ON_KILL/HEAL/ALL_ALLIES/3 -> 'On kill: heal allies +3'.
    """
    when_label = _WHEN_LABEL.get(when,
                 TriggerWhen(when).name.title() if when in (w.value for w in TriggerWhen) else "Trigger")
    return f"{when_label}: {_humanize_op(op, target, value)}"


def _move_name_for_when(moves: tuple, when_token: str) -> str:
    """Look up the author-chosen move name for a given when-token.

    `moves` is the CardRenderInfo.moves tuple of (name, when_token) pairs.
    Returns "" if no matching move — caller renders the trigger description
    line without a name header.
    """
    for entry in moves:
        if isinstance(entry, dict):
            n = entry.get("name", "")
            w = entry.get("when", "")
        else:
            n, w = entry[0], entry[1]
        if w == when_token:
            return n
    return ""


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


def _fit_art_full(art_path: Path, target_w: int, target_h: int) -> Image.Image:
    """Center-crop and resize art to fully cover (target_w, target_h).

    Used by the full-art layout — art fills the entire card surface, chrome
    overlays on top. Cropping is minimal because card aspect (≈0.714) and
    NovelAI art aspect (≈0.684) are nearly identical.
    """
    art = Image.open(art_path).convert("RGB")
    aw, ah = art.size
    target_aspect = target_w / target_h
    if aw / ah > target_aspect:
        # art too wide — crop sides
        new_w = int(ah * target_aspect)
        art = art.crop(((aw - new_w) // 2, 0, (aw + new_w) // 2, ah))
    else:
        # art too tall — crop top/bottom
        new_h = int(aw / target_aspect)
        art = art.crop((0, (ah - new_h) // 2, aw, (ah + new_h) // 2))
    art = art.resize((target_w, target_h), Image.LANCZOS)
    art = ImageEnhance.Color(art).enhance(1.10)
    art = ImageEnhance.Contrast(art).enhance(1.04)
    return art


def _placeholder_art(card: Card, pal: Palette, W: int, H: int, s: int) -> Image.Image:
    """Fallback when an art file is missing — gradient + huge species letter."""
    img = _vertical_gradient(
        W, H,
        (pal.bg_top[0] + 20, pal.bg_top[1] + 20, pal.bg_top[2] + 30),
        pal.bg_bottom,
    )
    draw = ImageDraw.Draw(img)
    ph_font = _font(_FONT_BOLD, 90, s)
    ph_text = (card.species[:1] or card.card_id[:1] or "?").upper()
    bbox = draw.textbbox((0, 0), ph_text, font=ph_font)
    draw.text(
        ((W - (bbox[2] - bbox[0])) // 2,
         (H - (bbox[3] - bbox[1])) // 2 - 10 * s),
        ph_text, font=ph_font, fill=pal.accent_dark,
    )
    return img


def _alpha_band_top(W: int, H: int, color: tuple,
                    *, height_frac: float, max_alpha: int,
                    fade_frac: float = 0.45) -> Image.Image:
    """RGBA dark band at the top.

    The top of the band is fully opaque ``color`` at ``max_alpha`` so chrome
    text (title / element chip / rarity badge) sits on a solid background.
    The bottom ``fade_frac`` of the band gradients down to transparent so
    the band visually melts into the art instead of hard-edging.
    """
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    band_h = max(1, int(H * height_frac))
    solid_h = int(band_h * (1 - fade_frac))
    fade_h = max(1, band_h - solid_h)
    px = band.load()
    # Solid header zone
    for y in range(solid_h):
        for x in range(W):
            px[x, y] = (*color, max_alpha)
    # Fade-out zone at the band's lower edge
    for y in range(solid_h, solid_h + fade_h):
        t = (y - solid_h) / fade_h
        a = int(max_alpha * ((1 - t) ** 1.4))
        if a <= 0:
            continue
        for x in range(W):
            px[x, y] = (*color, a)
    return band


def _alpha_band_bottom(W: int, H: int, color: tuple,
                       *, height_frac: float, max_alpha: int,
                       fade_frac: float = 0.30) -> Image.Image:
    """RGBA dark band at the bottom.

    The top ``fade_frac`` of the band gradients in from transparent so the
    band visually melts into the art. The remaining lower portion is solid
    ``color`` at ``max_alpha`` so stats + flavor sit on a fully opaque
    background and stay readable on any character art.
    """
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    band_h = max(1, int(H * height_frac))
    fade_h = max(1, int(band_h * fade_frac))
    start_y = H - band_h
    px = band.load()
    # Fade-in zone at the band's upper edge
    for y in range(start_y, start_y + fade_h):
        t = (y - start_y) / fade_h
        a = int(max_alpha * (t ** 1.2))
        if a <= 0:
            continue
        for x in range(W):
            px[x, y] = (*color, a)
    # Solid stats/flavor zone
    for y in range(start_y + fade_h, H):
        for x in range(W):
            px[x, y] = (*color, max_alpha)
    return band


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
    """Mid-edge rune marks for the epic tier — a diamond at the center of
    each border edge. They sit on the border line itself so they never
    collide with title text or flavor text. ``glow`` 0..255 drives the inner
    diamond brightness for the breathing animation.
    """
    s = supersample
    half = 6 * s
    glow = max(0, min(255, glow))
    fill_color = (
        min(255, pal.accent_light[0]),
        min(255, pal.accent_light[1]),
        min(255, max(150, pal.accent_light[2] - 60 + glow // 2)),
    )
    edge_inset = 4 * s
    centers = [
        (w // 2, edge_inset + half // 2),
        (w // 2, h - 1 - edge_inset - half // 2),
        (edge_inset + half // 2, h // 2),
        (w - 1 - edge_inset - half // 2, h // 2),
    ]
    for cx, cy in centers:
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
    """
    s = supersample
    half = 8 * s
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
        draw.ellipse([cx - half, cy - half, cx + half, cy + half],
                     outline=pal.accent, width=2 * s)
        d = half - 2 * s
        if d > 0:
            draw.polygon(
                [(cx, cy - d), (cx + d, cy), (cx, cy + d), (cx - d, cy)],
                outline=pal.accent_light, width=s,
            )
        pip = max(1, 2 * s)
        draw.ellipse([cx - pip, cy - pip, cx + pip, cy + pip], fill=pip_color)


def _radial_halo(w: int, h: int, color: tuple, *,
                 strength: float = 1.0,
                 cy_ratio: float = -0.25) -> Image.Image:
    """Top-down radial halo. ``strength`` scales the alpha cap. Returns RGBA."""
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
    """Constellation field — sparse bright dots over the card. Deterministic
    seed so the same dots stay put across frames; only their twinkle phase
    advances.
    """
    field = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(field)
    n = max(1, int(w * h * density))
    state = 0x9E3779B1
    for _ in range(n):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        x = state % w
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        y = state % h
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        phase = (state & 0xFFFF) / 0xFFFF
        brightness = 0.5 + 0.5 * math.cos(2 * math.pi * (phase - twinkle_phase))
        a = int(220 * brightness)
        if a < 30:
            continue
        # 1-px star + 1-px brighter center for a subtle sparkle look
        fd.point((x, y), fill=(*color, a))
        if a > 160 and 0 < x < w - 1 and 0 < y < h - 1:
            fd.point((x + 1, y), fill=(*color, a // 3))
            fd.point((x - 1, y), fill=(*color, a // 3))
            fd.point((x, y + 1), fill=(*color, a // 3))
            fd.point((x, y - 1), fill=(*color, a // 3))
    return field


def _diagonal_shimmer(w: int, h: int, color: tuple, *,
                      offset: float = 0.0,
                      width_frac: float = 0.18,
                      strength: float = 0.45) -> Image.Image:
    """Soft diagonal gradient band sweeping across the card.

    ``offset`` 0..1 slides the band from upper-left to lower-right.
    """
    band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = band.load()
    cap = max(0, min(255, int(255 * strength)))
    width = max(1.0, w * width_frac)
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
    """R/B fringe on the source — RGBA shift for the epic chromatic look."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    r, g, b, a = img.split()
    r_shift = Image.new("L", img.size, 0)
    r_shift.paste(r, (dx, 0))
    b_shift = Image.new("L", img.size, 0)
    b_shift.paste(b, (-dx, 0))
    fringe = Image.merge("RGBA", (r_shift, Image.new("L", img.size, 0), b_shift,
                                  Image.eval(a, lambda v: int(v * alpha / 255))))
    return Image.alpha_composite(img, fringe)


# ---------------------------------------------------------------------------
# Full-art layout — base art + overlay chrome
# ---------------------------------------------------------------------------

def _draw_art_full(card_img: Image.Image, card: Card, info: "CardRenderInfo",
                   pal: Palette, W: int, H: int, s: int) -> None:
    """Paint the character art (or placeholder) over the entire card surface."""
    if info.art_path and Path(info.art_path).exists():
        art = _fit_art_full(Path(info.art_path), W, H)
    else:
        art = _placeholder_art(card, pal, W, H, s)
    card_img.paste(art, (0, 0))


def _composite_overlay(card_img: Image.Image, overlay: Image.Image) -> Image.Image:
    """Convert card_img to RGBA, alpha_composite overlay, return RGB."""
    rgba = card_img.convert("RGBA")
    return Image.alpha_composite(rgba, overlay).convert("RGB")


def _draw_top_overlay(card_img: Image.Image, card: Card, info: "CardRenderInfo",
                      pal: Palette, W: int, H: int, s: int) -> Image.Image:
    """Translucent gradient band at the top + title text + element chip +
    rarity badge. Returns the new card_img (RGB).
    """
    band = _alpha_band_top(W, H, pal.bg_bottom,
                           height_frac=TOP_BAND_FRAC, max_alpha=215)
    card_img = _composite_overlay(card_img, band)

    draw = ImageDraw.Draw(card_img)

    # Rarity badge — small rounded tag pinned to the top-left corner of the
    # band. Drawn first so the title can sit to its right.
    rt_text = info.rarity.upper()
    rt_font = _font(_FONT_BOLD, 9, s)
    rt_pad_x, rt_pad_y = 5 * s, 3 * s
    rt_x = 6 * s
    rt_y = 5 * s
    rt_bbox = draw.textbbox((rt_x + rt_pad_x, rt_y + rt_pad_y),
                            rt_text, font=rt_font)
    badge_box = [rt_x, rt_y,
                 rt_bbox[2] + rt_pad_x, rt_bbox[3] + rt_pad_y]
    # Rounded rect via Pillow >= 8 supports radius
    draw.rounded_rectangle(
        badge_box,
        radius=int(2.5 * s),
        fill=pal.bg_bottom,
        outline=pal.accent,
        width=max(1, s),
    )
    draw.text((rt_x + rt_pad_x, rt_y + rt_pad_y), rt_text,
              font=rt_font, fill=pal.accent)
    badge_right = badge_box[2]

    # Title — sits on the second row of the band, centered. Badge + chip
    # share the row above, so the title gets the full card width here.
    title_font = _font(_FONT_BOLD, 17, s)
    title = (info.name or card.card_id).upper()
    # Truncate gracefully if too long for the available width
    max_title_w = W - 14 * s
    while title:
        tb = draw.textbbox((0, 0), title, font=title_font)
        if tb[2] - tb[0] <= max_title_w:
            break
        title = title[:-1]
    if title != (info.name or card.card_id).upper():
        title = title.rstrip() + "…"
    tb_final = draw.textbbox((0, 0), title, font=title_font)
    title_w = tb_final[2] - tb_final[0]
    title_x = (W - title_w) // 2
    title_y = badge_box[3] + 4 * s
    # Subtle title shadow for legibility on bright art
    draw.text((title_x + max(1, s // 2), title_y + max(1, s // 2)),
              title, font=title_font, fill=(0, 0, 0))
    draw.text((title_x, title_y), title, font=title_font, fill=pal.accent_light)

    # Element chip — top-right of the band
    chip_text = card.element.name
    chip_font = _font(_FONT_BOLD, 11, s)
    chip_pad_x, chip_pad_y = 5 * s, 3 * s
    chip_bbox = draw.textbbox((0, 0), chip_text, font=chip_font)
    chip_w = chip_bbox[2] - chip_bbox[0]
    chip_h = chip_bbox[3] - chip_bbox[1]
    chip_x2 = W - 6 * s
    chip_y = 5 * s
    chip_box = [chip_x2 - chip_w - 2 * chip_pad_x, chip_y,
                chip_x2, chip_y + chip_h + 2 * chip_pad_y]
    draw.rounded_rectangle(
        chip_box,
        radius=int(2.5 * s),
        fill=pal.bg_bottom,
        outline=pal.accent,
        width=max(1, s),
    )
    draw.text((chip_box[0] + chip_pad_x, chip_box[1] + chip_pad_y),
              chip_text, font=chip_font, fill=pal.accent)

    return card_img


def _draw_bottom_overlay(card_img: Image.Image, card: Card, info: "CardRenderInfo",
                         pal: Palette, W: int, H: int, s: int,
                         *, foil_strength: float = 0.0) -> Image.Image:
    """Translucent gradient band at the bottom + stats strip + flavor text.
    Returns the new card_img (RGB). ``foil_strength`` 0..1 brightens stat
    values toward accent_light for the legendary foil look.
    """
    bottom_fade_frac = 0.30
    band = _alpha_band_bottom(W, H, pal.bg_bottom,
                              height_frac=BOTTOM_BAND_FRAC,
                              max_alpha=240,
                              fade_frac=bottom_fade_frac)
    card_img = _composite_overlay(card_img, band)

    band_h = int(H * BOTTOM_BAND_FRAC)
    band_top = H - band_h
    fade_h = max(1, int(band_h * bottom_fade_frac))
    solid_top = band_top + fade_h
    draw = ImageDraw.Draw(card_img)

    # Divider line at the top of the SOLID zone — separates the
    # melting-into-art fade from the readable stats backing.
    draw.line([(0, solid_top), (W, solid_top)],
              fill=pal.accent_dark, width=max(1, s))

    # Stats strip — placed inside the solid zone so labels/values are
    # always readable regardless of the underlying art's brightness.
    stats_top = solid_top + 6 * s
    stat_label_font = _font(_FONT_BOLD, 10, s)
    stat_val_font = _font(_FONT_BOLD, 20, s)

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
    val_y = stats_top + 14 * s
    for i, (lbl, val, col) in enumerate(stats):
        cx = i * col_w + col_w // 2
        bbox = draw.textbbox((0, 0), lbl, font=stat_label_font)
        lw = bbox[2] - bbox[0]
        draw.text((cx - lw // 2, stats_top), lbl,
                  font=stat_label_font, fill=pal.accent)
        bbox = draw.textbbox((0, 0), str(val), font=stat_val_font)
        vw = bbox[2] - bbox[0]
        # Shadow under stat numbers — keeps them legible if the band is thin
        draw.text((cx - vw // 2 + max(1, s // 2), val_y + max(1, s // 2)),
                  str(val), font=stat_val_font, fill=(0, 0, 0))
        draw.text((cx - vw // 2, val_y), str(val),
                  font=stat_val_font, fill=col)

    # Vertical column separators — subtle ticks between stat columns
    sep_color = pal.accent_dark
    for i in range(1, 4):
        x = i * col_w
        draw.line([(x, stats_top + 2 * s), (x, val_y + 20 * s)],
                  fill=sep_color, width=max(1, s // 2))

    stats_bottom_y = val_y + 22 * s

    # Divider between stats and triggers
    draw.line([(10 * s, stats_bottom_y), (W - 10 * s, stats_bottom_y)],
              fill=pal.accent_dark, width=max(1, s))

    # ----- Triggers / rule-change block -----------------------------------
    # The actual gameplay text — what the card DOES — lives here, between
    # stats and flavor. Each trigger renders as a single line:
    #   "MOVE NAME — humanized effect"
    # If the card pack didn't author a move name for this trigger's `when`,
    # we drop the dash and just render the humanized effect. Legendary
    # rule-changers (card.rule_change != None) render an extra "RULE:" line
    # describing the global mutation. We cap at 2 trigger lines + 1 rule
    # line; surplus triggers are dropped (Phase 5 cards never exceed this).
    trigger_top = stats_bottom_y + 6 * s
    trigger_name_font = _font(_FONT_BOLD, 10, s)
    trigger_desc_font = _font(_FONT_REGULAR, 9, s)
    trigger_line_h = 13 * s          # single-line height
    trigger_wrap_line_h = 12 * s     # per-line height in two-line wrap mode
    # The trigger zone has horizontal padding so we don't render up to the
    # frame border. Anything that exceeds (W - 2 * trigger_pad_x) wraps to
    # two centered lines (move name on top, desc below).
    trigger_pad_x = 8 * s
    trigger_max_w = W - 2 * trigger_pad_x

    triggers_drawn = 0
    cursor_y = trigger_top

    def _draw_centered(text: str, font: ImageFont.ImageFont, fill: tuple,
                       y: int, *, shadow: bool = False) -> int:
        """Draw `text` horizontally-centered in the card. Returns text width."""
        tb = draw.textbbox((0, 0), text, font=font)
        tw = tb[2] - tb[0]
        x = (W - tw) // 2
        if shadow:
            draw.text((x + max(1, s // 2), y + max(1, s // 2)),
                      text, font=font, fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=fill)
        return tw

    def _draw_trigger_line(name: str, desc: str, y: int) -> int:
        """One trigger entry — bold accent name + dim regular description.

        Renders single-line ("NAME  —  desc") when it fits within
        ``trigger_max_w``; otherwise falls through to two centered lines
        (name above, description below). Returns the y-height consumed so
        the caller can advance ``cursor_y`` correctly without overlap.

        The single-vs-two-line decision is made PER TRIGGER, not per card —
        so a card can have one short trigger inline AND one long trigger
        wrapped without all triggers paying the wrap cost.
        """
        sep = "  \u2014  " if name else ""
        name_w = (draw.textbbox((0, 0), name, font=trigger_name_font)[2]
                  if name else 0)
        sep_w = (draw.textbbox((0, 0), sep, font=trigger_desc_font)[2]
                 if sep else 0)
        desc_w = draw.textbbox((0, 0), desc, font=trigger_desc_font)[2]
        total_w = name_w + sep_w + desc_w

        # Single-line path — fits comfortably; preserve compact look
        if total_w <= trigger_max_w:
            x = (W - total_w) // 2
            if name:
                draw.text((x + max(1, s // 2), y + max(1, s // 2)),
                          name, font=trigger_name_font, fill=(0, 0, 0))
                draw.text((x, y), name, font=trigger_name_font, fill=pal.accent)
                x += name_w
                draw.text((x, y), sep, font=trigger_desc_font,
                          fill=pal.accent_dark)
                x += sep_w
            draw.text((x, y), desc, font=trigger_desc_font,
                      fill=pal.accent_light)
            return trigger_line_h

        # Two-line wrap path — name centered above desc. Used for long rule
        # descriptions (e.g. "Rule: Damage adds +1 burn stack" + a name)
        # and any future long-tail authoring.
        if name:
            _draw_centered(name, trigger_name_font, pal.accent, y, shadow=True)
            _draw_centered(desc, trigger_desc_font, pal.accent_light,
                           y + trigger_wrap_line_h)
            return 2 * trigger_wrap_line_h
        # No name — just center the description on a single line
        _draw_centered(desc, trigger_desc_font, pal.accent_light, y)
        return trigger_line_h

    moves = info.moves or ()
    for trig in card.triggers[:2]:
        # Trigger.when is a TriggerWhen IntEnum; its .name matches the JSON
        # token used in the moves array (e.g. "ON_BATTLE_START").
        try:
            when_token = TriggerWhen(int(trig.when)).name
        except ValueError:
            when_token = ""
        move_name = _move_name_for_when(moves, when_token) if when_token else ""
        desc = _humanize_trigger(int(trig.when), int(trig.op),
                                 int(trig.target), int(trig.value))
        consumed = _draw_trigger_line(move_name, desc, cursor_y)
        cursor_y += consumed
        triggers_drawn += 1

    # Legendary rule-change block — one extra line below the triggers (or
    # above flavor if there are no triggers, which is the magma_tyrant case).
    if card.rule_change:
        token = f"RULE_CHANGE_{card.rule_change}"
        rule_move_name = _move_name_for_when(moves, token)
        rule_desc_label = _RULE_CHANGE_LABEL.get(
            card.rule_change, f"Mutation {card.rule_change}"
        )
        # Prefix with "Rule:" so the player can tell this is a global
        # mutation and not a per-trigger ability.
        consumed = _draw_trigger_line(rule_move_name,
                                      f"Rule: {rule_desc_label}", cursor_y)
        cursor_y += consumed
        triggers_drawn += 1

    triggers_bottom_y = (cursor_y if triggers_drawn > 0 else stats_bottom_y)

    # Divider between triggers and flavor (only if we drew anything)
    if triggers_drawn > 0:
        draw.line(
            [(10 * s, triggers_bottom_y + 2 * s),
             (W - 10 * s, triggers_bottom_y + 2 * s)],
            fill=pal.accent_dark, width=max(1, s),
        )

    # ----- Flavor block ---------------------------------------------------
    # Italic serif, centered, capped at 3 lines. Curly quotes (U+201C / U+201D)
    # open on the FIRST rendered line and close on the LAST rendered line so
    # multi-line flavor reads as one continuous quote — the previous code
    # wrapped both quotes around line 0, leaving lines 1+ unquoted and
    # giving an open-then-close-and-open pattern that looked wrong.
    if info.flavor:
        flavor_font = _font(_FONT_ITALIC, 13, s)
        flavor_top = triggers_bottom_y + (10 * s if triggers_drawn > 0 else 8 * s)
        words = info.flavor.split()
        lines: list[str] = []
        current = ""
        # Slightly tighter wrap target since glyphs are bigger now
        for w in words:
            if len(current) + len(w) + 1 > 26:
                lines.append(current); current = w
            else:
                current = (current + " " + w).strip()
        if current:
            lines.append(current)
        line_h = 17 * s
        rendered = lines[:3]
        last_idx = len(rendered) - 1
        for i, line in enumerate(rendered):
            prefix = "\u201c" if i == 0 else ""
            suffix = "\u201d" if i == last_idx else ""
            text = f"{prefix}{line}{suffix}"
            tb = draw.textbbox((0, 0), text, font=flavor_font)
            tw = tb[2] - tb[0]
            tx = (W - tw) // 2
            ty = flavor_top + i * line_h
            # Shadow then text for crisp italic on uneven backgrounds
            draw.text((tx + max(1, s // 2), ty + max(1, s // 2)),
                      text, font=flavor_font, fill=(0, 0, 0))
            draw.text((tx, ty), text, font=flavor_font, fill=pal.accent_light)

    return card_img


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

    `moves` is a list of (display_name, when_token) pairs from the card pack
    JSON `moves` array. The renderer matches each Trigger's `when` against
    these tokens to label the trigger with the author-chosen ability name
    (e.g. "Jade-Skirt's Quiet" for an ON_BATTLE_START trigger). For legendary
    rule-changers the token is the synthetic string `"RULE_CHANGE_<id>"`
    (e.g. `"RULE_CHANGE_L1"`) so the move name labels the mutation block.
    Moves are PURELY cosmetic — engine ignores them entirely.
    """
    name: str = ""
    flavor: str = ""
    rarity: str = "common"
    art_path: Optional[Path] = None
    moves: tuple = ()                  # tuple[tuple[str, str], ...] — (display_name, when_token)


# ---------------------------------------------------------------------------
# Per-tier composers — each returns a fully composed RGB image at supersampled
# size. The dispatcher (compose_card / compose_card_frames) handles
# downsampling + saving.
# ---------------------------------------------------------------------------

def _compose_common(card: Card, info: CardRenderInfo, W: int, H: int, s: int,
                    pal: Palette, *, frame_t: float = 0.0) -> Image.Image:
    """Plain frame: full art + plain dark overlays + single hairline border."""
    card_img = Image.new("RGB", (W, H), pal.bg_bottom)
    _draw_art_full(card_img, card, info, pal, W, H, s)
    card_img = _draw_top_overlay(card_img, card, info, pal, W, H, s)
    card_img = _draw_bottom_overlay(card_img, card, info, pal, W, H, s)

    draw = ImageDraw.Draw(card_img)
    draw.rectangle([0, 0, W - 1, H - 1], outline=pal.accent_dark, width=s)
    return card_img


def _compose_uncommon(card: Card, info: CardRenderInfo, W: int, H: int, s: int,
                      pal: Palette, *, frame_t: float = 0.0) -> Image.Image:
    """Full art + subtle vignette over art + corner studs + single border."""
    card_img = Image.new("RGB", (W, H), pal.bg_bottom)
    _draw_art_full(card_img, card, info, pal, W, H, s)
    # Vignette over the art for depth (darkens edges of the character)
    vignette = _radial_vignette(W, H, strength=0.30)
    card_img = _composite_overlay(card_img, vignette)

    card_img = _draw_top_overlay(card_img, card, info, pal, W, H, s)
    card_img = _draw_bottom_overlay(card_img, card, info, pal, W, H, s)

    draw = ImageDraw.Draw(card_img)
    draw.rectangle([0, 0, W - 1, H - 1], outline=pal.accent, width=2 * s)
    _draw_corner_studs(draw, W, H, pal, s)
    return card_img


def _compose_rare(card: Card, info: CardRenderInfo, W: int, H: int, s: int,
                  pal: Palette, *, frame_t: float = 0.0) -> Image.Image:
    """Full art + top blue halo + vignette + baked diagonal shimmer +
    double border + corner brackets.
    """
    card_img = Image.new("RGB", (W, H), pal.bg_bottom)
    _draw_art_full(card_img, card, info, pal, W, H, s)

    # Top halo overlay (subtle blue glow, low alpha so art still reads)
    halo = _radial_halo(W, H, pal.accent, strength=0.5)
    card_img = _composite_overlay(card_img, halo)
    # Vignette
    vignette = _radial_vignette(W, H, strength=0.35)
    card_img = _composite_overlay(card_img, vignette)

    card_img = _draw_top_overlay(card_img, card, info, pal, W, H, s)
    card_img = _draw_bottom_overlay(card_img, card, info, pal, W, H, s)

    # Baked shimmer (single static frame; legendary animates)
    shimmer = _diagonal_shimmer(W, H, pal.accent_light, offset=0.5, strength=0.16)
    card_img = _composite_overlay(card_img, shimmer)

    draw = ImageDraw.Draw(card_img)
    draw.rectangle([0, 0, W - 1, H - 1], outline=pal.accent, width=2 * s)
    draw.rectangle([2 * s, 2 * s, W - 1 - 2 * s, H - 1 - 2 * s],
                   outline=pal.accent_dark, width=s)
    _draw_corner_brackets(draw, W, H, pal, s)
    return card_img


def _compose_epic(card: Card, info: CardRenderInfo, W: int, H: int, s: int,
                  pal: Palette, *, frame_t: float = 0.0) -> Image.Image:
    """Full art + breathing purple halo + vignette + chromatic R/B fringe +
    triple border + animated mid-edge runes.
    """
    breathe = 0.925 + 0.225 * math.sin(2 * math.pi * frame_t)

    card_img = Image.new("RGB", (W, H), pal.bg_bottom)
    _draw_art_full(card_img, card, info, pal, W, H, s)

    halo = _radial_halo(W, H, pal.accent, strength=breathe)
    card_img = _composite_overlay(card_img, halo)
    vignette = _radial_vignette(W, H, strength=0.30)
    card_img = _composite_overlay(card_img, vignette)

    card_img = _draw_top_overlay(card_img, card, info, pal, W, H, s)
    card_img = _draw_bottom_overlay(card_img, card, info, pal, W, H, s)

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

    # Chromatic R/B fringe (subtle, applies to whole card incl. art) —
    # offset shifts per frame (1..3 px) so the art appears to vibrate
    chroma_dx = 1 + int(2 * abs(math.sin(2 * math.pi * frame_t)))
    card_img = _chromatic_offset(card_img, dx=chroma_dx, alpha=45).convert("RGB")
    return card_img


def _compose_legendary(card: Card, info: CardRenderInfo, W: int, H: int, s: int,
                       pal: Palette, *, frame_t: float = 0.0) -> Image.Image:
    """Full art + breathing gold halo + sparkle starfield over art + foil
    stats + sweeping diagonal shimmer + gold double border + animated
    mid-edge sigils. The most expensive composer.
    """
    breathe = 0.85 + 0.3 * math.sin(2 * math.pi * frame_t)

    card_img = Image.new("RGB", (W, H), pal.bg_bottom)
    _draw_art_full(card_img, card, info, pal, W, H, s)

    halo = _radial_halo(W, H, pal.accent, strength=breathe)
    card_img = _composite_overlay(card_img, halo)
    # Constellation sparkles over the art — twinkle phase advances with frame
    stars = _starfield(W, H, pal.accent_light, density=0.0005,
                       twinkle_phase=frame_t)
    card_img = _composite_overlay(card_img, stars)
    vignette = _radial_vignette(W, H, strength=0.40)
    card_img = _composite_overlay(card_img, vignette)

    card_img = _draw_top_overlay(card_img, card, info, pal, W, H, s)
    # Foil treatment on stats — strength oscillates with breathe
    card_img = _draw_bottom_overlay(card_img, card, info, pal, W, H, s,
                                    foil_strength=0.5 + 0.3 * breathe)

    # Diagonal rainbow shimmer SWEEP — offset advances each frame
    shimmer = _diagonal_shimmer(W, H, pal.accent_light, offset=frame_t,
                                width_frac=0.20, strength=0.35)
    card_img = _composite_overlay(card_img, shimmer)

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
    # Layout scale: `s` plays double duty as the supersample factor (for crisp
    # font/line downsampling) AND the per-pixel coordinate multiplier used
    # throughout the per-tier composers (`5 * s`, `13 * s`, `_font(..., 17, s)`,
    # etc.). When the caller requests a canvas larger than the legacy 280-wide
    # tile, we proportionally bump `s` so font sizes and layout offsets keep
    # the same RELATIVE proportion of the card. Effect: a 2× canvas produces
    # 2× sharper output at the same visual layout, instead of the chrome
    # shrinking to half its proportional size.
    canvas_scale = max(1.0, width / 280.0)
    s = max(1, int(round(supersample * canvas_scale)))
    W, H = width * supersample, height * supersample

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

    # `moves` is the author-chosen ability-name layer. Each entry pairs a
    # display name with the trigger `when` token it labels (or the synthetic
    # "RULE_CHANGE_<id>" for legendary mutations). The renderer matches by
    # `when` token; the engine never reads this list.
    moves_raw = _pick("moves", []) or []
    moves: tuple = tuple(
        (str(m.get("name", "")), str(m.get("when", "")))
        for m in moves_raw
        if isinstance(m, dict)
    )

    return CardRenderInfo(
        name=_pick("name", ""),
        flavor=_pick("flavor", ""),
        rarity=_pick("rarity", "common"),
        art_path=art_path if art_path and Path(art_path).exists() else None,
        moves=moves,
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
