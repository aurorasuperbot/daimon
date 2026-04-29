"""Tests for the skin shop — V1 cosmetic marketplace.

Covers all five layers:

  * `listings` — art-pack walk + skin discovery
  * `rotation` — daily seed determinism + per-pubkey divergence
  * `owned` — per-skin persistence + ISO-week cap counting
  * `equipped` — equip/unequip + ownership gating
  * `core` — full purchase flow (atomicity, all guards)

Plus the MCP envelopes (`dm_shop`, `dm_shop_buy`, `dm_skins_owned`,
`dm_skin_equip`, `dm_skin_unequip`).

Test strategy: each test builds a small synthetic art-pack on `tmp_path`
so we don't depend on the 400-PNG production pack being on disk. The
art-pack format is stable (manifest.json + variants/<vid>.png), so the
synthetic pack exercises the same code paths as the real one.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import List

import pytest

from daimon.mcp import server as mcp_server
from daimon.mcp.server import (
    dm_shop,
    dm_shop_buy,
    dm_skin_equip,
    dm_skin_unequip,
    dm_skins_owned,
)


def _call(tool, **kwargs):
    fn = getattr(tool, "fn", tool)
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Fixtures: synthetic art-pack + path isolation
# ---------------------------------------------------------------------------

def _png(card_dir: Path, vid: str) -> Path:
    """Write a 1×1 PNG (real bytes) to make `art_path.is_file()` pass."""
    p = card_dir / "variants" / f"{vid}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    # 1×1 transparent PNG header — 67 bytes, smallest valid PNG.
    p.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000d4944415478da63fa0f00000100015b8c1d480000000049454e44ae426082"
        )
    )
    return p


def _write_card(art_root: Path, card_id: str, skins: List[dict]) -> None:
    """Build one synthetic card directory: manifest.json + v0 base + skin variants.

    `skins` is a list of dicts, each:
      {"slug": "...", "name": "...", "axis": "cultural"|"anatomical",
       "rarity": "rare"|"super_rare"}
    """
    card_dir = art_root / card_id
    card_dir.mkdir(parents=True, exist_ok=True)

    variants = [{
        "id": "v0", "seed": 0, "seed_offset": 0,
        "created_at": "2026-04-23T00:00:00Z",
        "status": "active",
        "model": "test", "prompt_version": "test_v1",
    }]
    _png(card_dir, "v0")
    (card_dir / "base.png").write_bytes((card_dir / "variants" / "v0.png").read_bytes())

    for i, sk in enumerate(skins, start=1):
        vid = f"v{i}"
        variants.append({
            "id": vid, "seed": 100 + i, "seed_offset": 0,
            "created_at": "2026-04-24T00:00:00Z",
            "status": "active",
            "model": "test", "prompt_version": "skin_v1_2axis",
            "kind": "skin",
            "skin_slug": sk["slug"],
            "skin_name": sk["name"],
            "skin_axis": sk["axis"],
            "rarity": sk["rarity"],
        })
        _png(card_dir, vid)

    (card_dir / "manifest.json").write_text(json.dumps({
        "card_id": card_id,
        "canonical": "v0",
        "variants": variants,
    }, indent=2), encoding="utf-8")


def _build_synthetic_pack(art_root: Path, *, rare_cards: int = 6,
                          super_rare_cards: int = 4) -> None:
    """Build a synthetic pack with `rare_cards` rare-skinned cards and
    `super_rare_cards` super_rare-skinned cards, each skinned with both axes."""
    art_root.mkdir(parents=True, exist_ok=True)
    for i in range(rare_cards):
        _write_card(art_root, f"card_rare_{i:02d}", [
            {"slug": f"cultural_{i:02d}", "name": f"Cultural {i:02d}",
             "axis": "cultural", "rarity": "rare"},
        ])
    for i in range(super_rare_cards):
        _write_card(art_root, f"card_super_{i:02d}", [
            {"slug": f"anatomical_{i:02d}", "name": f"Anatomical {i:02d}",
             "axis": "anatomical", "rarity": "super_rare"},
        ])


@pytest.fixture
def shop_env(monkeypatch, tmp_path):
    """Isolate paths + build a synthetic art-pack. Returns (cfg_dir, art_root)."""
    from daimon.identity import keys as identity_keys
    from daimon.mining import ledger as ledger_mod
    from daimon.shop import equipped as equipped_mod
    from daimon.shop import owned as owned_mod

    cfg = tmp_path / "config"
    cfg.mkdir()
    art_root = tmp_path / "art_pack"

    # Identity + ledger
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "mining_ledger.jsonl")

    # Shop persistence
    monkeypatch.setattr(owned_mod, "OWNED_PATH", cfg / "owned_skins.json")
    monkeypatch.setattr(equipped_mod, "EQUIPPED_PATH",
                        cfg / "equipped_skins.json")

    # MCP server's own re-bound paths (it caches some at import time).
    monkeypatch.setattr(mcp_server, "LEDGER_PATH",
                        cfg / "mining_ledger.jsonl")

    # Art-pack
    monkeypatch.setenv("DAIMON_ART_DIR", str(tmp_path))
    # The pack lives at <DAIMON_ART_DIR>/art/v1_alpha by convention.
    pack_root = tmp_path / "art" / "v1_alpha"
    _build_synthetic_pack(pack_root)

    return cfg, pack_root


def _seed_balance(amount: int) -> None:
    """Mine N hundred ¤ into the active ledger."""
    from daimon.mining.ledger import append_mine_entry
    n = amount // 100
    for i in range(n):
        append_mine_entry(
            tool_name="Bash", amount=100, factors={},
            novelty_key=f"k{i}", idempotency_key=f"ik{i}",
        )


# ---------------------------------------------------------------------------
# 1. Listings — catalog discovery
# ---------------------------------------------------------------------------

def test_listings_loads_synthetic_pack(shop_env):
    from daimon.shop import load_skin_pool
    pool = load_skin_pool()
    # 6 cultural + 4 anatomical → 10 skins
    assert len(pool) == 10
    by_axis = {}
    for s in pool:
        by_axis[s.skin_axis] = by_axis.get(s.skin_axis, 0) + 1
    assert by_axis == {"cultural": 6, "anatomical": 4}


def test_listings_skips_inactive_variants(shop_env, tmp_path):
    """A variant with status=discarded must NOT appear in the pool."""
    pack_root = tmp_path / "art" / "v1_alpha"
    # Patch one card's manifest to mark its skin as discarded.
    mp = pack_root / "card_rare_00" / "manifest.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    for v in m["variants"]:
        if v.get("kind") == "skin":
            v["status"] = "discarded"
    mp.write_text(json.dumps(m))

    from daimon.shop import load_skin_pool
    pool = load_skin_pool()
    slugs = [s.skin_slug for s in pool]
    assert "cultural_00" not in slugs


def test_listings_skips_when_png_missing(shop_env, tmp_path):
    pack_root = tmp_path / "art" / "v1_alpha"
    (pack_root / "card_rare_01" / "variants" / "v1.png").unlink()

    from daimon.shop import load_skin_pool
    pool = load_skin_pool()
    slugs = [s.skin_slug for s in pool]
    assert "cultural_01" not in slugs


def test_listings_skips_corrupt_manifest(shop_env, tmp_path):
    pack_root = tmp_path / "art" / "v1_alpha"
    (pack_root / "card_rare_02" / "manifest.json").write_text("{not valid")

    from daimon.shop import load_skin_pool
    pool = load_skin_pool()
    # Other 5 cultural + 4 anatomical = 9 (lost cultural_02).
    assert len(pool) == 9


def test_listings_deterministic_sort(shop_env):
    from daimon.shop import load_skin_pool
    p1 = load_skin_pool()
    p2 = load_skin_pool()
    assert [s.skin_slug for s in p1] == [s.skin_slug for s in p2]


# ---------------------------------------------------------------------------
# 2. Rotation — daily determinism + per-pubkey divergence
# ---------------------------------------------------------------------------

PK_A = "a" * 64
PK_B = "b" * 64
NOON = dt.datetime(2026, 4, 25, 12, 0, 0, tzinfo=dt.timezone.utc)


def test_rotation_is_deterministic_within_day(shop_env):
    from daimon.shop import current_rotation
    s1 = current_rotation(PK_A, owned=[], now=NOON)
    s2 = current_rotation(PK_A, owned=[],
                          now=dt.datetime(2026, 4, 25, 23, 59, 59,
                                          tzinfo=dt.timezone.utc))
    assert [s.listing.skin_slug for s in s1] == [s.listing.skin_slug for s in s2]


def test_rotation_changes_across_days(shop_env):
    from daimon.shop import current_rotation
    s1 = current_rotation(PK_A, owned=[], now=NOON)
    s2 = current_rotation(PK_A, owned=[],
                          now=NOON + dt.timedelta(days=1))
    assert [s.listing.skin_slug for s in s1] != [s.listing.skin_slug for s in s2]


def test_rotation_diverges_per_pubkey(shop_env):
    from daimon.shop import current_rotation
    a = current_rotation(PK_A, owned=[], now=NOON)
    b = current_rotation(PK_B, owned=[], now=NOON)
    # With our 10-skin pack, two distinct pubkeys should produce different orderings.
    assert [s.listing.skin_slug for s in a] != [s.listing.skin_slug for s in b]


def test_rotation_slot_composition(shop_env):
    """4 rare slots first, then up to 2 super_rare slots."""
    from daimon.shop import current_rotation
    slots = current_rotation(PK_A, owned=[], now=NOON)
    assert len(slots) == 6
    # First 4 must all be rare; last 2 must all be super_rare.
    rarities = [s.listing.rarity for s in slots]
    assert rarities[:4] == ["rare"] * 4
    assert rarities[4:] == ["super_rare"] * 2


def test_rotation_filters_owned(shop_env):
    from daimon.shop import current_rotation
    base = current_rotation(PK_A, owned=[], now=NOON)
    # Pretend we own slot 0 — it must NOT show up next time.
    owned_key = (base[0].listing.card_id, base[0].listing.skin_slug)
    after = current_rotation(PK_A, owned=[owned_key], now=NOON)
    assert all(
        (s.listing.card_id, s.listing.skin_slug) != owned_key
        for s in after
    )


def test_rotation_shrinks_when_pool_exhausted(shop_env):
    """Owning ALL super_rare skins drops the super_rare slot count to 0."""
    from daimon.shop import current_rotation, load_skin_pool
    pool = load_skin_pool()
    sr_keys = [(s.card_id, s.skin_slug) for s in pool
               if s.rarity == "super_rare"]
    assert len(sr_keys) == 4
    slots = current_rotation(PK_A, owned=sr_keys, now=NOON)
    rarities = [s.listing.rarity for s in slots]
    assert "super_rare" not in rarities
    assert len(slots) == 4  # all 4 rare slots, no super_rare


def test_rotation_seconds_until_clock(shop_env):
    from daimon.shop import seconds_until_next_rotation
    secs = seconds_until_next_rotation(
        dt.datetime(2026, 4, 25, 23, 59, 30, tzinfo=dt.timezone.utc)
    )
    assert secs == 30


# ---------------------------------------------------------------------------
# 3. Owned — persistence + weekly cap
# ---------------------------------------------------------------------------

def test_owned_roundtrip(shop_env):
    from daimon.shop.owned import OwnedSkin, append_owned, list_owned
    s = OwnedSkin(
        card_id="card_rare_00", skin_slug="cultural_00",
        skin_name="Cultural 00", skin_axis="cultural", rarity="rare",
        purchased_at="2026-04-24T12:00:00Z", cost=300,
        ledger_entry_hash="abc",
    )
    append_owned(s)
    rows = list_owned()
    assert len(rows) == 1
    assert rows[0].skin_slug == "cultural_00"


def test_owned_append_is_idempotent(shop_env):
    from daimon.shop.owned import OwnedSkin, append_owned, list_owned
    s = OwnedSkin(
        card_id="x", skin_slug="y", skin_name="Y", skin_axis="cultural",
        rarity="rare", purchased_at="t", cost=300, ledger_entry_hash="h",
    )
    append_owned(s)
    append_owned(s)
    assert len(list_owned()) == 1


def test_weekly_count_iso_week_boundaries(shop_env):
    from daimon.shop.owned import OwnedSkin, append_owned, weekly_purchase_count

    # ISO week 17 of 2026 = Mon 2026-04-20 → Sun 2026-04-26.
    in_week = OwnedSkin(
        card_id="a", skin_slug="b", skin_name="B", skin_axis="cultural",
        rarity="rare", purchased_at="2026-04-22T10:00:00Z",
        cost=300, ledger_entry_hash="h",
    )
    out_of_week = OwnedSkin(
        card_id="c", skin_slug="d", skin_name="D", skin_axis="cultural",
        rarity="rare", purchased_at="2026-04-13T10:00:00Z",  # week 16
        cost=300, ledger_entry_hash="h",
    )
    append_owned(in_week)
    append_owned(out_of_week)

    n = weekly_purchase_count(now=dt.datetime(2026, 4, 25, 12, 0, 0,
                                              tzinfo=dt.timezone.utc))
    assert n == 1


# ---------------------------------------------------------------------------
# 4. Equipped — equip / unequip with ownership gating
# ---------------------------------------------------------------------------

def test_equip_requires_ownership(shop_env):
    from daimon.shop import NotOwnedError, equip_skin
    with pytest.raises(NotOwnedError):
        equip_skin("card_rare_00", "cultural_00")


def test_equip_then_get_then_unequip(shop_env):
    from daimon.shop import equip_skin, get_equipped, unequip_skin
    from daimon.shop.owned import OwnedSkin, append_owned
    append_owned(OwnedSkin(
        card_id="card_rare_00", skin_slug="cultural_00",
        skin_name="C", skin_axis="cultural", rarity="rare",
        purchased_at="t", cost=300, ledger_entry_hash="h",
    ))

    equip_skin("card_rare_00", "cultural_00")
    assert get_equipped("card_rare_00") == "cultural_00"
    unequip_skin("card_rare_00")
    assert get_equipped("card_rare_00") is None


# ---------------------------------------------------------------------------
# 5. Purchase flow
# ---------------------------------------------------------------------------

def test_purchase_requires_identity(shop_env):
    from daimon.shop import purchase_slot
    with pytest.raises(FileNotFoundError):
        purchase_slot(0)


def test_purchase_requires_balance(shop_env):
    from daimon.identity import generate_identity
    from daimon.mining.ledger import InsufficientBalanceError
    from daimon.shop import purchase_slot

    generate_identity(force=True)
    with pytest.raises(InsufficientBalanceError):
        purchase_slot(0)


def test_purchase_atomic_ledger_and_owned(shop_env):
    from daimon.identity import generate_identity
    from daimon.mining.ledger import get_balance, get_recent_entries
    from daimon.shop import get_shop_state, list_owned, purchase_slot

    generate_identity(force=True)
    _seed_balance(2000)

    state = get_shop_state()
    assert state.balance == 2000
    slot0 = state.slots[0]
    receipt = purchase_slot(0)

    assert receipt.cost == slot0.cost
    assert receipt.balance_after == 2000 - slot0.cost
    assert get_balance() == 2000 - slot0.cost
    # Ledger has a kind=purchase entry.
    last = get_recent_entries(limit=1)[0]
    assert last["kind"] == "purchase"
    assert last["amount"] == -slot0.cost
    assert last["card_id"] == slot0.listing.card_id
    assert last["skin_slug"] == slot0.listing.skin_slug
    # Owned cache has the skin with the matching ledger hash.
    owned = list_owned()
    assert len(owned) == 1
    assert owned[0].ledger_entry_hash == receipt.ledger_entry_hash


def test_purchase_keeps_slot_in_place_marked_sold(shop_env):
    """Intra-day purchase MUST NOT shift slot indices — the bought slot
    stays at the same index marked ``sold=True``. (This is the slot
    stability guarantee that broke in the playtest.)"""
    from daimon.identity import generate_identity
    from daimon.shop import get_shop_state, purchase_slot

    generate_identity(force=True)
    _seed_balance(2000)

    before = get_shop_state()
    bought_slug = before.slots[0].listing.skin_slug
    bought_card = before.slots[0].listing.card_id
    purchase_slot(0)

    after = get_shop_state()
    # Same number of slots, same listings at every index.
    assert len(after.slots) == len(before.slots)
    for i, (a, b) in enumerate(zip(after.slots, before.slots)):
        assert a.listing.card_id == b.listing.card_id, f"slot {i} card shifted"
        assert a.listing.skin_slug == b.listing.skin_slug, f"slot {i} slug shifted"
    # Slot 0 is now sold; others remain available.
    assert after.slots[0].sold is True
    assert after.slots[0].listing.skin_slug == bought_slug
    assert after.slots[0].listing.card_id == bought_card
    assert after.slots[0].purchased_at is not None
    assert all(not s.sold for s in after.slots[1:])


def test_next_day_rotation_excludes_today_purchase(shop_env):
    """At 00:00 UTC tomorrow, today's purchase becomes 'yesterday' and is
    properly filtered out of the new rotation."""
    import datetime as dt

    from daimon.identity import generate_identity, load_identity
    from daimon.shop import current_rotation, purchase_slot
    from daimon.shop.owned import OwnedSkin, append_owned

    generate_identity(force=True)
    _seed_balance(2000)
    pk = load_identity().pubkey_hex

    # Buy slot 0 "now" (real wall clock, since OwnedSkin.purchased_at
    # uses now_iso() at append time and we can't mock that easily here).
    bought = purchase_slot(0)

    # Tomorrow at noon UTC, with the purchase still in the owned cache:
    tomorrow = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    slots = current_rotation(pk, now=tomorrow)
    bought_key = (bought.card_id, bought.skin_slug)
    assert all(
        (s.listing.card_id, s.listing.skin_slug) != bought_key for s in slots
    ), "yesterday's purchase should be filtered out of today's rotation"


def test_purchase_sold_slot_rejected(shop_env):
    """Trying to buy a slot you already bought today raises
    SlotNotInRotationError — the slot is in the rotation but marked sold."""
    from daimon.identity import generate_identity
    from daimon.shop import (
        SlotNotInRotationError,
        get_shop_state,
        purchase_slot,
    )

    generate_identity(force=True)
    _seed_balance(2000)

    purchase_slot(0)
    state = get_shop_state()
    assert state.slots[0].sold is True
    with pytest.raises(SlotNotInRotationError, match="already purchased today"):
        purchase_slot(0)


def test_rotation_marks_today_purchases_sold(shop_env):
    """Direct rotation call: an OwnedSkin with a today-timestamp shows up
    as a sold slot at the same index it would have been before purchase."""
    import datetime as dt

    from daimon.shop import current_rotation
    from daimon.shop.owned import OwnedSkin

    NOW = dt.datetime(2026, 4, 25, 12, 0, 0, tzinfo=dt.timezone.utc)
    base = current_rotation(PK_A, owned=[], now=NOW)
    target = base[0].listing
    purchased_at = "2026-04-25T08:30:00Z"  # same UTC day as NOW
    owned = [OwnedSkin(
        card_id=target.card_id, skin_slug=target.skin_slug,
        skin_name=target.skin_name, skin_axis=target.skin_axis,
        rarity=target.rarity, purchased_at=purchased_at,
        cost=300, ledger_entry_hash="h",
    )]
    after = current_rotation(PK_A, owned=owned, now=NOW)
    # Indices and listings must be identical to the empty-owned snapshot.
    assert [s.listing.skin_slug for s in after] == [s.listing.skin_slug for s in base]
    # Slot 0 is sold; rest are not.
    assert after[0].sold is True
    assert after[0].purchased_at == purchased_at
    assert all(not s.sold for s in after[1:])


def test_rotation_yesterday_purchase_filtered_not_marked_sold(shop_env):
    """An OwnedSkin purchased BEFORE the current UTC day is removed from
    the rotation entirely — it does NOT show as a sold slot."""
    import datetime as dt

    from daimon.shop import current_rotation
    from daimon.shop.owned import OwnedSkin

    NOW = dt.datetime(2026, 4, 25, 12, 0, 0, tzinfo=dt.timezone.utc)
    base = current_rotation(PK_A, owned=[], now=NOW)
    target = base[0].listing
    purchased_at = "2026-04-24T15:00:00Z"  # yesterday UTC
    owned = [OwnedSkin(
        card_id=target.card_id, skin_slug=target.skin_slug,
        skin_name=target.skin_name, skin_axis=target.skin_axis,
        rarity=target.rarity, purchased_at=purchased_at,
        cost=300, ledger_entry_hash="h",
    )]
    after = current_rotation(PK_A, owned=owned, now=NOW)
    bought_key = (target.card_id, target.skin_slug)
    assert all(
        (s.listing.card_id, s.listing.skin_slug) != bought_key for s in after
    )
    assert all(not s.sold for s in after)


def test_rotation_tuple_owned_treated_as_yesterday(shop_env):
    """Tuple-form (card_id, skin_slug) entries have no timestamp — they
    must be filtered like 'yesterday' (preserves legacy caller behavior)."""
    import datetime as dt

    from daimon.shop import current_rotation

    NOW = dt.datetime(2026, 4, 25, 12, 0, 0, tzinfo=dt.timezone.utc)
    base = current_rotation(PK_A, owned=[], now=NOW)
    target = (base[0].listing.card_id, base[0].listing.skin_slug)
    after = current_rotation(PK_A, owned=[target], now=NOW)
    assert all(
        (s.listing.card_id, s.listing.skin_slug) != target for s in after
    )
    assert all(not s.sold for s in after)


def test_rotation_unparseable_purchase_ts_filtered(shop_env):
    """Owned entries with garbage timestamps are filtered (yesterday) —
    we'd rather drop the slot than show a phantom sold marker."""
    import datetime as dt

    from daimon.shop import current_rotation
    from daimon.shop.owned import OwnedSkin

    NOW = dt.datetime(2026, 4, 25, 12, 0, 0, tzinfo=dt.timezone.utc)
    base = current_rotation(PK_A, owned=[], now=NOW)
    target = base[0].listing
    owned = [OwnedSkin(
        card_id=target.card_id, skin_slug=target.skin_slug,
        skin_name=target.skin_name, skin_axis=target.skin_axis,
        rarity=target.rarity, purchased_at="not-a-timestamp",
        cost=300, ledger_entry_hash="h",
    )]
    after = current_rotation(PK_A, owned=owned, now=NOW)
    bought_key = (target.card_id, target.skin_slug)
    assert all(
        (s.listing.card_id, s.listing.skin_slug) != bought_key for s in after
    )


