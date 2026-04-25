"""Interactive shop TUI — `daimon shop` (default mode), V2 tile-grid layout.

Browse today's 6-slot rotation as a 3×2 grid of card-art TILES with a side
detail panel showing the focused card. Same agentic-first contract:

  * ``render_frame(view, mode=...) -> (frame_str, overlays)`` — pure, no I/O.
    HALFBLOCK mode rasterises the art into the frame string itself (live
    terminals); OVERLAY_ONLY emits blank cells and a list of
    :class:`ImageOverlay` records the screenshot pipeline pastes on top.
  * ``run_shop_tui()`` — human event loop. Default mode HALFBLOCK.
  * ``--no-tui`` / ``--json`` on the CLI keep the scripted paths working.

Layout (110 col × ~28 row frame):

    ╔══════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
    ║  DAIMON · shop                                                                  santiago · 8a3c…f1e0         ║
    ╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
    ║  balance  1,700 ¤   ·   weekly  2 / 5   ·   refresh in  14h 23m 11s                                          ║
    ╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
    ║                                                                                  │  DETAIL — slot 0           ║
    ║   ╔══════════════════════╗  ┌──────────────────────┐  ┌──────────────────────┐    │  aegis_lion · NORMAL · 6/8/30
    ║   ║ ░░░░░ART░░░░░░░░░░░░ ║  │   art (selected hi)  │  │      art             │    │  ╔══════════════════════╗
    ║   ║                      ║  │                      │  │                      │    │  ║                      ║
    ║   ║                      ║  │                      │  │                      │    │  ║                      ║
    ║   ║                      ║  │                      │  │                      │    │  ║   HERO  ART          ║
    ║   ╚══════════════════════╝  └──────────────────────┘  └──────────────────────┘    │  ║                      ║
    ║   aegis_lion        300¤    blazewolf       300¤      frost_fang   [OWNED]       │  ╚══════════════════════╝
    ║   rare              cultur  rare            cultur    rare         (sold 14:02)  │  Heretic Manuscript
    ║   ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐    │  cultural · rare · 300 ¤
    ║   │      art             │  │      art             │  │      art             │    │  art:  …/aegis/heretic.png
    ║   ╚══════════════════════╝  ╚══════════════════════╝  ╚══════════════════════╝    │
    ║   stormhawk         800¤    mire_drake     300¤      verdant_horn   800¤        │
    ║   super_rare        anatom  rare           cultur    super_rare    anatom        │
    ╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
    ║  slot 0/5 · weekly 2/5            [←→↑↓]select [⏎]buy [r]refresh [q]quit                                     ║
    ╚══════════════════════════════════════════════════════════════════════════════════════════════════════════════╝

Keys:

  ←/→           move cursor horizontally between columns
  ↑/↓           move cursor vertically between rows
  ENTER         buy the selected slot (if unsold + balance OK + cap not hit)
  R             reload state from disk (catches background mining mints)
  Q / ESC       quit
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, TextIO, Tuple

from daimon.play.art_render import RenderMode, paint_overlays_as_kgp
from daimon.render.wezterm_bundle import terminal_supports_kgp
from daimon.play.hud.keyboard import Key, keyboard_reader_or_dummy
from daimon.play.screenshot import ImageOverlay
from daimon.play.tile import (
    Tile,
    compose_row,
    overlays_for_row,
    render_tile,
)
from daimon.play.tui_style import (
    BOLD,
    BRIGHT_CYAN,
    BRIGHT_GREEN,
    BRIGHT_RED,
    BRIGHT_YELLOW,
    CLEAR_SCREEN,
    CURSOR_HIDE,
    CURSOR_SHOW,
    DIM,
    GRAY,
    GREEN,
    HOME,
    RESET,
    centered,
    colorize,
    divider,
    frame_line,
    header,
    pad_visible,
    rarity_color,
    status_bar,
    visible_len,
)
from daimon.shop import ShopState
from daimon.shop.rotation import RotationSlot

# ---------------------------------------------------------------------------
# Layout constants — V2 frame
# ---------------------------------------------------------------------------

WIDTH = 110            # outer frame width (incl ║ walls)
GRID_COLS = 3
GRID_ROWS = 2
TILE_W = 24            # one card tile (incl single-rule borders)
TILE_ART_H = 12        # rows of art inside the tile
TILE_GAP = 2           # blank cells between tiles in a row
ROW_GAP = 1            # blank rows between tile-grid rows
LEFT_PAD = 3           # cells of left padding before the first tile
SEP_W = 3              # " │ " between tile grid and detail panel
DETAIL_W = WIDTH - 2 - LEFT_PAD - (GRID_COLS * TILE_W + (GRID_COLS - 1) * TILE_GAP) - SEP_W
# DETAIL_W computed: 110-2 - 3 - (3*24 + 2*2) - 3 = 108 - 3 - 76 - 3 = 26


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_secs_short(secs: int) -> str:
    """e.g. 51631 → '14h 20m 31s'. Negative or zero → 'now'."""
    if secs <= 0:
        return "now"
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _format_balance(n: int) -> str:
    return f"{n:,}"


def _ident_short(pubkey_hex: str, *, name: Optional[str] = None) -> str:
    short = f"{pubkey_hex[:4]}…{pubkey_hex[-4:]}" if pubkey_hex else "anon"
    if name:
        return f"{name} · {short}"
    return short


# ---------------------------------------------------------------------------
# View model
# ---------------------------------------------------------------------------

@dataclass
class ShopView:
    """Snapshot of shop UI state at one render tick.

    The 6 slots are laid out left-to-right, top-to-bottom into a
    GRID_COLS × GRID_ROWS grid; ``cursor`` is the linear slot index.
    """
    state: ShopState
    cursor: int = 0
    flash: Optional[str] = None
    flash_color: Optional[str] = None
    identity_name: Optional[str] = None

    @property
    def slot_count(self) -> int:
        return len(self.state.slots)

    @property
    def selected(self) -> Optional[RotationSlot]:
        if not self.state.slots:
            return None
        return self.state.slots[self.cursor]

    def move(self, dr: int, dc: int) -> None:
        """Move the cursor in the GRID_COLS × GRID_ROWS grid."""
        if not self.state.slots:
            return
        n = self.slot_count
        r, c = divmod(self.cursor, GRID_COLS)
        r = (r + dr) % GRID_ROWS
        c = (c + dc) % GRID_COLS
        idx = r * GRID_COLS + c
        if idx < n:
            self.cursor = idx


# ---------------------------------------------------------------------------
# Render — pure
# ---------------------------------------------------------------------------

def render_frame(view: ShopView, *,
                 mode: RenderMode = RenderMode.HALFBLOCK,
                 color: bool = True,
                 width: int = WIDTH
                 ) -> Tuple[str, List[ImageOverlay]]:
    """Return (frame_string, overlays).

    HALFBLOCK rasterises card art into the frame text itself (live
    terminals); OVERLAY_ONLY emits blank cells under each tile's art region
    and returns absolute-coord overlays so :mod:`screenshot` can paste real
    PIL bitmaps over them. Live runners ignore the overlays list.
    """
    s = view.state

    # ----- HEADER ------------------------------------------------------
    header_lines = header(
        "shop",
        identity=_ident_short(s.pubkey_hex, name=view.identity_name),
        width=width,
    )

    # ----- SUMMARY (balance / weekly / refresh) ------------------------
    bal_color = (BRIGHT_GREEN if (color and s.balance >= 800)
                 else BRIGHT_YELLOW if (color and s.balance >= 300)
                 else GRAY)
    weekly_color = (BRIGHT_RED if color and s.weekly_count >= s.weekly_cap
                    else GRAY)
    bal_field = (colorize(f"{_format_balance(s.balance)} ¤", bal_color, bold=True)
                 if color else f"{s.balance} ¤")
    weekly_field = (colorize(f"{s.weekly_count}/{s.weekly_cap}", weekly_color, bold=True)
                    if color else f"{s.weekly_count}/{s.weekly_cap}")
    refresh_field = (colorize(_format_secs_short(s.seconds_until_rotation), DIM)
                     if color else _format_secs_short(s.seconds_until_rotation))
    summary = (f"  balance  {bal_field}   ·   "
               f"weekly  {weekly_field}   ·   "
               f"refresh in  {refresh_field}")
    summary_line = frame_line(summary, width)

    # ----- TILE GRID + DETAIL PANEL ------------------------------------
    body_lines, body_overlays = _render_grid_and_detail(
        view, mode=mode, color=color, width=width,
        body_top_row=len(header_lines) + 2,   # header(3) + summary(1) + divider(1)
    )

    # ----- FLASH (optional) --------------------------------------------
    flash_lines: list[str] = []
    if view.flash:
        flash_color = view.flash_color or BRIGHT_YELLOW
        flash_lines.append(divider(width))
        flash_lines.append(centered(
            view.flash, width,
            color=flash_color if color else None, bold=True))

    # ----- STATUS BAR --------------------------------------------------
    status_left = (f"slot {view.cursor + 1}/{max(1, view.slot_count)}  ·  "
                   f"weekly {s.weekly_count}/{s.weekly_cap}")
    status_keys = "[←→↑↓]select  [⏎]buy  [r]refresh  [q]quit"
    sb = status_bar(status_left, status_keys, width=width)

    # ----- ASSEMBLE ----------------------------------------------------
    body: list[str] = []
    body.append(summary_line)
    body.append(divider(width))
    body.extend(body_lines)
    body.extend(flash_lines)
    return "\n".join(header_lines + body + sb), body_overlays


def _render_grid_and_detail(view: ShopView, *,
                            mode: RenderMode,
                            color: bool,
                            width: int,
                            body_top_row: int
                            ) -> Tuple[List[str], List[ImageOverlay]]:
    """Render the 3×2 tile grid + side detail panel as one stack of lines.

    Returns ``(lines, overlays)`` — overlays are in *absolute* coords ready
    for the screenshot pipeline (already translated by ``body_top_row``).
    """
    slots = view.state.slots
    overlays: List[ImageOverlay] = []
    lines: List[str] = []

    # Build the tile-grid rows.
    grid_rows: List[List[Tile]] = []
    for ri in range(GRID_ROWS):
        row_tiles: List[Tile] = []
        for ci in range(GRID_COLS):
            idx = ri * GRID_COLS + ci
            slot = slots[idx] if idx < len(slots) else None
            row_tiles.append(_slot_to_tile(slot, idx, view.cursor,
                                           mode=mode, color=color))
        grid_rows.append(row_tiles)

    # Compose each row, splice the detail panel on the right.
    detail_lines, detail_overlay_specs = _render_detail_panel(
        view, mode=mode, color=color, width=DETAIL_W,
        # Hero tile sits inside the panel — it knows its own art_h.
    )

    # The detail panel spans the full body height; tile-grid spans rows1+row2.
    # We need both columns to share height; pad whichever is shorter.
    composed_grid_lines: List[str] = []
    cur_body_row = 0   # row offset (within the body) where this composed row starts
    for ri, row_tiles in enumerate(grid_rows):
        composed = compose_row(row_tiles, gap=TILE_GAP, left_pad=LEFT_PAD)
        # absolute top row of THIS composed_row in the full frame
        abs_row_top = body_top_row + cur_body_row
        overlays.extend(overlays_for_row(composed, base_row=abs_row_top, base_col=1))
        composed_grid_lines.extend(composed.lines)
        # blank gap row (only between rows)
        if ri < GRID_ROWS - 1:
            composed_grid_lines.append(" " * composed.width)
            cur_body_row = len(composed_grid_lines)
        else:
            cur_body_row = len(composed_grid_lines)

    # Now stitch grid + " │ " + detail.
    grid_w = LEFT_PAD + GRID_COLS * TILE_W + (GRID_COLS - 1) * TILE_GAP
    grid_h = len(composed_grid_lines)
    detail_h = len(detail_lines)
    total_h = max(grid_h, detail_h)
    while len(composed_grid_lines) < total_h:
        composed_grid_lines.append(" " * grid_w)
    while len(detail_lines) < total_h:
        detail_lines.append(" " * DETAIL_W)

    sep_color = colorize("│", DIM) if color else "│"
    sep = " " + sep_color + " "

    # Translate detail overlays from local-to-detail coords into absolute frame coords.
    detail_col_offset = 1 + grid_w + SEP_W   # +1 for the outer ║ wall
    for spec in detail_overlay_specs:
        abs_row = body_top_row + spec.row
        abs_col = detail_col_offset + spec.col
        overlays.append(ImageOverlay(
            row=abs_row,
            col=abs_col,
            rows=spec.rows,
            cols=spec.cols,
            image_path=spec.image_path,
            border_color=spec.border_color,
            border_width=spec.border_width,
            glow=spec.glow,
            caption=spec.caption,
            caption_color=spec.caption_color,
        ))

    for li in range(total_h):
        gline = composed_grid_lines[li]
        dline = detail_lines[li]
        # Pad grid line to grid_w in case any row came in short (shouldn't, but
        # defensive — visible_len matters because tiles emit ANSI).
        gpad = grid_w - visible_len(gline)
        if gpad > 0:
            gline = gline + " " * gpad
        body_line = gline + sep + pad_visible(dline, DETAIL_W)
        lines.append(frame_line(body_line, width))

    return lines, overlays


def _slot_to_tile(slot: Optional[RotationSlot], idx: int, cursor: int, *,
                  mode: RenderMode, color: bool) -> Tile:
    """Render one shop slot as a Tile.

    Empty slot (out-of-bounds): ghost tile.
    Sold slot: dim greyscale chrome + [OWNED hh:mm] caption.
    Unsold: rarity-coloured cost + skin name caption.
    """
    selected = (idx == cursor)

    if slot is None:
        return render_tile(
            card_id="",
            width=TILE_W,
            art_h=TILE_ART_H,
            caption_lines=("(no slot)", ""),
            ghost=True,
            mode=mode,
            color=color,
        )

    listing = slot.listing
    rar_col = rarity_color(listing.rarity) if color else None

    # Caption row 1 — card id (with selected/sold colour)
    name_text = listing.card_id
    if color:
        name_text = colorize(name_text, rar_col, bold=selected)
    name_line = pad_visible(f"[{slot.index}] " + name_text, TILE_W - 2)

    # Caption row 2 — price + state
    if slot.sold:
        ts = (slot.purchased_at or "")[11:16]
        right = f"[OWNED {ts}]" if ts else "[OWNED]"
        if color:
            right = colorize(right, GRAY, bold=False)
        cap2 = pad_visible(listing.rarity[:6], 7) + "  " + pad_visible(right, TILE_W - 2 - 9, align="right")
    else:
        rar_text = listing.rarity
        cost_text = f"{slot.cost} ¤"
        if color:
            rar_text = colorize(rar_text, rar_col, bold=False)
            cost_text = colorize(cost_text, BRIGHT_YELLOW, bold=True)
        cap2 = (pad_visible(rar_text, 11)
                + pad_visible(cost_text, TILE_W - 2 - 11, align="right"))

    # Border colour for screenshot overlay border
    border = None
    if mode == RenderMode.OVERLAY_ONLY:
        if selected:
            border = (130, 220, 240)        # bright cyan
        elif slot.sold:
            border = (90, 90, 90)
        else:
            border = (110, 110, 130)

    return render_tile(
        card_id=listing.card_id,
        skin_slug=listing.skin_slug,
        width=TILE_W,
        art_h=TILE_ART_H,
        caption_lines=(name_line, cap2),
        selected=selected,
        dim=slot.sold,
        mode=mode,
        border_color_rgb=border,
        color=color,
    )


def _render_detail_panel(view: ShopView, *,
                         mode: RenderMode,
                         color: bool,
                         width: int
                         ) -> Tuple[List[str], List[ImageOverlay]]:
    """Right-side detail panel: hero tile + skin metadata.

    Returns ``(lines, overlay_specs)`` — overlay coords are LOCAL to the
    panel (row=0,col=0 = top-left); the caller translates them to absolute
    frame coords.
    """
    sl = view.selected
    if sl is None:
        return ([
            "DETAIL",
            "(no slots in rotation)",
        ], [])

    listing = sl.listing
    title = "DETAIL — slot " + str(sl.index)
    if color:
        title = BOLD + title + RESET

    # Hero tile — wider art, no caption (we put caption rows below the tile).
    HERO_ART_H = 14
    hero = render_tile(
        card_id=listing.card_id,
        skin_slug=listing.skin_slug,
        width=width,
        art_h=HERO_ART_H,
        caption_lines=(),
        selected=True,
        mode=mode,
        border_color_rgb=(130, 220, 240) if mode == RenderMode.OVERLAY_ONLY else None,
        color=color,
    )

    rar_text = colorize(listing.rarity,
                        rarity_color(listing.rarity) if color else None,
                        bold=color)
    cost_text = (colorize(f"{sl.cost} ¤", BRIGHT_YELLOW, bold=True)
                 if color and not sl.sold else (f"{sl.cost} ¤" if not sl.sold else "—"))

    skin_line = listing.skin_name
    if color:
        skin_line = colorize(skin_line, BRIGHT_CYAN, bold=True)

    axis_line = f"{listing.skin_axis} · {rar_text} · {cost_text}"

    art_path = _short_path(listing.art_path, max_len=width - 6)
    if color:
        art_path = colorize(art_path, GRAY)

    sold_line = ""
    if sl.sold:
        ts = sl.purchased_at or ""
        sold_line = colorize(f"[OWNED {ts}]", GRAY) if color else f"[OWNED {ts}]"

    rows: List[str] = []
    rows.append(pad_visible(title, width))
    rows.extend(hero.lines)
    rows.append(pad_visible(skin_line, width))
    rows.append(pad_visible(axis_line, width))
    rows.append(pad_visible(f"slug: {listing.skin_slug}", width))
    rows.append(pad_visible("art: " + art_path, width))
    if sold_line:
        rows.append(pad_visible(sold_line, width))

    overlays: List[ImageOverlay] = []
    if hero.local_overlay is not None:
        # local row inside the panel = title(1) + hero overlay's own row offset
        ov = hero.local_overlay
        overlays.append(ImageOverlay(
            row=1 + ov.row,
            col=ov.col,
            rows=ov.rows,
            cols=ov.cols,
            image_path=ov.image_path,
            border_color=ov.border_color,
            border_width=ov.border_width,
            glow=ov.glow,
            caption=ov.caption,
            caption_color=ov.caption_color,
        ))

    return rows, overlays


def _short_path(p: str, max_len: int = 60) -> str:
    if len(p) <= max_len:
        return p
    return "…" + p[-(max_len - 1):]


# ---------------------------------------------------------------------------
# Interactive runner
# ---------------------------------------------------------------------------

@dataclass
class ShopRunner:
    """Event-loop driver for the shop TUI (live-terminal, HALFBLOCK mode)."""
    state_loader: Callable[[], ShopState]
    purchaser: Callable[[int], "tuple[bool, str]"]
    sink: TextIO = field(default_factory=lambda: sys.stdout)
    color: bool = True
    keyboard: bool = True
    identity_name: Optional[str] = None
    width: int = WIDTH

    _view: ShopView = field(init=False)
    _stop: bool = field(default=False, init=False)
    _last_signature: Optional[tuple] = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._view = ShopView(state=self.state_loader(),
                              identity_name=self.identity_name)

    def run(self) -> int:
        try:
            self._enter_screen()
            with keyboard_reader_or_dummy(self.keyboard) as kb:
                self._render(force=True)
                while not self._stop:
                    if kb is None:
                        break
                    key = kb.poll(timeout_ms=100)
                    if key is None:
                        continue
                    self._handle_key(key)
                    self._render()
        finally:
            self._exit_screen()
        return 0

    def render_once(self, *, mode: RenderMode = RenderMode.HALFBLOCK
                    ) -> Tuple[str, List[ImageOverlay]]:
        return render_frame(self._view, mode=mode,
                            color=self.color, width=self.width)

    # ----- key handling -----

    def _handle_key(self, key) -> None:
        if key in (Key.Q, Key.ESC):
            self._stop = True
            return
        if not self._view.state.slots:
            return
        if key == Key.LEFT:
            self._view.move(0, -1)
            self._view.flash = None
        elif key == Key.RIGHT:
            self._view.move(0, +1)
            self._view.flash = None
        elif key == Key.UP:
            self._view.move(-1, 0)
            self._view.flash = None
        elif key == Key.DOWN:
            self._view.move(+1, 0)
            self._view.flash = None
        elif key == Key.ENTER:
            self._buy()
        elif key == Key.R:
            self._reload(message="reloaded")

    def _buy(self) -> None:
        sl = self._view.selected
        if sl is None:
            return
        if sl.sold:
            self._view.flash = (
                f"slot {sl.index} already owned — rotates at next 00:00 UTC"
            )
            self._view.flash_color = BRIGHT_RED
            return
        ok, message = self.purchaser(sl.index)
        self._reload(message=None)
        self._view.flash = message
        self._view.flash_color = BRIGHT_GREEN if ok else BRIGHT_RED

    def _reload(self, *, message: Optional[str]) -> None:
        self._view.state = self.state_loader()
        if self._view.cursor >= self._view.slot_count and self._view.slot_count > 0:
            self._view.cursor = self._view.slot_count - 1
        if message is not None:
            self._view.flash = message
            self._view.flash_color = GREEN

    # ----- screen control -----

    def _enter_screen(self) -> None:
        if not self._is_tty():
            return
        self.sink.write(CURSOR_HIDE + CLEAR_SCREEN)
        self.sink.flush()

    def _exit_screen(self) -> None:
        if not self._is_tty():
            return
        try:
            self.sink.write(RESET + CURSOR_SHOW + "\n")
            self.sink.flush()
        except Exception:
            pass

    def _is_tty(self) -> bool:
        try:
            return self.sink.isatty()
        except Exception:
            return False

    def _render(self, *, force: bool = False) -> None:
        # KGP path (in our bundled WezTerm): emit blank art regions in the
        # text frame, then KGP-paint each ImageOverlay's bitmap on top.
        # Falls back to half-block when running in a non-KGP terminal.
        use_kgp = terminal_supports_kgp() and self._is_tty()
        mode = RenderMode.OVERLAY_ONLY if use_kgp else RenderMode.HALFBLOCK
        screen, overlays = render_frame(self._view, mode=mode,
                                        color=self.color, width=self.width)
        sig = (self._view.cursor, self._view.state.balance,
               tuple((sl.sold, sl.purchased_at) for sl in self._view.state.slots),
               self._view.flash, self._view.state.weekly_count,
               self._view.state.seconds_until_rotation // 60,
               use_kgp)
        if not force and sig == self._last_signature:
            return
        kgp_paint = paint_overlays_as_kgp(overlays) if use_kgp else ""
        self._last_signature = sig
        if self._is_tty():
            self.sink.write(HOME + screen + kgp_paint + "\n")
        else:
            self.sink.write(screen + "\n")
        try:
            self.sink.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI entry — wired by daimon.cli when subcommand is omitted.
# ---------------------------------------------------------------------------

def run_shop_tui(*, identity_name: Optional[str] = None,
                 sink: Optional[TextIO] = None,
                 color: bool = True,
                 keyboard: bool = True) -> int:
    """Build a runner against the real shop subsystem and start the loop."""
    from daimon.mining.ledger import InsufficientBalanceError
    from daimon.shop import (
        AlreadyOwnedError,
        SlotNotInRotationError,
        WeeklyCapExceededError,
        get_shop_state,
        purchase_slot,
    )

    def _load() -> ShopState:
        return get_shop_state()

    def _buy(slot_idx: int) -> "tuple[bool, str]":
        try:
            r = purchase_slot(slot_idx)
        except SlotNotInRotationError as e:
            return False, f"slot rejected: {e}"
        except AlreadyOwnedError as e:
            return False, f"already owned: {e}"
        except WeeklyCapExceededError as e:
            return False, f"weekly cap hit: {e}"
        except InsufficientBalanceError as e:
            return False, f"not enough ¤: {e}"
        return True, (f"BOUGHT  {r.skin_name}  ({r.skin_slug})  "
                      f"-{r.cost} ¤  →  balance {r.balance_after} ¤")

    runner = ShopRunner(
        state_loader=_load,
        purchaser=_buy,
        sink=sink or sys.stdout,
        color=color,
        keyboard=keyboard,
        identity_name=identity_name,
    )
    return runner.run()
