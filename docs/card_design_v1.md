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
| Common | 98 | 49% | 60% | ~16 each |
| Uncommon | 60 | 30% | 25% | ~10 each |
| Rare | 28 | 14% | 10% | ~4-5 each |
| Epic | 12 | 6% | 4% | 2 each (the Phase-3 archetype anchor pair) |
| Legendary | 2 | 1% | 1% | cross-archetype set icons |

*(Revised 2026-04-22 after Phase 3: bumped epic count 10→12 so every
archetype gets exactly 2 anchors; commons dropped 100→98 to keep the
total at 200.)*

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

## 7. Engine vocabulary expansion (Phase 2 — SHIPPED 2026-04-22)

Prior vocab had 6 events × 8 ops × 7 targets — realistically supports ~100-120 mechanically distinct cards before remix-fatigue. For 200 unique cards across 6 archetypes, we needed new vocabulary.

### ✅ New `when` events (shipped)
- `ON_TURN_END` — fires on actor after its action; enables BURN/DOT mechanics
- `ON_KILL` — fires on attacker when its attack KOs the target (INFERNO snowball)
- `ON_LOW_HP` — one-shot fire when `self.hp ≤ hp_max // 4` (desperation cards)
- `ON_OPENING_ATTACK` — one-shot fire on unit's first attack of the match (alpha-strike)

### ✅ New `op` effects (shipped)
- `APPLY_BURN` — set BURN status for N rounds; ticks 3 dmg at round start
- `APPLY_POISON` — set POISON status for N rounds; ticks 2 dmg at round start (lower-magnitude DOT distinct from BURN)
- `APPLY_STUN` — set STUN status; target skips next action (consumed on skip, NOT on round tick — per design "1 = next action only")
- `APPLY_SILENCE` — set SILENCE status for N rounds; ALL triggers on target suppressed (including ON_DEATH); ticks at round-start
- `APPLY_TAUNT` — set TAUNT status for N rounds; basic attack target-priority override (taunting enemies must be hit first)
- `LIFESTEAL` — deal N damage to target (element-multiplier applies), attacker heals `ceil(N/2)` (heal-back is % of intent, not post-mult)

### ✅ Conditional triggers (shipped)
New trigger field: `condition` (optional string). Restricted-eval DSL (`daimon/engine/conditions.py`) — AST whitelist, `{"__builtins__": None}` eval frame. Vocabulary:
- `self.{hp, hp_max, shield, atk, def, spd, element}` — actor state
- `team.{distinct_elements, alive_count, size}` — actor's side
- `enemies.{distinct_elements, alive_count, size}` — opposing side
- `round` — current round number (0 at ON_BATTLE_START)
- Booleans: `and or not`; comparisons `< > <= >= == !=`; arithmetic `+ - * // %`; `True`/`False`; int/float literals
- **Disallowed**: function calls, subscripts, division `/`, power `**`, bitwise, strings, lambdas, walrus

Conditions are parsed + validated at card-LOAD time (`cards/loader.py`); compiled callable is cached per condition string via `lru_cache` in `combat.py`. Engine determinism: conditions can NEVER raise at fire-time.

### ⏳ Deferred (need larger architectural work — NOT in Phase 2)
- `RETALIATE` — already expressible as ON_TAKE_DAMAGE + DAMAGE (keyword flavor, not new op)
- `SUMMON` — requires virtual-slot team expansion; punt to V1.1
- `RESURRECT` — requires once-per-match state + revive lifecycle hooks; punt to V1.1
- `BUFF_HP`/`DEBUFF_HP` — requires hp_max mutation (currently treated as immutable card.hp); punt to V1.1
- `CHARGE` / `ROOT` / `CHILL` — already implemented as StatusConditions but no `APPLY_*` op yet; if Phase 3/4 needs them, trivial to add

