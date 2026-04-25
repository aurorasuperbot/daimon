"""Daily shop rotation — deterministic per-pubkey, refreshes 00:00 UTC.

Selection algorithm (snapshot-stable — today's slots stay put even as you
buy them):

  1. Split owned skins by purchase time vs today's UTC midnight:
       - "owned-yesterday" (purchased before 00:00 UTC today) — these
         are excluded from the shuffle pool entirely.
       - "owned-today" (purchased on or after 00:00 UTC today) — these
         remain in the rotation, marked ``sold=True``.
  2. Filter the skin pool to remove owned-yesterday entries.
  3. Partition the remaining pool by rarity (rare vs super_rare).
  4. Within each rarity bucket, deterministically shuffle using a
     ``HMAC_SHA256(pubkey, "YYYY-MM-DD")``-seeded RNG.
  5. Take the first 4 from rare + first 2 from super_rare → 6 slots.
  6. For each selected slot, look up the owned-today set; if the
     listing's ``(card_id, skin_slug)`` is present, attach
     ``sold=True`` + the buy timestamp.

Why snapshot-stable:
  Earlier the rotation filtered owned skins unconditionally, so buying
  slot 0 caused later slots to shift left (slot 1 → 0, slot 2 → 1, etc).
  That broke the user's mental model — "I'll come back to slot 3" was a
  lie. The fix is to compute the rotation as of midnight, keep the
  layout fixed all day, and mark intra-day purchases with a ``sold``
  flag so the UI can render them as ``[OWNED]`` without shifting siblings.

Properties:
  - Two players see different shops on the same day (different pubkey →
    different HMAC seed → different shuffle).
  - The same player sees the SAME 6 slots all day; only the ``sold``
    flag changes as they buy.
  - At 00:00 UTC the next day a fresh rotation is computed against the
    new owned-yesterday set; today's purchases are now excluded.
  - If the player owns ALL rare skins as-of-midnight, the rare bucket
    yields 0 slots that day (no padding from super_rare); the shop can
    return fewer than 6 slots when the pool is exhausted.
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
    """One offered slot in today's shop.

    Slots are computed once per UTC day and stay at the same index all day.
    When the player buys a slot intra-day, the listing remains in place and
    is marked ``sold=True`` so the UI can render an ``[OWNED]`` placeholder
    without shifting siblings. At 00:00 UTC tomorrow the rotation is
    recomputed against the new owned set and today's purchase is excluded.
    """

    index: int          # 0-based slot index for purchase addressing
    listing: SkinListing
    cost: int
    sold: bool = False
    purchased_at: Optional[str] = None  # iso-ts of the intra-day buy, if sold

    def to_dict(self) -> dict:
        out = {
            "index": self.index,
            "cost": self.cost,
            "sold": self.sold,
            **self.listing.to_dict(),
        }
        if self.purchased_at is not None:
            out["purchased_at"] = self.purchased_at
        return out


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

def _now_utc(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc)


def _today_utc(now: Optional[_dt.datetime] = None) -> _dt.date:
    return _now_utc(now).date()


def _day_start_utc(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    """The 00:00 UTC instant of the current UTC day."""
    return _dt.datetime.combine(
        _today_utc(now), _dt.time.min, tzinfo=_dt.timezone.utc,
    )


def _parse_iso_ts(ts: str) -> Optional[_dt.datetime]:
    """Parse an RFC3339 / ISO timestamp. None if unparseable.

    The owned-skin file emits ``"%Y-%m-%dT%H:%M:%SZ"`` but we accept any
    ISO form Python's ``fromisoformat`` understands, plus the trailing-Z
    convention. Bad timestamps return None — callers MUST treat them as
    "purchase time unknown" rather than crashing.
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


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


