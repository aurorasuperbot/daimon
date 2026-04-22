#!/usr/bin/env python3
"""One-shot migration: V1 test card fixtures (with `slot`) → V2 (element/species).

Rewrites every `tests/fixtures/*.json` and `daimon/play/fixtures/*.json`
that has a `slot` field, dropping `slot` and adding `element` + `species`.

Defaults all test fixtures to NATURE element (neutral against nobody) so
existing combat tests stay deterministic across the migration — they test
trigger mechanics, not type effectiveness.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


DEFAULT_ELEMENT = "NATURE"

# slot name → neutral default element for fixtures (all NATURE, deterministic)
SLOT_TO_ELEMENT: dict[str, str] = {
    "HEAD":  "NATURE",
    "TORSO": "NATURE",
    "ARM_L": "NATURE",
    "ARM_R": "NATURE",
    "LEGS":  "NATURE",
    "CORE":  "NATURE",
}


def migrate_card_dict(d: dict) -> dict:
    out = dict(d)
    slot = out.pop("slot", None)
    if slot is not None and "element" not in out:
        out["element"] = SLOT_TO_ELEMENT.get(slot, DEFAULT_ELEMENT)
    if "species" not in out:
        # species = card_id, stripped of leading "test_" prefix for readability
        card_id = out.get("card_id", "unknown")
        out["species"] = card_id[5:] if card_id.startswith("test_") else card_id
    return out


def migrate_file(path: Path) -> bool:
    """Return True if migrated, False if already V2 or not a card."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ! {path.name}: parse error {e}")
        return False
    if not isinstance(raw, dict) or "card_id" not in raw:
        return False
    if "slot" not in raw:
        return False
    migrated = migrate_card_dict(raw)
    path.write_text(json.dumps(migrated, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    targets = [
        repo_root / "tests" / "fixtures",
        repo_root / "daimon" / "play" / "fixtures",
    ]
    migrated_count = 0
    for target in targets:
        if not target.exists():
            continue
        print(f"Scanning {target}")
        for path in sorted(target.glob("*.json")):
            if migrate_file(path):
                migrated_count += 1
                print(f"  ✓ migrated {path.name}")
    print(f"\nMigrated {migrated_count} fixtures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
