"""Daily shop rotation — deterministic per-pubkey, refreshes 00:00 UTC.

Selection algorithm (memoryless — yesterday's slots are not reserved):

  1. Filter the full skin pool down to skins this player doesn't already own.
  2. Partition the remaining pool by rarity (rare vs super_rare).
  3. Within each rarity bucket, deterministically shuffle using a
     ``HMAC_SHA256(pubkey, "YYYY-MM-DD")``-seeded RNG.
  4. Take the first 4 from rare + first 2 from super_rare → 6 slots.

Properties:
  - Two players see different shops on the same day (different pubkey →
    different HMAC seed → different shuffle).
  - The same player sees a fresh shop every UTC midnight.
  - If the player owns ALL rare skins, the rare bucket simply yields 0
    slots that day (no padding from super_rare); the shop window can
    return fewer than 6 slots when the pool is exhausted.
  - The shuffle is stable across processes — same (pubkey, date, owned
    set) → same slot list. This means the "list slots" call and the
    "purchase slot N" call are guaranteed to agree on what slot N is, as
    long as no purchase has happened in between (a purchase removes the
    bought skin from `owned`, which would shift later slots — see
    ``purchase_slot`` for the re-check).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from daimon.shop.listings import SkinListing, load_skin_pool

# ---------------------------------------------------------------------------
# Pricing & quotas — single source of truth
# ---------------------------------------------------------------------------

PRICE_RARE = 300
PRICE_SUPER_RARE = 800

# 4 rare + 2 super_rare = 6 daily slots
RARE_SLOTS = 4
SUPER_RARE_SLOTS = 2
SLOTS_PER_DAY = RARE_SLOTS + SUPER_RARE_SLOTS

# Marvel Snap parallel: prevents whales-vs-grind imbalance for V1.
WEEKLY_CAP = 5


def price_for(rarity: str) -> int:
    """Resolve the ¤ cost for a shop rarity. Raises on unknown rarity."""
    if rarity == "rare":
        return PRICE_RARE
    if rarity == "super_rare":
        return PRICE_SUPER_RARE
    raise ValueError(f"unknown shop rarity: {rarity!r}")


# ---------------------------------------------------------------------------
# Slot model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RotationSlot:
    """One offered slot in today's shop."""

    index: int          # 0-based slot index for purchase addressing
    listing: SkinListing
    cost: int

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "cost": self.cost,
            **self.listing.to_dict(),
        }


# ---------------------------------------------------------------------------
# Determinism primitives
# ---------------------------------------------------------------------------

def daily_seed(pubkey_hex: str, day: _dt.date) -> bytes:
    """HMAC-SHA256(pubkey_hex, "YYYY-MM-DD"). 32 bytes."""
    msg = day.strftime("%Y-%m-%d").encode("ascii")
    key = pubkey_hex.encode("ascii")
    return hmac.new(key, msg, hashlib.sha256).digest()


def _drbg_uint32(seed: bytes, label: bytes, idx: int) -> int:
    """Deterministic uint32 stream from (seed, label, index). Used by the
    Fisher-Yates shuffle below — separate from the catalog DRBG so they
    can't accidentally shadow each other."""
    h = hashlib.sha256()
    h.update(seed)
    h.update(b"|")
    h.update(label)
    h.update(b"|")
    h.update(idx.to_bytes(8, "big"))
    return int.from_bytes(h.digest()[:4], "big")


def _deterministic_shuffle(items: List[SkinListing], seed: bytes,
                           label: bytes) -> List[SkinListing]:
    """Fisher-Yates shuffle keyed on (seed, label). Returns a NEW list."""
    out = list(items)
    n = len(out)
    for i in range(n - 1, 0, -1):
        j = _drbg_uint32(seed, label, i) % (i + 1)
        out[i], out[j] = out[j], out[i]
    return out


# ---------------------------------------------------------------------------
# Public rotation API
# ---------------------------------------------------------------------------

def _today_utc(now: Optional[_dt.datetime] = None) -> _dt.date:
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc).date()


def _owned_keys(owned: List[Tuple[str, str]] | List) -> Set[Tuple[str, str]]:
    """Normalize an owned-skin spec into a (card_id, skin_slug) key set.

    Accepts either:
      - a list of (card_id, skin_slug) tuples
      - a list of OwnedSkin dataclass instances (uses .key)
    """
    out: Set[Tuple[str, str]] = set()
    for entry in owned:
        if isinstance(entry, tuple) and len(entry) == 2:
            out.add((str(entry[0]), str(entry[1])))
        elif hasattr(entry, "key"):
            out.add(entry.key)
        elif hasattr(entry, "card_id") and hasattr(entry, "skin_slug"):
            out.add((entry.card_id, entry.skin_slug))
    return out


def current_rotation(
    pubkey_hex: str,
    *,
    owned: Optional[List] = None,
    pool: Optional[List[SkinListing]] = None,
    now: Optional[_dt.datetime] = None,
    art_root: Optional[Path] = None,
) -> List[RotationSlot]:
    """Compute today's shop slots for a given pubkey.

    Args:
      pubkey_hex: the agent's identity public key (hex).
      owned: list of OwnedSkin / (card_id, skin_slug) tuples to filter out.
             Defaults to reading from `daimon.shop.owned.load_owned()`.
      pool: full skin pool to draw from. Defaults to walking the art-pack
            via `load_skin_pool`. Inject for tests.
      now: clock override for tests. Defaults to UTC now.
      art_root: art-pack dir override. Forwarded to `load_skin_pool` if pool
                is not provided.

    Returns slots in canonical order: 4 rare slots first (indices 0..3),
    then up to 2 super_rare slots (indices 4..5). When the player owns the
    entire bucket, the corresponding slot count is reduced — the shop never
    duplicates listings to pad out 6.
    """
    if pool is None:
        pool = load_skin_pool(art_root)
    if owned is None:
        from daimon.shop.owned import load_owned  # local import to avoid cycle
        owned = load_owned()
    owned_keys = _owned_keys(owned)

    day = _today_utc(now)
    seed = daily_seed(pubkey_hex, day)

    # Partition by rarity; deterministically sort each bucket BEFORE shuffle
    # so the shuffle input is process-stable. (load_skin_pool already sorts,
    # but we re-sort defensively in case a custom pool was injected.)
    rare = sorted([s for s in pool if s.rarity == "rare"
                   and (s.card_id, s.skin_slug) not in owned_keys])
    super_rare = sorted([s for s in pool if s.rarity == "super_rare"
                         and (s.card_id, s.skin_slug) not in owned_keys])

    rare_shuffled = _deterministic_shuffle(rare, seed, b"rare")
    super_shuffled = _deterministic_shuffle(super_rare, seed, b"super_rare")

    selected: List[SkinListing] = (
        rare_shuffled[:RARE_SLOTS] + super_shuffled[:SUPER_RARE_SLOTS]
    )

    return [
        RotationSlot(index=i, listing=lst, cost=price_for(lst.rarity))
        for i, lst in enumerate(selected)
    ]


def seconds_until_next_rotation(now: Optional[_dt.datetime] = None) -> int:
    """Seconds until the next 00:00 UTC. Always returns a positive int."""
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    now = now.astimezone(_dt.timezone.utc)
    tomorrow = (now + _dt.timedelta(days=1)).date()
    next_rot = _dt.datetime.combine(tomorrow, _dt.time.min,
                                    tzinfo=_dt.timezone.utc)
    delta = next_rot - now
    return max(1, int(delta.total_seconds()))
