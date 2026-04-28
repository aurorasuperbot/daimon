"""Widget base — every UI component implements this contract.

A Widget is a renderable, optionally focusable, optionally clickable
component. Widgets are stateless renderers driven by external state; they
don't hold mutable UI state of their own (matches the existing daimon
``render_frame(view)`` pattern). The Screen layer owns the View; widgets
just render what they're told.

A widget's ``render(width, height)`` returns a Frame of EXACTLY those
dimensions. Layout containers ask child widgets for specific sizes when
composing; the contract being strict makes layout math deterministic.

Focus + click intent is declared via attributes on the widget (``focused``,
``action``) and surfaced by adding HitRegions to the returned Frame. The
Screen reads HitRegions out of the rendered Frame and dispatches mouse
clicks accordingly.
"""

from __future__ import annotations

from typing import Optional

from daimon.ui.frame import Frame


class Widget:
    """Base for all UI components.

    Subclasses MUST override :meth:`render`. Subclasses MAY set ``id`` (for
    debugging / queries) and ``focusable`` (defaults to False).

    Stateless on purpose: every render is a pure function of (config, size).
    The Screen owns the view model; widgets are constructed fresh on every
    render pass with the right ``focused`` / ``selected`` flags from the view.
    This matches how ``daimon/play/collection_ui.py`` already works.
    """

    #: Subclasses set True if they should be reachable via Tab / arrow focus.
    #: Has no effect unless the screen registers the widget with a FocusManager.
    focusable: bool = False

    def __init__(self, *, id: Optional[str] = None) -> None:
        self.id = id

    def render(self, width: int, height: int) -> Frame:
        """Return a Frame of exactly ``width × height`` cells.

        Implementations MUST honor the requested size — pad short content,
        truncate long content. The layout layer above relies on this
        invariant to stitch frames together without arithmetic surprises.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must override render()"
        )
