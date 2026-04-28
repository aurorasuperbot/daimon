"""Primitive widgets — Static, Panel, Button, ProgressBar.

These are the building blocks every screen composes. Widgets are stateless
renderers; the View dataclass owns the state and feeds the right config to
each widget on every render pass.

Markup uses Rich's bracket syntax (``[bold yellow]X[/bold yellow]``) since
Rich is already a daimon dep — we delegate parsing + ANSI emission to Rich
and keep the layout math in our own hands.
"""

from __future__ import annotations

import io
from typing import List, Optional, Sequence, Tuple

from rich.console import Console
from rich.text import Text

from daimon.play.tui_style import (
    pad_visible,
    truncate_visible,
    visible_len,
)
from daimon.ui.frame import Frame, HitRegion
from daimon.ui.widget import Widget


# ---------------------------------------------------------------------------
# Markup → ANSI
# ---------------------------------------------------------------------------


def _make_render_console() -> Console:
    """A Rich console configured to capture truecolor ANSI output.

    width is set huge so Rich doesn't wrap — we control wrapping ourselves
    via the layout layer. legacy_windows=False forces VT processing on
    Windows (we always run inside WezTerm so VT is available).
    """
    return Console(
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=10_000,
        legacy_windows=False,
        markup=False,  # we pre-parse with Text.from_markup
        highlight=False,
        soft_wrap=True,
    )


_CONSOLE = _make_render_console()


def render_markup(markup: str) -> str:
    """Convert Rich markup like ``[bold yellow]X[/]`` to ANSI-coded text.

    No-op fast path when the string contains no markup brackets.
    """
    if "[" not in markup:
        return markup
    try:
        text = Text.from_markup(markup)
    except Exception:  # noqa: BLE001 — bad markup shouldn't crash a render
        return markup
    with _CONSOLE.capture() as capture:
        _CONSOLE.print(text, end="", soft_wrap=True)
    return capture.get()


# ---------------------------------------------------------------------------
# Static — fixed text content padded to size
# ---------------------------------------------------------------------------


