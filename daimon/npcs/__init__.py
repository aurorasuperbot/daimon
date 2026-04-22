"""DAIMON NPC tier roster.

V1 alpha ships 25 named opponents across 5 tiers (Rookie, Novice, Veteran,
Elite, Champion). Each NPC has a fixed loadout drawn from the v1_alpha
catalog so a player progressing through the roster fights deterministic,
named opponents -- not randomly-generated decks.

Public entry points:

  list_tiers()                    -> ['rookie', 'novice', ...] in rank order
  list_npcs(tier='rookie')        -> [NPC, NPC, ...]
  get_npc('sparring_sam')         -> NPC
  npc_loadout(npc)                -> engine.Loadout (cards resolved)
  npc_card_dicts(npc)             -> [card_dict, ...] (for MCP/render)

The roster JSON lives under daimon/npcs/<tier>/<npc_id>.json with
daimon/npcs/manifest.json as the index. See loader.py docstring for the
file shape.
"""

from daimon.npcs.loader import (
    DEFAULT_ROSTER_PKG,
    NPC,
    Roster,
    Tier,
    clear_roster_cache,
    get_npc,
    get_roster,
    list_npcs,
    list_tiers,
    load_roster,
    npc_card_dicts,
    npc_loadout,
)

__all__ = [
    "NPC",
    "Roster",
    "Tier",
    "DEFAULT_ROSTER_PKG",
    "load_roster",
    "get_roster",
    "clear_roster_cache",
    "list_tiers",
    "list_npcs",
    "get_npc",
    "npc_loadout",
    "npc_card_dicts",
]
