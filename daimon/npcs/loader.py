"""NPC roster loader.

A roster directory contains:
  - manifest.json  with `tiers`: list of {tier_id, rank, label, rule, npcs}
  - one .json per NPC, grouped by tier subdirectory:
      <root>/<tier_id>/<npc_id>.json
    Each NPC file is shape:
      {npc_id, name, tier, rank, flavor, bio, loadout: [card_id, ...]}

The loader resolves each NPC's `loadout` (a list of card_ids) against a
catalog (default: ``v1_alpha``) so the engine never sees raw NPC JSON --
NPCs reuse the bundled card pool.

  list_tiers()      -> tier_id list, sorted by rank
  list_npcs(tier?)  -> NPC list (tier-filtered if given), sorted by tier-then-id
  get_npc(npc_id)   -> NPC by id
  npc_loadout(npc)  -> Loadout (resolved cards from catalog)
  npc_card_dicts(n) -> list[dict] of raw card payloads (for MCP/render display)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional

from daimon.cards import load_card_dict
from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
from daimon.engine import Loadout

DEFAULT_ROSTER_PKG = "daimon.npcs"

# Tier ranks for ordering. The canonical source is manifest.json -- this is a
# fallback used only when sorting NPCs whose tiers don't appear in the manifest.
TIER_RANK_FALLBACK = {
    "rookie": 1,
    "novice": 2,
    "veteran": 3,
    "elite": 4,
    "champion": 5,
}


@dataclass(frozen=True)
class NPC:
    """One NPC: name + tier + fixed loadout (as card_ids).

    The loadout is stored as ``card_ids`` (strings); call ``npc_loadout`` to
    resolve them to a real ``engine.Loadout`` against a catalog.
    """
    npc_id: str
    name: str
    tier: str
    rank: int
    flavor: str
    bio: str
    loadout: tuple[str, ...]   # card_id list (resolved against catalog at match time)


@dataclass(frozen=True)
class Tier:
    tier_id: str
    rank: int
    label: str
    rule: str
    npc_ids: tuple[str, ...]


@dataclass(frozen=True)
class Roster:
    roster_version: str
    description: str
    tiers: tuple[Tier, ...]
    npcs: tuple[NPC, ...]
    by_id: Dict[str, NPC] = field(default_factory=dict)
    by_tier: Dict[str, List[NPC]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        by_id = {n.npc_id: n for n in self.npcs}
        by_tier: Dict[str, List[NPC]] = {}
        for n in self.npcs:
            by_tier.setdefault(n.tier, []).append(n)
        # Sort each tier's NPCs by id for stable iteration.
        for k in by_tier:
            by_tier[k] = sorted(by_tier[k], key=lambda x: x.npc_id)
        object.__setattr__(self, "by_id", by_id)
        object.__setattr__(self, "by_tier", by_tier)

    def tier_ids(self) -> List[str]:
        """Tier ids in rank order."""
        return [t.tier_id for t in sorted(self.tiers, key=lambda t: t.rank)]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _roster_dir(pkg: str = DEFAULT_ROSTER_PKG) -> Path:
    """Resolve the bundled NPC roster directory."""
    root = resources.files(pkg)
    p = Path(str(root))
    if not (p / "manifest.json").is_file():
        raise FileNotFoundError(f"NPC roster manifest missing under {p}")
    return p


def load_roster(*, root: Optional[Path] = None) -> Roster:
    """Load the NPC roster. Pass ``root`` to load from an arbitrary directory."""
    rdir = root if root is not None else _roster_dir()
    manifest_path = rdir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing roster manifest at {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    roster_version = manifest.get("roster_version", "v1_alpha")
    description = manifest.get("description", "")

    tiers_meta = manifest.get("tiers", [])
    if not isinstance(tiers_meta, list) or not tiers_meta:
        raise ValueError("roster manifest must declare a non-empty `tiers` list")

    tiers: List[Tier] = []
    expected_npc_ids: List[tuple[str, str]] = []  # (npc_id, tier_id)
    for tm in tiers_meta:
        if not isinstance(tm, dict):
            raise ValueError(f"tier entry not an object: {tm!r}")
        tier_id = tm.get("tier_id")
        rank = tm.get("rank")
        label = tm.get("label", tier_id)
        rule = tm.get("rule", "")
        npc_ids = tm.get("npcs", [])
        if not tier_id or rank is None or not isinstance(npc_ids, list):
            raise ValueError(f"tier entry missing fields: {tm!r}")
        tiers.append(Tier(
            tier_id=tier_id, rank=int(rank), label=label, rule=rule,
            npc_ids=tuple(npc_ids),
        ))
        for nid in npc_ids:
            expected_npc_ids.append((nid, tier_id))

    npcs: List[NPC] = []
    seen_ids: set[str] = set()
    for npc_id, tier_id in expected_npc_ids:
        if npc_id in seen_ids:
            raise ValueError(f"duplicate npc_id {npc_id!r} in manifest")
        seen_ids.add(npc_id)
        npc_path = rdir / tier_id / f"{npc_id}.json"
        if not npc_path.is_file():
            raise FileNotFoundError(
                f"NPC file missing: {npc_path} "
                f"(referenced by manifest tier {tier_id!r})"
            )
        data = json.loads(npc_path.read_text(encoding="utf-8"))
        if data.get("npc_id") != npc_id:
            raise ValueError(
                f"NPC {npc_id!r}: file npc_id {data.get('npc_id')!r} "
                f"does not match manifest entry"
            )
        if data.get("tier") != tier_id:
            raise ValueError(
                f"NPC {npc_id!r}: file tier {data.get('tier')!r} "
                f"does not match manifest tier {tier_id!r}"
            )
        loadout_ids = data.get("loadout", [])
        if not isinstance(loadout_ids, list) or len(loadout_ids) != 6:
            raise ValueError(
                f"NPC {npc_id!r}: loadout must be list of 6 card_ids, "
                f"got {loadout_ids!r}"
            )
        if any(not isinstance(c, str) or not c for c in loadout_ids):
            raise ValueError(
                f"NPC {npc_id!r}: loadout entries must be non-empty strings"
            )
        npcs.append(NPC(
            npc_id=npc_id,
            name=str(data.get("name", npc_id)),
            tier=tier_id,
            rank=int(data.get("rank", TIER_RANK_FALLBACK.get(tier_id, 0))),
            flavor=str(data.get("flavor", "")),
            bio=str(data.get("bio", "")),
            loadout=tuple(loadout_ids),
        ))

    return Roster(
        roster_version=roster_version,
        description=description,
        tiers=tuple(tiers),
        npcs=tuple(npcs),
    )


# Module-level cache of the bundled roster -- the JSON files don't change at
# runtime, and loading is cheap but not trivial. Tests can clear it via
# ``clear_roster_cache``.
_ROSTER_CACHE: Optional[Roster] = None


def get_roster(*, force_reload: bool = False) -> Roster:
    """Return the bundled NPC roster (cached)."""
    global _ROSTER_CACHE
    if _ROSTER_CACHE is None or force_reload:
        _ROSTER_CACHE = load_roster()
    return _ROSTER_CACHE


def clear_roster_cache() -> None:
    """Drop the cached roster so the next get_roster() reloads from disk."""
    global _ROSTER_CACHE
    _ROSTER_CACHE = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_tiers() -> List[str]:
    """Return tier ids in rank order (rookie -> champion)."""
    return get_roster().tier_ids()


def list_npcs(tier: Optional[str] = None) -> List[NPC]:
    """List NPCs, optionally filtered by tier_id.

    Returns NPCs sorted by (rank, npc_id) so iteration is stable across runs.
    """
    roster = get_roster()
    if tier is not None:
        if tier not in roster.by_tier:
            raise ValueError(
                f"unknown tier {tier!r}; available: {list(roster.by_tier)}"
            )
        return list(roster.by_tier[tier])
    return sorted(roster.npcs, key=lambda n: (n.rank, n.npc_id))


def get_npc(npc_id: str) -> NPC:
    """Return one NPC by id. Raises KeyError if not found."""
    roster = get_roster()
    if npc_id not in roster.by_id:
        raise KeyError(
            f"unknown npc_id {npc_id!r}; "
            f"call list_npcs() to enumerate"
        )
    return roster.by_id[npc_id]


def npc_card_dicts(npc: NPC,
                   *,
                   catalog_name: str = DEFAULT_CATALOG_ID) -> List[Dict[str, Any]]:
    """Resolve an NPC's loadout to a list of raw catalog card payloads.

    Used by callers that want to render the NPC's team or pass it through MCP
    as a loadout argument. The catalog is loaded fresh each call -- callers
    that need many NPCs in a hot loop should pass a pre-loaded catalog via
    the lower-level ``_resolve_loadout_cards``.
    """
    cat = load_catalog(catalog_name)
    return _resolve_loadout_cards(npc, cat)


def _resolve_loadout_cards(npc: NPC, catalog) -> List[Dict[str, Any]]:
    """Resolve NPC card_ids -> catalog card payloads (raw dicts)."""
    out: List[Dict[str, Any]] = []
    for cid in npc.loadout:
        if cid not in catalog.by_id:
            raise ValueError(
                f"NPC {npc.npc_id!r}: card_id {cid!r} not in catalog "
                f"{catalog.pack_id!r}"
            )
        out.append(dict(catalog.by_id[cid].payload))
    return out


def npc_loadout(npc: NPC,
                *,
                catalog_name: str = DEFAULT_CATALOG_ID) -> Loadout:
    """Resolve an NPC's loadout to an engine.Loadout, ready for resolve_match.

    Raises ValueError if any card_id is missing from the catalog or the
    resulting team fails Loadout validation (duplicate ids, species cap, etc).
    """
    cat = load_catalog(catalog_name)
    raw = _resolve_loadout_cards(npc, cat)
    cards = tuple(load_card_dict(c) for c in raw)
    return Loadout(cards=cards)


__all__ = [
    "NPC",
    "Tier",
    "Roster",
    "load_roster",
    "get_roster",
    "clear_roster_cache",
    "list_tiers",
    "list_npcs",
    "get_npc",
    "npc_loadout",
    "npc_card_dicts",
    "DEFAULT_ROSTER_PKG",
    "TIER_RANK_FALLBACK",
]
