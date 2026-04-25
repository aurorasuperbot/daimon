"""Interactive collection viewer — `daimon collection` (default mode).

Browse the cards your identity owns, sorted/filtered live, with a stat +
trigger detail panel for whatever the cursor is on.

Surface contract (mirrors shop_ui):

  * ``render_frame(view) -> str``       — pure renderer (agent-friendly)
  * ``CollectionRunner.run()``          — interactive event loop (humans)
  * ``--no-tui`` / ``--json`` on the CLI keep the scripted paths working

Layout (80 col × ~24 row):

    ╔══════════════════════════════════════════════════════════════════════════════╗
    ║  DAIMON · collection                                 santiago · 8a3c…f1e0   ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  total 31 serials · 18 unique · L:1 E:2 R:8 U:5 C:2     sort: rarity↓ filter:* ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  ▶ magma_tyrant         legendary   ×1   FIRE      atk 9  def 6  hp 38       ║
    ║    abyss_warden         epic        ×1   WATER     atk 7  def 9  hp 34       ║
    ║    arc_predator         epic        ×2   VOLT      atk 8  def 5  hp 28       ║
    ║    aegis_lion           rare        ×3   NORMAL    atk 6  def 8  hp 30       ║
    ║    blazewolf            rare        ×2   FIRE      atk 7  def 5  hp 24       ║
    ║    ...                                                                       ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  DETAIL — magma_tyrant (Magma Tyrant · legendary · FIRE)                     ║
    ║    stats   atk 9   def 6   hp 38   spd 4                                     ║
    ║    flavor  Born of an unburied volcano, his footsteps still steam.           ║
    ║    triggers                                                                  ║
    ║      · ON_BATTLE_START   →  BUFF_ATK  ALL_ALLIES  +2                         ║
    ║      · ON_KILL           →  HEAL      SELF        4                          ║
    ║      · ON_TURN_END       →  DAMAGE    ENEMY_LOWEST_HP  3                     ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  18 cards · sort r↓                  [↑↓]select [s]sort [f]filter [q]quit    ║
    ╚══════════════════════════════════════════════════════════════════════════════╝

Keys:

  ↑/↓        move cursor between cards
  PgUp/PgDn  jump 10 rows
  s          cycle sort (rarity → card_id → count → rarity)
  f          cycle rarity filter (* → legendary → epic → rare → uncommon → common → *)
  e          cycle element filter (* → fire → water → nature → volt → void → normal → *)
  ENTER      (no-op for now; reserved for inspecting all serials)
  Q / ESC    quit
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple

from daimon.play.hud.keyboard import Key, keyboard_reader_or_dummy
from daimon.play.tui_style import (
    BG_GRAY,
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
    WIDTH,
    blank,
    box_bottom,
    box_top,
    centered,
    colorize,
    cursor_prefix,
    divider,
    element_color,
    frame_line,
    header,
    pad_visible,
    rarity_color,
    section_title,
    split_row,
    status_bar,
)

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
# View model — what render_frame consumes
# ---------------------------------------------------------------------------

@dataclass
class OwnedCard:
    """One unique card_id + how many copies the player owns + payload."""
    card_id: str
    rarity: str
    count: int
    payload: Optional[Dict[str, Any]] = None    # catalog payload, if found

    # Derived (cached) fields used by the renderer
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
        """Legendary cards bind a global rule_change instead of triggers."""
        rc = (self.payload or {}).get("rule_change")
        return rc if rc else None


@dataclass
class CollectionView:
    """All state the collection TUI cares about at one render tick."""
    cards: List[OwnedCard]
    pubkey_hex: str = ""
    identity_name: Optional[str] = None
    cursor: int = 0
    sort_idx: int = 0                # -> SORT_OPTIONS
    sort_descending: bool = True
    rarity_filter_idx: int = 0       # -> RARITY_FILTERS
    element_filter_idx: int = 0      # -> ELEMENT_FILTERS

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
        if not self.sort_descending and self.sort == "rarity":
            cs = list(reversed(cs))
        return cs

    @property
    def total_serials(self) -> int:
        cs = self.visible_cards()
        return sum(c.count for c in cs)

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


# ---------------------------------------------------------------------------
# Render — pure
# ---------------------------------------------------------------------------

LIST_ROWS = 9          # rows reserved for the card list (so layout stable)
DETAIL_ROWS = 7        # rows reserved for the detail panel


def render_frame(view: CollectionView, *, color: bool = True,
                 width: int = WIDTH) -> str:
    """Build the full collection frame as one string."""
    visible = view.visible_cards()

    # Clamp cursor.
    if not visible:
        view.cursor = 0
    else:
        view.cursor = max(0, min(view.cursor, len(visible) - 1))

    # ----- SUMMARY HEADER ---------------------------------------------
    total = view.total_serials
    unique = len(visible)
    sort_label = _sort_label(view.sort)
    filt_label = _filter_label(view.rarity_filter, view.element_filter)
    summary = (
        f"  total {total} · {unique} unique · {view.rarity_summary}"
    )
    summary_right = f"sort: {sort_label}  filter: {filt_label}  "

    # ----- LIST SECTION ------------------------------------------------
    list_lines: list[str] = []
    if not visible:
        list_lines.append(blank(width))
        list_lines.append(centered(
            "(no cards match — clear filters or run `daimon pull` to mint your first)",
            width, dim=True, color=DIM if color else None))
        while len(list_lines) < LIST_ROWS:
            list_lines.append(blank(width))
    else:
        # Pagination: keep the cursor visible by sliding a window.
        start = max(0, min(len(visible) - LIST_ROWS, view.cursor - LIST_ROWS // 2))
        for offset, c in enumerate(visible[start:start + LIST_ROWS]):
            global_idx = start + offset
            list_lines.append(_render_card_row(
                c, selected=(global_idx == view.cursor),
                color=color, width=width))
        # Pad to LIST_ROWS so frame height stays constant.
        while len(list_lines) < LIST_ROWS:
            list_lines.append(blank(width))

    # ----- DETAIL PANEL ------------------------------------------------
    selected = visible[view.cursor] if visible else None
    detail_lines = _render_detail_panel(selected, color=color, width=width)
    while len(detail_lines) < DETAIL_ROWS:
        detail_lines.append(blank(width))

    # ----- BODY ASSEMBLY -----------------------------------------------
    body: list[str] = []
    body.append(split_row(summary, summary_right, width))
    body.append(divider(width))
    body.extend(list_lines)
    body.append(divider(width))
    body.extend(detail_lines[:DETAIL_ROWS])

    # ----- STATUS BAR --------------------------------------------------
    status_left = (
        f"{unique} cards  ·  sort {sort_label}"
        + (f"  ·  showing {view.cursor + 1}/{unique}" if visible else "")
    )
    status_keys = "[↑↓]select  [s]sort  [f]rarity  [e]elem  [q]quit"

    # Compose
    h = header("collection",
               identity=_ident(view.pubkey_hex, view.identity_name),
               width=width)
    sb = status_bar(status_left, status_keys, width=width)
    return "\n".join(h + body + sb)


def _render_card_row(c: OwnedCard, *, selected: bool,
                     color: bool, width: int) -> str:
    """One ║ ▶ card_id   rarity   ×N   ELEMENT   atk/def/hp ║ row."""
    rar_col = rarity_color(c.rarity) if color else None
    elem_col = element_color(c.element) if color else None

    cur = cursor_prefix(selected, color=color)
    name_text = c.card_id
    rar_text = c.rarity
    count_text = f"×{c.count}"
    elem_text = c.element
    stat_text = (f"atk {c.atk:>2}  def {c.def_:>2}  "
                 f"hp {c.hp:>2}  spd {c.spd:>1}")

    if color:
        name_text = colorize(name_text, rar_col)
        rar_text = colorize(rar_text, rar_col, bold=True)
        elem_text = colorize(elem_text, elem_col, bold=True)
        count_text = colorize(count_text, BRIGHT_GREEN, bold=False) if c.count > 1 else (
            colorize(count_text, GRAY, bold=False))

    # Layout: 2sp + cur(2) + card(20) + 2 + rar(11) + 2 + count(4) +
    #         2 + elem(8) + 2 + stat(23) = 76 (+2 gutter = 78)
    body = ("  " + cur
            + pad_visible(name_text, 20) + "  "
            + pad_visible(rar_text, 11) + "  "
            + pad_visible(count_text, 4) + "  "
            + pad_visible(elem_text, 8) + "  "
            + pad_visible(stat_text, 23))

    if selected and color:
        body = (BG_GRAY + body.replace(RESET, RESET + BG_GRAY) + RESET)
    return frame_line(body, width)


def _render_detail_panel(c: Optional[OwnedCard], *, color: bool, width: int
                         ) -> list[str]:
    """Stat block + flavor + triggers for the cursor's card."""
    if c is None:
        return [
            section_title("DETAIL", width),
            frame_line("    (collection empty)", width),
        ]
    rar_col = rarity_color(c.rarity) if color else None
    elem_col = element_color(c.element) if color else None
    title = (f"DETAIL — {c.card_id}  "
             f"({c.name} · {colorize(c.rarity, rar_col, bold=True) if color else c.rarity}"
             f" · {colorize(c.element, elem_col, bold=True) if color else c.element})")
    lines = [
        section_title(title, width),
        frame_line(
            f"    stats   atk {c.atk}   def {c.def_}   hp {c.hp}   spd {c.spd}",
            width),
    ]
    if c.flavor:
        flavor = c.flavor.strip()
        if len(flavor) > 64:
            flavor = flavor[:63] + "…"
        lines.append(frame_line(
            colorize(f"    flavor  {flavor}", DIM, bold=False) if color else (
                f"    flavor  {flavor}"),
            width))
    triggers = c.triggers
    if c.rule_change:
        rc_text = colorize(c.rule_change, BRIGHT_YELLOW, bold=True) if color else (
            c.rule_change)
        lines.append(frame_line(
            f"    rule    {rc_text}  (legendary global rule_change)", width))
    if not triggers:
        if not c.rule_change:
            lines.append(frame_line("    triggers  (none)", width))
        return lines
    lines.append(frame_line("    triggers", width))
    for t in triggers[:3]:    # cap to 3 so we keep the row count stable
        when = t.get("when", "?")
        op = t.get("op", "?")
        target = t.get("target", "?")
        value = t.get("value", "")
        line = (f"      · {when:<18} →  {op:<10} {target:<14}  "
                f"{value if value != '' else ''}")
        lines.append(frame_line(line, width))
    return lines


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
# Loader — assembles OwnedCard list from collection.json + catalog
# ---------------------------------------------------------------------------

