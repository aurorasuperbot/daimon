"""Interactive shop TUI — `daimon shop` (default mode).

Browse today's 6-slot rotation, see balance / weekly cap / refresh clock at
a glance, drill into a slot for full skin metadata, buy with one keystroke.

Surface contract — agentic-first then human:

  * The same module exposes ``render_frame(state) -> str`` (pure, no I/O)
    so agents can capture the rendered terminal state for log/preview.
  * ``run(state_loader, purchaser)`` is the human-facing event loop.
  * Both ``--no-tui`` (text dump) and ``--json`` (raw payload) remain on
    the CLI as the agent-friendly opt-outs — see ``daimon shop --help``.

Layout (80 col × ~22 row frame):

    ╔══════════════════════════════════════════════════════════════════════════════╗
    ║  DAIMON · shop                                       santiago · 8a3c…f1e0   ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  balance  1 700 ¤   ·   weekly  2 / 5   ·   refresh in  14h 23m 11s          ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  TODAY'S 6 SLOTS                                                             ║
    ║                                                                              ║
    ║  ▶ [0] aegis_lion         Heretic Manuscript  cultural   rare        300 ¤  ║
    ║    [1] blazewolf          Ukiyoe Scroll       cultural   rare        300 ¤  ║
    ║    [2] frost_fang         [OWNED 14:02 UTC]   cultural   rare              · ║
    ║    [3] stormhawk          Volcanic Plate      anatomical super_rare  800 ¤  ║
    ║    [4] mire_drake         Lacquer Mask        cultural   rare        300 ¤  ║
    ║    [5] verdant_horn       Iron Ribcage        anatomical super_rare  800 ¤  ║
    ║                                                                              ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  DETAIL — slot 0                                                             ║
    ║    card:    aegis_lion (Nemean Wanderer · NORMAL · rare)                     ║
    ║    skin:    Heretic Manuscript (heretic_manuscript)                          ║
    ║    axis:    cultural · price 300 ¤                                           ║
    ║    art:     ~/.daimon/art/v1_alpha/aegis_lion/variants/heretic_v1.png        ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  slot 0/5 · weekly 2/5     [←→]select [⏎]buy [r]refresh [q]quit              ║
    ╚══════════════════════════════════════════════════════════════════════════════╝

Keys:

  ←/→           move cursor between slots
  ↑/↓           same as ←/→ (ergonomic alias)
  ENTER         buy the selected slot (if unsold + balance OK + cap not hit)
  R             reload state from disk (catches background mining mints)
  Q / ESC       quit
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, TextIO

from daimon.play.hud.keyboard import Key, keyboard_reader_or_dummy
from daimon.play.tui_style import (
    BG_GRAY,
    BOLD,
    BRIGHT_GREEN,
    BRIGHT_RED,
    BRIGHT_YELLOW,
    CLEAR_SCREEN,
    CURSOR_HIDE,
    CURSOR_SHOW,
    DIM,
    GLYPH_CURSOR,
    GRAY,
    GREEN,
    HOME,
    RESET,
    WIDTH,
    blank,
    box_bottom,
    box_top,
    centered,
    colorize,
    compose_frame,
    cursor_prefix,
    divider,
    frame_line,
    header,
    left,
    pad_visible,
    rarity_color,
    row,
    section_title,
    split_row,
    status_bar,
)
from daimon.shop import ShopState
from daimon.shop.rotation import RotationSlot

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
    """Group thousands: 1700 → '1,700'."""
    return f"{n:,}"


def _ident_short(pubkey_hex: str, *, name: Optional[str] = None) -> str:
    """e.g. 'santiago · 8a3c…f1e0' or '8a3c…f1e0' if no nick."""
    short = f"{pubkey_hex[:4]}…{pubkey_hex[-4:]}" if pubkey_hex else "anon"
    if name:
        return f"{name} · {short}"
    return short


# ---------------------------------------------------------------------------
# View state — what render_frame consumes
# ---------------------------------------------------------------------------

@dataclass
class ShopView:
    """Snapshot of shop UI state at one render tick.

    Pure data — no callbacks, no I/O. The interactive runner mutates this
    in place; the renderer consumes a copy.
    """
    state: ShopState
    cursor: int = 0                  # selected slot index
    flash: Optional[str] = None      # transient banner (e.g. 'BOUGHT', 'OOPS')
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


# ---------------------------------------------------------------------------
# Render — pure (no I/O, no curses)
# ---------------------------------------------------------------------------

def render_frame(view: ShopView, *, color: bool = True,
                 width: int = WIDTH) -> str:
    """Return the full shop frame as one '\\n'-joined string.

    ANSI escapes are emitted only when ``color=True``. Frame is exactly
    21 lines tall regardless of slot count (sub-6 slots are padded with
    blanks so the bottom panes don't dance around).
    """
    s = view.state

    # ----- HEADER ROW: balance / weekly / refresh ---------------------
    bal_color = BRIGHT_GREEN if (color and s.balance >= 800) else (
        BRIGHT_YELLOW if color and s.balance >= 300 else GRAY)
    weekly_color = (BRIGHT_RED if color and s.weekly_count >= s.weekly_cap
                    else GRAY)
    bal_field = colorize(f"{_format_balance(s.balance)} ¤",
                         bal_color, bold=True) if color else f"{s.balance} ¤"
    weekly_field = colorize(f"{s.weekly_count}/{s.weekly_cap}",
                            weekly_color, bold=True) if color else (
        f"{s.weekly_count}/{s.weekly_cap}")
    refresh_field = colorize(_format_secs_short(s.seconds_until_rotation),
                             DIM, bold=False) if color else (
        _format_secs_short(s.seconds_until_rotation))
    summary = (f"  balance  {bal_field}   ·   "
               f"weekly  {weekly_field}   ·   "
               f"refresh in  {refresh_field}")
    summary_line = frame_line(summary, width)

    # ----- SLOT GRID --------------------------------------------------
    slot_lines: list[str] = [section_title("TODAY'S 6 SLOTS", width),
                             blank(width)]
    if not s.slots:
        slot_lines.append(centered(
            "(no rotation — pool exhausted or art-pack empty)",
            width, dim=True, color=DIM if color else None))
        # Pad to 6-row equivalent so the frame stays the right height.
        while len(slot_lines) < 8:
            slot_lines.append(blank(width))
    else:
        for s_idx, slot in enumerate(s.slots):
            slot_lines.append(_render_slot_row(slot, selected=(s_idx == view.cursor),
                                                color=color, width=width))
        # Pad up to 6 rows so layout stays stable on partial pools.
        while len(slot_lines) < 8:
            slot_lines.append(blank(width))
    slot_lines.append(blank(width))

    # ----- DETAIL PANEL ------------------------------------------------
    detail_lines = _render_detail_panel(view, color=color, width=width)

    # ----- FLASH (optional) -------------------------------------------
    flash_lines: list[str] = []
    if view.flash:
        flash_color = view.flash_color or BRIGHT_YELLOW
        flash_lines.append(divider(width))
        flash_lines.append(centered(
            view.flash, width,
            color=flash_color if color else None, bold=True))

    # ----- BODY ASSEMBLY -----------------------------------------------
    body: list[str] = []
    body.append(summary_line)
    body.append(divider(width))
    body.extend(slot_lines)
    body.append(divider(width))
    body.extend(detail_lines)
    body.extend(flash_lines)

    # ----- STATUS BAR --------------------------------------------------
    status_left = (f"slot {view.cursor + 1}/{max(1, view.slot_count)}  ·  "
                   f"weekly {s.weekly_count}/{s.weekly_cap}")
    status_keys = "[←→]select  [⏎]buy  [r]refresh  [q]quit"

    # Compose: header (3 lines) + body + status_bar (3 lines).
    h = header("shop",
               identity=_ident_short(s.pubkey_hex, name=view.identity_name),
               width=width)
    sb = status_bar(status_left, status_keys, width=width)
    return "\n".join(h + body + sb)


def _render_slot_row(slot: RotationSlot, *, selected: bool,
                     color: bool, width: int) -> str:
    """One ║   ▶ [N] card_id  skin_name  axis  rarity  cost ║ row.

    Column widths are visible-width — ANSI escapes don't count toward
    padding, so the rows stay aligned regardless of how many color codes
    are inlined.
    """
    listing = slot.listing
    rar_col = rarity_color(listing.rarity) if color else None

    cur = cursor_prefix(selected, color=color)
    idx_field = f"[{slot.index}]"
    if slot.sold:
        ts = (slot.purchased_at or "")[11:16]
        card_text = listing.card_id
        skin_text = f"[OWNED {ts} UTC]" if ts else "[OWNED]"
        axis_text = listing.skin_axis
        rar_text = listing.rarity
        cost_text = "—"
        if color:
            idx_field = colorize(idx_field, GRAY, bold=False)
            card_text = colorize(card_text, GRAY, bold=False)
            skin_text = colorize(skin_text, GRAY, bold=True)
            axis_text = colorize(axis_text, GRAY, bold=False)
            rar_text = colorize(rar_text, GRAY, bold=False)
            cost_text = colorize(cost_text, GRAY, bold=False)
    else:
        card_text = listing.card_id
        skin_text = listing.skin_name
        axis_text = listing.skin_axis
        rar_text = listing.rarity
        cost_text = f"{slot.cost} ¤"
        if color:
            card_text = colorize(card_text, rar_col, bold=False)
            rar_text = colorize(rar_text, rar_col, bold=True)
            cost_text = colorize(cost_text, BRIGHT_YELLOW, bold=True)

    # Build with visible-width-aware padding (so ANSI escapes don't bleed
    # the layout). Total interior is WIDTH-2 = 78 cols; we leave a 2-col
    # right gutter so frame_line's pad doesn't make the row look cramped.
    # Layout: 2sp + cur(2) + idx(4) + 1 + card(14) + 2 + skin(20) + 2 +
    #         axis(10) + 2 + rar(10) + 2 + cost(5) = 76 (+2 gutter = 78)
    body = ("  " + cur
            + pad_visible(idx_field, 4) + " "
            + pad_visible(card_text, 14) + "  "
            + pad_visible(skin_text, 20) + "  "
            + pad_visible(axis_text, 10) + "  "
            + pad_visible(rar_text, 10) + "  "
            + pad_visible(cost_text, 5, align="right"))

    if selected and color:
        # Replace stand-alone RESETs inside the row with RESET+BG to keep the
        # selection bar continuous, then arm the background once and reset at end.
        body = (BG_GRAY + body.replace(RESET, RESET + BG_GRAY) + RESET)
    return frame_line(body, width)


def _render_detail_panel(view: ShopView, *, color: bool, width: int
                         ) -> list[str]:
    """4-line detail block describing the cursor's slot."""
    sl = view.selected
    if sl is None:
        return [
            section_title("DETAIL", width),
            frame_line("    (no slots in rotation)", width),
            blank(width),
            blank(width),
        ]
    listing = sl.listing
    rar = colorize(listing.rarity,
                   rarity_color(listing.rarity) if color else None, bold=color)
    sold_marker = ""
    if sl.sold:
        ts = sl.purchased_at or ""
        sold_marker = colorize(f"  [OWNED {ts}]", GRAY, bold=False) if color else (
            f"  [OWNED {ts}]")
    return [
        section_title(f"DETAIL — slot {sl.index}", width),
        frame_line(f"    card:    {listing.card_id}", width),
        frame_line(f"    skin:    {listing.skin_name}  ({listing.skin_slug})"
                   + sold_marker, width),
        frame_line(f"    axis:    {listing.skin_axis}  ·  "
                   f"rarity {rar}  ·  price {sl.cost} ¤", width),
        frame_line(f"    art:     {_short_path(listing.art_path)}", width),
    ]


def _short_path(p: str, max_len: int = 60) -> str:
    """Trim long art paths from the head with an ellipsis."""
    if len(p) <= max_len:
        return p
    return "…" + p[-(max_len - 1):]


# ---------------------------------------------------------------------------
# Interactive runner
# ---------------------------------------------------------------------------

@dataclass
class ShopRunner:
    """Event-loop driver for the shop TUI.

    Args:
      state_loader:  callable returning a fresh ShopState. Called on init,
                     after every purchase, and on R-key refresh.
      purchaser:     callable(slot_index) → ``(ok, message)`` tuple. The
                     runner does NOT import shop.purchase_slot directly so
                     the same loop is testable in isolation.
      sink:          stdout (default) or a StringIO for tests/screenshots.
      color:         False to strip ANSI (tests / pipes).
      keyboard:      False to disable keyboard input (tests / one-shot
                     screenshots).
      identity_name: optional friendly name for the header overlay.
    """
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

    # ----- public API -----

    def run(self) -> int:
        """Block until the user quits. Returns 0 on clean exit."""
        try:
            self._enter_screen()
            with keyboard_reader_or_dummy(self.keyboard) as kb:
                self._render(force=True)
                while not self._stop:
                    if kb is None:
                        # No keyboard — render once and exit (script use).
                        break
                    key = kb.poll(timeout_ms=100)
                    if key is None:
                        continue
                    self._handle_key(key)
                    self._render()
        finally:
            self._exit_screen()
        return 0

    def render_once(self) -> str:
        """Return the current frame as a string (no I/O). For screenshots."""
        return render_frame(self._view, color=self.color, width=self.width)

    # ----- key handling -----

    def _handle_key(self, key) -> None:
        if key in (Key.Q, Key.ESC):
            self._stop = True
            return
        if not self._view.state.slots:
            return
        if key in (Key.LEFT, Key.UP):
            self._view.cursor = (self._view.cursor - 1) % self._view.slot_count
            self._view.flash = None
        elif key in (Key.RIGHT, Key.DOWN):
            self._view.cursor = (self._view.cursor + 1) % self._view.slot_count
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
        # Reload either way; the message tells the story.
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
        screen = render_frame(self._view, color=self.color, width=self.width)
        sig = (self._view.cursor, self._view.state.balance,
               tuple((sl.sold, sl.purchased_at) for sl in self._view.state.slots),
               self._view.flash, self._view.state.weekly_count,
               self._view.state.seconds_until_rotation // 60)
        if not force and sig == self._last_signature:
            return
        self._last_signature = sig
        if self._is_tty():
            self.sink.write(HOME + screen + "\n")
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
    """Build a runner against the real shop subsystem and start the loop.

    Imports happen here (lazy) so the module doesn't touch the ledger /
    identity layers at import time — keeps unit tests fast.
    """
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