def test_purchase_weekly_cap_blocks_sixth(shop_env):
    from daimon.identity import generate_identity
    from daimon.shop import (
        WeeklyCapExceededError,
        get_shop_state,
        purchase_slot,
    )

    generate_identity(force=True)
    _seed_balance(10_000)

    # Burn the cap. Synthetic pack has 6 cultural rare + 4 super_rare = 10
    # skins, so we can definitely purchase 5 within the same week. Slots are
    # stable intra-day now (sold slots stay in place), so we have to walk to
    # the first unsold slot each iteration instead of always hammering slot 0.
    for _ in range(5):
        state = get_shop_state()
        unsold = [s for s in state.slots if not s.sold]
        assert unsold, "ran out of unsold slots before cap"
        purchase_slot(unsold[0].index)

    state = get_shop_state()
    unsold = [s for s in state.slots if not s.sold]
    assert unsold, "no unsold slot to attempt the 6th purchase against"
    with pytest.raises(WeeklyCapExceededError):
        purchase_slot(unsold[0].index)


def test_purchase_already_owned_is_blocked(shop_env):
    """Composite-key re-purchase of an intra-day buy is rejected.

    Under the snapshot-stable rotation, the bought slot stays in the
    rotation marked ``sold=True`` until 00:00 UTC tomorrow — so the
    composite-key lookup *finds* the slot but ``_resolve_slot``'s
    sold-check raises ``SlotNotInRotationError("already purchased today")``.
    """
    from daimon.identity import generate_identity
    from daimon.shop import (
        SlotNotInRotationError,
        get_shop_state,
        purchase_slot,
    )

    generate_identity(force=True)
    _seed_balance(2000)

    state = get_shop_state()
    s0 = state.slots[0]
    sel = f"{s0.listing.card_id}/{s0.listing.skin_slug}"
    purchase_slot(sel)
    with pytest.raises(SlotNotInRotationError, match="already purchased today"):
        purchase_slot(sel)