### Keyword shorthand (render layer, deferred to Phase 3)
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
| **1. Framework doc** | This file | ✅ done |
| **2. Engine vocab expansion** | New ops/events/conditions in `engine/types.py` + `engine/conditions.py` + `engine/combat.py` + tests | ✅ done (4 new whens, 6 new ops, condition DSL — 595 tests pass) |
| **3. Archetype skeletons** | One legendary + two epic anchors per archetype = ~14-18 cards proving each archetype plays distinctly | ✅ done (2 legendaries + 12 epics = 14 anchors, 31 integration tests, 626 total tests pass) |
| **4. Pool fill-out** | 200 cards meeting distribution + species shape | ✅ done (4a reconciled, 4b +75 commons, 4c +45 uncommons, 4d locked via distribution gates; 200/98/60/28/12/2 lock) |
| **5. Balance via simulation** | `daimon/sim/` harness + matchup matrix + tuning pass | next |

After Phase 5: the legacy 67-card scaffolding (`scripts/author_v1_alpha_expansion.py`) is deleted and the v1_alpha catalog is the *real* base set.

---

## 11. Open questions (resolved + remaining)

1. ✅ **FLUX trigger condition syntax** — RESOLVED: restricted-eval string DSL with AST whitelist. Lives at `daimon/engine/conditions.py`; vocabulary documented in §7. Validated at card load, never raises mid-match.
2. ⏳ **SUMMON phantom card representation** — DEFERRED to V1.1 (engine architecture change too large for V1 scope; phantoms not in Phase-2 op shipset).
3. ❓ **Legendary balance philosophy** — Hearthstone (strictly bigger) vs Magic (mechanically unique)? **Provisional**: Magic model — legendaries are *unique* in mechanic, not strictly bigger numbers. Confirm in Phase 3 when designing the 2 legendary anchors.
4. ❓ **Should FLUX cards have an element field at all?** Currently every card requires an element. **Provisional**: yes, FLUX cards have a "host element" that determines elemental matchup, but their main trigger only fires when team is multi-element (gated via `condition: "team.distinct_elements >= 2"`). Phase 2 condition DSL makes this trivially expressible.
5. ❓ **Status-condition stack semantics** — APPLY_* uses `max(existing, new)` refresh (not addition). Is that the right default for BURN/POISON, or should DOTs stack additively? **Provisional**: max-refresh keeps total tick damage bounded by card design (single epic with BURN(3) + another BURN(3) doesn't double the DOT). Re-evaluate in Phase 3 if INFERNO archetype needs additive BURN.

---

## 12. Phase 2 changelog (2026-04-22)

Files added:
- `daimon/engine/conditions.py` — restricted-eval DSL parser
- `tests/test_conditions.py` — 43 DSL tests (whitelist + happy path + edge cases)
- `tests/test_combat_phase2.py` — 20 combat tests for new ops/whens

Files modified:
- `daimon/engine/types.py` — 4 new TriggerWhen, 6 new EffectOp, 4 new StatusCondition, `Trigger.condition` field, `UnitState.{low_hp_fired, has_attacked}` lifecycle flags
- `daimon/engine/combat.py` — `_apply_status` helper, `_apply_effect` dispatch for 6 new ops, `_resolve_action` dispatch for 4 new whens, STUN/TAUNT semantics, `_sweep_low_hp_triggers` helper, `_build_condition_ctx` + condition gating in `_fire_triggers_for_unit`, SILENCE gate, condition compile cache
- `daimon/cards/loader.py` — accept new op/when names, parse + validate `condition` field at load time
- `docs/card_design_v1.md` — this update

Test count: 595 passing, 1 skipped (was 529 + 43 conditions + 20 combat-phase2 + a few others = 595).

---

## 13. Phase 3 changelog (2026-04-22)

**Deliverable**: 14 archetype anchors (2 legendaries + 12 epics — 2 per archetype) proving each of the 6 archetypes plays distinctly via the new Phase-2 vocab.

### Cards shipped (14)

**Legendaries (2)** — V1 set-defining icons:
| Card | Element | Archetype | Signature mechanic |
|---|---|---|---|
| `voidking_morr` | VOID | REVENANT | ON_ALLY_DEATH BUFF_ATK SELF +4 (overwrote existing scaffolding to add the snowball) |
| `world_eater` | VOID | FLUX | 3 trigger gates on `team.distinct_elements >= 2/3/4` — apex card requires rainbow team |

**Epics (12)** — 2 per archetype:
| Archetype | Cards | Defining mechanic |
|---|---|---|
| INFERNO | `magma_tyrant`, `solar_phoenix` | ON_ATTACK APPLY_BURN + ON_KILL snowball; ON_OPENING_ATTACK alpha + ON_DEATH legacy heal |
| BULWARK | `worldroot_sentinel`, `bulwark_patriarch` | ON_BATTLE_START APPLY_TAUNT + ON_TAKE_DAMAGE shield; `round >= 2` gated team heal |
| TIDAL | `tide_empress`, `coral_augur` | ON_ATTACK LIFESTEAL; `self.hp == self.hp_max` gated heal |
| STORMCHAIN | `tempest_apex`, `arc_predator` | Team SPD buff + ON_OPENING_ATTACK AOE; ON_KILL BUFF_SPD chain |
| REVENANT | `crypt_wraith`, `mourners_lich` | ON_ALLY_DEATH APPLY_SILENCE; ON_ALLY_DEATH BUFF_ATK + ON_DEATH lingering DEBUFF |
| FLUX | `prism_chimera`, `rainbow_drake` | NATURE-host: `>=2` ATK buff + `>=3` AOE; FIRE-host: `>=2` heal + `>=3` shield-on-kill |

### Files added
- `scripts/author_phase3_anchors.py` — one-shot anchor authoring script (idempotent; re-running overwrites)
- `tests/test_phase3_anchors.py` — 31 integration tests, one per anchor's signature mechanic plus 4 catalog-load smoke tests (all 80 manifest entries load, legendaries enumerated)
- `daimon/catalog/v1_alpha/{world_eater,magma_tyrant,solar_phoenix,worldroot_sentinel,bulwark_patriarch,tide_empress,coral_augur,tempest_apex,arc_predator,crypt_wraith,mourners_lich,prism_chimera,rainbow_drake}.json` — 13 new cards

### Files modified
- `daimon/catalog/v1_alpha/voidking_morr.json` — overwrote existing legendary scaffolding with the Phase 3 REVENANT anchor (ON_ALLY_DEATH snowball replaces the prior ON_ATTACK chip; battle-start debuff + on-death AOE retained)
- `daimon/catalog/v1_alpha/manifest.json` — added 13 entries, bumped version to 0.4.0, updated description (now 80 cards total)
- `docs/card_design_v1.md` — Phase 3 marked done, this section added

### Naming collisions resolved
Three Phase-3 epics chose new card_ids to avoid colliding with previously scaffolded "legendaries" that ship at legendary rarity in the current manifest:
- `tempest_apex` (epic) instead of overwriting `storm_celestial` (legendary scaffold)
- `arc_predator` (epic) instead of overwriting `voltcat_apex` (legendary scaffold)
- `mourners_lich` (epic) instead of overwriting `echo_lich` (legendary scaffold)

The 6 legacy scaffolded "legendaries" (`storm_celestial`, `voltcat_apex`, `echo_lich`, `pyrotyrant`, `leviathan_prime`, `worldroot_colossus`) stay in the pack at their declared rarity. **Phase 4 reconciles** the catalog so only `voidking_morr` + `world_eater` remain at legendary rarity — others get redesignated as rare or epic with stat tuning to match the lower band.

### Test count
**626 passing, 1 skipped** (was 595 + 31 Phase-3 = 626). Catalog grew 67 → 80 cards.

### Open follow-ups for Phase 4
- Reconcile 6 legacy "legendaries" to rare/epic (target: exactly 2 legendary cards)
- Fill 100 commons / 60 uncommons / 28 rares / 10 epics / 2 legendaries totals
- Author species evolution lines per §5
- Audit no-trigger commons cap (≤30% of commons may be vanilla)
- Update `tests/test_phase3_anchors.py::TestCatalogLoad::test_legendary_count_locked_at_two` to assert exact 2-legendary set after Phase 4 reconciles

---

*End of Phase 3. Phase 4 (pool fill-out) begins next.*

---

## 14. Phase 4a changelog — rarity reconciliation (2026-04-22)

**Deliverable**: demote legacy scaffolded "legendaries" + "epics" down to `rare` so the rarity histogram matches the V1 lock before we start filling the pool to 200.

### Demotions (15 cards → `rare`)

| Before | Card IDs |
|---|---|
| legendary (scaffold, 6) | `storm_celestial`, `voltcat_apex`, `echo_lich`, `pyrotyrant`, `leviathan_prime`, `worldroot_colossus` |
| epic (scaffold, 9) | `bulwarthog`, `mindroot`, `inferno_lynx`, `ashen_phoenix`, `maelstrom_serpent`, `forest_warden`, `plasma_djinn`, `abyss_warden`, `nullhound` |

Stats + triggers intentionally UNTOUCHED — these cards retain their existing mechanical power at the lower rarity tier. Phase 5 (balance sim) will flag any over-budget rares and apply targeted stat tuning once the full pool is authored.

### Post-4a distribution (80 cards, pre-fill)

| Rarity | Count | V1 target | Gap |
|---|---|---|---|
| legendary | 2 | 2 | 0 ✅ |
| epic | 12 | 12 | 0 ✅ |
| rare | 28 | 28 | 0 ✅ |
| uncommon | 15 | 60 | need +45 |
| common | 23 | 98 | need +75 |
| **Total** | **80** | **200** | **need +120** |

Phase 4b+c authors the remaining 120 cards (75 commons + 45 uncommons) to hit the 200 target.

### Files added
- `scripts/reconcile_phase4_rarities.py` — one-shot idempotent reconciliation

### Files modified
- `daimon/catalog/v1_alpha/manifest.json` — bumped to 0.4.1; 15 rarity entries patched; description regenerated
- 15 card JSONs: rarity + art_path patched
- `tests/test_phase3_anchors.py` — tightened `test_legendary_count_locked_at_two` to exact match; added `test_epic_count_locked_at_twelve` + `test_json_rarity_matches_manifest` (drift gates)
- `tests/test_mcp.py` — updated `test_match_propagates_real_catalog_display_metadata` + `test_catalog_card_full_payload` to reflect post-reconciliation rarities (voltcat_apex is now rare; swapped the legendary-assertion target to world_eater)
- `docs/card_design_v1.md` — this section + rarity distribution table updated (10→12 epics, 100→98 commons)

### Test count
**628 passing, 1 skipped** (was 626 + 2 new Phase-4a test gates = 628).

---

*End of Phase 4a. Phase 4b (author 75 new commons) begins next.*

---

## 15. Phase 4b changelog — author 75 new commons (2026-04-22)

**Deliverable**: bring the common tier from 23 → 98 cards (the V1 lock from §3) by authoring 75 new commons across 5 elements with archetype-aligned design.

### Per-element shape (15 each, 75 total)
| Element | Existing C | + new C | After 4b |
|---|---|---|---|
| FIRE   | 5 | +15 | 20 |
| WATER  | 5 | +15 | 20 |
| NATURE | 5 | +15 | 20 |
| VOLT   | 4 | +15 | 19 |
| VOID   | 4 | +15 | 19 |
| **Total** | **23** | **+75** | **98 ✅** |

Each element's 15 = **13 archetype-pure + 2 FLUX** (host element matches the card's element; FLUX trigger gated on `team.distinct_elements >= 2`). This mirrors the Phase-3 archetype-anchor shape but at the lowest power tier.

