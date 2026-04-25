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

The full roster (5 NPCs per tier):

```
ROOKIE      sparring_sam  hedgerow_hannah  spark_kid_sora  sundown_si  tidepool_tom
NOVICE      forge_hand_fran  gentle_goro  owl_eyed_olive  static_sky  watchman_wren
VETERAN     bramble_beth  quickfoot_quinn  rainmaker_reka  rust_priest_rhea  stormrider_sven
ELITE       iron_shield_ira  mind_eater_mox  storm_warden_wynn  tide_priest_telos  volt_priest_vex
CHAMPION    apex_king_atlas  doom_paw_doppia  mythbreaker_marn  stormcrown_sienna  voidwalker_vance
```

## Inspect one NPC

```bash
daimon npc voidwalker_vance --json
```

Returns the full loadout (6 cards) + tier + rank + lore flavor. The
loadout is signed identically to a player loadout — the engine sees no
distinction.

## Fight one

```bash
daimon match-npc my_loadout.json sparring_sam
# opponent: Sparring Sam  (rookie, rank 1)
#           "Trains with what's at hand."
# seed:     <random>
# winner:   you
# reason:   wipe
# hp_a:     122  (you)
# hp_b:     0    (Sparring Sam)
# rounds:   5
```

Useful flags:

```bash
daimon match-npc my_loadout.json sparring_sam --seed <64-hex>      # pin a seed
daimon match-npc my_loadout.json voidwalker_vance --rounds         # per-round HP log
```

(`--rounds` is supported on `match-npc` only — `daimon match` is
PvE-vs-fixture and prints just the final result.)

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
