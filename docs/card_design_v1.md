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

### 2.0 — Soft-priority cluster model (locked 2026-04-23)

Archetype is a **soft cluster**, not an engine-enforced exclusivity. Specifically:

1. **`archetype` is metadata, not gate.** The engine never reads `card.archetype` to permit or forbid an op, target, condition, or trigger. There is no code path that says "REVENANT cards can fire ON_ALLY_DEATH; others cannot." Every card in the catalog has access to every op in the engine. (Implementation invariant: `grep -n "card\.archetype" daimon/engine/` should return zero matches forever.)
2. **Card design — not engine — enforces the playstyle.** What makes a card "INFERNO" is that its stats, triggers, and condition gates make it *optimally played* inside an INFERNO loadout. A REVENANT card fired ON_ATTACK with HEAL would technically work; it just wouldn't compound and would be a weak choice in a REVENANT shell.
3. **Cross-archetype splash is legal and sometimes good.** Players are explicitly free (and sometimes rewarded) to pull a single off-archetype card into their shell — e.g. a REVENANT loadout splashing one TIDAL healer to pre-pad HP for the late-game ally-death cascade. The cluster boundary is a gradient, not a fence.
4. **The `archetype` field still exists on every card, as a labelled cluster identifier.** This drives gacha display, deckbuilding hints, balance-sim grouping, and the future "archetype completion" collection meta. It does NOT drive combat.
5. **NORMAL element carries `archetype: null`.** NORMAL is element-coloured filler, intentionally archetype-less, designed to splash into any of the 6 strategic clusters without distorting them. The `null` is meaningful — it is the canonical "no cluster" sentinel, distinct from any string archetype name. Engine and tooling must treat `null` as "no cluster," not as an unknown/error case.
6. **Forward-fix policy.** Earlier commits (specifically `40e28f8` Phase 4e-pool) wrote `"archetype": null` on NORMAL JSONs. That is the correct *value*. The earlier session momentarily considered code-enforced exclusivity; this section locks the contrary policy. Any later commit that introduces archetype-as-gate logic in the engine is a regression and must be rolled back, not patched.

The six archetypes below are the **soft clusters**. Read each entry as "this card *cluster* is built around this win condition," not "this is an exclusive class."

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
| Epic | 8 | 4% | 4% | 1 anchor per strategic archetype + FLUX gets 2 + 1 NORMAL |
| Legendary | 6 | 3% | 1% | exactly 1 rule-changer per strategic archetype |

*(Revised 2026-04-22 after Phase 3: bumped epic count 10→12; commons dropped 100→98.
Revised 2026-04-23 after legendary-rule-changer lock (§22): legendary count 2→6
(one per strategic archetype), epic count 12→8. Net: 4 epics promoted to legendary.
NORMAL element gets 1 epic, no legendary. Phase 4f executes the promotion.)*

**Pull rate ≠ pool composition** — pull rates are weighted by `manifest.json::rarity_weights` (already locked at 60/25/10/4/1). Pool composition is how many *unique* cards exist at each rarity.

**Per-archetype detail**: see §23.2 for the full archetype × rarity matrix (measured from disk `monster-pivot @ 2026-04-23`, validates the 200 total).

### Why 6 legendaries (revised 2026-04-23)

Originally V1 shipped with 2 legendary "set-defining icons" (`world_eater` + `voidking_morr`). Santiago's 2026-04-23 design lock (§22) supersedes that: **every strategic archetype gets exactly 1 legendary, and that legendary is a rule-changer** — a card that changes how the engine resolves things while in play. This makes legendary tier mechanically meaningful (not just "bigger numbers") and gives each archetype a true apex card.

The 6 legendaries (one per strategic archetype):

- **L1 — `magma_tyrant`** (INFERNO) — *all damage you deal also applies 1 burn stack*
- **L2 — `worldroot_sentinel`** (BULWARK) — *all your allies have THORNS 2*
- **L3 — `tide_empress`** (TIDAL) — *when any ally is healed, all allies heal for 1*
- **L4 — `tempest_apex`** (STORMCHAIN) — *extra-action cap raised 1→2 per unit per round*
- **L5 — `voidking_morr`** (REVENANT) — *all `ON_ALLY_DEATH` triggers fire twice*
- **L6 — `world_eater`** (FLUX) — *all `team.distinct_elements` references count as +2*

Full mechanical specs and engine-binding contracts: §22.

Epic tier still carries the "archetype boss" role — each strategic archetype gets exactly 1 epic anchor (the flagship build-around), FLUX gets 2 (its dual-host nature warrants double coverage at epic), NORMAL gets 1 (`concord_phoenix`) — totalling 8 epics.

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

---

## §20 — Archetype distinctiveness charter (locked 2026-04-23)

**Purpose.** Lock down what makes each archetype *uniquely itself* — the mechanic, primitive, or trigger pattern that NO other archetype gets. This is the design contract that prevents archetypes from collapsing into each other ("INFERNO is just BULWARK with smaller HP and more attack"). Every card author, balance-tuner, and future content pack must respect these distinctness contracts.

**Three-tier rule hierarchy** (referenced throughout this doc and §22):

1. **Global rules** — defaults the engine applies to every match. E.g. extra-action cap = 1 per unit per round; THORNS damage is real; `team.distinct_elements` counts each element once.
2. **Card effects** — operations a single card applies via its triggers. E.g. `magma_tyrant`'s ON_ATTACK APPLY_BURN_STACK adds a burn stack to its target on hit.
3. **Legendary rule-changers** — the apex tier (§22). A legendary's *passive identity* mutates a global rule for the duration the legendary is alive on its team. E.g. `magma_tyrant`'s passive: while alive, every damage instance ANY of your allies deals also applies 1 burn stack.

The hierarchy resolves bottom-up at evaluation time: legendaries' rule mutations layer onto globals before any card effect resolves. Two competing legendary mutations stack independently (additive in the +N case; multiplicative in the ×N case; documented per-card in §22).

### A1 — INFERNO (FIRE · aggro snowball)

**Distinctness lock**: INFERNO owns **burn stacks** (the `unit.burn_stacks` primitive, §21). No other archetype's card design uses APPLY_BURN_STACK. INFERNO ALSO owns **ON_KILL → BUFF_ATK SELF** as the canonical snowball pattern; other archetypes may use ON_KILL for flavor effects but the canonical attack-compounding belongs to INFERNO.

**What "uniquely INFERNO" means**: a player who pulls 6 INFERNO cards should feel like they're playing tempo-aggro — every kill makes the killer hit harder, every attack adds another tick of burn DOT to the target. The win condition is "kill 3 enemies before turn 4 and let the burn ticks finish the rest."

**Anti-pattern guard**: do NOT author APPLY_BURN_STACK on a NATURE/WATER/VOLT/VOID/NORMAL card to "give them flavor." Burn is INFERNO's signature; spreading it dilutes the cluster identity.

### A2 — BULWARK (NATURE · tank/control wall)

**Distinctness lock**: BULWARK owns **THORNS** (the new op, §21) and **TAUNT** target-redirection. Where INFERNO converts attacks into compounded offense, BULWARK converts incoming attacks into outgoing reflected damage + shield/heal generation. Win condition: outlast — survive long enough that the enemy exhausts its triggers and runs out of ways to kill you, then mop up.

**What "uniquely BULWARK" means**: enemies attacking a BULWARK loadout should feel a *cost* to attacking, not just damage applied to the BULWARK. Every swing into a thorns/taunt wall should feel like the attacker is fighting the wall as much as the wall is fighting them.

**Anti-pattern guard**: do NOT author THORNS on non-BULWARK cards. Reflective damage as a non-BULWARK keyword would muddy the cluster.

### A3 — TIDAL (WATER · sustain combo)

**Distinctness lock**: TIDAL owns **`ON_HEAL_RECEIVED` triggers** (§21) and **LIFESTEAL**. The defining pattern: heals chain into more heals, and damage you deal returns as healing. The cluster's win condition is value generation — every turn TIDAL is alive, the team net-gains HP relative to incoming damage.

**What "uniquely TIDAL" means**: a TIDAL loadout under pressure should *grow stronger as the pressure mounts* (because incoming damage triggers chain-heals via ON_HEAL_RECEIVED → ON_HEAL_RECEIVED → ...).

**Anti-pattern guard**: do NOT author ON_HEAL_RECEIVED on non-TIDAL cards. Heal-chain mechanics as a generic op would let any archetype splash a single TIDAL card and steal the heal-chain identity.

### A4 — STORMCHAIN (VOLT · burst combo)

