"""
Run the full Legendary Plasma Lance card through the 7-tier cascade
and produce a side-by-side comparison.
"""
from PIL import Image, ImageDraw, ImageFont
import os

SRC = "/opt/leeroy-web/uploads/cards/legendary_full_card.png"
OUT_DIR = "/opt/leeroy-web/uploads/cards"
DISPLAY_W, DISPLAY_H = 280, 392

src = Image.open(SRC).convert("RGB")

def font(size):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

# Tier 1-3: full PNG fidelity (just save the source as the reference)
src.save(f"{OUT_DIR}/leg_tier1.png")

def simulate(eff_w, eff_h, fname, palette_bits=None, grayscale=False):
    img = src.resize((eff_w, eff_h), Image.LANCZOS)
    if palette_bits is not None:
        img = img.quantize(colors=2 ** palette_bits, method=Image.Quantize.MEDIANCUT).convert("RGB")
    if grayscale:
        img = img.convert("L").convert("RGB")
    img = img.resize((DISPLAY_W, DISPLAY_H), Image.NEAREST)
    img.save(f"{OUT_DIR}/{fname}")

simulate(60, 126, "leg_tier4.png", palette_bits=8)
simulate(60, 84,  "leg_tier5.png", palette_bits=8)
simulate(30, 84,  "leg_tier6.png", palette_bits=8)

# Tier 7: ASCII rendering
ASCII_RAMP = "@%#*+=-:. "
ascii_w, ascii_h = 30, 42
gray = src.resize((ascii_w, ascii_h), Image.LANCZOS).convert("L")
canvas = Image.new("RGB", (DISPLAY_W, DISPLAY_H), (10, 10, 15))
d = ImageDraw.Draw(canvas)
fnt = font(12)
cw = DISPLAY_W / ascii_w
ch = DISPLAY_H / ascii_h
for y in range(ascii_h):
    for x in range(ascii_w):
        v = gray.getpixel((x, y))
        idx = int((255 - v) / 255 * (len(ASCII_RAMP) - 1))
        ch_ = ASCII_RAMP[idx]
        shade = 60 + int(v * 0.8)
        d.text((x * cw, y * ch), ch_, font=fnt, fill=(shade, shade, shade))
canvas.save(f"{OUT_DIR}/leg_tier7.png")

# Composite
panel_w, panel_h = 280, 392
gap = 24
label_h = 36
n = 7
total_w = panel_w * n + gap * (n - 1) + 40
total_h = panel_h + label_h + 40
comp = Image.new("RGB", (total_w, total_h), (10, 10, 15))
d = ImageDraw.Draw(comp)
labels = [
    "T1: Kitty graphics\n(full PNG)",
    "T2: iTerm2 inline\n(full PNG)",
    "T3: Sixel\n(full PNG)",
    "T4: Sextant 2x3\n(60x126)",
    "T5: Quarter-block\n(60x84)",
    "T6: Half-block\n(30x84)",
    "T7: ASCII\n(30x42)",
]
files = [
    "leg_tier1.png", "leg_tier1.png", "leg_tier1.png",
    "leg_tier4.png", "leg_tier5.png", "leg_tier6.png", "leg_tier7.png",
]
lf = font(14)
for i, (lbl, f) in enumerate(zip(labels, files)):
    x = 20 + i * (panel_w + gap)
    panel = Image.open(f"{OUT_DIR}/{f}").convert("RGB")
    comp.paste(panel, (x, 20))
    d.text((x, 20 + panel_h + 4), lbl, font=lf, fill=(220, 220, 230))
comp.save(f"{OUT_DIR}/legendary_cascade.png")
print("Saved:", f"{OUT_DIR}/legendary_cascade.png", os.path.getsize(f"{OUT_DIR}/legendary_cascade.png"), "bytes")
