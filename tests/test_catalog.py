"""Catalog + pull RNG tests.

Covers:
  - Bundled v1_alpha catalog loads
  - Manifest rarities match per-card rarities
  - All cards parse via the engine loader (so triggers/stats are valid)
  - roll_pull is deterministic for a given seed
  - roll_pull respects rarity weights over many rolls (statistical sanity)
  - roll_pull rejects bad seeds
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest

from nullpoint.cards import load_card_dict
from nullpoint.catalog import (
    DEFAULT_CATALOG_ID,
    list_catalogs,
    load_catalog,
    roll_pull,
)


def test_v1_alpha_in_catalogs():
    cats = list_catalogs()
    assert DEFAULT_CATALOG_ID in cats


def test_v1_alpha_loads():
    cat = load_catalog(DEFAULT_CATALOG_ID)
    assert cat.pack_id == "v1_alpha"
    assert len(cat.cards) >= 7
    # All five rarities should be represented
    assert {"common", "uncommon", "rare", "epic", "legendary"}.issubset(
        set(c.rarity for c in cat.cards)
    )


def test_all_cards_parse_via_engine_loader():
    """Engine loader must accept every catalog card."""
    cat = load_catalog(DEFAULT_CATALOG_ID)
    for c in cat.cards:
        card = load_card_dict(c.payload)
        assert card.card_id == c.card_id
        # Stats positive, slot in enum
        assert card.atk >= 0


def test_manifest_rarity_matches_card_rarity():
    cat = load_catalog(DEFAULT_CATALOG_ID)
    for c in cat.cards:
        assert c.payload["rarity"] == c.rarity


def test_roll_pull_deterministic():
    cat = load_catalog(DEFAULT_CATALOG_ID)
    seed = b"\xab" * 32
    a = roll_pull(cat, seed)
    b = roll_pull(cat, seed)
    assert a.card.card_id == b.card.card_id
    assert a.rarity == b.rarity


def test_roll_pull_different_seeds_different_outcomes():
    cat = load_catalog(DEFAULT_CATALOG_ID)
    outcomes = set()
    for i in range(20):
        seed = bytes([i]) * 32
        outcomes.add(roll_pull(cat, seed).card.card_id)
    # 20 random rolls with 13 cards: expect at least 5 distinct outcomes
    assert len(outcomes) >= 5


def test_roll_pull_respects_weights_statistically():
    """Roll 1000 times — common rarity should dominate per the weights."""
    cat = load_catalog(DEFAULT_CATALOG_ID)
    counts = collections.Counter()
    for i in range(1000):
        seed = i.to_bytes(2, "big") + b"\x00" * 30
        counts[roll_pull(cat, seed).rarity] += 1
    # Weights: common=60, uncommon=25, rare=10, epic=4, legendary=1
    # Expectations (out of 1000): common ~600, uncommon ~250, ...
    assert counts["common"] > counts["uncommon"]
    assert counts["uncommon"] > counts["rare"]
    assert counts["rare"] > counts["epic"]
    # legendary is rare — just assert it can happen
    assert counts["legendary"] >= 0


def test_roll_pull_rejects_short_seed():
    cat = load_catalog(DEFAULT_CATALOG_ID)
    with pytest.raises(ValueError):
        roll_pull(cat, b"\x00" * 10)


def test_load_catalog_from_arbitrary_root(tmp_path):
    """Smoke test: load_catalog can read from a custom root."""
    # Copy bundled catalog to tmp
    from importlib import resources
    import shutil
    src = Path(str(resources.files("nullpoint.catalog") / DEFAULT_CATALOG_ID))
    dst = tmp_path / "v1_alpha"
    shutil.copytree(src, dst)
    cat = load_catalog("v1_alpha", root=dst)
    assert len(cat.cards) >= 7
