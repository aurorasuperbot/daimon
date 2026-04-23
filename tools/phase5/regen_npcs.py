"""Regenerate `daimon/npcs/<tier>/<npc>.json` + manifest from npc_design.py.

Validates every loadout against the v1_alpha catalog (catalog hit, species
cap, no duplicates, exactly 6 cards) BEFORE writing. Aborts on any error
so we never ship a roster the engine refuses to load.

Usage::

    python tools/phase5/regen_npcs.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the daimon package importable when run from repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from daimon.cards import load_card_dict           # noqa: E402
from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog  # noqa: E402
from daimon.engine import Loadout                 # noqa: E402

from tools.phase5.npc_design import (             # noqa: E402
    ROSTER, ROSTER_DESCRIPTION, ROSTER_VERSION,
    TIER_LABEL, TIER_ORDER, TIER_RANK, TIER_RULE,
)


NPC_ROOT = REPO_ROOT / "daimon" / "npcs"


def validate_loadout(npc: dict, catalog) -> Loadout:
    """Resolve + validate one NPC's loadout. Raises on any failure."""
    cids = npc["loadout"]
    if len(cids) != 6:
        raise ValueError(f"{npc['npc_id']}: loadout must be 6 cards, got {len(cids)}")
    seen = set()
    for cid in cids:
        if cid in seen:
            raise ValueError(f"{npc['npc_id']}: duplicate card_id {cid!r}")
        seen.add(cid)
        if cid not in catalog.by_id:
            raise ValueError(
                f"{npc['npc_id']}: card_id {cid!r} not in catalog "
                f"{catalog.pack_id!r}"
            )
    cards = tuple(load_card_dict(dict(catalog.by_id[cid].payload)) for cid in cids)
    return Loadout(cards=cards)  # raises on species cap / dup


def write_npc(tier: str, npc: dict) -> None:
    """Write one NPC JSON file."""
    out_dir = NPC_ROOT / tier
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "npc_id":  npc["npc_id"],
        "name":    npc["name"],
        "tier":    tier,
        "rank":    TIER_RANK[tier],
        "flavor":  npc["flavor"],
        "bio":     npc["bio"],
        "loadout": list(npc["loadout"]),
    }
    out_path = out_dir / f"{npc['npc_id']}.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_manifest() -> None:
    """Rewrite daimon/npcs/manifest.json from the design."""
    manifest = {
        "schema_version": 1,
        "roster_version": ROSTER_VERSION,
        "description":    ROSTER_DESCRIPTION,
        "tiers": [
            {
                "tier_id": tier,
                "rank":    TIER_RANK[tier],
                "label":   TIER_LABEL[tier],
                "rule":    TIER_RULE[tier],
                "npcs":    [npc["npc_id"] for npc in ROSTER[tier]],
            }
            for tier in TIER_ORDER
        ],
    }
    (NPC_ROOT / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Validate loadouts without writing any files.")
    args = ap.parse_args()

    cat = load_catalog(DEFAULT_CATALOG_ID)
    print(f"Loaded catalog {cat.pack_id} with {len(cat.by_id)} cards")

    n_total = 0
    for tier in TIER_ORDER:
        npcs = ROSTER[tier]
        if len(npcs) != 5:
            print(f"  ERROR: tier {tier} must have 5 NPCs, got {len(npcs)}")
            return 1
        for npc in npcs:
            try:
                ld = validate_loadout(npc, cat)
            except Exception as e:
                print(f"  FAIL  {tier:9s} {npc['npc_id']:25s}: {e}")
                return 1
            n_total += 1
            print(f"  ok    {tier:9s} {npc['npc_id']:25s}: 6 cards loaded")
    print(f"\nValidated {n_total} NPC loadouts.")

    if args.dry_run:
        print("(dry-run; no files written)")
        return 0

    for tier in TIER_ORDER:
        for npc in ROSTER[tier]:
            write_npc(tier, npc)
    write_manifest()
    print(f"\nWrote {n_total} NPC files + manifest under {NPC_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
