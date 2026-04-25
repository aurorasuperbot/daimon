"""Showcase loadout loader + unified file loader.

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

The unified `load_loadout_file(path)` accepts any of three on-disk shapes
and returns a normalized `(engine.Loadout, raw_payloads)` tuple — the
canonical entry point for CLI commands (``daimon match``, ``daimon
match-npc``) and any other surface that takes a user-supplied loadout
JSON path. See its docstring for the format matrix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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


# ---------------------------------------------------------------------------
# Unified file loader — one entry point for every CLI surface
# ---------------------------------------------------------------------------

def _is_showcase_shape(data: Any) -> bool:
    """True iff `data` looks like a showcase-format loadout payload.

    Showcase payloads have a top-level ``loadout`` key holding a list of
    card_id strings (NOT card-pack dicts). We require BOTH the key and
    the str-only list to disambiguate from a pathological file that
    happens to use the word ``loadout`` for something else.
    """
    if not isinstance(data, dict):
        return False
    inner = data.get("loadout")
    if not isinstance(inner, list) or not inner:
        return False
    return all(isinstance(x, str) for x in inner)


def _resolve_showcase_dict(
    data: Dict[str, Any],
    *,
    catalog_name: str,
    source: Union[str, Path],
) -> Tuple[Loadout, List[Dict[str, Any]]]:
    """Resolve an in-memory showcase dict against the catalog.

    Returns (engine.Loadout, raw_card_payloads). The raw payloads are
    deep-copied from the catalog so a mutation in the caller can't
    poison the catalog cache.
    """
    card_ids = data["loadout"]  # validated by _is_showcase_shape
    if len(card_ids) != 6:
        raise ValueError(
            f"loadout {source!s}: showcase format requires exactly 6 card_ids, "
            f"got {len(card_ids)}"
        )

    cat = load_catalog(catalog_name)
    cards = []
    payloads: List[Dict[str, Any]] = []
    for cid in card_ids:
        if cid not in cat.by_id:
            raise ValueError(
                f"loadout {source!s}: card_id {cid!r} not in catalog "
                f"{cat.pack_id!r}"
            )
        payload = dict(cat.by_id[cid].payload)
        payloads.append(payload)
        cards.append(load_card_dict(payload))
    return Loadout(cards=tuple(cards)), payloads


def loadout_from_data(
    data: Any,
    *,
    catalog_name: str = DEFAULT_CATALOG_ID,
    source: str = "<payload>",
) -> Tuple[Loadout, List[Dict[str, Any]]]:
    """Normalize any-shape loadout JSON → (engine.Loadout, raw_payloads).

    Accepted shapes (auto-detected by structure, no flag needed):

      1. **Bare list** — legacy compact form::

             [{cardobj}, {cardobj}, ...]

      2. **Cards dict** — full stat-block, used by hand-rolled teams::

             {"name": "...", "cards": [{cardobj}, ...]}

      3. **Showcase dict** — catalog-id refs, used by bundled examples::

             {"loadout_id": "...", "name": "...", "demonstrates": "L1",
              "loadout": ["card_id_1", "card_id_2", ..., "card_id_6"]}

    Returns ``(loadout, raw_payloads)``:

      * ``loadout`` — the engine-ready ``daimon.engine.Loadout``
      * ``raw_payloads`` — the per-card pack dicts (with ``name``,
        ``rarity``, ``art_path`` etc. intact) so callers can feed them
        to ``publish_match_state`` for HUD display.

    ``source`` is used only to make error messages locatable (file path,
    tool arg name, etc.).

    Raises ValueError on any invalid format. Showcase resolution may
    raise if a card_id is missing from the named catalog.
    """
    # Shape 3: showcase format (must be checked BEFORE the cards-dict
    # branch — a showcase payload happens to be a dict, but its
    # `loadout` key holds strings, not card objects).
    if _is_showcase_shape(data):
        return _resolve_showcase_dict(data, catalog_name=catalog_name, source=source)

    # Shape 2: {"cards": [...]} — full stat-block dict.
    if isinstance(data, dict) and "cards" in data:
        raw = data["cards"]
        if not isinstance(raw, list):
            raise ValueError(
                f"loadout {source}: `cards` must be a list, got {type(raw).__name__}"
            )
    # Shape 1: bare list of card dicts.
    elif isinstance(data, list):
        raw = data
    else:
        keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        raise ValueError(
            f"loadout {source}: unrecognized format. Expected one of:\n"
            f"  - a JSON list of card objects, or\n"
            f"  - {{\"cards\": [...]}} (full stat-block), or\n"
            f"  - {{\"loadout_id\": ..., \"loadout\": [\"card_id\", ...]}} (showcase)\n"
            f"got top-level: {keys}"
        )

    if not isinstance(raw, list) or not raw:
        raise ValueError(f"loadout {source}: card list must be non-empty")
    if any(not isinstance(c, dict) for c in raw):
        raise ValueError(
            f"loadout {source}: each entry must be a card object (dict). "
            f"If you meant to reference catalog cards by id, use the showcase "
            f"format with a `loadout_id` field."
        )

    payloads = [dict(c) for c in raw]
    cards = tuple(load_card_dict(c) for c in payloads)
    return Loadout(cards=cards), payloads


def load_loadout_file(
    path: Union[str, Path],
    *,
    catalog_name: str = DEFAULT_CATALOG_ID,
) -> Tuple[Loadout, List[Dict[str, Any]]]:
    """Load any supported loadout JSON file → (engine.Loadout, raw_payloads).

    Thin file-IO wrapper around ``loadout_from_data`` — see that function
    for the full format matrix.

    Raises:
      FileNotFoundError — path doesn't exist.
      ValueError       — JSON unparseable, format unrecognized, or a
                         showcase card_id missing from the catalog.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as e:
        raise ValueError(f"loadout {path!s}: cannot read file: {e}") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"loadout {path!s}: invalid JSON: {e}") from e

    return loadout_from_data(data, catalog_name=catalog_name, source=str(path))


__all__ = [
    "ShowcaseLoadout",
    "DEFAULT_SHOWCASE_PKG",
    "list_showcase_loadouts",
    "get_showcase_loadout",
    "resolve_showcase_loadout",
    "loadout_from_data",
    "load_loadout_file",
]