### Design rules enforced in-script
The author script (`scripts/author_phase4b_commons.py`) carries an internal `_validate()` that fails BEFORE writing disk if any of these break:
- Stat budget: `atk + def + hp/3 + spd ∈ [18, 22]` (§4 commons envelope)
- Trigger count: exactly 1 (every new common; keeps total vanilla count at 23/98 = 23.5%, well under the §4 30% cap)
- Trigger value: exactly 2-3 (§4 common range; STUN/SILENCE excluded entirely as their canonical value=1 violates the band, AND they're disable mechanics that don't belong on commons)
- Per-element count: exactly 15 each
- Per-archetype count: 13 INFERNO+BULWARK+TIDAL+STORMCHAIN+REVENANT each, 10 FLUX

### Files added
- `scripts/author_phase4b_commons.py` — one-shot idempotent authoring script
- 75 new card JSONs: see the script's `FIRE_COMMONS`, `WATER_COMMONS`, `NATURE_COMMONS`, `VOLT_COMMONS`, `VOID_COMMONS` lists for the full roster

### Files modified
- `daimon/catalog/v1_alpha/manifest.json` — version bumped 0.4.1 → 0.4.2; +75 entries; description regenerated

### Test count
**628 passing, 1 skipped** (no new tests in this phase; 4d adds the distribution gates).