class Static(Widget):
    """Plain text widget.

    ``content`` is a multi-line string with optional Rich markup. Lines
    longer than ``width`` are truncated; fewer lines than ``height`` get
    padded with blank rows. ``align`` controls horizontal alignment per
    line; ``valign`` centers vertically when content is shorter than the
    requested height.
    """

    def __init__(self,
                 content: str = "",
                 *,
                 align: str = "left",     # left | center | right
                 valign: str = "top",     # top | middle | bottom
                 id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self._content = content
        self._align = align
        self._valign = valign

    def update(self, content: str) -> None:
        self._content = content

    def render(self, width: int, height: int) -> Frame:
        # Render markup once, then split — markup might span lines but we
        # support \n-separated content as a convenience.
        rendered = render_markup(self._content)
        lines = rendered.split("\n") if rendered else [""]
        # Truncate / pad each line to width.
        normalized = [pad_visible(line, width, align=self._align)
                      for line in lines]
        # Vertical alignment.
        if len(normalized) < height:
            blank = " " * width
            extra = height - len(normalized)
            if self._valign == "middle":
                top = extra // 2
                bottom = extra - top
                normalized = [blank] * top + normalized + [blank] * bottom
            elif self._valign == "bottom":
                normalized = [blank] * extra + normalized
            else:
                normalized = normalized + [blank] * extra
        elif len(normalized) > height:
            normalized = normalized[:height]
        return Frame.from_rows(normalized, width=width, height=height)


# ---------------------------------------------------------------------------
# Panel — bordered container with optional title
# ---------------------------------------------------------------------------


# Box-drawing characters by style. Heavy = double-ruled (game-screen look),
# round = soft corners, light = utility/secondary panels.
_BORDER_STYLES = {
    "heavy":  ("╔", "╗", "╚", "╝", "═", "║"),
    "round":  ("╭", "╮", "╰", "╯", "─", "│"),
    "light":  ("┌", "┐", "└", "┘", "─", "│"),
    "double": ("╔", "╗", "╚", "╝", "═", "║"),
}


class Panel(Widget):
    """Bordered container around a single child widget.

    The child is given the inner area (``width - 2 × height - 2``) and its
    Frame is composited inside the border. ``title`` (if set) is overlaid
    on the top border, color-coded by ``border_color``.
    """

    def __init__(self,
                 child: Widget,
                 *,
                 title: Optional[str] = None,
                 border_style: str = "round",     # heavy | round | light | double
                 border_color: Optional[str] = None,  # Rich color name
                 padding_h: int = 1,
                 padding_v: int = 0,
                 id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self._child = child
        self._title = title
        self._border_style = border_style
        self._border_color = border_color
        self._padding_h = max(0, padding_h)
        self._padding_v = max(0, padding_v)

    def render(self, width: int, height: int) -> Frame:
        if width < 2 or height < 2:
            return Frame.empty(max(0, width), max(0, height))

        tl, tr, bl, br, h, v = _BORDER_STYLES.get(
            self._border_style, _BORDER_STYLES["round"]
        )
        inner_w = max(0, width - 2 - 2 * self._padding_h)
        inner_h = max(0, height - 2 - 2 * self._padding_v)

        # Render child into the inner rect.
        child_frame = self._child.render(inner_w, inner_h)
        # Defensive resize.
        from daimon.ui.layout import _coerce_size
        child_frame = _coerce_size(child_frame, inner_w, inner_h)

        # Translate child overlays + hits to absolute coords inside the panel
        # (border + padding offsets).
        child_offset_row = 1 + self._padding_v
        child_offset_col = 1 + self._padding_h
        child_frame = child_frame.translated(child_offset_row, child_offset_col)

        # Build border rows.
        color_open = f"[{self._border_color}]" if self._border_color else ""
        color_close = f"[/{self._border_color}]" if self._border_color else ""

        title_str = self._title or ""
        # Top border with optional title overlaid 2 cells from the left.
        top_inner = h * (width - 2)
        if title_str:
            label = f" {title_str} "
            label_visible = visible_len(label)
            # Truncate the title if it would overflow.
            if label_visible > width - 4:
                label = " " + truncate_visible(title_str, width - 6) + " "
                label_visible = visible_len(label)
            # Place the label starting at col 2 (one corner + one edge in).
            top_inner = (
                h + label + h * (width - 2 - 1 - label_visible)
            )
        top_row = render_markup(f"{color_open}{tl}{top_inner}{tr}{color_close}")
        bottom_row = render_markup(
            f"{color_open}{bl}{h * (width - 2)}{br}{color_close}"
        )

        # Left/right edges.
        left = render_markup(f"{color_open}{v}{color_close}")
        right = render_markup(f"{color_open}{v}{color_close}")
        edge_pad_left = " " * self._padding_h
        edge_pad_right = " " * self._padding_h

        rows: List[str] = [top_row]
        # Padding rows above the child.
        for _ in range(self._padding_v):
            rows.append(left + " " * (width - 2) + right)
        for child_row in child_frame.rows:
            # child_row already has visible width = inner_w
            rows.append(left + edge_pad_left + child_row + edge_pad_right + right)
        for _ in range(self._padding_v):
            rows.append(left + " " * (width - 2) + right)
        rows.append(bottom_row)

        # Pad/truncate to exactly height.
        while len(rows) < height:
            rows.append(left + " " * (width - 2) + right)
        if len(rows) > height:
            rows = rows[:height - 1] + [bottom_row]

        return Frame(
            rows=tuple(rows),
            width=width,
            height=height,
            overlays=child_frame.overlays,
            hit_regions=child_frame.hit_regions,
        )


# ---------------------------------------------------------------------------
# Button — focusable card-style widget with a hit region for mouse clicks
# ---------------------------------------------------------------------------


class Button(Widget):
    """Focusable, clickable card.

    Renders as a multi-line block with the icon on top, label in the middle,
    optional hotkey hint and detail line below. The frame includes a single
    HitRegion covering the whole card so a mouse click activates it.

    Visual states (in CSS-like terms):
      * normal  — dim border, default colors
      * hovered — accent color (set by the screen when the mouse is over it)
      * focused — accent border + bold label (set when the screen registers
        focus on this button)
    """

    focusable: bool = True

    def __init__(self,
                 *,
                 action: str,
                 icon: str = "",
                 label: str = "",
                 hotkey: str = "",
                 detail: str = "",
                 focused: bool = False,
                 highlighted: bool = False,
                 accent_color: str = "yellow",
                 dim_color: str = "grey50",
                 id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self.action = action
        self._icon = icon
        self._label = label
        self._hotkey = hotkey
        self._detail = detail
        self._focused = focused
        self._highlighted = highlighted
        self._accent = accent_color
        self._dim = dim_color

    def render(self, width: int, height: int) -> Frame:
        # Border + colors depend on focus state.
        if self._focused:
            border_style = "heavy"
            border_color = self._accent
            label_color = self._accent
            icon_color = self._accent
        elif self._highlighted:
            border_style = "round"
            border_color = self._accent
            label_color = self._accent
            icon_color = self._accent
        else:
            border_style = "round"
            border_color = self._dim
            label_color = "white"
            icon_color = "white"

        # Compose interior content.
        icon_line = (
            f"[bold {icon_color}]{self._icon}[/bold {icon_color}]"
            if self._icon else ""
        )
        label_line = (
            f"[bold {label_color}]{self._label}[/bold {label_color}]"
            if self._label else ""
        )
        hotkey_line = (
            f"[{self._dim}]({self._hotkey})[/{self._dim}]"
            if self._hotkey else ""
        )
        detail_line = self._detail or ""

        # Vertical centering: stack non-empty lines.
        lines = [s for s in (icon_line, label_line, hotkey_line, detail_line)
                 if s]
        body = "\n".join(lines)

        inner = Static(body, align="center", valign="middle")
        panel = Panel(
            inner,
            border_style=border_style,
            border_color=border_color,
            padding_h=1,
            padding_v=0,
        )
        frame = panel.render(width, height)

        # Add the hit region covering the whole button (excluding the border
        # is fine too, but covering the whole rect is more forgiving).
        hit = HitRegion(
            row_start=0, row_end=height,
            col_start=0, col_end=width,
            action=self.action,
            widget_id=self.id,
        )
        return frame.with_hit(hit)


# ---------------------------------------------------------------------------
# ProgressBar — a single-line filled bar with optional caption
# ---------------------------------------------------------------------------


class ProgressBar(Widget):
    """One-row progress bar.

    Fills the bar to ``progress / total`` of the width using block characters.
    ``caption`` (if set) is overlaid on top of the bar in the center.
    """

    def __init__(self,
                 progress: int,
                 total: int,
                 *,
                 caption: Optional[str] = None,
                 fill_color: str = "yellow",
                 dim_color: str = "grey39",
                 id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self._progress = max(0, progress)
        self._total = max(1, total)
        self._caption = caption
        self._fill = fill_color
        self._dim = dim_color

    def render(self, width: int, height: int) -> Frame:
        if width <= 0 or height <= 0:
            return Frame.empty(max(0, width), max(0, height))

        ratio = min(1.0, self._progress / self._total)
        fill_cells = int(ratio * width)
        empty_cells = width - fill_cells
        bar = "█" * fill_cells + "░" * empty_cells

        # Center caption on top of the bar — we render an overlay row with
        # the caption padded to width and replace the corresponding chars.
        if self._caption:
            cap = pad_visible(self._caption, width, align="center")
            # The caption replaces the bar entirely on its row (the bar isn't
            # interesting if we're showing a caption — keeps it readable).
            row = render_markup(
                f"[{self._fill}]{cap}[/{self._fill}]"
            )
        else:
            row = (
                render_markup(f"[{self._fill}]{'█' * fill_cells}[/{self._fill}]")
                + render_markup(f"[{self._dim}]{'░' * empty_cells}[/{self._dim}]")
            )

        # ProgressBar is logically 1 row — pad vertical to fill the requested
        # height with blanks (so it can sit inside a taller container).
        rows: List[str] = []
        blank = " " * width
        if height > 1:
            top_pad = (height - 1) // 2
            bottom_pad = height - 1 - top_pad
            rows = [blank] * top_pad + [row] + [blank] * bottom_pad
        else:
            rows = [row]

        return Frame.from_rows(rows, width=width, height=height)


# ---------------------------------------------------------------------------
# Tiny convenience: build a row of buttons all at once
# ---------------------------------------------------------------------------


def button_row(buttons: Sequence[Button], *, gap: int = 1) -> Widget:
    """Return an HBox of buttons with equal flex weight + given gap."""
    from daimon.ui.layout import HBox  # local import to avoid cycle at module load
    return HBox([(b, 1) for b in buttons], gap=gap)