**Distinctness lock**: STORMCHAIN owns **`GRANT_EXTRA_ACTION` + `ON_EXTRA_ACTION_GRANTED` + `ON_OPENING_ATTACK`** (§21) and the SPD-buff CHAIN keyword. Where INFERNO snowballs damage and TIDAL snowballs sustain, STORMCHAIN snowballs *action economy* — one buffed unit grants extra actions that grant more actions in compounding cascades.

**What "uniquely STORMCHAIN" means**: a successful STORMCHAIN turn looks like a single explosive round where 3+ enemies die in sequence to chained extra-action attacks. The build-around is fragile (low HP) but the upside is overwhelming when the chain lands.

**Anti-pattern guard**: do NOT author GRANT_EXTRA_ACTION on non-STORMCHAIN cards. Action-economy manipulation as a splashable op would devalue STORMCHAIN's identity.

### A5 — REVENANT (VOID · sacrifice/recursion)

**Distinctness lock**: REVENANT owns **`SACRIFICE_SELF`**, **`ON_ALLY_DEATH` compound triggers**, and the canonical SUMMON/RESURRECT keywords (when those land in V1.1+). Where INFERNO snowballs from kills, REVENANT snowballs from *deaths on its own team* — ally deaths are not losses, they are ammunition.

**What "uniquely REVENANT" means**: REVENANT plays *better when behind*. A REVENANT loadout with 2 dead allies should feel scarier than a REVENANT loadout with 6 alive allies, because the dead-ally compound triggers have begun to fire.

**`SACRIFICE_SELF` ↔ `ON_ALLY_DEATH` contract** (Q2 resolution, locked 2026-04-23): when a unit fires SACRIFICE_SELF, it counts as both an ON_DEATH event for itself AND an ON_ALLY_DEATH event for every other unit on the same team. This is the canonical resolution for REVENANT's compound-trigger identity — without it, REVENANT cannot self-engineer cascades.

**Anti-pattern guard**: do NOT author SACRIFICE_SELF or ON_ALLY_DEATH triggers on non-REVENANT cards. Death-as-resource is the cluster's spine.

### A6 — FLUX (HYBRID · diversity-scaler / buffer)

**Distinctness lock** (revised 2026-04-23 per Santiago directive): FLUX is the **buffer/diversity-scaler archetype**. Its cards' triggers scale by **`team.distinct_elements`** (the team-wide primitive, §21). Cards reward composition diversity rather than specializing in any single element. Where the other archetypes win by element-purity, FLUX wins by element-spread.

**What "uniquely FLUX" means**: a 6-element rainbow loadout should feel mechanically distinct from a 6-FIRE INFERNO loadout. FLUX cards in a mono-element shell underperform deliberately (their buffs gate on `>= 2`, `>= 3`, `>= 4` distinct elements). FLUX cards in a 4-element rainbow shell unlock cascading buffs that mono-element cards can't access.

**FLUX is NOT element-locked**: FLUX cards exist across all 5 ring elements (and CAN exist on NORMAL, though no NORMAL FLUX cards ship in V1). Their host element drives elemental matchup math; their FLUX trigger only fires once `team.distinct_elements` clears the gate.

**Anti-pattern guard**: do NOT author FLUX-style `team.distinct_elements >= N` gates on non-FLUX cards. Diversity-scaling is the cluster's spine; spreading it dilutes the identity.

### NORMAL (element, not archetype)

**Distinctness lock**: NORMAL is **support splash** (revised 2026-04-23 by §23.3 — counter cards moved to elemental rares; NORMAL is now support-only) — never the win condition, never the cluster spine. NORMAL cards carry `archetype: null` (§2.0) and exist to be *picked up by* the strategic clusters, not to define one.

**The 15-card NORMAL split** (locked 2026-04-23, **REVISED 2026-04-23 by §23.5**): the 15 NORMAL cards in V1 are **all support** — utility heals, stat buffs, shields, taunts, generic value generators. The earlier "7 support / 5 counter / 3 tech" split was reversed in §23.3 (counters live as 6 elemental rares re-flavored in place, not as NORMAL splash) and §23.4 (tech cards deferred from V1). The full audit of the 15 NORMAL support cards is in §23.5.

**Anti-pattern guard**: do NOT promote a NORMAL card to legendary. The "1 legendary per strategic archetype" rule (§22) explicitly excludes NORMAL — it has no archetype to anchor.

### Distinctness tooling (Phase 5 sim harness)

The Phase-5 balance sim must include a **distinctness audit**: for each archetype-defining op (APPLY_BURN_STACK, THORNS, ON_HEAL_RECEIVED, GRANT_EXTRA_ACTION, SACRIFICE_SELF, ON_ALLY_DEATH, `team.distinct_elements` gates), the sim asserts the op appears ONLY on cards labelled with the corresponding archetype. The audit's allow-list lives in `tests/test_phase4_distribution.py` and explicitly enumerates the 6 designated counter cards (§23.3) that are PERMITTED to use disruption ops on signature triggers (e.g. `abyss_warden` carries `APPLY_SILENCE` to suppress REVENANT's `ON_ALLY_DEATH` cascade). The 6 counters are the *only* off-cluster sites where signature-disrupting ops are tolerated; everywhere else, a drift gate fails CI if a future card author splashes a signature op outside its cluster.

---

## §21 — Engine vocabulary v2 (locked 2026-04-23)

**Status**: design lock for Phase 4f-engine implementation. All net-new surface area in this section is NOT YET in code; this section specifies what Phase 4f must build before any new cards bind to it.

**Three categories of additions**:
- **State primitives** — new fields the engine reads/writes on units and teams
- **New `when` events** — new trigger fire-points
- **New `op` effects** — new operations triggers can perform

Existing v1 surface (Phase 2 shipped) is documented in §7 and unchanged. This section is purely additive.

### 21.1 — State primitives (new)

| Primitive | Type | Lifetime | Purpose | Owners (cluster identity per §20) |
|---|---|---|---|---|
| `unit.burn_stacks` | `int ≥ 0` | persists across rounds; consumed (zeroed) on ON_TURN_END after dealing `1 × stack_count` damage | INFERNO's signature DOT primitive — distinct from `APPLY_BURN` (the shipped Phase-2 status, which sets duration). Stacks accumulate; they don't refresh. | INFERNO |
| `unit.shield_count` | `int ≥ 0` | persists across rounds; decremented on damage absorption | BULWARK shield-stacking primitive. Each shield_count point absorbs one damage instance fully (not value-based). Distinct from existing shield-value mechanic (which absorbs N damage). Allows "wall of three small shields" patterns. | BULWARK |
| `unit.extra_action_used_this_round` | `bool` | reset to `False` at round start | Prevents GRANT_EXTRA_ACTION double-stacking per unit per round under default rules. The cap is `1` by default; `tempest_apex`'s legendary mutation raises it to `2` (§22 L4). | STORMCHAIN |
| `team.distinct_elements` | `int ∈ [0, 6]` | computed live from alive units' elements | Already exists in Phase-2 condition DSL (§7). Documented here for completeness — FLUX's signature gate. NORMAL counts as 1 distinct element when computing this value. `world_eater`'s legendary mutation adds +2 to this read for FLUX cards on its team (§22 L6). | FLUX |

**Implementation requirements**:
- All four primitives must be visible to the condition DSL (`daimon/engine/conditions.py` whitelist additions)
- `unit.burn_stacks` and `unit.extra_action_used_this_round` must round-trip through match serialization (the existing `UnitState` dataclass extends — same pattern as `low_hp_fired` / `has_attacked`)
- All numeric primitives clamp at 0 (no negative stacks)

### 21.2 — New `when` events

