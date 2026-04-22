#!/usr/bin/env python3
"""Phase 4a: reconcile legacy scaffolded rarities.

V1 locks **exactly 2 legendaries** (voidking_morr + world_eater) and
**exactly 12 epics** (the 12 Phase-3 anchors). The current v1_alpha pack
carries legacy scaffolding from pre-Phase-1 work:
  - 6 scaffolded "legendaries" (pre-Phase-3 placeholders)
  - 9 scaffolded "epics" (pre-Phase-3 placeholders)

This script demotes all 15 to `rare` — both in each card's JSON AND in the
manifest. Stats + triggers are intentionally UNTOUCHED here; Phase 5 (sim
balance pass) will identify individual cards needing stat tuning after
the full pool is authored. The cards remain mechanically strong but their
rarity metadata now matches the V1 distribution target.

Why JSON AND manifest? Render layer + dm_pull both read rarity from manifest;
loader + display metadata read it from JSON. Must stay in sync.

Post-Phase 4a rarity counts (projected):
  legendary: 2  (voidking_morr, world_eater)
  epic:     12  (Phase-3 anchors)
  rare:     13 existing + 15 demoted = 28
  uncommon: 15 (unchanged)
  common:   23 (unchanged)
  Total:    80 (unchanged — no new cards yet; Phase 4b+c fills out to 200)

Run once, idempotent. Re-running on an already-reconciled pack is a no-op.
"""

from __future__ import annotations

import json
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent.parent / "daimon" / "catalog" / "v1_alpha"
MANIFEST_PATH = PACK_DIR / "manifest.json"

# Legacy legendaries that were scaffolded before Phase 1/3 locked the
# 2-legendary policy. All six demoted to `rare`.
LEGACY_LEGENDARIES_TO_RARE = [
    "storm_celestial",
    "voltcat_apex",
    "echo_lich",
    "pyrotyrant",
    "leviathan_prime",
    "worldroot_colossus",
]

# Scaffolded epics that predate the Phase-3 archetype-anchor design.
# The 12 Phase-3 epic anchors stay at epic; these 9 demote to rare.
LEGACY_EPICS_TO_RARE = [
    "bulwarthog",
    "mindroot",
    "inferno_lynx",
    "ashen_phoenix",
    "maelstrom_serpent",
    "forest_warden",
    "plasma_djinn",
    "abyss_warden",
    "nullhound",
]

TO_DEMOTE = LEGACY_LEGENDARIES_TO_RARE + LEGACY_EPICS_TO_RARE


def main() -> None:
    demoted_json = 0
    # 1. Patch each card JSON's `rarity` field.
    for cid in TO_DEMOTE:
        path = PACK_DIR / f"{cid}.json"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing — catalog drift")
        data = json.loads(path.read_text())
        if data.get("rarity") == "rare":
            # Already reconciled; idempotent no-op.
            continue
        data["rarity"] = "rare"
        # Also patch the `art` path (convention: art/<rarity>/<cid>.png) to
        # keep render-layer conventions consistent. Non-load-bearing but tidy.
        data["art"] = f"art/rare/{cid}.png"
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        demoted_json += 1

    # 2. Patch manifest entries.
    manifest = json.loads(MANIFEST_PATH.read_text())
    demoted_manifest = 0
    for entry in manifest["cards"]:
        if entry["card_id"] in TO_DEMOTE and entry["rarity"] != "rare":
            entry["rarity"] = "rare"
            entry["file"] = f"{entry['card_id']}.json"
            demoted_manifest += 1

    # 3. Bump manifest version + refresh description to reflect the reconciliation.
    manifest["version"] = "0.4.1"

    # Rebuild rarity histogram from scratch (trustworthy count).
    from collections import Counter
    hist = Counter(e["rarity"] for e in manifest["cards"])
    manifest["description"] = (
        f"DAIMON V1 alpha bundled creature pool. {len(manifest['cards'])} "
        f"monsters across 5 elements and 6 archetypes. Phase 4a reconciled "
        f"legacy scaffolded rarities to match V1 target distribution. "
        f"Current: {hist.get('common', 0)}C/{hist.get('uncommon', 0)}U/"
        f"{hist.get('rare', 0)}R/{hist.get('epic', 0)}E/{hist.get('legendary', 0)}L. "
        f"Phase 4b+c fills out to the 200-card target "
        f"(100C/60U/28R/12E/2L)."
    )

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Reconciled {demoted_json} card JSONs + {demoted_manifest} manifest entries.")
    print(f"Version bumped to {manifest['version']}.")
    print(f"New distribution: {dict(hist)}")


if __name__ == "__main__":
    main()
