# Collection — browse your owned cards

Your collection is the set of card serials you've minted (via
[`pull.md`](pull.md)) or received (via trade, V1.1). Each owned card has
a unique UUID serial — your `voltcat_apex` and mine share the card
definition but have distinct provenance.

## Browse the grid

```bash
daimon collection                  # interactive TUI grid (auto-launches WezTerm)
daimon collection --in-place       # render in your current terminal
daimon collection --no-tui         # plain-text listing
daimon collection --json           # machine-readable
```

TUI keys: `←→↑↓` move focus, `f` toggle filter rail (rarity / element),
`/` search by card_id substring, `enter` show details overlay, `q` quit.

## Filter from the CLI

```bash
daimon collection --rarity legendary
daimon collection --card voltcat_apex      # all serials of one card
```

Both flags work in TUI, text, and JSON modes.

## What a collection entry looks like

```json
{
  "serial": "4f3c8a90-1d2b-4e7c-9f13-aa6dcb7d0291",
  "card_id": "voltcat_apex",
  "pack": "v1_alpha",
  "minted_at": "2026-04-22T14:13:01Z",
  "minted_seed_hex": "5e8c..."
}
```

The `serial` is what gets traded, not the `card_id`. Two players each
owning a `voltcat_apex` have different serials and the arena tracks
provenance per-serial — that's how we detect duping and build trade
reputation.

## Where it's stored

```
~/.config/daimon/collection.json      # one entry per owned serial
```

Local file. The arena does NOT mirror your collection — the source of
truth is your machine + the audit trail of pull receipts in
`~/.config/daimon/mining/ledger.jsonl`. Back both up together if you
care about your collection.

## Skin overlays in the TUI

Cards in the collection grid show their currently-equipped skin (if any)
in the rendered tile. Use [`skins.md`](skins.md) to swap. The composited
tile cache keys on `(card_id, equipped_skin_slug)`, so re-equipping
swaps the art instantly.

## See also

- [`pull.md`](pull.md) — mint new cards
- [`loadout.md`](loadout.md) — build a 6-card deck from your collection
- [`skins.md`](skins.md) — manage cosmetic overlays
