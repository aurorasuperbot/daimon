"""Bundled card catalogs.

A catalog is a versioned bundle of card JSON files + a manifest that lists
which cards exist at which rarity, with rarity weights for gacha rolls.

V1 alpha ships `v1_alpha/` (13 cards across 5 rarities) so `daimon pull` works
the moment the package is installed. Additional packs land via OCI artifacts
in V1.5.

The catalog is read-only. The pull RNG is exposed via `roll_pull(seed)`
which returns a deterministic (card_id, rarity, full_dict) for a given
32-byte seed — agents can simulate pulls before committing currency.
"""

from daimon.catalog.loader import (
    Catalog,
    CatalogCard,
    DEFAULT_CATALOG_ID,
    list_catalogs,
    load_catalog,
    roll_pull,
)

__all__ = [
    "Catalog",
    "CatalogCard",
    "DEFAULT_CATALOG_ID",
    "list_catalogs",
    "load_catalog",
    "roll_pull",
]
