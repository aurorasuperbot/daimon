"""Unified card tile renderer — the single place that paints a card.

Every battle-UI surface (6v6 grid, pre-match lineup, outcome highlights, fight
log thumbnails) goes through this module. It guarantees cards look identical
everywhere — same frame, same rarity tint, same art treatment — and lets us
iterate on card visuals in ONE place instead of hunting through renderers.

Architecture:
  render_card_tile(card_info, width, height, *, active_effects, is_dead)
    -> PIL.Image.Image

Under the hood, tile rendering delegates to `daimon.render.compose.compose_card`
(the same pipeline that produced the Plasma Lance proof). The compose layer
gives us the expensive, crisp "full-art" card at 280×392 with gold double border,
corner brackets, rarity halo, stats strip, and flavor text. We then LANCZOS-
downsample to the requested tile dimensions and overlay battle-specific state:
  - Flash color border (from ActiveEffect kind=="color_flash")
  - Overlay icon (from ActiveEffect kind=="overlay_icon")
  - Death tint / greyscale for dead cards

Caching:
  Compose is not cheap at supersample=3 (3×3 pixel render + LANCZOS downsample).
  We cache the base composed tile on disk keyed on (name, rarity, w, h, is_dead)
  so repeated frame renders reuse the same base image. Effects are applied in
  memory per-frame — they're the only mutable part of a tile.

V1-alpha placeholder art:
  While the catalog ships with only one legendary art (plasma_lance), we let the
  caller pass a `placeholder_art` path that stands in for every card's art slot.
  This is the "use the only image we have for all cards" escape hatch Santiago
  asked for. When real art lands per-card, callers stop passing it.

Engineered to be replaceable:
  daimon.ui TUI and the HTML exporter will NOT reuse this PIL pipeline — they
  have their own rendering backends. But the CardTileInfo dataclass and the
  rarity palette lookup (daimon.render.compose.palette_for) ARE shared. Treat
  this module as the PIL-specific renderer; the data model is portable.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from daimon.engine.types import Card as EngineCard
from daimon.engine.types import Element as EngineElement
from daimon.play.frame import ActiveEffect, CardState
from daimon.play.schema import Element as PlayElement, LoadoutCard
from daimon.render.compose import (
    DEFAULT_H,
    DEFAULT_W,
    CardRenderInfo,
    compose_card,
    palette_for,
)


# ---------------------------------------------------------------------------
# Public data shape
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardTileInfo:
    """Everything needed to render one card's tile.

    Comes in two flavors in practice:
      1. Pre-match / lineup — built from a LoadoutCard (no runtime HP changes)
      2. Mid-action — built from a CardState (current HP, active effects)
    The helpers below do the conversion.
    """

    name: str
    short_name: str
    rarity: str
    position: int                         # 0..5 — team position (V2)
    hp: int
    hp_max: int
    species: str = ""                     # monster species — used as fallback label
    element: Optional[PlayElement] = None # drives header chip "FIRE"/"WATER"/etc.
    flavor: str = ""
    art_path: Optional[Path] = None
    # Decorative stats (not enforced by the renderer; used for ATK/DEF/SPD labels)
    atk: int = 0
    defense: int = 0
    spd: int = 0


# ---------------------------------------------------------------------------
# Element mapping (play.schema.Element str enum → engine.types.Element IntEnum)
# ---------------------------------------------------------------------------

_PLAY_TO_ENGINE_ELEMENT: dict[PlayElement, EngineElement] = {
    PlayElement.FIRE:   EngineElement.FIRE,
    PlayElement.WATER:  EngineElement.WATER,
    PlayElement.NATURE: EngineElement.NATURE,
    PlayElement.VOLT:   EngineElement.VOLT,
    PlayElement.VOID:   EngineElement.VOID,
    PlayElement.NORMAL: EngineElement.NORMAL,
}


def _engine_element(element: Optional[PlayElement]) -> EngineElement:
    if element is None:
        return EngineElement.NATURE
    return _PLAY_TO_ENGINE_ELEMENT.get(element, EngineElement.NATURE)


# ---------------------------------------------------------------------------
# Placeholder art resolution
# ---------------------------------------------------------------------------

# Env var override so Santiago's VPS and dev-laptops resolve to different files
# without code changes. Falls back to None → compose.py renders a gradient
# placeholder with the slot initial (which is also fine).
_PLACEHOLDER_ENV = "DAIMON_PLACEHOLDER_ART"


def _resolve_placeholder_art(passed: Optional[Path]) -> Optional[Path]:
    if passed is not None:
        p = Path(passed)
        return p if p.exists() else None
    env = os.environ.get(_PLACEHOLDER_ENV)
    if env:
        p = Path(env)
        return p if p.exists() else None
    return None


# ---------------------------------------------------------------------------
# Cache — keep generated tiles on disk so repeated frame renders stay fast.
# ---------------------------------------------------------------------------

def _default_cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "daimon" / "tiles"


_CACHE_DIR: Path = _default_cache_dir()


def set_cache_dir(path: Path) -> None:
    """Override tile cache location (tests use this to get a clean slate)."""
    global _CACHE_DIR
    _CACHE_DIR = Path(path)


def clear_cache() -> None:
    if _CACHE_DIR.exists():
        for f in _CACHE_DIR.glob("*.png"):
            try:
                f.unlink()
            except OSError:
                pass


def _cache_key(
    info: CardTileInfo,
    width: int,
    height: int,
    is_dead: bool,
    placeholder: Optional[Path],
) -> str:
    art_sig = str(placeholder) if placeholder else "none"
    if info.art_path is not None:
        art_sig = str(info.art_path)
    element_sig = info.element.value if info.element is not None else "none"
    raw = "|".join([
        info.name, info.short_name, info.rarity, str(info.position),
        info.species, element_sig,
        str(info.hp), str(info.hp_max),
        str(info.atk), str(info.defense), str(info.spd),
        str(width), str(height),
        "dead" if is_dead else "alive",
        art_sig,
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Core render
# ---------------------------------------------------------------------------

def _derive_decorative_stats(info: CardTileInfo) -> tuple[int, int, int]:
    """Derive decorative atk/def/spd from hp_max + rarity so the stats strip
    isn't blank when a LoadoutCard lands without full combat stats.

    The V1 wire protocol (play.schema.LoadoutCard) only carries hp/hp_max/rarity;
    the full engine Card (with atk/def/spd) lives server-side. For visual parity
    we want every tile to show SOMETHING in the stat columns — so we synthesize
    values proportional to hp_max and rarity tier. These numbers are purely
    display; they do NOT feed into any match logic.
    """
    if info.atk or info.defense or info.spd:
        return int(info.atk), int(info.defense), int(info.spd)

    base = max(1, info.hp_max // 2)
    rarity_bonus = {
        "legendary": 4, "epic": 3, "rare": 2, "uncommon": 1, "common": 0,
    }.get(info.rarity.lower(), 0)
    atk = max(1, base + rarity_bonus - 1)
    defense = max(0, base // 2 + rarity_bonus // 2)
    spd = max(1, (info.hp_max // 3) + rarity_bonus)
    return atk, defense, spd


def _synthesize_engine_card(info: CardTileInfo) -> EngineCard:
    """Adapt a CardTileInfo into the EngineCard shape compose_card() wants."""
    atk, defense, spd = _derive_decorative_stats(info)
    species = info.species or (info.name.lower().replace(" ", "_") or "unknown")
    return EngineCard(
        card_id=info.name.lower().replace(" ", "_") or "unknown",
        species=species,
        element=_engine_element(info.element),
        atk=atk,
        defense=defense,
        hp=max(0, int(info.hp_max)),
        spd=spd,
        triggers=(),
    )


def _apply_death_tint(img: Image.Image) -> Image.Image:
    """Greyscale + darken for dead cards so the grid reads 'this card is out'.

    Keep it subtle — we still want the name legible so the player can see WHICH
    card died at a glance. The HP tick and death overlay icon do the "this just
    happened" work; the tint is the steady-state afterward.
    """
    # Convert to greyscale then back to RGB, then multiply by 0.55 to darken.
    grey = img.convert("L").convert("RGB")
    darkened = Image.eval(grey, lambda v: int(v * 0.55))
    return darkened


def _compose_base_tile(
    info: CardTileInfo,
    width: int,
    height: int,
    is_dead: bool,
    placeholder_art: Optional[Path],
) -> Image.Image:
    """Compose the base tile (without active-effect overlays) and return it.

    Always composes at compose.py's native 280×392 reference dimensions (where
    its font sizes and layout are calibrated), then LANCZOS-downsamples to the
    caller's target (width, height). Composing directly at small sizes made the
    stat-value font overflow the stats strip, so numbers crossed the bottom
    border line. Downsampling from native sidesteps that entirely.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(info, width, height, is_dead, placeholder_art)
    cache_path = _CACHE_DIR / f"{key}.png"
    if cache_path.exists():
        try:
            return Image.open(cache_path).convert("RGBA")
        except OSError:
            pass  # fall through to recompose

    # Native-sized composition is cached separately so many tile sizes of the
    # same card share one expensive compose_card call.
    native_key = _cache_key(info, DEFAULT_W, DEFAULT_H, False, placeholder_art)
    native_path = _CACHE_DIR / f"{native_key}__native.png"

    if not native_path.exists():
        # Prepare CardRenderInfo — prefer the card's own art, then env/placeholder
        effective_art = info.art_path if (info.art_path and Path(info.art_path).exists()) \
            else _resolve_placeholder_art(placeholder_art)

        render_info = CardRenderInfo(
            name=info.name,
            flavor=info.flavor,
            rarity=info.rarity,
            art_path=effective_art,
        )
        engine_card = _synthesize_engine_card(info)
        compose_card(
            engine_card,
            render_info,
            native_path,
            width=DEFAULT_W,
            height=DEFAULT_H,
        )

    img = Image.open(native_path).convert("RGBA")
    if (width, height) != (DEFAULT_W, DEFAULT_H):
        img = img.resize((width, height), Image.LANCZOS)

    if is_dead:
        img = _apply_death_tint(img)

    # Persist the final-sized tile (with death tint applied, if any) so repeat
    # lookups for this exact (info, size, is_dead) combo skip even the resize.
    img.save(cache_path)
    return img