def test_purchase_composite_addressing(shop_env):
    from daimon.identity import generate_identity
    from daimon.shop import get_shop_state, purchase_slot

    generate_identity(force=True)
    _seed_balance(2000)

    state = get_shop_state()
    s0 = state.slots[0]
    sel = f"{s0.listing.card_id}/{s0.listing.skin_slug}"
    receipt = purchase_slot(sel)
    assert receipt.card_id == s0.listing.card_id
    assert receipt.skin_slug == s0.listing.skin_slug


def test_purchase_bad_selector(shop_env):
    from daimon.identity import generate_identity
    from daimon.shop import SlotNotInRotationError, purchase_slot

    generate_identity(force=True)
    _seed_balance(2000)
    with pytest.raises(SlotNotInRotationError):
        purchase_slot("nonexistent_slug")


def test_purchase_out_of_range(shop_env):
    from daimon.identity import generate_identity
    from daimon.shop import SlotNotInRotationError, purchase_slot

    generate_identity(force=True)
    _seed_balance(2000)
    with pytest.raises(SlotNotInRotationError):
        purchase_slot(99)


# ---------------------------------------------------------------------------
# 6. Render integration — art_path_for honors equipped skin
# ---------------------------------------------------------------------------

def test_art_path_for_falls_back_to_canonical(shop_env):
    from daimon.cards import art_path_for
    p = art_path_for("card_rare_00")
    assert p is not None
    assert p.name == "v0.png"


