"""DAIMON example loadouts.

Showcase loadouts demonstrate the V1 legendary mutations (L1-L6) in real
6-card teams that exercise each rule-changer's intended interaction. Unlike
NPCs (which are tier-graded opponents in `daimon/npcs/`), showcase loadouts
are documentation: "this is what an L1 INFERNO burn-stack team looks like
in the V1 catalog."

Public entry points (see ``loadouts.loader``):

  list_showcase_loadouts()              -> [ShowcaseLoadout, ...]
  get_showcase_loadout(loadout_id)      -> ShowcaseLoadout
  resolve_showcase_loadout(showcase)    -> engine.Loadout (cards resolved)

The bundle lives under ``daimon/loadouts/showcase/`` with ``manifest.json``
as the index and one ``<loadout_id>.json`` file per loadout.
"""

from daimon.loadouts.loader import (
    DEFAULT_SHOWCASE_PKG,
    ShowcaseLoadout,
    get_showcase_loadout,
    list_showcase_loadouts,
    resolve_showcase_loadout,
)

__all__ = [
    "ShowcaseLoadout",
    "DEFAULT_SHOWCASE_PKG",
    "list_showcase_loadouts",
    "get_showcase_loadout",
    "resolve_showcase_loadout",
]
