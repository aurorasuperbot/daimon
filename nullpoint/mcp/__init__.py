"""MCP server — exposes np_* tools for AI agents.

V1 scope:
  np_whoami            return pubkey + identity metadata
  np_match             resolve a deterministic match between two loadouts
  np_loadout_validate  validate a loadout JSON without playing
  np_collection        list owned cards (reads local collection.json)
  np_pull              STUB — gacha pull lands V1.1 (mining daemon dep)
  np_mine_status       STUB until daemon ships; reads ledger if present

Run as a stdio server with:
  python -m nullpoint.mcp

Or programmatically:
  from nullpoint.mcp import mcp, run_stdio
"""

# Lazy import so importing the package does not require `mcp` to be installed.
# Only the actual server entry points need the dependency.
try:
    from nullpoint.mcp.server import mcp, run_stdio
    __all__ = ["mcp", "run_stdio"]
except ImportError:
    __all__ = []
