"""Tests for the daimon.ui framework primitives.

Headless tests covering Frame composition, layout flex math, widget
rendering, mouse decoder, hit region dispatch, and Screen lifecycle.
Aimed to catch regressions in the framework as we port screens onto it.
"""

from __future__ import annotations

import io

import pytest

from daimon.play.tui_style import strip_ansi, visible_len
from daimon.ui import (
    Binding,
    BindingTable,
    Button,
    Frame,
    GameApp,
    HBox,
    HitRegion,
    KeyEvent,
    MouseEvent,
    MouseKind,
    Pad,
    Panel,
    ProgressBar,
    Screen,
    Static,
    VBox,
    Widget,
    button_row,
)
from daimon.ui.input import (
    _decode_csi_sgr_mouse,
    _decode_key,
    decode_sequence,
)
from daimon.ui.layout import _coerce_size, _distribute


# ---------------------------------------------------------------------------
# Frame
# ---------------------------------------------------------------------------


class TestFrame:
    def test_empty_frame_has_correct_dimensions(self):
        f = Frame.empty(width=10, height=3)
        assert f.width == 10
        assert f.height == 3
        assert len(f.rows) == 3
        assert all(len(r) == 10 for r in f.rows)

    def test_from_rows_pads_short_input(self):
        f = Frame.from_rows(["abc"], width=3, height=5)
        assert f.height == 5
        assert f.rows[0] == "abc"
        assert all(r == "   " for r in f.rows[1:])

    def test_from_rows_truncates_long_input(self):
        f = Frame.from_rows(list("abcde"), width=1, height=2)
        assert len(f.rows) == 2
        assert f.rows == ("a", "b")

    def test_translated_shifts_overlays_and_hits(self):
        from daimon.play.screenshot import ImageOverlay
        from pathlib import Path
        ov = ImageOverlay(row=2, col=3, rows=4, cols=5, image_path=Path("x.png"))
        hr = HitRegion(row_start=1, row_end=2, col_start=0, col_end=4,
                       action="click")
        f = Frame.empty(10, 5)
        f = Frame(rows=f.rows, width=10, height=5,
                  overlays=(ov,), hit_regions=(hr,))
        shifted = f.translated(row_offset=10, col_offset=20)
        assert shifted.overlays[0].row == 12
        assert shifted.overlays[0].col == 23
        assert shifted.hit_regions[0].row_start == 11
        assert shifted.hit_regions[0].col_end == 24

    def test_translated_zero_is_noop(self):
        f = Frame.empty(5, 5)
        assert f.translated(0, 0) is f

    def test_with_hit_appends_region(self):
        f = Frame.empty(5, 5)
        new_f = f.with_hit(HitRegion(0, 1, 0, 1, action="x"))
        assert len(new_f.hit_regions) == 1
        assert len(f.hit_regions) == 0  # original unchanged


# ---------------------------------------------------------------------------
# HitRegion
# ---------------------------------------------------------------------------


class TestHitRegion:
    def test_contains_inside(self):
        hr = HitRegion(row_start=2, row_end=5, col_start=10, col_end=15,
                       action="x")
        assert hr.contains(2, 10)
        assert hr.contains(4, 14)
        assert not hr.contains(5, 10)  # row_end is exclusive
        assert not hr.contains(2, 15)  # col_end is exclusive
        assert not hr.contains(1, 10)

    def test_translated(self):
        hr = HitRegion(0, 5, 0, 10, action="x")
        t = hr.translated(3, 4)
        assert (t.row_start, t.row_end, t.col_start, t.col_end) == (3, 8, 4, 14)


# ---------------------------------------------------------------------------
# Layout — _distribute (flex weight math)
# ---------------------------------------------------------------------------


