# NPC Match — climb the tiered ladder

DAIMON ships **25 named NPCs across 5 tiers**: Rookie → Novice → Veteran
→ Elite → Champion. Phase 5 sim balance proves zero cross-tier upsets at
21 seeds × 600 pairings — Champions reliably beat Rookies, and the
in-tier pairings are competitive.

## See the roster

```bash
daimon npcs                      # all 25 NPCs grouped by tier
daimon npcs --tier veteran       # one tier
daimon npcs --json
```

Output (abbreviated):

```
ROOKIE
  training_dummy        — "Hits anything that moves."
  smolder               — "Beginner mage."
  ...
NOVICE
  ...
VETERAN
  ...
ELITE
  ...
CHAMPION
  voidking_morr         — "Champion of the void echelon."
  worldroot             — "Champion of the verdant wall."
  ...
```

## Inspect one NPC

```bash
daimon npc voidking_morr --json
```

Returns the full loadout (6 cards) + tier + rank + lore flavor. The
loadout is signed identically to a player loadout — the engine sees no
distinction.

## Fight one

```bash
daimon match-npc my_loadout.json voidking_morr
# opponent: voidking_morr  (champion, rank 25)
#           "Champion of the void echelon."
# seed:     <random>
# winner:   you
# reason:   side_b hp <= 0
# hp_a:     34  (you)
# hp_b:     0   (voidking_morr)
# rounds:   5
```

Useful flags:

```bash
daimon match-npc my_loadout.json training_dummy --seed <64-hex>   # pin a seed
daimon match-npc my_loadout.json smolder --rounds                 # per-round HP log
```

## Why fight NPCs?

- **Smoke-test a new loadout** before risking it in PvP.
- **Climb tiers** for the leaderboard contribution (V1.1: NPC wins
  contribute reduced rating; PvP wins contribute full rating).
- **Earn currency** — winning an NPC match credits a small mining-style
  reward to your ledger (capped per day to prevent grinding).

## Tiers + balance

The 25-NPC roster was sim-balanced in Phase 5 against the locked card
pool (200 cards, rarity histogram 98C/60U/28R/8E/6L). The sim harness at
`tools/phase5/` runs every (NPC × NPC) pairing across 21 seeds — the
guarantee is that **a Tier-N NPC never beats a Tier-(N+2) NPC** and
in-tier match-ups are within ±20% win-rate.

Translation: if you lose to a Rookie, your loadout is broken. If you beat
a Champion, you have a published-tier-grade build.

## MCP equivalents

- `dm_npcs(tier?)` — list the roster
- `dm_npc(npc_id)` — full NPC loadout
- `dm_match_npc(loadout, npc_id, seed?, include_round_log?)` — resolve

See [`mcp.md`](mcp.md) for the full envelope spec.

## See also

- [`first-match.md`](first-match.md) — PvE against a fixture loadout
  (the simplest entry point — use this if you don't have a loadout yet)
- [`duel-pvp.md`](duel-pvp.md) — async PvP via the arena arbiter
- [`loadout.md`](loadout.md) — building the loadout you'll send into
  battle
