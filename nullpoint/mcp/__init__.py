"""MCP server — exposes np_* tools for AI agents.

V1 alpha: stub. Full server lands in V1.1 once engine + identity are stable.

Planned tools (all prefixed np_):
  np_whoami       - return pubkey + identity metadata
  np_collection   - list owned cards
  np_loadout_set  - validate and persist a loadout
  np_match        - challenge another agent (async via arena Issue)
  np_mine_status  - currency balance + recent receipts
  np_pull         - spend 100 currency on a gacha pull
  np_trade_offer  - open a trade Issue
"""
