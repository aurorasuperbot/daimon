"""
Simulate what /opt/leeroy-web/uploads/cards/plasma_lance_v1.png looks like
when rendered at each terminal-cascade tier.

Assumptions:
- Card is rendered in a terminal slot 30 columns × 42 rows wide
- Each terminal cell is approximately 9px × 18px (typical monospace)
- Display window therefore renders at ~270×756, but we resize all outputs
  to a uniform 280×392 (the card portrait aspect) for fair comparison.

Tiers:
  T1-3: Kitty/iTerm2/Sixel — true PNG. Output = original.
  T4:   Sextant (U+1FB00..1FB3B) — 2×3 sub-cells per char → 60×126 eff. res.
  T5:   Quarter-block (▘▝▖▗▟▙▛▜) — 2×2 per char → 60×84.
  T6:   Half-block (▀)         — 1×2 per char → 30×84.
  T7:   ASCII (@#%*+=-:.)      — 1×1 per char, grayscale → 30×42.
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

SRC = "/opt/leeroy-web/uploads/cards/plasma_lance_v1.png"
OUT_DIR = "/opt/leeroy-web/uploads/cards"
DISPLAY_W, DISPLAY_H = 280, 392

src = Image.open(SRC).convert("RGB")
# Crop the original to the same aspect as display (280:392 = 5:7)
sw, sh = src.size
target_aspect = DISPLAY_W / DISPLAY_H
src_aspect = sw / sh
if src_aspect > target_aspect:
    new_w = int(sh * target_aspect)
    src = src.crop(((sw - new_w) // 2, 0, (sw + new_w) // 2, sh))
else:
    new_h = int(sw / target_aspect)
    src = src.crop((0, (sh - new_h) // 2, sw, (sh + new_h) // 2))

# Tier 1-3: full PNG fidelity
t1 = src.resize((DISPLAY_W, DISPLAY_H), Image.LANCZOS)
t1.save(f"{OUT_DIR}/tier1_kitty.png")

# Helper: simulate a tier by downsampling to (eff_w, eff_h),
# then upscaling NEAREST to display size to preserve the chunky terminal look.
def simulate(eff_w, eff_h, fname, grayscale=False, palette_bits=None):
    img = src.resize((eff_w, eff_h), Image.LANCZOS)
    if palette_bits is not None:
        # Quantize colors (e.g. 256-color terminal)
        img = img.quantize(colors=2 ** palette_bits, method=Image.Quantize.MEDIANCUT).convert("RGB")
    if grayscale:
        img = img.convert("L").convert("RGB")
    img = img.resize((DISPLAY_W, DISPLAY_H), Image.NEAREST)
    img.save(f"{OUT_DIR}/{fname}")

# Tier 4: Sextant — 60w × 126h effective, 256 colors
simulate(60, 126, "tier4_sextant.png", palette_bits=8)

# Tier 5: Quarter-block — 60w × 84h, 256 colors
simulate(60, 84, "tier5_quarter.png", palette_bits=8)

# Tier 6: Half-block — 30w × 84h, 256 colors (one fg + one bg per cell, half height each)
simulate(30, 84, "tier6_half.png", palette_bits=8)

# Tier 7: ASCII — 30w × 42h, grayscale.
# Render as actual ASCII glyphs into a 280×392 canvas using monospace font.
ASCII_RAMP = "@%#*+=-:. "  # dark → light
ascii_w, ascii_h = 30, 42
gray = src.resize((ascii_w, ascii_h), Image.LANCZOS).convert("L")

canvas = Image.new("RGB", (DISPLAY_W, DISPLAY_H), (10, 10, 15))
draw = ImageDraw.Draw(canvas)
# pick a monospace font; fallback to default if not present
font = None
for path in [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
]:
    if os.path.exists(path):
        font = ImageFont.truetype(path, 12)
        break
if font is None:
    font = ImageFont.load_default()

cell_w = DISPLAY_W / ascii_w
cell_h = DISPLAY_H / ascii_h
for y in range(ascii_h):
    for x in range(ascii_w):
        v = gray.getpixel((x, y))
        idx = int((255 - v) / 255 * (len(ASCII_RAMP) - 1))
        ch = ASCII_RAMP[idx]
        # color in a vague green/amber phosphor for vibe
        shade = 60 + int(v * 0.8)
        draw.text((x * cell_w, y * cell_h), ch, font=font, fill=(shade, shade, shade))
canvas.save(f"{OUT_DIR}/tier7_ascii.png")

# Composite: 7 panels side by side with labels
panel_w = 280
panel_h = 392
gap = 24
label_h = 36
n = 7
total_w = panel_w * n + gap * (n - 1) + 40
total_h = panel_h + label_h + 40

comp = Image.new("RGB", (total_w, total_h), (10, 10, 15))
draw = ImageDraw.Draw(comp)
labels = [
    "T1: Kitty graphics\n(full PNG)",
    "T2: iTerm2 inline\n(full PNG)",
    "T3: Sixel\n(full PNG)",
    "T4: Sextant 2×3\n(60×126)",
    "T5: Quarter-block\n(60×84)",
    "T6: Half-block\n(30×84)",
    "T7: ASCII\n(30×42)",
]
files = [
    "tier1_kitty.png",
    "tier1_kitty.png",
    "tier1_kitty.png",
    "tier4_sextant.png",
    "tier5_quarter.png",
    "tier6_half.png",
    "tier7_ascii.png",
]
label_font = font
for i, (lbl, f) in enumerate(zip(labels, files)):
    x = 20 + i * (panel_w + gap)
    panel = Image.open(f"{OUT_DIR}/{f}").convert("RGB")
    comp.paste(panel, (x, 20))
    draw.text((x, 20 + panel_h + 4), lbl, font=label_font, fill=(220, 220, 230))
comp.save(f"{OUT_DIR}/cascade_comparison.png")

print("Generated:")
for f in ["tier1_kitty.png", "tier4_sextant.png", "tier5_quarter.png",
          "tier6_half.png", "tier7_ascii.png", "cascade_comparison.png"]:
    p = f"{OUT_DIR}/{f}"
    print(f"  {p}  ({os.path.getsize(p)} bytes)")
