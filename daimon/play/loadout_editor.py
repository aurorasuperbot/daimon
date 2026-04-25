"""Interactive loadout editor — `daimon loadout edit <name>`.

Split-pane deck builder: catalog/collection on the left, your 6-card loadout
on the right. Live validation against engine.Loadout rules (TEAM_SIZE=6,
no duplicate card_id, ≤2 same-species). Save with `s`, quit with `q`.

Surface contract (mirrors shop_ui / collection_ui):

  * ``render_frame(view) -> str``       — pure renderer (agent-friendly)
  * ``LoadoutEditorRunner.run()``       — interactive event loop (humans)
  * Saved file is a showcase-format JSON: ``{"name", "cards"}``, the same
    shape `daimon loadout save` writes — so the file is fungible across
    the CLI surface.

Layout (80 col × ~24 row):

    ╔══════════════════════════════════════════════════════════════════════════════╗
    ║  DAIMON · loadout edit  my_team               santiago · 8a3c…1c2d  · DIRTY  ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  CATALOG (focus →)                  ║  LOADOUT  (4/6)                        ║
    ║  ▶ aegis_lion       rare   NORMAL   ║  [0] aegis_lion       rare    NORMAL   ║
    ║    blazewolf        rare   FIRE     ║  [1] blazewolf        rare    FIRE     ║
    ║    voltcat_apex     rare   VOLT     ║  [2] voltcat_apex     rare    VOLT     ║
    ║    bulwarthog       rare   NATURE   ║  [3] bulwarthog       rare    NATURE   ║
    ║    tidewyrm         rare   WATER    ║  [4] (empty)                           ║
    ║    glimmerowl       uncm   VOLT     ║  [5] (empty)                           ║
    ║    iron_boar        comm   NORMAL   ║                                        ║
    ║    dashmouse        comm   NORMAL   ║                                        ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  validation: NEED 2 MORE CARDS                                               ║
    ║  cursor:    aegis_lion · rare · NORMAL · atk 6 def 8 hp 30                   ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  4/6 cards     [↑↓]select  [⏎/+]add  [TAB]focus  [-]drop  [s]save  [q]quit   ║
    ╚══════════════════════════════════════════════════════════════════════════════╝

Keys:

  ↑/↓             move cursor (in focused pane)
  TAB / →         shift focus from CATALOG to LOADOUT (to remove cards)
  TAB / ←         shift focus from LOADOUT to CATALOG (to add cards)
  ENTER / +       add cursor's catalog card to first empty loadout slot
                  (or replace cursor's loadout slot if loadout pane focused)
  -               remove cursor's loadout card (loadout pane only)
  s               save and exit (only succeeds if validation == OK)
  Q / ESC         quit without saving (prompts if dirty? — V1: no prompt,
                  refuse-on-q-when-dirty-and-save-pending is left for v1.1)
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
    GRAY,
    GREEN,
    HOME,
    RED,
    RESET,
    WIDTH,
    YELLOW,
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
    visible_len,
)


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
    """One pickable card in the left pane.

    ``payload`` is the catalog dict (what we save into the loadout file when
    the user adds it to their team).
    """
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


@dataclass
class LoadoutSlot:
    """One position in the 6-slot team. Empty if entry is None."""
    entry: Optional[CatalogEntry] = None


@dataclass
class EditorView:
    """All TUI state at one render tick."""
    name: str                          # the loadout file name being edited
    catalog: List[CatalogEntry]
    slots: List[LoadoutSlot]           # always TEAM_SIZE long
    pubkey_hex: str = ""
    identity_name: Optional[str] = None
    pane: Pane = Pane.CATALOG
    catalog_cursor: int = 0
    loadout_cursor: int = 0
    dirty: bool = False
    flash: Optional[str] = None
    flash_color: Optional[str] = None
    save_path: Optional[Path] = None   # set when the editor is wired to disk

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
    # Build the engine.Loadout — that's the canonical check.
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

LIST_ROWS = 9            # rows reserved per pane
# Layout math: " " + LEFT_W + PANE_SEP(3) + RIGHT_W must fit in WIDTH-2 (78).
# 1 + 37 + 3 + 37 = 78 — fits exactly, no truncation.
LEFT_W = 37              # left pane visible width (inside the inner ║ wall)
RIGHT_W = 37             # right pane visible width
PANE_SEP = " ║ "         # vertical separator between panes (3 chars)


def render_frame(view: EditorView, *, color: bool = True,
                 width: int = WIDTH) -> str:
    """Build the editor frame as one string."""
    val = validate_view(view)

    # ----- HEADER -----
    dirty_field = ""
    if view.dirty:
        dirty_field = colorize("· DIRTY", BRIGHT_YELLOW, bold=True) if color else (
            "· DIRTY")
    title = f"loadout edit  {view.name}  {dirty_field}".rstrip()
    h = header(title, identity=_ident(view.pubkey_hex, view.identity_name),
               width=width)

    # ----- COLUMN HEADERS + LIST ROWS -----
    cat_header = "CATALOG" + (
        colorize("  ◀ FOCUS", BRIGHT_YELLOW, bold=True) if (color and view.pane == Pane.CATALOG)
        else "         "
    )
    lo_header = (f"LOADOUT  ({view.filled_count}/{TEAM_SIZE})"
                 + (colorize("  ◀ FOCUS", BRIGHT_YELLOW, bold=True)
                    if (color and view.pane == Pane.LOADOUT) else "         "))

    rows: list[str] = [_split_line(cat_header, lo_header, width)]
    for i in range(LIST_ROWS):
        left = _render_catalog_row(view, i, color=color)
        right = _render_loadout_row(view, i, color=color)
        rows.append(_split_line(left, right, width))

    # ----- VALIDATION BAND -----
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
            cur_text = (
                f"slot {view.loadout_cursor}: "
                + (colorize("(empty — press ←/TAB then ENTER to fill)", DIM, bold=False)
                   if color else "(empty — press ←/TAB then ENTER to fill)")
            )

    cur_line = frame_line(f"  cursor:     {cur_text}", width)

    flash_lines: list[str] = []
    if view.flash:
        fc = view.flash_color or BRIGHT_GREEN
        flash_lines.append(frame_line(
            "  " + (colorize(view.flash, fc, bold=True) if color else view.flash),
            width))

    # ----- BODY ASSEMBLY -----
    body: list[str] = []
    body.extend(rows)
    body.append(divider(width))
    body.append(val_line)
    body.append(cur_line)
    body.extend(flash_lines)

    # ----- STATUS BAR -----
    status_left = (f"{view.filled_count}/{TEAM_SIZE} cards"
                   + ("  ·  unsaved" if view.dirty else ""))
    status_keys = "[↑↓]select [⏎/+]add [TAB]focus [-]drop [s]save [q]quit"
    sb = status_bar(status_left, status_keys, width=width)

    return "\n".join(h + body + sb)


def _render_catalog_row(view: EditorView, row: int, *, color: bool) -> str:
    """One row inside the left pane (CATALOG)."""
    if not view.catalog:
        if row == 0:
            return colorize("  (no catalog)", DIM, bold=False) if color else "  (no catalog)"
        return ""
    # Pagination so the catalog cursor stays visible.
    n = len(view.catalog)
    start = max(0, min(n - LIST_ROWS, view.catalog_cursor - LIST_ROWS // 2))
    idx = start + row
    if idx >= n:
        return ""
    ce = view.catalog[idx]
    selected = (idx == view.catalog_cursor and view.pane == Pane.CATALOG)

    rar_col = rarity_color(ce.rarity) if color else None
    elem_col = element_color(ce.element) if color else None

    name_text = ce.card_id
    rar_text = _rarity_short(ce.rarity)
    elem_text = ce.element
    if color:
        name_text = colorize(name_text, rar_col)
        rar_text = colorize(rar_text, rar_col, bold=True)
        elem_text = colorize(elem_text, elem_col, bold=True)

    in_loadout = any(s.entry is not None and s.entry.card_id == ce.card_id
                     for s in view.slots)
    marker = "✓" if in_loadout else " "
    if color and in_loadout:
        marker = colorize("✓", BRIGHT_GREEN, bold=True)

    cur = cursor_prefix(selected, color=color)
    # Row: cur(2) + marker(1) + space(1) + name(18) + 2 + rar(4) + 2 + elem(7) = 37.
    body = (cur + marker + " "
            + pad_visible(name_text, 18) + "  "
            + pad_visible(rar_text, 4) + "  "
            + pad_visible(elem_text, 7))
    if selected and color:
        body = (BG_GRAY + body.replace(RESET, RESET + BG_GRAY) + RESET)
    return body


def _render_loadout_row(view: EditorView, row: int, *, color: bool) -> str:
    """One row inside the right pane (LOADOUT)."""
    if row >= TEAM_SIZE:
        return ""
    slot = view.slots[row]
    selected = (row == view.loadout_cursor and view.pane == Pane.LOADOUT)
    cur = cursor_prefix(selected, color=color)
    idx_field = f"[{row}]"
    if slot.entry is None:
        body = (cur
                + pad_visible(idx_field, 4) + " "
                + (colorize("(empty)", DIM, bold=False) if color else "(empty)"))
    else:
        ce = slot.entry
        rar_col = rarity_color(ce.rarity) if color else None
        elem_col = element_color(ce.element) if color else None
        name_text = ce.card_id
        rar_text = _rarity_short(ce.rarity)
        elem_text = ce.element
        if color:
            name_text = colorize(name_text, rar_col)
            rar_text = colorize(rar_text, rar_col, bold=True)
            elem_text = colorize(elem_text, elem_col, bold=True)
        # Row: cur(2) + idx(4) + space(1) + name(15) + 2 + rar(4) + 2 + elem(7) = 37.
        body = (cur
                + pad_visible(idx_field, 4) + " "
                + pad_visible(name_text, 15) + "  "
                + pad_visible(rar_text, 4) + "  "
                + pad_visible(elem_text, 7))
    if selected and color:
        body = (BG_GRAY + body.replace(RESET, RESET + BG_GRAY) + RESET)
    return body


def _split_line(left: str, right: str, width: int) -> str:
    """Render ║ left ║ right ║ row, padding each pane to its column width.

    Each pane gets ``LEFT_W`` / ``RIGHT_W`` visible cells; PANE_SEP sits
    between them with a center wall char so the split is unmistakeable.
    """
    interior = width - 2  # excluding outer ║ walls
    # Compose: " " + left(LEFT_W) + " ║ " + right(RIGHT_W) + " "
    left_field = pad_visible(left, LEFT_W)
    right_field = pad_visible(right, RIGHT_W)
    body = " " + left_field + PANE_SEP + right_field
    # Pad to fill remainder if needed (e.g. width > computed sum).
    if visible_len(body) < interior:
        body += " " * (interior - visible_len(body))
    return frame_line(body, width)


def _rarity_short(r: str) -> str:
    """Short rarity tag for tight panes."""
    return {"common": "comm", "uncommon": "uncm", "rare": "rare",
            "epic": "epic", "legendary": "lgnd"}.get(r, r[:4])


def _ident(pubkey_hex: str, name: Optional[str]) -> str:
    short = f"{pubkey_hex[:4]}…{pubkey_hex[-4:]}" if pubkey_hex else "anon"
    return f"{name} · {short}" if name else short


# ---------------------------------------------------------------------------
# Persistence — load + save
# ---------------------------------------------------------------------------

def load_editor_view(name: str, *,
                     catalog_id: str = "v1_alpha",
                     identity_name: Optional[str] = None,
                     loadouts_dir: Optional[Path] = None) -> EditorView:
    """Build an EditorView for ``name``, creating an empty file if missing.

    Reads the catalog into ``CatalogEntry``s and pre-populates slots from
    the existing loadout file (showcase or full-card-dict format).
    """
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
    """Persist the loadout to disk in the canonical ``{name, cards}`` shape.

    Returns ``(ok, message)``. Refuses to save if validation fails.
    """
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
        return render_frame(self.view, color=self.color, width=self.width)

    # ----- key handling -----

    def _handle_key(self, key) -> None:
        v = self.view
        v.flash = None    # clear stale message on any key
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
        if key == "\t" or key == "tab":
            v.pane = Pane.LOADOUT if v.pane == Pane.CATALOG else Pane.CATALOG
            return
        if key == Key.LEFT:
            if v.pane == Pane.LOADOUT:
                v.pane = Pane.CATALOG
                return
        if key == Key.RIGHT:
            if v.pane == Pane.CATALOG:
                v.pane = Pane.LOADOUT
                return
        if key == Key.UP:
            self._move(-1)
            return
        if key == Key.DOWN:
            self._move(+1)
            return
        if key in (Key.ENTER, "+"):
            self._add()
            return
        if key == "-":
            self._drop()
            return

    def _move(self, delta: int) -> None:
        v = self.view
        if v.pane == Pane.CATALOG:
            n = len(v.catalog)
            if n:
                v.catalog_cursor = (v.catalog_cursor + delta) % n
        else:
            n = len(v.slots)
            if n:
                v.loadout_cursor = (v.loadout_cursor + delta) % n

    def _add(self) -> None:
        v = self.view
        ce = v.selected_catalog_entry
        if ce is None:
            v.flash = "catalog empty"
            v.flash_color = BRIGHT_RED
            return
        # If the loadout pane is focused, replace the cursor's slot.
        if v.pane == Pane.LOADOUT:
            v.slots[v.loadout_cursor] = LoadoutSlot(entry=ce)
            v.dirty = True
            return
        # Otherwise add into the first empty slot (or replace cursor if all full).
        for i, s in enumerate(v.slots):
            if s.entry is None:
                v.slots[i] = LoadoutSlot(entry=ce)
                v.loadout_cursor = i
                v.dirty = True
                return
        # All filled — bump cursor's loadout-side slot for replacement.
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
        screen = render_frame(self.view, color=self.color, width=self.width)
        sig = (self.view.pane, self.view.catalog_cursor,
               self.view.loadout_cursor,
               tuple((s.entry.card_id if s.entry else None) for s in self.view.slots),
               self.view.dirty, self.view.flash)
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
