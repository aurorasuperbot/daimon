"""DAIMON skin shop — daily-rotating cosmetic marketplace.

V1 design: Marvel-Snap-style 6-slot daily rotation, deterministic per-pubkey,
spending the same ¤ currency that mining mints. Two price tiers: 300¤ for
cultural skins (rare), 800¤ for anatomical skins (super_rare). Hard cap of
5 purchases per ISO week.

See ``docs/shop_design.md`` (alongside this engine) for the full design.

Public surface — everything CLI/MCP layers should import from here:

    from daimon.shop import (
        # catalog
        SkinListing, iter_skins, load_skin_pool,
        # rotation
        RotationSlot, current_rotation, seconds_until_next_rotation,
        # ownership / equip
        OwnedSkin, load_owned, is_owned, list_owned,
        load_equipped, equip_skin, unequip_skin, get_equipped,
        # purchase
        ShopState, PurchaseReceipt, get_shop_state, purchase_slot,
        # constants
        PRICE_RARE, PRICE_SUPER_RARE, WEEKLY_CAP,
        SLOTS_PER_DAY, RARE_SLOTS, SUPER_RARE_SLOTS,
        # errors
        ShopError, AlreadyOwnedError, WeeklyCapExceededError,
        SlotNotInRotationError, SkinNotFoundError, NotOwnedError,
    )
"""

from daimon.shop.core import (
    PurchaseReceipt,
    ShopState,
    get_shop_state,
    purchase_slot,
)
from daimon.shop.errors import (
    AlreadyOwnedError,
    NotOwnedError,
    ShopError,
    SkinNotFoundError,
    SlotNotInRotationError,
    WeeklyCapExceededError,
)
from daimon.shop.equipped import (
    equip_skin,
    get_equipped,
    load_equipped,
    unequip_skin,
)
from daimon.shop.listings import (
    SkinListing,
    iter_skins,
    load_skin_pool,
)
from daimon.shop.owned import (
    OwnedSkin,
    is_owned,
    list_owned,
    load_owned,
    weekly_purchase_count,
)
from daimon.shop.rotation import (
    PRICE_RARE,
    PRICE_SUPER_RARE,
    RARE_SLOTS,
    SLOTS_PER_DAY,
    SUPER_RARE_SLOTS,
    WEEKLY_CAP,
    RotationSlot,
    current_rotation,
    seconds_until_next_rotation,
)

__all__ = [
    # listings
    "SkinListing", "iter_skins", "load_skin_pool",
    # rotation
    "RotationSlot", "current_rotation", "seconds_until_next_rotation",
    # ownership / equip
    "OwnedSkin", "load_owned", "is_owned", "list_owned",
    "weekly_purchase_count",
    "load_equipped", "equip_skin", "unequip_skin", "get_equipped",
    # purchase
    "ShopState", "PurchaseReceipt", "get_shop_state", "purchase_slot",
    # constants
    "PRICE_RARE", "PRICE_SUPER_RARE", "WEEKLY_CAP",
    "SLOTS_PER_DAY", "RARE_SLOTS", "SUPER_RARE_SLOTS",
    # errors
    "ShopError", "AlreadyOwnedError", "WeeklyCapExceededError",
    "SlotNotInRotationError", "SkinNotFoundError", "NotOwnedError",
]
