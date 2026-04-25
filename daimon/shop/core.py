"""Shop state + atomic purchase flow.

The single entry point for "show the shop" and "buy slot N". Both operations
are pure-local — no network, no arena round-trip. The ledger is the only
shared-state contact.

Purchase atomicity:

  1. Re-derive today's rotation (using the player's CURRENT owned set).
  2. Map the addressing arg (slot index OR skin_slug) to a SkinListing
     in that rotation. Reject if not present (defends against stale UI
     across the daily rollover).
  3. Verify the skin isn't already owned (paranoia — the rotation already
     filtered it, but a concurrent purchase could race).
  4. Verify the weekly cap (5 / Mon–Sun UTC) isn't exhausted.
  5. Verify balance ≥ price.
  6. Append `kind="purchase"` ledger entry — this is the authoritative
     spend. If this fails, NOTHING is recorded.
  7. Append the OwnedSkin entry to owned_skins.json with the ledger entry
     hash for provenance. If this fails, the ledger entry already landed —
     ``rebuild_owned_from_ledger`` (next call) repopulates the cache from
     ledger truth.

Idempotency: there's no naive idempotency key here because purchases are
inherently one-shot per (card_id, skin_slug). A retry of a successful
purchase will land on the "already owned" check in step 3 and raise
``AlreadyOwnedError``, which the caller surfaces as a benign no-op.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from daimon.identity import Identity, load_identity
from daimon.mining import ledger as _ledger_mod
from daimon.mining.ledger import (
    InsufficientBalanceError,
    append_purchase_entry,
    entry_hash,
    get_balance,
)
from daimon.shop.equipped import load_equipped
from daimon.shop.errors import (
    AlreadyOwnedError,
    SkinNotFoundError,
    SlotNotInRotationError,
    WeeklyCapExceededError,
)
from daimon.shop.listings import SkinListing, load_skin_pool
from daimon.shop.owned import (
    OWNED_PATH,
    OwnedSkin,
    append_owned,
    is_owned,
    load_owned,
    now_iso,
    weekly_purchase_count,
)
from daimon.shop.rotation import (
    SLOTS_PER_DAY,
    WEEKLY_CAP,
    RotationSlot,
    current_rotation,
    price_for,
    seconds_until_next_rotation,
)


# ---------------------------------------------------------------------------
# Public state types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShopState:
    """A snapshot of the shop as the player sees it right now."""

    pubkey_hex: str
    balance: int
    weekly_count: int                  # purchases made this ISO week
    weekly_cap: int                    # mirror of WEEKLY_CAP for clarity
    seconds_until_rotation: int
    slots: List[RotationSlot] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pubkey_hex": self.pubkey_hex,
            "balance": self.balance,
            "weekly_count": self.weekly_count,
            "weekly_cap": self.weekly_cap,
            "weekly_remaining": max(0, self.weekly_cap - self.weekly_count),
            "seconds_until_rotation": self.seconds_until_rotation,
            "slot_count": len(self.slots),
            "slots": [s.to_dict() for s in self.slots],
        }


@dataclass(frozen=True)
class PurchaseReceipt:
    """Returned by ``purchase_slot`` on success."""

    card_id: str
    skin_slug: str
    skin_name: str
    skin_axis: str
    rarity: str
    cost: int
    balance_after: int
    purchased_at: str
    ledger_entry_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "card_id": self.card_id,
            "skin_slug": self.skin_slug,
            "skin_name": self.skin_name,
            "skin_axis": self.skin_axis,
            "rarity": self.rarity,
            "cost": self.cost,
            "balance_after": self.balance_after,
            "purchased_at": self.purchased_at,
            "ledger_entry_hash": self.ledger_entry_hash,
        }


# ---------------------------------------------------------------------------
# Shop state
# ---------------------------------------------------------------------------

def get_shop_state(
    *,
    identity: Optional[Identity] = None,
    pool: Optional[List[SkinListing]] = None,
    art_root: Optional[Path] = None,
    owned_path: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
    now=None,
) -> ShopState:
    """Build today's shop snapshot for the local identity."""
    identity = identity or load_identity()
    if ledger_path is None:
        ledger_path = _ledger_mod.LEDGER_PATH

    owned = load_owned(owned_path)
    slots = current_rotation(
        identity.pubkey_hex,
        owned=owned,
        pool=pool,
        now=now,
        art_root=art_root,
    )
    bal = get_balance(ledger_path)
    weekly = weekly_purchase_count(now=now, path=owned_path)
    return ShopState(
        pubkey_hex=identity.pubkey_hex,
        balance=bal,
        weekly_count=weekly,
        weekly_cap=WEEKLY_CAP,
        seconds_until_rotation=seconds_until_next_rotation(now),
        slots=slots,
    )


# ---------------------------------------------------------------------------
# Purchase
# ---------------------------------------------------------------------------

