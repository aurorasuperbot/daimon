"""PIL-based BattleFrame → PNG renderer.

Terminal-mockup pattern (per doc convention): render the battle UI as it would
appear in a real terminal, using a monospace font and box-drawing characters
for the chrome (outer frame, log area, HP bar, prompt). Cards themselves are
NOT text boxes — they're full composed card images (same pipeline as the Plasma
Lance proof) pasted onto the canvas, so every surface in the UI shows real
cards with rarity tints, art, and frame decorations.

This renderer is the marketing/screenshot path. The real Textual TUI will look
the same (same box-drawing chars, same color mapping, same primitives) but runs
live in the player's terminal.

Keep this renderer simple: no animation timeline, no interactivity — it paints
ONE still frame. Animation is producing multiple frames at different t_ms and
compositing into a GIF/video later (or just as the Textual widget redraws).

Layout (80 cols × 27 rows, each cell CELL_W × CELL_H):
  row 0                top border + inline title (`┌──── DAIMON ... ────┐`)
  row 2                participant header labels (YOU / OPPONENT + rank)
  rows 3..9            player/opponent cards — first row (3 slots per side)
  rows 10..16          player/opponent cards — second row (3 slots per side)
  row 17               round separator (`──── ROUND N ────`)
  rows 18..22          fight log (5 lines max; thumbnails at column 2)
  row 23               total HP bar
  row 25               footer prompt
  row 26               bottom border (`└──────────────┘`)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from daimon.play.card_tile import (
    CardTileInfo,
    render_card_thumbnail,
    render_card_tile,
    render_card_tile_from_loadout,
    render_card_tile_from_state,
    tile_info_from_loadout,
)
from daimon.play.frame import BattleFrame, CardState, ConnectionLine
from daimon.play.schema import Match, Outcome, Side, TEAM_POSITIONS


# ---------------------------------------------------------------------------
# Dimensions + fonts
# ---------------------------------------------------------------------------

GRID_COLS = 80
GRID_ROWS = 27
CELL_W = 13
CELL_H = 26
PAD = 14

CANVAS_W = GRID_COLS * CELL_W + 2 * PAD
CANVAS_H = GRID_ROWS * CELL_H + 2 * PAD

# Terminal-native palette (dark-theme baseline; actual terminal-native
# ANSI colors would defer to the user's terminal — here we emulate)
BG = (14, 18, 26)                   # deep blue-black
FG = (220, 225, 235)                # off-white default text
FG_DIM = (130, 138, 152)            # labels, dim chrome
FG_MUTED = (95, 102, 115)
BORDER = (120, 130, 145)            # default card borders (rounded)
TITLE_GOLD = (230, 198, 90)         # header title

# Effect colors
EFFECT_COLOR = {
    "red":    (235, 90, 80),
    "green":  (90, 210, 130),
    "blue":   (90, 160, 235),
    "purple": (180, 130, 220),
    "yellow": (230, 198, 90),
    "gray":   (130, 138, 152),
    "cyan":   (120, 210, 210),
    "white":  (220, 225, 235),
}

HP_BAR_FULL = (90, 210, 130)        # green
HP_BAR_MID = (230, 198, 90)         # yellow
HP_BAR_LOW = (235, 90, 80)          # red
HP_BAR_EMPTY = (60, 68, 82)         # track


# Font fallback chain
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]
_FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
]


def _load_font(paths: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


FONT_SIZE = 18
FONT = _load_font(_FONT_CANDIDATES, FONT_SIZE)
FONT_BOLD = _load_font(_FONT_BOLD_CANDIDATES, FONT_SIZE)


# ---------------------------------------------------------------------------
# Grid helpers
# ---------------------------------------------------------------------------

def _xy(col: int, row: int) -> tuple[int, int]:
    """Convert grid (col, row) to canvas pixel (x, y)."""
    return PAD + col * CELL_W, PAD + row * CELL_H


def _draw_text(draw: ImageDraw.ImageDraw, col: int, row: int, text: str,
                color: tuple = FG, bold: bool = False) -> None:
    x, y = _xy(col, row)
    draw.text((x, y), text, fill=color, font=FONT_BOLD if bold else FONT)


# ---------------------------------------------------------------------------
# Card-cell layout — tall tiles that maintain compose_card's 280:392 aspect
# ---------------------------------------------------------------------------

# Each side has 3 columns × 2 rows of cards.
# Card aspect target: 280:392 == 0.714 (compose_card's locked ratio).
# Cell width 10 cols × 13 px = 130 px → height = 130/0.714 ≈ 182 px → 7 rows × 26 px.
CARD_W_COLS = 10
CARD_H_ROWS = 7

# Spacing between adjacent cards (in grid cells). Gives cards room to breathe
# and makes the flash-border overlays visible without clipping their neighbor.
CARD_COL_GAP = 1
CARD_ROW_GAP = 1

CARD_COL_STRIDE = CARD_W_COLS + CARD_COL_GAP     # 11
CARD_ROW_STRIDE = CARD_H_ROWS + CARD_ROW_GAP     # 8

CARD_PIXEL_W = CARD_W_COLS * CELL_W              # 130
CARD_PIXEL_H = CARD_H_ROWS * CELL_H              # 182

# Horizontal layout (80 cols total):
#   [margin 2] [3×stride - gap = 32] [center gap 12] [3×stride - gap = 32] [margin 2]
# Each side is 32 wide (3 cards of 10 + 2 intra-side gaps of 1 each).
PLAYER_COL_START = 2
SIDE_WIDTH_COLS = 3 * CARD_COL_STRIDE - CARD_COL_GAP   # 32
CENTER_GAP = 12
OPPONENT_COL_START = PLAYER_COL_START + SIDE_WIDTH_COLS + CENTER_GAP   # 2 + 32 + 12 = 46

# Vertical layout (anchored to top border at row 0)
#   row 0     — top border + inline title
#   row 1     — blank (breathing room)
#   row 2     — YOU / OPPONENT headers
#   rows 3..9   — card row 1 (7 rows)
#   row 10    — blank gap between card rows
#   rows 11..17 — card row 2 (7 rows)
#   row 18    — round separator
#   rows 19..22 — action log (4 rows; fits 2 log entries at 2 rows each)
#   row 23    — blank
#   row 24    — total HP bar
#   row 25    — prompt / keybindings
#   row 26    — bottom border
PLAYER_ROW_1 = 3
PLAYER_ROW_2 = PLAYER_ROW_1 + CARD_ROW_STRIDE    # 11

LOG_ROW_DIV = PLAYER_ROW_2 + CARD_H_ROWS         # 18  — round separator
LOG_ROW = LOG_ROW_DIV + 1                        # 19
LOG_HEIGHT = 4                                   # rows 19..22

HP_BAR_ROW = LOG_ROW + LOG_HEIGHT + 1            # 24  — one row of breathing room
PROMPT_ROW = HP_BAR_ROW + 1                      # 25


# V2 grid layout: 2 rows × 3 columns of team positions 0..5
#   row 0: positions 0, 1, 2
#   row 1: positions 3, 4, 5
POSITION_ROW_ORDER = [
    (0, 1, 2),
    (3, 4, 5),
]


def _position_grid_pos(position: int, side_col_start: int) -> tuple[int, int]:
    """Return (col, row) for the top-left of this team-position's card cell."""
    for row_idx, row in enumerate(POSITION_ROW_ORDER):
        for col_idx, p in enumerate(row):
            if p == position:
                col = side_col_start + col_idx * CARD_COL_STRIDE
                row_abs = PLAYER_ROW_1 + row_idx * CARD_ROW_STRIDE
                return col, row_abs
    raise ValueError(f"position {position} not in layout")


