#!/usr/bin/env python3
"""Phase 4e fixup: rewire NPC loadouts that reference cards retired in
`scripts/author_phase4e_normals.py`.

The 25 V1 NPCs were authored before the Phase-4b/4c expansion, so most use
the original 23 vanilla commons + a small set of rares as their building
blocks. Phase 4e retired 8 of those vanillas (plus 4 uncommons, 2 rares,
1 epic) to make room for the NORMAL element. Without this fixup, every
match-vs-NPC entry-point fails with "card_id 'X' not in catalog".

Substitution rules (in priority order):

  1. Same element as the retired card (preserve the NPC's elemental identity).
  2. Same rarity tier as the retired card (preserve the rarity-mix the NPC
     was tuned for — replacing a common with an epic would change the matchup).
  3. Not already in the loadout (engine + schema both reject duplicate
     card_ids in a single team).
  4. Pick deterministically from a per-element preference list (vanilla
     commons first, then designed commons; uncommons in author order; etc.)
     so re-running this script produces identical output.

If no candidate at the same rarity is available (shouldn't happen — every
element has 18+ commons surviving), fall back to the next-lowest rarity.

This is a one-shot maintenance script. After running, every NPC should
load through `daimon.npcs.loader` without raising.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACK = REPO / "daimon" / "catalog" / "v1_alpha"
NPC_DIR = REPO / "daimon" / "npcs"

RETIRED = {
    # commons
    "blade_foxling", "sparrowflame", "shellpup", "bubblefry",
    "scoutling", "mossbat", "jolthog", "nullkit",
    # uncommons
    "cinder_lancer", "riverotter", "anvilram", "thunderfox",
    # rares
    "flarewing", "void_serpent",
    # epic
    "mourners_lich",
}


def _load_pack() -> dict[tuple[str, str], list[str]]:
    """(element, rarity) -> deterministic preference list of card_ids that
    survived Phase 4e. Order = manifest order, which matches the chronological
    authoring order — vanilla commons end up first within each (elem, rarity)
    bucket because they were authored in Phase 1 / Phase 3."""
    manifest = json.loads((PACK / "manifest.json").read_text())
    out: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entry in manifest["cards"]:
        if entry["card_id"] in RETIRED:
            continue
        out[(entry["element"], entry["rarity"])].append(entry["card_id"])
    return out


def _retired_meta() -> dict[str, tuple[str, str]]:
    """retired card_id -> (element, rarity), looked up from the *git history*
    is too fancy. Instead we inline the metadata here — these IDs are gone
    from disk now, so we can't read them back. Hard-code the meta from the
    Phase-4e author script's RETIRE list rationale."""
    return {
        # commons (from §18 retire list)
        "blade_foxling":  ("FIRE",   "common"),
        "sparrowflame":   ("FIRE",   "common"),
        "shellpup":       ("WATER",  "common"),
        "bubblefry":      ("WATER",  "common"),
        "scoutling":      ("NATURE", "common"),
        "mossbat":        ("NATURE", "common"),
        "jolthog":        ("VOLT",   "common"),
        "nullkit":        ("VOID",   "common"),
        # uncommons
        "cinder_lancer":  ("FIRE",   "uncommon"),
        "riverotter":     ("WATER",  "uncommon"),
        "anvilram":       ("NATURE", "uncommon"),
        "thunderfox":     ("VOLT",   "uncommon"),
        # rares
        "flarewing":      ("FIRE",   "rare"),
        "void_serpent":   ("VOID",   "rare"),
        # epic
        "mourners_lich":  ("VOID",   "epic"),
    }


def _pick_substitute(
    retired_id: str,
    loadout: list[str],
    pack: dict[tuple[str, str], list[str]],
    meta: dict[str, tuple[str, str]],
) -> str:
    elem, rarity = meta[retired_id]
    # Try same (elem, rarity) first; then walk down rarity tiers.
    rarity_fallback = ["common", "uncommon", "rare", "epic", "legendary"]
    start = rarity_fallback.index(rarity)
    # Prefer SAME tier, then lower (cheaper) tiers, then higher tiers as last
    # resort. NPC rarity-mix gets preserved as much as the catalog allows.
    order = (
        rarity_fallback[start:start + 1]                  # same tier
        + list(reversed(rarity_fallback[:start]))         # cheaper tiers
        + rarity_fallback[start + 1:]                     # more expensive
    )
    seen = set(loadout)
    for tier in order:
        for cid in pack.get((elem, tier), []):
            if cid in seen:
                continue
            return cid
    raise RuntimeError(
        f"No substitute found for {retired_id} (elem={elem}, rarity={rarity}); "
        f"loadout already has: {loadout}"
    )


def main() -> None:
    pack = _load_pack()
    meta = _retired_meta()

    rewired = 0
    inspected = 0
    for path in sorted(NPC_DIR.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        data = json.loads(path.read_text())
        loadout = data.get("loadout")
        if not isinstance(loadout, list):
            continue
        inspected += 1
        if not any(cid in RETIRED for cid in loadout):
            continue

        # Substitute in-place, preserving the loadout's slot order. We pick
        # substitutes one-at-a-time so each pick sees the partially-updated
        # loadout (preventing two retired cards from picking the same
        # substitute and creating a duplicate).
        new_loadout: list[str] = []
        for cid in loadout:
            if cid in RETIRED:
                sub = _pick_substitute(cid, new_loadout + loadout[len(new_loadout) + 1:], pack, meta)
                # ^ pass the post-substitution view so the picker avoids
                # collisions with both already-substituted slots AND the
                # not-yet-processed tail (which still holds its originals).
                # Original cards that are themselves retired won't block the
                # picker because retired cards aren't in `pack` either.
                new_loadout.append(sub)
                print(f"  {path.relative_to(REPO)}: {cid} -> {sub}")
            else:
                new_loadout.append(cid)

        # Sanity check: no duplicates, no retired ids remain.
        assert len(new_loadout) == len(set(new_loadout)), (
            f"Duplicate after substitution in {path}: {new_loadout}"
        )
        assert not (set(new_loadout) & RETIRED), (
            f"Retired id remained in {path}: {set(new_loadout) & RETIRED}"
        )

        data["loadout"] = new_loadout
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        rewired += 1

    print()
    print(f"Inspected {inspected} NPC files; rewired {rewired}.")


if __name__ == "__main__":
    main()