def test_art_path_for_uses_equipped(shop_env):
    from daimon.cards import art_path_for
    from daimon.shop import equip_skin
    from daimon.shop.owned import OwnedSkin, append_owned

    append_owned(OwnedSkin(
        card_id="card_rare_00", skin_slug="cultural_00",
        skin_name="C", skin_axis="cultural", rarity="rare",
        purchased_at="t", cost=300, ledger_entry_hash="h",
    ))
    equip_skin("card_rare_00", "cultural_00")

    p = art_path_for("card_rare_00")
    assert p is not None
    assert p.name == "v1.png"  # the cultural skin's variant id


def test_art_path_for_falls_back_when_skin_png_missing(shop_env, tmp_path):
    """If the equipped skin's PNG is absent (mid-update etc), fall through
    to canonical without raising."""
    from daimon.cards import art_path_for
    from daimon.shop import equip_skin
    from daimon.shop.owned import OwnedSkin, append_owned

    append_owned(OwnedSkin(
        card_id="card_rare_00", skin_slug="cultural_00",
        skin_name="C", skin_axis="cultural", rarity="rare",
        purchased_at="t", cost=300, ledger_entry_hash="h",
    ))
    equip_skin("card_rare_00", "cultural_00")

    # Delete the skin PNG.
    (tmp_path / "art" / "v1_alpha" / "card_rare_00" / "variants"
     / "v1.png").unlink()

    p = art_path_for("card_rare_00")
    assert p is not None
    assert p.name == "v0.png"


