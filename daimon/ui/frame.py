"""Frame — the unit of rendering across daimon.ui.

A Frame is a fixed-size rectangle of rendered content: ANSI-colored text rows
plus optional KGP image overlays plus optional mouse hit regions. Widgets
return Frames; layout containers compose child Frames into bigger Frames;
Screens flush a top-level Frame to the terminal.

The contract is intentionally narrow:
  * ``rows`` are exactly ``height`` ANSI-colored strings, each with visible
    width ``width`` (use ``tui_style.pad_visible`` to enforce).
  * ``overlays`` are KGP image records in cell coordinates LOCAL TO THIS
    FRAME (0-indexed). When a layout container places this frame at offset
    (R, C) within itself, it calls :meth:`Frame.translated` to shift them.
  * ``hit_regions`` follow the same local-coordinate rule.

Everything in daimon.ui flows through Frame, so getting this dataclass
right is load-bearing — keep it boring.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional, Tuple

from daimon.play.screenshot import ImageOverlay


@dataclass(frozen=True)
class HitRegion:
    """A clickable rectangle within a Frame.

    Coordinates are 0-indexed cells, half-open: ``[row_start, row_end) ×
    [col_start, col_end)``. ``action`` is the action name dispatched to
    ``Screen.on_action`` when the player clicks inside the region.
    """

    row_start: int
    row_end: int
    col_start: int
    col_end: int
    action: str
    payload: Optional[Any] = None
    widget_id: Optional[str] = None

    def contains(self, row: int, col: int) -> bool:
        return (self.row_start <= row < self.row_end
                and self.col_start <= col < self.col_end)

    def translated(self, row_offset: int, col_offset: int) -> "HitRegion":
        return replace(
            self,
            row_start=self.row_start + row_offset,
            row_end=self.row_end + row_offset,
            col_start=self.col_start + col_offset,
            col_end=self.col_end + col_offset,
        )


def _translate_overlay(o: ImageOverlay,
                       row_offset: int,
                       col_offset: int) -> ImageOverlay:
    """Return a copy of ``o`` shifted by the given cell offset.

    ImageOverlay is a mutable dataclass so we construct a fresh instance
    rather than mutating in place — Frames are conceptually immutable.
    """
    return ImageOverlay(
        row=o.row + row_offset,
        col=o.col + col_offset,
        rows=o.rows,
        cols=o.cols,
        image_path=o.image_path,
        border_color=o.border_color,
        border_width=o.border_width,
        glow=o.glow,
        caption=o.caption,
        caption_color=o.caption_color,
    )


@dataclass(frozen=True)
class Frame:
    """A rendered region: text + KGP overlays + hit regions.

    Construct via :meth:`Frame.empty` or directly with explicit ``rows``.
    Layout containers (HBox / VBox / etc.) consume child Frames and produce
    composed Frames — see :mod:`daimon.ui.layout`.
    """

    rows: Tuple[str, ...]
    width: int
    height: int
    overlays: Tuple[ImageOverlay, ...] = field(default_factory=tuple)
    hit_regions: Tuple[HitRegion, ...] = field(default_factory=tuple)

    @classmethod
    def empty(cls, width: int, height: int) -> "Frame":
        """Empty frame of given dimensions — useful as a placeholder."""
        return cls(
            rows=tuple(" " * width for _ in range(height)),
            width=width,
            height=height,
        )

    @classmethod
    def from_rows(cls,
                  rows: Tuple[str, ...] | list,
                  width: int,
                  height: int,
                  *,
                  overlays: Tuple[ImageOverlay, ...] = (),
                  hit_regions: Tuple[HitRegion, ...] = (),
                  ) -> "Frame":
        """Build a Frame from a row sequence; pads/truncates to ``height``.

        Each row is left untouched (caller is responsible for padding to
        ``width`` — typically via ``tui_style.pad_visible``). If fewer than
        ``height`` rows are passed we pad with blank rows; more are truncated.
        """
        rows_t = tuple(rows)
        if len(rows_t) < height:
            blank = " " * width
            rows_t = rows_t + tuple(blank for _ in range(height - len(rows_t)))
        elif len(rows_t) > height:
            rows_t = rows_t[:height]
        return cls(
            rows=rows_t,
            width=width,
            height=height,
            overlays=tuple(overlays),
            hit_regions=tuple(hit_regions),
        )

    def translated(self, row_offset: int, col_offset: int) -> "Frame":
        """Return a Frame whose overlays + hit_regions are shifted by the offset.

        ``rows`` are unchanged — they remain a height-tall × width-wide block.
        Used by layout containers when placing this frame inside themselves;
        the parent stitches the rows side-by-side or top-to-bottom and then
        calls translated() so the child's hits/overlays land at the right
        absolute coordinates of the composite.
        """
        if row_offset == 0 and col_offset == 0:
            return self
        return Frame(
            rows=self.rows,
            width=self.width,
            height=self.height,
            overlays=tuple(
                _translate_overlay(o, row_offset, col_offset)
                for o in self.overlays
            ),
            hit_regions=tuple(
                h.translated(row_offset, col_offset) for h in self.hit_regions
            ),
        )

    def with_hit(self, hit: HitRegion) -> "Frame":
        """Return a copy with one extra hit region appended (at local coords)."""
        return replace(self, hit_regions=self.hit_regions + (hit,))

    def render_text(self) -> str:
        """Return the rows joined by newlines — what gets sent to the terminal."""
        return "\n".join(self.rows)