# ---------------------------------------------------------------------------
# Outer frame + inline title (fixes the "title crossed by top border" bug)
# ---------------------------------------------------------------------------

def _draw_outer_frame(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    title: str,
) -> None:
    """Paint the outer frame with the title embedded INSIDE the top border.

    Prevents the bug where the horizontal border line ran through the middle
    of the title characters: now the top border is broken into three segments
    (left dashes, title, right dashes) so the title replaces a chunk of the
    border rather than overwriting it.

    Additionally, we rebuild the bottom/side borders with the correct grid
    range so the right edge always closes exactly at column GRID_COLS - 1.
    """
    # Bottom border — full-width dashes between two corners
    bottom = "└" + "─" * (GRID_COLS - 2) + "┘"
    _draw_text(draw, 0, GRID_ROWS - 1, bottom, color=FG_DIM)

    # Side borders — one char per row (skip row 0 and the last row)
    for r in range(1, GRID_ROWS - 1):
        _draw_text(draw, 0, r, "│", color=FG_DIM)
        _draw_text(draw, GRID_COLS - 1, r, "│", color=FG_DIM)

    # Top border with inline title --------------------------------------
    # total inner width (between corners) = GRID_COLS - 2
    # title segment = " <title> " (padding spaces so border dashes don't touch text)
    inner = GRID_COLS - 2
    seg = f" {title} "
    if len(seg) > inner - 4:   # always leave at least 2 dashes on each side
        seg = seg[: inner - 4]
    pad = inner - len(seg)
    left = pad // 2
    right = pad - left

    # Corners + dashes (dim chrome)
    _draw_text(draw, 0, 0, "┌", color=FG_DIM)
    _draw_text(draw, 1, 0, "─" * left, color=FG_DIM)
    _draw_text(draw, 1 + left + len(seg), 0, "─" * right, color=FG_DIM)
    _draw_text(draw, GRID_COLS - 1, 0, "┐", color=FG_DIM)

    # Title (gold, bold) — painted AFTER the dashes, in the reserved gap
    _draw_text(draw, 1 + left, 0, seg, color=TITLE_GOLD, bold=True)