class TestDistribute:
    def test_equal_weights_equal_split(self):
        assert _distribute(10, [1, 1]) == [5, 5]

    def test_uneven_split_with_leftover(self):
        # 10 across [1,1,1] = 3,3,3 + 1 leftover. Goes to higher-weight slot
        # first (here all equal so falls to the first one).
        result = _distribute(10, [1, 1, 1])
        assert sum(result) == 10
        assert max(result) - min(result) <= 1

    def test_weighted_split(self):
        # 10 across [3,1] = 7,3 (3/4 vs 1/4 of 10 = 7.5,2.5 → 7,2 + 1 left → 8,2)
        result = _distribute(10, [3, 1])
        assert sum(result) == 10
        assert result[0] > result[1]  # higher weight gets more

    def test_zero_total(self):
        assert _distribute(0, [1, 2, 3]) == [0, 0, 0]

    def test_empty_weights(self):
        assert _distribute(10, []) == []


# ---------------------------------------------------------------------------
# HBox / VBox
# ---------------------------------------------------------------------------


class TestHBox:
    def test_renders_to_requested_size(self):
        h = HBox([Static("a"), Static("b"), Static("c")])
        f = h.render(30, 1)
        assert f.width == 30
        assert f.height == 1
        # Each child gets 10 cells.
        assert visible_len(f.rows[0]) == 30

    def test_gap_eats_into_total_width(self):
        h = HBox([Static("a"), Static("b")], gap=2)
        f = h.render(10, 1)
        assert f.width == 10
        # Each child gets (10 - 2) / 2 = 4 cells; gap is 2 spaces between.
        plain = strip_ansi(f.rows[0])
        # Char index 4..6 should be spaces (the gap).
        assert plain[4:6] == "  "

    def test_hits_translated_to_absolute_cols(self):
        b1 = Button(action="a", label="A")
        b2 = Button(action="b", label="B")
        h = HBox([b1, b2], gap=1)
        f = h.render(20, 5)
        assert len(f.hit_regions) == 2
        # First button at col 0..(width/2 - gap_share); second after the gap.
        assert f.hit_regions[0].action == "a"
        assert f.hit_regions[0].col_start == 0
        assert f.hit_regions[1].action == "b"
        assert f.hit_regions[1].col_start > f.hit_regions[0].col_end

    def test_empty_children_returns_empty_frame(self):
        h = HBox([])
        f = h.render(10, 5)
        assert f.width == 10 and f.height == 5


class TestVBox:
    def test_renders_to_requested_size(self):
        v = VBox([Static("a"), Static("b")])
        f = v.render(10, 4)
        assert f.height == 4
        assert all(visible_len(r) == 10 for r in f.rows)

    def test_hits_translated_to_absolute_rows(self):
        h = HBox([Button(action="x", label="X")])
        v = VBox([Static("title"), h])
        f = v.render(20, 6)
        # title gets 3 rows, hbox gets 3 rows. hit row should start at row 3.
        assert f.hit_regions
        assert f.hit_regions[0].row_start >= 3


# ---------------------------------------------------------------------------
# Pad
# ---------------------------------------------------------------------------


class TestPad:
    def test_margins_shrink_inner(self):
        p = Pad(Button(action="x", label="X"), top=1, bottom=1, left=2, right=2)
        f = p.render(20, 6)
        # Hit region should be inside the padded area, not at (0,0).
        hr = f.hit_regions[0]
        assert hr.row_start == 1
        assert hr.col_start == 2


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class TestStatic:
    def test_renders_text(self):
        s = Static("hello")
        f = s.render(10, 1)
        assert "hello" in strip_ansi(f.rows[0])

    def test_supports_markup(self):
        s = Static("[bold]hi[/bold]")
        f = s.render(10, 1)
        assert "hi" in strip_ansi(f.rows[0])
        assert "\x1b[" in f.rows[0]  # ANSI codes present

    def test_multiline_with_valign_middle(self):
        s = Static("a\nb", valign="middle")
        f = s.render(10, 5)
        # 2 lines centered in 5 rows → blank, a, b, blank, blank or similar
        assert any("a" in strip_ansi(r) for r in f.rows)
        assert any("b" in strip_ansi(r) for r in f.rows)