# ---------------------------------------------------------------------------
# Effect overlays (applied per-frame, not cached)
# ---------------------------------------------------------------------------

_EFFECT_RGBA = {
    "red":    (235, 90, 80),
    "green":  (90, 210, 130),
    "blue":   (90, 160, 235),
    "purple": (180, 130, 220),
    "yellow": (230, 198, 90),
    "gray":   (130, 138, 152),
    "cyan":   (120, 210, 210),
    "white":  (220, 225, 235),
}


def _overlay_flash_border(
    img: Image.Image,
    color: str,
    intensity: float,
    *,
    rarity: str = "common",
) -> Image.Image:
    """Paint a soft colored glow around the tile to signal 'this card just fired
    an effect', while PROTECTING the compose-layer rarity frame (gold on
    legendary, purple on epic, blue on rare, green on uncommon).

    Key invariant: the native compose pipeline paints an outer double-border at
    the tile's outermost pixels (3 px outer + 1 px inner after LANCZOS
    downsample — a band of ~5-6 px). Earlier flash overlays painted concentric
    rectangles starting at (0,0), stamping the colored alpha directly on top of
    those border pixels and washing out the gold on legendary.

    Fix: inset the flash rings by `frame_protect` px so they never touch the
    outer border band. The flash pulses INSIDE the frame (over the art region),
    the gold/purple/blue frame stays crisp. For legendary/epic specifically,
    alpha is also capped lower since gold/purple are themselves strong cues
    that a subtle flash won't drown out.
    """
    rgba = _EFFECT_RGBA.get(color, _EFFECT_RGBA["red"])
    # Rarity-aware alpha: gold/purple are vivid borders we want to preserve,
    # so we pulse softer over them. Common/uncommon tolerate a stronger flash.
    alpha_cap = 110 if rarity.lower() in ("legendary", "epic") else 170
    base_alpha = max(0, min(alpha_cap, int(alpha_cap * intensity)))
    w, h = img.size

    # Reserve a band at the outer edge for the rarity double-border. At 130×182
    # (our grid-tile size) the compose border is ~5-6 px wide; 7% of min-dim
    # (~9 px) gives a safety margin so the blur doesn't bleed onto it either.
    frame_protect = max(4, min(w, h) // 14)

    # Outer aura — blurred rings painted INSIDE the frame-protect band so they
    # pulse over the art region, never the gold border.
    halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    aura_thick = max(3, min(w, h) // 18)
    for i in range(aura_thick):
        a = int(base_alpha * (1 - i / max(1, aura_thick)) * 0.9)
        off = frame_protect + i
        hd.rectangle([off, off, w - 1 - off, h - 1 - off],
                     outline=(*rgba, a), width=1)
    halo = halo.filter(ImageFilter.GaussianBlur(radius=max(3, aura_thick // 2)))

    out = img.convert("RGBA")
    out = Image.alpha_composite(out, halo)
    return out


def _overlay_icon(
    img: Image.Image,
    icon: str,
    color: str,
    intensity: float,
) -> Image.Image:
    """Stamp a small icon in the upper-right quadrant of the tile (not dead-center,
    so it doesn't hide the art). Uses DejaVu Sans (not Mono) at a size that scales
    with tile dimension. ASCII fallback for emoji-less fonts.
    """
    # Fall back to an ASCII-safe glyph — DejaVu Mono/Sans have no color emoji tables.
    fallback_map = {
        "💥": "!", "✨": "+", "⚡": "^", "✦": "v",
        "🛡": "#", "☠": "x", "◆": "o", "·": ".",
    }
    glyph = fallback_map.get(icon, icon[:1] or ".")

    rgba = _EFFECT_RGBA.get(color, _EFFECT_RGBA["red"])
    alpha = max(0, min(255, int(255 * intensity)))

    size_px = max(16, int(min(img.size) * 0.24))
    font = _load_overlay_font(size_px)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # position: upper-right, inset 8% of width from right edge, 6% from top
    bbox = od.textbbox((0, 0), glyph, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = img.size[0] - tw - int(img.size[0] * 0.08)
    y = int(img.size[1] * 0.06)
    # dark shadow for legibility on any art
    od.text((x + 2, y + 2), glyph, font=font, fill=(0, 0, 0, min(200, alpha)))
    od.text((x, y), glyph, font=font, fill=(*rgba, alpha))
    return Image.alpha_composite(img.convert("RGBA"), overlay)


_OVERLAY_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _load_overlay_font(size_px: int) -> ImageFont.FreeTypeFont:
    for p in _OVERLAY_FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size_px)
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Live HP bar overlay — shows CURRENT hp/hp_max (stats strip only has max)
# ---------------------------------------------------------------------------

_HP_BAR_FULL = (90, 210, 130)
_HP_BAR_MID = (230, 198, 90)
_HP_BAR_LOW = (235, 90, 80)
_HP_BAR_EMPTY = (40, 44, 54)


def _hp_bar_color(frac: float) -> tuple[int, int, int]:
    if frac > 0.5:
        return _HP_BAR_FULL
    if frac > 0.25:
        return _HP_BAR_MID
    return _HP_BAR_LOW


def _overlay_live_hp(img: Image.Image, hp: int, hp_max: int, is_dead: bool) -> Image.Image:
    """Paint an HP bar + numeric HP readout in the card's flavor-text strip.

    Placement: bottom 17% of the card, inside the gold outer border. Semi-opaque
    backing so the bar reads over whatever the compose flavor area happened to
    show (currently blank, but safe against future decorations).
    """
    w, h = img.size
    # Total bar strip: start at 82% down, end at 96% down (leaves the bottom
    # corner-brackets visible)
    strip_top = int(h * 0.82)
    strip_bottom = int(h * 0.96)
    strip_left = int(w * 0.09)
    strip_right = w - strip_left

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    # Backing panel (subtle dark)
    od.rectangle(
        [strip_left - 2, strip_top - 2, strip_right + 2, strip_bottom + 2],
        fill=(8, 12, 20, 190),
        outline=None,
    )

    # HP bar track
    track_top = strip_top + 2
    track_bottom = strip_bottom - 2
    od.rectangle(
        [strip_left, track_top, strip_right, track_bottom],
        fill=_HP_BAR_EMPTY,
    )

    # Fill
    if hp_max > 0:
        frac = max(0.0, min(1.0, hp / hp_max))
    else:
        frac = 0.0
    fill_w = int((strip_right - strip_left) * frac)
    color = _hp_bar_color(frac)
    if is_dead or frac == 0.0:
        color = _HP_BAR_LOW
    if fill_w > 0:
        od.rectangle(
            [strip_left, track_top, strip_left + fill_w, track_bottom],
            fill=color,
        )

    # Numeric readout — centered over the bar
    font_px = max(10, int((strip_bottom - strip_top) * 0.90))
    font = _load_overlay_font(font_px)
    label = "DEAD" if is_dead else f"{hp}/{hp_max}"
    bbox = od.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (w - tw) // 2
    ty = strip_top + (strip_bottom - strip_top - th) // 2 - 2
    # dark outline for legibility
    od.text((tx + 1, ty + 1), label, font=font, fill=(0, 0, 0, 220))
    od.text((tx, ty), label, font=font, fill=(240, 242, 248, 255))

    return Image.alpha_composite(img.convert("RGBA"), overlay)


# ---------------------------------------------------------------------------
# Conversion helpers — CardState / LoadoutCard → CardTileInfo
# ---------------------------------------------------------------------------

def tile_info_from_loadout(card: LoadoutCard) -> CardTileInfo:
    return CardTileInfo(
        name=card.name,
        short_name=card.short_name or card.name[:7],
        rarity=card.rarity,
        position=card.position,
        species=card.species,
        element=card.element,
        hp=card.hp,
        hp_max=card.hp_max,
        art_path=Path(card.art_path) if card.art_path and Path(card.art_path).exists() else None,
    )


def tile_info_from_card_state(state: CardState) -> CardTileInfo:
    element = state.element if isinstance(state.element, PlayElement) else None
    return CardTileInfo(
        name=state.name,
        short_name=state.short_name,
        rarity=state.rarity,
        position=state.position,
        species=state.species,
        element=element,
        hp=state.hp,
        hp_max=state.hp_max,
    )


def tile_info_from_catalog_payload(
    payload: dict,
    *,
    position: int = 0,
    art_path: Optional[Path] = None,
) -> CardTileInfo:
    """Build a CardTileInfo from a catalog card's JSON payload (dict).

    The TUI screens (shop / collection / loadout-edit) all start from
    catalog dicts loaded by :mod:`daimon.catalog.loader` — this is the
    bridge to the composited-tile renderer. Accepts a dict (not a
    ``CatalogCard``) so this module avoids an import-cycle on catalog.

    ``art_path`` overrides the payload's ``art`` field — pass it when
    a specific skin variant is selected (shop preview / equipped skin).
    Falls back to the payload's own ``art`` value otherwise; finally to
    None (renderer uses the placeholder gradient).

    Combat rarity (legendary/epic/rare/uncommon/common) is read from
    ``payload['rarity']`` — explicitly NOT the shop's price-tier rarity
    (rare/super_rare). The two namespaces are independent.
    """
    elem_str = payload.get("element", "NORMAL")
    # Catalog payloads use uppercase enum names ("FIRE"); PlayElement
    # values are lowercase ("fire"). Try both forms before giving up.
    element: Optional[PlayElement] = None
    if isinstance(elem_str, str):
        try:
            element = PlayElement(elem_str.lower())
        except (ValueError, KeyError):
            try:
                element = PlayElement[elem_str.upper()]
            except (ValueError, KeyError):
                element = None

    name = payload.get("name") or payload.get("card_id") or "?"
    species = payload.get("species") or payload.get("card_id") or ""
    hp_max = int(payload.get("hp", 1))

    if art_path is None:
        art_field = payload.get("art")
        if art_field:
            p = Path(art_field)
            if p.exists():
                art_path = p

    return CardTileInfo(
        name=name,
        short_name=name[:7],
        rarity=payload.get("rarity", "common"),
        position=position,
        species=species,
        element=element,
        flavor=payload.get("flavor", ""),
        hp=hp_max,
        hp_max=hp_max,
        atk=int(payload.get("atk", 0)),
        defense=int(payload.get("def", 0)),
        spd=int(payload.get("spd", 0)),
        art_path=art_path,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def render_card_tile(
    info: CardTileInfo,
    width: int,
    height: int,
    *,
    is_dead: bool = False,
    active_effects: Iterable[ActiveEffect] = (),
    placeholder_art: Optional[Path] = None,
    show_hp_bar: bool = True,
) -> Image.Image:
    """Render one card tile at (width, height) as an RGBA PIL image.

    The base composition (frame, art, rarity tint, stats) is cached on disk.
    Live HP bar + active effects (flash border, overlay icon) are applied per-
    call and never cached — they're the frame-specific state.

    show_hp_bar=False skips the current-HP overlay (used for lineup/outcome
    thumbnails where HP isn't in play yet, and for tiny log thumbnails where
    there isn't room for the readout).
    """
    base = _compose_base_tile(info, width, height, is_dead, placeholder_art)

    flash: Optional[ActiveEffect] = None
    icon: Optional[ActiveEffect] = None
    for e in active_effects:
        if e.kind == "color_flash" and flash is None:
            flash = e
        elif e.kind == "overlay_icon" and icon is None:
            icon = e

    out = base
    if show_hp_bar:
        out = _overlay_live_hp(out, info.hp, info.hp_max, is_dead)
    if flash and flash.color and not is_dead:
        out = _overlay_flash_border(out, flash.color, flash.intensity,
                                    rarity=info.rarity)
    if icon and icon.icon and not is_dead:
        out = _overlay_icon(
            out,
            icon.icon,
            icon.color or (flash.color if flash else "red"),
            icon.intensity,
        )

    return out


def render_card_tile_from_state(
    state: CardState,
    width: int,
    height: int,
    *,
    placeholder_art: Optional[Path] = None,
) -> Image.Image:
    info = tile_info_from_card_state(state)
    return render_card_tile(
        info,
        width,
        height,
        is_dead=state.is_dead,
        active_effects=state.effects,
        placeholder_art=placeholder_art,
    )


def render_card_tile_from_loadout(
    card: LoadoutCard,
    width: int,
    height: int,
    *,
    placeholder_art: Optional[Path] = None,
    show_hp_bar: bool = False,
) -> Image.Image:
    """Render a card from a LoadoutCard. HP bar defaults OFF because pre-match
    screens (lineup) show starting HP anyway; callers who want the live overlay
    (e.g. a lineup with already-damaged cards) can opt in.
    """
    info = tile_info_from_loadout(card)
    return render_card_tile(
        info,
        width,
        height,
        is_dead=(card.hp <= 0),
        placeholder_art=placeholder_art,
        show_hp_bar=show_hp_bar,
    )


def render_card_thumbnail(
    name: str,
    rarity: str,
    position: int,
    height: int,
    *,
    element: Optional[PlayElement] = None,
    species: str = "",
    placeholder_art: Optional[Path] = None,
) -> Image.Image:
    """Tiny card render for fight-log entries. Locks to the compose card aspect
    (280:392 ≈ 0.714) so thumbnails look like scaled-down versions of the same
    tile, not a different card. HP bar is suppressed — too small to read.
    """
    width = max(16, int(round(height * 280 / 392)))
    info = CardTileInfo(
        name=name, short_name=name[:7], rarity=rarity, position=position,
        species=species, element=element,
        hp=1, hp_max=1,
    )
    return render_card_tile(
        info, width, height, placeholder_art=placeholder_art, show_hp_bar=False,
    )


# ---------------------------------------------------------------------------
# Disk-cache path access — for the OVERLAY pipeline (KGP painter + screenshot)
# ---------------------------------------------------------------------------

def compose_tile_to_path(
    info: CardTileInfo,
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
    *,
    is_dead: bool = False,
    placeholder_art: Optional[Path] = None,
) -> Path:
    """Compose the base tile and return the on-disk cache path of the PNG.

    Why this exists: the TUI overlay pipeline (live KGP painter + the
    deterministic screenshot renderer) both refer to art via
    :class:`ImageOverlay.image_path` — a real file on disk. Phase F replaces
    the per-tile *raw* card art in that field with the *composited* tile
    (gold rarity border, element chip, stats strip, flavor text — full
    chrome). Composing in memory then writing to a temp file would
    re-transmit the same bytes on every redraw; instead we lean on
    :func:`_compose_base_tile`'s built-in disk cache so a (info, w, h) combo
    is composed once per session and the KGP image_id (derived from the
    path string) stays stable across redraws.

    Active-effects + live-HP overlays are NOT applied here (they're per-frame
    state, never cached). Pre-match TUIs (shop / collection / loadout-edit)
    don't need them; battle-UI surfaces should keep calling
    :func:`render_card_tile` directly.

    Defaults are the compose-pipeline native size (280×392). Callers free
    to pass smaller dims for bandwidth-sensitive paths, but a single shared
    size across all TUI surfaces maximizes cache reuse — the terminal
    re-uploads each card's bitmap exactly once per session.
    """
    _compose_base_tile(info, width, height, is_dead, placeholder_art)
    key = _cache_key(info, width, height, is_dead, placeholder_art)
    return _CACHE_DIR / f"{key}.png"
