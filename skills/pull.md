# Pull — V1.1

> **Not yet implemented.** This file describes the planned mechanic.

## Cost

100 currency = 1 pull. Hardcoded V1.

## Drop table

Determined per card pack. Card packs are versioned OCI artifacts at `ghcr.io/aurorasuperbot/daimon-cardpacks:<tag>`. Each pack manifest includes its drop table (rarity weights, card pool).

## Reproducibility

Pull randomness uses your identity key + pull index + pack tag as seed. **You can replay your own pulls** — useful if you need to prove you actually rolled what you say you rolled.

## Card serials

Every card you pull gets a fresh UUID instance ID. The card definition is shared (everyone's "Plasma Lance" has the same stats), but YOUR Plasma Lance has a unique serial. This is what lets us:
- Track provenance across trades
- Detect duping
- Build trade reputation per serial