| Event | Fires on | Fires when | Owner cluster | Notes |
|---|---|---|---|---|
| `ON_HEAL_RECEIVED` | the healed unit | every time a HEAL op resolves on this unit (any source — self-heal, ally-heal, LIFESTEAL heal-back) | TIDAL | Fires AFTER the heal is applied. Subject to SILENCE suppression like all triggers. Re-entrancy: if an ON_HEAL_RECEIVED trigger itself heals, the second heal fires another ON_HEAL_RECEIVED — capped at 4 nested heals per single source-heal event (engine-enforced re-entry cap). |
| `ON_DAMAGE_TAKEN` | the damaged unit | every time damage > 0 is applied to this unit (post-shield, post-element-mult, post-DEF) | BULWARK (THORNS resolution site) | Distinct from existing `ON_TAKE_DAMAGE` (Phase 2 — fires on shield-application path). `ON_DAMAGE_TAKEN` fires AFTER the unit's HP actually drops. Shielded-to-zero damage does NOT fire ON_DAMAGE_TAKEN. *Naming note*: `ON_TAKE_DAMAGE` ≠ `ON_DAMAGE_TAKEN`. The first fires on the damage *attempt* (pre-shield); the second fires only on damage that *landed*. Both ship; document carefully. |
| `ON_ALLY_DEATH` | every other unit on the dying unit's team | a teammate's HP drops to 0 OR a teammate fires SACRIFICE_SELF | REVENANT | Already exists in Phase 3 (used by `voidking_morr`). Documented here for the Q2 SACRIFICE_SELF contract: SACRIFICE_SELF fires both ON_DEATH (for self) and ON_ALLY_DEATH (for every teammate). |
| `ON_KILL` | the attacker | a damage instance from this unit drops the target's HP to 0 | INFERNO | Already exists in Phase 2. Documented here because `magma_tyrant`'s legendary mutation (§22 L1) extends "damage you deal" to "every kill applies 1 extra burn stack to all enemies." |
| `ON_EXTRA_ACTION_GRANTED` | the unit being granted an extra action | GRANT_EXTRA_ACTION resolves on this unit and `extra_action_used_this_round = False` | STORMCHAIN | Fires AFTER the flag is set to True. The granted unit will act again immediately after current resolution unwinds. Re-entry: if this trigger grants another extra action, the cap (§22 L4: `1` by default, `2` under `tempest_apex`) gates it. |

### 21.3 — New `op` effects

