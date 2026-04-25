"""Shop error hierarchy.

All shop-side errors derive from `ShopError` so callers can catch the family
with one except. The MCP layer maps these to its `{"error": "..."}` envelope
shape; the CLI layer prints them as user-facing messages.
"""

from __future__ import annotations


class ShopError(Exception):
    """Base for all shop errors. Carries a stable `code` for MCP envelopes."""

    code: str = "shop_error"


class SkinNotFoundError(ShopError):
    """Raised when a skin slug doesn't resolve to any catalog listing."""

    code = "skin_not_found"


class SlotNotInRotationError(ShopError):
    """Raised when a slot index / skin_slug isn't in today's rotation.

    Defends against stale UI: a player who fetched yesterday's rotation and
    then calls `purchase_slot(2)` after the daily roll happened won't get
    a stale skin.
    """

    code = "slot_not_in_rotation"


class AlreadyOwnedError(ShopError):
    """Raised when trying to purchase a skin you already own."""

    code = "already_owned"


class NotOwnedError(ShopError):
    """Raised when trying to equip a skin you don't own."""

    code = "not_owned"


class WeeklyCapExceededError(ShopError):
    """Raised when this purchase would push past the weekly cap."""

    code = "weekly_cap_exceeded"
