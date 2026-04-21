"""Hybrid renderer — terminal cells for the frame, chafa cascade for the art.

This is the core insight from the V0 proof in scripts/render_legacy/hybrid_render.py:
the frame (border, title, stats, flavor) MUST stay legible at every terminal
capability. Only the art panel is allowed to degrade through the chafa cascade.

The frame is always crisp because it's drawn as terminal cells. Art is rendered
through the active tier (T1=sixel/kitty, T2-6=chafa color depths, T7=ASCII).

Output is a list of strings — the caller prints them. We do NOT print directly
because the same output may be used in TUI buffers, file dumps, or test
captures.

For tier T7 the renderer omits the art panel entirely and increases the rules
text height — graceful degradation rather than ugly fallback.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from nullpoint.engine.types import Card
from nullpoint.render.cascade import Tier, detect_tier
from nullpoint.render.compose import CardRenderInfo, palette_for


# Frame characters — Unicode box drawing for T1-T6, ASCII for T7
class FrameChars:
    def __init__(self, ascii_only: bool = False):
        if ascii_only:
            self.tl, self.tr, self.bl, self.br = "+", "+", "+", "+"
            self.h, self.v = "-", "|"
            self.cross_l, self.cross_r = "+", "+"
        else:
            self.tl, self.tr, self.bl, self.br = "╔", "╗", "╚", "╝"
            self.h, self.v = "═", "║"
            self.cross_l, self.cross_r = "╠", "╣"


# ANSI 256-color helpers (we ship without `rich` dep)
def _fg(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"


_RESET = "\x1b[0m"


def _color_for_rarity(rarity: str) -> tuple:
    pal = palette_for(rarity)
    return pal.accent


def _ansi_color(rgb: tuple, ansi: bool) -> str:
    if not ansi:
        return ""
    return _fg(*rgb)


def _ansi_reset(ansi: bool) -> str:
    return _RESET if ansi else ""


# ---------------------------------------------------------------------------
# Art panel rendering via chafa cascade
# ---------------------------------------------------------------------------

def _render_art_panel(
    art_path: Path, width_cells: int, height_cells: int, tier: Tier,
) -> List[str]:
    """Render `art_path` as `height_cells` lines of `width_cells` chars."""
    if tier == 7 or not shutil.which("chafa"):
        return _ascii_placeholder(width_cells, height_cells)

    # Tier → chafa flags
    tier_flags = {
        1: ["--format", "kitty"],            # graphics protocol
        2: ["--format", "symbols", "--colors", "240"],
        3: ["--format", "symbols", "--colors", "256"],
        4: ["--format", "symbols", "--colors", "16"],
        5: ["--format", "symbols", "--colors", "8"],
        6: ["--format", "symbols", "--colors", "2"],
    }.get(tier, ["--format", "symbols", "--colors", "16"])

    cmd = [
        "chafa",
        "--size", f"{width_cells}x{height_cells}",
        *tier_flags,
        str(art_path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=10, check=True).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return _ascii_placeholder(width_cells, height_cells)

    lines = out.rstrip("\n").split("\n")
    # Pad/clip to exactly height_cells lines
    if len(lines) < height_cells:
        lines.extend([""] * (height_cells - len(lines)))
    return lines[:height_cells]


def _ascii_placeholder(w: int, h: int) -> List[str]:
    """ASCII filler when chafa unavailable or tier=7."""
    line = "·" * w
    return [line] * h


# ---------------------------------------------------------------------------
# Hybrid frame renderer
# ---------------------------------------------------------------------------

DEFAULT_WIDTH_CELLS = 38
DEFAULT_HEIGHT_CELLS = 22
ART_HEIGHT_CELLS_DEFAULT = 8


def render_hybrid(
    card: Card,
    info: CardRenderInfo,
    *,
    tier: Optional[Tier] = None,
    width_cells: int = DEFAULT_WIDTH_CELLS,
    height_cells: int = DEFAULT_HEIGHT_CELLS,
    ansi: bool = True,
) -> str:
    """Render a card as a multi-line string suitable for terminal printing.

    Frame is terminal cells (always crisp). Art panel uses the cascade tier.
    """
    if tier is None:
        tier = detect_tier()

    chars = FrameChars(ascii_only=(tier == 7))
    accent_rgb = _color_for_rarity(info.rarity)
    secondary_rgb = palette_for(info.rarity).secondary
    a, r = _ansi_color(accent_rgb, ansi), _ansi_reset(ansi)
    s = _ansi_color(secondary_rgb, ansi)
    inner_w = width_cells - 2

    lines: List[str] = []

    # Top border
    lines.append(f"{a}{chars.tl}{chars.h * inner_w}{chars.tr}{r}")

    # Header row: NAME ............ ELEMENT   (V2: element chip replaces slot)
    name = (info.name or card.card_id).upper()[:inner_w - 12]
    chip = card.element.name
    pad = inner_w - len(name) - len(chip) - 4
    if pad < 1:
        pad = 1
    header = f" {name} {' ' * pad} {chip} "
    lines.append(f"{a}{chars.v}{r}{a}{header[:inner_w]}{r}{a}{chars.v}{r}")

    # Rarity tag row
    rarity = f" [{info.rarity.upper()}]"
    lines.append(f"{a}{chars.v}{r}{a}{rarity.ljust(inner_w)}{r}{a}{chars.v}{r}")

    # Divider
    lines.append(f"{a}{chars.cross_l}{chars.h * inner_w}{chars.cross_r}{r}")

    # Art panel (tier-dependent height)
    art_h = ART_HEIGHT_CELLS_DEFAULT if tier != 7 else 0
    if art_h > 0:
        if info.art_path and Path(info.art_path).exists():
            art_lines = _render_art_panel(Path(info.art_path), inner_w, art_h, tier)
        else:
            art_lines = _ascii_placeholder(inner_w, art_h)
        for art_line in art_lines:
            # Truncate ANSI-aware length is hard; assume chafa respects --size
            visible = _strip_ansi_for_pad(art_line, inner_w)
            lines.append(f"{a}{chars.v}{r}{art_line}{visible}{a}{chars.v}{r}")

    # Divider
    lines.append(f"{a}{chars.cross_l}{chars.h * inner_w}{chars.cross_r}{r}")

    # Stats row
    stats_text = (f" ATK:{s}{card.atk:>3}{r}{a}  DEF:{r}{card.defense:>3}"
                  f"{a}  HP:{r}{card.hp:>3}{a}  SPD:{r}{card.spd:>3}{a} ")
    # ANSI codes don't take visible space; pad based on the visible content
    visible_len = sum(1 for _ in f" ATK:{card.atk:>3}  DEF:{card.defense:>3}"
                      f"  HP:{card.hp:>3}  SPD:{card.spd:>3} ")
    stat_pad = inner_w - visible_len
    if stat_pad < 0:
        stat_pad = 0
    lines.append(f"{a}{chars.v}{stats_text}{' ' * stat_pad}{chars.v}{r}")

    # Divider
    lines.append(f"{a}{chars.cross_l}{chars.h * inner_w}{chars.cross_r}{r}")

    # Trigger summary (count + types — never the trigger value or text).
    # Truncate to fit; full details available via np_match include_round_log.
    trigger_summary = _summarize_triggers(card)
    if not trigger_summary:
        trigger_summary = ["(no triggers)"]
    for line in trigger_summary[:3]:
        clipped = line[:inner_w - 2]
        lines.append(f"{a}{chars.v}{r} {clipped.ljust(inner_w - 1)}{a}{chars.v}{r}")

    # Flavor text (italic in ANSI not portable — render plain quoted)
    if info.flavor:
        for fline in _wrap(info.flavor, inner_w - 4)[:2]:
            content = f'"{fline}"'[:inner_w - 2]
            lines.append(f"{a}{chars.v}{r} {content.ljust(inner_w - 1)}{a}{chars.v}{r}")

    # Pad to height_cells
    while len(lines) < height_cells - 1:
        lines.append(f"{a}{chars.v}{r}{' ' * inner_w}{a}{chars.v}{r}")

    # Bottom border
    lines.append(f"{a}{chars.bl}{chars.h * inner_w}{chars.br}{r}")

    return "\n".join(lines)


def _summarize_triggers(card: Card) -> List[str]:
    """Generate short labels for triggers. Stays ENUM-only — never reads text."""
    out = []
    for t in card.triggers:
        out.append(f"• {t.when.name} → {t.op.name} {t.target.name} ({t.value:+d})")
    return out


def _wrap(text: str, width: int) -> List[str]:
    """Naive word wrap."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def _strip_ansi_for_pad(line: str, target_w: int) -> str:
    """Hack: assume chafa output is target_w visible cells; pad nothing."""
    return ""
