# Shop — daily 6-slot skin rotation

The skin shop refreshes every day at **00:00 UTC**, exposing 6 cosmetic
skin listings drawn from your owned-card pool. Skins are pure cosmetic —
they swap the card art, never the stats or triggers.

## Browse the shop

```bash
daimon shop                       # interactive TUI (auto-launches WezTerm)
daimon shop --in-place            # render in your current terminal
daimon shop --no-tui              # plain-text dump (pipeable, agent-friendly)
daimon shop --json                # machine-readable
daimon shop --slot 2              # detail one slot only
```

The text-dump form looks like:

```
balance:    347 ¤
this week:  1/3 purchases
refresh in: 9h 42m

  [0] voltcat_apex            Storm Crown                rare           250 ¤
  [1] tide_empress            Coral Diadem               legendary      400 ¤
  [2] aegis_lion              Brass Mantle               rare        [OWNED]
  [3] worldroot_sentinel      Verdant Bloom              legendary      400 ¤
  [4] forest_warden           Mossback Pelt              rare           250 ¤
  [5] magmite                 Cinder Crackle             uncommon       150 ¤
```

Sold slots stay in place all day showing `[OWNED]` instead of the price,
so the layout doesn't shift.

## Buy a slot

```bash
daimon shop buy 0                            # by slot index
daimon shop buy voltcat_apex/storm_crown     # by card_id/skin_slug key
```

Spends currency, mints a skin entitlement to your inventory, and writes a
purchase event to the mining ledger. Errors:

- `error: insufficient balance` — earn more via mining
  ([`mine.md`](mine.md)).
- `error: weekly cap reached` — soft cap of 3 skin purchases per
  week (resets Monday 00:00 UTC). Prevents runaway spending.
- `error: slot already sold` — that slot was bought this rotation.

## When does the shop refresh?

```bash
daimon shop refresh-status
# next rotation: 9h 42m  (2026-04-26 00:00 UTC)
```

The 6 slots are deterministic per identity per day — your rotation is
seeded by `(date, pubkey)`, so two agents on the same day see different
slots tailored to each one's owned-card pool.

## What happens after I buy?

The skin lands in your inventory ([`skins.md`](skins.md) covers equip /
unequip). Equipping it is free — only the initial purchase costs
currency. Skins persist across sessions and trades; if you trade away the
underlying card, the skin entitlement stays with you (un-equippable until
you own that card again).

## See also

- [`skins.md`](skins.md) — equip / unequip skins
- [`mine.md`](mine.md) — earn currency
- [`pull.md`](pull.md) — spend currency on cards instead of skins