# ---------------------------------------------------------------------------
# 7. MCP envelopes
# ---------------------------------------------------------------------------

def test_dm_shop_no_identity(shop_env):
    res = _call(dm_shop)
    assert res["error"] == "no_identity"


def test_dm_shop_with_identity(shop_env):
    from daimon.identity import generate_identity
    generate_identity(force=True)
    res = _call(dm_shop)
    assert res["status"] == "ok"
    assert res["slot_count"] == 6
    assert res["balance"] == 0
    assert res["weekly_cap"] == 5


def test_dm_shop_single_slot(shop_env):
    from daimon.identity import generate_identity
    generate_identity(force=True)
    res = _call(dm_shop, slot=0)
    assert res["status"] == "ok"
    assert "card_id" in res
    assert res["index"] == 0


def test_dm_shop_buy_no_args(shop_env):
    from daimon.identity import generate_identity
    generate_identity(force=True)
    res = _call(dm_shop_buy)
    assert res["error"] == "invalid_input"


def test_dm_shop_buy_insufficient(shop_env):
    from daimon.identity import generate_identity
    generate_identity(force=True)
    res = _call(dm_shop_buy, slot=0)
    assert res["error"] == "insufficient_balance"
    assert res["balance"] == 0
    assert res["needed"] == 300
    assert res["cost"] == 300


