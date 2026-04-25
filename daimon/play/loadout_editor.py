"""Interactive loadout editor — `daimon loadout edit <name>`, V2 image grid.

Split-pane deck builder: card-art TILE catalog on the left, the player's 6
loadout slots as image tiles on the right. Live validation against
engine.Loadout rules (TEAM_SIZE=6, no duplicate card_id, ≤2 same-species).
Save with `s`, quit with `q`.

Surface contract (mirrors shop_ui / collection_ui):

  * ``render_frame(view) -> (frame_str, overlays)`` — pure renderer; always
    emits OVERLAY_ONLY (blank cells + ImageOverlay records the live runner
    KGP-paints, the screenshot pipeline pastes PIL bitmaps over).
  * ``LoadoutEditorRunner.run()`` — interactive event loop. Bails with a
    clean error if the TTY isn't the bundled WezTerm.
  * Saved file is ``{"name", "cards"}`` JSON, fungible across the CLI surface

Layout (130 col × ~30 row):

    ╔═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
    ║  DAIMON · loadout edit  my_team                                                              santiago · 8a3c…1c2d  · DIRTY  ║
    ╠═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
    ║   CATALOG ◀ FOCUS                                                          │  LOADOUT  (4/6)                                ║
    ║   [TILE]  [TILE]  [TILE]  [TILE]  [TILE]  [TILE]                           │  [SLOT 0] [SLOT 1] [SLOT 2]                    ║
    ║   [TILE]  [TILE]  [TILE]  [TILE]  [TILE]  [TILE]                           │  [SLOT 3] [SLOT 4] [SLOT 5]                    ║
    ╠═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
    ║  validation: NEED 2 MORE CARDS                                                                                              ║
    ║  cursor:    aegis_lion · rare · NORMAL · atk 6 def 8 hp 30                                                                  ║
    ╠═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
    ║  4/6 cards    [↑↓←→]select [⏎/+]add [TAB]focus [-]drop [s]save [q]quit                                                      ║
    ╚═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝

Keys:

  ↑/↓/←/→         move cursor in focused pane's grid
  TAB             swap focus between CATALOG and LOADOUT
  ENTER / +       add catalog card to first empty slot (or replace cursor's
                  loadout slot if loadout is focused)
  -               clear cursor's loadout slot (loadout pane only)
  PgUp/PgDn       page through the catalog
  s               save and exit (validation must be OK)
  Q / ESC         quit (no save-prompt yet — V1.1 work)
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple

from daimon.engine.loadout import MAX_SAME_SPECIES, validate_loadout
from daimon.engine.types import TEAM_SIZE
from daimon.play.art_render import RenderMode, paint_overlays_as_kgp
from daimon.render.wezterm_bundle import terminal_supports_kgp
from daimon.play.card_tile import CardTileInfo, tile_info_from_catalog_payload
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
# Layout
# ---------------------------------------------------------------------------

WIDTH = 145
CAT_GRID_COLS = 6
CAT_GRID_ROWS = 2
LO_GRID_COLS = 3
LO_GRID_ROWS = 2
TILE_W = 14
TILE_ART_H = 7
TILE_GAP = 1
ROW_GAP = 1
LEFT_PAD = 2
SEP_W = 3

# Catalog pane width (left)
CAT_W = LEFT_PAD + CAT_GRID_COLS * TILE_W + (CAT_GRID_COLS - 1) * TILE_GAP
# Loadout pane width (right)
LO_W = LEFT_PAD + LO_GRID_COLS * TILE_W + (LO_GRID_COLS - 1) * TILE_GAP
# Spare cells in the body interior (after CAT + sep + LO)
INTERIOR = WIDTH - 2
# Validate at import — don't ship a misaligned frame.
assert CAT_W + SEP_W + LO_W <= INTERIOR, (
    f"loadout editor frame oversized: {CAT_W} + {SEP_W} + {LO_W} > {INTERIOR}"
)


CATALOG_PAGE = CAT_GRID_COLS * CAT_GRID_ROWS    # 12 cards per page


# ---------------------------------------------------------------------------
# Pane focus
# ---------------------------------------------------------------------------

class Pane(str, Enum):
    CATALOG = "catalog"
    LOADOUT = "loadout"


# ---------------------------------------------------------------------------
# View model
# ---------------------------------------------------------------------------

@dataclass
class CatalogEntry:
    """One pickable card in the left pane."""
    card_id: str
    rarity: str
    element: str
    species: str
    payload: Dict[str, Any]

    @property
    def name(self) -> str:
        return self.payload.get("name", self.card_id)

    @property
    def atk(self) -> int:
        return int(self.payload.get("atk", 0))

    @property
    def def_(self) -> int:
        return int(self.payload.get("def", 0))

    @property
    def hp(self) -> int:
        return int(self.payload.get("hp", 0))

    @property
    def spd(self) -> int:
        return int(self.payload.get("spd", 0))

    def to_tile_info(self, *, position: int = 0) -> CardTileInfo:
        """Build a CardTileInfo for the composited-tile overlay path (Phase F)."""
        return tile_info_from_catalog_payload(self.payload, position=position)


@dataclass
class LoadoutSlot:
    """One of the 6 team slots."""
    entry: Optional[CatalogEntry] = None


@dataclass
class EditorView:
    name: str
    catalog: List[CatalogEntry]
    slots: List[LoadoutSlot]
    pubkey_hex: str = ""
    identity_name: Optional[str] = None
    pane: Pane = Pane.CATALOG
    catalog_cursor: int = 0
    loadout_cursor: int = 0
    dirty: bool = False
    flash: Optional[str] = None
    flash_color: Optional[str] = None
    save_path: Optional[Path] = None

    def __post_init__(self) -> None:
        if len(self.slots) != TEAM_SIZE:
            self.slots = (self.slots + [LoadoutSlot() for _ in range(TEAM_SIZE)])[:TEAM_SIZE]

    @property
    def filled_count(self) -> int:
        return sum(1 for s in self.slots if s.entry is not None)

    @property
    def selected_catalog_entry(self) -> Optional[CatalogEntry]:
        if not self.catalog:
            return None
        i = max(0, min(self.catalog_cursor, len(self.catalog) - 1))
        return self.catalog[i]

    @property
    def selected_loadout_slot(self) -> Optional[LoadoutSlot]:
        if not self.slots:
            return None
        i = max(0, min(self.loadout_cursor, len(self.slots) - 1))
        return self.slots[i]

    @property
    def catalog_page(self) -> int:
        return self.catalog_cursor // CATALOG_PAGE

    @property
    def catalog_page_count(self) -> int:
        n = len(self.catalog)
        if n == 0:
            return 1
        return (n + CATALOG_PAGE - 1) // CATALOG_PAGE


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    ok: bool
    message: str


def validate_view(view: EditorView) -> ValidationResult:
    """Live structural validation. Mirrors engine.loadout.validate_loadout."""
    n = view.filled_count
    if n < TEAM_SIZE:
        return ValidationResult(
            ok=False,
            message=f"NEED {TEAM_SIZE - n} MORE CARDS",
        )
    from daimon.cards import load_card_dict
    try:
        cards = tuple(load_card_dict(dict(s.entry.payload))
                      for s in view.slots if s.entry is not None)
        validate_loadout(cards)
    except (ValueError, TypeError) as e:
        return ValidationResult(ok=False, message=f"INVALID: {e}")
    return ValidationResult(ok=True, message="OK — ready to save")


# ---------------------------------------------------------------------------
# Render — pure
# ---------------------------------------------------------------------------

def render_frame(view: EditorView, *,
                 mode: RenderMode = RenderMode.OVERLAY_ONLY,
                 color: bool = True,
                 width: int = WIDTH
                 ) -> Tuple[str, List[ImageOverlay]]:
    """Build the editor frame. Returns ``(frame_string, overlays)``.

    Always emits OVERLAY_ONLY (blank art cells + absolute-coord overlays).
    The ``mode`` parameter is preserved for backward compat with the
    screenshot harness; only ``OVERLAY_ONLY`` is supported now.
    """
    val = validate_view(view)

    # ----- HEADER -----
    dirty_field = ""
    if view.dirty:
        dirty_field = colorize("· DIRTY", BRIGHT_YELLOW, bold=True) if color else (
            "· DIRTY")
    title = f"loadout edit  {view.name}  {dirty_field}".rstrip()
    header_lines = header(title,
                          identity=_ident(view.pubkey_hex, view.identity_name),
                          width=width)

    # Pane-headers row (above the tile grids)
    cat_focused = (view.pane == Pane.CATALOG)
    lo_focused = (view.pane == Pane.LOADOUT)
    cat_label = "CATALOG" + (
        colorize("  ◀ FOCUS", BRIGHT_YELLOW, bold=True) if (color and cat_focused)
        else "          "
    )
    lo_label = (f"LOADOUT  ({view.filled_count}/{TEAM_SIZE})"
                + (colorize("  ◀ FOCUS", BRIGHT_YELLOW, bold=True)
                   if (color and lo_focused) else "          "))
    pane_header_left = pad_visible("  " + cat_label, CAT_W)
    pane_header_right = pad_visible("  " + lo_label, LO_W)

    sep_color = colorize("│", DIM) if color else "│"
    sep = " " + sep_color + " "
    pane_header_body = pane_header_left + sep + pane_header_right
    pane_header_line = frame_line(pane_header_body, width)

    # ----- TILE GRIDS -----
    grid_lines, overlays = _render_grids(
        view, mode=mode, color=color, width=width,
        body_top_row=len(header_lines) + 1,    # +1 for the pane-header row
    )

    # ----- VALIDATION + CURSOR LINE -----
    val_color = BRIGHT_GREEN if val.ok else BRIGHT_RED
    if not val.ok and val.message.startswith("NEED"):
        val_color = BRIGHT_YELLOW
    val_text = colorize(val.message, val_color, bold=True) if color else val.message
    val_line = frame_line(f"  validation: {val_text}", width)

    cur_text = "—"
    if view.pane == Pane.CATALOG:
        ce = view.selected_catalog_entry
        if ce is not None:
            cur_text = (f"{ce.card_id} · "
                        f"{colorize(ce.rarity, rarity_color(ce.rarity), bold=True) if color else ce.rarity}"
                        f" · {colorize(ce.element, element_color(ce.element), bold=True) if color else ce.element}"
                        f" · atk {ce.atk} def {ce.def_} hp {ce.hp} spd {ce.spd}")
    else:
        slot = view.selected_loadout_slot
        if slot is not None and slot.entry is not None:
            ce = slot.entry
            cur_text = (f"slot {view.loadout_cursor}: {ce.card_id} · "
                        f"{colorize(ce.rarity, rarity_color(ce.rarity), bold=True) if color else ce.rarity}"
                        f" · {colorize(ce.element, element_color(ce.element), bold=True) if color else ce.element}"
                        f" · atk {ce.atk} def {ce.def_} hp {ce.hp} spd {ce.spd}")
        elif slot is not None:
            cur_text = (f"slot {view.loadout_cursor}: "
                        + (colorize("(empty — TAB to CATALOG, ENTER to fill)", DIM)
                           if color else "(empty — TAB to CATALOG, ENTER to fill)"))
    cur_line = frame_line(f"  cursor:     {cur_text}", width)

    flash_lines: List[str] = []
    if view.flash:
        fc = view.flash_color or BRIGHT_GREEN
        flash_lines.append(frame_line(
            "  " + (colorize(view.flash, fc, bold=True) if color else view.flash),
            width))

    # ----- STATUS BAR -----
    page_part = (f"  ·  catalog page {view.catalog_page + 1}/{view.catalog_page_count}"
                 if view.pane == Pane.CATALOG else "")
    status_left = (f"{view.filled_count}/{TEAM_SIZE} cards"
                   + ("  ·  unsaved" if view.dirty else "")
                   + page_part)
    status_keys = "[↑↓←→]select [⏎/+]add [TAB]focus [-]drop [PgUp/Dn]page [s]save [q]quit"
    sb = status_bar(status_left, status_keys, width=width)

    # ----- ASSEMBLE -----
    body: List[str] = [pane_header_line]
    body.extend(grid_lines)
    body.append(divider(width))
    body.append(val_line)
    body.append(cur_line)
    body.extend(flash_lines)
    return "\n".join(header_lines + body + sb), overlays


def _render_grids(view: EditorView, *,
                  mode: RenderMode, color: bool, width: int,
                  body_top_row: int
                  ) -> Tuple[List[str], List[ImageOverlay]]:
    """Render the catalog tile grid (left) and loadout tile grid (right)."""
    overlays: List[ImageOverlay] = []

    # ----- CATALOG GRID -----
    page = view.catalog_page
    start = page * CATALOG_PAGE
    page_entries = view.catalog[start:start + CATALOG_PAGE]
    # Build per-slot in-loadout marker lookup.
    in_loadout_ids = {s.entry.card_id for s in view.slots if s.entry is not None}

    cat_rows: List[List[Tile]] = []
    for ri in range(CAT_GRID_ROWS):
        row_tiles: List[Tile] = []
        for ci in range(CAT_GRID_COLS):
            local_idx = ri * CAT_GRID_COLS + ci
            global_idx = start + local_idx
            if local_idx < len(page_entries):
                entry = page_entries[local_idx]
                in_loadout = entry.card_id in in_loadout_ids
                tile = _catalog_tile(entry, global_idx, view, in_loadout,
                                     mode=mode, color=color)
            else:
                tile = render_tile(card_id="", width=TILE_W, art_h=TILE_ART_H,
                                   caption_lines=("", ""), ghost=True,
                                   mode=mode, color=color)
            row_tiles.append(tile)
        cat_rows.append(row_tiles)

    # ----- LOADOUT GRID -----
    lo_rows: List[List[Tile]] = []
    for ri in range(LO_GRID_ROWS):
        row_tiles: List[Tile] = []
        for ci in range(LO_GRID_COLS):
            slot_idx = ri * LO_GRID_COLS + ci
            slot = view.slots[slot_idx]
            tile = _loadout_tile(slot, slot_idx, view,
                                 mode=mode, color=color)
            row_tiles.append(tile)
        lo_rows.append(row_tiles)

    # ----- COMPOSE -----
    cat_lines: List[str] = []
    for ri, row_tiles in enumerate(cat_rows):
        composed = compose_row(row_tiles, gap=TILE_GAP, left_pad=LEFT_PAD)
        abs_row_top = body_top_row + len(cat_lines)
        overlays.extend(overlays_for_row(composed, base_row=abs_row_top, base_col=1))
        cat_lines.extend(composed.lines)
        if ri < CAT_GRID_ROWS - 1:
            cat_lines.append(" " * composed.width)

    lo_lines: List[str] = []
    lo_col_offset = 1 + CAT_W + SEP_W
    for ri, row_tiles in enumerate(lo_rows):
        composed = compose_row(row_tiles, gap=TILE_GAP, left_pad=LEFT_PAD)
        abs_row_top = body_top_row + len(lo_lines)
        overlays.extend(overlays_for_row(composed, base_row=abs_row_top,
                                         base_col=lo_col_offset))
        lo_lines.extend(composed.lines)
        if ri < LO_GRID_ROWS - 1:
            lo_lines.append(" " * composed.width)

    # ----- STITCH -----
    total_h = max(len(cat_lines), len(lo_lines))
    while len(cat_lines) < total_h:
        cat_lines.append(" " * CAT_W)
    while len(lo_lines) < total_h:
        lo_lines.append(" " * LO_W)

    sep_color = colorize("│", DIM) if color else "│"
    sep = " " + sep_color + " "

    out_lines: List[str] = []
    for li in range(total_h):
        cat_line = cat_lines[li]
        lo_line = lo_lines[li]
        cpad = CAT_W - visible_len(cat_line)
        if cpad > 0:
            cat_line = cat_line + " " * cpad
        lpad = LO_W - visible_len(lo_line)
        if lpad > 0:
            lo_line = lo_line + " " * lpad
        body_line = cat_line + sep + lo_line
        out_lines.append(frame_line(body_line, width))
    return out_lines, overlays


def _catalog_tile(entry: CatalogEntry, global_idx: int, view: EditorView,
                  in_loadout: bool, *,
                  mode: RenderMode, color: bool) -> Tile:
    """Render one catalog card tile.

    Phase F: art region holds the composited card (gold rarity border, name,
    element chip, stats, flavor) baked in by daimon.play.card_tile. Captions
    slim down to TUI-only state: a ✓ marker when this card is already in the
    loadout. Card identity (name/rarity/element) is visible inside the
    composited tile itself.
    """
    selected = (view.pane == Pane.CATALOG
                and global_idx == view.catalog_cursor)

    # Caption row 1 — ✓ marker when card is already on the team.
    if in_loadout:
        marker = colorize("✓ EQUIPPED", BRIGHT_GREEN, bold=True) if color else "✓ EQUIPPED"
    else:
        marker = ""
    cap1 = pad_visible(marker, TILE_W - 2)

    # Caption row 2 — focus/cursor marker.
    if selected and color:
        cap2 = colorize("◀ FOCUS", BRIGHT_CYAN, bold=True)
    else:
        cap2 = ""
    cap2 = pad_visible(cap2, TILE_W - 2)

    border = None
    if mode == RenderMode.OVERLAY_ONLY:
        if selected:
            border = (130, 220, 240)
        elif in_loadout:
            border = (90, 200, 110)
        else:
            border = (80, 80, 100)

    return render_tile(
        card_id=entry.card_id,
        width=TILE_W,
        art_h=TILE_ART_H,
        caption_lines=(cap1, cap2),
        selected=selected,
        mode=mode,
        border_color_rgb=border,
        color=color,
        composited_info=entry.to_tile_info(position=global_idx % TEAM_SIZE),
    )


def _loadout_tile(slot: LoadoutSlot, slot_idx: int, view: EditorView, *,
                  mode: RenderMode, color: bool) -> Tile:
    """Render one loadout slot — empty (ghost) or filled (image tile).

    Phase F: filled slots show the composited card tile in the art region;
    captions carry only the TUI-specific slot index ([0]..[5]) and an
    optional empty/cursor marker.
    """
    selected = (view.pane == Pane.LOADOUT and slot_idx == view.loadout_cursor)
    if slot.entry is None:
        cap1 = pad_visible(f"[{slot_idx}]", TILE_W - 2)
        cap2 = pad_visible(
            colorize("(empty)", DIM) if color else "(empty)",
            TILE_W - 2)
        return render_tile(
            card_id="",
            width=TILE_W,
            art_h=TILE_ART_H,
            caption_lines=(cap1, cap2),
            ghost=True,
            selected=selected,
            mode=mode,
            color=color,
        )
    ce = slot.entry

    # Caption row 1 — slot index handle (the TUI's only addressing).
    idx_text = f"[{slot_idx}]"
    if color:
        idx_text = colorize(idx_text, BRIGHT_CYAN if selected else GRAY,
                            bold=selected)
    cap1 = pad_visible(idx_text, TILE_W - 2)

    # Caption row 2 — focus marker (only when this is the cursor slot).
    if selected and color:
        cap2 = colorize("◀ FOCUS", BRIGHT_CYAN, bold=True)
    else:
        cap2 = ""
    cap2 = pad_visible(cap2, TILE_W - 2)

    border = None
    if mode == RenderMode.OVERLAY_ONLY:
        if selected:
            border = (130, 220, 240)
        else:
            border = (110, 200, 130)

    return render_tile(
        card_id=ce.card_id,
        width=TILE_W,
        art_h=TILE_ART_H,
        caption_lines=(cap1, cap2),
        selected=selected,
        mode=mode,
        border_color_rgb=border,
        color=color,
        composited_info=ce.to_tile_info(position=slot_idx),
    )


def _rarity_short(r: str) -> str:
    return {"common": "comm", "uncommon": "uncm", "rare": "rare",
            "epic": "epic", "legendary": "lgnd"}.get(r, r[:4])


def _ident(pubkey_hex: str, name: Optional[str]) -> str:
    short = f"{pubkey_hex[:4]}…{pubkey_hex[-4:]}" if pubkey_hex else "anon"
    return f"{name} · {short}" if name else short


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_editor_view(name: str, *,
                     catalog_id: str = "v1_alpha",
                     identity_name: Optional[str] = None,
                     loadouts_dir: Optional[Path] = None) -> EditorView:
    """Build an EditorView for ``name``, creating an empty file if missing."""
    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
    from daimon.identity.keys import CONFIG_DIR
    from daimon.identity import load_identity

    cat = load_catalog(catalog_id or DEFAULT_CATALOG_ID)
    catalog: List[CatalogEntry] = []
    for cid in sorted(cat.by_id.keys()):
        cc = cat.by_id[cid]
        p = dict(cc.payload)
        catalog.append(CatalogEntry(
            card_id=cc.card_id,
            rarity=cc.rarity,
            element=p.get("element", "NORMAL"),
            species=p.get("species", cc.card_id),
            payload=p,
        ))

    by_id = {ce.card_id: ce for ce in catalog}

    if loadouts_dir is None:
        loadouts_dir = Path(CONFIG_DIR) / "loadouts"
    loadouts_dir.mkdir(parents=True, exist_ok=True)
    save_path = loadouts_dir / f"{name}.json"

    slots: List[LoadoutSlot] = [LoadoutSlot() for _ in range(TEAM_SIZE)]
    if save_path.exists():
        try:
            doc = json.loads(save_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            doc = {}
        cards_field = doc.get("cards") or doc.get("loadout") or []
        for i, c in enumerate(cards_field[:TEAM_SIZE]):
            if isinstance(c, str):
                ce = by_id.get(c)
            elif isinstance(c, dict):
                ce = by_id.get(c.get("card_id", ""))
            else:
                ce = None
            if ce is not None:
                slots[i] = LoadoutSlot(entry=ce)

    pubkey_hex = ""
    try:
        pubkey_hex = load_identity().pubkey_hex
    except Exception:    # noqa: BLE001 — identity optional for editor
        pubkey_hex = ""

    return EditorView(
        name=name,
        catalog=catalog,
        slots=slots,
        pubkey_hex=pubkey_hex,
        identity_name=identity_name,
        save_path=save_path,
    )


def save_editor_view(view: EditorView) -> Tuple[bool, str]:
    val = validate_view(view)
    if not val.ok:
        return False, f"can't save: {val.message}"
    if view.save_path is None:
        return False, "save path not set"
    payload = {
        "name": view.name,
        "cards": [s.entry.payload for s in view.slots if s.entry is not None],
    }
    view.save_path.parent.mkdir(parents=True, exist_ok=True)
    view.save_path.write_text(json.dumps(payload, indent=2),
                              encoding="utf-8")
    view.dirty = False
    return True, f"saved to {view.save_path}"


# ---------------------------------------------------------------------------
# Interactive runner
# ---------------------------------------------------------------------------

@dataclass
class LoadoutEditorRunner:
    view: EditorView
    sink: TextIO = field(default_factory=lambda: sys.stdout)
    color: bool = True
    keyboard: bool = True
    width: int = WIDTH

    _stop: bool = field(default=False, init=False)
    _last_signature: Optional[tuple] = field(default=None, init=False)

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
        return render_frame(self.view, mode=mode,
                            color=self.color, width=self.width)

    # ----- key handling -----

    def _handle_key(self, key) -> None:
        v = self.view
        v.flash = None
        if key in (Key.Q, Key.ESC):
            self._stop = True
            return
        if key == "s":
            ok, msg = save_editor_view(v)
            v.flash = msg
            v.flash_color = BRIGHT_GREEN if ok else BRIGHT_RED
            if ok:
                self._stop = True
            return
        if key in ("\t", "tab"):
            v.pane = Pane.LOADOUT if v.pane == Pane.CATALOG else Pane.CATALOG
            return
        if key == Key.UP:
            self._move_grid(0, -1)
            return
        if key == Key.DOWN:
            self._move_grid(0, +1)
            return
        if key == Key.LEFT:
            self._move_grid(-1, 0)
            return
        if key == Key.RIGHT:
            self._move_grid(+1, 0)
            return
        if key in ("p", Key.P):
            self._move_page(-1)
            return
        if key in ("n", Key.N):
            self._move_page(+1)
            return
        if key in (Key.ENTER, "+"):
            self._add()
            return
        if key == "-":
            self._drop()
            return

    def _move_grid(self, dc: int, dr: int) -> None:
        v = self.view
        if v.pane == Pane.CATALOG:
            cols = CAT_GRID_COLS
            n = len(v.catalog)
            if n == 0:
                return
            cur = v.catalog_cursor
            cur += dc + dr * cols
            cur %= n
            v.catalog_cursor = cur
        else:
            cols = LO_GRID_COLS
            cur = v.loadout_cursor
            cur += dc + dr * cols
            cur %= TEAM_SIZE
            v.loadout_cursor = cur

    def _move_page(self, delta: int) -> None:
        v = self.view
        if v.pane != Pane.CATALOG:
            return
        n = len(v.catalog)
        if n == 0:
            return
        new_page = (v.catalog_page + delta) % v.catalog_page_count
        v.catalog_cursor = min(new_page * CATALOG_PAGE, n - 1)

    def _add(self) -> None:
        v = self.view
        ce = v.selected_catalog_entry
        if ce is None:
            v.flash = "catalog empty"
            v.flash_color = BRIGHT_RED
            return
        if v.pane == Pane.LOADOUT:
            v.slots[v.loadout_cursor] = LoadoutSlot(entry=ce)
            v.dirty = True
            return
        for i, s in enumerate(v.slots):
            if s.entry is None:
                v.slots[i] = LoadoutSlot(entry=ce)
                v.loadout_cursor = i
                v.dirty = True
                return
        v.slots[v.loadout_cursor] = LoadoutSlot(entry=ce)
        v.dirty = True

    def _drop(self) -> None:
        v = self.view
        if v.pane != Pane.LOADOUT:
            v.flash = "switch to LOADOUT pane (TAB) to drop a card"
            v.flash_color = BRIGHT_YELLOW
            return
        if v.slots[v.loadout_cursor].entry is None:
            return
        v.slots[v.loadout_cursor] = LoadoutSlot()
        v.dirty = True

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
        screen, overlays = render_frame(self.view,
                                        color=self.color, width=self.width)
        sig = (self.view.pane, self.view.catalog_cursor,
               self.view.loadout_cursor,
               tuple((s.entry.card_id if s.entry else None) for s in self.view.slots),
               self.view.dirty, self.view.flash)
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

def run_loadout_editor(name: str, *,
                       catalog_id: str = "v1_alpha",
                       identity_name: Optional[str] = None,
                       sink: Optional[TextIO] = None,
                       color: bool = True,
                       keyboard: bool = True) -> int:
    view = load_editor_view(name, catalog_id=catalog_id,
                            identity_name=identity_name)
    runner = LoadoutEditorRunner(
        view=view,
        sink=sink or sys.stdout,
        color=color,
        keyboard=keyboard,
    )
    return runner.run()
