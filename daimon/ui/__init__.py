"""daimon.ui — unified UI framework for all DAIMON game screens.

Public API:

    from daimon.ui import (
        # Core
        Frame, HitRegion, Widget,
        # Layout
        HBox, VBox, Pad,
        # Widgets
        Static, Panel, Button, ProgressBar, button_row,
        # Events
        KeyEvent, MouseEvent, MouseKind,
        # App
        Screen, GameApp,
        BindingTable, Binding,
    )

Build a screen by subclassing Screen, declaring a BindingTable, and
implementing compose() + signature() + on_action(). See
``daimon/play/menu_ui.py`` for a worked example.
"""

from daimon.ui.app import GameApp
from daimon.ui.bindings import Binding, BindingTable
from daimon.ui.events import KeyEvent, MouseEvent, MouseKind
from daimon.ui.frame import Frame, HitRegion
from daimon.ui.layout import HBox, Pad, VBox
from daimon.ui.screen import Screen
from daimon.ui.widget import Widget
from daimon.ui.widgets import Button, Panel, ProgressBar, Static, button_row

__all__ = [
    "Binding",
    "BindingTable",
    "Button",
    "Frame",
    "GameApp",
    "HBox",
    "HitRegion",
    "KeyEvent",
    "MouseEvent",
    "MouseKind",
    "Pad",
    "Panel",
    "ProgressBar",
    "Screen",
    "Static",
    "VBox",
    "Widget",
    "button_row",
]