---

## 16. Phase 4c changelog — author 45 new uncommons (2026-04-22)

**Deliverable**: bring the uncommon tier from 15 → 60 cards (the V1 lock from §3), reaching the **200-card V1 total**.

### Per-element shape (9 each, 45 total)
| Element | Existing U | + new U | After 4c |
|---|---|---|---|
| FIRE   | 3 | +9 | 12 |
| WATER  | 3 | +9 | 12 |
| NATURE | 3 | +9 | 12 |
| VOLT   | 3 | +9 | 12 |
| VOID   | 3 | +9 | 12 |
| **Total** | **15** | **+45** | **60 ✅** |

Each element's 9 = **7 archetype-pure + 2 FLUX**. Mirrors Phase-4b shape at the next power tier.

### Species evolution lines seeded
14 of the 45 uncommons intentionally extend a Phase-4b common into a 2-tier (C → U) species line via the `species` field, satisfying §5's "most species span 2-3 rarity tiers":

| Element | C → U evolution lines (species root) |
|---|---|
| FIRE   | ashpup→ash_strider, magmaling→magma_warden, coalwhelp→coalbreaker, emberhawk→ember_raptor, flame_chimerlet→flame_chimera_adept, sunscale_drake→sunscale_serpent |
| WATER  | brineling→brineprince, tidefry→tidewatcher, shellfin→shellguard, mistchimera→mistchimera_adept, tidemerger→tide_synth |
| NATURE | mossling→mossbear, barkpup→barkguard, thornling→thornserpent, verdant_chimerlet→verdant_chimera, prism_seedling→prism_grove |
| VOLT   | zapling→zapdrake, boltkit→boltrunner, prismbolt→prism_strider, spectral_volt→spectral_charge |
| VOID   | shadeling→shadebishop, wraithling→wraith_prince, void_chimerlet→void_chimera, shadeprism→shade_prismatic |

