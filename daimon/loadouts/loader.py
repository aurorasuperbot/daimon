"""Showcase loadout loader.

Bundle layout:

  daimon/loadouts/showcase/
    manifest.json                   # {version, description, loadouts:[{id,demonstrates,name}]}
    <loadout_id>.json               # {loadout_id, name, demonstrates, flavor, description, loadout:[card_id,...]}

A showcase loadout is exactly 6 card_ids drawn from a catalog (default
v1_alpha). Loaders defer catalog resolution to `resolve_showcase_loadout`
so manifest reads are cheap and engine-free.

  list_showcase_loadouts()       -> [ShowcaseLoadout, ...] (manifest order)
  get_showcase_loadout(id)       -> ShowcaseLoadout
  resolve_showcase_loadout(sl)   -> engine.Loadout (validates against catalog)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import List, Optional

from daimon.cards import load_card_dict
from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
from daimon.engine import Loadout

DEFAULT_SHOWCASE_PKG = "daimon.loadouts.showcase"


@dataclass(frozen=True)
class ShowcaseLoadout:
    """One showcase loadout: legendary + 5 supporting cards.

    `demonstrates` is the rule_change id (L1..L6) that this loadout is
    designed to exercise. `card_ids` are catalog card_ids; resolve them
    via `resolve_showcase_loadout` to get an `engine.Loadout`.
    """
    loadout_id: str
    name: str
    demonstrates: str          # "L1".."L6"
    flavor: str
    description: str
    card_ids: tuple[str, ...]  # exactly 6, validated at resolve-time


def _showcase_dir(pkg: str = DEFAULT_SHOWCASE_PKG) -> Path:
    """Resolve the bundled showcase-loadout directory."""
    root = resources.files(pkg)
    p = Path(str(root))
    if not (p / "manifest.json").is_file():
        raise FileNotFoundError(
            f"showcase manifest missing under {p} "
            f"(expected daimon/loadouts/showcase/manifest.json)"
        )
    return p


def list_showcase_loadouts(*, root: Optional[Path] = None) -> List[ShowcaseLoadout]:
    """Load all showcase loadouts in manifest order.

    `root` overrides the bundled package directory (used by tests that
    point at a temporary fixture tree).
    """
    rdir = root if root is not None else _showcase_dir()
    manifest_path = rdir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("loadouts", [])
    if not isinstance(entries, list) or not entries:
        raise ValueError("showcase manifest must declare a non-empty `loadouts` list")

    out: List[ShowcaseLoadout] = []
    seen_ids: set[str] = set()
    seen_demo: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"showcase manifest entry not an object: {entry!r}")
        lid = entry.get("loadout_id")
        if not lid or not isinstance(lid, str):
            raise ValueError(f"showcase manifest entry missing loadout_id: {entry!r}")
        if lid in seen_ids:
            raise ValueError(f"duplicate showcase loadout_id {lid!r} in manifest")
        seen_ids.add(lid)

        path = rdir / f"{lid}.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"showcase loadout file missing: {path} "
                f"(referenced by manifest entry {lid!r})"
            )
        data = json.loads(path.read_text(encoding="utf-8"))

        if data.get("loadout_id") != lid:
            raise ValueError(
                f"showcase {lid!r}: file loadout_id {data.get('loadout_id')!r} "
                f"does not match manifest entry"
            )
        demo = data.get("demonstrates")
        if not demo or not isinstance(demo, str):
            raise ValueError(f"showcase {lid!r}: missing/invalid `demonstrates` field")
        if demo in seen_demo:
            raise ValueError(
                f"showcase {lid!r}: duplicate demonstrates={demo!r} "
                f"(manifest already declared a showcase for this rule_change)"
            )
        seen_demo.add(demo)

        card_ids = data.get("loadout", [])
        if not isinstance(card_ids, list) or len(card_ids) != 6:
            raise ValueError(
                f"showcase {lid!r}: loadout must be list of 6 card_ids, "
                f"got {card_ids!r}"
            )
        if any(not isinstance(c, str) or not c for c in card_ids):
            raise ValueError(
                f"showcase {lid!r}: loadout entries must be non-empty strings"
            )

        out.append(ShowcaseLoadout(
            loadout_id=lid,
            name=str(data.get("name", lid)),
            demonstrates=demo,
            flavor=str(data.get("flavor", "")),
            description=str(data.get("description", "")),
            card_ids=tuple(card_ids),
        ))
    return out


def get_showcase_loadout(loadout_id: str) -> ShowcaseLoadout:
    """Return one showcase loadout by id. Raises KeyError if not found."""
    for sl in list_showcase_loadouts():
        if sl.loadout_id == loadout_id:
            return sl
    raise KeyError(
        f"unknown showcase loadout {loadout_id!r}; "
        f"call list_showcase_loadouts() to enumerate"
    )


def resolve_showcase_loadout(
    sl: ShowcaseLoadout,
    *,
    catalog_name: str = DEFAULT_CATALOG_ID,
) -> Loadout:
    """Resolve a showcase loadout to an engine.Loadout against `catalog_name`.

    Raises ValueError if any card_id is missing from the catalog or the
    resulting team fails Loadout validation (duplicate ids, species cap, etc).
    """
    cat = load_catalog(catalog_name)
    cards = []
    for cid in sl.card_ids:
        if cid not in cat.by_id:
            raise ValueError(
                f"showcase {sl.loadout_id!r}: card_id {cid!r} not in catalog "
                f"{cat.pack_id!r}"
            )
        cards.append(load_card_dict(dict(cat.by_id[cid].payload)))
    return Loadout(cards=tuple(cards))


__all__ = [
    "ShowcaseLoadout",
    "DEFAULT_SHOWCASE_PKG",
    "list_showcase_loadouts",
    "get_showcase_loadout",
    "resolve_showcase_loadout",
]
