"""Layout containers — HBox, VBox, Pad.

These are Widgets whose render() composes child Widgets into a single Frame.
The composition contract:

  * Each child gets a sub-rect of the parent's render area, sized according
    to flex weights. The child's render() must produce a Frame of EXACTLY
    that sub-rect's dimensions.
  * The parent stitches the child rows together (left-to-right for HBox,
    top-to-bottom for VBox) and aggregates the children's overlays +
    hit_regions, translated to absolute coordinates within the composite.
  * Gaps between children are blank cells, no overlays, no hit regions.

Flex weight math is integer division of (available_size / total_weight)
with leftover pixels distributed to the trailing children — keeps layout
deterministic and pixel-stable across resizes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

from daimon.play.tui_style import pad_visible
from daimon.ui.frame import Frame, HitRegion
from daimon.ui.widget import Widget


# Public child spec: either a bare Widget (weight=1) or (Widget, weight) tuple.
ChildSpec = Union[Widget, Tuple[Widget, int]]


def _normalize_children(specs: Sequence[ChildSpec]) -> List[Tuple[Widget, int]]:
    out: List[Tuple[Widget, int]] = []
    for spec in specs:
        if isinstance(spec, tuple):
            widget, weight = spec
            out.append((widget, max(1, int(weight))))
        else:
            out.append((spec, 1))
    return out


def _distribute(total: int, weights: List[int]) -> List[int]:
    """Split ``total`` across ``weights`` with integer arithmetic.

    Leftover (from rounding) goes to the trailing slots so the sum is
    exactly ``total`` and earlier slots get their honest share. Used by
    HBox/VBox to compute each child's allocated size.
    """
    if not weights or total <= 0:
        return [0] * len(weights)
    total_w = sum(weights)
    base = [(total * w) // total_w for w in weights]
    leftover = total - sum(base)
    # Distribute the leftover one cell at a time, biased to the larger weights
    # first (so a 3:1 split with leftover=1 gives 3 the extra cell, not 1).
    order = sorted(range(len(weights)), key=lambda i: -weights[i])
    for i in range(leftover):
        base[order[i % len(order)]] += 1
    return base


# ---------------------------------------------------------------------------
# HBox
# ---------------------------------------------------------------------------


class HBox(Widget):
    """Horizontal layout — children placed left-to-right.

    Args:
        children: sequence of Widget or (Widget, weight) — defaults weight 1.
        gap: blank cells between adjacent children (default 0).
    """

    def __init__(self,
                 children: Sequence[ChildSpec],
                 *,
                 gap: int = 0,
                 id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self._children = _normalize_children(children)
        self._gap = max(0, gap)

    def render(self, width: int, height: int) -> Frame:
        if not self._children:
            return Frame.empty(width, height)

        n = len(self._children)
        # Reserve gap cells before distributing the rest by weight.
        gap_total = self._gap * (n - 1) if n > 1 else 0
        avail = max(0, width - gap_total)
        weights = [w for _, w in self._children]
        col_widths = _distribute(avail, weights)

        # Render each child at its allocated (width, full-height) rect.
        child_frames: List[Frame] = []
        for (widget, _), cw in zip(self._children, col_widths):
            cf = widget.render(cw, height)
            # Defensive — widgets are supposed to honor the requested size,
            # but if they slip we pad/truncate to keep layout stable.
            cf = _coerce_size(cf, cw, height)
            child_frames.append(cf)

        # Stitch rows + accumulate overlays/hits with absolute coords.
        gap_str = " " * self._gap
        composed_rows: List[str] = []
        for r in range(height):
            parts: List[str] = []
            for i, cf in enumerate(child_frames):
                parts.append(cf.rows[r])
                if i < n - 1 and self._gap:
                    parts.append(gap_str)
            composed_rows.append("".join(parts))

        overlays = []
        hits: List[HitRegion] = []
        col_offset = 0
        for cf, cw in zip(child_frames, col_widths):
            shifted = cf.translated(0, col_offset)
            overlays.extend(shifted.overlays)
            hits.extend(shifted.hit_regions)
            col_offset += cw + self._gap

        return Frame(
            rows=tuple(composed_rows),
            width=width,
            height=height,
            overlays=tuple(overlays),
            hit_regions=tuple(hits),
        )


# ---------------------------------------------------------------------------
# VBox
# ---------------------------------------------------------------------------


class VBox(Widget):
    """Vertical layout — children stacked top-to-bottom."""

    def __init__(self,
                 children: Sequence[ChildSpec],
                 *,
                 gap: int = 0,
                 id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self._children = _normalize_children(children)
        self._gap = max(0, gap)

    def render(self, width: int, height: int) -> Frame:
        if not self._children:
            return Frame.empty(width, height)

        n = len(self._children)
        gap_total = self._gap * (n - 1) if n > 1 else 0
        avail = max(0, height - gap_total)
        weights = [w for _, w in self._children]
        row_heights = _distribute(avail, weights)

        child_frames: List[Frame] = []
        for (widget, _), rh in zip(self._children, row_heights):
            cf = widget.render(width, rh)
            cf = _coerce_size(cf, width, rh)
            child_frames.append(cf)

        composed_rows: List[str] = []
        gap_row = " " * width
        for i, cf in enumerate(child_frames):
            composed_rows.extend(cf.rows)
            if i < n - 1:
                for _ in range(self._gap):
                    composed_rows.append(gap_row)

        overlays = []
        hits: List[HitRegion] = []
        row_offset = 0
        for cf, rh in zip(child_frames, row_heights):
            shifted = cf.translated(row_offset, 0)
            overlays.extend(shifted.overlays)
            hits.extend(shifted.hit_regions)
            row_offset += rh + self._gap

        return Frame(
            rows=tuple(composed_rows),
            width=width,
            height=height,
            overlays=tuple(overlays),
            hit_regions=tuple(hits),
        )


# ---------------------------------------------------------------------------
# Pad — simple margin wrapper around a single child
# ---------------------------------------------------------------------------


class Pad(Widget):
    """Wrap a single child with margins on each side.

    Useful as a thin spacer: ``Pad(MyWidget(), top=1, left=2)``. Margins
    eat into the parent's allocated size — the child receives
    ``(width - left - right, height - top - bottom)``.
    """

    def __init__(self,
                 child: Widget,
                 *,
                 top: int = 0,
                 right: int = 0,
                 bottom: int = 0,
                 left: int = 0,
                 id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self._child = child
        self._top = max(0, top)
        self._right = max(0, right)
        self._bottom = max(0, bottom)
        self._left = max(0, left)

    def render(self, width: int, height: int) -> Frame:
        inner_w = max(0, width - self._left - self._right)
        inner_h = max(0, height - self._top - self._bottom)
        cf = self._child.render(inner_w, inner_h)
        cf = _coerce_size(cf, inner_w, inner_h)
        cf = cf.translated(self._top, self._left)

        # Build the padded rows: top blanks, then [pad-left + child-row + pad-right],
        # then bottom blanks.
        rows: List[str] = []
        blank = " " * width
        for _ in range(self._top):
            rows.append(blank)
        left_pad = " " * self._left
        right_pad = " " * self._right
        for child_row in cf.rows:
            rows.append(left_pad + child_row + right_pad)
        for _ in range(self._bottom):
            rows.append(blank)

        return Frame(
            rows=tuple(rows),
            width=width,
            height=height,
            overlays=cf.overlays,
            hit_regions=cf.hit_regions,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_size(frame: Frame, width: int, height: int) -> Frame:
    """Defensive padding/truncation — keep child Frames at requested size.

    Widgets are supposed to honor (width, height) exactly, but a misbehaving
    widget shouldn't break layout for everything else. Pad short rows with
    spaces, truncate long ones, and clip to ``height`` rows.
    """
    if frame.width == width and frame.height == height \
            and all(len(r) >= width for r in frame.rows) \
            and len(frame.rows) == height:
        return frame
    rows: List[str] = []
    for r in frame.rows[:height]:
        rows.append(pad_visible(r, width))
    while len(rows) < height:
        rows.append(" " * width)
    return Frame(
        rows=tuple(rows),
        width=width,
        height=height,
        overlays=frame.overlays,
        hit_regions=frame.hit_regions,
    )