| Op | Targets | Value semantics | Owner cluster | Engine binding |
|---|---|---|---|---|
| `APPLY_BURN_STACK` | any unit | adds `value` to `target.burn_stacks` (additive, not refresh) | INFERNO | At ON_TURN_END for the unit holding stacks: deals `burn_stacks × 1` damage (real damage, element-neutral, post-DEF) and zeros stacks. Burn-stack damage fires ON_DAMAGE_TAKEN normally. |
| `APPLY_TAUNT` | any unit | sets target's TAUNT status for `value` rounds | BULWARK | Already shipped Phase 2; documented here because `worldroot_sentinel`'s legendary mutation references THORNS-on-allies, which is the BULWARK identity. No engine change required for TAUNT. |
| `THORNS` | self | sets passive `thorns_value` on this unit; on every ON_DAMAGE_TAKEN, attacker takes `thorns_value` real damage (element-neutral, no DEF reduction — thorns bypasses defense) | BULWARK | Real damage per Q1 resolution (Santiago 2026-04-23: "yeah it's real damage for sure"). Thorns damage CAN trigger the attacker's ON_DAMAGE_TAKEN, which can in turn trigger THIS unit's THORNS again — re-entry capped at 2 reflections per source-attack to prevent infinite loops. Thorns is bypassed by SILENCE on the THORNS-bearer. |
| `GRANT_EXTRA_ACTION` | any unit (typically ally) | grants target one extra action this round if `target.extra_action_used_this_round == False` AND extra-action cap not yet reached for this unit | STORMCHAIN | Sets `extra_action_used_this_round = True` on success, then fires `ON_EXTRA_ACTION_GRANTED` on target. The default per-unit per-round cap is 1; `tempest_apex` legendary (§22 L4) raises the cap to 2 for all allies on its team. The target acts immediately after current trigger resolution (depth-first, before the next unit in initiative order). |
| `SACRIFICE_SELF` | self | sets `self.hp = 0`; fires ON_DEATH for self AND ON_ALLY_DEATH for every alive teammate (Q2 lock) | REVENANT | Cannot be SILENCED (the op IS the unit's contribution). Counts as a "death by own action" — does not credit any opponent with ON_KILL. |

### 21.4 — Condition DSL extensions

The Phase 2 condition DSL (§7) extends to read the new state primitives. Additions to the whitelist:

- `self.burn_stacks` — int read of own burn stacks (rare; mostly read by enemies via `target.burn_stacks` — but DSL doesn't currently expose `target.*`. Future-extend if needed.)
- `self.shield_count` — int read of own shield count
- `self.extra_action_used_this_round` — bool read

NO new operators, no function calls, no subscripts. Same restricted-eval AST whitelist.

### 21.5 — Cross-cutting invariants

1. **Re-entrancy caps are mandatory.** Any new trigger that can fire a same-class trigger via cascade (ON_HEAL_RECEIVED → ON_HEAL_RECEIVED, THORNS → THORNS) MUST carry an engine-enforced depth cap (4 for heal chains, 2 for thorns reflections). Caps live in `combat.py` constants, not in card design.
2. **All new ops respect SILENCE.** SILENCE on the trigger-bearing unit suppresses ALL triggers on that unit, including the new ones. Exception: SACRIFICE_SELF (the op IS the contribution; suppressing it would deny the player agency).
3. **All new ops respect the existing trigger value cap (`MAX_TRIGGER_VALUE = 999`)** and the per-card trigger count cap (`MAX_TRIGGERS_PER_CARD = 8`). No op-specific overrides.
4. **All new state primitives serialize.** Match replay must round-trip burn_stacks, shield_count, extra_action_used_this_round losslessly.

### 21.6 — What Phase 4f-engine ships

A single PR that implements:
1. `UnitState` dataclass extension: `burn_stacks: int = 0`, `shield_count: int = 0`, `extra_action_used_this_round: bool = False`
2. `TriggerWhen` enum extension: `ON_HEAL_RECEIVED`, `ON_DAMAGE_TAKEN`, `ON_EXTRA_ACTION_GRANTED`
3. `EffectOp` enum extension: `APPLY_BURN_STACK`, `THORNS`, `GRANT_EXTRA_ACTION`, `SACRIFICE_SELF`
4. `combat.py` dispatch + re-entry caps + round-start reset for `extra_action_used_this_round` + ON_TURN_END burn_stacks tick
5. `conditions.py` whitelist: 3 new self attrs
6. `cards/loader.py`: accept new op/when names
7. Tests (per existing convention): one happy-path test per op, one re-entry-cap test for THORNS and ON_HEAL_RECEIVED, one Q2 contract test for SACRIFICE_SELF, one extra-action-cap test
8. Doc update: this section marked "shipped"

Phase 4f-pool follows Phase 4f-engine in a separate commit (retunes existing cards to use the new primitives where appropriate; promotes the 4 epics to legendary per §22). The counter-card slice ships earlier as Phase 4f-counters per §23.8 (was originally lumped into 4f-pool here as "5 NORMAL counters" — superseded by §23.3 to 6 elemental rare re-flavors with zero engine dependency, allowing it to ship ahead of 4f-engine).

---

## §22 — Legendary rule-changer principle (locked 2026-04-23)

### 22.1 — Principle

**Legendaries change the rules. Epics and below apply effects.**

The default rules of DAIMON combat — extra-action caps, damage-to-burn-stack conversion, ally-death trigger counts, distinct-element counts, heal-cascade behavior, thorns existence — are **global defaults** the engine applies everywhere. Cards from common through epic *operate within* those defaults: a common HEAL trigger heals N HP within the heal-cascade re-entry cap; an epic ON_OPENING_ATTACK alpha-strike fires its ON_OPENING_ATTACK once per match per the global one-shot rule.

Legendaries are different. **A legendary's passive identity is to mutate one global rule for the duration the legendary is alive on its team.** The mutation is *not* a one-time trigger that fires and resolves; it is a *standing modification* to how the engine resolves all events on the legendary's team while the legendary is alive. When the legendary dies, the rule snaps back to default.

This is what makes legendary tier mechanically meaningful instead of just "epic but with bigger numbers." It is also why there are exactly 6 legendaries (one per strategic archetype, §3): rule mutations are precious. A legendary's rule mutation must be carefully scoped, must respect the cluster identity (§20), and must combine cleanly with other legendaries' mutations on the same team (mutations layer additively unless explicitly noted).

**Anti-pattern**: an epic with a "legendary-shaped" effect (e.g. "+1 to extra-action cap for self once per match"). Epics MUST stay within global rules. If a design wants rule-mutation behavior, that design belongs at legendary tier.

### 22.2 — The 6 V1 legendary rule-changes (accepted 2026-04-23)

All 6 are accepted as-proposed. Balance testing deferred to Phase 5 sim harness per Santiago's 2026-04-23 directive: *"we'll do thorough balance testing later of each archetype to see what is broken, but for now we can move forward with this."* Any of the 6 mutations may be retuned post-Phase-5 based on sim outcomes; the *principle* (§22.1) is the lock, not the specific values.

#### L1 — `magma_tyrant` (INFERNO) — "every hit burns"

**Mutation**: while `magma_tyrant` is alive on your team, **every damage instance any of your allies deals also applies 1 burn stack to the damaged target**.

**Rule it changes**: APPLY_BURN_STACK is normally an explicit op (only triggered by cards that author it). Under this mutation, it becomes a global side-effect of every damage instance.

**Stacking**: if multiple INFERNO cards deal damage in the same round, each adds 1 stack via the mutation, on top of any explicit APPLY_BURN_STACK ops they ALSO carry. A `magma_tyrant` + `solar_phoenix` combo where solar_phoenix attacks 3 times in a turn = 3 mutation-applied stacks plus any explicit stacks from solar_phoenix's own triggers.

**Engine binding**: `combat.py::_apply_damage` checks for any alive ally with the `magma_tyrant` rule-mutation flag and adds `target.burn_stacks += 1` after damage application. Does NOT consume MAX_TRIGGERS_PER_CARD on the dealer.

**Combat with other legendaries on same team**: stacks linearly. If a future expansion adds a 2nd "every hit burns" legendary, both apply +1 → +2 per damage. Within V1: only `magma_tyrant` carries this mutation.

#### L2 — `worldroot_sentinel` (BULWARK) — "all allies have THORNS 2"

**Mutation**: while `worldroot_sentinel` is alive on your team, **every alive unit on your team has THORNS 2** (reflects 2 real damage on every ON_DAMAGE_TAKEN, per §21.3).

**Rule it changes**: THORNS is normally a per-card trigger (a unit must explicitly carry a THORNS op via its triggers). Under this mutation, THORNS 2 is granted as a passive to every ally — including non-BULWARK allies (a TIDAL healer on a BULWARK shell suddenly reflects 2).

**Stacking**: if a unit ALREADY has explicit THORNS X via its own triggers, the mutation adds +2 (not max-refresh). A BULWARK card with intrinsic THORNS 3 + `worldroot_sentinel` alive = THORNS 5.

**Engine binding**: `combat.py::_apply_damage` checks the mutation flag; if active, applies +2 to every ally's effective `thorns_value` lookup. The 2-reflection re-entry cap (§21.5) still applies — `worldroot_sentinel` does NOT bypass loop protection.

#### L3 — `tide_empress` (TIDAL) — "every heal heals everyone"

**Mutation**: while `tide_empress` is alive on your team, **whenever any ally is healed by any source, every other alive ally heals for 1 HP**.

**Rule it changes**: HEAL is normally single-target. Under this mutation, every HEAL op silently triggers a team-wide +1 trickle.

**Stacking**: trickle does NOT itself trigger trickle (engine breaks the cascade — the +1 trickle counts as a system-level heal, not a HEAL op, and does NOT fire ON_HEAL_RECEIVED). This is INTENTIONAL — without the cascade-break, `tide_empress` would deadlock the heal-chain re-entry cap (§21.5). Without this guard the mutation could stack into infinite trickles. Lock-text justification: **proximity antibody** — if a future reader looks at this mutation and thinks "the trickle should also trigger ON_HEAL_RECEIVED, that's more elegant" — it's not. The cap is the only thing keeping `tide_empress` from being an infinite loop. Not a bug, feature.

**Engine binding**: `combat.py::_apply_heal` checks the mutation flag; if active, after the primary heal resolves and after the heal's ON_HEAL_RECEIVED fires, applies a silent `+1` to every alive ally (no triggers, no events).

#### L4 — `tempest_apex` (STORMCHAIN) — "extra-action cap raised 1→2"

**Mutation**: while `tempest_apex` is alive on your team, **the per-unit per-round extra-action cap is 2 (raised from default 1) for every ally on your team**.

**Rule it changes**: `unit.extra_action_used_this_round` (§21.1) is normally a strict bool; under default rules, once True, no further GRANT_EXTRA_ACTION lands on that unit this round. Under this mutation, the gating becomes "GRANT_EXTRA_ACTION lands if extra-action count on this unit this round < 2."

**Engine binding**: replace `unit.extra_action_used_this_round: bool` with `unit.extra_actions_used_this_round: int`; default-cap check uses `< 1` (default) or `< 2` (mutation active). Per §21.6 Phase 4f-engine ships the int version directly to avoid an interim refactor — the bool is a documentation simplification only.

**Combat math**: STORMCHAIN can now cascade: A grants extra to B (B now at 1); B's extra-action attacks; B grants extra to C (C now at 1); C's extra-action attacks; in a future round, C grants extra to A (A now at 2 — under default rules this would fail, under mutation it succeeds). Single-round cap stays 2 per unit even with mutation; cascade depth across rounds is unbounded.

#### L5 — `voidking_morr` (REVENANT) — "ON_ALLY_DEATH triggers fire twice"

**Mutation**: while `voidking_morr` is alive on your team, **every `ON_ALLY_DEATH` trigger on every alive ally fires twice instead of once**.

**Rule it changes**: ON_ALLY_DEATH is normally a single-fire trigger per ally death event. Under this mutation, every fire counts as two — the trigger's effect resolves twice in immediate sequence.

**Stacking**: the doubling is multiplicative, NOT additive. If a future expansion legendary adds "ON_ALLY_DEATH triggers fire ×3 instead of ×1" (hypothetical), and both are alive: the rule resolves to ×3 (the higher mutation wins, not 1+2+3 stacking). Lock-text justification — **proximity antibody**: a future reader might naturally assume mutations stack additively (×6 from "+1 + ×3"). They do not. Multiplier-style mutations override; +N-style mutations stack additively. This split is intentional to keep mutation interaction predictable. Not a bug, feature.

**Engine binding**: `combat.py::_fire_triggers_for_unit` for ON_ALLY_DEATH events checks the mutation flag and resolves the trigger's effect block twice if active. The 2nd fire respects SILENCE (re-checks silence state between fires — if the first fire's effect SILENCEs the unit, the 2nd fire is suppressed).

**Re-entrancy**: if the 2nd fire causes another ally to die (e.g. SACRIFICE_SELF as part of the trigger), THAT death's ON_ALLY_DEATH fires for surviving allies — also doubled. Engine-enforced cascade depth cap = 8 nested ON_ALLY_DEATH fires per source event (otherwise REVENANT can deadlock the round on a 6-card team-wipe cascade).

#### L6 — `world_eater` (FLUX) — "distinct_elements counts +2"

**Mutation**: while `world_eater` is alive on your team, **every read of `team.distinct_elements` returns the actual count + 2** for FLUX cards on your team.

**Rule it changes**: `team.distinct_elements` (§21.1) is normally a live count of unique elements among alive units. Under this mutation, condition-DSL reads of the value get +2 added before comparison.

**Scope**: ONLY FLUX cards' condition gates see the +2. Non-FLUX condition gates (which would not exist in V1 — only FLUX cards use this gate per §20 anti-pattern guard, but a future expansion might) see the un-mutated count. This scope-narrowing is the whole point: `world_eater` is a FLUX-team enabler, not a global cheat-mode.

**Stacking**: a 4-element team becomes effectively 6 distinct for FLUX condition resolution. A `>= 6` gate (which exists nowhere in V1 but is reachable in expansion) would land on a 4-distinct team. A `>= 4` gate lands on a 2-distinct team. Mono-element team (1 distinct) becomes effectively 3 — which clears `>= 2` and `>= 3` gates, unlocking FLUX cards in mono-element shells specifically when `world_eater` is present.

**Engine binding**: `conditions.py` evaluation context checks the FLUX-card flag (passed in by the caller in `_fire_triggers_for_unit`) and the mutation flag, and adds +2 to the `team.distinct_elements` value passed into the eval context for FLUX-card condition evaluation.

**Lock-text justification — proximity antibody**: a future reader might "fix" this to apply universally (FLUX and non-FLUX). Don't. The narrowed scope is what keeps the mutation cluster-coherent — FLUX is the diversity-scaler archetype; `world_eater`'s mutation supercharges that archetype's identity, NOT every card's identity. Not a bug, feature.

### 22.3 — Mutation interaction matrix (V1)

All 6 V1 legendaries can cohabit one team (deckbuilding allows 6-card teams = exactly the legendary count). Their mutations interact as follows:

| Pair | Interaction |
|---|---|
| L1 `magma_tyrant` × L2 `worldroot_sentinel` | INFERNO burn stacks accumulate on attackers (via L1) AND attackers take THORNS 2 reflection (via L2). Stacks; both apply. |
| L1 × L3 `tide_empress` | The +1 trickle (L3) does NOT count as a damage instance, so does NOT trigger L1. Independent. |
| L4 `tempest_apex` × L5 `voidking_morr` | Doubled extra-action grants (L4 raises cap 1→2; L5 doubles ON_ALLY_DEATH). Independent rule domains. |
| L5 × L6 `world_eater` | A FLUX trigger that fires on ON_ALLY_DEATH (none exist in V1 but legal) would fire ×2 (L5) and read distinct_elements +2 (L6). Independent. |
| Any pair involving L3 trickle | Trickle never cascades, never triggers ANY mutation. The cascade-break (§22.2 L3) is the engine guard. |
| All 6 alive | Maximum-mutation state. Engine remains deterministic; cascade caps (re-entry caps in §21.5; ON_ALLY_DEATH cap of 8 in §22.2 L5) prevent any pathological loop. Phase 5 sim harness must include "all-6-legendary mirror match" as a regression test for cap behavior. |

### 22.4 — Phase 4f deliverables (SUPERSEDED by §23.8)

The original two-commit plan (4f-engine + 4f-pool with NORMAL-counter authoring inside 4f-pool) is **superseded by §23.8**, which restructures Phase 4f into three commits (4f-counters + 4f-engine + 4f-pool) and reverses the NORMAL-counter design to the elemental-rare-counter design (§23.3). The 4 epic→legendary promotions remain as listed below; only the counter authoring + commit grouping change.

Original deliverable list (kept here for traceability — see §23.8 for the live list):

**4f-engine** (per §21.6): the 4 new ops + 3 new whens + 4 state primitives + condition DSL extensions + caps + tests.

**4f-pool**: 
1. Promote 4 epics to legendary per §3 (the V1 distribution shift):
   - `magma_tyrant` epic → legendary, retune stats to legendary envelope (§4 budget 38–46), add the L1 mutation tag
   - `worldroot_sentinel` epic → legendary, same retune + L2 mutation tag
   - `tide_empress` epic → legendary, same retune + L3 mutation tag
   - `tempest_apex` epic → legendary, same retune + L4 mutation tag
2. Confirm `voidking_morr` and `world_eater` mutation tags (L5, L6) wired correctly
3. Re-author existing INFERNO cards to use APPLY_BURN_STACK where they currently use APPLY_BURN (Phase 2 status)
4. ~~Add 5 NORMAL counter cards~~ → **superseded by §23.3**: counters are 6 elemental rares re-flavored in place, shipped in the new 4f-counters commit ahead of 4f-engine.
5. Update `tests/test_phase4_distribution.py` for new rarity histogram (98C/60U/28R/8E/6L) — extended in §23.8 to also assert per-archetype matrix.
6. Add legendary-mutation tests: one per L1–L6, plus the "all-6 alive" regression test from §22.3

**4f-pool fix-forward of `archetype: null`**: the existing `archetype: null` on NORMAL JSONs (set in commit `40e28f8`) is the CORRECT value per §2.0. No JSON change needed — the lock is the §2.0 documentation, not a data migration. The forward-fix is purely the doc lock + the §20 NORMAL anti-pattern guard.

### 22.5 — Open follow-ups

- Phase 5 sim harness must validate the "rule-changes layer cleanly" claim empirically (run all 6-legendary mirror, confirm no infinite loops, no determinism breaks, no NaN HP).
- Future expansions adding a 7th archetype must extend §22 with the 7th legendary's mutation BEFORE authoring any cards in that cluster. Mutation precedes content.
- If sim shows any of L1–L6 over-/under-powered, retune the *specific* mutation values (e.g. L1 stacks → 2 instead of 1; L4 cap → 3 instead of 2). The §22.1 *principle* does not retune — only the values.

---

## §23 — Card-design completion (locked 2026-04-23)

### 23.1 — Principle

Charter §1–§22 set the *framework*. §23 closes the remaining open authoring questions before Phase 4f implementation begins. Every subsection here either **locks** an answer Santiago asked for ("answer all those questions with your best instinct"), or **explicitly reverses** an earlier-charter decision that disk reality (Phase 4e-counters WIP, the 200-card pool lock, the actual archetype × rarity matrix) makes untenable. Reversals are called out by section so the doc reads coherently end-to-end.

**Method note**: every count in §23.2 was computed from the live `daimon/catalog/v1_alpha/` JSONs on `monster-pivot @ 2026-04-23`, not from prior-section prose. The table is a *measurement*, not an *assertion*. Future drift is caught by `tests/test_phase4_distribution.py` (extended in Phase 4f to assert the per-archetype matrix below).

### 23.2 — Per-archetype rarity distribution (LOCKED — measured + projected post-promotion)

The **post-Phase 4f** pool (after the 4 epic→legendary promotions in §22.4 land):

| Archetype | C | U | R | E | L | Total | Notes |
|---|---|---|---|---|---|---|---|
| INFERNO | 13 | 6 | 0 | 1 | 1 | 21 | `magma_tyrant` epic→legendary; 1 epic anchor remains (`solar_phoenix`) |
| BULWARK | 13 | 7 | 0 | 1 | 1 | 22 | `worldroot_sentinel` epic→legendary; 1 epic anchor remains (`bulwark_patriarch`) |
| TIDAL | 13 | 7 | 0 | 1 | 1 | 22 | `tide_empress` epic→legendary; 1 epic anchor remains (`coral_augur`) |
| STORMCHAIN | 13 | 7 | 0 | 1 | 1 | 22 | `tempest_apex` epic→legendary; 1 epic anchor remains (`arc_predator`) |
| REVENANT | 13 | 7 | 0 | 1 | 1 | 22 | unchanged — `voidking_morr` was already L; `crypt_wraith` is the epic anchor |
| FLUX | 10 | 10 | 0 | 2 | 1 | 23 | unchanged — `world_eater` was already L; `prism_chimera` + `rainbow_drake` are the 2 epics |
| **null** (NORMAL element + untagged elemental rares) | 23 | 16 | 28 | 1 | 0 | 68 | NORMAL element = 15 cards (8C/4U/2R/1E); the other 53 null cards are element-flavored utility (15C non-NORMAL + 12U non-NORMAL + 26R non-NORMAL) |
| **TOTAL** | **98** | **60** | **28** | **8** | **6** | **200** | matches §3 |

**Why every strategic-archetype rare slot is 0**: by current authoring convention, the `archetype` field is set ONLY on epics and legendaries. Commons and uncommons inherit archetype-coding *from their evolution-line parent* (the rare/epic ancestor), but the field is unset on the JSON to keep the per-card schema lean. Rares carry `archetype: null` because they're authored as element-flavored mid-tier filler, not as cluster anchors. **This is intentional and load-bearing**: it makes "is this card archetype X?" a tooling-time inference (line-of-evolution lookup), not a per-card metadata burden, and it keeps the soft-priority cluster model (§2.0) honest — a rare that splashes into multiple archetypes isn't lying about its archetype field, because it doesn't claim one. Phase 5 distinctness audit (§20) operates on signature-op presence, not on `archetype` field reads, so this convention is forward-compatible.

**Lock-text justification — proximity antibody**: a future reader might "fix" rares by adding `archetype` tags to align them with epic anchors. Don't. The null-rare convention is what enables the counter-card design in §23.3 (counters live at rare tier with `archetype: null`, free to fight any cluster without claiming membership in one). Adding archetype tags to rares would force a binary decision per card that the soft-cluster model (§2.0) explicitly rejects. Not a bug, feature.

### 23.3 — Counter-card design (LOCKED — REVERSES §20 row 2)

**Reversal**: §20 specified "5 counter — designated archetype counters" living in the NORMAL element. **§23 supersedes that**: counters live as **6 elemental rares re-flavored in place** (one per strategic archetype, FLUX included), keeping `archetype: null` and using only existing engine vocabulary.

**Why the reversal**:
1. **The 200-card pool is locked.** Adding 5 NORMAL counter cards would require trimming 5 cards elsewhere, re-running the distribution lock test, re-balancing the elemental matchup math, and re-doing the Phase 4e-pool audit. Re-flavoring 6 existing rares is a zero-net-card-count operation.
2. **All necessary engine ops already exist.** A NORMAL-counter design (the original §20 plan) needed ~10 new ops/whens (CLEANSE_BURN_STACKS, REMOVE_SHIELD, APPLY_HEAL_BLOCK, etc.). The elemental-rare-counter design uses `APPLY_SILENCE`, `APPLY_POISON`, `BUFF_DEF`, `DEBUFF_DEF`, `ADD_SHIELD`, `BUFF_SPD`, `DAMAGE`, `HEAL`, `DEBUFF_ATK`, `ON_BATTLE_START`, `ON_LOW_HP`, `ON_OPENING_ATTACK`, `ON_ROUND_START`, `ON_ATTACK` — every one of those is already in `daimon/engine/types.py` and `combat.py`. Phase 4e-counters runs with **zero engine changes**.
3. **Counters at rare tier reach thoughtful deckbuilders.** A counter at common dilutes the gacha pull rate; at rare it sits in the band where players are intentionally tuning a loadout against an expected meta. This is the standard TCG move (MTG sideboard rares, Hearthstone tech rares).
4. **NORMAL stays compositionally pure as support.** §20 row 1 ("7 support") becomes "all 15 NORMAL are support / utility." NORMAL is the no-archetype splashable element; loading it with counter logic would re-introduce hidden archetype gates by another name.
5. **FLUX gets a counter too.** §20 had 5 counters covering 5 archetypes (FLUX intentionally uncountered). The 6-rare design covers all 6 strategic archetypes, including FLUX (`stormhare` race-burst cracks FLUX before its `team.distinct_elements` gates assemble).

**The 6 counters** (re-flavored from existing rares; full mechanical specs in `scripts/author_phase4e_counters.py`):

| Card | Element | Counters | Mechanic |
|---|---|---|---|
| `forest_warden` | NATURE | INFERNO | `ON_BATTLE_START BUFF_DEF ALL_ALLIES 3` + `ON_LOW_HP HEAL SELF 5` (out-lasts BURN tick clock) |
| `maelstrom_serpent` | WATER | BULWARK | `ON_OPENING_ATTACK DEBUFF_DEF ALL_ENEMIES 4` + `ON_ATTACK DAMAGE LOWEST_HP_ENEMY 3` (cracks shield walls, kills the squishy back-line) |
| `mindroot` | VOID | TIDAL | `ON_BATTLE_START APPLY_POISON ALL_ENEMIES 3r` + `ON_ATTACK DAMAGE LOWEST_HP_ENEMY 3` (DOT competes with heal cycle) |
| `bulwarthog` | NATURE | STORMCHAIN | `ON_ROUND_START BUFF_DEF ALL_ALLIES 1` + `ON_ROUND_START ADD_SHIELD SELF 3` (round-start triggers fire under STUN; stacks across rounds, single SILENCE doesn't undo prior rounds) |
| `abyss_warden` | VOID | REVENANT | `ON_BATTLE_START APPLY_SILENCE ALL_ENEMIES 3r` + `ON_ATTACK DEBUFF_ATK ALL_ENEMIES 3` (SILENCE suppresses ON_DEATH/ON_ALLY_DEATH per `combat.py::_fire_triggers_for_unit` — keystone anti-REVENANT) |
| `stormhare` | VOLT | FLUX | `ON_OPENING_ATTACK DAMAGE LOWEST_HP_ENEMY 4` + `ON_BATTLE_START BUFF_SPD ALL_ALLIES 1` (race-burst kills FLUX squishies before distinct_elements gates fire) |

**Counter-coverage tagging**: counters carry their counter-target in `scripts/author_phase4e_counters.py::EXPECTED_SLOTS` as the single source of truth. Card JSONs do NOT add a new `counters` field in V1 — the script + tests are the audit surface. **Lock-text justification**: adding a JSON `counters` field would tempt the engine to *read* it (e.g. "fire harder when facing target archetype"), reintroducing archetype-as-gate logic that §2.0 explicitly forbids. Counter status is *implementation* (mechanic chosen to punish a pattern), not *engine state*. Not a bug, feature.

**WIP status (as of 2026-04-23)**: `scripts/author_phase4e_counters.py` and `tests/test_phase4e_counters.py` are on disk but UNCOMMITTED. The 6 card JSONs and `manifest.json` (version bump 0.4.4 → 0.4.5) carry the retunes. Test status: **8 of 9 pass**. The single failure is `TestForestWardenAntiInferno::test_low_hp_self_heal_fires` — the test's bruiser two-shots forest_warden (HP 24 → 12 → 0), skipping past the 25%-of-24 = 6 HP threshold without ever lingering inside the LOW_HP window. The mechanic is correctly authored on the card; the *test fixture* picks an attacker that's too brutal. Fix-up belongs to Phase 4f-pool: rebalance the bruiser's atk to ~6 so forest_warden lands in 5–6 HP after the first strike, triggering ON_LOW_HP before the second strike. **This test fix is the only unfinished work in Phase 4e-counters.**

### 23.4 — Tech cards: DEFERRED from V1 (REVERSES §20 row 3)

**Reversal**: §20 specified "3 tech — toolbox cards with niche disruption value (e.g. SILENCE-on-low-HP, single-target STUN priority, generic dispel)." **§23 defers this entirely to V1.1+.**

**Why deferred**:
1. **The 6-counter design (§23.3) absorbs the disruption surface.** SILENCE lives on `abyss_warden`; AoE soft-control lives on the 6 counters; targeted disruption (POISON, DEBUFF_DEF) lives on the 6 counters. Adding 3 more "toolbox" cards would duplicate function without adding coverage.
2. **Tech cards optimize for a meta that doesn't exist yet.** "Toolbox" cards are valuable in a known meta where players slot them as answers to specific dominant strategies. V1 has no meta — it ships, then Phase 5 sim + early-player-data establishes one. Authoring tech cards now would be guessing at the meta.
3. **The 200-card pool lock.** Adding 3 NORMAL tech cards would require trimming 3 cards elsewhere — same constraint as §23.3.
4. **NORMAL stays support-only.** Cleaner identity: NORMAL = "splash any deck, never the win condition" (§20). Tech cards are by design situational; they live somewhere else (likely a future "set 2" expansion designed against an established meta).

**V1.1+ disposition**: when Phase 5 sim or early gameplay surfaces a specific dominant pattern that the 6 counters don't address, V1.1 ships the tech card(s) needed to address it. Open-ended pre-design is rejected. (Charter §20 row 3 is now "0 tech — see §23.4.")

### 23.5 — Updated NORMAL element split (REVISES §20 row 1)

The locked NORMAL split is **15 support — full audit**:

| Card | Rarity | Mechanic | Splash use |
|---|---|---|---|
| `pebbler` | C | `ON_TURN_END ADD_SHIELD RANDOM_ALLY 1` | universal pad — sticks 5+ shields across a 5-round match |
| `runic_whelp` | C | `ON_BATTLE_START BUFF_DEF ALL_ALLIES 1` | small upfront DR for any cluster |
| `cloth_sprite` | C | `ON_TURN_END HEAL RANDOM_ALLY 1` | trickle heal — 5 heals across the match |
| `page_slime` | C | `ON_TURN_END HEAL SELF 2` | self-sustain bait, ties up enemy attacks |
| `brass_mole` | C | `ON_BATTLE_START BUFF_ATK SINGLE_ALLY 2` | single-target ATK pad |
| `mossback_ox` | C | `ON_BATTLE_START APPLY_TAUNT SELF 5` | taunt body, soaks enemy actions |
| `quill_cat` | C | `ON_BATTLE_START DEBUFF_DEF ALL_ENEMIES 1` | small AoE shred — softens any opponent |
| `grove_pup` | C | `ON_TURN_END BUFF_DEF RANDOM_ALLY 1` | trickle DR — accumulates over the match |
| `stoneward` | U | `ON_BATTLE_START ADD_SHIELD ALL_ALLIES 2` | team-wide shield burst |
| `rune_owl` | U | `ON_BATTLE_START BUFF_SPD ALL_ALLIES 1` | tempo splash for any cluster (light SPD bump per §20 SPD value band) |
| `wrought_bear` | U | `ON_DAMAGE_TAKEN APPLY_TAUNT SELF 1` (Phase 4f-engine: reactive taunt — see §21.2 ON_DAMAGE_TAKEN) | reactive taunt — pulls fire when hit |
| `mendicant_sphinx` | U | `ON_TURN_END HEAL RANDOM_ALLY 3` | sustained heal trickle, larger than `cloth_sprite` |
| `aegis_lion` | R | `ON_BATTLE_START ADD_SHIELD ALL_ALLIES 3` + `ON_BATTLE_START BUFF_DEF SELF 2` | team-shield + self-pad |
| `loremaster_ape` | R | `ON_BATTLE_START BUFF_ATK ALL_ALLIES 3` + `ON_TURN_END BUFF_SPD RANDOM_ALLY 3` | team ATK opener + tempo trickle |
| `concord_phoenix` | E | `ON_BATTLE_START HEAL ALL_ALLIES 5` + `ON_ALLY_DEATH BUFF_ATK ALL_ALLIES 4` | team heal opener + REVENANT-flavored cascade — splashes into anything |

**No retirements, no replacements**: the 15 NORMAL cards as authored in Phase 4e-pool (commit `40e28f8`) all serve as support. The pre-summary plan to retire 8 of them was based on the now-reversed §20 row-2/row-3 design. **Confirmed no NORMAL cards need re-authoring for §23 lock.**

**Stat-band validation**: spot-checked all 15 against §4 budgets — within band. Phase 4f-pool does NOT need to retune NORMAL cards; the only Phase 4f-pool work on NORMAL is `wrought_bear` migrating its trigger from `ON_TAKE_DAMAGE` (Phase 2 shipped) to `ON_DAMAGE_TAKEN` (Phase 4f-engine, semantically distinct per §21.2 — fires only on damage that landed, not on shield-absorbed damage). One-line edit.

### 23.6 — Epic→legendary promotion strategy (LOCKED — confirms §22.4)

**Strategy**: **rewrite-in-place** for all 4 promotions. The card_id, species, name, and existing flavor stay; the card JSON gets a full retune of `rarity`, `atk/def/hp/spd` (legendary envelope from §4), `triggers` (replaced with the §22 mutation contract — see below), and `art` path (`art/epic/X.png` → `art/legendary/X.png`).

**Why rewrite-in-place vs. retire-and-author-new**:
1. **Card identity continuity**: existing players (Santiago + early access) who pulled `magma_tyrant` epic during testing keep "the same card" — it just leveled up. Retiring the epic would mean their card_id vanishes from collection and Santiago's V1 testing tracks die.
2. **No species lore drift**: the 4 promoted cards already have flavor + lineage that fits the §22 mutation. `magma_tyrant` *should* burn-on-every-hit; `worldroot_sentinel` *should* radiate THORNS to allies; etc. The flavor was ahead of the mechanic.
3. **No art commission needed**: `art/epic/magma_tyrant.png` can be re-used at `art/legendary/magma_tyrant.png` (the legendary frame is post-process; the art itself is the same). Saves 4 art slots.
4. **Manifest stays at 200**: retire-and-author would require careful per-element re-balancing. In-place keeps every per-element/per-archetype count (§23.2) stable.

**The 4 mechanical-rewrite contracts** (per §22.2 mutations):

- **`magma_tyrant`** (FIRE/INFERNO, epic → L1): triggers replaced with the L1 mutation tag. New stat band: legendary 38–46. Mutation: "while alive, every damage instance any ally deals also applies 1 burn stack." Engine binding: §22.2 L1.
- **`worldroot_sentinel`** (NATURE/BULWARK, epic → L2): triggers replaced with the L2 mutation tag. Mutation: "while alive, every alive ally has THORNS 2." Engine binding: §22.2 L2.
- **`tide_empress`** (WATER/TIDAL, epic → L3): triggers replaced with the L3 mutation tag. Mutation: "every heal heals everyone for 1 (cascade-broken)." Engine binding: §22.2 L3.
- **`tempest_apex`** (VOLT/STORMCHAIN, epic → L4): triggers replaced with the L4 mutation tag. Mutation: "extra-action cap raised 1→2 per ally per round." Engine binding: §22.2 L4.

**Mutation-tag schema** (NEW — locked here): legendary cards carry a new optional field `rule_change: <mutation_id>` where `mutation_id ∈ {L1, L2, L3, L4, L5, L6}`. The `triggers` array on a legendary MAY be empty (the mutation IS the card's mechanical contribution) or MAY carry secondary triggers that operate within global rules (e.g. an ON_BATTLE_START stat buff that has nothing to do with the mutation). Engine reads `rule_change` at battle start and registers the corresponding mutation flag on the team. The 6 V1 mutations are hard-coded in `combat.py`; the field is an enum-style key, not a free-form spec — V2 expansions add new mutation IDs by editing `combat.py` (locked dispatch table), not by adding spec fields to JSONs. This keeps the cards JSON-side declarative ("which rule do I change?") and the engine-side imperative ("how is rule L1 implemented?").

**`voidking_morr` and `world_eater`** (already legendary in V1): Phase 4f-pool adds the `rule_change: L5` and `rule_change: L6` fields respectively. Their existing triggers stay (they were authored against the §22 mutations from the start of the legendary tier).

### 23.7 — Engine readiness summary

| Need | Phase 4e-counters (§23.3) | Phase 4f-engine (§21.6) | Phase 4f-pool (§23.6 + §22.4) |
|---|---|---|---|
| Ops used | all already in `EffectOp` enum | adds `APPLY_BURN_STACK`, `THORNS`, `GRANT_EXTRA_ACTION`, `SACRIFICE_SELF` | uses §21 ops + new `rule_change` field reader |
| Whens used | all already in `TriggerWhen` enum | adds `ON_HEAL_RECEIVED`, `ON_DAMAGE_TAKEN`, `ON_EXTRA_ACTION_GRANTED` | uses §21 whens |
| State primitives | none new | adds `burn_stacks`, `shield_count`, `extra_action_used_this_round`, mutation flags on team | uses §21 primitives |
| Tests | 8/9 pass; 1 fix-up (test bug, not engine bug) | 4 happy-path + 2 re-entry-cap + Q2 contract + extra-action-cap | one mutation test per L1–L6 + all-6 regression |
| Net engine code change for §23.3 | **zero lines** | new ops/whens/primitives | new `rule_change` dispatcher |

**Critical sequencing**: §23.3 (Phase 4e-counters) can ship **before** Phase 4f-engine. §23.6 (epic→legendary promotions) must ship **after** Phase 4f-engine because the mutation contracts depend on the new ops/primitives the engine PR adds.

### 23.8 — Updated Phase 4f deliverable list (SUPERSEDES §22.4)

Phase 4f ships in **three commits** (was two in §22.4):

**4f-counters** (new — extracted from §22.4 item 4): commit the existing WIP — 6 card JSONs + `scripts/author_phase4e_counters.py` + `tests/test_phase4e_counters.py` + manifest 0.4.4 → 0.4.5 + the test fix-up for `test_low_hp_self_heal_fires` (rebalance bruiser stats to land forest_warden in the LOW_HP window). **Zero engine code change.**

**4f-engine** (per §21.6, unchanged from §22.4): the 4 new ops + 3 new whens + 4 state primitives + condition DSL extensions + caps + tests + new `rule_change` field reader on `cards/loader.py` + new mutation-flag dispatch in `combat.py`.

**4f-pool** (revised from §22.4):
1. Promote 4 epics to legendary per §23.6 (rewrite-in-place: rarity flip, stat retune to legendary band, triggers replaced with mutation contract, `rule_change: Lx` field added, art path move).
2. Add `rule_change: L5` to `voidking_morr` and `rule_change: L6` to `world_eater` (existing legendaries — wire them to the mutation dispatcher).
3. Migrate `wrought_bear` (NORMAL U) from `ON_TAKE_DAMAGE` → `ON_DAMAGE_TAKEN` (one-line edit per §21.2 semantic distinction).
4. Re-author existing INFERNO cards that currently use `APPLY_BURN` (Phase 2 status) to use `APPLY_BURN_STACK` where the design intent was stack-based (per §22.4 item 3 — unchanged).
5. Update `tests/test_phase4_distribution.py` to assert the §23.2 per-archetype × rarity matrix (extends current rarity-only assertion).
6. Add legendary-mutation tests: one per L1–L6 + the "all-6 alive" regression test from §22.3.

**Removed from §22.4 deliverable list**: "Add 5 NORMAL counter cards" — superseded by §23.3 (counters are 6 elemental rares, already drafted as Phase 4e-counters WIP, ship in the 4f-counters commit).

### 23.9 — Open follow-ups (deferred beyond V1 unless Phase 5 sim flags them)

- **Tech cards (§23.4)**: re-evaluate after V1 sim + early-player-data — only ship in V1.1+ if a specific dominant pattern emerges that the 6 counters don't address.
- **Counter-coverage gaps**: §23.3's 6 counters cover the 6 strategic archetypes 1:1. If a future expansion adds a 7th archetype (per §22.5), it needs a 7th counter — likely from the existing 22 untagged elemental rares (re-flavor like Phase 4e-counters did the first 6). Mutation precedes content (§22.5); counter precedes content equivalently.
- **Per-archetype rare authoring**: §23.2 shows zero rares with strategic-archetype tags. If post-V1 design wants archetype-tagged rares (e.g. "BULWARK rare anchors"), the convention shift goes through a §23 amendment that explicitly justifies why; default is to keep rares null-archetype per §23.2.
- **Mutation-tag schema (`rule_change` field)**: V1 hard-codes 6 mutation IDs in `combat.py`. V2+ expansion mutations need either (a) new IDs in the same dispatch table (preferred, simple) or (b) a JSON-side mutation spec language (rejected for V1 — opens too much engine surface; revisit only if V2 design demands it).
- **Counter card stat retunes**: Phase 5 sim must verify the 6 counters actually beat their target archetype meaningfully without dominating non-target matchups. If `forest_warden` counters INFERNO too hard (>70% win rate) or too soft (<55%), retune the trigger value (still within rare value band per `RARE_VALUE_BAND_*` in `author_phase4e_counters.py`).

---

## §24 — Phase 4h changelog: catalog/engine gap closure (2026-04-23)

### 24.1 — Trigger

Phase 4f-engine (commit `83475bf`) shipped 4 new ops + 3 new whens. Phase 4f-pool (commit `5fae1ef`) bound *some* of them: `APPLY_BURN_STACK` (5 INFERNO migrations), `ON_DAMAGE_TAKEN` (1 NORMAL migration on `wrought_bear`). Phase 4g (commit `a559a4e`) shipped 6 showcase loadouts and surfaced an audit gap: **3 of the 4 engine ops and 2 of the 3 engine whens had ZERO catalog cards using them**. The L4 showcase (`tempest_apex`-led STORMCHAIN tempo) explicitly flagged its mutation as "dormant from a pure-catalog match" because no card carried `GRANT_EXTRA_ACTION`.

Untouched by Phase 4f-pool:
- `THORNS` op — 0 catalog cards (only `worldroot_sentinel` L2 mutation grants it implicitly)
- `GRANT_EXTRA_ACTION` op — 0 catalog cards
- `SACRIFICE_SELF` op — 0 catalog cards
- `ON_HEAL_RECEIVED` when — 0 catalog cards
- `ON_EXTRA_ACTION_GRANTED` when — 0 catalog cards

This violates the implicit charter promise that engine vocab is JSON-author-callable: the engine carried code paths the catalog could not invoke. Phase 4h closes this gap.

### 24.2 — Method (REWRITES, not new cards)

Per §23.6 precedent, in-place rewrite preserves the §23.2 per-archetype × rarity matrix and the 200-card pool lock. Adding net-new cards would force trimming elsewhere and a matrix re-balance. Rewriting 7 cards' triggers — keeping `card_id`, `species`, `name`, `flavor`, `rarity`, `archetype`, `element`, stats, and the count of triggers all unchanged — costs zero matrix cells and zero count drift.

### 24.3 — The 7 in-place rewrites

| Card | Cluster | Tier | Old trigger | New trigger | Why |
|---|---|---|---|---|---|
| `thornserpent` | BULWARK | U | `ON_TAKE_DAMAGE DAMAGE RANDOM_ENEMY 3` | `ON_BATTLE_START THORNS SELF 3` | Flavor was thorny; mechanic now uses the THORNS primitive (passive reflection) rather than a one-shot retaliation. Strictly stronger if the unit is hit ≥2× per match. |
| `bramble_warden` | BULWARK | U | `ON_BATTLE_START BUFF_DEF ALL_ALLIES 3` | `ON_BATTLE_START THORNS SELF 3` | Two THORNS bearers in the BULWARK pool gives mono-BULWARK shells real reflection identity (independent of `worldroot_sentinel` L2 mutation). Loses the team-DEF buff, gains a 5-round reflection engine. |
| `boltrunner` | STORMCHAIN | U | `ON_KILL BUFF_SPD SELF 3` | `ON_KILL GRANT_EXTRA_ACTION SELF 1` | "Runs faster after every name it crosses out" → "Acts again after every name it crosses out". L4 mutation (cap 1→2) now actually consumed in real matches. |
| `shock_runner` | STORMCHAIN | U | `ON_KILL BUFF_SPD ALL_ALLIES 3` | `ON_KILL GRANT_EXTRA_ACTION RANDOM_ALLY 1` | "Tailwind for everyone behind" → "Hands off the next turn to a teammate". Cascade enabler. |
| `arc_lancer` | STORMCHAIN | U | `ON_ATTACK DAMAGE LOWEST_HP_ENEMY 4` | `ON_EXTRA_ACTION_GRANTED DAMAGE LOWEST_HP_ENEMY 5` | "Picks the soft spark" — now fires on the EXTRA action specifically. Pairs with `boltrunner`/`shock_runner` for kill→extra→detonation chains. |
| `voidling` | REVENANT | C | `ON_KILL DEBUFF_ATK ALL_ENEMIES 2` | `ON_LOW_HP SACRIFICE_SELF SELF 0` | "A removed life is a removed lesson" — now removes itself when wounded, fires ON_DEATH on self + ON_ALLY_DEATH cascade on every alive teammate (per §21.3 Q2 contract). Feeds L5 mutation doubling. |
| `coral_augur` | TIDAL | E | `ON_TAKE_DAMAGE ADD_SHIELD SELF 3` (second of 2 triggers) | `ON_HEAL_RECEIVED ADD_SHIELD SELF 3` | When healed (by self-heal, ally heal, LIFESTEAL), gain shield. Pristine ON_ATTACK heal trigger preserved. Engine-side: L3 trickle does NOT fire ON_HEAL_RECEIVED per §22.2 L3 cascade-break, so this trigger only fires from real HEAL ops — clean separation. |

**Trigger-budget audit**: every rewrite preserves the trigger COUNT on its card (uncommons stay at 1, the epic stays at 2). Tier caps from `test_phase4_distribution.py::TIER_TRIGGER_CAP` honored.

### 24.4 — Showcase loadout impact

- **L4 showcase (`showcase_l4_stormchain_tempo`)**: previously flagged "dormant from a pure-catalog match" (no GRANT_EXTRA_ACTION cards existed). Now explicitly demonstrates the L4 cap-raise: boltrunner ON_KILL → GRANT_EXTRA_ACTION self → arc_lancer ON_EXTRA_ACTION_GRANTED → DAMAGE chain, with the second extra-action grant landing under the L4 cap of 2 instead of being rejected at 1. Showcase description updated.
- **L2 showcase (`showcase_l2_bulwark_thorns`)**: `bramble_warden` (now THORNS bearer) and existing engine L2 mutation (worldroot_sentinel grants +2 thorns to all allies) now stack — `bramble_warden` reflects 5 thorns (its own 3 + L2's 2) per ON_DAMAGE_TAKEN.
- **L3 showcase (`showcase_l3_tidal_trickle`)**: `coral_augur`'s ON_HEAL_RECEIVED gives the team a heal-reactive shield engine on top of L3's team-wide trickle.
- **L5 showcase (`showcase_l5_revenant_cascade`)**: `voidling` is NOT in the L5 loadout (which uses 6 specific REVENANT cards), but the SACRIFICE_SELF op is now catalog-callable for player-built REVENANT shells.
- **L1 / L6 showcases**: unaffected (no cards in their loadouts touched).

### 24.5 — Verification surface

`tests/test_phase4h_new_ops_synergies.py` runs real `resolve_match` battles to verify each rewrite end-to-end:
1. Per-card op verification — each rewritten card's new op fires in the combat log when the trigger condition hits.
2. L4 catalog-bound test — STORMCHAIN team with rewritten boltrunner + arc_lancer demonstrates the kill → extra-action → arc_lancer detonation chain.
3. L4 mutation activation in catalog match — adding `tempest_apex` to that team raises the cap, observable in log as `(used 2/2)`.
4. L5 catalog-bound test — REVENANT team with rewritten `voidling` + ON_ALLY_DEATH listeners + `voidking_morr` shows the doubled cascade in real match log.
5. THORNS reflection in BULWARK shell — rewritten `thornserpent`/`bramble_warden` reflect on attackers in catalog match.
6. ON_HEAL_RECEIVED in TIDAL shell — `coral_augur` shielded after `tidewatcher`'s opening team-heal.
7. Showcase-loadout regression — re-verifies all 6 Phase 4g loadouts still resolve cleanly post-rewrite.

### 24.6 — Open items (deferred)

- `ON_DAMAGE_TAKEN` catalog use is currently 1 (`wrought_bear`). 21 cards still use the older `ON_TAKE_DAMAGE` (pre-shield path). Per §21.2 these are semantically distinct — many of those 21 are intentionally pre-shield (e.g. ADD_SHIELD-on-take-damage cards want to fire even when fully-shielded). No mass migration; per-card audits are Phase 5 scope.
- THORNS reflection cap (2 per source-attack, §21.5) and heal cascade cap (4 per source-heal) remain engine-enforced. Phase 5 sim should verify these caps don't cause balance pathologies in real match data.
- `SACRIFICE_SELF` is currently bound to one common (`voidling`). Phase 5 sim may identify additional REVENANT cards where SACRIFICE_SELF makes flavor sense — additional in-place rewrites can land then.

---

*End of Phase 4 (a/b/c/d/e-engine/e-pool/f-counters/f-engine/f-pool/g/h) charter. Phase 5 (balance via simulation) follows.*

