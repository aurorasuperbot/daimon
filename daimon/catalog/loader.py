"""Catalog loader + deterministic pull RNG.

A catalog directory contains:
  - manifest.json with rarity weights + a `cards` list (card_id, rarity, file)
  - one .json per card, schema-compatible with daimon.cards.loader

`load_catalog(name)` returns a Catalog object indexed by card_id and rarity.

`roll_pull(catalog, seed)` deterministically picks a rarity (weighted by the
manifest) then a card uniformly within that rarity. Determinism uses a
DRBG-style hash chain over the 32-byte seed so the same seed → same card.

This is *gacha randomness*, not crypto. Agents that want a verifiable pull
should bind the seed to a signed payload (planned for V1.5 PvP arena pulls).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CATALOG_ID = "v1_alpha"

# Rarity ordering for stable iteration (lowest → highest)
RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary"]


@dataclass(frozen=True)
class CatalogCard:
    card_id: str
    rarity: str
    pack: str           # catalog id (e.g. "v1_alpha")
    file: str           # filename within the catalog dir
    payload: Dict[str, Any]  # full card JSON (for the engine + render layers)


@dataclass(frozen=True)
class Catalog:
    pack_id: str
    version: str
    description: str
    rarity_weights: Dict[str, int]
    cards: List[CatalogCard]
    by_id: Dict[str, CatalogCard] = field(default_factory=dict)
    by_rarity: Dict[str, List[CatalogCard]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # frozen dataclass — bypass __setattr__ via object.__setattr__
        by_id = {c.card_id: c for c in self.cards}
        by_rarity: Dict[str, List[CatalogCard]] = {}
        for c in self.cards:
            by_rarity.setdefault(c.rarity, []).append(c)
        object.__setattr__(self, "by_id", by_id)
        object.__setattr__(self, "by_rarity", by_rarity)

    def rarities(self) -> List[str]:
        """Rarities present in the catalog AND with > 0 weight."""
        return [r for r in RARITY_ORDER
                if r in self.by_rarity and self.rarity_weights.get(r, 0) > 0]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _catalog_dir(name: str) -> Path:
    """Resolve the bundled catalog directory.

    Uses importlib.resources so this works inside zipped wheels too.
    """
    pkg = "daimon.catalog"
    # importlib.resources.files() returns a Traversable; wrap in Path when local.
    root = resources.files(pkg) / name
    if not root.is_dir():
        raise FileNotFoundError(f"catalog {name!r} not found in package")
    return Path(str(root))


def list_catalogs() -> List[str]:
    """Return the catalog ids available inside the package."""
    pkg = "daimon.catalog"
    root = resources.files(pkg)
    out: List[str] = []
    for entry in root.iterdir():
        if entry.is_dir() and (entry / "manifest.json").is_file():
            out.append(entry.name)
    return sorted(out)


def load_catalog(name: str = DEFAULT_CATALOG_ID,
                 *,
                 root: Optional[Path] = None) -> Catalog:
    """Load a catalog by name. Pass `root` to load from an arbitrary directory."""
    cdir = root if root is not None else _catalog_dir(name)
    manifest_path = cdir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing manifest at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    pack_id = manifest.get("pack_id", name)
    version = manifest.get("version", "0.0.0")
    description = manifest.get("description", "")
    rarity_weights = manifest.get("rarity_weights", {})
    if not isinstance(rarity_weights, dict):
        raise ValueError("manifest.rarity_weights must be an object")

    cards_meta = manifest.get("cards", [])
    if not isinstance(cards_meta, list) or not cards_meta:
        raise ValueError("manifest.cards must be a non-empty array")

    cards: List[CatalogCard] = []
    for c in cards_meta:
        if not isinstance(c, dict):
            raise ValueError(f"manifest.cards entry not an object: {c!r}")
        card_id = c.get("card_id")
        rarity = c.get("rarity")
        fname = c.get("file")
        if not card_id or not rarity or not fname:
            raise ValueError(f"manifest entry missing field: {c!r}")
        payload_path = cdir / fname
        if not payload_path.is_file():
            raise FileNotFoundError(f"card file missing: {payload_path}")
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        # Sanity: manifest rarity should match payload rarity.
        if payload.get("rarity") and payload["rarity"] != rarity:
            raise ValueError(
                f"{card_id}: manifest rarity {rarity!r} != "
                f"card rarity {payload['rarity']!r}"
            )
        cards.append(CatalogCard(
            card_id=card_id,
            rarity=rarity,
            pack=pack_id,
            file=fname,
            payload=payload,
        ))

    return Catalog(
        pack_id=pack_id,
        version=version,
        description=description,
        rarity_weights=rarity_weights,
        cards=cards,
    )


# ---------------------------------------------------------------------------
# Pull RNG
# ---------------------------------------------------------------------------

def _drbg_uint32(seed: bytes, label: bytes) -> int:
    """Deterministic 32-bit unsigned int from (seed, label) via SHA-256."""
    h = hashlib.sha256()
    h.update(seed)
    h.update(b"|")
    h.update(label)
    return int.from_bytes(h.digest()[:4], "big")


def _weighted_pick(weights: Dict[str, int],
                   choices: List[str],
                   roll: int) -> str:
    """Pick one of `choices` weighted by `weights`. roll is a uint32.

    Uses (roll % total_weight) — biased by ~2^-32 / total_weight which is
    immaterial for our weight scale (< 1000).
    """
    pairs = [(c, max(0, int(weights.get(c, 0)))) for c in choices]
    total = sum(w for _, w in pairs)
    if total <= 0:
        raise ValueError("no positive weights to pick from")
    r = roll % total
    cum = 0
    for c, w in pairs:
        cum += w
        if r < cum:
            return c
    return pairs[-1][0]  # unreachable


@dataclass(frozen=True)
class PullResult:
    card: CatalogCard
    rarity: str
    seed_hex: str


def roll_pull(catalog: Catalog, seed: bytes, *,
              override_weights: Optional[Dict[str, int]] = None) -> PullResult:
    """Deterministically roll one card from the catalog.

    Steps:
      1. Pick rarity weighted by `catalog.rarity_weights` (or
         ``override_weights`` when provided), restricted to rarities
         that exist in this catalog.
      2. Pick a card uniformly within that rarity.

    Same seed + same weights → same result. Always.
    """
    if len(seed) != 32:
        raise ValueError(f"seed must be 32 bytes, got {len(seed)}")

    weights = override_weights or catalog.rarity_weights
    rarities = [r for r in RARITY_ORDER
                if r in catalog.by_rarity and weights.get(r, 0) > 0]
    if not rarities:
        raise ValueError("catalog has no rollable rarities")

    rarity_roll = _drbg_uint32(seed, b"rarity")
    rarity = _weighted_pick(weights, rarities, rarity_roll)

    pool = catalog.by_rarity[rarity]
    card_roll = _drbg_uint32(seed, b"card")
    card = pool[card_roll % len(pool)]

    return PullResult(card=card, rarity=rarity, seed_hex=seed.hex())
