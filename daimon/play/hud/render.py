"""Renderer for the spectator HUD — wide tile layout with KGP card art.

Pure render: takes a ``Frame`` (or no frame, for IDLE), returns ``(screen,
overlays)``. The owning loop diffs / KGP-paints — render itself does no I/O.

Layout (148 cols × ~36 rows; tile-based, mirrors ``shop_ui.render_frame``):

    ╔══════════════════════════════════════════════════════════════════════════════╗
    ║                  DAIMON — Champion Lyra (Champion)                        ...║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  OPPONENT                                                                 ...║
    ║  ┌────────────────────┐  ┌────────────────────┐  ... (6 tiles, art via KGP) ║
    ║  │░░░ART░░░░░░░░░░░░░░│  │░░░ART░░░░░░░░░░░░░░│                            ║
    ║  │  (KGP-painted PNG) │  │                    │                            ║
    ║  │                    │  │                    │                            ║
    ║  │                    │  │                    │                            ║
    ║  │                    │  │                    │                            ║
    ║  │                    │  │                    │                            ║
    ║  └────────────────────┘  └────────────────────┘                            ║
    ║   [Voltc]              [Bulw ]                                             ║
    ║   HP: ██████████ 11/14  HP: █████░░░░░  6/10                               ║
    ╠═══════════════▼═════════════════════════════════════════════════════════════╣
    ║  → Voltcat Apex hits Blade Foxling for 7  (player/2: 13→6)                 ║
    ║    └ trigger: Blade Foxling counters for 3  (opponent/0: 14→11)            ║
    ╠═══════════════▲═════════════════════════════════════════════════════════════╣
    ║  PLAYER (santiago)                                                         ║
    ║  ┌────────────────────┐  ... (6 tiles)                                      ║
    ...
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║ R1 act 3/9 · step 7/53 · 1.0× · ▶ playing  │  [space]pause [→/←]step [q]quit║
    ╚══════════════════════════════════════════════════════════════════════════════╝

Card art is painted via Kitty Graphics Protocol overlays — the renderer
returns blank cells where each tile's art region sits, plus a list of
:class:`ImageOverlay` records carrying absolute frame coords + the PNG
path. The owning HUD app calls :func:`paint_overlays_as_kgp` to stream
the bitmaps; the screenshot pipeline pastes the same PNGs onto a canvas
for design review. Both paths share the overlays — pixel parity by
construction (same pattern as ``shop_ui``).

Animation effects (color_flash, intent, glow, overlay_icon) compose into
the per-tile ASCII captions so they stay visible even when the terminal
can't paint KGP (e.g. ``--no-color`` regression-test mode). The card art
itself is painted by KGP on top of the blank tile interior.

Color: optional ANSI 256-color. Rendering is deterministic — same frame
in, same string + overlays out. The app loop renders on every tick at
most once.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from daimon.play.animator import AnimationSnapshot
from daimon.play.art_render import RenderMode
from daimon.play.hud.playback import (
    Frame,
    Phase,
    PlaybackStatus,
    SPEED_LADDER,
    Step,
)
from daimon.play.primitives import CardEmission
from daimon.play.schema import ActionKind, Match, Side
from daimon.play.screenshot import ImageOverlay
from daimon.play.tile import Tile, compose_row, overlays_for_row, render_tile
from daimon.play.tui_style import pad_visible, visible_len


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
#
# WezTerm window (locked by daimon/render/wezterm.lua) is 150 cols × 42 rows.
# The play HUD claims 148 cols (147 inner + 2 walls) so it sits comfortably
# within the locked window with room for the bottom mining strip + a spare
# row of breathing space.
#
# Per side: 6 cards × TILE_W=22 + 5 gaps × TILE_GAP=2 + 2× LEFT_PAD=2
#   = 132 + 10 + 4 = 146 inner cells. The remaining 1 cell of slack pads
# the trailing edge of each tile row.
#
# Tile shape: 1 (top border) + TILE_ART_H=7 (art) + 1 (bottom border)
#   + 2 (caption rows) = 11 rows. Two side-tile rows (opp + player) plus
# the chrome (title, dividers, log, status) totals ~36 rows.

WIDTH = 148
TILE_W = 22                   # 1 left border + 20 inner + 1 right border
TILE_ART_H = 7                # rows of art region inside the tile
TILE_GAP = 2                  # blank cells between tiles in a row
LEFT_PAD = 2                  # cells of left padding before the first tile

HP_BAR_LEN = 10               # always 10 pips so a full bar reads "██████████"

# ANSI helpers — kept tiny + escape-string-only so tests can assert on them.
RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
WHITE = "\x1b[37m"
GRAY = "\x1b[90m"

# Element → color. Mapped to readable ANSI 4-bit so it works on any terminal.
# NORMAL is outside the type ring — rendered as plain WHITE to read as
# "neutral / utility" against the colored elemental tints.
ELEMENT_COLOR = {
    "fire":   RED,
    "water":  BLUE,
    "nature": GREEN,
    "volt":   YELLOW,
    "void":   MAGENTA,
    "normal": WHITE,
}

# Symbolic primitive-emission color → ANSI escape. Primitives speak in
# semantic color names ("red", "blue", "purple") — render maps to ANSI here so
# changing the palette never touches primitive code.
EMISSION_COLOR = {
    "red":     RED,
    "green":   GREEN,
    "blue":    BLUE,
    "yellow":  YELLOW,
    "cyan":    CYAN,
    "magenta": MAGENTA,
    "purple":  MAGENTA,        # purple ≈ magenta in 4-bit ANSI
    "gray":    GRAY,
    "white":   WHITE,
    "gold":    YELLOW,         # gold ≈ yellow on terminals without 256-color
}

KIND_PREFIX = {
    ActionKind.DAMAGE:  "⚔",
    ActionKind.HEAL:    "✚",
    ActionKind.BUFF:    "↑",
    ActionKind.DEBUFF:  "↓",
    ActionKind.SHIELD:  "◇",
    ActionKind.STATUS:  "✦",
    ActionKind.DEATH:   "✕",
    ActionKind.PASSIVE: "·",
}


# ---------------------------------------------------------------------------
# Frame builder
# ---------------------------------------------------------------------------

def render_frame(
    frame: Frame,
    *,
    color: bool = True,
    width: int = WIDTH,
) -> Tuple[str, List[ImageOverlay]]:
    """Build the full terminal screen + KGP overlays for the playback frame.

    Returns ``(screen_string, overlays)``:
      * ``screen_string`` is the ASCII frame with ANSI escapes (or no
        escapes when ``color=False``). Tile art regions are painted as
        blank cells; the captions below carry name/HP/animation state
        so the layout reads even without KGP support.
      * ``overlays`` is a list of :class:`ImageOverlay` records in
        absolute frame coords. The owning HUD app calls
        :func:`daimon.play.art_render.paint_overlays_as_kgp` on these to
        stream PNG bytes via Kitty Graphics Protocol; the screenshot
        pipeline pastes the same PNGs onto a PIL canvas for export.

    Layout shape is constant (~36 lines × ``width`` cols) regardless of
    whether the current cursor sits on an action step or a chrome phase
    step. When the cursor lands on a phase step (LINEUP / ROUND_START /
    OUTCOME), the middle "log" band is replaced with a centered chrome
    banner; the rest (title, lineups, status bar) stays intact.

    ``color=False`` strips ANSI escapes — useful for tests + plain logs.
    """
    top_arrows, bot_arrows = _connection_arrows(frame, color=color)

    lines: List[str] = []
    overlays: List[ImageOverlay] = []

    lines.append(_box_top(width=width))
    lines.append(_title_line(frame, color=color, width=width))
    lines.append(_divider(width=width))

    # Opponent lineup — row of 6 tiles painted via KGP overlays.
    opp_lines, opp_overlays = _render_lineup_section(
        frame, side=Side.OPPONENT, color=color,
        width=width, base_row=len(lines),
    )
    lines.extend(opp_lines)
    overlays.extend(opp_overlays)

    lines.append(_divider_with_arrows(top_arrows, width=width))

    if frame.current_step is not None and frame.current_step.is_phase:
        lines.extend(_chrome_section(frame, color=color, width=width))
    else:
        lines.extend(_log_section(frame, color=color, width=width))

    lines.append(_divider_with_arrows(bot_arrows, width=width))

    play_lines, play_overlays = _render_lineup_section(
        frame, side=Side.PLAYER, color=color,
        width=width, base_row=len(lines),
    )
    lines.extend(play_lines)
    overlays.extend(play_overlays)

    lines.append(_divider(width=width))
    lines.append(_status_line(frame, color=color, width=width))
    lines.append(_box_bottom(width=width))
    return "\n".join(lines), overlays


# ---------------------------------------------------------------------------
# Connection arrows — overlaid on the dividers between lineups + log band.
# Each card has its own arrow column at the centre of its tile.
# ---------------------------------------------------------------------------

def _arrow_col_for_position(position: int) -> int:
    """Column inside the divider's interior (0-indexed, excludes ╠) for the
    centre of the tile at the given lineup ``position``.

    Tile p sits at LEFT_PAD + p × (TILE_W + TILE_GAP). The arrow is
    centred at + TILE_W // 2.
    """
    return LEFT_PAD + position * (TILE_W + TILE_GAP) + TILE_W // 2


def _connection_arrows(
    frame: Frame, *, color: bool,
) -> tuple[list[tuple[int, str, str]], list[tuple[int, str, str]]]:
    """Compute (top_divider_arrows, bot_divider_arrows) for the active conn.

    Each arrow is (col, glyph, ansi_escape). The top divider sits below the
    OPPONENT lineup; the bot divider sits above the PLAYER lineup. We paint a
    directional arrow at each actor/target column on the appropriate divider
    so the eye can trace cause→effect across the grid.
    """
    if frame.animation is None or frame.animation.connection is None:
        return [], []
    conn = frame.animation.connection
    ansi = EMISSION_COLOR.get(conn.color, "") if color else ""

    top: list[tuple[int, str, str]] = []
    bot: list[tuple[int, str, str]] = []
    # Actor: ▼ if opponent (action travels down), ▲ if player (travels up).
    if conn.actor_side == Side.OPPONENT:
        top.append((_arrow_col_for_position(conn.actor_position), "▼", ansi))
    else:
        bot.append((_arrow_col_for_position(conn.actor_position), "▲", ansi))
    # Target: arrow points INTO the target's lineup.
    if conn.target_side == Side.OPPONENT:
        top.append((_arrow_col_for_position(conn.target_position), "▲", ansi))
    else:
        bot.append((_arrow_col_for_position(conn.target_position), "▼", ansi))
    return top, bot


def _divider_with_arrows(
    arrows: list[tuple[int, str, str]], *, width: int = WIDTH,
) -> str:
    """Render a divider with optional arrow overlays at given columns.

    ``arrows`` is a list of (col, glyph, ansi_escape) tuples. Columns are
    0-indexed within the divider's interior (i.e. excluding the corner
    chars). The default divider line is ``═`` × (width-2); each overlay
    replaces that column with the colored glyph.
    """
    if not arrows:
        return _divider(width=width)
    interior_len = width - 2
    cells = ["═"] * interior_len
    overlays: dict[int, str] = {}
    for col, glyph, ansi in arrows:
        if 0 <= col < interior_len:
            cells[col] = "\0"   # placeholder we'll replace below
            overlays[col] = (ansi + glyph + RESET) if ansi else glyph
    out = ["╠"]
    for i, ch in enumerate(cells):
        out.append(overlays[i] if ch == "\0" else ch)
    out.append("╣")
    return "".join(out)


# ---------------------------------------------------------------------------
# Idle screen + mining strip — unchanged contract, just wider box
# ---------------------------------------------------------------------------

def render_idle(
    *,
    recent: Iterable[str] = (),
    mine_ticks: Iterable[dict] = (),
    color: bool = True,
    width: int = WIDTH,
) -> str:
    """Screen shown when no match is loaded. Lists last-rendered match ids
    and the most recent mining ticks (from ``mine_buffer.jsonl``).

    Renderer doesn't fetch anything — the app passes both feeds in. Caller
    decides truncation; this layer just paints what it's given (capped per
    section so the box layout stays predictable).
    """
    lines: list[str] = []
    lines.append(_box_top(width=width))
    lines.append(_centered("DAIMON — spectator HUD", color=color,
                           bold=True, width=width))
    lines.append(_divider(width=width))
    lines.append(_centered("waiting for match…", color=color,
                           dim=True, width=width))
    lines.append(_centered("(claude can run a match via dm_match_npc, dm_match)",
                           color=color, dim=True, width=width))
    lines.append(_divider(width=width))
    recent_l = list(recent)[:4]
    if recent_l:
        lines.append(_left("  recent activity:", color=color,
                           bold=True, width=width))
        for r in recent_l:
            lines.append(_left(f"    · {r}", color=color,
                               dim=True, width=width))
    else:
        lines.append(_left("  recent activity: (none yet)",
                           color=color, dim=True, width=width))
    lines.append(_divider(width=width))
    # Mining pane — chrome the agent's productive work even while no match
    # is loaded. Shows the last few ticks with running balance so Santiago
    # can tell at a glance "how much currency landed since I last looked."
    ticks_l = list(mine_ticks)[-6:]
    if ticks_l:
        lines.append(_left("  ⛏ MINING (live):", color=color,
                           bold=True, width=width))
        for t in ticks_l:
            lines.append(_left("    " + _format_mine_tick(t, color=color),
                               color=False, width=width))
    else:
        lines.append(_left("  ⛏ MINING (live): waiting for first tool call…",
                           color=color, dim=True, width=width))
    lines.append(_divider(width=width))
    lines.append(_status_line_idle(color=color, width=width))
    lines.append(_box_bottom(width=width))
    return "\n".join(lines)


def render_mining_strip(
    tick: Optional[dict] = None,
    *,
    color: bool = True,
    width: int = WIDTH,
) -> str:
    """Compact 1-line mining ticker for the bottom of the match frame.

    Returns a single line (no trailing newline) shaped to the same width
    as the box so it slots cleanly under the match frame. ``tick=None``
    renders an empty placeholder of the same width — keeps the terminal
    layout stable between mining states.
    """
    if tick is None:
        body = " ⛏ idle …"
    else:
        body = " " + _format_mine_tick(tick, color=color)
    visible = _strip_ansi(body)
    pad = max(0, width - len(visible))
    if color:
        return DIM + body + RESET + " " * pad
    return body + " " * pad


def _format_mine_tick(tick: dict, *, color: bool) -> str:
    """One-liner for a single mine_buffer entry. Used in idle pane + strip.

    Layout:  '⛏  +3¤  Edit                                  balance: 247'
    Milestones get a star and the note instead of tool/amount.
    """
    kind = tick.get("kind", "?")
    amount = tick.get("amount", 0)
    bal = tick.get("balance_after", 0)
    tool = tick.get("tool", "")
    note = tick.get("note", "")

    if kind == "milestone":
        glyph = "★"
        body = f"{glyph}  {note or 'milestone'}"
    elif kind == "match":
        glyph = "⚔"
        body = f"{glyph}  match: {note or 'resolved'}"
    elif kind == "pull":
        glyph = "✦"
        body = f"{glyph}  pull: {note or 'received'}"
    else:   # mine (default)
        glyph = "⛏"
        sign = "+" if amount >= 0 else ""
        body = f"{glyph}  {sign}{amount}¤  {tool}"

    # Pad body to the left half so balance lines up on the right.
    visible = body
    target_left = 48
    pad_inner = max(1, target_left - len(visible))
    line = body + " " * pad_inner + f"balance: {bal}¤"

    if not color:
        return line

    if kind == "milestone":
        return BOLD + YELLOW + line + RESET
    if kind == "match":
        return CYAN + line + RESET
    if kind == "pull":
        return MAGENTA + line + RESET
    return GREEN + line + RESET


# ---------------------------------------------------------------------------
# Box-drawing primitives
# ---------------------------------------------------------------------------

def _box_top(*, width: int = WIDTH) -> str:
    return "╔" + "═" * (width - 2) + "╗"


def _box_bottom(*, width: int = WIDTH) -> str:
    return "╚" + "═" * (width - 2) + "╝"


def _divider(*, width: int = WIDTH) -> str:
    return "╠" + "═" * (width - 2) + "╣"


def _blank(*, width: int = WIDTH) -> str:
    return "║" + " " * (width - 2) + "║"


def _frame_line(content: str, *, width: int = WIDTH) -> str:
    """Pad/truncate ``content`` (visible-width) to fit between the box walls."""
    visible_n = visible_len(content)
    pad = (width - 2) - visible_n
    if pad < 0:
        # Truncate visible portion; ANSI inside will be passed through, which
        # is fine — terminals don't care about partial color codes if we
        # never strip an open without its close. We accept that cost and
        # truncate the raw string by visible width.
        content = _truncate_visible(content, width - 2)
        pad = 0
    return "║" + content + " " * pad + "║"


def _centered(text: str, *, color: bool, bold: bool = False,
              dim: bool = False, width: int = WIDTH) -> str:
    visible = text
    pad_each = max(0, ((width - 2) - len(visible)) // 2)
    body = " " * pad_each + text
    if color:
        if bold:
            body = BOLD + body + RESET
        elif dim:
            body = DIM + body + RESET
    return _frame_line(body, width=width)


def _left(text: str, *, color: bool, bold: bool = False,
          dim: bool = False, width: int = WIDTH) -> str:
    if color:
        if bold:
            text = BOLD + text + RESET
        elif dim:
            text = DIM + text + RESET
    return _frame_line(text, width=width)


# ---------------------------------------------------------------------------
# Title + status
# ---------------------------------------------------------------------------

def _title_line(frame: Frame, *, color: bool, width: int = WIDTH) -> str:
    """Top banner — usually opponent name; swaps to phase label on chrome."""
    cur = frame.current_step
    if cur is not None and cur.is_phase:
        if cur.phase == Phase.LINEUP:
            return _centered("DAIMON — match starting",
                             color=color, bold=True, width=width)
        if cur.phase == Phase.ROUND_START:
            return _centered(
                f"DAIMON — round {cur.round_number} begins",
                color=color, bold=True, width=width,
            )
        if cur.phase == Phase.OUTCOME:
            outcome = frame.match.outcome
            if outcome is None:
                title = "DAIMON — match concluded"
            elif outcome.winner == Side.DRAW:
                title = "DAIMON — draw"
            else:
                title = f"DAIMON — {outcome.winner.value.upper()} wins"
            return _centered(title, color=color, bold=True, width=width)
    match = frame.match
    opp = match.participants.get("opponent")
    name = opp.name if opp else "opponent"
    rank = opp.rank if opp else "?"
    title = f"DAIMON — {name}  ({rank})"
    return _centered(title, color=color, bold=True, width=width)


def _status_line(frame: Frame, *, color: bool, width: int = WIDTH) -> str:
    cur_step = frame.current_step
    if cur_step is None:
        round_no = "-"
        action_no = "-/-"
    elif cur_step.is_phase:
        # Chrome steps don't have a parent-action notion. Use a label per phase
        # so the status bar stays informative without misreporting "act 1/0".
        if cur_step.phase == Phase.LINEUP:
            round_no = "-"
            action_no = "lineup"
        elif cur_step.phase == Phase.ROUND_START:
            round_no = str(cur_step.round_number)
            action_no = "intro"
        else:   # OUTCOME
            round_no = str(cur_step.round_number)
            action_no = "final"
    else:
        round_no = str(cur_step.round_number)
        per = frame.elapsed_steps_in_round.get(cur_step.round_number, 0)
        # round-action count is hard to compute precisely under nested triggers;
        # we show the step's parent action_index for orientation.
        action_no = f"{cur_step.action_index + 1}/{per}"

    speed_str = _speed_label(frame.speed)
    state_str = _status_label(frame.status, color=color)

    left = (
        f" R{round_no} act {action_no} · "
        f"step {frame.cursor + 1}/{frame.total_steps} · "
        f"{speed_str} · {state_str}"
    )
    right = "[sp]pause [←→]step [↑↓]spd [r]rst [n]end [q]quit "

    # Compose with right-justified controls if there's room.
    visible_left = _strip_ansi(left)
    visible_right = _strip_ansi(right)
    avail = (width - 2) - len(visible_left) - len(visible_right) - 1
    if avail >= 1:
        return _frame_line(left + " " * avail + " │ " + right, width=width)
    return _frame_line(left, width=width)


def _status_line_idle(*, color: bool, width: int = WIDTH) -> str:
    left = " IDLE — no match loaded"
    right = "[q]quit  [n]demo  "
    avail = (width - 2) - len(left) - len(right) - 1
    if avail >= 1:
        return _frame_line(left + " " * avail + " │ " + right, width=width)
    return _frame_line(left, width=width)


def _speed_label(speed: float) -> str:
    # Compact representation: 0.25 → "0.25×", 1.0 → "1.0×".
    if speed >= 1:
        return f"{speed:.1f}×"
    return f"{speed:.2f}×"


def _status_label(status: PlaybackStatus, *, color: bool) -> str:
    icons = {
        PlaybackStatus.IDLE:    "○ idle",
        PlaybackStatus.PLAYING: "▶ playing",
        PlaybackStatus.PAUSED:  "❚❚ paused",
        PlaybackStatus.ENDED:   "■ ended",
    }
    text = icons.get(status, str(status.value))
    if not color:
        return text
    color_for = {
        PlaybackStatus.PLAYING: GREEN,
        PlaybackStatus.PAUSED:  YELLOW,
        PlaybackStatus.ENDED:   CYAN,
        PlaybackStatus.IDLE:    GRAY,
    }
    return color_for.get(status, "") + text + RESET


# ---------------------------------------------------------------------------
# Lineup section — tile rows with KGP-painted card art
# ---------------------------------------------------------------------------

def _render_lineup_section(
    frame: Frame, *, side: Side, color: bool,
    width: int, base_row: int,
) -> Tuple[List[str], List[ImageOverlay]]:
    """Render one side's label + tile row + KGP overlays.

    Returns ``(lines, overlays)`` where overlays carry absolute frame
    coordinates ready for :func:`paint_overlays_as_kgp`. ``base_row`` is
    the absolute row in the full frame at which the LABEL line will sit;
    the tile row begins at ``base_row + 1``.

    The label is a single ASCII line ("OPPONENT" / "PLAYER (santiago)").
    Each tile is 11 rows tall: 1 (top border) + TILE_ART_H (art region,
    blank cells holding the KGP overlay) + 1 (bottom border) + 2 (caption
    rows: name + HP bar). Animation effects (color_flash, intent, glow,
    overlay_icon) are baked into the captions so a non-KGP fallback (e.g.
    --no-color, screenshot text-only path) still reads the cause/effect.
    """
    part = frame.match.participants.get(side.value)
    if part is None:
        return ([_left(f"  {side.value.upper()}  (missing)",
                       color=color, dim=True, width=width)], [])

    label = "OPPONENT" if side == Side.OPPONENT else f"PLAYER ({part.name})"
    lines: List[str] = [_left(f"  {label}", color=color,
                              bold=True, width=width)]

    cards = sorted(part.loadout, key=lambda c: c.position)
    tiles = [_card_to_tile(frame, side, c, color=color) for c in cards]

    composed = compose_row(tiles, gap=TILE_GAP, left_pad=LEFT_PAD)

    # Wrap each composed row in the outer ║ walls + trailing pad.
    inner_w = width - 2
    for line in composed.lines:
        line_visible = visible_len(line)
        pad_n = max(0, inner_w - line_visible)
        if line_visible > inner_w:
            line = _truncate_visible(line, inner_w)
            pad_n = 0
        lines.append("║" + line + " " * pad_n + "║")

    # Translate each tile's local overlay to absolute frame coords.
    # The tile row's row-0 sits at base_row + 1 (the LABEL line is row 0,
    # tiles start at row 1 within this section). base_col = 1 accounts
    # for the left wall ║ (the composed row's columns are 0-based starting
    # immediately to the right of that wall, with LEFT_PAD already baked
    # in by compose_row).
    overlays = overlays_for_row(composed, base_row=base_row + 1, base_col=1)
    return lines, overlays


def _emissions_for(frame: Frame, side: Side, position: int) -> list[CardEmission]:
    """Return primitive emissions targeting (side, position) for the current beat.

    Pure read of ``frame.animation``. Empty list when no animation is active
    (phase steps, idle, dead frames). Order matches registry paint order so
    the renderer can apply them last-write-wins.
    """
    if frame.animation is None:
        return []
    return frame.animation.for_card(side, position)


def _card_to_tile(frame: Frame, side: Side, card, *, color: bool) -> Tile:
    """Build one card's :class:`Tile` (KGP art region + animation captions).

    Animation effects compose into the captions so the play HUD reads
    cause/effect even on a no-KGP fallback path:

      shake → caption offset (deferred to V1.1 — terminal jitters poorly)
      intent → BOLD on both captions
      color_flash → name-color override (red on damage, green on heal, …)
      zap → element-color flash on name (cascade triggers)
      glow → bold yellow brackets around the name
      overlay_icon → 1-char prefix on the name caption

    The KGP overlay points at the raw card art PNG resolved via
    :func:`daimon.play.art_render.resolve_card_art` (lazy fetch on first
    sight, pure cache hit thereafter — see :func:`prewarm_card_art`,
    called from :func:`HudApp._load_match`). When art is missing on disk
    the tile renders a placeholder block instead and emits no overlay.
    """
    cur_hp = frame.hp.get(side, card.position, default=card.hp)
    is_dead = cur_hp <= 0

    # Read primitive emissions for this card.
    emissions = _emissions_for(frame, side, card.position)
    flash_color: Optional[str] = None
    intent_active = False
    glow_active = False
    overlay_icon: Optional[str] = None
    for e in emissions:
        if color and e.kind == "color_flash" and e.color:
            flash_color = EMISSION_COLOR.get(e.color, "")
        elif color and e.kind == "zap" and e.color:
            # Zap overrides flash with element color (more specific signal).
            flash_color = EMISSION_COLOR.get(e.color, "") or flash_color
        elif color and e.kind == "intent":
            intent_active = True
        elif color and e.kind == "glow":
            glow_active = True
        elif e.kind == "overlay_icon" and e.icon:
            overlay_icon = e.icon

    name = (card.short_name or card.name or card.species)[:7]
    elem_color = ELEMENT_COLOR.get(card.element.value, "") if color else ""

    # Choose name color: flash > glow > element (so impact reads "now").
    if flash_color:
        name_color = flash_color
    elif glow_active and color:
        name_color = YELLOW + BOLD
    else:
        name_color = elem_color

    bracket_open = "["
    bracket_close = "]"
    if glow_active and color:
        bracket_open = BOLD + YELLOW + "[" + RESET + name_color
        bracket_close = BOLD + YELLOW + "]" + RESET

    if is_dead and color:
        name_text = GRAY + f"[{name}]" + RESET
    else:
        name_text = (name_color + f"{bracket_open}{name}{bracket_close}"
                     + (RESET if name_color else ""))

    # Caption row 1 — name (with optional 1-char overlay-icon prefix).
    if overlay_icon:
        cap1 = f"{overlay_icon} {name_text}"
    else:
        cap1 = name_text

    # Caption row 2 — HP bar (10 pips) + numeric HP.
    bar = _hp_bar(cur_hp, card.hp_max, color=color)
    hp_str = f"{cur_hp:>2}/{card.hp_max:<2}"
    cap2 = f"HP: {bar} {hp_str}"

    # Intent — bold both captions to telegraph upcoming impact.
    if intent_active and color:
        cap1 = BOLD + cap1 + RESET
        cap2 = BOLD + cap2 + RESET

    return render_tile(
        card_id=card.species,
        width=TILE_W,
        art_h=TILE_ART_H,
        caption_lines=(cap1, cap2),
        dim=is_dead,
        mode=RenderMode.OVERLAY_ONLY,
        color=color,
    )


def _hp_bar(cur: int, mx: int, *, color: bool) -> str:
    if mx <= 0:
        return "░" * HP_BAR_LEN
    pct = max(0.0, min(1.0, cur / mx))
    filled = int(round(pct * HP_BAR_LEN))
    bar_text = "█" * filled + "░" * (HP_BAR_LEN - filled)
    if not color:
        return bar_text
    if pct >= 0.66:
        col = GREEN
    elif pct >= 0.33:
        col = YELLOW
    else:
        col = RED
    return col + bar_text + RESET


# ---------------------------------------------------------------------------
# Log section
# ---------------------------------------------------------------------------

LOG_ROWS = 5    # rows reserved for the action log


def _log_section(frame: Frame, *, color: bool, width: int = WIDTH) -> list[str]:
    if not frame.log_tail:
        return [_blank(width=width) for _ in range(LOG_ROWS)]
    # Build lines for each step in the tail; current step gets ▶ marker.
    items = list(frame.log_tail)
    # We want most-recent at the bottom, so leave items in chronological order.
    rendered = [
        _log_line(s, is_current=(s.index == frame.cursor),
                  color=color, width=width)
        for s in items
    ]
    # Pad the top with blanks if we have < LOG_ROWS items so the layout is stable.
    while len(rendered) < LOG_ROWS:
        rendered.insert(0, _blank(width=width))
    # If we somehow have more (shouldn't, capped at LOG_TAIL_LEN), take last N.
    if len(rendered) > LOG_ROWS:
        rendered = rendered[-LOG_ROWS:]
    return rendered


def _log_line(step: Step, *, is_current: bool, color: bool,
              width: int = WIDTH) -> str:
    if step.is_phase:
        return _phase_log_line(step, is_current=is_current,
                               color=color, width=width)
    indent = "  " + ("  " * step.depth)
    arrow = "▶" if is_current else " "
    glyph = KIND_PREFIX.get(step.action.kind, "·")
    text = step.action.log_line.strip() or _fallback_log(step)
    side_marker = _side_marker(step.action.actor.side)
    line = f"{indent}{arrow} {glyph} {side_marker} {text}"
    if not color:
        return _frame_line(line, width=width)
    if is_current:
        line = BOLD + line + RESET
    elif step.depth > 0:
        line = DIM + line + RESET
    return _frame_line(line, width=width)


def _phase_log_line(step: Step, *, is_current: bool, color: bool,
                    width: int = WIDTH) -> str:
    """Compact in-log marker for chrome events (used when scrolled past)."""
    arrow = "▶" if is_current else " "
    if step.phase == Phase.LINEUP:
        body = "─── match begins ───"
    elif step.phase == Phase.ROUND_START:
        body = f"═══ ROUND {step.round_number} ═══"
    else:   # OUTCOME
        body = "═══ match concluded ═══"
    line = f"  {arrow}  {body}"
    if not color:
        return _frame_line(line, width=width)
    if is_current:
        line = BOLD + CYAN + line + RESET
    else:
        line = CYAN + line + RESET
    return _frame_line(line, width=width)


# ---------------------------------------------------------------------------
# Chrome section — replaces the log band when current step is a phase
# ---------------------------------------------------------------------------

def _chrome_section(frame: Frame, *, color: bool, width: int = WIDTH) -> list[str]:
    """Centered banner shown when cursor sits on a phase step.

    Always produces exactly ``LOG_ROWS`` lines so the overall frame stays
    a fixed height regardless of what the cursor is showing.
    """
    cur = frame.current_step
    assert cur is not None and cur.is_phase
    if cur.phase == Phase.LINEUP:
        rows = _lineup_chrome_rows(frame)
    elif cur.phase == Phase.ROUND_START:
        rows = _round_chrome_rows(frame, cur.round_number)
    else:
        rows = _outcome_chrome_rows(frame)
    # Pad/truncate to exactly LOG_ROWS so layout height stays constant.
    while len(rows) < LOG_ROWS:
        rows.append("")
    rows = rows[:LOG_ROWS]
    return [_centered(r, color=color, bold=(i == 0),
                      dim=(i > 0 and not r.startswith("═")),
                      width=width)
            for i, r in enumerate(rows)]


def _lineup_chrome_rows(frame: Frame) -> list[str]:
    p = frame.match.participants.get("player")
    o = frame.match.participants.get("opponent")
    pl = p.name if p else "?"
    op = o.name if o else "?"
    return [
        "═══ MATCH STARTING ═══",
        f"{pl}  vs  {op}",
        "lineups revealed — combat begins",
        "",
        "",
    ]


def _round_chrome_rows(frame: Frame, round_no: int) -> list[str]:
    # Show first-player for the round, if findable in source rounds list.
    fp = ""
    for r in frame.match.rounds:
        if r.round == round_no:
            fp = f"first player: {r.first_player.value}"
            break
    return [
        f"═══ ROUND {round_no} ═══",
        fp,
        "",
        "",
        "",
    ]


def _outcome_chrome_rows(frame: Frame) -> list[str]:
    o = frame.match.outcome
    if o is None:
        return ["═══ MATCH OVER ═══", "", "", "", ""]
    p_name = (frame.match.participants.get("player").name
              if frame.match.participants.get("player") else "player")
    o_name = (frame.match.participants.get("opponent").name
              if frame.match.participants.get("opponent") else "opponent")
    p_hp = o.player_hp_remaining
    o_hp = o.opponent_hp_remaining
    rounds = o.stats.round_count
    cards_killed = o.stats.cards_killed
    p_killed = cards_killed.get("player", 0)
    o_killed = cards_killed.get("opponent", 0)
    if o.winner == Side.DRAW:
        banner = "═══ DRAW ═══"
    else:
        banner = f"═══ {o.winner.value.upper()} WINS ═══"
    return [
        banner,
        f"{p_name}: {p_hp} hp · {o_name}: {o_hp} hp",
        f"rounds: {rounds} · kills — player {p_killed}, opponent {o_killed}",
        "",
        "",
    ]


def _side_marker(side: Side) -> str:
    return "↑" if side == Side.PLAYER else "↓"


def _fallback_log(step: Step) -> str:
    a = step.action
    actor = a.actor.card if a.actor else "?"
    target = a.target.card if a.target else None
    amt = a.amount
    if a.kind == ActionKind.DAMAGE:
        if target and amt is not None:
            return f"{actor} hits {target} for {amt}"
        return f"{actor} attacks"
    if a.kind == ActionKind.HEAL:
        return f"{actor} heals {target or 'self'} for {amt or '?'}"
    if a.kind == ActionKind.DEATH:
        return f"{target or actor} falls"
    if a.kind == ActionKind.SHIELD:
        return f"{actor} gains shield {amt or ''}".strip()
    if a.kind == ActionKind.BUFF:
        return f"{actor} buffs {target or 'self'}"
    if a.kind == ActionKind.DEBUFF:
        return f"{actor} debuffs {target or '?'}"
    return f"{actor} → {a.kind.value}"


# ---------------------------------------------------------------------------
# Tiny ANSI utils
# ---------------------------------------------------------------------------

_ANSI_RE = None


def _strip_ansi(s: str) -> str:
    """Strip ANSI CSI escapes for visible-width math."""
    global _ANSI_RE
    if _ANSI_RE is None:
        import re
        _ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
    return _ANSI_RE.sub("", s)


def _truncate_visible(s: str, n: int) -> str:
    """Truncate string s to visible width n. Naïve — counts code points."""
    visible = _strip_ansi(s)
    if len(visible) <= n:
        return s
    # Walk forward keeping a running visible-char count, drop after n.
    out = []
    count = 0
    i = 0
    while i < len(s) and count < n:
        if s[i] == "\x1b":
            # Pass through CSI to next 'm'
            j = s.find("m", i)
            if j < 0:
                out.append(s[i])
                i += 1
                continue
            out.append(s[i:j + 1])
            i = j + 1
        else:
            out.append(s[i])
            count += 1
            i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Misc helpers (exposed for tests)
# ---------------------------------------------------------------------------

def speed_ladder() -> tuple[float, ...]:
    """Re-export for tests/UI."""
    return SPEED_LADDER


def render_outcome_banner(match: Match, *, color: bool = True) -> Optional[str]:
    """Optional one-line winner banner. Renderer concatenates as needed."""
    outcome = match.outcome
    if outcome is None:
        return None
    if outcome.winner == Side.DRAW:
        verdict = "DRAW"
    else:
        verdict = f"{outcome.winner.value.upper()} WINS"
    label = f" === {verdict} — player {outcome.player_hp_remaining} hp · opponent {outcome.opponent_hp_remaining} hp === "
    if color:
        return BOLD + CYAN + label + RESET
    return label