class TestButton:
    def test_emits_hit_region(self):
        b = Button(action="pull", label="PULL")
        f = b.render(20, 6)
        assert len(f.hit_regions) == 1
        assert f.hit_regions[0].action == "pull"
        assert f.hit_regions[0].row_start == 0
        assert f.hit_regions[0].col_end == 20

    def test_focused_uses_heavy_border(self):
        normal = Button(action="x", label="X").render(20, 6)
        focused = Button(action="x", label="X", focused=True).render(20, 6)
        # Heavy border uses ╔ and ╚, round border uses ╭ and ╰.
        assert "╔" in focused.rows[0]
        assert "╭" in normal.rows[0]


class TestPanel:
    def test_draws_border(self):
        p = Panel(Static("inner"), border_style="round")
        f = p.render(10, 5)
        assert "╭" in f.rows[0]
        assert "╮" in f.rows[0]
        assert "╰" in f.rows[-1]

    def test_title_in_top_border(self):
        p = Panel(Static(""), title="HELLO")
        f = p.render(20, 5)
        assert "HELLO" in strip_ansi(f.rows[0])


class TestProgressBar:
    def test_zero_progress(self):
        pb = ProgressBar(0, 100)
        f = pb.render(10, 1)
        # All empty cells.
        assert "░" in strip_ansi(f.rows[0])
        assert "█" not in strip_ansi(f.rows[0])

    def test_full_progress(self):
        pb = ProgressBar(100, 100)
        f = pb.render(10, 1)
        assert "█" in strip_ansi(f.rows[0])
        assert "░" not in strip_ansi(f.rows[0])

    def test_half_progress(self):
        pb = ProgressBar(50, 100)
        f = pb.render(10, 1)
        plain = strip_ansi(f.rows[0])
        assert plain.count("█") == 5
        assert plain.count("░") == 5


# ---------------------------------------------------------------------------
# Input decoders
# ---------------------------------------------------------------------------


class TestKeyDecoder:
    @pytest.mark.parametrize("seq,expected", [
        (b"a", "a"),
        (b"Z", "z"),  # lowercased
        (b" ", "space"),
        (b"\r", "enter"),
        (b"\n", "enter"),
        (b"\t", "tab"),
        (b"\x1b", "esc"),
        (b"\x7f", "backspace"),
        (b"\x03", "ctrl+c"),
        (b"\x1b[A", "up"),
        (b"\x1b[B", "down"),
        (b"\x1b[C", "right"),
        (b"\x1b[D", "left"),
        (b"\x1b[H", "home"),
        (b"\x1b[5~", "pgup"),
        (b"\x1bOP", "f1"),
    ])
    def test_decode(self, seq, expected):
        ev = _decode_key(seq)
        assert ev is not None
        assert ev.key == expected

    def test_unknown_returns_none(self):
        assert _decode_key(b"\x1b[Z") is None  # shift-tab; not modeled
        assert _decode_key(b"") is None


class TestMouseDecoder:
    def test_left_press(self):
        ev = _decode_csi_sgr_mouse(b"\x1b[<0;10;5M")
        assert ev == MouseEvent(MouseKind.PRESS, row=4, col=9, button=1)

    def test_left_release(self):
        ev = _decode_csi_sgr_mouse(b"\x1b[<0;10;5m")
        assert ev == MouseEvent(MouseKind.RELEASE, row=4, col=9, button=1)

    def test_scroll_up(self):
        ev = _decode_csi_sgr_mouse(b"\x1b[<64;5;3M")
        assert ev.kind == MouseKind.SCROLL_UP

    def test_scroll_down(self):
        ev = _decode_csi_sgr_mouse(b"\x1b[<65;5;3M")
        assert ev.kind == MouseKind.SCROLL_DOWN

    def test_motion_carries_button(self):
        ev = _decode_csi_sgr_mouse(b"\x1b[<32;10;5M")
        assert ev.kind == MouseKind.MOVE
        assert ev.button == 1

    def test_decode_sequence_routes_to_mouse(self):
        ev = decode_sequence(b"\x1b[<0;1;1M")
        assert isinstance(ev, MouseEvent)


