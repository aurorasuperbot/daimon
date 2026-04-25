"""Interactive collection viewer — `daimon collection` (default mode), V2 grid.

Browse owned cards as a paged grid of card-art TILES with a side detail panel
showing the focused card's stats, flavor, and triggers.

Surface contract (mirrors shop_ui):

  * ``render_frame(view) -> (frame_str, overlays)`` — pure renderer; always
    emits OVERLAY_ONLY (blank cells + ImageOverlay records the live runner
    KGP-paints, the screenshot pipeline pastes PIL bitmaps over).
  * ``CollectionRunner.run()`` — interactive event loop (humans). Bails
    with a clean error if the TTY isn't the bundled WezTerm.
  * ``--no-tui`` / ``--json`` on the CLI keep the scripted paths working

Layout (110 col × ~28 row), 4×2 tile grid + sticky detail panel:

    ╔══════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
    ║  DAIMON · collection                                                            santiago · 8a3c…f1e0         ║
    ╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
    ║  18 unique  ·  31 serials  ·  L:1 E:2 R:8 U:5 C:2     sort: rarity↓  filter: *                               ║
    ╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
    ║   [TILE]  [TILE]  [TILE]  [TILE]    │  DETAIL                                                                ║
    ║   [TILE]  [TILE]  [TILE]  [TILE]    │  hero art / stats / triggers                                           ║
    ╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
    ║  card 3/18  ·  page 1/3       [↑↓←→]select [s]sort [f]rarity [e]elem [PgUp/Dn] [q]quit                       ║
    ╚══════════════════════════════════════════════════════════════════════════════════════════════════════════════╝

Keys:

  ↑/↓/←/→     move cursor in the tile grid
  PgUp/PgDn   jump one page (8 cards)
  s           cycle sort (rarity → card_id → count → rarity)
  f           cycle rarity filter
  e           cycle element filter
  Q / ESC     quit
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple

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
    BRIGHT_YELLOW,
    CLEAR_SCREEN,
    CURSOR_HIDE,
    CURSOR_SHOW,
    DIM,
    GRAY,
    HOME,
    RESET,
    centered,
    colorize,
    divider,
    element_color,
    frame_line,
    header,
    pad_visible,
    rarity_color,
    split_row,
    status_bar,
    visible_len,
)


# ---------------------------------------------------------------------------
# Hard-require error message — surfaced when a TTY caller bypassed the
# launcher's auto-relaunch into the bundled WezTerm. DAIMON ships its own
# terminal; the half-block fallback was retired in Phase E.
# ---------------------------------------------------------------------------

_TERMINAL_REQUIRED_ERROR = (
    "error: DAIMON's interactive TUIs require the bundled WezTerm to render card art.\n"
    "  • Run `daimon install` to install the terminal (idempotent — safe to re-run).\n"
    "  • Or drop --in-place so DAIMON auto-launches in WezTerm for you.\n"
)


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

WIDTH = 110
GRID_COLS = 4
GRID_ROWS = 2
PAGE_SIZE = GRID_COLS * GRID_ROWS    # 8 cards per page
TILE_W = 18                          # tighter tiles than shop (more cards visible)
TILE_ART_H = 10
TILE_GAP = 1
ROW_GAP = 1
LEFT_PAD = 2
SEP_W = 3
DETAIL_W = WIDTH - 2 - LEFT_PAD - (GRID_COLS * TILE_W + (GRID_COLS - 1) * TILE_GAP) - SEP_W
# DETAIL_W = 110-2 - 2 - (4*18 + 3*1) - 3 = 108-2-75-3 = 28


# ---------------------------------------------------------------------------
# Sort + filter state
# ---------------------------------------------------------------------------

SORT_OPTIONS: List[str] = ["rarity", "card_id", "count"]
RARITY_FILTERS: List[Optional[str]] = [
    None, "legendary", "epic", "rare", "uncommon", "common"
]
ELEMENT_FILTERS: List[Optional[str]] = [
    None, "FIRE", "WATER", "NATURE", "VOLT", "VOID", "NORMAL"
]

_RARITY_ORDER = ("common", "uncommon", "rare", "epic", "legendary")


def _rarity_rank(r: str) -> int:
    try:
        return _RARITY_ORDER.index(r)
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# View model
# ---------------------------------------------------------------------------

@dataclass
class OwnedCard:
    """One unique card_id + how many copies + payload."""
    card_id: str
    rarity: str
    count: int
    payload: Optional[Dict[str, Any]] = None

    @property
    def species(self) -> str:
        return (self.payload or {}).get("species") or self.card_id

    @property
    def element(self) -> str:
        return (self.payload or {}).get("element") or "NORMAL"

    @property
    def name(self) -> str:
        return (self.payload or {}).get("name") or self.card_id

    @property
    def atk(self) -> int:
        return int((self.payload or {}).get("atk", 0))

    @property
    def def_(self) -> int:
        return int((self.payload or {}).get("def", 0))

    @property
    def hp(self) -> int:
        return int((self.payload or {}).get("hp", 0))

    @property
    def spd(self) -> int:
        return int((self.payload or {}).get("spd", 0))

    @property
    def flavor(self) -> str:
        return (self.payload or {}).get("flavor", "")

    @property
    def triggers(self) -> List[Dict[str, Any]]:
        return list((self.payload or {}).get("triggers", []) or [])

    @property
    def rule_change(self) -> Optional[str]:
        rc = (self.payload or {}).get("rule_change")
        return rc if rc else None


@dataclass
class CollectionView:
    """All TUI state at one render tick."""
    cards: List[OwnedCard]
    pubkey_hex: str = ""
    identity_name: Optional[str] = None
    cursor: int = 0                   # absolute index into visible list
    sort_idx: int = 0
    sort_descending: bool = True
    rarity_filter_idx: int = 0
    element_filter_idx: int = 0

    @property
    def sort(self) -> str:
        return SORT_OPTIONS[self.sort_idx]

    @property
    def rarity_filter(self) -> Optional[str]:
        return RARITY_FILTERS[self.rarity_filter_idx]

    @property
    def element_filter(self) -> Optional[str]:
        return ELEMENT_FILTERS[self.element_filter_idx]

    def visible_cards(self) -> List[OwnedCard]:
        cs = self.cards
        if self.rarity_filter:
            cs = [c for c in cs if c.rarity == self.rarity_filter]
        if self.element_filter:
            cs = [c for c in cs if c.element.upper() == self.element_filter]
        if self.sort == "rarity":
            cs = sorted(cs, key=lambda c: (-_rarity_rank(c.rarity), c.card_id))
        elif self.sort == "card_id":
            cs = sorted(cs, key=lambda c: c.card_id)
        elif self.sort == "count":
            cs = sorted(cs, key=lambda c: (-c.count, c.card_id))
        return cs

    @property
    def total_serials(self) -> int:
        return sum(c.count for c in self.visible_cards())

    @property
    def rarity_summary(self) -> str:
        counts: Dict[str, int] = {}
        for c in self.visible_cards():
            counts[c.rarity] = counts.get(c.rarity, 0) + c.count
        order = ["legendary", "epic", "rare", "uncommon", "common"]
        glyphs = {"legendary": "L", "epic": "E", "rare": "R",
                  "uncommon": "U", "common": "C"}
        parts = []
        for r in order:
            if counts.get(r, 0):
                parts.append(f"{glyphs[r]}:{counts[r]}")
        return "  ".join(parts) if parts else "—"

    def page_for(self, idx: int) -> int:
        return idx // PAGE_SIZE

    @property
    def page(self) -> int:
        return self.page_for(self.cursor)

    @property
    def page_count(self) -> int:
        n = len(self.visible_cards())
        if n == 0:
            return 1
        return (n + PAGE_SIZE - 1) // PAGE_SIZE


# ---------------------------------------------------------------------------
# Render — pure
# ---------------------------------------------------------------------------

def render_frame(view: CollectionView, *,
                 mode: RenderMode = RenderMode.OVERLAY_ONLY,
                 color: bool = True,
                 width: int = WIDTH
                 ) -> Tuple[str, List[ImageOverlay]]:
    """Return ``(frame_string, overlays)`` for one render tick.

    Always emits OVERLAY_ONLY (blank art cells + absolute-coord overlays).
    The ``mode`` parameter is preserved for backward compat with the
    screenshot harness; only ``OVERLAY_ONLY`` is supported now.
    """
    visible = view.visible_cards()

    # Clamp cursor.
    if not visible:
        view.cursor = 0
    else:
        view.cursor = max(0, min(view.cursor, len(visible) - 1))

    # ----- HEADER ------------------------------------------------------
    header_lines = header(
        "collection",
        identity=_ident(view.pubkey_hex, view.identity_name),
        width=width,
    )

    # ----- SUMMARY -----------------------------------------------------
    total = view.total_serials
    unique = len(visible)
    sort_label = _sort_label(view.sort)
    filt_label = _filter_label(view.rarity_filter, view.element_filter)
    summary_left = f"  {unique} unique  ·  {total} serials  ·  {view.rarity_summary}"
    summary_right = f"sort: {sort_label}  filter: {filt_label}  "
    summary_line = split_row(summary_left, summary_right, width)

    # ----- TILE GRID + DETAIL PANEL ------------------------------------
    body_lines, body_overlays = _render_grid_and_detail(
        view, visible, mode=mode, color=color, width=width,
        body_top_row=len(header_lines) + 2,
    )

    # ----- STATUS BAR --------------------------------------------------
    if visible:
        status_left = (f"card {view.cursor + 1}/{unique}  ·  "
                       f"page {view.page + 1}/{view.page_count}")
    else:
        status_left = "0 cards"
    status_keys = "[↑↓←→]select  [s]sort  [f]rarity  [e]elem  [PgUp/Dn]page  [q]quit"
    sb = status_bar(status_left, status_keys, width=width)

    body: list[str] = [summary_line, divider(width)]
    body.extend(body_lines)
    return "\n".join(header_lines + body + sb), body_overlays


def _render_grid_and_detail(view: CollectionView,
                            visible: List[OwnedCard],
                            *, mode: RenderMode, color: bool, width: int,
                            body_top_row: int
                            ) -> Tuple[List[str], List[ImageOverlay]]:
    """Compose the 4×2 tile grid + side detail panel for one page."""
    overlays: List[ImageOverlay] = []
    page = view.page
    start = page * PAGE_SIZE
    page_cards = visible[start:start + PAGE_SIZE]

    grid_rows: List[List[Tile]] = []
    for ri in range(GRID_ROWS):
        row_tiles: List[Tile] = []
        for ci in range(GRID_COLS):
            local_idx = ri * GRID_COLS + ci
            global_idx = start + local_idx
            card = page_cards[local_idx] if local_idx < len(page_cards) else None
            row_tiles.append(_card_to_tile(card, global_idx, view.cursor,
                                            mode=mode, color=color))
        grid_rows.append(row_tiles)

    composed_grid_lines: List[str] = []
    for ri, row_tiles in enumerate(grid_rows):
        composed = compose_row(row_tiles, gap=TILE_GAP, left_pad=LEFT_PAD)
        abs_row_top = body_top_row + len(composed_grid_lines)
        overlays.extend(overlays_for_row(composed, base_row=abs_row_top, base_col=1))
        composed_grid_lines.extend(composed.lines)
        if ri < GRID_ROWS - 1:
            composed_grid_lines.append(" " * composed.width)

    # Detail panel on the right.
    selected_card = visible[view.cursor] if visible else None
    detail_lines, detail_overlay_specs = _render_detail_panel(
        selected_card, mode=mode, color=color, width=DETAIL_W,
    )

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

    detail_col_offset = 1 + grid_w + SEP_W
    for spec in detail_overlay_specs:
        overlays.append(ImageOverlay(
            row=body_top_row + spec.row,
            col=detail_col_offset + spec.col,
            rows=spec.rows,
            cols=spec.cols,
            image_path=spec.image_path,
            border_color=spec.border_color,
            border_width=spec.border_width,
            glow=spec.glow,
            caption=spec.caption,
            caption_color=spec.caption_color,
        ))

    lines: List[str] = []
    for li in range(total_h):
        gline = composed_grid_lines[li]
        dline = detail_lines[li]
        gpad = grid_w - visible_len(gline)
        if gpad > 0:
            gline = gline + " " * gpad
        body_line = gline + sep + pad_visible(dline, DETAIL_W)
        lines.append(frame_line(body_line, width))
    return lines, overlays


def _card_to_tile(card: Optional[OwnedCard], idx: int, cursor: int, *,
                  mode: RenderMode, color: bool) -> Tile:
    """Render one collection card as a Tile."""
    selected = (idx == cursor)
    if card is None:
        return render_tile(
            card_id="",
            width=TILE_W,
            art_h=TILE_ART_H,
            caption_lines=("(empty)", ""),
            ghost=True,
            mode=mode,
            color=color,
        )

    rar_col = rarity_color(card.rarity) if color else None
    elem_col = element_color(card.element) if color else None

    name_text = card.card_id
    if color:
        name_text = colorize(name_text, rar_col, bold=selected)
    count_text = f"×{card.count}" if card.count > 1 else " "
    if color and card.count > 1:
        count_text = colorize(count_text, BRIGHT_GREEN, bold=True)

    cap1 = pad_visible(name_text, TILE_W - 2 - 4) + pad_visible(count_text, 4, align="right")

    rar_text = card.rarity[:6]
    elem_text = card.element[:6]
    if color:
        rar_text = colorize(rar_text, rar_col, bold=False)
        elem_text = colorize(elem_text, elem_col, bold=True)
    cap2 = pad_visible(rar_text, 9) + pad_visible(elem_text, TILE_W - 2 - 9, align="right")

    border = None
    if mode == RenderMode.OVERLAY_ONLY:
        if selected:
            border = (130, 220, 240)
        else:
            border = (80, 80, 100)

    return render_tile(
        card_id=card.card_id,
        width=TILE_W,
        art_h=TILE_ART_H,
        caption_lines=(cap1, cap2),
        selected=selected,
        mode=mode,
        border_color_rgb=border,
        color=color,
    )


def _render_detail_panel(card: Optional[OwnedCard], *,
                         mode: RenderMode, color: bool, width: int
                         ) -> Tuple[List[str], List[ImageOverlay]]:
    """Right-side detail panel for the focused card."""
    if card is None:
        return ([
            "DETAIL",
            "(no cards match)",
        ], [])

    rar_col = rarity_color(card.rarity) if color else None
    elem_col = element_color(card.element) if color else None

    title = "DETAIL — " + card.card_id
    if color:
        title = BOLD + title + RESET

    HERO_ART_H = 12
    hero = render_tile(
        card_id=card.card_id,
        width=width,
        art_h=HERO_ART_H,
        caption_lines=(),
        selected=True,
        mode=mode,
        border_color_rgb=(130, 220, 240) if mode == RenderMode.OVERLAY_ONLY else None,
        color=color,
    )

    rar_text = colorize(card.rarity, rar_col, bold=color) if color else card.rarity
    elem_text = colorize(card.element, elem_col, bold=color) if color else card.element

    rows: List[str] = []
    rows.append(pad_visible(title, width))
    rows.extend(hero.lines)
    rows.append(pad_visible(f"{card.name}", width))
    rows.append(pad_visible(f"{rar_text} · {elem_text}  ×{card.count}", width))
    rows.append(pad_visible(
        f"atk {card.atk}  def {card.def_}  hp {card.hp}  spd {card.spd}",
        width))

    if card.flavor:
        flavor = card.flavor.strip()
        # Word-wrap the flavor across remaining rows so it doesn't blow past
        # the panel width.
        for w in _wrap_lines(flavor, width):
            rows.append(pad_visible(
                colorize(w, DIM) if color else w, width))

    if card.rule_change:
        rc = card.rule_change
        if color:
            rc = colorize(rc, BRIGHT_YELLOW, bold=True)
        rows.append(pad_visible("rule: " + rc, width))

    triggers = card.triggers
    if triggers:
        rows.append(pad_visible("triggers:", width))
        for t in triggers[:4]:
            when = t.get("when", "?")
            op = t.get("op", "?")
            target = t.get("target", "")
            value = t.get("value", "")
            line = f"  {when}"
            line2 = f"    →{op}"
            if target:
                line2 += f" {target}"
            if value not in ("", None):
                line2 += f" {value}"
            rows.append(pad_visible(line, width))
            rows.append(pad_visible(
                colorize(line2, GRAY) if color else line2, width))

    overlays: List[ImageOverlay] = []
    if hero.local_overlay is not None:
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
        ))
    return rows, overlays


def _wrap_lines(text: str, width: int) -> List[str]:
    """Crude word-wrap. Splits on spaces; keeps lines <= ``width`` cells."""
    out: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for w in text.split():
        if cur_len + (1 if cur else 0) + len(w) > width:
            if cur:
                out.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
        else:
            cur.append(w)
            cur_len += (1 if cur_len else 0) + len(w)
    if cur:
        out.append(" ".join(cur))
    return out


def _sort_label(s: str) -> str:
    return {"rarity": "rarity↓", "card_id": "name↑", "count": "count↓"}.get(s, s)


def _filter_label(rar: Optional[str], elem: Optional[str]) -> str:
    if rar is None and elem is None:
        return "*"
    parts = []
    if rar:
        parts.append(rar)
    if elem:
        parts.append(elem.lower())
    return "/".join(parts)


def _ident(pubkey_hex: str, name: Optional[str]) -> str:
    short = f"{pubkey_hex[:4]}…{pubkey_hex[-4:]}" if pubkey_hex else "anon"
    return f"{name} · {short}" if name else short


# ---------------------------------------------------------------------------
# Loader (unchanged from V1)
# ---------------------------------------------------------------------------

def build_owned_cards(serials: List[Dict[str, Any]],
                      catalog_payloads: Dict[str, Dict[str, Any]]
                      ) -> List[OwnedCard]:
    """Group raw serials by card_id and attach the matching catalog payload."""
    by_id: Dict[str, List[Dict[str, Any]]] = {}
    for s in serials:
        cid = s.get("card_id", "?")
        by_id.setdefault(cid, []).append(s)
    out: List[OwnedCard] = []
    for cid, rows in by_id.items():
        rarity = rows[0].get("rarity", "?")
        payload = catalog_payloads.get(cid)
        out.append(OwnedCard(card_id=cid, rarity=rarity,
                             count=len(rows), payload=payload))
    return out


# ---------------------------------------------------------------------------
# Interactive runner
# ---------------------------------------------------------------------------

@dataclass
class CollectionRunner:
    """Event-loop driver for the collection TUI."""
    loader: Callable[[], CollectionView]
    sink: TextIO = field(default_factory=lambda: sys.stdout)
    color: bool = True
    keyboard: bool = True
    width: int = WIDTH

    _view: CollectionView = field(init=False)
    _stop: bool = field(default=False, init=False)
    _last_signature: Optional[tuple] = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._view = self.loader()

    def run(self) -> int:
        if self._is_tty() and not terminal_supports_kgp():
            sys.stderr.write(_TERMINAL_REQUIRED_ERROR)
            sys.stderr.flush()
            return 2
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

    def render_once(self, *, mode: RenderMode = RenderMode.OVERLAY_ONLY
                    ) -> Tuple[str, List[ImageOverlay]]:
        return render_frame(self._view, mode=mode,
                            color=self.color, width=self.width)

    # ----- key handling -----

    def _handle_key(self, key) -> None:
        if key in (Key.Q, Key.ESC):
            self._stop = True
            return
        v = self._view
        n = len(v.visible_cards())
        if n == 0:
            if key == "f":
                v.rarity_filter_idx = (v.rarity_filter_idx + 1) % len(RARITY_FILTERS)
            elif key == "e":
                v.element_filter_idx = (v.element_filter_idx + 1) % len(ELEMENT_FILTERS)
            return
        if key == Key.UP:
            self._move_grid(-GRID_COLS, n)
        elif key == Key.DOWN:
            self._move_grid(+GRID_COLS, n)
        elif key == Key.LEFT:
            self._move_grid(-1, n)
        elif key == Key.RIGHT:
            self._move_grid(+1, n)
        elif key in ("p", Key.P):
            self._move_grid(-PAGE_SIZE, n)
        elif key in ("n", Key.N):
            self._move_grid(+PAGE_SIZE, n)
        elif key == "s":
            v.sort_idx = (v.sort_idx + 1) % len(SORT_OPTIONS)
            v.cursor = 0
        elif key == "f":
            v.rarity_filter_idx = (v.rarity_filter_idx + 1) % len(RARITY_FILTERS)
            v.cursor = 0
        elif key == "e":
            v.element_filter_idx = (v.element_filter_idx + 1) % len(ELEMENT_FILTERS)
            v.cursor = 0

    def _move_grid(self, delta: int, n: int) -> None:
        """Wrap-around navigation in the visible list."""
        self._view.cursor = (self._view.cursor + delta) % n

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
        # Always emit OVERLAY_ONLY: the text frame leaves blank cells for
        # each tile's art region; on a TTY we KGP-paint the real bitmap on
        # top via paint_overlays_as_kgp. Non-TTY sinks (pipes, screenshot
        # harness) get the text frame only — overlays surface via the
        # public render_once API for downstream pipelines.
        # The startup check in run() guarantees the TTY supports KGP.
        screen, overlays = render_frame(self._view,
                                        color=self.color, width=self.width)
        sig = (self._view.cursor, self._view.sort_idx,
               self._view.rarity_filter_idx, self._view.element_filter_idx,
               len(self._view.cards))
        if not force and sig == self._last_signature:
            return
        self._last_signature = sig
        if self._is_tty():
            kgp_paint = paint_overlays_as_kgp(overlays)
            self.sink.write(HOME + screen + kgp_paint + "\n")
        else:
            self.sink.write(screen + "\n")
        try:
            self.sink.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def run_collection_tui(*, identity_name: Optional[str] = None,
                       sink: Optional[TextIO] = None,
                       color: bool = True,
                       keyboard: bool = True) -> int:
    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
    from daimon.collection import list_serials, load_collection

    def _load() -> CollectionView:
        doc = load_collection()
        serials = doc.get("serials", [])
        try:
            cat = load_catalog(DEFAULT_CATALOG_ID)
            payloads = {cid: dict(cc.payload) for cid, cc in cat.by_id.items()}
        except Exception:    # noqa: BLE001 — catalog optional for collection view
            payloads = {}
        cards = build_owned_cards(serials, payloads)
        return CollectionView(
            cards=cards,
            pubkey_hex=str(doc.get("pubkey_hex") or ""),
            identity_name=identity_name,
        )

    runner = CollectionRunner(
        loader=_load,
        sink=sink or sys.stdout,
        color=color,
        keyboard=keyboard,
    )
    return runner.run()
