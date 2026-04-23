# DAIMON V1 Canon Audit — Mechanical-Lore Alignment

**Date:** 2026-04-23  
**Scope:** All 200 cards in `daimon/catalog/v1_alpha/` against proposed mapping in `docs/canon_mapping.md`.  
**Method:** For each card, compare element/archetype/rarity + triggers against the mythology of the proposed name. Rated CLEAN / BORDERLINE / MISMATCH / MISSING.

---

## §1 Summary stats

- **Clean matches:** 165 cards — name fits mechanics without hedge
- **Borderline:** 19 cards — plausible but not strong, or awaiting V2 engine work
- **Mismatches:** 13 cards — name contradicts mechanical fingerprint, recommend swap
- **Missing / unclear:** 3 cards — mapping has placeholder, internal conflict, or no entry
- **Total:** 200/200

Rare-tier problem density (§4 of design highlights this as the priority tier): of 28 rares, 5 are mismatches or critical borderlines (`mindroot`, `tidewyrm`, `sea_warden`, `frostfin`, `inferno_lynx`). Down from the earlier audit's flagged 18-of-27 — most of canon_mapping.md's rare-tier work is landing correctly.

---

## §2 MISMATCHES (13)

| card_id | element / archetype / rarity | trigger summary | current proposal (Canon) | why mismatches | replacement candidates | recommended pick |
|---|---|---|---|---|---|---|
| `magma_tyrant` | FIRE / INFERNO / legendary | (vanilla) | **Hephaestus, Forgelord** (OLYMPIAN) | Hephaestus is a SMITH — lore-appropriate effect is forge allies with weapons/shields. L1 rule-change is "every damage applies burn stack" = primordial pyromania, not craftsmanship. Hephaestus-on-INFERNO-legendary is a category error. | Prometheus (fire-theft spreading flame — the mythic arsonist); Typhon (freed from pyrotyrant; primordial volcanic tyrant is perfect); Pyriphlegethon (river-of-fire of the Underworld — Olympian) | **Prometheus, Fire-Thief — matches "all damage you deal also applies burn" (fire itself spreads as gift/plague)** |
| `tidewyrm` | WATER / None / rare | ON_ROUND_START:HEAL(1,ALL_ALLIES) | **Jörmungandr** (AESIR) | Jörmungandr is the WORLD-SERPENT OF RAGNARÖK — mythically the cataclysmic poisoner who kills Thor. Current card is a feeble heal-tick (HEAL 1 AoE per round). Mechanically it should apply POISON AoE or crush-damage. Name is a shipwreck on this card. | Nidhogg (dragon at the roots of Yggdrasil — gnaws the dead; fits sustain-denial profile weakly); Ran (sea-goddess who drowns sailors — steady HEAL-sea-mother is actually a plausible read); Thökk (disguised Loki, refused to weep for Baldr — cold sustain) | **Rán, Drowning-Queen — sea-mother who gathers the drowned; tiny steady heal-tick fits her welcoming-the-sunken lore (keep tidewyrm's rare slot for a REAL Jörmungandr card with POISON)** |
| `mindroot` | VOID / None / rare | ON_BATTLE_START:APPLY_POISON(3,ALL_ENEMIES) ; ON_ATTACK:DAMAGE(3,LOWEST_HP_ENEMY) | **Lethe-Root** (OLYMPIAN) | Lethe is the river of FORGETTING — mechanical fit is APPLY_SILENCE (can't act) or DEBUFF to memory, not POISON (active decay). The card applies poison = rot/decay, not amnesia. | Hydra of Lerna (already used on molten_drake as Lernean Pyrohydra — but poison is classic hydra); Styx-Bloom (river-of-hate bloom — poison-rage); Apep-Root (Egyptian chaos-snake as root — wrong Canon though) | **Mandragora of Kokytos — underworld mandrake whose scream poisons (Olympian, poison-appropriate, death-tied)** |
| `sea_warden` | WATER / TIDAL / uncommon | ON_ATTACK:LIFESTEAL(4,HIGHEST_HP_ENEMY) | **Triton, Horn-Bearer** (OLYMPIAN) | Triton is Poseidons HERALD — should announce/buff (horn-blast) not vampirically drain. Lifesteal is predator-profile, not messenger. | Glaukos (fisherman-turned-sea-god, eats prey); Ceto (sea-predator mother); Karkinos (giant sea-crab attacker) | **Karkinos, Deep-Crab — predatory sea-grinder matches lifesteal-on-highest-hp perfectly** |
| `frostfin` | WATER / None / uncommon | ON_ATTACK:DEBUFF_ATK(2,RANDOM_ENEMY) | **Delphin** (OLYMPIAN) | Delphin is a sacred dolphin — lore profile is swift, helpful, speed-support. Debuff-atk (weakening-bite) reads predatory, not delphic. | Echidna-spawn sea-predator; Lamia-of-the-deep; Ichthyes (zodiac fish — pure pair, neutral) | **A Telchine (malevolent sea-smith who curses weapons — debuffing attack is literally their myth)** |
| `shadebishop` | VOID / REVENANT / uncommon | ON_ALLY_DEATH:BUFF_ATK(3,SELF) | **Osiris-Priest** (NETJER) | Osiris is the resurrected-king; his priests preserve the dead, they don't eat ally-deaths for self-power. This is a Set or Ammit cultist pattern. | Priest of Set (power-from-sacrifice, canonical); Ammit-Cultist (soul-eater doctrine); Anubis-Embalmer (tending the dead — passive) | **Priest of Set — drains ally-death into self-power exactly matches the cultic vampirism** |
| `ghostfin` | VOID / REVENANT / common | ON_ATTACK:APPLY_POISON(2,RANDOM_ENEMY) | **Ba-Fin** (NETJER) | Ba is the soul/personality-fragment; Ba-Fin as poison-applier is category mismatch (souls don't poison — they cling, haunt, or drain). | Apep-Fry (chaos-snake juvenile — poison canonical); Hapi-Poison-Fry (Nile-plague fish); Oxyrhynchus-Fish (sacred Nile-fish that ATE Osiris's member — grotesque-devouring) | **Oxyrhynchus — sacred poison-fish of the Nile, eats corpse-pieces; poison-on-attack is a perfect match** |
| `galekit` | VOLT / STORMCHAIN / common | ON_KILL:DAMAGE(3,LOWEST_HP_ENEMY) | **Nephthys Wind-Kit** (NETJER) | Nephthys is the mourning/night goddess — her profile is lament and soul-guide, not volt-aggro finishing-strike. Kill-then-strike-weakest is assassin-profile. | Ba-Winged Sandstorm (Set-aspect storm); Seshat Storm-Scribe (scribe-goddess, obscure); Shezmu (executioner-god — on-kill blood-ritual fits) | **Shezmu-Whelp — Shezmu is the blood-drinking executioner-god; on-kill cascade is literally his ritual** |
| `abyss_minnow` | WATER / TIDAL / common | ON_ATTACK:LIFESTEAL(2,RANDOM_ENEMY) | **Hapi-Fry** (NETJER) | Hapi is the Nile bounty-god — provides flood-abundance, not parasitic drain. Lifesteal on a Hapi-fry is counter-mythic (takes instead of gives). | Nile-Leech (generic Egyptian parasite); Devourer-Fry of Ammit (Ammit already on thornpup; avoid overuse); Sobek-Hatchling (crocodile young — predatory is canonical) | **Sobek-Hatchling — young crocodile-god, lifesteal-predation is literally crocodile behavior** |
| `boulder_mole` | NATURE / BULWARK / common | ON_TAKE_DAMAGE:BUFF_DEF(2,SELF) | **Redcap Burrower** (APOCRYPHA) | Redcap is a MURDEROUS Scottish/Borderlands goblin who dips his cap in victims' blood — mechanical profile is predator-aggro, NOT damage-taunting defender. Wrong folk-creature for this card. | Grimnir-Gnome (folk-mountain-defender); Hodag (American-folk lumpy-bristled-beast, defensive); Dobhar-Chú Pup (Irish water-hound, def-leaning) | **Hodag — American folk-beast, lumpy-bristled, stand-your-ground fits damage-to-DEF perfectly** |
| `mistchimera_adept` | WATER / SYNCRETIC / uncommon | ON_BATTLE_START:HEAL(3,ALL_ALLIES) | **Tlaloc's Cloud** (TEOTL) | CRITICAL: canon_mapping §8 puts mistchimera_adept in TEOTL VOLT as "Tlaloc's Cloud" but card JSON is WATER/SYNCRETIC. Either rename (keep WATER-TEOTL name like "Tlaloc's Mist Adept") or the element is wrong. Evo chain pairs with mistchimera (WATER) so element should stay WATER. | Tlaloc's Storm-Mist (WATER adept); Chalchiuhtlicue-Adept (lake-goddess adept); Ehecatl-Mist (wind-water hybrid) | **Tlaloc's Storm-Mist — keep WATER element, evolve from mistchimera; §8 VOLT allocation is an editing error** |
| `mossbear` | NATURE / BULWARK / uncommon | ON_TAKE_DAMAGE:ADD_SHIELD(3,SELF) | **Arkouda of Arcadia** (OLYMPIAN) | Mapping §4 table lists "Arkouda of Arcadia" (Olympian bear of Artemis) but §10 problem-spots REASSIGNS to Kami "Kodama Elder" to keep mossling → mossbear chain single-Canon. Name in active table is stale; needs reconcile. | Kodama Elder (Kami — keeps evolution chain intact); Arkouda of Arcadia (Olympian — breaks chain) | **Kodama Elder (Kami) — §10 fix is correct; update §4 table** |
| `loremaster_ape` | NORMAL / None / rare | ON_BATTLE_START:BUFF_ATK(3,ALL_ALLIES) ; ON_TURN_END:BUFF_SPD(3,RANDOM_ALLY) | **Sanzaru** (APOCRYPHA) | Conflicting state: §9 Apocrypha table says "Hanuman-Wanderer" (deprecated per §11), §11 reassigns to "Sanzaru" (Kami three-monkeys). Mapping document is internally inconsistent — needs reconcile. Mechanical profile (team ATK + SPD rally) fits Sanzaru as a unified-three-monkeys harmony-buff OK. | Sanzaru (Kami) — per §11 resolution; Hanuman-Wanderer (Apocrypha — deprecated); Anansi-Wanderer (West-African trickster; currently no Canon fits) | **Sanzaru — confirm §11 resolution and remove Hanuman entry from §9 table; move Canon to KAMI** |

---

## §3 BORDERLINE (19)

Plausible but soft fits, or mechanics await V2 engine ops. Listed with keep/swap recommendation.

| card_id | element / archetype / rarity | trigger summary | current proposal (Canon) | why borderline | keep or swap |
|---|---|---|---|---|---|
| `concord_phoenix` | NORMAL / None / epic | ON_BATTLE_START:HEAL(5,ALL_ALLIES) ; ON_ALLY_DEATH:BUFF_ATK(4,ALL_ALLIES) | **The First Phoenix** (APOCRYPHA) | First Phoenix — universal revive archetype — but card has ON_BATTLE_START:HEAL(5) + ON_ALLY_DEATH:BUFF_ATK(4); the ON_ALLY_DEATH reads more as mourning-berserker than phoenix. V2 REVIVE_AT_HP deferral flagged in §11. | **KEEP** — Keep The First Phoenix (locked in §11; revive retrofit planned V2) |
| `solar_phoenix` | FIRE / INFERNO / epic | ON_OPENING_ATTACK:DAMAGE(4,ALL_ENEMIES) ; ON_DEATH:HEAL(6,ALL_ALLIES) | **Bennu, Sun-Bird** (NETJER) | Bennu is dawn-phoenix (rebirth at sunrise). Card has ON_OPENING_ATTACK:DAMAGE AoE + ON_DEATH:HEAL — no self-revive op. Matches Ra-at-dawn + heal-legacy reasonably; same V2 REVIVE deferral applies as ashen_phoenix. | **KEEP** — Keep Bennu, Sun-Bird (opening AoE = sun at horizon, heal-on-death = leaves legacy) |
| `aegis_lion` | NORMAL / None / rare | ON_BATTLE_START:ADD_SHIELD(4,ALL_ALLIES) ; ON_TAKE_DAMAGE:BUFF_DEF(3,SELF) | **Nemean Wanderer** (APOCRYPHA) | Nemean Lion is canonically Olympian (Heracles). Labelling it APOCRYPHA "Wanderer" is a thin folk-recontextualization; defenders of Canon placement could argue it belongs under Olympian. | **KEEP** — cleanly dissociates from Olympian Nemean to preserve Apocrypha identity |
| `ashen_phoenix` | FIRE / None / rare | ON_DEATH:HEAL(6,ALL_ALLIES) ; ON_BATTLE_START:BUFF_ATK(2,ALL_ALLIES) | **Phoenix of Heliopolis** (OLYMPIAN) | Phoenix lore is self-revive but card is heal-allies-on-death (no REVIVE op yet); V2 work item flagged in mapping §11 to add REVIVE_AT_HP. Phoenix name is aspirational; mechanics currently read as sacrificial-gift not resurrection. | **KEEP** — Keep Phoenix of Heliopolis (deferred V2 revive op makes this canonical) |
| `inferno_lynx` | FIRE / None / rare | ON_BATTLE_START:BUFF_SPD(2,SELF) ; ON_ATTACK:DAMAGE(4,RANDOM_ENEMY) | **Sphinx of Thebes** (OLYMPIAN) | Sphinx of Thebes is a RIDDLER (card should apply SILENCE or DEBUFF). Current card is BUFF_SPD-self + DAMAGE-random = pure aggro. Swift-stalker framing fits loosely but the riddling identity is lost. | **SWAP** — Chimaera — FIRE rare legendary monster, exactly the run-down-and-burn profile |
| `riftwraith` | VOID / None / rare | ON_DEATH:DAMAGE(4,ALL_ENEMIES) ; ON_BATTLE_START:DEBUFF_DEF(1,ALL_ENEMIES) | **Charon, Ferry-Wraith** (OLYMPIAN) | Charon is a ferryman — passive collector. Death-burst-damage reads more vengeful wraith than stoic boatman. Plausible (final-toll claim) but not strong. | **SWAP** — Keres — battlefield death-spirits, ON_DEATH AoE is literally their mythic profile |
| `riptide_wyrm` | WATER / None / rare | ON_BATTLE_START:ADD_SHIELD(3,ALL_ALLIES) ; ON_ATTACK:HEAL(3,RANDOM_ALLY) | **Ophion** (OLYMPIAN) | Ophion is obscure pre-Olympian world-serpent; shield+heal profile fits but not distinctively. Better: a named Nereid or sea-nymph with protector-healer lore. | **SWAP** — Thetis, Silver-Footed — protector-mother-nymph matches shield+heal twin-trigger exactly |
| `flame_chimera_adept` | FIRE / SYNCRETIC / uncommon | ON_BATTLE_START:BUFF_ATK(3,ALL_ALLIES) | **Xochitonal Adept** (TEOTL) | Xochitonal Adept is SYNCRETIC but the ON_BATTLE_START:BUFF_ATK(3,ALL_ALLIES) doesn't gate on team-diversity. Lore-name is fine; mechanical-SYNCRETIC-tag is nominal. | **KEEP** — archetype is metadata (per §2.0 soft-cluster), name is fine |
| `glimmerowl` | VOLT / None / uncommon | ON_BATTLE_START:BUFF_SPD(1,SELF) | **Glaukos-Owl of Athena** (OLYMPIAN) | Athena's owl is wisdom — self-speed buff is weak and doesn't hit the wisdom-gives-insight beat. Would prefer an ally/team effect or a DEBUFF-as-insight effect. | **KEEP** — cheap, common-adjacent, name is fine for 1-trigger uncommon |
| `spark_serpent` | VOLT / STORMCHAIN / uncommon | ON_ATTACK:BUFF_SPD(3,SELF) | **Nidhogg's Coil** (AESIR) | Nidhogg is the corpse-eater DRAGON at Yggdrasil's roots — a VOID/NATURE concept, not VOLT. "Nidhogg's Coil" as a lightning-serpent is a stretch; lightning-snake lore is thin in Norse mythos. | **SWAP** — Lyngorm — Norse folk lightning-serpent, fits VOLT STORMCHAIN properly |
| `verdant_chimera` | NATURE / SYNCRETIC / uncommon | ON_BATTLE_START:BUFF_DEF(3,ALL_ALLIES) | **Mixcoatl Adept** (TEOTL) | Same Mixcoatl issue as verdant_chimerlet at uncommon tier. | **SWAP** — Mayahuel Adept — matches the pair flip |
| `wraith_prince` | VOID / REVENANT / uncommon | ON_DEATH:DAMAGE(3,ALL_ENEMIES) | **Ushabti Overseer** (NETJER) | Ushabti Overseer: Ushabti are tomb-workers who labor in the afterlife. Death-burst AoE doesn't match quiet-servitor. Better would be on-ally-death-summon an ushabti (V2 op). | **SWAP** — Wepwawet Herald — "opener-of-ways" Anubis-aspect; death-burst = clearing the path |
| `boltbat` | VOLT / STORMCHAIN / common | ON_ATTACK:DAMAGE(3,RANDOM_ENEMY) | **Yamabiko-Bat** (KAMI) | Yamabiko is an ECHO-SPIRIT; random single-target damage doesn't match "echo amplifies." Would prefer a DAMAGE-SPREAD or CHAIN effect. | **SWAP** — Raiju-Hatchling — simpler thunder-pup fits single-target random bolt |
| `emberpup` | FIRE / None / common | (vanilla) | **Coyote-Pup of Huehuecoyotl** (TEOTL) | Huehuecoyotl is the TRICKSTER-coyote god; his pup should have some trick/debuff, not be a vanilla 7-ATK beater. Name is aspirational but mechanics don't reflect trickster identity. | **SWAP** — Ocelocoyotl Pup — jaguar-coyote hybrid, vanilla-aggro fits; save trickster for a trigger-bearing card |
| `mistling` | WATER / None / common | (vanilla) | **Kappa** (KAMI) | Kappa is a major folk yōkai with signature tricks (cucumber bribe, head-bowl water). Vanilla no-trigger is a disservice — kappa should have a stealable/tricky effect. | **SWAP** — Ayakashi-Drift — generic mist-yōkai matches vanilla-beater tier better; save Kappa for a trigger-bearing card |
| `mosscat` | NATURE / BULWARK / common | ON_BATTLE_START:HEAL(2,ALL_ALLIES) | **Bake-Neko Kit** (KAMI) | Bakeneko is a SHAPESHIFTING trickster-cat; opening-heal-AoE reads as benevolent-shrine-cat which is the Nekomata or Maneki-Neko, not bakeneko (which is ominous/haunting). | **SWAP** — Maneki-Neko Kit — fortune-cat whose opening-wave blesses the team; perfect mechanical fit |
| `soot_finch` | FIRE / INFERNO / common | ON_BATTLE_START:BUFF_ATK(2,SELF) | **Cintli-Finch** (TEOTL) | Cintli is MAIZE (Centeotl); a maize-finch self-buffing ATK is counter-mythic (maize = growth/nourish, not battle-swagger). Fits better as BUFF_DEF or HEAL. | **SWAP** — Tototl-Warrior — generic Nahuatl war-bird; self-buff ATK is literal warrior-cry |
| `spectral_kit` | VOID / REVENANT / common | ON_TURN_END:APPLY_POISON(2,RANDOM_ENEMY) | **Mut-Kit** (NETJER) | Mut is vulture-mother goddess; her kit as passive poison-applier is marginal (vultures don't poison). Shift to a canonical poison-source. | **SWAP** — Serket-Kit — scorpion-goddess's young, poison-tick is textbook |
| `verdant_chimerlet` | NATURE / SYNCRETIC / common | ON_BATTLE_START:BUFF_DEF(2,ALL_ALLIES) | **Mixcoatl Whelp** (TEOTL) | Mixcoatl is HUNTER/cloud-serpent; ally-DEF-buff reads as defender, not hunter. Reallocate effect or rename. | **SWAP** — Mayahuel Whelp — maguey/agave-goddess young; ally-DEF is leaf-shield nurturing, clean fit |

---

## §4 CLEAN MATCHES (165)

Locked. No action needed.

### OLYMPIAN (28)
`arc_serpent`, `bramblegoat`, `bulwark_patriarch`, `coral_augur`, `coral_priest`, `crypt_wraith`, `echo_lich`, `glacier_kraken`, `haunt_hare`, `krakenling`, `leviathan_prime`, `maelstrom_serpent`, `molten_drake`, `nullhound`, `petalwing`, `plasma_djinn`, `prism_strider`, `prismbolt`, `pyroshrike`, `pyrotyrant`, `storm_celestial`, `tempest_eagle`, `tide_chanter`, `tide_empress`, `tidewatcher`, `voidking_morr`, `whisperling`, `worldroot_sentinel`

### AESIR (35)
`abysseel`, `barkguard`, `barkpup`, `bramble_warden`, `brambleling`, `brimling`, `cinderhound`, `crypt_seer`, `dewfin`, `dirge_lich`, `dread_kit`, `dread_warden`, `flarefly`, `flarelord`, `forest_cub`, `forest_keeper`, `forest_warden`, `geodeling`, `iron_boar`, `ironseed`, `mistray`, `moss_titan`, `saltsprite`, `shadow_warden`, `shadowpup`, `spring_otter`, `sproutkin`, `stone_titan`, `stormhare`, `tempest_apex`, `thunderfly`, `tide_imp`, `voltcat_apex`, `voltsprite`, `worldroot_colossus`

### NETJER (24)
`abyssbreaker`, `arc_kit`, `arc_lancer`, `ash_strider`, `ashpup`, `bulwarthog`, `cinder_serpent`, `coralwhelp`, `cryptmoth`, `dread_imp`, `ghoul_imp`, `hollowpup`, `lava_skink`, `magmite`, `miasma_imp`, `plasma_hound`, `rootsnake`, `shadepup`, `silentmoth`, `spectral_owl`, `sproutling`, `thornpup`, `tidefry`, `wraithling`

### KAMI (37)
`arc_predator`, `arc_pup`, `arcweasel`, `boltkit`, `boltrunner`, `brineling`, `brineprince`, `cindermote`, `coalmunch`, `dirgebat`, `duskmoth`, `ember_raptor`, `emberhawk`, `embershrew`, `flashfox`, `flickerimp`, `mosshound`, `mossling`, `plasma_kit`, `root_warden`, `seapup`, `shade_prismatic`, `shadeling`, `shadeprism`, `shellfin`, `shellguard`, `shock_runner`, `shockling`, `spark_imp`, `stonepup`, `stormpup`, `thornling`, `thornserpent`, `tide_synth`, `tidemerger`, `zapdrake`, `zapling`

### TEOTL (27)
`abyss_warden`, `ashling`, `blazewolf`, `charge_chick`, `coalbreaker`, `coalwhelp`, `dashmouse`, `flame_chimerlet`, `ignis_kit`, `magma_warden`, `magmaling`, `mistchimera`, `nullsprite`, `prism_chimera`, `prism_grove`, `prism_seedling`, `rainbow_drake`, `spectral_charge`, `spectral_volt`, `sunscale_drake`, `sunscale_serpent`, `surfling`, `tidepup`, `void_chimera`, `void_chimerlet`, `voidcrawler`, `world_eater`

### APOCRYPHA (14)
`brass_mole`, `cloth_sprite`, `coralcub`, `grove_pup`, `mendicant_sphinx`, `mossback_ox`, `page_slime`, `pebbler`, `quill_cat`, `rune_owl`, `runic_whelp`, `sparkling`, `stoneward`, `wrought_bear`

---

## §5 MISSING / UNCLEAR (3)

Cards where `canon_mapping.md` has no entry, an unresolved placeholder, or internal contradiction. Each needs a name + Canon assigned before the data rewrite.

| card_id | element / archetype / rarity | trigger summary | status | proposed name + Canon |
|---|---|---|---|---|
| `blazefiend` | FIRE / INFERNO / uncommon | ON_ATTACK:APPLY_BURN_STACK(3,LOWEST_HP_ENEMY) | `canon_mapping.md` §9 says "moved to Teotl" but no name assigned | **Xiuhcoatl Adept** (TEOTL) — fire-serpent adept; burns the weakest target = venom-flame directed strike |
| `galelord` | VOLT / STORMCHAIN / uncommon | ON_BATTLE_START:BUFF_SPD(3,ALL_ALLIES) | Not in `canon_mapping.md` at all | **Fūjin Herald** (KAMI) — wind-god envoy; team SPD buff mirrors `stormpup`→`galelord` as Fūjin evolution-chain (natural Kami slot) |
| `voidling` | VOID / REVENANT / common | ON_LOW_HP:SACRIFICE_SELF(0,SELF) | Not in `canon_mapping.md` at all | **Draugr Dreg** (AESIR) — lesser undead who self-destructs at low HP = warrior's last-lunge; fits Aesir's §11 VOID roster (currently 6, room for 7) |

Two additional reconciliation bugs surfaced during audit (documented in §2 Mismatches, restated here for file-cleanup tracking):

- **`mossbear`**: §4 table lists "Arkouda of Arcadia" (Olympian), §10 problem-spots reassigns to Kami "Kodama Elder". §4 is stale — update to Kami.
- **`loremaster_ape`**: §9 Apocrypha table lists "Hanuman-Wanderer" (deprecated), §11 resolution #3 reassigns to "Sanzaru" (Kami). §9 is stale — remove row, confirm §11.
- **`mistchimera_adept`**: §8 TEOTL VOLT lists "Tlaloc's Cloud" but card is WATER/SYNCRETIC. Either §8 Canon section is in the wrong element-subsection, or the name must pivot to a WATER-appropriate "Tlaloc's Storm-Mist".

---

## §6 Canon-distribution health

Applying all §11 resolutions and the reallocations from §2 of the mapping (cinderhound→Aesir, geodeling→Aesir, flashfox→Kami, loremaster_ape→Kami Sanzaru):

| Canon | Actual | Target | Drift |
|---|---:|---:|---:|
| OLYMPIAN | 38 | 38 | 0 |
| AESIR | 37 | 37 | 0 |
| NETJER | 31 | 32 | −1 |
| KAMI | 40 | 37 | +3 |
| TEOTL | 34 | 32 | +2 |
| APOCRYPHA | 18 | 24 | −6 |
| **Missing** | 2 | — | — (galelord, voidling unassigned) |
| **Total** | 200 | 200 | |

**Flagged drift:**

- **APOCRYPHA under-served by 6.** The §9 table projected 22-29 cards but resolutions (moving Hanuman-Wanderer→Kami Sanzaru, reallocating cinderhound/geodeling/flashfox to native Canons) have drained it to 18. Options:
    1. Pull 6 cards back from over-represented Canons (KAMI +3, TEOTL +2) into Apocrypha folk-equivalents.
    2. Accept the new target of 18 and revise §2 of `canon_mapping.md` to reflect actual ship counts.
    3. If we assign both `voidling` and `galelord` to Apocrypha (not recommended — they fit specific Canons better), Apocrypha rises to 20, still −4.
    - **Recommended: option (2)** — accept 18. The original 24 target was a by-product of the NORMAL-floor calculation (15 NORMAL + 9 non-NORMAL); if §11 resolutions net 3 fewer non-NORMAL folk-slots, target should be honest.

- **KAMI over by 3.** Driven by §11 Sanzaru reassignment (+1) and §2 flashfox-reallocation (+1) plus §10 mossbear-fix (+1, reclaimed from Olympian). If Apocrypha target lowered to 18, KAMI +3 is fine — total still 200.

- **TEOTL over by 2.** Driven by §11 Tlaloc → Teotl STORMCHAIN VOLT addition. The two over-count cards are `mistchimera_adept` (Tlaloc's Cloud) and `charge_chick` (Hummingbird of Huitzilopochtli) — both defensible, no pressure to reallocate. Accept +2.

- **NETJER under by 1.** Marginal; fine.

**Net read:** if we accept revised Apocrypha target of 18 and assign blazefiend/galelord/voidling per §5, distribution is solid.

---

## §7 Saturation warnings

Single deities appearing on 3+ cards (flagged for diversity).

| Deity | Count | Cards | Assessment |
|---|---:|---|---|
| **Set** | 4 | `arc_kit` (Whelp), `arc_lancer` (Initiate), `plasma_hound` (Hound), `dread_imp` (Imp) | **Heavy.** "The Church of Set" risk. Set is NETJER's chaos-archetype, but 4 of 32 Netjer cards = 12% is concentrated. Recommend: rename `dread_imp` (the only non-STORMCHAIN of the four) to a non-Set chaos-entity (e.g. **Apep-Imp** for cinder-chaos, or **Shezmu-Imp** for blood-executioner). Keeps Set's VOLT presence while diversifying VOID. |
| **Raiju** | 4 | `zapling` (Whelp), `zapdrake` (Initiate), `arc_pup` (Cub), `arc_predator` (Raijū-Taishō) | **Acceptable.** Raiju is the KAMI VOLT signature (thunder-beast family). 4-card family is a designed Pokémon-style evolution-chain. No action. |
| **Raijin** | 4 | `boltkit` (Kit), `boltrunner` (Herald), `shock_runner` (Drumroller), `plasma_kit` (Acolyte) | **Acceptable.** Raijin is KAMI VOLT's headline god; thunder-god retinue cards are a designed cluster. No action. |
| **Skogsrå** | 3 | `barkpup` (Pup), `barkguard` (Warden), `forest_keeper` (Elder) | **Acceptable.** Designed evolution-chain in AESIR NATURE. No action. |
| **Garm** | 3 | `shadowpup` (Garm's Pup), `shadow_warden` (Garm, Warden), `cinderhound` (Garmr-Whelp) | **Marginal.** 2 are the pup/warden line (VOID); `cinderhound` adds a third (FIRE Garmr-Whelp). Fine — 3 Garm-variants spanning two elements is a canonical Aesir hellhound spread, not saturation. |
| **Kappa** | 3 | `mistling` (Kappa), `shellfin` (Scholar), `shellguard` (Warden) | **Marginal.** Kappa is KAMI WATER's major folk yōkai; 3 cards is barely a retinue. If `mistling` swaps to Ayakashi-Drift (§3 recommendation), Kappa drops to 2 and is clean. |
| **Quetzalcóatl** | 3 | `rainbow_drake` (Plumed One), `sunscale_drake` (Hatchling), `sunscale_serpent` (Adept) | **Acceptable.** Designed SYNCRETIC evolution-chain; Quetzalcóatl is TEOTL's apex benevolent-creator. 3 cards is just the family. No action. |

**Highest-priority saturation fix:** `dread_imp` → non-Set name (breaks the 4-Set saturation).

---

## Appendix: rare-tier snapshot

Per the pivot brief "rares matter most (showcase/art visibility)." Status of all 28 rares:

- `abyss_warden` — **CLEAN** — VOID/None → **Chalchiuhtlicue, Jade-Skirt** (TEOTL)
- `aegis_lion` — **BORDER** — NORMAL/None → **Nemean Wanderer** (APOCRYPHA)
- `arc_serpent` — **CLEAN** — VOLT/None → **Python of Delphi** (OLYMPIAN)
- `ashen_phoenix` — **BORDER** — FIRE/None → **Phoenix of Heliopolis** (OLYMPIAN)
- `blazewolf` — **CLEAN** — FIRE/None → **Xolotl, Dog-Star** (TEOTL)
- `bulwarthog` — **CLEAN** — NATURE/None → **Khepri, Scarab-Warden** (NETJER)
- `echo_lich` — **CLEAN** — VOID/None → **Erebus** (OLYMPIAN)
- `forest_warden` — **CLEAN** — NATURE/None → **Huldra, Forest-Hidden** (AESIR)
- `glacier_kraken` — **CLEAN** — WATER/None → **Kraken of Aegaeon** (OLYMPIAN)
- `haunt_hare` — **CLEAN** — VOID/None → **Lampas of Hekate** (OLYMPIAN)
- `inferno_lynx` — **BORDER** — FIRE/None → **Sphinx of Thebes** (OLYMPIAN)
- `leviathan_prime` — **CLEAN** — WATER/None → **Keto Primeval** (OLYMPIAN)
- `loremaster_ape` — **MISMATCH** — NORMAL/None → **Sanzaru** (APOCRYPHA)
- `maelstrom_serpent` — **CLEAN** — WATER/None → **Charybdis** (OLYMPIAN)
- `mindroot` — **MISMATCH** — VOID/None → **Lethe-Root** (OLYMPIAN)
- `molten_drake` — **CLEAN** — FIRE/None → **Lernean Pyrohydra** (OLYMPIAN)
- `moss_titan` — **CLEAN** — NATURE/None → **Jötunn of Jotunheim** (AESIR)
- `nullhound` — **CLEAN** — VOID/None → **Cerberus** (OLYMPIAN)
- `plasma_djinn` — **CLEAN** — VOLT/None → **Astrape** (OLYMPIAN)
- `pyrotyrant` — **CLEAN** — FIRE/None → **Typhon** (OLYMPIAN)
- `riftwraith` — **BORDER** — VOID/None → **Charon, Ferry-Wraith** (OLYMPIAN)
- `riptide_wyrm` — **BORDER** — WATER/None → **Ophion** (OLYMPIAN)
- `storm_celestial` — **CLEAN** — VOLT/None → **Zeus, Sky-Lord** (OLYMPIAN)
- `stormhare` — **CLEAN** — VOLT/None → **Vindhare** (AESIR)
- `tempest_eagle` — **CLEAN** — VOLT/None → **Aetos Dios** (OLYMPIAN)
- `tidewyrm` — **MISMATCH** — WATER/None → **Jörmungandr** (AESIR)
- `voltcat_apex` — **CLEAN** — VOLT/None → **Valravn** (AESIR)
- `worldroot_colossus` — **CLEAN** — NATURE/None → **Yggdrasil-Root** (AESIR)