def _split_owned_by_day_start(
    owned: List,
    day_start: _dt.datetime,
) -> Tuple[Set[Tuple[str, str]], dict]:
    """Split an owned-skin list into (yesterday-or-earlier keys, today-keys-with-ts).

    Returns:
      - ``yesterday_keys``: set of (card_id, skin_slug) purchased BEFORE
        ``day_start`` — these are removed from the rotation pool.
      - ``today_map``: dict[(card_id, skin_slug)] -> purchased_at iso str
        for purchases made on or after ``day_start``. These stay in the
        rotation, marked ``sold``.

    Tuple-form owned entries (no timestamp) are treated as yesterday — the
    safe default that preserves the legacy "filter owned" behavior for
    callers that don't care about intra-day stability.

    Owned entries with unparseable timestamps are also treated as yesterday
    (better to filter than to leak a sold marker for an unknown date).
    """
    yesterday: Set[Tuple[str, str]] = set()
    today_map: dict = {}
    for entry in owned:
        # Tuple form has no timestamp → assume yesterday.
        if isinstance(entry, tuple) and len(entry) == 2:
            yesterday.add((str(entry[0]), str(entry[1])))
            continue
        # Object form must expose .key + .purchased_at.
        if not (hasattr(entry, "key") and hasattr(entry, "purchased_at")):
            # Best effort: drop in yesterday bucket if we can derive a key.
            if hasattr(entry, "card_id") and hasattr(entry, "skin_slug"):
                yesterday.add((entry.card_id, entry.skin_slug))
            continue
        ts = _parse_iso_ts(entry.purchased_at)
        if ts is None or ts < day_start:
            yesterday.add(entry.key)
        else:
            today_map[entry.key] = entry.purchased_at
    return yesterday, today_map


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
      owned: list of OwnedSkin / (card_id, skin_slug) tuples. Entries
             purchased BEFORE today's 00:00 UTC are filtered out of the
             rotation; entries purchased ON OR AFTER 00:00 UTC stay in
             the rotation marked ``sold=True`` so slot indices don't
             shift across intra-day purchases. Tuple-form entries (no
             timestamp) are always treated as "before today" — the safe
             pre-snapshot fallback for callers that don't care about
             intra-day stability.
             Defaults to reading from `daimon.shop.owned.load_owned()`.
      pool: full skin pool to draw from. Defaults to walking the art-pack
            via `load_skin_pool`. Inject for tests.
      now: clock override for tests. Defaults to UTC now.
      art_root: art-pack dir override. Forwarded to `load_skin_pool` if pool
                is not provided.

    Returns slots in canonical order: 4 rare slots first (indices 0..3),
    then up to 2 super_rare slots (indices 4..5). When the player owns the
    entire bucket as-of-midnight, the corresponding slot count is reduced
    — the shop never duplicates listings to pad out 6.
    """
    if pool is None:
        pool = load_skin_pool(art_root)
    if owned is None:
        from daimon.shop.owned import load_owned  # local import to avoid cycle
        owned = load_owned()

    day = _today_utc(now)
    day_start = _day_start_utc(now)
    seed = daily_seed(pubkey_hex, day)

    yesterday_keys, today_map = _split_owned_by_day_start(owned, day_start)

    # Partition by rarity; deterministically sort each bucket BEFORE shuffle
    # so the shuffle input is process-stable. (load_skin_pool already sorts,
    # but we re-sort defensively in case a custom pool was injected.)
    rare = sorted([s for s in pool if s.rarity == "rare"
                   and (s.card_id, s.skin_slug) not in yesterday_keys])
    super_rare = sorted([s for s in pool if s.rarity == "super_rare"
                         and (s.card_id, s.skin_slug) not in yesterday_keys])

    rare_shuffled = _deterministic_shuffle(rare, seed, b"rare")
    super_shuffled = _deterministic_shuffle(super_rare, seed, b"super_rare")

    selected: List[SkinListing] = (
        rare_shuffled[:RARE_SLOTS] + super_shuffled[:SUPER_RARE_SLOTS]
    )

    out: List[RotationSlot] = []
    for i, lst in enumerate(selected):
        key = (lst.card_id, lst.skin_slug)
        sold_ts = today_map.get(key)
        out.append(RotationSlot(
            index=i,
            listing=lst,
            cost=price_for(lst.rarity),
            sold=sold_ts is not None,
            purchased_at=sold_ts,
        ))
    return out


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