def _resolve_slot(slots: List[RotationSlot],
                  selector: Union[int, str]) -> RotationSlot:
    """Translate `selector` → RotationSlot. Accepts:

      - int (or all-digit string): 0-based slot index.
      - "card_id/skin_slug" or "card_id:skin_slug": exact composite key.
      - "skin_slug": only valid if exactly one slot in today's rotation
        has this slug. Cultural skins re-use slugs across an entire canon,
        so a bare slug can be ambiguous — addressing with the composite
        form is required in that case.

    Raises:
      - SlotNotInRotationError: index out of range, no slot matches the
        composite key, or the bare slug matches more than one slot.
    """
    def _check_unsold(s: RotationSlot) -> RotationSlot:
        if s.sold:
            raise SlotNotInRotationError(
                f"slot {s.index} ({s.listing.card_id}/{s.listing.skin_slug}) "
                f"already purchased today; rotates at next 00:00 UTC"
            )
        return s

    # int / digit-string → slot index
    if isinstance(selector, int) or (isinstance(selector, str) and selector.isdigit()):
        idx = int(selector)
        if idx < 0 or idx >= len(slots):
            raise SlotNotInRotationError(
                f"slot {idx} out of range (0..{max(0, len(slots) - 1)})"
            )
        return _check_unsold(slots[idx])

    if not isinstance(selector, str):
        raise SlotNotInRotationError(
            f"invalid selector type: {type(selector).__name__}"
        )

    # composite "card_id/skin_slug" or "card_id:skin_slug"
    sep = "/" if "/" in selector else (":" if ":" in selector else None)
    if sep:
        card_id, _, slug = selector.partition(sep)
        if not card_id or not slug:
            raise SlotNotInRotationError(
                f"composite selector {selector!r} must be 'card_id{sep}skin_slug'"
            )
        for s in slots:
            if s.listing.card_id == card_id and s.listing.skin_slug == slug:
                return _check_unsold(s)
        raise SlotNotInRotationError(
            f"{card_id}{sep}{slug} not in today's rotation"
        )

    # bare slug — must be unambiguous
    matches = [s for s in slots if s.listing.skin_slug == selector]
    if not matches:
        raise SlotNotInRotationError(
            f"skin slug {selector!r} not in today's rotation"
        )
    if len(matches) > 1:
        raise SlotNotInRotationError(
            f"skin slug {selector!r} is ambiguous in today's rotation "
            f"({len(matches)} slots match — disambiguate with "
            f"'card_id/{selector}' or use the slot index)"
        )
    return _check_unsold(matches[0])


def purchase_slot(
    selector: Union[int, str],
    *,
    identity: Optional[Identity] = None,
    pool: Optional[List[SkinListing]] = None,
    art_root: Optional[Path] = None,
    owned_path: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
    now=None,
) -> PurchaseReceipt:
    """Buy one skin from today's rotation. Atomic at the ledger boundary.

    Args:
      selector: either a 0-based slot index (int) or a skin_slug (str).
                Slot index is ergonomic for CLI; skin_slug is more
                resilient to UI staleness across the daily rollover.

    Raises:
      - SlotNotInRotationError: selector doesn't resolve in today's rotation.
      - AlreadyOwnedError: the skin is already in the owned cache.
      - WeeklyCapExceededError: this purchase would push past the cap.
      - InsufficientBalanceError: balance < price.
      - FileNotFoundError: no identity (`daimon init` first).

    Side effects on success:
      - Ledger gets a new `kind="purchase"` entry (-cost, signed).
      - owned_skins.json gets a new entry with the ledger entry hash.
    """
    identity = identity or load_identity()
    if ledger_path is None:
        ledger_path = _ledger_mod.LEDGER_PATH

    # Re-derive rotation against current owned set + clock — defends against
    # stale UI between "show" and "buy" calls.
    owned = load_owned(owned_path)
    slots = current_rotation(
        identity.pubkey_hex,
        owned=owned,
        pool=pool,
        now=now,
        art_root=art_root,
    )

    slot = _resolve_slot(slots, selector)
    listing = slot.listing
    cost = slot.cost  # already mirrors price_for(rarity)

    # Already-owned guard. Belt-and-suspenders: rotation already filters,
    # but a multi-process race could slip past.
    if is_owned(listing.card_id, listing.skin_slug, owned_path):
        raise AlreadyOwnedError(
            f"already own {listing.skin_slug} on {listing.card_id}"
        )

    # Weekly cap.
    weekly = weekly_purchase_count(now=now, path=owned_path)
    if weekly >= WEEKLY_CAP:
        raise WeeklyCapExceededError(
            f"weekly cap {WEEKLY_CAP} reached ({weekly} this week); "
            f"resets Monday 00:00 UTC"
        )

    # Sanity: belt-check the cost matches the rarity (rotation built it from
    # price_for, but if a custom rotation injected mismatched prices we want
    # to reject loudly rather than silently miscount).
    expected = price_for(listing.rarity)
    if cost != expected:
        raise SlotNotInRotationError(
            f"slot price {cost} doesn't match rarity {listing.rarity!r} "
            f"(expected {expected})"
        )

    # Spend (this raises InsufficientBalanceError on its own).
    entry = append_purchase_entry(
        cost=cost,
        card_id=listing.card_id,
        skin_slug=listing.skin_slug,
        skin_axis=listing.skin_axis,
        rarity=listing.rarity,
        identity=identity,
        path=ledger_path,
    )
    eh = entry_hash(entry)

    # Persist the owned-skin cache (ledger is already authoritative).
    purchased_at = now_iso()
    owned_entry = OwnedSkin(
        card_id=listing.card_id,
        skin_slug=listing.skin_slug,
        skin_name=listing.skin_name,
        skin_axis=listing.skin_axis,
        rarity=listing.rarity,
        purchased_at=purchased_at,
        cost=cost,
        ledger_entry_hash=eh,
    )
    append_owned(owned_entry, path=owned_path)

    return PurchaseReceipt(
        card_id=listing.card_id,
        skin_slug=listing.skin_slug,
        skin_name=listing.skin_name,
        skin_axis=listing.skin_axis,
        rarity=listing.rarity,
        cost=cost,
        balance_after=get_balance(ledger_path),
        purchased_at=purchased_at,
        ledger_entry_hash=eh,
    )


# ---------------------------------------------------------------------------
# Re-export commonly-needed peers (so callers can `from daimon.shop.core
# import load_equipped`) — not strictly necessary, but keeps import sites
# tidy in CLI/MCP layers.
# ---------------------------------------------------------------------------
__all__ = [
    "ShopState", "PurchaseReceipt",
    "get_shop_state", "purchase_slot",
    "load_equipped",
]