The remaining uncommons are **singletons** (their own species). Phase 4c intentionally does NOT exhaust species line opportunities — singletons leave room for Phase 5 balance tuning to invent or merge species without rewriting Phase-4 art conventions.

### Design rules enforced in-script
`scripts/author_phase4c_uncommons.py::_validate()` enforces (fail-before-write):
- Stat budget: `atk + def + hp/3 + spd ∈ [22, 26]` (§4 uncommon envelope)
- Trigger count: exactly 1 (uncommons differ from commons by *value*, not count)
- Trigger value: exactly 3-4 (§4 uncommon range)
- Per-element count: exactly 9 each
- Per-archetype count: 7 INFERNO+BULWARK+TIDAL+STORMCHAIN+REVENANT each, 10 FLUX

### Files added
- `scripts/author_phase4c_uncommons.py` — one-shot idempotent authoring script
- 45 new card JSONs across the 5 elements

### Files modified
- `daimon/catalog/v1_alpha/manifest.json` — version 0.4.2 → 0.4.3; +45 entries; description rewritten to reflect the 200-card lock

### Test count
**628 passing, 1 skipped** (carrying through; 4d adds 11 new gates).

---

## 17. Phase 4d changelog — distribution lock tests (2026-04-22)

**Deliverable**: prevent silent drift away from the V1 200-card composition by adding structural gates that fail loudly if the manifest, JSONs, or rarity histogram diverge from the locked spec.

