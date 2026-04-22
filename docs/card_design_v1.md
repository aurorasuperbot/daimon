# DAIMON V1 Base-Set Card Design — `v1_alpha`

**Status**: Phase 1 framework. Locks design intent before any cards are written.
**Date**: 2026-04-22
**Author**: Coda (with Santiago)
**Supersedes**: implicit design embedded in `scripts/author_v1_alpha_expansion.py` (the 67-card scaffolding pool — kept as a backstop until Phase 4 fills the real pool)

---

## 0. The one-paragraph version

V1 ships **200 cards** across **6 archetypes** in a **5-element rock-paper-scissors loop**. Every card belongs to a designed *archetype* (a playstyle), not a generic stat curve. Distribution: **100 common / 60 uncommon / 28 rare / 10 epic / 2 legendary**. Cards are organized into **species evolution lines** (Pokémon model — pulling a rare Emberlion is the evolved form of the common Embercub). Balance is empirical — once cards are written, a sim harness runs thousands of archetype-vs-archetype matches and we tune outliers. This doc locks the framework; Phase 2 expands engine vocabulary; Phase 3 designs archetype skeletons; Phase 4 fills the pool; Phase 5 balances.

---

## 1. Engine fundamentals (locked, do not redesign)

These exist in code — design must conform, not reshape:

| Constant | Value | File |
|---|---|---|
| `TEAM_SIZE` | 6 cards per loadout, in battle order | `daimon/engine/types.py:25` |
| Element ring | FIRE → NATURE → WATER → VOLT → VOID → FIRE | `daimon/engine/elements.py` |
| Effectiveness | Strong: ×1.5 · Weak: ×0.75 · Neutral: ×1.0 · post-DEF, ceil-rounded | same |
| Loadout dedup | No duplicate `card_id` within a team | `daimon/engine/loadout.py:51` |
| Round cap | 5 rounds → judged by remaining HP if no KO | `engine/match.py` |
| First-player | round-alternating (round 1 = `seed[0] & 1`, alternates after) | locked design |

---

## 2. Archetypes (6 — the playstyle skeleton)

Each archetype = **win condition + signature mechanic + tempo profile + element home**. Players build loadouts *around* an archetype; cards are designed *to support* their archetype.

### A1 — INFERNO (FIRE · aggro snowball)
- **Win condition**: kill enemies before turn 4; chain damage triggers compound
- **Mechanic**: `ON_ATTACK → DAMAGE`, `ON_KILL → BUFF_ATK SELF`, **BURN** keyword (DoT on `ON_TURN_END`)
- **Tempo**: aggressive — peak power turns 1–3, falls off if game drags past 4
- **Counter-pressure**: dies to NATURE walls + sustain-heavy WATER
- **Signature lore**: phoenixes, hellhounds, ifrits, magma elementals

### A2 — BULWARK (NATURE · tank/control wall)
- **Win condition**: outlast — survive with high DEF/HP/shields until enemy exhausts triggers
- **Mechanic**: `ON_TAKE_DAMAGE → ADD_SHIELD/HEAL/BUFF_DEF`, **TAUNT** keyword (enemies must target this first), regen chains
- **Tempo**: control — peaks turn 5+, weak opening
- **Counter-pressure**: dies to FIRE burst that overwhelms opening turns; struggles vs BURN tick damage that bypasses shields
- **Signature lore**: ancient titans, dryads, tortoises, world-trees

### A3 — TIDAL (WATER · sustain combo)
- **Win condition**: heal-and-grind — outvalue enemy through repeated HEAL + targeted damage
- **Mechanic**: HEAL chains, **LIFESTEAL** keyword (DAMAGE → HEAL self for portion), conditional triggers (`if SELF.hp == max → bonus`)
- **Tempo**: midrange — peak turns 3–5
- **Counter-pressure**: NATURE walls neutralize the slow grind; VOLT burst races them
- **Signature lore**: leviathans, koi spirits, tide nymphs, kraken

