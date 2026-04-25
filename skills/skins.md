# Skins — equip / unequip cosmetic art

Skins are pure cosmetic — they swap the rendered card art and never touch
the stats or triggers. The engine doesn't see skin metadata. Equipping is
free; the cost is paid at purchase time in the [`shop.md`](shop.md).

## List skins you own

```bash
daimon skins                  # text listing
daimon skins --json           # machine-readable
```

```
voltcat_apex:
  - storm_crown        (equipped ✓)
  - lightning_crest
tide_empress:
  - coral_diadem
aegis_lion:
  - brass_mantle       (equipped ✓)
```

## Equip a skin

```bash
daimon skin equip <card_id> <skin_slug>
# Equipped storm_crown on voltcat_apex.
```

Only one skin can be equipped per card at a time. Equipping a new one
implicitly un-equips whatever was there.

## Unequip (revert to base art)

```bash
daimon skin unequip <card_id>
# Unequipped voltcat_apex (reverted to base art).
```

The skin entitlement stays in your inventory — only the equip state is
removed.

## What gets persisted

```
~/.config/daimon/equipped_skins.json   # {card_id: skin_slug, ...} the equip map
~/.config/daimon/owned_skins.json      # the entitlement records (one entry per owned skin)
```

Both files are local. Trading away the underlying card un-equips the
skin (the equip state is dropped) but the entitlement record stays — if
you later re-acquire the same `card_id`, the skin is available again.

## Where skins come from

- **Daily shop** — see [`shop.md`](shop.md). Most common path.
- **Pull rewards** — some pulls drop a skin variant alongside the card
  (rare; weighted into the pack manifest).
- **Trade** — V1.1; not yet implemented.

## What skins look like

The art pack ships canonical PNGs at `art/v1_alpha/<card_id>/base.png`
and skin variants at `art/v1_alpha/<card_id>/variants/<skin_slug>.png`.
The `manifest.json` per card directory enumerates the available variants
with their metadata (name, axis, rarity).

DAIMON's bundled WezTerm renders skins via the Kitty Graphics Protocol
through the same composited-tile pipeline as base art — equipping a new
skin invalidates the per-tile SHA1 cache for that card, so the next
shop / collection / loadout-edit redraw picks it up automatically.

## See also

- [`shop.md`](shop.md) — buy skins
- [`collection.md`](collection.md) — view your owned cards (with equipped
  skins highlighted)