# ---------------------------------------------------------------------------
# BindingTable
# ---------------------------------------------------------------------------


class TestBindingTable:
    def test_dict_form(self):
        t = BindingTable({"p": "pull", "q": ("quit", "Quit")})
        assert t.lookup("p").action == "pull"
        assert t.lookup("q").description == "Quit"

    def test_multi_key_split(self):
        t = BindingTable({"left,h": "focus_prev"})
        assert t.lookup("left").action == "focus_prev"
        assert t.lookup("h").action == "focus_prev"

    def test_visible_filters_show_false(self):
        t = BindingTable([
            Binding(("p",), "pull", "Pull"),
            Binding(("r",), "refresh", "Refresh", show=False),
        ])
        visible = t.visible()
        assert len(visible) == 1
        assert visible[0].action == "pull"


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


class _CountingScreen(Screen):
    """Minimal Screen subclass for testing dispatch + signature dedupe."""

    bindings = BindingTable({
        "p": "pull",
        "q": "quit",
    })

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.actions: list = []
        self.tick_count = 0
        self._sig = 0

    def compose(self):
        return Button(action="click_target", label="X")

    def signature(self):
        return self._sig

    def on_action(self, name, *, source=None):
        self.actions.append((name, source))
        if name == "quit":
            self.quit()

    def on_tick(self):
        self.tick_count += 1


class TestScreen:
    def test_dispatch_key_fires_action(self):
        s = _CountingScreen(sink=io.StringIO())
        s.dispatch_event(KeyEvent("p"))
        assert s.actions == [("pull", None)]

    def test_dispatch_quit_sets_needs_quit(self):
        s = _CountingScreen(sink=io.StringIO())
        s.dispatch_event(KeyEvent("q"))
        assert s.needs_quit() is True

    def test_dispatch_unhandled_key(self):
        s = _CountingScreen(sink=io.StringIO())
        s.dispatch_event(KeyEvent("z"))
        # Default on_unhandled_key is no-op — assert no actions fired.
        assert s.actions == []

    def test_signature_dedupe_skips_render(self):
        sink = io.StringIO()
        s = _CountingScreen(sink=sink, width=20, height=5)
        # First render: emits something.
        assert s.render_to_terminal() is True
        first_output = sink.getvalue()
        # Second render with same signature: no output.
        assert s.render_to_terminal() is False
        assert sink.getvalue() == first_output

    def test_signature_change_triggers_render(self):
        sink = io.StringIO()
        s = _CountingScreen(sink=sink, width=20, height=5)
        s.render_to_terminal()
        s._sig = 1
        assert s.render_to_terminal() is True

    def test_mouse_click_resolves_to_hit_region(self):
        s = _CountingScreen(sink=io.StringIO(), width=20, height=5)
        # Render so hit regions are captured.
        s.render_to_terminal(force=True)
        # Click in the middle of the button (button covers the whole frame).
        s.dispatch_event(MouseEvent(MouseKind.PRESS, row=2, col=10, button=1))
        assert s.actions == [("click_target", s._last_hit_regions[0])]

    def test_mouse_click_outside_hit_no_action(self):
        s = _CountingScreen(sink=io.StringIO(), width=20, height=5)
        s.render_to_terminal(force=True)
        # Render hit covers entire frame; click way outside.
        s.dispatch_event(MouseEvent(MouseKind.PRESS, row=100, col=100, button=1))
        assert s.actions == []


# ---------------------------------------------------------------------------
# GameApp — basic smoke. Full loop is covered by the live menu tests.
# ---------------------------------------------------------------------------