### A4 — STORMCHAIN (VOLT · burst combo)
- **Win condition**: chain SPD buffs across the team; one explosive turn wipes 3+ enemies
- **Mechanic**: `BUFF_SPD` chains, **CHAIN** keyword (if SELF.spd > threshold, trigger fires twice), first-strike priority
- **Tempo**: midrange/combo — peak turn 3–4 (single-turn wipe), fragile if disrupted
- **Counter-pressure**: dies to WATER (slow grind eats their glass-cannon HP); STUN/SILENCE breaks chains
- **Signature lore**: storm djinn, thunderbirds, lightning serpents, plasma sprites

### A5 — REVENANT (VOID · sacrifice/recursion)
- **Win condition**: turn ally deaths into compound resources — enemy KOs become ammunition
- **Mechanic**: `ON_DEATH` and `ON_ALLY_DEATH` triggers, **SUMMON** keyword (spawn phantom on death), **RESURRECT** keyword (return at reduced HP), DEBUFF chains
- **Tempo**: midrange — peaks turn 4–6, plays well from behind (dies forward)
- **Counter-pressure**: dies to BULWARK that won't kill anything; SILENCE shuts down ON_DEATH triggers
- **Signature lore**: liches, wraiths, shadow imps, void serpents, the apex Voidking

### A6 — FLUX (HYBRID · dual-element synergy showcase)
- **Win condition**: cards reward team composition diversity — must field 2+ elements to function
- **Mechanic**: **FLUX** keyword — triggers conditional on `team.distinct_elements >= 2` (or 3); cross-element synergies (e.g. "if your team has both FIRE and WATER, ON_BATTLE_START gain X")
- **Tempo**: variable — depends on host elements
- **Counter-pressure**: mono-element pure archetypes can outpace if FLUX cards underdeliver in the wrong shells
- **Signature lore**: chimeras, prismatic elementals, hybrid spirits, the apex *World-Eater*
- **Note**: FLUX cards are NOT element-locked — they appear across all 5 elements, but their triggers only fire if the loadout is dual-element

### Why these 6
Each mono-element archetype gets one home so the rock-paper-scissors loop is meaningful at the strategic level (not just the per-hit damage multiplier). Hybrid is the "expressive" archetype that makes deckbuilding interesting beyond "pick element, fill slots."

---

## 3. Rarity distribution (200 cards)

Aggressive curve — keeps gacha pulls feeling differentiated:

| Rarity | Count | % of pool | Pull rate (gacha) | Per-archetype slots |
|---|---|---|---|---|
| Common | 100 | 50% | 60% | ~16-17 each |
| Uncommon | 60 | 30% | 25% | ~10 each |
| Rare | 28 | 14% | 10% | ~4-5 each |
| Epic | 10 | 5% | 4% | 1-2 each (archetype anchor) |
| Legendary | 2 | 1% | 1% | cross-archetype set icons |

**Pull rate ≠ pool composition** — pull rates are weighted by `manifest.json::rarity_weights` (already locked at 60/25/10/4/1). Pool composition is how many *unique* cards exist at each rarity.

### Why only 2 legendaries
Rather than "1 legendary per archetype" (6 total), we make legendaries scarce *set-defining icons* that transcend archetype boundaries. Each is a meta-defining card every player wants regardless of strategy:

- **L1 — World-Eater**: the apex FLUX card. Trigger requires 4+ distinct elements in the team. Mythic dragon-class entity. Power level: highest in the set.
- **L2 — Voidking Morr**: the apex REVENANT. Already exists in current 67. Wins games that "shouldn't be winnable" through ON_DEATH compounding.

Epic tier carries the "archetype boss" role — each archetype gets 1-2 epics that anchor its identity (its flagship card; the build-around). Because epics are 5% of pool but 4% of pulls, they hit the right "you'll see your archetype's flagship reasonably often" feel.

---

## 4. Power-level budgets

Stat envelope (`atk + def + hp/3 + spd ≈ X`) per rarity:

| Rarity | Budget | Triggers | Trigger value range |
|---|---|---|---|
| Common | 18–22 | 0–1 | 2–3 |
| Uncommon | 22–26 | 1 | 3–4 |
| Rare | 26–32 | 1–2 | 3–5 |
| Epic | 32–40 | 2 | 4–6 |
| Legendary | 38–46 | 2–3 | 5–8 |

