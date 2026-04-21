"""
Compose the full Legendary Plasma Lance card (280×392) in PIL,
matching the chat-embedded version: gold border, filigree corners,
lens flare, sparkles, sheen, gradient title, glowing stats, flavor text.

Then render at 2x for smooth gradients and downsample to 280×392.
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math

ART = "/opt/leeroy-web/uploads/cards/plasma_lance_v1.png"
OUT = "/opt/leeroy-web/uploads/cards/legendary_full_card.png"

S = 3  # supersample factor
W, H = 280 * S, 392 * S

GOLD = (251, 191, 36)
GOLD_LIGHT = (255, 248, 224)
GOLD_DARK = (212, 160, 74)
CYAN = (34, 211, 238)
WHITE = (255, 255, 255)

def font(size, bold=True):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size * S)
        except OSError:
            continue
    return ImageFont.load_default()

def serif_italic(size):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size * S)
        except OSError:
            continue
    return ImageFont.load_default()

# ── Base card with vertical gradient brown→black ─────────────────────────
card = Image.new("RGB", (W, H), (15, 10, 4))
px = card.load()
for y in range(H):
    t = y / H
    r = int(42 * (1 - t * 0.85) + 15 * t * 0.85)
    g = int(31 * (1 - t * 0.85) + 10 * t * 0.85)
    b = int(10 * (1 - t * 0.85) + 4 * t * 0.85)
    for x in range(W):
        px[x, y] = (r, g, b)

# Foil-stamp diagonal weave (very subtle)
overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
od = ImageDraw.Draw(overlay)
spacing = 7 * S
for d in range(-H, W + H, spacing):
    od.line([(d, 0), (d + H, H)], fill=(*GOLD, 12), width=1)
for d in range(-H, W + H, spacing * 2):
    od.line([(d, 0), (d + H, H)], fill=(*GOLD_LIGHT, 10), width=1)
card.paste(Image.alpha_composite(card.convert("RGBA"), overlay).convert("RGB"))

# Top radial gold halo
halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
hd = ImageDraw.Draw(halo)
cx, cy = W // 2, -H // 4
for r in range(int(H * 0.9), 0, -10):
    a = int(80 * (1 - r / (H * 0.9)) ** 2)
    hd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*GOLD, a))
halo = halo.filter(ImageFilter.GaussianBlur(20))
card = Image.alpha_composite(card.convert("RGBA"), halo).convert("RGB")

# ── Art region ───────────────────────────────────────────────────────────
ART_TOP = 32 * S
ART_H = 170 * S
art = Image.open(ART).convert("RGB")
aw, ah = art.size
target_aspect = W / ART_H
if aw / ah > target_aspect:
    new_w = int(ah * target_aspect)
    art = art.crop(((aw - new_w) // 2, 0, (aw + new_w) // 2, ah))
else:
    new_h = int(aw / target_aspect)
    art = art.crop((0, (ah - new_h) // 2, aw, (ah + new_h) // 2))
art = art.resize((W, ART_H), Image.LANCZOS)
# Slight saturation/contrast bump
from PIL import ImageEnhance
art = ImageEnhance.Color(art).enhance(1.2)
art = ImageEnhance.Contrast(art).enhance(1.08)
art = ImageEnhance.Brightness(art).enhance(1.05)
card.paste(art, (0, ART_TOP))

# Lens flare burst on art (centered)
art_layer = Image.new("RGBA", (W, ART_H), (0, 0, 0, 0))
ad = ImageDraw.Draw(art_layer)
flare_cx, flare_cy = W // 2, ART_H // 2
for r in range(int(70 * S), 0, -2):
    t = 1 - r / (70 * S)
    a = int(140 * t ** 2)
    ad.ellipse([flare_cx - r, flare_cy - r, flare_cx + r, flare_cy + r],
               fill=(255, 248, 224, a))
art_layer = art_layer.filter(ImageFilter.GaussianBlur(4))

# Lens flare 4-axis rays
ray_layer = Image.new("RGBA", (W, ART_H), (0, 0, 0, 0))
rd = ImageDraw.Draw(ray_layer)
ray_len = int(110 * S)
for angle in (0, 45, 90, 135):
    rad = math.radians(angle)
    dx, dy = math.cos(rad), math.sin(rad)
    for off in range(-ray_len, ray_len, 1):
        t = 1 - abs(off) / ray_len
        a = int(160 * t)
        x = int(flare_cx + dx * off)
        y = int(flare_cy + dy * off)
        if 0 <= x < W and 0 <= y < ART_H:
            for w in range(-2, 3):
                wx = int(x - dy * w)
                wy = int(y + dx * w)
                if 0 <= wx < W and 0 <= wy < ART_H:
                    fade = 1 - abs(w) / 3
                    rd.point((wx, wy), fill=(255, 248, 224, int(a * fade * 0.7)))
ray_layer = ray_layer.filter(ImageFilter.GaussianBlur(2))

# Sparkles (9 of them, varying sizes)
sparkles = [
    (0.20, 0.15, 10), (0.75, 0.30, 8), (0.15, 0.45, 6),
    (0.60, 0.55, 5), (0.30, 0.70, 7), (0.78, 0.80, 5),
    (0.50, 0.25, 4), (0.88, 0.65, 3), (0.40, 0.40, 4),
]
sp_layer = Image.new("RGBA", (W, ART_H), (0, 0, 0, 0))
sd = ImageDraw.Draw(sp_layer)
def draw_sparkle(d, cx, cy, size):
    sz = size * S
    # 4-point star
    d.polygon([
        (cx, cy - sz), (cx + sz * 0.25, cy - sz * 0.25),
        (cx + sz, cy), (cx + sz * 0.25, cy + sz * 0.25),
        (cx, cy + sz), (cx - sz * 0.25, cy + sz * 0.25),
        (cx - sz, cy), (cx - sz * 0.25, cy - sz * 0.25),
    ], fill=(255, 255, 255, 255))
for fx, fy, sz in sparkles:
    cx2 = int(W * fx); cy2 = int(ART_H * fy)
    draw_sparkle(sd, cx2, cy2, sz)
# Glow each sparkle
glow = sp_layer.filter(ImageFilter.GaussianBlur(6))
glow_color = Image.new("RGBA", (W, ART_H), (*GOLD, 0))
glow_color.putalpha(glow.getchannel("A"))
gd2 = glow_color.load()
for yy in range(ART_H):
    for xx in range(W):
        r, g, b, a = gd2[xx, yy]
        if a > 0:
            gd2[xx, yy] = (251, 191, 36, min(255, a * 2))
glow_color = glow_color.filter(ImageFilter.GaussianBlur(3))

# Composite layers onto card art region
art_region = card.crop((0, ART_TOP, W, ART_TOP + ART_H)).convert("RGBA")
art_region = Image.alpha_composite(art_region, art_layer)
art_region = Image.alpha_composite(art_region, ray_layer)
art_region = Image.alpha_composite(art_region, glow_color)
art_region = Image.alpha_composite(art_region, sp_layer)

# Chromatic sheen sweep (diagonal)
sheen = Image.new("RGBA", (W, ART_H), (0, 0, 0, 0))
sd2 = ImageDraw.Draw(sheen)
for x in range(W):
    t = (x / W - 0.5)
    a = max(0, int(60 * (1 - abs(t) * 6)))
    if a > 0:
        sd2.line([(x, 0), (x + ART_H // 3, ART_H)], fill=(255, 248, 224, a), width=1)
sheen = sheen.filter(ImageFilter.GaussianBlur(3))
art_region = Image.alpha_composite(art_region, sheen)

# Vignette bottom of art
vig = Image.new("RGBA", (W, ART_H), (0, 0, 0, 0))
vd = ImageDraw.Draw(vig)
for y in range(ART_H):
    t = max(0, (y - ART_H * 0.7) / (ART_H * 0.3))
    a = int(180 * t ** 2)
    vd.line([(0, y), (W, y)], fill=(15, 10, 4, a))
art_region = Image.alpha_composite(art_region, vig)

card.paste(art_region.convert("RGB"), (0, ART_TOP))

# Border lines on art
draw = ImageDraw.Draw(card)
draw.line([(0, ART_TOP - 1), (W, ART_TOP - 1)], fill=GOLD, width=2 * S)
draw.line([(0, ART_TOP + ART_H), (W, ART_TOP + ART_H)], fill=GOLD, width=2 * S)

# ── Top header bar ───────────────────────────────────────────────────────
hdr_bg = Image.new("RGB", (W, 32 * S), (15, 10, 4))
hdrpx = hdr_bg.load()
for x in range(W):
    t = abs(x - W / 2) / (W / 2)
    r = int(58 - 43 * t); g = int(42 - 32 * t); b = int(8 - 4 * t)
    for y in range(32 * S):
        hdrpx[x, y] = (r, g, b)
card.paste(hdr_bg, (0, 0))

# Title — gold gradient text. Render text in gold, then overlay shimmer.
title_font = font(11)
title = "PLASMA LANCE"
draw = ImageDraw.Draw(card)
# Gold base
draw.text((14 * S, 8 * S), title, font=title_font, fill=GOLD_LIGHT)
# Shadow halo around title
title_shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
tsd = ImageDraw.Draw(title_shadow)
tsd.text((14 * S, 8 * S), title, font=title_font, fill=(*GOLD, 200))
title_shadow = title_shadow.filter(ImageFilter.GaussianBlur(6))
card = Image.alpha_composite(card.convert("RGBA"), title_shadow).convert("RGB")
draw = ImageDraw.Draw(card)
draw.text((14 * S, 8 * S), title, font=title_font, fill=WHITE)

# Energy chip ⚡3 on right
chip_w, chip_h = 32 * S, 18 * S
chip_x = W - chip_w - 12 * S
chip_y = 7 * S
draw.rectangle([chip_x, chip_y, chip_x + chip_w, chip_y + chip_h],
               fill=(58, 42, 8), outline=GOLD, width=S)
chip_font = font(10)
draw.text((chip_x + 5 * S, chip_y + 2 * S), "[3", font=chip_font, fill=GOLD)
# Replace [ with bolt — easier to draw a small bolt shape
draw.rectangle([chip_x + 4 * S, chip_y + 3 * S, chip_x + 11 * S, chip_y + 15 * S], fill=(58, 42, 8))
# Tiny lightning glyph
bolt = [
    (chip_x + 9 * S, chip_y + 3 * S), (chip_x + 5 * S, chip_y + 9 * S),
    (chip_x + 8 * S, chip_y + 9 * S), (chip_x + 6 * S, chip_y + 15 * S),
    (chip_x + 10 * S, chip_y + 8 * S), (chip_x + 7 * S, chip_y + 8 * S),
]
draw.polygon(bolt, fill=GOLD)
draw.text((chip_x + 14 * S, chip_y + 2 * S), "3", font=chip_font, fill=GOLD)

# Top header bottom border (gold)
draw.line([(0, 32 * S - 1), (W, 32 * S - 1)], fill=GOLD, width=2 * S)

# Rarity tag overlay on top-left of art
rt_x, rt_y = 7 * S, ART_TOP + 5 * S
rt_text = "* ENERGY > LEGENDARY *"
rt_font = font(8)
bbox = draw.textbbox((rt_x, rt_y), rt_text, font=rt_font)
draw.rectangle([bbox[0] - 4 * S, bbox[1] - 2 * S, bbox[2] + 4 * S, bbox[3] + 2 * S],
               fill=(0, 0, 0, 220), outline=GOLD)
draw.text((rt_x, rt_y), rt_text, font=rt_font, fill=GOLD)

# ── Stats panel ──────────────────────────────────────────────────────────
STATS_TOP = ART_TOP + ART_H + 2 * S
STATS_H = 38 * S
stats_bg = Image.new("RGB", (W, STATS_H), (26, 20, 8))
spx = stats_bg.load()
for x in range(W):
    t = abs(x - W / 2) / (W / 2)
    r = int(58 - 32 * t); g = int(42 - 22 * t); b = int(8 - 0 * t)
    for y in range(STATS_H):
        spx[x, y] = (r, g, b)
card.paste(stats_bg, (0, STATS_TOP))
draw = ImageDraw.Draw(card)
draw.line([(0, STATS_TOP + STATS_H), (W, STATS_TOP + STATS_H)], fill=GOLD, width=2 * S)

stat_label_font = font(8)
stat_val_font = font(15)
slot_font = font(10)
stats = [("ATK", "5", CYAN), ("HP", "3", WHITE), ("PRC", "1", GOLD), ("SLOT", "L*ARM", WHITE)]
col_w = W // 4
for i, (lbl, val, col) in enumerate(stats):
    cx = i * col_w + col_w // 2
    bbox = draw.textbbox((0, 0), lbl, font=stat_label_font)
    lw = bbox[2] - bbox[0]
    draw.text((cx - lw // 2, STATS_TOP + 4 * S), lbl, font=stat_label_font, fill=GOLD)
    fnt = stat_val_font if lbl != "SLOT" else slot_font
    bbox = draw.textbbox((0, 0), val, font=fnt)
    vw = bbox[2] - bbox[0]
    # Glow
    if col != WHITE:
        glow_img = Image.new("RGBA", (W, STATS_H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_img)
        gd.text((cx - vw // 2, 14 * S), val, font=fnt, fill=(*col, 220))
        glow_img = glow_img.filter(ImageFilter.GaussianBlur(5))
        card_rgba = card.convert("RGBA")
        card_rgba.paste(glow_img, (0, STATS_TOP), glow_img)
        card = card_rgba.convert("RGB")
        draw = ImageDraw.Draw(card)
    draw.text((cx - vw // 2, STATS_TOP + 14 * S), val, font=fnt, fill=col)

# ── Rules text ───────────────────────────────────────────────────────────
RULES_TOP = STATS_TOP + STATS_H + 5 * S
rules_font = font(9)
flavor_font = serif_italic(9)
draw.text((10 * S, RULES_TOP), "> PIERCE 1", font=rules_font, fill=CYAN)
draw.text((10 * S, RULES_TOP + 12 * S), "Bypasses 1 enemy defense per attack.",
          font=font(8, bold=False), fill=(255, 248, 224))

# ── Flavor text ──────────────────────────────────────────────────────────
FLAVOR_TOP = RULES_TOP + 28 * S
draw.line([(10 * S, FLAVOR_TOP - 3 * S), (W - 10 * S, FLAVOR_TOP - 3 * S)],
          fill=(*GOLD, 100), width=1)
draw.text((10 * S, FLAVOR_TOP),
          '"Forged from a fallen reactor\'s',
          font=flavor_font, fill=GOLD)
draw.text((10 * S, FLAVOR_TOP + 12 * S),
          'core. The lance remembers',
          font=flavor_font, fill=GOLD)
draw.text((10 * S, FLAVOR_TOP + 24 * S),
          'everything it pierced."',
          font=flavor_font, fill=GOLD)

# ── Outer gold border ────────────────────────────────────────────────────
draw.rectangle([0, 0, W - 1, H - 1], outline=GOLD, width=3 * S)
draw.rectangle([2 * S, 2 * S, W - 1 - 2 * S, H - 1 - 2 * S], outline=GOLD_DARK, width=S)

# ── Filigree corner brackets w/ gem inlays ───────────────────────────────
corner_size = 18 * S
gem_size = 8 * S
def corner(x, y, dx, dy):
    # L-bracket
    draw.line([(x, y), (x + corner_size * dx, y)], fill=GOLD, width=2 * S)
    draw.line([(x, y), (x, y + corner_size * dy)], fill=GOLD, width=2 * S)
    # gem (diamond)
    gx, gy = x + 7 * S * dx, y + 7 * S * dy
    half = gem_size // 2
    draw.polygon([
        (gx, gy - half), (gx + half, gy), (gx, gy + half), (gx - half, gy)
    ], fill=GOLD_LIGHT, outline=GOLD)
    # Gem highlight
    draw.polygon([
        (gx, gy - half + 2 * S), (gx + half - 2 * S, gy),
        (gx, gy - 1), (gx - half + 2 * S, gy)
    ], fill=WHITE)

corner(3 * S, 3 * S, 1, 1)
corner(W - 3 * S - 1, 3 * S, -1, 1)
corner(3 * S, H - 3 * S - 1, 1, -1)
corner(W - 3 * S - 1, H - 3 * S - 1, -1, -1)

# Corner gem glow
glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gld = ImageDraw.Draw(glow_layer)
for cx, cy in [(10 * S, 10 * S), (W - 10 * S, 10 * S),
               (10 * S, H - 10 * S), (W - 10 * S, H - 10 * S)]:
    for r in range(15 * S, 0, -1):
        a = int(80 * (1 - r / (15 * S)) ** 2)
        gld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*GOLD, a))
glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(5))
card = Image.alpha_composite(card.convert("RGBA"), glow_layer).convert("RGB")

# ── Downsample to display size ───────────────────────────────────────────
final = card.resize((280, 392), Image.LANCZOS)
final.save(OUT)
print(f"Saved: {OUT}")
