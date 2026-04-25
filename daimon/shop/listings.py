"""Skin catalog discovery — walks the installed art-pack manifests.

The "catalog" of purchasable skins is NOT a separate database. It's derived
from the art-pack manifests on disk: every art-pack card directory has a
``manifest.json`` with a ``variants`` list. Variants where
``kind == "skin"`` and ``status == "active"`` are eligible shop listings.

The art-pack lives at ``art_pack_dir()`` (resolved from ``DAIMON_ART_DIR``
or ``~/.daimon/art/<pack>``). The CLI/MCP layer guarantees the pack is
installed before invoking shop tools (every non-art-pure command runs
``ensure_art_available()`` in the click group callback).

Why no separate catalog DB?
  - The art-pack IS the source of truth: a skin can't be in the shop if
    its PNG isn't shipped.
  - One fewer thing to keep in sync with the cards repo.
  - Re-rolling a skin (regenerating the PNG with a new seed but same slug)
    is a transparent operation — the shop sees the new variant the moment
    the manifest updates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

from daimon.update.paths import art_pack_dir


# Skin axes recognised by the shop. Tied 1:1 to a price tier (see
# `daimon.shop.rotation.PRICE_RARE` / `PRICE_SUPER_RARE`).
VALID_AXES = frozenset({"cultural", "anatomical"})

# Shop rarities. Decoupled from the card's *combat* rarity (which lives in
# the catalog payload). A skin's rarity is purely about its price tier.
VALID_RARITIES = frozenset({"rare", "super_rare"})


@dataclass(frozen=True, order=True)
class SkinListing:
    """One purchasable skin as exposed by the shop.

    ``order=True`` so a list of listings has a stable, deterministic sort
    independent of disk traversal order. Sort key is (card_id, skin_slug)
    via field declaration order.
    """

    card_id: str        # which card this skin re-skins
    skin_slug: str      # globally-unique within (card_id) namespace
    skin_name: str      # human-readable (e.g. "Heretic Manuscript")
    skin_axis: str      # "cultural" | "anatomical"
    rarity: str         # "rare" | "super_rare"
    variant_id: str     # art-pack variant id (e.g. "v1")
    art_path: str       # absolute path to the PNG, as a string for JSON

    def to_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "skin_slug": self.skin_slug,
            "skin_name": self.skin_name,
            "skin_axis": self.skin_axis,
            "rarity": self.rarity,
            "variant_id": self.variant_id,
            "art_path": self.art_path,
        }


def _read_manifest(p: Path) -> Optional[dict]:
    """Load + parse a manifest. Returns None on missing file or invalid JSON.

    A corrupt manifest is logged-by-omission, not raised — one bad card
    must not take down the entire shop. The art-pass scripts are the place
    where corrupt manifests should be fixed; the shop just skips them.
    """
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _variant_to_listing(card_id: str, variant: dict, art_root: Path
                        ) -> Optional[SkinListing]:
    """Build a SkinListing from a manifest variant entry. Returns None if
    the variant isn't a valid shop entry (wrong kind, missing fields,
    invalid axis/rarity, etc.)."""
    if variant.get("kind") != "skin":
        return None
    if variant.get("status") != "active":
        return None
    slug = variant.get("skin_slug")
    name = variant.get("skin_name") or slug
    axis = variant.get("skin_axis")
    rarity = variant.get("rarity")
    vid = variant.get("id")
    if not (slug and axis and rarity and vid):
        return None
    if axis not in VALID_AXES or rarity not in VALID_RARITIES:
        return None
    art = art_root / card_id / "variants" / f"{vid}.png"
    if not art.is_file():
        # Variant entry exists but PNG hasn't shipped — don't list it.
        return None
    return SkinListing(
        card_id=card_id,
        skin_slug=slug,
        skin_name=name,
        skin_axis=axis,
        rarity=rarity,
        variant_id=vid,
        art_path=str(art),
    )


def iter_skins(art_root: Optional[Path] = None) -> Iterator[SkinListing]:
    """Yield every active skin listing from the installed art-pack.

    Yields in disk-traversal order. Callers that need a stable order should
    use ``load_skin_pool`` instead.

    ``art_root`` defaults to ``art_pack_dir()`` (resolved fresh each call,
    not cached at import — tests monkeypatch ``DAIMON_ART_DIR``).
    """
    if art_root is None:
        art_root = art_pack_dir()
    if not art_root.is_dir():
        return
    for card_dir in art_root.iterdir():
        if not card_dir.is_dir():
            continue
        # Skip control files like ".version", ".checksum", "__pycache__"
        if card_dir.name.startswith("."):
            continue
        manifest = _read_manifest(card_dir / "manifest.json")
        if not manifest:
            continue
        card_id = manifest.get("card_id") or card_dir.name
        for variant in manifest.get("variants", []):
            if not isinstance(variant, dict):
                continue
            listing = _variant_to_listing(card_id, variant, art_root)
            if listing is not None:
                yield listing


def load_skin_pool(art_root: Optional[Path] = None) -> List[SkinListing]:
    """Return a deterministically-sorted list of every shop listing.

    Sort key is (card_id, skin_slug) — same skins always come back in the
    same order across processes and platforms. The rotation seeded shuffle
    relies on this stability.
    """
    return sorted(iter_skins(art_root))