### New file
- `tests/test_phase4_distribution.py` — 11 structural tests across 5 classes:
  - `TestPoolShape` — total = 200, rarity histogram = {98C, 60U, 28R, 12E, 2L}, no unknown rarity tiers leak in
  - `TestVanillaCap` — vanilla commons ≤30% of common pool (currently 23/98 = 23%)
  - `TestElementCoverage` — each element ≥6 cards (mono-element loadout buildable); each bulk tier (C/U/R) covers all 5 elements; common tier element distribution within ±25% of the 19.6 mean
  - `TestTriggerBudget` — no card exceeds its tier's trigger cap (1/1/2/2/3 for C/U/R/E/L), with an explicit `PHASE5_TRIGGER_DEBT` allowlist for the 5 Phase-4a-demoted rares (pyrotyrant, leviathan_prime, worldroot_colossus, storm_celestial, echo_lich) that still carry their original 3-trigger legendary scaffolding. Phase 5 must collapse this allowlist to empty as part of the balance pass; a self-check test (`test_phase5_debt_set_is_real`) prevents the allowlist from going stale and silently masking new violators.
  - `TestUniqueness` — manifest card_ids unique; every manifest entry has a disk file; no orphan JSONs (every card on disk is referenced by manifest)

### Files modified
- `docs/card_design_v1.md` — Phase 4 row in §10 plan table marked done; this section + §15 (4b) + §16 (4c) added

### Test count
**639 passing, 1 skipped** (was 628 + 11 new Phase-4d gates).

### Phase 4 complete
The full Phase-4 arc (a/b/c/d) ships:
- **15 demotions** to clean the rarity histogram
- **120 new cards** authored against §4 stat-budget + §3 distribution + §5 species line shape
- **11 new structural gates** locking the result against drift
- **Final state**: 200 cards / 98C / 60U / 28R / 12E / 2L, with each element supporting mono-element 6-card loadouts at every bulk tier

