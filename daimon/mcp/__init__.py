"""MCP server — exposes dm_* tools for AI agents.

27-tool surface (21 locked 2026-04-21 + dm_init follow-up + 3 NPC roster
tools shipped 2026-04-22 + dm_mine_status deprecated alias + dm_pvp_reveal
added 2026-04-24 when arena wiring landed). See `daimon/mcp/server.py`
for the authoritative docstring + tool signatures. High-level groups:

  Identity      dm_init, dm_whoami, dm_register,
                dm_mine_status (deprecated alias)
  Catalog       dm_expansions, dm_catalog_list, dm_catalog_card, dm_card_compare
  Collection    dm_collection, dm_pull
  Loadouts      dm_loadout_validate, dm_loadout_save, dm_loadout_list,
                dm_loadout_load
  Match (PvE)   dm_match, dm_npcs, dm_npc, dm_match_npc
  PvP (arena)   dm_pvp_challenge, dm_pvp_accept, dm_pvp_reveal,
                dm_pvp_status, dm_pvp_my_matches
  Arena state   dm_leaderboard, dm_my_rank
  Disputes      dm_dispute_open, dm_card_propose

Arena-bound tools (`dm_register`, `dm_pvp_*`, `dm_leaderboard`,
`dm_my_rank`, `dm_dispute_open`, `dm_card_propose`) are thin shims over
`daimon.arena.ops` — they shell out to the `gh` CLI to publish to the
arena repo via the commit-reveal protocol documented in
`daimon/arena/encoding.py`.

Run as a stdio server with:
  python -m daimon.mcp

Or programmatically:
  from daimon.mcp import mcp, run_stdio
"""

# Lazy import so importing the package does not require `mcp` to be installed.
# Only the actual server entry points need the dependency.
try:
    from daimon.mcp.server import mcp, run_stdio
    __all__ = ["mcp", "run_stdio"]
except ImportError:
    __all__ = []