# ---------------------------------------------------------------------------
# Card tile placement — paste composed card images onto the canvas
# ---------------------------------------------------------------------------

def _paste_card_from_state(
    canvas: Image.Image,
    col: int,
    row: int,
    card: CardState,
    placeholder_art: Optional[Path],
) -> None:
    tile = render_card_tile_from_state(
        card, CARD_PIXEL_W, CARD_PIXEL_H, placeholder_art=placeholder_art,
    )
    x, y = _xy(col, row)
    canvas.paste(tile, (x, y), tile)


def _paste_card_from_loadout(
    canvas: Image.Image,
    col: int,
    row: int,
    card,
    placeholder_art: Optional[Path],
) -> None:
    tile = render_card_tile_from_loadout(
        card, CARD_PIXEL_W, CARD_PIXEL_H, placeholder_art=placeholder_art,
    )
    x, y = _xy(col, row)
    canvas.paste(tile, (x, y), tile)


# ---------------------------------------------------------------------------
# Connection line — box-drawing chars in the gap between player + opponent
# ---------------------------------------------------------------------------

def _card_anchor(col: int, row: int, side: str) -> tuple[int, int]:
    """Anchor point for a connection line (mid-height of the tile).

    side = 'left' or 'right' — which side of the card we attach to.
    """
    mid_row = row + CARD_H_ROWS // 2
    if side == "left":
        return col, mid_row
    return col + CARD_W_COLS - 1, mid_row


def _draw_connection_line(
    draw: ImageDraw.ImageDraw,
    conn: ConnectionLine,
    player_card_pos: dict,
    opponent_card_pos: dict,
) -> None:
    if conn.actor_side == Side.PLAYER:
        actor_col, actor_row = player_card_pos[conn.actor_position]
    else:
        actor_col, actor_row = opponent_card_pos[conn.actor_position]
    if conn.target_side == Side.PLAYER:
        target_col, target_row = player_card_pos[conn.target_position]
    else:
        target_col, target_row = opponent_card_pos[conn.target_position]

    if actor_col < target_col:
        a_anchor = _card_anchor(actor_col, actor_row, "right")
        t_anchor = _card_anchor(target_col, target_row, "left")
        arrow = "◀"
    else:
        a_anchor = _card_anchor(actor_col, actor_row, "left")
        t_anchor = _card_anchor(target_col, target_row, "right")
        arrow = "▶"

    color = EFFECT_COLOR.get(conn.color, EFFECT_COLOR["red"])

    a_col, a_row = a_anchor
    t_col, t_row = t_anchor

    if a_row == t_row:
        col_lo, col_hi = min(a_col, t_col) + 1, max(a_col, t_col) - 1
        for c in range(col_lo, col_hi + 1):
            _draw_text(draw, c, a_row, "━", color=color, bold=True)
        if a_col < t_col:
            _draw_text(draw, col_hi, a_row, "◀", color=color, bold=True)
        else:
            _draw_text(draw, col_lo, a_row, "▶", color=color, bold=True)
    else:
        col_lo, col_hi = min(a_col, t_col) + 1, max(a_col, t_col) - 1
        for c in range(col_lo, col_hi + 1):
            _draw_text(draw, c, a_row, "━", color=color, bold=True)
        for r in range(min(a_row, t_row) + 1, max(a_row, t_row)):
            _draw_text(draw, t_col, r, "┃", color=color, bold=True)
        if a_col < t_col:
            _draw_text(draw, t_col, a_row, "┓" if t_row > a_row else "┛", color=color, bold=True)
        _draw_text(draw, t_col, t_row, arrow, color=color, bold=True)


