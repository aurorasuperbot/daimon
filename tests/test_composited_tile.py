"""Phase F: composited-tile overlay path.

Verifies the unified pipeline: every TUI surface (shop / collection /
loadout-edit) now feeds a :class:`CardTileInfo` into ``tile.render_tile``,
which composites the FULL card chrome (gold rarity border, name, element
chip, stats strip, flavor) via ``card_tile.render_card_tile`` and points
the :class:`ImageOverlay` at the composited PNG cache instead of the raw
card-art file. The KGP painter then ships those composited bitmaps to the
terminal — same chrome, every surface, pixel-perfect.

These tests are intentionally lightweight: we don't pixel-diff the
composited tiles themselves (``test_card_tile.py``-style coverage lives
elsewhere). We verify the *contract*:

  1. ``compose_tile_to_path`` returns a real, on-disk PNG cache path.
  2. ``render_tile`` with ``composited_info`` produces an
     :class:`ImageOverlay` pointing at that path (NOT the raw art).
  3. Re-rendering the same (info, dims) hits the cache (same path).
  4. Different rarities / element / hp_max → different cache paths.
  5. The TUI helper builders (``OwnedCard.to_tile_info``,
     ``CatalogEntry.to_tile_info``, ``shop_ui._tile_info_for_listing``)
     all produce composable CardTileInfos that resolve to a real PNG.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daimon.play.card_tile import (
    CardTileInfo,
    compose_tile_to_path,
    set_cache_dir,
    tile_info_from_catalog_payload,
)
from daimon.play.schema import Element
from daimon.play.screenshot import ImageOverlay
from daimon.play.tile import render_tile


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path):
    """Each test gets a fresh on-disk cache dir so we can assert path
    membership without bleed across tests / sessions."""
    set_cache_dir(tmp_path / "tiles")
    yield


def _info(name: str = "Aegis Lion", rarity: str = "rare", hp: int = 30,
          element: Element = Element.NORMAL) -> CardTileInfo:
    return CardTileInfo(
        name=name,
        short_name=name[:7],
        rarity=rarity,
        position=0,
        species=name.lower().replace(" ", "_"),
        element=element,
        flavor="",
        hp=hp,
        hp_max=hp,
        atk=6,
        defense=8,
        spd=4,
    )


# ---------------------------------------------------------------------------
# compose_tile_to_path
# ---------------------------------------------------------------------------


def test_compose_tile_to_path_returns_existing_png(tmp_path):
    info = _info()
    p = compose_tile_to_path(info)
    assert p.suffix == ".png"
    assert p.exists()
    assert p.stat().st_size > 100  # actual PNG, not an empty stub


def test_compose_tile_to_path_idempotent_for_same_input():
    """Same (info, w, h) → same cache path; re-call does NOT recompose."""
    info = _info()
    p1 = compose_tile_to_path(info)
    p2 = compose_tile_to_path(info)
    assert p1 == p2
    assert p1.exists()


def test_compose_tile_to_path_distinct_for_different_rarity():
    info_a = _info(rarity="rare")
    info_b = _info(rarity="legendary")
    a = compose_tile_to_path(info_a)
    b = compose_tile_to_path(info_b)
    assert a != b


def test_compose_tile_to_path_distinct_for_different_element():
    info_a = _info(element=Element.FIRE)
    info_b = _info(element=Element.WATER)
    a = compose_tile_to_path(info_a)
    b = compose_tile_to_path(info_b)
    assert a != b


def test_compose_tile_to_path_distinct_for_different_size():
    info = _info()
    a = compose_tile_to_path(info, 280, 392)
    b = compose_tile_to_path(info, 140, 196)
    assert a != b
    assert a.exists() and b.exists()


# ---------------------------------------------------------------------------
# render_tile with composited_info
# ---------------------------------------------------------------------------


def test_render_tile_composited_overlay_points_at_cache_path():
    """The Phase F path: overlay.image_path is the composited PNG, NOT
    the raw card-art path."""
    info = _info()
    cache_path = compose_tile_to_path(info)
    tile = render_tile(
        card_id="aegis_lion",
        width=18,
        art_h=10,
        caption_lines=("[0]", ""),
        composited_info=info,
    )
    assert tile.local_overlay is not None
    assert tile.local_overlay.image_path == cache_path


def test_render_tile_composited_overlay_dims_match_art_region():
    info = _info()
    tile = render_tile(
        card_id="aegis_lion",
        width=18,    # 18 outer cells
        art_h=10,    # 10 cells of art height
        caption_lines=("", ""),
        composited_info=info,
    )
    ov = tile.local_overlay
    assert ov is not None
    assert ov.rows == 10
    assert ov.cols == 18 - 2  # inner_w (stripped of side borders)
    assert ov.row == 1        # below top border
    assert ov.col == 1        # right of left border


def test_render_tile_composited_selected_sets_glow_and_border_width():
    info = _info()
    tile = render_tile(
        card_id="aegis_lion",
        width=18, art_h=10,
        caption_lines=("", ""),
        composited_info=info,
        selected=True,
        border_color_rgb=(130, 220, 240),
    )
    ov = tile.local_overlay
    assert ov is not None
    assert ov.border_width == 2
    assert ov.glow == 4
    assert ov.border_color == (130, 220, 240)


def test_render_tile_composited_unselected_no_glow():
    info = _info()
    tile = render_tile(
        card_id="aegis_lion",
        width=18, art_h=10,
        caption_lines=("", ""),
        composited_info=info,
        selected=False,
    )
    ov = tile.local_overlay
    assert ov is not None
    assert ov.border_width == 0
    assert ov.glow == 0


def test_render_tile_composited_art_region_is_blank_cells():
    """The art rows are blank (so KGP can paint over them); only the
    side-border glyphs sit in the row, no other visible chars."""
    info = _info()
    tile = render_tile(
        card_id="aegis_lion",
        width=18, art_h=10,
        caption_lines=("[0]", ""),
        composited_info=info,
    )
    # First line is the top border, last lines are bottom + captions.
    # Lines [1..art_h] are the art rows. Strip ANSI to inspect content.
    import re
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    for art_line in tile.lines[1:1 + 10]:
        stripped = ansi_re.sub("", art_line)
        # Side border glyphs ┌─┐│└─┘ count as 1 visible cell each side.
        # Interior must be exactly inner_w spaces.
        assert stripped[0] in "│║┌╔└╚├╠╎"  # left edge glyph
        assert stripped[-1] in "│║┐╗┘╝┤╣╎"  # right edge glyph
        interior = stripped[1:-1]
        assert interior == " " * (18 - 2), (
            f"art row should be all spaces, got {interior!r}"
        )


def test_render_tile_legacy_path_still_works_when_composited_info_none():
    """Backwards compatibility: callers that don't pass composited_info
    still get a tile (with the raw-art overlay path or placeholder)."""
    tile = render_tile(
        card_id="aegis_lion",
        width=18, art_h=10,
        caption_lines=("[0]", ""),
    )
    assert tile.lines  # rendered something
    # local_overlay may or may not be set depending on art availability,
    # but the tile chrome must always exist.
    assert len(tile.lines) >= 10  # at least the art rows


# ---------------------------------------------------------------------------
# tile_info_from_catalog_payload — common builder for all three TUIs
# ---------------------------------------------------------------------------


def test_tile_info_from_catalog_payload_full():
    payload = {
        "card_id": "aegis_lion",
        "name": "Nemean Wanderer",
        "species": "aegis_lion",
        "element": "NORMAL",
        "rarity": "rare",
        "hp": 30, "atk": 6, "def": 8, "spd": 4,
        "flavor": "Lion of no temple.",
    }
    info = tile_info_from_catalog_payload(payload, position=2)
    assert info.name == "Nemean Wanderer"
    assert info.rarity == "rare"
    assert info.element == Element.NORMAL
    assert info.hp == 30
    assert info.hp_max == 30
    assert info.atk == 6
    assert info.defense == 8
    assert info.spd == 4
    assert info.position == 2


def test_tile_info_from_catalog_payload_missing_fields_use_defaults():
    """Sparse payload → minimal CardTileInfo, no exceptions."""
    info = tile_info_from_catalog_payload({"card_id": "x"})
    assert info.name == "x"  # falls back to card_id
    assert info.rarity == "common"
    # Default element string "NORMAL" → Element.NORMAL (helper policy:
    # missing key gets the neutral/normal fallback so chrome still renders).
    assert info.element == Element.NORMAL
    assert info.hp == 1
    assert info.atk == 0
    assert info.flavor == ""


def test_tile_info_from_catalog_payload_unknown_element_falls_back_none():
    info = tile_info_from_catalog_payload({"card_id": "x", "element": "PURPLE"})
    assert info.element is None


def test_tile_info_from_catalog_payload_composes_to_real_png():
    """End-to-end: catalog dict → CardTileInfo → composited PNG on disk."""
    payload = {
        "card_id": "aegis_lion", "name": "Aegis Lion", "species": "aegis_lion",
        "element": "NORMAL", "rarity": "rare",
        "hp": 30, "atk": 6, "def": 8, "spd": 4,
    }
    info = tile_info_from_catalog_payload(payload)
    p = compose_tile_to_path(info)
    assert p.exists()
    assert p.stat().st_size > 100


# ---------------------------------------------------------------------------
# Per-TUI helpers — exercise the bridges from each TUI's domain object
# ---------------------------------------------------------------------------


def test_owned_card_to_tile_info_with_payload():
    """collection_ui.OwnedCard.to_tile_info() should compose successfully."""
    from daimon.play.collection_ui import OwnedCard
    payload = {
        "card_id": "aegis_lion", "name": "Aegis", "species": "aegis_lion",
        "element": "NORMAL", "rarity": "rare",
        "hp": 30, "atk": 6, "def": 8, "spd": 4,
    }
    oc = OwnedCard(card_id="aegis_lion", rarity="rare", count=2, payload=payload)
    info = oc.to_tile_info(position=3)
    assert info.position == 3
    assert info.name == "Aegis"
    p = compose_tile_to_path(info)
    assert p.exists()


def test_owned_card_to_tile_info_without_payload_falls_back():
    """Catalog-miss path: OwnedCard with payload=None still produces a
    composable CardTileInfo (no crash)."""
    from daimon.play.collection_ui import OwnedCard
    oc = OwnedCard(card_id="orphan", rarity="rare", count=1, payload=None)
    info = oc.to_tile_info()
    assert info.name == "orphan"
    p = compose_tile_to_path(info)
    assert p.exists()


def test_catalog_entry_to_tile_info():
    """loadout_editor.CatalogEntry.to_tile_info() should compose successfully."""
    from daimon.play.loadout_editor import CatalogEntry
    payload = {
        "card_id": "blaze_wolf", "name": "Blaze Wolf", "species": "blaze_wolf",
        "element": "FIRE", "rarity": "uncommon",
        "hp": 18, "atk": 9, "def": 3, "spd": 7,
    }
    ce = CatalogEntry(
        card_id="blaze_wolf", rarity="uncommon",
        element="FIRE", species="blaze_wolf",
        payload=payload,
    )
    info = ce.to_tile_info(position=1)
    assert info.element == Element.FIRE
    assert info.rarity == "uncommon"
    p = compose_tile_to_path(info)
    assert p.exists()


def test_shop_ui_tile_info_for_listing_uses_catalog_payload(monkeypatch):
    """shop_ui._tile_info_for_listing prefers the catalog payload's combat
    rarity over the listing's price-tier rarity."""
    from daimon.play import shop_ui
    from daimon.shop.listings import SkinListing
    # Inject a fake catalog into the cache so we don't depend on disk.
    monkeypatch.setattr(shop_ui, "_CATALOG_BY_ID_CACHE", {
        "aegis_lion": {
            "card_id": "aegis_lion", "name": "Aegis Lion",
            "species": "aegis_lion", "element": "NORMAL",
            "rarity": "rare",   # combat rarity
            "hp": 30, "atk": 6, "def": 8, "spd": 4,
        },
    })
    listing = SkinListing(
        card_id="aegis_lion",
        skin_slug="heretic",
        skin_name="Heretic Manuscript",
        skin_axis="cultural",
        rarity="super_rare",   # SHOP rarity (price tier)
        variant_id="v1",
        art_path="/tmp/__nope__.png",  # missing, falls back to placeholder
    )
    info = shop_ui._tile_info_for_listing(listing)
    # Combat rarity ("rare") wins, NOT the price tier ("super_rare")
    assert info.rarity == "rare"
    assert info.element == Element.NORMAL
    assert info.atk == 6
    assert info.defense == 8


def test_shop_ui_tile_info_for_listing_catalog_miss_uses_stub(monkeypatch):
    """When the catalog has no entry, fall back to a stub built from the
    listing alone — UI keeps rendering instead of crashing."""
    from daimon.play import shop_ui
    from daimon.shop.listings import SkinListing
    monkeypatch.setattr(shop_ui, "_CATALOG_BY_ID_CACHE", {})
    listing = SkinListing(
        card_id="orphan_card", skin_slug="x", skin_name="X",
        skin_axis="cultural", rarity="rare", variant_id="v1",
        art_path="/tmp/__nope__.png",
    )
    info = shop_ui._tile_info_for_listing(listing)
    assert info.name == "X"  # uses skin_name fallback
    assert info.rarity == "common"
    assert info.flavor == "(catalog miss)"
    # And it still composites to a valid PNG.
    p = compose_tile_to_path(info)
    assert p.exists()
