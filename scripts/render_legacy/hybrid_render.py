"""
HYBRID RENDER — v2, polished.

Frame, title, stats, rules, flavor text are rendered as native TERMINAL CELLS
using Unicode box-drawing + ANSI text. Only the art region uses the cascade.

v2 fixes from v1:
  - Every char is drawn at its exact grid cell (col*CELL_W, row*CELL_H), not as
    concatenated strings. This forces perfect alignment regardless of the font's
    natural advance widths for box-drawing chars (═ ║ ╔ etc).
  - Tier label is in its own band ABOVE the card so it never collides with the
    frame's top-right corner.
  - Bottom row corners drawn explicitly.
  - Replaced multi-byte ⚡ with custom-drawn lightning glyph centered in cell.
"""
from PIL import Image, ImageDraw, ImageFont
import os

ART_SRC = "/opt/leeroy-web/uploads/cards/plasma_lance_v1.png"
OUT_DIR = "/opt/leeroy-web/uploads/cards"

# Terminal cell metrics
CELL_W = 9
CELL_H = 18

# Card grid in terminal cells
COLS = 38
ROWS = 26

# Pixels: card area
CARD_W = COLS * CELL_W   # 342
CARD_H = ROWS * CELL_H   # 468

# Add a label band above the card (3 rows tall)
LABEL_BAND_H = CELL_H * 2

IMG_W = CARD_W
IMG_H = CARD_H + LABEL_BAND_H
CARD_Y_OFFSET = LABEL_BAND_H  # card starts here

# Art region inside the card (in terminal cells, relative to card top)
ART_COL_START = 2
ART_COL_END = COLS - 2          # 36
ART_ROW_START = 4
ART_ROW_END = 14
ART_COLS = ART_COL_END - ART_COL_START   # 34
ART_ROWS = ART_ROW_END - ART_ROW_START   # 10
ART_PX_W = ART_COLS * CELL_W             # 306
ART_PX_H = ART_ROWS * CELL_H             # 180

GOLD = (251, 191, 36)
GOLD_DIM = (180, 130, 30)
GOLD_DARK = (110, 78, 18)
CYAN = (34, 211, 238)
WHITE = (255, 248, 224)
BG = (15, 12, 8)
DIM = (140, 110, 50)