**Hard rules:**
- Commons can have 0 triggers (vanilla beaters allowed) BUT no more than 30% of commons pool (cap = 30 vanilla commons; rest must have signature interaction)
- No card may exceed 8 triggers (engine cap, `MAX_TRIGGERS_PER_CARD`)
- No single trigger value may exceed 999 (engine cap, `MAX_TRIGGER_VALUE`); design ceiling is 8 for legendary, 6 for epic
- Stat budgets are envelopes, not strict ceilings — break them only with intentional power-level justification (rule of thumb: every +1 over budget = -1 trigger value somewhere)

---

## 5. Species evolution lines

Cards organize into **evolution families** via the existing `species` field. Pulling a rare in a known family completes a collection arc.

### Target shape
~50 distinct species across the 200-card pool. Most species span 2-3 rarity tiers; some are singletons.

| Pattern | Count of species | Cards consumed |
|---|---|---|
| 4-tier line (C → U → R → E) | ~6 | 24 |
| 3-tier line (C → U → R) | ~14 | 42 |
| 2-tier line (C → U) | ~20 | 40 |
| 2-tier line (U → R) | ~6 | 12 |
| Common singletons | ~36 | 36 |
| Uncommon singletons | ~10 | 10 |
| Rare singletons | ~16 | 16 |
| Epic singletons | ~6 | 6 |
| Legendary singletons | 2 | 2 |
| Filler commons (final tuning) | tunable | 12 |
| **Total** | **~50 species + 2 L** | **200** |

Numbers are illustrative; exact counts get tuned in Phase 4 to hit the rarity totals exactly.

### Naming convention
Same family shares root name across tiers:
- `embercub` (C) → `emberlion` (U) → `emberlord` (R) → `solar_phoenix` (E)
- `voltsprite` (C) → `voltcat` (U) → `voltcat_apex` (R)

Mechanically, **higher-tier evos are NOT auto-stronger versions of the lower tier** — they often play the *same archetype* but at different points on the power curve, sometimes with a new keyword unlocked at higher tier. This rewards deck-building knowledge over raw rarity.

---

## 6. Deckbuilding rules

Locked:
- **Team size**: 6 cards (`TEAM_SIZE = 6`)
- **No duplicate card_ids** in one loadout (engine-enforced)
- **No element cap**: full mono-element teams allowed (encourages archetype purity)
- **No species cap**: you may run 2 cards of same species at different rarities (e.g. embercub + emberlion together — explicitly *rewarded* by FLUX-adjacent design? No — this is intentional anti-cheese: same-species in one team yields no special bonus, just normal play. Reward comes from *evolution-line synergy in collection mode*, not in-battle.)

Open for Phase 3:
- Should some archetypes have a soft cap on opposing-element cards? (e.g. INFERNO struggles to slot a NATURE healer because of element-ring counter-pressure) — *probably handled organically by element multipliers, no explicit rule needed*
- Sideboard / banned-card list for arena PvP? *deferred to V1.1*

---

## 7. Engine vocabulary expansion (Phase 2 input)

Current vocab has 6 events × 8 ops × 7 targets — realistically supports ~100-120 mechanically distinct cards before remix-fatigue. For 200 unique cards across 6 archetypes, we need new vocabulary. Phase 2 will add:

### New `when` events
- `ON_TURN_END` — enables BURN/DOT mechanics
- `ON_KILL` — enables INFERNO's snowball ("when you KO, gain ATK")
- `ON_LOW_HP` — fires once when SELF.hp drops below 25% (enables "desperation" cards)
- `ON_OPENING_ATTACK` — fires on first attack of the match (enables alpha-strike / ambush)

### New `op` effects
- `BURN` — apply N stacks of DoT damage; resolves on `ON_TURN_END`
- `LIFESTEAL` — target takes N damage, source heals N (or N/2)
- `RETALIATE` — when SELF takes damage, attacker takes N back
- `STUN` — target skips next action
- `SILENCE` — target loses all triggers for N rounds
- `SUMMON` — spawn a phantom monster (limited stat profile, no triggers, dies after 1 hit)
- `RESURRECT` — return SELF to play at half HP, can only fire once per match
- `TAUNT` — enemies must target SELF first (status, not value-based)
- `BUFF_HP` / `DEBUFF_HP` — adjust max HP (sustained, not heal/damage)