def build_owned_cards(serials: List[Dict[str, Any]],
                      catalog_payloads: Dict[str, Dict[str, Any]]
                      ) -> List[OwnedCard]:
    """Group raw serials by card_id and attach the matching catalog payload.

    Pure: no file I/O. The CLI/MCP wrappers feed `serials` from
    ``collection.list_serials()`` and `catalog_payloads` from the loaded
    catalog's ``by_id`` dict.
    """
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
    """Event loop driver for the collection TUI."""
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

    def render_once(self) -> str:
        return render_frame(self._view, color=self.color, width=self.width)

    # ----- key handling -----

    def _handle_key(self, key) -> None:
        if key in (Key.Q, Key.ESC):
            self._stop = True
            return
        n = len(self._view.visible_cards())
        if n == 0:
            # Filters can be cleared even with no visible cards.
            if key == "f":
                self._view.rarity_filter_idx = (
                    self._view.rarity_filter_idx + 1) % len(RARITY_FILTERS)
            elif key == "e":
                self._view.element_filter_idx = (
                    self._view.element_filter_idx + 1) % len(ELEMENT_FILTERS)
            return
        if key in (Key.UP, Key.LEFT):
            self._view.cursor = (self._view.cursor - 1) % n
        elif key in (Key.DOWN, Key.RIGHT):
            self._view.cursor = (self._view.cursor + 1) % n
        elif key == "s":
            self._view.sort_idx = (self._view.sort_idx + 1) % len(SORT_OPTIONS)
            self._view.cursor = 0
        elif key == "f":
            self._view.rarity_filter_idx = (
                self._view.rarity_filter_idx + 1) % len(RARITY_FILTERS)
            self._view.cursor = 0
        elif key == "e":
            self._view.element_filter_idx = (
                self._view.element_filter_idx + 1) % len(ELEMENT_FILTERS)
            self._view.cursor = 0

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
        sig = (self._view.cursor, self._view.sort_idx,
               self._view.rarity_filter_idx, self._view.element_filter_idx,
               len(self._view.cards))
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
# CLI entry — launches against the real collection + catalog.
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