class TestGameApp:
    def test_initial_screen_is_active(self):
        s = _CountingScreen(sink=io.StringIO())
        app = GameApp(s, sink=io.StringIO(), enable_alt_buffer=False,
                      enable_mouse=False)
        assert app._screens == [s]
        assert s._app is app

    def test_quit_sets_stop(self):
        s = _CountingScreen(sink=io.StringIO())
        app = GameApp(s, sink=io.StringIO(), enable_alt_buffer=False,
                      enable_mouse=False)
        app.quit(code=42)
        assert app._stop.is_set()
        assert app._exit_code == 42

    def test_push_pop_screen(self):
        s1 = _CountingScreen(sink=io.StringIO())
        s2 = _CountingScreen(sink=io.StringIO())
        app = GameApp(s1, sink=io.StringIO(), enable_alt_buffer=False,
                      enable_mouse=False)
        app.push_screen(s2)
        assert app._screens[-1] is s2
        app.pop_screen()
        assert app._screens[-1] is s1


# ---------------------------------------------------------------------------
# HomeMenu (the ported menu screen) — verifies focus/dispatch wiring on
# top of the framework. We stub _run_subcommand so nothing actually spawns.
# ---------------------------------------------------------------------------


class TestHomeMenu:
    def _make(self, monkeypatch=None):
        from daimon.play import menu_ui
        # Skip the dm_home() call entirely.
        screen = menu_ui.HomeMenu(
            home_loader=lambda: menu_ui.HomeMenu._initial_home_payload(),
            width=150, height=42, sink=io.StringIO(),
        )
        # Capture rather than spawn.
        screen._spawned = []
        def fake(self, args):
            self._spawned.append(args)
        screen._run_subcommand = fake.__get__(screen, type(screen))
        # Render once to populate hit regions.
        screen.render_to_terminal(force=True)
        return screen

    def test_initial_focus_is_pull(self):
        s = self._make()
        assert s._focus_index == 0

    def test_arrow_keys_move_focus(self):
        s = self._make()
        s.dispatch_event(KeyEvent("right"))
        assert s._focus_index == 1
        s.dispatch_event(KeyEvent("right"))
        assert s._focus_index == 2
        s.dispatch_event(KeyEvent("left"))
        assert s._focus_index == 1

    def test_left_from_zero_wraps(self):
        s = self._make()
        s.dispatch_event(KeyEvent("left"))
        assert s._focus_index == 4  # SHOP

    def test_enter_activates_focused_card(self):
        s = self._make()
        s.dispatch_event(KeyEvent("right"))  # MATCH
        s.dispatch_event(KeyEvent("enter"))
        assert s._spawned == [["npcs"]]

    def test_letter_hotkey_runs_action_and_refocuses(self):
        s = self._make()
        s.dispatch_event(KeyEvent("c"))
        assert s._focus_index == 3  # COLLECTION
        assert s._spawned == [["collection"]]

    def test_mouse_click_on_card_dispatches(self):
        s = self._make()
        # Render again so hit regions are fresh.
        s.render_to_terminal(force=True)
        # Find the MATCH hit region from the captured state.
        match_hits = [hr for hr in s._last_hit_regions
                      if hr.action == "card_match"]
        assert match_hits, "MATCH card has no hit region"
        hr = match_hits[0]
        s.dispatch_event(MouseEvent(
            MouseKind.PRESS,
            row=(hr.row_start + hr.row_end) // 2,
            col=(hr.col_start + hr.col_end) // 2,
            button=1,
        ))
        assert s._focus_index == 1
        assert s._spawned == [["npcs"]]

    def test_quit_sets_needs_quit(self):
        s = self._make()
        s.dispatch_event(KeyEvent("q"))
        assert s.needs_quit()

    def test_signature_changes_with_focus(self):
        s = self._make()
        sig0 = s.signature()
        s.dispatch_event(KeyEvent("right"))
        sig1 = s.signature()
        assert sig0 != sig1

    def test_signature_changes_with_data(self):
        s = self._make()
        sig0 = s.signature()
        with s._home_lock:
            s._home_data = dict(s._home_data)  # shallow copy
            s._home_data["balance"] = 999
        sig1 = s.signature()
        assert sig0 != sig1
