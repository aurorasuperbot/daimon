"""Apply canon rewrite to all 200 catalog cards.

Reads tools/canon_rewrite/mapping.py and patches each card JSON in
daimon/catalog/v1_alpha/ with its new name/species/canon/flavor plus renamed
moves. Engine-stable fields (card_id, element, atk, def, hp, spd, triggers,
rule_change, rarity, archetype, art) are left untouched.

Idempotent: running it twice produces the same output. Preserves JSON key
ordering to match the existing catalog convention (card_id and species first,
then mechanical fields, then name/flavor).

Usage:
    python3 tools/canon_rewrite/apply.py [--dry-run]

Exits non-zero on any inconsistency (missing card in MAPPING, unmapped move,
unknown canon, etc.) — fail loud, never silently partial.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from mapping import MAPPING  # type: ignore[import-not-found]


CATALOG_DIR = Path(__file__).resolve().parent.parent.parent / "daimon" / "catalog" / "v1_alpha"
VALID_CANONS = {"OLYMPIAN", "AESIR", "NETJER", "KAMI", "TEOTL", "APOCRYPHA"}


def _ordered_card(raw: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild the card dict with a canonical key order.

    The existing catalog order varies slightly between cards, but the common
    shape is: card_id, species, element, stats, triggers, rule_change (opt),
    name, flavor, rarity, archetype (opt), canon (new), art (opt), moves.
    We normalize to a predictable order so diffs are clean and future diffs
    don't churn key ordering.
    """
    key_order = [
        "card_id",
        "species",
        "element",
        "atk",
        "def",
        "hp",
        "spd",
        "triggers",
        "rule_change",
        "name",
        "flavor",
        "rarity",
        "archetype",
        "canon",
        "art",
        "moves",
    ]
    merged = dict(raw)
    merged.update(patch)
    out: Dict[str, Any] = {}
    for k in key_order:
        if k in merged:
            out[k] = merged[k]
    # Any keys not in the canonical order tail-append, so we never drop data.
    for k, v in merged.items():
        if k not in out:
            out[k] = v
    return out


def _rewrite_moves(existing: List[Dict[str, Any]], rename: Dict[str, str]) -> List[Dict[str, Any]]:
    """Return a new moves list with each move's `name` field remapped.

    Every existing move name MUST appear in `rename` either as a key (a
    pre-rename original) or as a value (an already-renamed target — apply.py
    is idempotent so re-runs against an already-rewritten catalog don't fail).
    Unmapped moves are a data error — raise rather than silently drop.
    """
    targets = set(rename.values())
    out: List[Dict[str, Any]] = []
    for move in existing:
        old_name = move.get("name")
        if old_name in rename:
            new_name = rename[old_name]
        elif old_name in targets:
            # Already-renamed in a prior apply run — preserve as-is.
            new_name = old_name
        else:
            raise KeyError(f"move {old_name!r} not in rename map: {rename}")
        new_move = dict(move)
        new_move["name"] = new_name
        out.append(new_move)
    return out


def apply_to_card(path: Path, dry_run: bool = False) -> Dict[str, str]:
    """Patch a single card JSON in place.

    Returns a summary dict: {before_name, after_name, canon, changed_fields}.
    Raises on any inconsistency.
    """
    card_id = path.stem
    if card_id == "manifest":
        return {}
    if card_id not in MAPPING:
        raise KeyError(f"{card_id} not in MAPPING — every card must have an entry")

    entry = MAPPING[card_id]
    if entry["canon"] not in VALID_CANONS:
        raise ValueError(f"{card_id}: invalid canon {entry['canon']!r}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("card_id") != card_id:
        raise ValueError(f"{card_id}: card_id field mismatch ({raw.get('card_id')!r})")

    # `species` is deliberately NOT patched. It's an engine identifier used by
    # NPC loadout resolution and the MAX_SAME_SPECIES loadout cap (see
    # engine/loadout.py::MAX_SAME_SPECIES). Currently species == card_id for all
    # cards except the skogsra evolution pair (barkpup, barkguard). The
    # mythological species stem in mapping.py is documentary — it's embedded in
    # `name` / `flavor`, not the engine species field.
    patch: Dict[str, Any] = {
        "name": entry["name"],
        "canon": entry["canon"],
        "flavor": entry["flavor"],
    }
    if "moves" in raw:
        patch["moves"] = _rewrite_moves(raw["moves"], entry["moves"])

    # FLUX → SYNCRETIC archetype migration (engine vocab already renamed in
    # engine/types.py::ARCHETYPE_IDS). Catalog JSONs carrying the legacy FLUX
    # string are rewritten to SYNCRETIC here so apply.py is a single,
    # self-contained migration step. Idempotent on already-SYNCRETIC cards.
    if raw.get("archetype") == "FLUX":
        patch["archetype"] = "SYNCRETIC"

    new_card = _ordered_card(raw, patch)

    before_name = raw.get("name", "")
    after_name = new_card["name"]

    if not dry_run:
        # Preserve trailing newline convention used across the catalog.
        path.write_text(json.dumps(new_card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "card_id": card_id,
        "before": before_name,
        "after": after_name,
        "canon": entry["canon"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply canon rewrite to catalog JSONs.")
    parser.add_argument("--dry-run", action="store_true", help="don't write files, just report")
    args = parser.parse_args()

    json_paths = sorted(p for p in CATALOG_DIR.glob("*.json") if p.stem != "manifest")
    if len(json_paths) != 200:
        print(f"ERROR: expected 200 card JSONs, found {len(json_paths)}", file=sys.stderr)
        return 1

    canon_counts: Dict[str, int] = {c: 0 for c in VALID_CANONS}
    summaries = []
    for path in json_paths:
        summary = apply_to_card(path, dry_run=args.dry_run)
        summaries.append(summary)
        canon_counts[summary["canon"]] += 1

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"{mode}: {len(summaries)} cards processed")
    print("\nPer-Canon counts:")
    for canon in ("OLYMPIAN", "AESIR", "NETJER", "KAMI", "TEOTL", "APOCRYPHA"):
        print(f"  {canon:10} {canon_counts[canon]}")

    # Print a few notable renames for sanity
    print("\nSample renames (first 6 legendaries + 3 phoenix + 5 audit swaps):")
    highlight = [
        "magma_tyrant", "worldroot_sentinel", "tide_empress", "tempest_apex", "voidking_morr", "world_eater",
        "ashen_phoenix", "solar_phoenix", "concord_phoenix",
        "tidewyrm", "mindroot", "sea_warden", "boulder_mole", "shadebishop",
    ]
    by_id = {s["card_id"]: s for s in summaries if s}
    for cid in highlight:
        s = by_id.get(cid)
        if s:
            print(f"  {cid:28} {s['before']!r:40} -> {s['after']!r} ({s['canon']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
