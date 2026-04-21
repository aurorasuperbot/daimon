# Using NULLPOINT through MCP

If you're an agent with MCP support (Claude Code, Cursor, custom client), you can use NULLPOINT as a tool server instead of shelling out to `np`.

## Install

```bash
pip install 'nullpoint[mcp]'
```

## Run

The server speaks MCP over stdio. Configure your client to launch:

```bash
np-mcp
# or equivalently:
python -m nullpoint.mcp
```

For Claude Code, add to `~/.config/claude/mcp_servers.json`:

```json
{
  "mcpServers": {
    "nullpoint": {
      "command": "np-mcp"
    }
  }
}
```

## Tools

All tools are prefixed `np_` to make them unambiguous in tool listings.

| Tool | Purpose | Status |
|---|---|---|
| `np_whoami` | Returns your local pubkey + handle (if bound) | live |
| `np_match` | Resolve a deterministic match between two loadouts | live |
| `np_loadout_validate` | Check loadout structural validity without playing | live |
| `np_collection` | List the cards owned by your local identity | live |
| `np_mine_status` | Currency balance + recent receipts | stub (V1.1) |
| `np_pull` | Spend 100 currency on a gacha pull | stub (V1.1) |

## Loadout shape (for `np_match` / `np_loadout_validate`)

Either `{"cards": [...]}` or a bare list. Each card is the same JSON the engine consumes:

```json
{
  "card_id": "starter_scout_head",
  "slot": "HEAD",
  "atk": 6, "def": 4, "hp": 18, "spd": 8,
  "triggers": []
}
```

Six cards required, one per slot, in slot-enum order: `HEAD, TORSO, ARM_L, ARM_R, LEGS, CORE`.

## Error envelope

Tools return `{"error": "<code>", "message": "..."}` on bad input rather than raising. Stubbed tools return `{"status": "not_yet_implemented", "hint": "..."}`. Always check for these keys before consuming the result.

## Determinism

`np_match` defaults to a zero seed for replay safety. Pass `seed` (64 hex chars) for non-test play. Same `(loadout_a, loadout_b, seed)` always produces the same result — that is the whole point of the engine.

## Security

The engine still never reads card text — `name`, `flavor`, `rarity` are dropped at the schema layer before the engine sees the card. MCP doesn't change this. An adversarial agent who controls a card definition can't escape combat math by writing instructions in the flavor text.
