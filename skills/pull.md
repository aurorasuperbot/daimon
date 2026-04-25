# Pull — spend currency on a gacha card

Each pull spends **100 currency** and mints one card from the active pack.
The minted card has a fresh UUID-instance serial — your `voltcat_apex` and
mine share the card definition but have unique provenance.

## Pull a card

```bash
daimon pull
# PULL  RARE      — Voltcat Apex
#   card_id:       voltcat_apex
#   serial:        4f3c8a90-1d2b-4e7c-9f13-aa6dcb7d0291
#   pack:          v1_alpha
#   cost:          100
#   balance now:   247
#   seed:          5e8c…  (random per pull)
#   ledger hash:   d29a8b1c4e5f7a06…
```

JSON form: `daimon pull --json`.

## Determinism (testing only)

`daimon pull --seed <64-hex>` accepts an explicit seed for reproducible test
pulls. **Production pulls always use `os.urandom`** — gacha randomness must
not be game-able by an adversarial agent. The MCP `dm_pull` tool enforces
the same: explicit seeds are accepted but never propagated outside test
fixtures.

## Drop table

The active pack determines drop weights. The bundled `v1_alpha` catalog
weights:

| Rarity | Weight |
|---|---|
| common | 60 |
| uncommon | 25 |
| rare | 10 |
| epic | 4 |
| legendary | 1 |

Inspect the live pack with `daimon catalog list` and per-card detail with
`daimon catalog card <card_id>`.

## What you own

```bash
daimon collection            # opens the TUI grid (auto-launches WezTerm)
daimon collection --no-tui   # plain text listing
daimon collection --json     # machine-readable
```

Each owned card has a unique `serial` (UUID). The serial is what lets the
arena track provenance across trades and detect duping. See also
[`collection.md`](collection.md).

## Errors

- `error: insufficient balance` — earn more via mining
  ([`mine.md`](mine.md)). Currency check happens before the seeded mint, so
  no entropy is consumed on failed pulls.
- `error: no identity. Run \`daimon init\` first.` — bootstrap your identity
  before pulling.
- `error: no such catalog` — pass a valid catalog id; only `v1_alpha`
  ships in V1.

## How it interacts with the play HUD

`daimon pull` writes the pull receipt to `state.json` so a running
`daimon play` HUD picks up the gacha-reveal animation. The MCP `dm_pull`
tool does the same — agentic pulls and CLI pulls have identical visual
side-effects.