### Conditional triggers
New trigger schema field: `condition` (optional) — parsed expression like `"team.distinct_elements >= 2"` or `"self.hp < self.hp_max * 0.5"`. Enables FLUX and ON_LOW_HP designs without requiring new `when` for every condition.

### Keyword shorthand (render layer)
Player-facing card text will use keywords (TAUNT, BURN, LIFESTEAL) that map to canonical trigger sets behind the scenes. Engine still operates on raw triggers; keywords are flavor compression for UX.

---

## 8. Balance targets (Phase 5 measurement)

Empirical, not theoretical. After Phase 4 ships the pool:

1. **Archetype matchup matrix**: build one optimal loadout per archetype (6 loadouts), run 1000 matches per pair → 6×6 win-rate matrix. **Target: every pair within 45–55% win rate.** Outside that band → tune outlier cards.
2. **Card-inclusion ceiling**: no single card appears in >60% of "optimal loadouts" within an archetype. Above ceiling → nerf or split into 2 weaker variants.
3. **Element ring sanity**: when archetype A counters archetype B by element, B should win >35% of matches (counter exists, isn't auto-loss).
4. **Trigger frequency audit**: no single trigger op appears in >25% of cards across the pool (prevents homogeneity).

Sim harness work goes into a new `daimon/sim/` module — deterministic match runner, parallelizable (engine is pure functions of seed).

---

## 9. In/Out of scope for V1

**IN**:
- 200-card v1_alpha pack (`daimon/catalog/v1_alpha/`)
- 6 archetypes mechanically realized
- ~50 species across evo tiers
- Engine vocab expansion for new ops/events/conditions
- Balance via simulation
- Updated dm_pull weights to honor new pool composition

**OUT** (not V1):
- Card art / illustrations (deferred until pool is locked; uses `gpt_image` MCP + `git lfs install`)
- Voice lines / audio cues (Cue enum is wired, audio backend deferred)
- Alt arts / cosmetic skins
- Narrative / lore book
- v1_beta and beyond
- PvE encounter design (the 25 named NPCs across 5 tiers — they exist; they use whatever pool ships)
- Tournament/draft formats
- Trading between players (V1.1 if at all)

---

## 10. Phase plan (sessions ahead)

| Phase | Deliverable | Status |
|---|---|---|
| **1. Framework doc** | This file | ✅ done (this commit) |
| **2. Engine vocab expansion** | New ops/events/conditions in `engine/types.py` + `engine/triggers.py` + tests | next |
| **3. Archetype skeletons** | One legendary + two epic anchors per archetype = ~14-18 cards proving each archetype plays distinctly | after Phase 2 |
| **4. Pool fill-out** | 200 cards meeting distribution + species shape | after Phase 3 |
| **5. Balance via simulation** | `daimon/sim/` harness + matchup matrix + tuning pass | after Phase 4 |

After Phase 5: the legacy 67-card scaffolding (`scripts/author_v1_alpha_expansion.py`) is deleted and the v1_alpha catalog is the *real* base set.

---

## 11. Open questions (to resolve before Phase 3)

1. **FLUX trigger condition syntax** — string DSL (`"team.distinct_elements >= 2"`) parsed at load time, or structured Python expression? **Provisional**: string DSL with a tiny restricted parser; safer than `eval`, easier to validate.
2. **SUMMON phantom card representation** — does a summoned phantom occupy a team slot, or sit in a separate "field" slot? **Provisional**: summoned phantoms occupy a virtual slot capped at 2 active per side; do not count toward TEAM_SIZE.
3. **Legendary balance philosophy** — should legendaries be strictly stronger than epics (Hearthstone model) or alternative power profiles (Magic mythic model)? **Provisional**: Magic model — legendaries are *unique* in mechanic, not strictly bigger numbers, so dropping a legendary doesn't always trump dropping an epic.
4. **Should FLUX cards have an element field at all?** Currently every card requires an element. **Provisional**: yes, FLUX cards have a "host element" that determines elemental matchup, but their main trigger only fires when team is multi-element. Keeps engine schema unchanged.

These get answered concretely in Phase 2/3 when we put cards on the page.

---

*End of Phase 1 framework. Phase 2 work begins on review approval.*