### Open follow-ups for Phase 5
- Build `daimon/sim/` deterministic match runner (§8: 6 archetype loadouts × 1000 matches per pair = 6×6 win-rate matrix; target 45-55%)
- Tune outlier cards based on matchup-matrix failures
- Collapse `PHASE5_TRIGGER_DEBT` (the 5 demoted-legacy-rares carrying 3 triggers each) — either rebalance to 1-2 triggers each at appropriate rare-tier values, or promote individual cards back to epic with explicit doc rationale
- Trigger frequency audit (§8: no trigger op > 25% of pool — currently DAMAGE/HEAL/BUFF_ATK are the heavy hitters; verify they don't tip past 25%)

---

---

## §18 — Phase 4e changelog (engine slice): NORMAL element

**Locked 2026-04-22**, after Santiago design-direction:
> *"we need one monster type to be normal, that is usually used in other monsters elemental decks, normal should have no elemental bonus or weakness against anyone in the affinity charts, and are mostly support monsters"*

### Decision

A **6th element** named `NORMAL` joins `Element` enum, deliberately **outside** the type-effectiveness ring. It exists as the home for splashable utility/support monsters that should slot into any archetype-aligned deck without distorting the affinity math.

### Affinity contract

| Pair shape | Multiplier |
|---|---|
| Any of FIRE/WATER/NATURE/VOLT/VOID vs ring-counter | 1.5× / 0.75× (unchanged) |
| Same ring element vs same | 1.0× (unchanged) |
| **NORMAL → anything (including NORMAL)** | **1.0×** |
| **anything → NORMAL** | **1.0×** |

Implementation note: NORMAL is absent from `_STRONG_AGAINST` entirely. The 1.0× behavior falls out of `_EFFECTIVENESS.get(..., NEUTRAL_MULT)` for free — no special case needed in the engine.

### Architectural framing

NORMAL is an **element**, not an archetype. The 6 strategic archetypes are unchanged:

```
INFERNO (FIRE) · BULWARK (NATURE) · TIDAL (WATER)
STORMCHAIN (VOLT) · REVENANT (VOID) · FLUX (hybrid)
```

NORMAL cards carry **`archetype: null`** in the catalog — they are intentionally archetype-less, designed to be picked up by any of the 6 strategic decks. They are NOT eligible for FLUX gates that count distinct elements (the implementation lets `team.distinct_elements` count NORMAL the same as any other element, but design intent is that NORMAL is "background colour" — gates should be authored on the assumption that NORMAL is filler).

NORMAL gets **no legendary** in V1. The "one legendary per archetype" rule (§3) only covers strategic archetypes; NORMAL's cap is epic.

### Engine + render changes shipped

| File | Change |
|---|---|
| `daimon/engine/types.py` | `Element.NORMAL = 6` added; docstring updated to "6 elements (5 ring + 1 outside)" |
| `daimon/engine/elements.py` | Docstring updated; matchup logic auto-handles NORMAL via `.get(..., NEUTRAL_MULT)` default |
| `daimon/play/schema.py` | `Element.NORMAL = "normal"` added to schema enum |
| `daimon/play/primitives.py` | `Element.NORMAL: "white"` in `ELEMENT_COLOR` (neutral tint) |
| `daimon/play/hud/render.py` | `"normal": WHITE` in HUD ANSI table |
| `daimon/play/card_tile.py` | NORMAL added to `_PLAY_TO_ENGINE_ELEMENT` mapping |
| `daimon/cards/loader.py` | Schema docstring updated to `FIRE\|WATER\|NATURE\|VOLT\|VOID\|NORMAL` |

### Test coverage shipped

`tests/test_elements.py` (was 11 tests, now 16):
- Old `test_every_element_has_exactly_one_strong_and_one_weak` rewritten as `test_every_ring_element_has_exactly_one_strong_and_one_weak` (iterates the 5 ring elements explicitly via new `RING_ELEMENTS` constant)
- `test_ring_closes` updated to assert NORMAL never appears in the ring walk
- New `test_normal_has_no_strong_or_weak_relationships` — locks NORMAL out of the affinity table
- New `test_normal_attacker_is_always_neutral` — NORMAL → every defender (incl. NORMAL) = 1.0×
- New `test_normal_defender_is_always_neutral` — every attacker → NORMAL = 1.0×

### What's still pending in Phase 4e

This commit is the **engine slice only**. The pool slice (authoring 15 NORMAL cards + retiring 15 elemental cards to keep 200 total) and the counter-card slice (re-flavoring 5-6 rares as designated archetype counters) ship in the next commits.

---

## §19 — Phase 4e changelog (pool slice): 15 NORMAL cards, 15 elemental cards retired

**Locked 2026-04-22.** Manifest version bumped `0.4.3 → 0.4.4`. Total cards: **200 (unchanged)**.

### What shipped

15 NORMAL cards added — all with `archetype: null`, no condition gates, no archetype-coded ops (LIFESTEAL/APPLY_BURN/APPLY_POISON/APPLY_SILENCE/APPLY_STUN). Authored as splashable utility:

| Rarity | Count | Cards |
|---|---|---|
| common | 8 | `brass_mole`, `cloth_sprite`, `grove_pup`, `mossback_ox`, `page_slime`, `pebbler`, `quill_cat`, `runic_whelp` |
| uncommon | 4 | `mendicant_sphinx`, `rune_owl`, `stoneward`, `wrought_bear` |
| rare | 2 | `aegis_lion`, `loremaster_ape` |
| epic | 1 | `concord_phoenix` |

15 elemental cards retired to keep the 200-card lock:

| Rarity | Count | Retired |
|---|---|---|
| common | 8 | `blade_foxling`, `sparrowflame`, `shellpup`, `bubblefry`, `scoutling`, `mossbat`, `jolthog`, `nullkit` (proportional 2/2/2/1/1 across FIRE/WATER/NATURE/VOLT/VOID — vanilla scaffold-tier with no archetype hooks) |
| uncommon | 4 | `cinder_lancer`, `riverotter`, `anvilram`, `thunderfox` (1 each FIRE/WATER/NATURE/VOLT — generic singletons with no archetype hooks) |
| rare | 2 | `flarewing`, `void_serpent` (single-DAMAGE rares, no archetype hooks) |
| epic | 1 | `mourners_lich` (REVENANT collapses to one epic anchor — `crypt_wraith` — symmetric with the other strategic archetypes once Phase 4f promotes the second epics to legendary) |

### Distribution after Phase 4e (manifest snapshot)

```
TOTAL:   200
RARITY:  98C / 60U / 28R / 12E / 2L
ELEMENT: FIRE 37 · WATER 36 · NATURE 36 · VOLT 37 · VOID 39 · NORMAL 15
```

### NPC fixup

The 25 V1 NPCs were authored against the original vanilla pool. Without rewiring, every match-vs-NPC entry-point would 500 with `card_id 'X' not in catalog`. `scripts/fix_npcs_phase4e.py` substitutes retired IDs deterministically: same element first, same rarity first, never duplicate within a loadout. All 25 NPC files updated; loadout rarity-mix preserved.

### Test coverage shipped

`tests/test_phase4_distribution.py` (was 11 tests, now 15):
- `ELEMENTS` tuple now includes NORMAL; ring-only invariants moved to a new `RING_ELEMENTS` constant
- `test_element_balance_within_common_tier` iterates `RING_ELEMENTS` only — NORMAL has its own dedicated floor
- New `TestNormalElementPool` class with 4 explicit gates: total floor, per-rarity floor, `archetype: null` enforcement, and an inverse check that NORMAL never appears as an archetype label on a non-NORMAL card

`tests/test_phase3_anchors.py` (was 33 tests, now 31):
- `mourners_lich` removed from `PHASE3_ANCHORS` list and from `expected_epics` (replaced by `concord_phoenix` — keeps the count locked at 12)
- `TestMournersLich` class deleted with a comment explaining why and noting that the ON_ALLY_DEATH/ON_DEATH op coverage lives on in `crypt_wraith`'s tests

`tests/test_npcs.py`: 3 hardcoded test loadouts updated to use surviving card IDs.

### Authoring tooling shipped

`scripts/author_phase4e_normals.py` — one-shot card authoring + manifest update + retire-list executor with comprehensive `_validate()` self-check (budget gates, NORMAL-specific rules: `archetype: null`, no condition gates, no archetype-coded ops).

`scripts/fix_npcs_phase4e.py` — deterministic NPC loadout rewriter (same element + rarity + dedup, walks rarity tiers as fallback).

### What's still pending in Phase 4e

The counter-card slice (re-flavoring 5-6 rares as designated archetype counters) ships in the next commit.

---

*End of Phase 4 (a/b/c/d/e-engine/e-pool). Phase 4e-counters and Phase 4f (legendary promotion) come next, then Phase 5 (balance via simulation).*