# ---------------------------------------------------------------------------
# Fight log with card thumbnails
# ---------------------------------------------------------------------------

THUMB_HEIGHT_PX = CELL_H * 2 - 4    # ~48 px; spans ~2 log rows so art is visible


def _paste_thumbnail_for_line(
    canvas: Image.Image,
    col: int,
    row: int,
    actor_card: Optional[CardState],
    placeholder_art: Optional[Path],
) -> int:
    """Render a tiny card thumbnail for the actor and paste at (col, row).

    Returns the number of columns the thumbnail consumes, so the log line can
    offset its text to the right of the image.
    """
    if actor_card is None:
        return 0
    info = CardTileInfo(
        name=actor_card.name or actor_card.short_name,
        short_name=actor_card.short_name,
        rarity=actor_card.rarity,
        position=actor_card.position,
        species=actor_card.species,
        element=actor_card.element if hasattr(actor_card, "element") else None,
        hp=actor_card.hp,
        hp_max=actor_card.hp_max,
    )
    thumb = render_card_thumbnail(
        info.name, info.rarity, info.position, THUMB_HEIGHT_PX,
        element=info.element, species=info.species,
        placeholder_art=placeholder_art,
    )
    x, y = _xy(col, row)
    # Vertically align thumbnail's top with the text baseline row; the
    # caller's 2-row block has room for a 48px-tall image starting at row top.
    canvas.paste(thumb, (x, y - 2), thumb)
    # columns consumed ≈ ceil(thumb.width / CELL_W) + 1 (spacer)
    cols = (thumb.width + CELL_W - 1) // CELL_W + 1
    return cols


def _actor_card_for_log_line(
    match: Match, line: str, *, player_cards: dict, opponent_cards: dict,
) -> Optional[CardState]:
    """Best-effort: find the CardState whose name appears at the start of `line`.

    Log lines conventionally look like `Plasma Lance hits Core for 3`, so we
    match on any card's name or short_name being a prefix. Returns None if
    nothing matches (the thumbnail column gets skipped for that line).
    """
    if not line:
        return None
    candidates = list(player_cards.values()) + list(opponent_cards.values())
    # Longest name first (so "Plasma Lance" wins over a hypothetical "Plasma")
    candidates.sort(key=lambda c: -len(c.name or ""))
    lo = line.lower()
    for c in candidates:
        for tag in (c.name, c.short_name):
            if tag and lo.startswith(tag.lower()):
                return c
    return None


# ---------------------------------------------------------------------------
# Main renderer entry points
# ---------------------------------------------------------------------------

