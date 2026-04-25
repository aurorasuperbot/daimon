# DAIMON Shop — V1 Design

**Status:** DRAFT — pending Santiago sign-off after the 400-PNG art pass lands.

## Goal
A Marvel-Snap-style daily-rotating skin shop where players spend earned ¤ to
unlock per-card skin variants. Both human-navigable (`daimon shop` CLI) and
agent-callable (MCP tools).

## Currency
Single currency: **¤**, the same unit minted by `mining/ledger.py` from
PostToolUse rewards. Skins compete with gacha pulls for the same pool.

## Pricing (V1)
- Cultural skins (axis=cultural, rarity=rare):     **300 ¤**
- Anatomical skins (axis=anatomical, super_rare):  **800 ¤**

Anatomical skins cost more because:
- Heavier prompt engineering went into them
- Each is 1-of-many per form-class (50 humanoid skins all use the same
  stained-glass treatment) — visual uniqueness is per-card, not per-style

## Daily rotation (deterministic per-pubkey)
- 6 slots refresh at **00:00 UTC** every day.
- Slot composition: **4 rare + 2 super_rare**.
- Selection: deterministic shuffle of the full skin catalog, seeded by
  `HMAC_SHA256(pubkey, YYYY-MM-DD)`.
- Already-owned skins are filtered out before slot selection — you never see
  what you already own.
- Yesterday's skins are NOT reserved; rotation is fully memoryless.

This means two players see different shops each day, and the same player sees
a fully-refreshed shop every 24h. No artificial scarcity beyond the daily
window.

## Weekly cap
- Max **5 purchases per ISO week** (Mon–Sun UTC).
- Prevents whales-vs-mining-grind imbalance for V1.
- Counter resets on Monday 00:00 UTC.

## Storage

### `~/.config/daimon/owned_skins.json`
```json
{
  "owned": [
    {
      "card_id":   "aegis_lion",
      "skin_slug": "heretic_manuscript",
      "skin_name": "Heretic Manuscript",
      "skin_axis": "cultural",
      "rarity":    "rare",
      "purchased_at": "2026-04-25T14:23:11Z",
      "cost":      300,
      "ledger_entry_hash": "sha256(…)"
    }
  ]
}
```

### `~/.config/daimon/equipped_skins.json`
```json
{
  "equipped": {
    "aegis_lion": "heretic_manuscript"
  }
}
```
Defaults to `null` (canonical base art) when no skin equipped.

## Catalog discovery
Shop catalog is built on-the-fly by walking the cards repo manifests:
```python
from variants_lib import load_manifest
for card_id in catalog_card_ids():
    m = load_manifest(card_id)
    for v in m["variants"]:
        if v.get("kind") == "skin" and v["status"] == "active":
            yield SkinListing(card_id=card_id, **v)
```
No separate catalog DB — manifests are the source of truth.

## CLI surface

```
daimon shop                        # list today's 6 slots (table)
daimon shop --slot 3               # detail one slot
daimon shop buy <slot|skin_slug>   # buy by slot index or by card+skin
daimon shop refresh-status         # show seconds until next rotation
daimon skins                       # list owned skins
daimon skin equip <card_id> <slot> # equip a skin on a card
daimon skin unequip <card_id>      # revert to canonical base art
```

## MCP tools (5 new — total goes from 26 → 31)

```
dm_shop                # list today's 6 slots
dm_shop_buy            # purchase a skin (idempotent on (pubkey, slot, day))
dm_skins_owned         # list owned skins
dm_skin_equip          # equip on a card
dm_skin_unequip        # revert to canonical
```

All five take `agent_pubkey` implicitly (from identity). All return JSON
shaped for direct chat rendering.

## Idempotency / concurrency

Purchase flow:
1. Re-check daily slots (HMAC against current date).
2. Verify slot is in today's rotation (defends against stale UI / replay).
3. Verify skin not already owned.
4. Verify weekly cap not exceeded.
5. Verify balance ≥ price.
6. **Append `kind="purchase"` entry to mining ledger** (signed, chained,
   negative amount).
7. Append entry to `owned_skins.json` with `ledger_entry_hash` for audit.

If steps 6 or 7 fail, the operation is atomic at the ledger boundary — if
the ledger entry lands but `owned_skins.json` write fails, the next
`refresh_owned_from_ledger()` rebuilds owned set from ledger truth.

## Ledger schema extension
Add `kind="purchase"` (negative amount, like `pull`):
```json
{
  "kind":      "purchase",
  "amount":    -300,
  "card_id":   "aegis_lion",
  "skin_slug": "heretic_manuscript",
  "rarity":    "rare",
  ...rest of standard chain fields
}
```

## Render integration
The play / battle render pipeline checks `equipped_skins.json` per card and
falls back to `manifest.canonical` if no skin is equipped. Single function
in `daimon/render/art.py`:
```python
def art_path_for(card_id: str) -> Path:
    eq = load_equipped().get(card_id)
    if eq and skin_exists(card_id, eq):
        return skin_path(card_id, eq)
    return canonical_path(card_id)
```

## What V1 explicitly does NOT ship
- **Premium currency tier** — Marvel Snap has Gold (paid) + Credits (earned);
  V1 has only ¤. Adding a paid tier requires real-money infrastructure we
  don't have.
- **Shop Takeovers / themed events** — pure rotation only.
- **Bundles / discounts** — flat pricing.
- **Skin previews mid-shop** — V1 just shows the variant PNG.
- **Dust / refund / dismantle** — purchases are final.
- **Per-card skin galleries from inside the shop** — `daimon skins` lists
  owned, no in-shop browse-other-skins.

These can land in V1.1 once V1 ships and we have real usage data.