def load_font(size, bold=True):
    p = ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold
         else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    if os.path.exists(p):
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# Pre-render the source art at exactly the art-slot pixel size
src_art = Image.open(ART_SRC).convert("RGB")
sw, sh = src_art.size
target_aspect = ART_PX_W / ART_PX_H
src_aspect = sw / sh
if src_aspect > target_aspect:
    new_w = int(sh * target_aspect)
    src_art = src_art.crop(((sw - new_w) // 2, 0, (sw + new_w) // 2, sh))
else:
    new_h = int(sw / target_aspect)
    src_art = src_art.crop((0, (sh - new_h) // 2, sw, (sh + new_h) // 2))
art_full = src_art.resize((ART_PX_W, ART_PX_H), Image.LANCZOS)


def render_art_for_tier(tier_eff_w, tier_eff_h, palette_bits=8, grayscale=False):
    img = art_full.resize((tier_eff_w, tier_eff_h), Image.LANCZOS)
    if palette_bits is not None:
        img = img.quantize(colors=2 ** palette_bits, method=Image.Quantize.MEDIANCUT).convert("RGB")
    if grayscale:
        img = img.convert("L").convert("RGB")
    return img.resize((ART_PX_W, ART_PX_H), Image.NEAREST)


def render_art_ascii():
    canvas = Image.new("RGB", (ART_PX_W, ART_PX_H), BG)
    d = ImageDraw.Draw(canvas)
    fnt = load_font(13)
    ramp = "@%#*+=-:. "
    gray = art_full.resize((ART_COLS, ART_ROWS), Image.LANCZOS).convert("L")
    for y in range(ART_ROWS):
        for x in range(ART_COLS):
            v = gray.getpixel((x, y))
            idx = int((255 - v) / 255 * (len(ramp) - 1))
            ch = ramp[idx]
            shade = 80 + int(v * 0.7)
            d.text((x * CELL_W, y * CELL_H), ch, font=fnt, fill=(shade, shade, shade))
    return canvas


def render_card(tier_label, art_image):
    """Render one tier as a terminal screenshot. Frame drawn as geometric primitives
    (always aligned), text drawn with natural font advance."""
    canvas = Image.new("RGB", (IMG_W, IMG_H), BG)
    d = ImageDraw.Draw(canvas)

    fnt = load_font(13)
    fnt_b = load_font(13, bold=True)
    fnt_sm = load_font(11)
    fnt_label = load_font(11, bold=True)

    # ── Tier label band (above the card) ────────────────────────────
    bbox = d.textbbox((0, 0), tier_label, font=fnt_label)
    lw = bbox[2] - bbox[0]
    lx = (IMG_W - lw) // 2
    ly = 4
    d.rectangle([lx - 8, ly - 2, lx + lw + 7, ly + 16], fill=(40, 30, 5), outline=GOLD)
    d.text((lx, ly), tier_label, font=fnt_label, fill=GOLD)

    # ── Frame geometry (in pixels) ──────────────────────────────────
    # Frame edges align to cell centers of the outer column/row.
    fx_left = CELL_W // 2                     # 4
    fx_right = (COLS - 1) * CELL_W + CELL_W // 2  # 337
    fy_top = CARD_Y_OFFSET + CELL_H // 2          # offset + 9
    fy_bot = CARD_Y_OFFSET + (ROWS - 1) * CELL_H + CELL_H // 2

    def hline(row, color=GOLD, double=True):
        """Double horizontal line at given card row."""
        y = CARD_Y_OFFSET + row * CELL_H + CELL_H // 2
        if double:
            d.line([(fx_left, y - 2), (fx_right, y - 2)], fill=color, width=1)
            d.line([(fx_left, y + 1), (fx_right, y + 1)], fill=color, width=1)
        else:
            d.line([(fx_left, y), (fx_right, y)], fill=color, width=1)

    def vline_segment(col, y0, y1, color=GOLD, double=True):
        """Double vertical line segment at given column."""
        x = col * CELL_W + CELL_W // 2
        if double:
            d.line([(x - 2, y0), (x - 2, y1)], fill=color, width=1)
            d.line([(x + 1, y0), (x + 1, y1)], fill=color, width=1)
        else:
            d.line([(x, y0), (x, y1)], fill=color, width=1)

    def put_text(col, row, text, color=GOLD, font=fnt):
        """Draw text at cell (col, row) using natural advance. For inline text only —
        not used for frame characters (those are geometric)."""
        x = col * CELL_W
        y = CARD_Y_OFFSET + row * CELL_H
        d.text((x, y), text, font=font, fill=color)

    # Compute exact pixel rows for separators (so vlines connect to them)
    sep_y = lambda row: CARD_Y_OFFSET + row * CELL_H + CELL_H // 2

    # ── Side walls (full height of card) ────────────────────────────
    vline_segment(0,        fy_top, fy_bot, GOLD)
    vline_segment(COLS - 1, fy_top, fy_bot, GOLD)

    # ── Horizontal lines: top, separator-under-title, separator-above-stats,
    #     separator-below-stats, bottom ────────────────────────────
    hline(0)                  # top border
    hline(2)                  # under title
    hline(ART_ROW_END)        # below art
    hline(ART_ROW_END + 3)    # below stats
    hline(ROWS - 1)           # bottom border

    # ── Title row ────────────────────────────────────────────────────
    put_text(2, 1, "PLASMA LANCE", WHITE, fnt_b)
    # Energy chip on the right
    chip_col = COLS - 7
    put_text(chip_col, 1, "[", CYAN, fnt_b)
    # Custom lightning bolt polygon
    bolt_x0 = (chip_col + 1) * CELL_W
    bolt_y0 = CARD_Y_OFFSET + 1 * CELL_H
    bolt = [
        (bolt_x0 + 5, bolt_y0 + 2),
        (bolt_x0 + 1, bolt_y0 + 9),
        (bolt_x0 + 4, bolt_y0 + 9),
        (bolt_x0 + 2, bolt_y0 + 16),
        (bolt_x0 + 8, bolt_y0 + 7),
        (bolt_x0 + 5, bolt_y0 + 7),
        (bolt_x0 + 7, bolt_y0 + 2),
    ]
    d.polygon(bolt, fill=CYAN)
    put_text(chip_col + 2, 1, "3]", CYAN, fnt_b)

    # ── Rarity row ──────────────────────────────────────────────────
    put_text(2, 3, "* ENERGY > LEGENDARY *", GOLD, fnt_b)
    # Re-color sections by overdrawing
    # (kept simple — single gold color reads as the rarity tier)

    # ── Paste art image inside the slot ─────────────────────────────
    art_x = ART_COL_START * CELL_W
    art_y = CARD_Y_OFFSET + ART_ROW_START * CELL_H
    canvas.paste(art_image, (art_x, art_y))

    # ── Stats header ────────────────────────────────────────────────
    r = ART_ROW_END + 1
    stat_cols = ["ATK", "HP", "PRC", "SLOT"]
    col_starts = [3, 11, 18, 26]
    for lbl, c in zip(stat_cols, col_starts):
        put_text(c, r, lbl, GOLD_DIM, fnt_sm)

    # ── Stats values ────────────────────────────────────────────────
    r = ART_ROW_END + 2
    vals = [("5", CYAN), ("3", WHITE), ("1", GOLD), ("L•ARM", WHITE)]
    for (v, col), c in zip(vals, col_starts):
        put_text(c, r, v, col, fnt_b)

    # ── Rules text ──────────────────────────────────────────────────
    r = ART_ROW_END + 4
    put_text(2, r, "> PIERCE 1", CYAN, fnt_b)

    r = ART_ROW_END + 5
    put_text(2, r, "Bypasses 1 enemy defense.", WHITE)

    # ── Flavor text ─────────────────────────────────────────────────
    r = ART_ROW_END + 6
    put_text(2, r, '"Forged from a fallen reactor\'s', GOLD_DIM)
    r = ART_ROW_END + 7
    put_text(2, r, ' core. The lance remembers."', GOLD_DIM)

    return canvas


# ── Build all 7 tier renderings ──────────────────────────────────
tier1_art = art_full
tier4_art = render_art_for_tier(ART_COLS * 2, ART_ROWS * 3, palette_bits=8)
tier5_art = render_art_for_tier(ART_COLS * 2, ART_ROWS * 2, palette_bits=8)
tier6_art = render_art_for_tier(ART_COLS * 1, ART_ROWS * 2, palette_bits=8)
tier7_art = render_art_ascii()

tier_specs = [
    ("T1: Kitty graphics",  tier1_art),
    ("T2: iTerm2 inline",   tier1_art),
    ("T3: Sixel",           tier1_art),
    ("T4: Sextant 2x3",     tier4_art),
    ("T5: Quarter-block",   tier5_art),
    ("T6: Half-block",      tier6_art),
    ("T7: ASCII",           tier7_art),
]

cards = []
for label, art in tier_specs:
    img = render_card(label, art)
    cards.append((label, img))
    safe = label.split(":")[0].lower()
    img.save(f"{OUT_DIR}/hybrid_{safe}.png")

# ── Composite side by side ───────────────────────────────────────
gap = 24
n = 7
total_w = IMG_W * n + gap * (n - 1) + 40
total_h = IMG_H + 70
comp = Image.new("RGB", (total_w, total_h), (8, 8, 12))
d = ImageDraw.Draw(comp)
title_fnt = load_font(16, bold=True)
d.text((20, 20), "Hybrid render v2 — frame & text as terminal cells, art uses cascade",
       font=title_fnt, fill=(220, 220, 230))
for i, (label, img) in enumerate(cards):
    x = 20 + i * (IMG_W + gap)
    comp.paste(img, (x, 56))
comp.save(f"{OUT_DIR}/hybrid_cascade.png")
print(f"Saved {OUT_DIR}/hybrid_cascade.png ({os.path.getsize(f'{OUT_DIR}/hybrid_cascade.png')} bytes)")