def render_frame_to_png(
    frame: BattleFrame,
    out_path: str | Path,
    title_override: Optional[str] = None,
    *,
    placeholder_art: Optional[Path] = None,
    match: Optional[Match] = None,
) -> Path:
    """Paint a BattleFrame to PNG at out_path. Returns the written path.

    `placeholder_art` is the stand-in art used for every card whose own
    `art_path` is unset. Leaving it None falls back to the compose gradient.
    `match` is optional; when provided we use it to resolve the actor card for
    log-line thumbnails (purely a visual nicety).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(img)

    # ----- Outer frame with inline title
    title_text = title_override or (
        f"DAIMON — Match #{frame.match_id[:4]}  vs  {frame.opponent_name}"
    )
    _draw_outer_frame(img, draw, title_text)

    # ----- Participant headers (row 2)
    _draw_text(draw, PLAYER_COL_START, 2, "YOU", color=FG, bold=True)
    _draw_text(draw, PLAYER_COL_START + 4, 2, f"·  {frame.player_rank}", color=FG_DIM)
    _draw_text(draw, OPPONENT_COL_START, 2, "OPPONENT", color=FG, bold=True)
    _draw_text(draw, OPPONENT_COL_START + 9, 2, f"·  {frame.opponent_rank}", color=FG_DIM)

    # ----- Card grid — paste composed tiles
    player_pos: dict[int, tuple[int, int]] = {}
    opponent_pos: dict[int, tuple[int, int]] = {}

    for position in range(TEAM_POSITIONS):
        col_p, row_p = _position_grid_pos(position, PLAYER_COL_START)
        player_pos[position] = (col_p, row_p)
        _paste_card_from_state(img, col_p, row_p, frame.player_cards[position], placeholder_art)

        col_o, row_o = _position_grid_pos(position, OPPONENT_COL_START)
        opponent_pos[position] = (col_o, row_o)
        _paste_card_from_state(img, col_o, row_o, frame.opponent_cards[position], placeholder_art)

    # Need a fresh Draw handle after paste operations (PIL docs: draw state is
    # tied to the image; pastes modify pixels but the existing Draw is still
    # valid. Still, re-creating is cheap insurance.)
    draw = ImageDraw.Draw(img)

    # ----- Connection line
    if frame.connection_line is not None:
        _draw_connection_line(draw, frame.connection_line, player_pos, opponent_pos)

    # ----- Round separator
    sep = "─" * 24 + f" ROUND {frame.round_number} " + "─" * 24
    sep = sep[: GRID_COLS - 4]
    _draw_text(draw, 2, LOG_ROW_DIV, sep, color=FG_MUTED)

    # ----- Action log with thumbnails
    # Each log entry takes 2 rows — row 0 is the line, row 1 is blank padding.
    # Thumbnail (48 px tall) spans both rows on the left, text is vertically
    # centered in the 2-row block by drawing on row 0.
    ENTRY_ROWS = 2
    max_entries = LOG_HEIGHT // ENTRY_ROWS           # 2 entries fit in 5 rows
    visible = frame.log_lines[-max_entries:] if len(frame.log_lines) > max_entries else frame.log_lines

    for i, line in enumerate(visible):
        log_row = LOG_ROW + i * ENTRY_ROWS
        actor = _actor_card_for_log_line(
            match, line, player_cards=frame.player_cards, opponent_cards=frame.opponent_cards,
        ) if match is not None else None
        thumb_cols = _paste_thumbnail_for_line(img, 2, log_row, actor, placeholder_art)
        draw = ImageDraw.Draw(img)
        text_col = 2 + thumb_cols
        # Text sits on row log_row (top of the 2-row block); row log_row+1 is spacing.
        _draw_text(draw, text_col, log_row, f"▶ {line}"[: GRID_COLS - 4 - thumb_cols], color=FG)

    if frame.log_line_typing is not None:
        i = len(visible)
        if i * ENTRY_ROWS < LOG_HEIGHT:
            log_row = LOG_ROW + i * ENTRY_ROWS
            _draw_text(draw, 2, log_row, f"▶ {frame.log_line_typing}_"[: GRID_COLS - 4], color=FG)

    # ----- Total HP bar
    hp_row = HP_BAR_ROW
    label_l = f"YOU {frame.player_total_hp} HP"
    label_r = f"OPPONENT {frame.opponent_total_hp} HP"

    total_hp_sum = frame.player_total_hp + frame.opponent_total_hp
    p_frac = 0.5 if total_hp_sum == 0 else frame.player_total_hp / total_hp_sum

    bar_chars = GRID_COLS - 4 - len(label_l) - len(label_r) - 2
    p_fill = int(bar_chars * p_frac)
    o_fill = bar_chars - p_fill

    _draw_text(draw, 2, hp_row, label_l, color=HP_BAR_FULL, bold=True)
    _draw_text(draw, 2 + len(label_l) + 1, hp_row, "═" * p_fill, color=HP_BAR_FULL)
    _draw_text(draw, 2 + len(label_l) + 1 + p_fill, hp_row, "═" * o_fill, color=HP_BAR_LOW)
    _draw_text(draw, 2 + len(label_l) + 1 + p_fill + o_fill + 1, hp_row, label_r,
               color=HP_BAR_LOW, bold=True)

    # ----- Footer prompt
    prompt = "space pause  •  → skip  •  1/2/3 speed  •  s sfx  •  q quit  •  ? help"
    prompt_col = max(2, (GRID_COLS - len(prompt)) // 2)
    _draw_text(draw, prompt_col, PROMPT_ROW, prompt, color=FG_MUTED)

    img.save(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Outer-phase renderers (P2 lineup, P3 round banner, P4 outcome)
# ---------------------------------------------------------------------------

def render_lineup_to_png(
    match: Match,
    out_path: str | Path,
    *,
    placeholder_art: Optional[Path] = None,
) -> Path:
    """Phase P2: pre-match lineup fan-in (3000ms)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(img)

    _draw_outer_frame(img, draw,
                      f"DAIMON — Match #{match.match_id[:4]}  ·  Lineup")

    player = match.participants["player"]
    opponent = match.participants["opponent"]

    # Row 2: name + rank for both sides, "VS" centered
    name_player = player.name.upper()
    name_opponent = opponent.name.upper()
    player_label = f"{name_player}  ·  {player.rank}"
    opponent_label = f"{opponent.rank}  ·  {name_opponent}"
    _draw_text(draw, PLAYER_COL_START, 2, player_label, color=FG, bold=True)
    _draw_text(draw, GRID_COLS - 2 - len(opponent_label), 2, opponent_label,
               color=FG, bold=True)

    vs_text = "VS"
    vs_col = (GRID_COLS - len(vs_text)) // 2
    _draw_text(draw, vs_col, 2, vs_text, color=TITLE_GOLD, bold=True)

    # Paste cards for both sides
    for card in player.loadout:
        col, row = _position_grid_pos(card.position, PLAYER_COL_START)
        _paste_card_from_loadout(img, col, row, card, placeholder_art)
    for card in opponent.loadout:
        col, row = _position_grid_pos(card.position, OPPONENT_COL_START)
        _paste_card_from_loadout(img, col, row, card, placeholder_art)

    draw = ImageDraw.Draw(img)

    # Center tagline under grid
    tagline = "— 6 cards · loadout vs loadout · deterministic combat —"
    tagline_col = max(2, (GRID_COLS - len(tagline)) // 2)
    _draw_text(draw, tagline_col, LOG_ROW + 1, tagline, color=FG_MUTED)

    wait = "press any key to begin the match"
    wait_col = max(2, (GRID_COLS - len(wait)) // 2)
    _draw_text(draw, wait_col, PROMPT_ROW, wait, color=FG_MUTED)

    img.save(out_path)
    return out_path


def render_round_banner_to_png(
    match: Match,
    round_number: int,
    out_path: str | Path,
    *,
    placeholder_art: Optional[Path] = None,
) -> Path:
    """Phase P3 (inter-round banner, 400ms)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(img)

    _draw_outer_frame(
        img, draw,
        f"DAIMON — Match #{match.match_id[:4]} vs {match.participants['opponent'].name}",
    )

    banner_text = f"ROUND  {round_number}"
    banner_row = GRID_ROWS // 2 - 1
    box_w = len(banner_text) + 8
    box_col = (GRID_COLS - box_w) // 2

    top_box = "╔" + "═" * (box_w - 2) + "╗"
    mid_box = "║" + " " * (box_w - 2) + "║"
    bot_box = "╚" + "═" * (box_w - 2) + "╝"

    _draw_text(draw, box_col, banner_row - 1, top_box, color=TITLE_GOLD, bold=True)
    _draw_text(draw, box_col, banner_row, mid_box, color=TITLE_GOLD, bold=True)
    _draw_text(draw, box_col + 4, banner_row, banner_text, color=TITLE_GOLD, bold=True)
    _draw_text(draw, box_col, banner_row + 1, bot_box, color=TITLE_GOLD, bold=True)

    if round_number == 1:
        subtitle = "fight begins"
    else:
        subtitle = f"round {round_number} of up to 5"
    sub_col = (GRID_COLS - len(subtitle)) // 2
    _draw_text(draw, sub_col, banner_row + 3, subtitle, color=FG_DIM)

    img.save(out_path)
    return out_path


def render_outcome_to_png(
    match: Match,
    out_path: str | Path,
    *,
    placeholder_art: Optional[Path] = None,
) -> Path:
    """Phase P4: outcome screen (4000ms)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(img)

    outcome: Outcome = match.outcome
    winner_is_player = outcome.winner == Side.PLAYER
    is_draw = outcome.winner == Side.DRAW

    _draw_outer_frame(
        img, draw,
        f"DAIMON — Match #{match.match_id[:4]}  ·  complete",
    )

    # Big outcome banner
    if is_draw:
        banner_text = "DRAW"
        banner_color = HP_BAR_FULL  # neutral / informational
    elif winner_is_player:
        banner_text = "VICTORY"
        banner_color = HP_BAR_FULL
    else:
        banner_text = "DEFEAT"
        banner_color = HP_BAR_LOW

    banner_row = 3
    box_w = len(banner_text) + 10
    box_col = (GRID_COLS - box_w) // 2
    top_box = "╔" + "═" * (box_w - 2) + "╗"
    mid_box = "║" + " " * (box_w - 2) + "║"
    bot_box = "╚" + "═" * (box_w - 2) + "╝"

    _draw_text(draw, box_col, banner_row, top_box, color=banner_color, bold=True)
    _draw_text(draw, box_col, banner_row + 1, mid_box, color=banner_color, bold=True)
    _draw_text(draw, box_col + 5, banner_row + 1, banner_text, color=banner_color, bold=True)
    _draw_text(draw, box_col, banner_row + 2, bot_box, color=banner_color, bold=True)

    opp = match.participants["opponent"]
    you = match.participants["player"]
    who = f"{you.name}  vs  {opp.name}  ({opp.rank})"
    _draw_text(draw, (GRID_COLS - len(who)) // 2, banner_row + 4, who, color=FG_DIM)

    # ----- Match stats (two columns)
    stats = outcome.stats
    col_left = 6
    col_right = GRID_COLS // 2 + 4
    stats_row = 9

    _draw_text(draw, col_left, stats_row, "MATCH STATS", color=TITLE_GOLD, bold=True)
    _draw_text(draw, col_left, stats_row + 1, "─" * 22, color=FG_DIM)

    lines_left = [
        ("Rounds played:", str(stats.round_count)),
        ("HP remaining:", f"{outcome.player_hp_remaining} / {outcome.opponent_hp_remaining}"),
        ("Your kills:", str(stats.cards_killed.get("player", 0))),
        ("Their kills:", str(stats.cards_killed.get("opponent", 0))),
    ]
    for i, (lbl, val) in enumerate(lines_left):
        _draw_text(draw, col_left, stats_row + 2 + i, lbl, color=FG_DIM)
        _draw_text(draw, col_left + 17, stats_row + 2 + i, val, color=FG, bold=True)

    _draw_text(draw, col_right, stats_row, "HIGHLIGHTS", color=TITLE_GOLD, bold=True)
    _draw_text(draw, col_right, stats_row + 1, "─" * 22, color=FG_DIM)

    highlights: list[tuple[str, str]] = []
    if stats.biggest_hit:
        highlights.append((
            "Biggest hit:",
            f"{stats.biggest_hit.get('by', '?')} · {stats.biggest_hit.get('amount', '?')}",
        ))
    if stats.longest_survivor:
        highlights.append((
            "Longest alive:",
            f"{stats.longest_survivor.get('card', '?')} · HP {stats.longest_survivor.get('hp_remaining', '?')}",
        ))
    if not highlights:
        highlights.append(("—", "—"))

    for i, (lbl, val) in enumerate(highlights):
        base_row = stats_row + 2 + i * 2
        _draw_text(draw, col_right, base_row, lbl, color=FG_DIM)
        _draw_text(draw, col_right, base_row + 1, f"  {val}", color=FG)

    # ----- Rewards panel
    rw = outcome.rewards
    rewards_row = HP_BAR_ROW - 3
    _draw_text(draw, col_left, rewards_row, "REWARDS", color=TITLE_GOLD, bold=True)
    _draw_text(draw, col_left, rewards_row + 1, "─" * 22, color=FG_DIM)

    reward_lines = [
        ("Currency:", f"+{rw.currency}  coins"),
        ("Rank:", rw.rank_delta),
    ]
    for i, (lbl, val) in enumerate(reward_lines):
        _draw_text(draw, col_left, rewards_row + 2 + i, lbl, color=FG_DIM)
        _draw_text(draw, col_left + 12, rewards_row + 2 + i,
                   val, color=HP_BAR_FULL if "+" in val else FG, bold=True)

    prompt = "press any key to return  •  r replay match  •  s share PNG"
    prompt_col = max(2, (GRID_COLS - len(prompt)) // 2)
    _draw_text(draw, prompt_col, PROMPT_ROW, prompt, color=FG_MUTED)

    img.save(out_path)
    return out_path