def test_dm_shop_buy_success_then_owned_then_equip(shop_env):
    from daimon.identity import generate_identity
    generate_identity(force=True)
    _seed_balance(2000)

    buy = _call(dm_shop_buy, slot=0)
    assert buy["status"] == "ok"
    assert buy["balance_after"] == 1700

    owned = _call(dm_skins_owned)
    assert owned["status"] == "ok"
    assert owned["count"] == 1
    assert owned["owned"][0]["equipped"] is False

    eq = _call(dm_skin_equip,
               card_id=buy["card_id"], skin_slug=buy["skin_slug"])
    assert eq["status"] == "ok"
    assert eq["equipped"][buy["card_id"]] == buy["skin_slug"]

    owned2 = _call(dm_skins_owned)
    assert owned2["owned"][0]["equipped"] is True

    un = _call(dm_skin_unequip, card_id=buy["card_id"])
    assert un["status"] == "ok"
    assert buy["card_id"] not in un["equipped"]


def test_dm_skin_equip_not_owned_envelope(shop_env):
    from daimon.identity import generate_identity
    generate_identity(force=True)
    res = _call(dm_skin_equip, card_id="card_rare_00",
                skin_slug="cultural_00")
    assert res["error"] == "not_owned"


def test_dm_shop_buy_composite_selector(shop_env):
    from daimon.identity import generate_identity
    generate_identity(force=True)
    _seed_balance(2000)

    state = _call(dm_shop)
    s0 = state["slots"][0]
    sel = f"{s0['card_id']}/{s0['skin_slug']}"
    res = _call(dm_shop_buy, selector=sel)
    assert res["status"] == "ok"
    assert res["card_id"] == s0["card_id"]


# ---------------------------------------------------------------------------
# 8. Ledger extension — purchase entries verify cleanly
# ---------------------------------------------------------------------------

def test_ledger_purchase_entry_chain_intact(shop_env):
    from daimon.identity import generate_identity
    from daimon.mining.ledger import (
        get_stats,
        verify_ledger,
    )
    from daimon.shop import purchase_slot, get_shop_state

    generate_identity(force=True)
    _seed_balance(2000)
    purchase_slot(0)

    v = verify_ledger()
    assert v["ok"] is True
    stats = get_stats()
    assert stats.purchase_count == 1
    s0_cost = get_shop_state().slots[0].cost  # next-rotation slot 0; not relevant
    # We bought the FIRST rotation's slot 0, which was a rare (300).
    assert stats.total_purchased == 300
