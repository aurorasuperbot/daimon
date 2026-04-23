# DAIMON Canon Mapping (v1_alpha → mythology)

**Status:** SHIPPED 2026-04-23 — 200 catalog JSONs rewritten on `monster-pivot`. Full 1315-test suite green.
**Branch:** monster-pivot
**Date last updated:** 2026-04-23

This is the authoritative editorial record of the v1_alpha mythology pivot:
which old `card_id` became which mythological figure, in which Canon, with
which lore-anchored display name and flavor. **Engine identifiers
(`card_id`, `species`, `element`, `archetype`, stats, triggers) are unchanged
by the pivot** — only display fields (`name`, `flavor`, `canon`) and move
names were rewritten. See `tools/canon_rewrite/mapping.py` for the
machine-readable source of truth and `tools/canon_rewrite/apply.py` for the
idempotent rewrite script.

For the upstream audit pass that drove the final name/canon assignments
(13 mechanical-lore mismatches resolved, 13 borderlines escalated to
swaps, 3 missing names filled), see `docs/canon_audit.md`.

## 1. Model recap — three orthogonal axes

Every card carries three independent tags:

| Axis | Controls | Values |
|---|---|---|
| **ELEMENT** | Type chart (mechanical) | FIRE · WATER · NATURE · VOLT · VOID · NORMAL |
| **ARCHETYPE** | Playstyle cluster (mechanical) | INFERNO · BULWARK · TIDAL · STORMCHAIN · REVENANT · SYNCRETIC · null |
| **CANON** | Lore bucket (pure flavor) | OLYMPIAN · AESIR · NETJER · KAMI · TEOTL · APOCRYPHA |

- ELEMENT and ARCHETYPE are **unchanged** by the pivot except for the
  `FLUX` → `SYNCRETIC` rename (engine-side `engine/types.py::ARCHETYPE_IDS`,
  catalog-side via `tools/canon_rewrite/apply.py`).
- CANON is **new**, lore-only, does not gate any engine op.
- Every Canon spans every element. Canons have *signatures* (Olympian leans
  WATER, Aesir leans NATURE, Kami leans VOLT, Netjer leans VOID, Teotl leans
  FIRE) but no Canon is element-locked.
- SYNCRETIC cards are drawn from all 5 mythology Canons — there is no
  "Syncretic roster." The thunder-bearer archetype lives as Zeus-Olympian,
  Thor-Aesir, Tlaloc-Teotl, Raijin-Kami, Set-Netjer.

## 2. Final shipped distribution (200 cards)

| Element | Olymp. | Aesir | Netjer | Kami | Teotl | Apoc. | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| FIRE | 7 | 4 | 5 | 6 | 15 | 0 | 37 |
| WATER | 12 | 7 | 4 | 8 | 4 | 1 | 36 |
| NATURE | 4 | 14 | 4 | 8 | 5 | 1 | 36 |
| VOLT | 7 | 6 | 4 | 15 | 4 | 1 | 37 |
| VOID | 8 | 7 | 13 | 5 | 6 | 0 | 39 |
| NORMAL | 0 | 0 | 0 | 1 | 0 | 14 | 15 |
| **Total** | **38** | **38** | **30** | **43** | **34** | **17** | **200** |

The pre-pivot draft target was OLYMPIAN 38 / AESIR 35 / NETJER 31 / KAMI 36 /
TEOTL 32 / APOCRYPHA 29. Final shipped numbers diverge per the audit pass
(`docs/canon_audit.md`):

- **APOCRYPHA shrank from 29 → 17.** Audit reallocated 12 borderline cards
  to native Canons whose mythology better matched their existing mechanics.
  Examples: `tidewyrm` (was Apocrypha) → AESIR Rán-Drowning-Queen; `mindroot`
  (was Apocrypha) → OLYMPIAN Mandragora of Kokytos. Apocrypha's floor is the
  14 NORMAL-element cards (archetype-null splash design invariant per
  `docs/card_design_v1.md` §A6) plus 3 NATURE/WATER/VOLT folkloric overflow.
- **KAMI grew to 43** (over draft 36) by absorbing wind-spirit, fox, and
  yōkai-water cards that had been parked in Apocrypha.
- **TEOTL grew to 34** by absorbing the full Quetzalcóatl evolution line
  + Tlāhuiztli warrior chain instead of scattering them across SYNCRETIC.
- The single KAMI · NORMAL card (`loremaster_ape` → Sanzaru) is the sole
  exception to "NORMAL = Apocrypha" — the see-no/hear-no/speak-no monkey
  trio is so distinctively Japanese that the Canon override beats the
  archetype-null invariant. Documented call.

**Signature element per Canon:**

| Canon | Signature element | Why |
|---|---|---|
| OLYMPIAN | WATER (12) | Poseidon's sea, Amphitrite, Charybdis, Scylla, Kraken |
| AESIR | NATURE (14) | Yggdrasil, Skogsrå, Jötnar, the great northern forest |
| NETJER | VOID (13) | Duat / Field of Reeds; Anubis, Osiris, Set, Apep |
| KAMI | VOLT (15) | Raijin's drum-line; Raiju, Kamaitachi, Fūjin |
| TEOTL | FIRE (15) | Solar cult: Huitzilopochtli, Xiuhcoatl, Xipe Totec, Quetzalcóatl |
| APOCRYPHA | NORMAL (14) | The archetype-null splash slot, by design |

## 3. Engine-stable identifiers, mutable display fields

The pivot follows the Magic-style "oracle id is forever, oracle text changes
per printing" pattern. Concretely:

| Field | Status | Why |
|---|---|---|
| `card_id` | **stable** | Engine codename. Referenced in 19+ test files, NPC loadouts, mining ledger, gacha pulls. Renaming breaks the world. |
| `filename` | **stable** | `card_id + ".json"`. Same reasoning. |
| `species` | **stable** | Engine identifier used by `MAX_SAME_SPECIES` loadout cap (see `engine/loadout.py`) and NPC loadout resolution. Currently `species == card_id` for all cards except the skogsrå evolution pair. |
| `element` | **stable** | Type chart axis. |
| `atk` / `def` / `hp` / `spd` | **stable** | Engine stats. |
| `triggers` / `rule_change` | **stable** | Engine ops. |
| `rarity` / `archetype` | **stable** | Drop-pool + playstyle cluster. (`FLUX` → `SYNCRETIC` rename was a one-shot engine vocab migration carried by `apply.py`.) |
| `art` | **stable** | Path to art asset. |
| **`name`** | **mutable** | Mythological display name. Rewrite source: `mapping.py::MAPPING[card_id]["name"]`. |
| **`flavor`** | **mutable** | Lore text. Same source. |
| **`canon`** | **new** | Lore-bucket tag, queryable but does not gate any engine op. |
| **moves[].name** | **mutable** | Display strings on triggered abilities. Engine resolves `triggers[]` independently of move names. |

Lookup by display name (e.g. `dm_catalog_card --by-name "Tezcatlipoca"`)
builds the `name → card_id` index off the `name` field at MCP-server
startup; no schema change needed.

---

## 4. OLYMPIAN Canon (Greek/Roman merged) — 38 cards

**Voice:** epic poetry, marble-clean, heroic-tragic. Proper names from Homer/Hesiod.  
**Visual:** bronze, laurel, aegean blue, sunlit marble.  
**Signature figures:** Zeus, Poseidon, Hades, Prometheus, Athena, Hephaestus, Heracles, Amphitrite, Hydra, Kraken, Cerberus.

### OLYMPIAN · FIRE (7)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `magma_tyrant` | legendary | INFERNO | **Prometheus, Fire-Thief** | He stole flame from the sky; the punishment is that flame still answers him. |
| `solar_phoenix` | epic | INFERNO | **Helios, Sun-Charioteer** | He drives the chariot from horizon to horizon; the dive at dawn, the bleeding light at dusk. |
| `ashen_phoenix` | rare | — | **Prometheus-Unbound** | The chains broke at last; the eagle still circles, but the gift goes to others now. |
| `inferno_lynx` | rare | — | **Chimaera** | Lion-fronted, goat-spined, serpent-tailed — and burning at every seam. |
| `molten_drake` | rare | — | **Lernean Pyrohydra** | Cousin to Lerna's serpent — sever a head and a candle answers in its place. |
| `pyrotyrant` | rare | — | **Typhon** | Hundred-throated, mountain-shouldered, the last child of Gaia to dare the sky. |
| `pyroshrike` | uncommon | — | **Chalkotaur Calf** | Hephaestus's bronze bulls were calves once — and even then they breathed forge-fire. |

### OLYMPIAN · WATER (12)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `tide_empress` | legendary | TIDAL | **Amphitrite, Sea-Queen** | Consort of Poseidon, sovereign of the salt-throne; the wave bows where her foot falls. |
| `coral_augur` | epic | TIDAL | **Scylla, Reef-Witch** | Twelve-voiced oracle of the strait — she names the drowned before the wave is done. |
| `glacier_kraken` | rare | — | **Kraken of Aegaeon** | Frost-bound cousin of the deep-kraken; even its silence cracks the hull. |
| `leviathan_prime` | rare | — | **Keto Primeval** | Mother of monsters in the dark below, before the gods drew lots for the sea. |
| `maelstrom_serpent` | rare | — | **Charybdis** | Thrice-daily she gulps the strait; thrice-daily the keels splinter in her throat. |
| `riptide_wyrm` | rare | — | **Thetis, Silver-Footed** | Silver-footed nymph who shielded a hero in the river; her hand still warms the wounded. |
| `coral_priest` | uncommon | TIDAL | **Nereid Priestess** | Daughter of Nereus, she sings the salt into wounds and the wounds into salt. |
| `frostfin` | uncommon | — | **Telchine of the Tide** | Sea-smiths whose hammered curses dulled the bronze of better men. |
| `sea_warden` | uncommon | TIDAL | **Karkinos, Deep-Crab** | The crab Hera sent against Heracles — crushed underfoot, set in stars, still hungry. |
| `tide_chanter` | uncommon | TIDAL | **Siren of Anthemoessa** | On the meadow-isle she sings; the rowers turn, and the rowers do not return. |
| `tidewatcher` | uncommon | TIDAL | **Glaukos** | A fisherman who tasted the green herb; his skin became scale and his eye became sea. |
| `krakenling` | common | TIDAL | **Krakenling** | Born in the trench, learning already the weight of the Aegean above. |

### OLYMPIAN · NATURE (4)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `worldroot_sentinel` | legendary | BULWARK | **Gaia, Worldmother** | The earth herself, broad-breasted, who gave the gods their thrones and outlasts them. |
| `bulwark_patriarch` | epic | BULWARK | **Kronos, Titan-Father** | Deposed lord of the golden age; the scythe rusts but the patience does not. |
| `bramblegoat` | uncommon | — | **Aegipan** | Goat-horned forest-guardian; the hedge thickens where his hoof has passed. |
| `petalwing` | common | BULWARK | **Meliai** | Ash-tree nymphs born of Ouranos's blood; their petals close every wound but one. |

### OLYMPIAN · VOLT (7)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `arc_serpent` | rare | — | **Python of Delphi** | The first oracle was a serpent at the navel-stone; the lightning is its modern tongue. |
| `plasma_djinn` | rare | — | **Astrape** | The lightning made woman; she walks beside Zeus and burns where she is asked. |
| `storm_celestial` | rare | — | **Zeus, Sky-Lord** | Cloud-gatherer, thunder-thrower; he reigns and the lesser sky obeys. |
| `tempest_eagle` | rare | — | **Aetos Dios** | Zeus's golden eagle — bearer of bolts, herald of the throne above the throne. |
| `glimmerowl` | uncommon | — | **Glaukos-Owl of Athena** | Grey-eyed bird of the grey-eyed goddess; what she sees by lamp, it sees by storm. |
| `prism_strider` | uncommon | SYNCRETIC | **Iris, Rainbow-Courier** | Bow of the gods bent across the sky; she carries the message between the thrones. |
| `prismbolt` | common | SYNCRETIC | **Iris-Mote** | A fleck of the rainbow-courier's bow; the small message before the great one. |

### OLYMPIAN · VOID (8)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `voidking_morr` | legendary | REVENANT | **Hades, Unseen-King** | Lord of the rich land below; his crown is forged of asphodel, his court of silence. |
| `crypt_wraith` | epic | REVENANT | **Thanatos, Reaper** | Soft-stepping brother of Sleep; even the strong yield to his cool hand at last. |
| `echo_lich` | rare | — | **Erebus** | Primordial dark beneath the earth, before there was a god to name him. |
| `haunt_hare` | rare | — | **Lampas of Hekate** | Torch-bearing hare of the witch-goddess; it crosses the crossroads and crosses again. |
| `mindroot` | rare | — | **Mandragora of Kokytos** | Pulled from the wailing river — its scream poisons the ear and the year that hears it. |
| `nullhound` | rare | — | **Cerberus** | Three heads at the gate; one for the coming, one for the going, one that does not sleep. |
| `riftwraith` | rare | — | **Keres, Battle-Spirit** | Sister-fates of violent death; they crouch at the wound and drink while the man still names them. |
| `whisperling` | common | — | **Moros** | Doom-spirit; he murmurs the ending under every beginning. |


## 5. AESIR Canon (Norse) — 38 cards

**Voice:** saga-terse, kenning-laden, doom-inflected. Winter-poetry cadence.  
**Visual:** runes, fur, rime, silver-birch, iron and bone.  
**Signature figures:** Odin, Thor, Loki, Freyja, Freyr, Fenrir, Jörmungandr, Hel, Rán, Valkyries, Draugr, Jötnar.

### AESIR · FIRE (4)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `cinderhound` | uncommon | — | **Garmr-Whelp** | Fire-eyed pup of the hellhound-line; the ember in the eye outlasts the hand that strikes it. |
| `flarelord` | uncommon | INFERNO | **Surtr** | Fire comes from the south; Surtr the flame-wreathed bears a sword brighter than the sun. |
| `brimling` | common | INFERNO | **Eldjötunn Whelp** | Young of the fire-giants; small still, but the cradle scorches the rock. |
| `flarefly` | common | INFERNO | **Muspel-Spark** | Ember from the south-realm before the worlds; it remembers being older than ice. |

### AESIR · WATER (7)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `tidewyrm` | rare | — | **Rán, Drowning-Queen** | She casts her net in the cold green water and gathers the drowned to her hall. |
| `abysseel` | uncommon | — | **Fossegrim** | Waterfall-spirit who plays the fiddle; the salmon learn the tune and the boys do not return. |
| `dewfin` | common | TIDAL | **Brunnmigi** | Spring-fouler of the homestead; the well goes sour where his shadow has crossed. |
| `mistray` | common | TIDAL | **Havmand's Fin** | Merman of the cold north water; the fin breaks the surface, the rest is rumor. |
| `saltsprite` | common | TIDAL | **Meermin** | Salt-spray sprite of the longship's wake; small, but the cold remembers her. |
| `spring_otter` | common | TIDAL | **Otr of Andvari** | Shape-shifter slain by Loki's stone; his pelt is the curse that doomed the Niflungs. |
| `tide_imp` | common | TIDAL | **Rán-Maiden** | Daughter of the net-queen; she counts the drowned by their belt-buckles. |

### AESIR · NATURE (14)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `forest_warden` | rare | — | **Huldra, Forest-Hidden** | Beautiful from the front, hollow at the back — and the woodsman never wakes the same. |
| `moss_titan` | rare | — | **Jötunn of Jotunheim** | Stone-and-rime giant of the eastern realm; the mountain walks and the snow follows. |
| `worldroot_colossus` | rare | — | **Yggdrasil-Root** | A root of the world-ash given walking shape; it carries the weight that holds the nine. |
| `barkguard` | uncommon | BULWARK | **Skogsrå Warden** | Grown into the grove; the trees bend where she walks, and the bear gives way. |
| `bramble_warden` | uncommon | BULWARK | **Thyrsus-Bearer** | Thorn-staffed druid of the cold groves; the briar answers to the staff's tap. |
| `forest_keeper` | uncommon | BULWARK | **Skogsrå Elder** | Eldest of the hidden-folk; she tends the wound and she remembers the hand that gave it. |
| `stone_titan` | uncommon | BULWARK | **Berg-Risi** | Mountain-giant of the high crags; storms cling to him as moss to a stone. |
| `barkpup` | common | BULWARK | **Skogsrå Pup** | Forest-warden's young; the bark grows over the wound while the pup still nurses. |
| `brambleling` | common | BULWARK | **Nisse of the Hedge** | Hedge-house spirit; leave the bowl of porridge or the cattle will not drink at dawn. |
| `forest_cub` | common | BULWARK | **Bjarn Cub** | Bear-cub of the northern wood; soon enough the stride will match the father's. |
| `geodeling` | common | — | **Bergrisi-Pup** | Mountain-giant's young; the boulder thickens around him as the pup remembers nothing. |
| `iron_boar` | common | — | **Gullinbursti** | Freyr's golden-bristled boar; the bristles light the night-road, the tusks open the morning. |
| `ironseed` | common | BULWARK | **Acorn of Yggdrasil** | A seed-shard of the world-ash; from such a thing the nine realms once grew. |
| `sproutkin` | common | BULWARK | **Vaetter Sapling** | Land-wight in seedling form; small, but the soil already knows its name. |

### AESIR · VOLT (6)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `tempest_apex` | legendary | STORMCHAIN | **Thor, Hammer-Bearer** | Red-bearded son of Odin; Mjölnir falls and the giants count themselves before the count. |
| `stormhare` | rare | — | **Vindhare** | Wind-hare of the northern moor; he runs ahead of the storm, and the storm follows. |
| `voltcat_apex` | rare | — | **Valravn** | The raven who ate a king's heart and learned to walk as a wolf in the storm. |
| `spark_serpent` | uncommon | STORMCHAIN | **Lyngorm** | Heath-serpent of the Norse fen; lightning answers when his coil drinks the cloud. |
| `voltsprite` | uncommon | — | **Ratatoskr** | Squirrel of the world-ash; he carries insults up the trunk and curses back down. |
| `thunderfly` | common | STORMCHAIN | **Skuld-Mote** | A spark off the youngest Norn's loom; what she has woven, the mote already knows. |

### AESIR · VOID (7)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `crypt_seer` | uncommon | REVENANT | **Völva** | Seeress of the staff and the trance; she names the fallen before the funeral fire. |
| `dirge_lich` | uncommon | REVENANT | **Draugr-King** | Howe-king who refused the long sleep; the gold turned black, the king did not. |
| `dread_warden` | uncommon | REVENANT | **Niflung Warden** | Elder of the misted dead; he tends the door between the rime and the road. |
| `shadow_warden` | uncommon | REVENANT | **Garm, Warden** | Hound at the gates of Hel; at Ragnarök his chain breaks, and Týr meets him at last. |
| `dread_kit` | common | REVENANT | **Niflung Whelp** | Mist-dead's young; born already cold, born already counting. |
| `shadowpup` | common | REVENANT | **Garm's Pup** | Hellhound's young; the chain is short still, but the howl is already true. |
| `voidling` | common | REVENANT | **Draugr Dreg** | Lesser of the howe-walkers; when the cold claims him at last, the lunge claims one of yours. |


## 6. NETJER Canon (Egyptian) — 30 cards

**Voice:** temple-formal, afterlife-cadenced, hieroglyphic repetition.  
**Visual:** gold, lapis, kohl, linen wraps, sun-disk, scarab, ankh.  
**Signature figures:** Ra, Anubis, Osiris, Isis, Horus, Set, Thoth, Ammit, Bastet, Sobek, Apep, Khepri.

### NETJER · FIRE (5)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `ash_strider` | uncommon | INFERNO | **Sunstrider of Ra** | He walks where Ra has walked; the sand keeps the print, the print keeps the heat. |
| `magmite` | uncommon | — | **Sekhet-Spark** | Ember-fragment from the sun-field of the blessed; the spark that walks of itself. |
| `ashpup` | common | INFERNO | **Solar Cub** | Young of the sun-disk; the light around him is yet small enough to cradle. |
| `cinder_serpent` | common | INFERNO | **Apep Hatchling** | Chaos-serpent's young; in the night-hours he learns the swallowing of the sun. |
| `lava_skink` | common | INFERNO | **Wadjet Skink** | Cobra-shaped, sun-warmed; the uraeus rises from the brow when the king wakes. |

### NETJER · WATER (4)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `abyssbreaker` | uncommon | TIDAL | **Sobek Adept** | Initiate of the crocodile-temple; the river closes its mouth around him in approval. |
| `abyss_minnow` | common | TIDAL | **Sobek-Hatchling** | Crocodile-god's young; even at this size, the tooth knows the meat. |
| `coralwhelp` | common | TIDAL | **Nile Whelp** | Hatchling of the great river; the reeds bend to hide him, the heron does not. |
| `tidefry` | common | TIDAL | **Ra-Barque Pilotfish** | Escort-fish to the solar boat; he swims before the dawn and never tires. |

### NETJER · NATURE (4)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `bulwarthog` | rare | — | **Khepri, Scarab-Warden** | Dung-beetle who rolls the morning sun up from the underworld; the rebirth is in the rolling. |
| `thornpup` | uncommon | — | **Ammit Cub** | Soul-devourer's young; the jaws are small but the ledger is already long. |
| `rootsnake` | common | BULWARK | **Uraeus-Root** | Cobra-root of the temple garden; the king's brow remembers what the soil first held. |
| `sproutling` | common | — | **Lotus of Nefertum** | Sacred lotus of the perfume-god; the bloom opens at dawn and the wound forgets itself. |

### NETJER · VOLT (4)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `arc_lancer` | uncommon | STORMCHAIN | **Set Initiate** | Servant of the red-land lord; he carries the storm under the skin like a scar. |
| `plasma_hound` | uncommon | STORMCHAIN | **Set-Hound** | Jackal of the red desert; storm in the throat, sand in the paw, hunger in the eye. |
| `arc_kit` | common | STORMCHAIN | **Set-Whelp** | Storm-chaos god's young; the red-land wind learns its shape around him. |
| `galekit` | common | STORMCHAIN | **Shezmu-Whelp** | Young of the blood-pressing executioner-god; the cup that he learns to hold is not water. |

### NETJER · VOID (13)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `dread_imp` | uncommon | — | **Apep-Imp** | Lesser chaos-spawn of the night-serpent; even his shadow makes the cobra recoil. |
| `ghoul_imp` | uncommon | REVENANT | **Servant of Ammit** | Lesser soul-eater; he gathers the crumbs the great devourer leaves on the scale. |
| `shadebishop` | uncommon | REVENANT | **Priest of Set** | He drinks the falling-of-allies as wine; the red-land god approves the cup. |
| `spectral_owl` | uncommon | — | **Nocturnal Ibis** | Thoth's night-bird; he reads the stars as letters and the wound as a verse. |
| `wraith_prince` | uncommon | REVENANT | **Wepwawet Herald** | Opener-of-Ways; where he falls the path opens, and the army follows the breach. |
| `cryptmoth` | common | REVENANT | **Scarab-Moth** | Psychopomp moth of the necropolis; the wing-dust marks the path the soul forgets. |
| `ghostfin` | common | REVENANT | **Oxyrhynchus** | Sacred Nile-fish that ate the lost piece of Osiris; what it tasted, it remembers as venom. |
| `hollowpup` | common | REVENANT | **Anubis Pup** | Jackal-child of the embalmer-god; the linen waits, the scale waits, the pup waits. |
| `miasma_imp` | common | REVENANT | **Pestilence of Sekhmet** | Plague-imp of the lion-goddess; she releases what she releases, and the scribes write fast. |
| `shadepup` | common | — | **Shade of Duat** | Nameless walker of the underworld; what name he had was weighed and found wanting. |
| `silentmoth` | common | REVENANT | **Hypocephalus Moth** | Amulet-moth of silent passage; she lays the disk beneath the head of the dead. |
| `spectral_kit` | common | REVENANT | **Serket-Kit** | Scorpion-goddess's young; the tail is small still, but the venom is the venom of Serket. |
| `wraithling` | common | REVENANT | **Ushabti** | Tomb-servant figurine; called by name, he rises and labors in the field of reeds. |


## 7. KAMI Canon (Japanese Shinto + folk yōkai) — 43 cards

**Voice:** haiku-adjacent, nature-animist, yōkai-folk, tea-house patient.  
**Visual:** ukiyo-e composition, torii red, ink-wash, storm-clouds, lantern-light.  
**Signature figures:** Amaterasu, Susanoo, Raijin, Fūjin, Inari, Ryūjin, Kitsune, Tengu, Oni, Kappa, Kodama, Shinigami.

### KAMI · FIRE (6)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `ember_raptor` | uncommon | INFERNO | **Karura Adept** | Grown into the wind-fire; he hunts the dragon now, not the snake. |
| `cindermote` | common | INFERNO | **Hi-no-Kagutsuchi Spark** | Ember of the fire-kami who burned his mother to the underworld at his birth. |
| `coalmunch` | common | — | **Okuribi** | Sending-fire that follows travelers; bow once, and it goes home with the polite. |
| `emberhawk` | common | INFERNO | **Karura Fledgling** | Garuda-bird of the Buddhist-Shinto sky; the fledgling already eats the small serpent whole. |
| `embershrew` | common | INFERNO | **Kamaitachi** | Wind-sickle weasel; the cut comes before the feeling; the feeling never comes. |
| `flickerimp` | common | INFERNO | **Hinode-Oni Whelp** | Dawn-demon young; the first red light is its first appetite. |

### KAMI · WATER (8)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `brineprince` | uncommon | TIDAL | **Namazu Lord** | Catfish under the islands; when the keystone slips, the rooftiles remember. |
| `shellguard` | uncommon | TIDAL | **Kappa Warden** | Elder of the river-imps; the cucumber is left, the boat is let pass, the shrine is tended. |
| `tide_synth` | uncommon | SYNCRETIC | **Mizu-no-Yōkai Adept** | Grown confluence-spirit; the rivers meet at his palm and remember the meeting. |
| `brineling` | common | TIDAL | **Namazu Fry** | Earthquake-catfish young; small turn, small tremor; the keystone-stone holds, for now. |
| `mistling` | common | — | **Ayakashi-Drift** | Sea-mist yōkai over the night-water; nothing seen, only something passing. |
| `seapup` | common | TIDAL | **Ryūjin Whelp** | Dragon-king's young; his cradle is a tide-pool, his lullaby is a typhoon. |
| `shellfin` | common | TIDAL | **Kappa Scholar** | River-imp of letters; he keeps the bowl of water on his head and the bowl of ink at his side. |
| `tidemerger` | common | SYNCRETIC | **Mizu-no-Yōkai Jr.** | Young water-spirit of confluences; he learns where two rivers become a third name. |

### KAMI · NATURE (8)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `mossbear` | uncommon | BULWARK | **Kodama Elder** | Bark-skin, slow heartbeat — the grove answers when its eldest is struck. |
| `root_warden` | uncommon | BULWARK | **Kodama Warden** | Grown tree-spirit; do not fell the trunk that does not answer the axe. |
| `thornserpent` | uncommon | BULWARK | **Jorōgumo Warden** | Grown spider-spirit; the parlor is set, the visitor is welcome, the visitor is not leaving. |
| `mosscat` | common | BULWARK | **Maneki-Neko Kit** | Beckoning-cat young; the small paw lifts, and the morning comes well to the household. |
| `mosshound` | common | BULWARK | **Inugami Pup** | Dog-spirit of the mossed shrine; faithful to the family that fed it, faithful past death. |
| `mossling` | common | BULWARK | **Kodama Sapling** | Tree-spirit's youngest form; the cedar-knock answers when none of the kin will speak. |
| `stonepup` | common | BULWARK | **Komainu Pup** | Shrine lion-dog young; even the cub at the gate makes the bad spirit hesitate. |
| `thornling` | common | BULWARK | **Jorōgumo Thread** | First strand of the spider-spirit; pretty, fine, and waiting for the second strand. |

### KAMI · VOLT (15)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `arc_predator` | epic | STORMCHAIN | **Raijū-Taishō** | Thunder-beast commander; the pack moves where the storm leans, and the storm leans where he turns. |
| `boltrunner` | uncommon | STORMCHAIN | **Raijin Herald** | Thunder-god's messenger; he runs the road of clouds and arrives before his footfall. |
| `galelord` | uncommon | STORMCHAIN | **Fūjin Herald** | Wind-god's envoy; he opens the bag a hand's-width and the army is fast. |
| `shock_runner` | uncommon | STORMCHAIN | **Raijin Drumroller** | Thunder-god's drum-beater; the cadence is the thunder, the thunder is the cadence. |
| `zapdrake` | uncommon | STORMCHAIN | **Raiju Initiate** | Thunder-beast grown; the bolt is the body, the body is the bolt's choice of road. |
| `arc_pup` | common | STORMCHAIN | **Raiju Cub** | Thunder-wolf juvenile; the play-bite already crackles. |
| `arcweasel` | common | STORMCHAIN | **Kamaitachi Jr.** | Younger wind-sickle; he learns the cut from his elder, and the elder is a quick teacher. |
| `boltbat` | common | STORMCHAIN | **Raiju-Hatchling** | Newborn thunder-beast; the wings have not yet learned to fold, the bolt has. |
| `boltkit` | common | STORMCHAIN | **Raijin-Kit** | Thunder-god's attendant-young; the small drum learns the cadence of the storm. |
| `flashfox` | common | STORMCHAIN | **Tenko-Kit** | Heaven-fox's young; her tail will be many in time, and her bolt will be one. |
| `plasma_kit` | common | STORMCHAIN | **Raijin Acolyte** | Young Raijin-servant; the drumstick is borrowed, the cadence is true. |
| `shockling` | common | STORMCHAIN | **Rai-Mote** | Thunder-spark; brief life, bright life, the cicada of the storm. |
| `spark_imp` | common | STORMCHAIN | **Oni-Spark** | Lesser demon-spark from the iron club; small, but the bruise it leaves is purple-blue. |
| `stormpup` | common | STORMCHAIN | **Fūjin Pup** | Wind-god's young; the bag at his back is small, but the wind in it is the same wind. |
| `zapling` | common | STORMCHAIN | **Raiju Whelp** | Thunder-beast young; in the storm he sleeps in the navel of the careless sleeper. |

### KAMI · VOID (5)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `shade_prismatic` | uncommon | SYNCRETIC | **Yōkai Chorus** | Many spirits, one voice; the night-procession becomes a single agreed song. |
| `dirgebat` | common | REVENANT | **Onryō-Bat** | Vengeful-ghost in bat-form; she remembers the room, she remembers the door, she remembers the name. |
| `duskmoth` | common | — | **Mothra of Yomi** | Moth that flies between the lit world and the underworld; the wings carry the ash of both. |
| `shadeling` | common | REVENANT | **Shinigami Initiate** | Young death-spirit; the ledger is short, but the hand that writes it is patient. |
| `shadeprism` | common | SYNCRETIC | **Yōkai Whisper** | Many spirits, one murmur; you cannot hear it; you have already answered it. |

### KAMI · NORMAL (1)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `loremaster_ape` | rare | — | **Sanzaru** | See-no, hear-no, speak-no — the three become one, and the one teaches the troop. |


## 8. TEOTL Canon (Aztec / Mexica) — 34 cards

**Voice:** ritual-cadenced, feather-and-obsidian, Nahuatl fragments. Blood-urgent but not gratuitous.  
**Visual:** turquoise, jaguar-pelt, feather-mosaic, obsidian black, cochineal red, gold.  
**Signature figures:** Huitzilopochtli, Quetzalcóatl, Tezcatlipoca, Tlaloc, Xipe Totec, Mictlantecuhtli, Coatlicue, Xolotl, Ehecatl.

### TEOTL · FIRE (15)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `rainbow_drake` | epic | SYNCRETIC | **Quetzalcóatl, Plumed One** | Plumed serpent of sky and earth; he gave the people maize and the calendar, then walked east. |
| `blazewolf` | rare | — | **Xolotl, Dog-Star** | Dog-headed twin of the plumed one; he guides the sun through the night-country and back. |
| `blazefiend` | uncommon | INFERNO | **Xiuhcoatl Adept** | Initiate of the fire-serpent cult; he aims the venom-flame and the weakest is named first. |
| `coalbreaker` | uncommon | INFERNO | **Jaguar Warrior** | Ocelotl initiate; the pelt is earned, the kill is recorded, the sun is fed. |
| `flame_chimera_adept` | uncommon | SYNCRETIC | **Xochitonal Adept** | Grown flower-fire spirit; the petal opens, the spark answers the petal's invitation. |
| `magma_warden` | uncommon | INFERNO | **Tlāhuiztli Commander** | Elder solar-warrior; the tunic fits, the orders carry, the sun is fed on schedule. |
| `sunscale_serpent` | uncommon | SYNCRETIC | **Quetzalcóatl Adept** | Grown plumed serpent; he learns the names of the four winds and answers each in turn. |
| `ashling` | common | — | **Xiuhcoatl Spark** | Ember of the turquoise fire-serpent; the sun eats thirteen hearts, the spark eats one. |
| `coalwhelp` | common | INFERNO | **Obsidian Pup** | Jaguar-warrior's young; the obsidian club is his teething-stone. |
| `emberpup` | common | — | **Ocelocoyotl Pup** | Jaguar-coyote hybrid pup; the bite is the jaguar's, the laugh is the coyote's. |
| `flame_chimerlet` | common | SYNCRETIC | **Xochitonal Whelp** | Flower-fire spirit's young; the bloom and the spark are the same gesture, just younger. |
| `ignis_kit` | common | INFERNO | **Xipe Totec Cub** | Flayed-god's young; the skin he sheds becomes the spring he gives. |
| `magmaling` | common | INFERNO | **Tlāhuiztli Whelp** | Solar-warrior's young; the feathered tunic is too large still — for now. |
| `soot_finch` | common | INFERNO | **Tototl-Warrior** | Bird-warrior of the codex-fields; the war-cry is the song, the song is the cry. |
| `sunscale_drake` | common | SYNCRETIC | **Quetzalcóatl Hatchling** | Plumed serpent's young; the down is already the color of the dawn-sky. |

### TEOTL · WATER (4)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `mistchimera_adept` | uncommon | SYNCRETIC | **Tlaloc's Storm-Mist** | Grown rain-mist envoy; the cloud is heavy now and the field will know it by morning. |
| `mistchimera` | common | SYNCRETIC | **Tlaloc's Mist** | Vaporous envoy of the rain-god; the mist is the message, the mist is the rain to come. |
| `surfling` | common | TIDAL | **Acihuatl Sprite** | Water-nymph of the Mexica lakes; she carries the gourd that does not empty. |
| `tidepup` | common | — | **Ahuizotl Pup** | Lagoon-dog's young; the tail-hand is small, the snatch-from-the-bank is already practiced. |

### TEOTL · NATURE (5)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `prism_chimera` | epic | SYNCRETIC | **Coatlicue, Serpent-Skirt** | Earth-mother of the serpent-skirt; she gave birth to the sun and the moon, and to a war between them. |
| `prism_grove` | uncommon | SYNCRETIC | **Xochipilli Grove** | Flower-prince's bloom; the grove sings without breath and the warrior remembers the field. |
| `verdant_chimera` | uncommon | SYNCRETIC | **Mayahuel Adept** | Grown maguey-priestess; the agave's four hundred breasts answer four hundred gods. |
| `prism_seedling` | common | SYNCRETIC | **Xochitl Seed** | Sacred flower-seed of the codex; planted in the right month, it answers the right god. |
| `verdant_chimerlet` | common | SYNCRETIC | **Mayahuel Whelp** | Maguey-goddess's young; the leaf-shield is the gift, the sap-wine is the second gift. |

### TEOTL · VOLT (4)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `spectral_charge` | uncommon | SYNCRETIC | **Teoyaomiqui Adept** | Grown war-dead spirit; he carries the obsidian and the calendar to the next sun. |
| `charge_chick` | common | — | **Hummingbird of Huitzilopochtli** | Sacred warrior-hummingbird; the small beak drinks the small heart and asks for the next. |
| `dashmouse` | common | — | **Ehecatl Mouse** | Wind-god's scurrier; he runs ahead of the rain and the rain says he is welcome. |
| `spectral_volt` | common | SYNCRETIC | **Teoyaomiqui Spark** | Spark of the war-dead; he came home as a hummingbird and as this small bright thing. |

### TEOTL · VOID (6)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `world_eater` | legendary | SYNCRETIC | **Tezcatlipoca, Smoking Mirror** | Night-jaguar primordial; the mirror smokes and the world is here, and the world is here. |
| `abyss_warden` | rare | — | **Chalchiuhtlicue, Jade-Skirt** | Lake-and-river goddess; her skirt of jade is the green water that drowns and the green water that gives. |
| `void_chimera` | uncommon | SYNCRETIC | **Tzitzimitl Adept** | Grown star-demon; if the sun should fail, she comes down for the children at noon. |
| `voidcrawler` | uncommon | — | **Xolotl-Shadow** | Death-twin's shade; he is the dog at the heel of the soul, and the soul does not refuse him. |
| `nullsprite` | common | REVENANT | **Mictlan-Wisp** | Wisp of the nine-layered underworld; she lights the fourth river and the fourth river only. |
| `void_chimerlet` | common | SYNCRETIC | **Tzitzimitl Spawn** | Star-demon's young; in the eclipse-hour she opens her eyes for the first time. |


## 9. APOCRYPHA (folk / liminal / nowhere-folk) — 17 cards

**Voice:** campfire-local, provincial, fable-murmured. No pantheon dignity; human-scale weirdness.  
**Visual:** moss, lantern-fog, threadbare cloaks, hollow trees, rain-on-shingle.  
**Signature figures:** Boggart, Kelpie, Will-o'-Wisp, Hodag, Brownie, Kobold, Nisse, Redcap, Dream-Fox, Jenny Greenteeth, Nemean Wanderer, First Ancestor.

### APOCRYPHA · WATER (1)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `coralcub` | common | — | **Jenny Greenteeth Pup** | Marsh-hag's juvenile; mind the ditch at twilight, mind the green skin under the duckweed. |

### APOCRYPHA · NATURE (1)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `boulder_mole` | common | BULWARK | **Hodag** | American forest-folk beast; lumpy-bristled and stand-your-ground; the stick rebounds. |

### APOCRYPHA · VOLT (1)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `sparkling` | common | — | **Will-o'-Wisp** | Swamp-light folk-spirit; follow it home and you will not arrive at the home you meant. |

### APOCRYPHA · NORMAL (14)
| card_id | rarity | archetype | name | flavor |
|---|---|---|---|---|
| `concord_phoenix` | epic | — | **The First Ancestor** | The eldest of the village dead; bless the field and the new-fallen step into the line. |
| `aegis_lion` | rare | — | **Nemean Wanderer** | Lion of no temple; he keeps the crossroads, and the road keeps him, and that is enough. |
| `mendicant_sphinx` | uncommon | — | **Crossroads Sphinx** | Riddleless sphinx of the highway; he asks the price of bread and lets you pass. |
| `rune_owl` | uncommon | — | **Soothsayer Owl** | Hedge-witch's familiar; she reads the barley-stalk and the barley-stalk reads her back. |
| `stoneward` | uncommon | — | **Cairn-Warden** | Stone-stacker of the mountain path; add a stone, and you will come back down. |
| `wrought_bear` | uncommon | — | **Iron-Tongued Bear** | Bear-spirit of the parish; he speaks once a year and the village writes it down. |
| `brass_mole` | common | — | **Brass Mole** | Earth-worker of the old folk; the brass is the color of the soil it has crossed. |
| `cloth_sprite` | common | — | **Brownie** | Household helper; he patches the cloak in the night and asks no thanks but the milk. |
| `grove_pup` | common | — | **Grove Hob** | Hedge-child between the rows; he trots between the wounded and is gone before the thanks. |
| `mossback_ox` | common | — | **Old Ox of the Parish** | Unmoveable farm-guardian; the field is plowed where he stops and not before. |
| `page_slime` | common | — | **Marginalia** | Book-margin spirit; he writes a small hand in the white space and the wound forgets the page. |
| `pebbler` | common | — | **Dolmen Spirit** | Stone-cairn folk; older than the parish, older than the parish's saint. |
| `quill_cat` | common | — | **Thistle Cat** | Hedge-cat of the thorn-rows; he hunts what the hedge will not hunt itself. |
| `runic_whelp` | common | — | **Hedge-Scribe** | Half-chant household spirit; he scratches a charm on the lintel and the hearth holds. |

---

## 10. Evolution lines preserved

Old species evolution chains stay bound to a single new mythology family
(no cross-Canon evolutions in the shipped data). Manually verified across
the 24 chains shipped in v1_alpha:

| Engine line (`card_id`s) | Mythology line | Canon |
|---|---|---|
| `ashpup` → `ash_strider` | Solar Cub → Sunstrider of Ra | NETJER |
| `barkpup` → `barkguard` | Skogsrå Pup → Skogsrå Warden | AESIR |
| `boltkit` → `boltrunner` | Raijin-Kit → Raijin Herald | KAMI |
| `brineling` → `brineprince` | Namazu Fry → Namazu Lord | KAMI |
| `coalwhelp` → `coalbreaker` | Obsidian Pup → Jaguar Warrior | TEOTL |
| `dread_kit` → `dread_warden` | Niflung Whelp → Niflung Warden | AESIR |
| `emberhawk` → `ember_raptor` | Karura Fledgling → Karura Adept | KAMI |
| `flame_chimerlet` → `flame_chimera_adept` | Xochitonal Whelp → Xochitonal Adept | TEOTL |
| `magmaling` → `magma_warden` | Tlāhuiztli Whelp → Tlāhuiztli Commander | TEOTL |
| `mistchimera` → `mistchimera_adept` | Tlaloc's Mist → Tlaloc's Storm-Mist | TEOTL |
| `mossling` → `mossbear` | Kodama Sapling → Kodama Elder | KAMI |
| `prism_seedling` → `prism_grove` | Xochitl Seed → Xochipilli Grove | TEOTL |
| `prismbolt` → `prism_strider` | Iris-Mote → Iris, Rainbow-Courier | OLYMPIAN |
| `shadeling` → `shadebishop` | Shinigami Initiate → Priest of Set | **cross-Canon** ⚠ |
| `shadeprism` → `shade_prismatic` | Yōkai Whisper → Yōkai Chorus | KAMI |
| `shadowpup` → `shadow_warden` | Garm's Pup → Garm, Warden | AESIR |
| `shellfin` → `shellguard` | Kappa Scholar → Kappa Warden | KAMI |
| `spectral_volt` → `spectral_charge` | Teoyaomiqui Spark → Teoyaomiqui Adept | TEOTL |
| `sunscale_drake` → `sunscale_serpent` | Quetzalcóatl Hatchling → Quetzalcóatl Adept | TEOTL |
| `thornling` → `thornserpent` | Jorōgumo Thread → Jorōgumo Warden | KAMI |
| `tidefry` → (no Uncommon evolution shipped) | Ra-Barque Pilotfish | NETJER |
| `verdant_chimerlet` → `verdant_chimera` | Mayahuel Whelp → Mayahuel Adept | TEOTL |
| `void_chimerlet` → `void_chimera` | Tzitzimitl Spawn → Tzitzimitl Adept | TEOTL |
| `wraithling` → `wraith_prince` | Ushabti → Wepwawet Herald | NETJER |
| `zapling` → `zapdrake` | Raiju Whelp → Raiju Initiate | KAMI |

**One known cross-Canon chain** (`shadeling` Kami → `shadebishop` Netjer):
the audit ranked Priest-of-Set as the strongest mechanical-lore fit for
`shadebishop`'s "drinks ally death as wine" trigger, and Shinigami-Initiate
as the strongest fit for `shadeling`'s baseline death-spirit. Acceptable
flavor inconsistency for one chain; flagged here for future smoothing.

## 11. Audit history (this rewrite)

The pre-rewrite draft assigned mythology figures from the canon-template
without per-card mechanical inspection. The audit pass
(`docs/canon_audit.md`, 2026-04-23) graded all 200 proposed assignments
against actual `triggers` and `rule_change` ops:

| Audit grade | Count | Action |
|---|---|---|
| ✅ Clean (mechanic matches lore) | 165 | Kept proposed name |
| ⚠ Borderline (mechanic only loosely matches) | 19 | 13 swapped to better fits, 6 kept with documented justification |
| ❌ Mismatch (mechanic contradicts lore) | 13 | All 13 swapped within same Canon |
| ⛔ Missing | 3 | Filled (`blazefiend`, `galelord`, `voidling`) |

Notable mechanical-lore corrections applied (full list in
`docs/canon_audit.md` §2-3):

- `magma_tyrant` was "Hephaestus, Forgelord" — but the card has no smith
  flavor in its triggers (it's a pure damage L6 with Stolen-Fire move).
  **Fixed:** Prometheus, Fire-Thief.
- `ashen_phoenix` was "Phoenix of Heliopolis" — but the card lacks any
  REVIVE_AT_HP op (Apocrypha-style chain that doesn't actually revive).
  **Fixed:** Prometheus-Unbound (the gift goes to others now = ON_DEATH heal
  allies trigger).
- `solar_phoenix` was "Bennu, Sun-Bird" — same revive issue.
  **Fixed:** Helios, Sun-Charioteer (dive at dawn = ON_OPENING_ATTACK damage,
  bleeding light at dusk = ON_DEATH heal).
- `concord_phoenix` was "The First Phoenix" — same.
  **Fixed:** The First Ancestor (bless the field = battle-start heal allies,
  new-fallen step into the line = on-ally-death buff).
- `world_eater` kept Tezcatlipoca placement — mechanic (Mirror's Roar +
  Night-Jaguar Maw + Smoking-Mirror Devouring) actively reinforces the
  smoking-mirror cosmology.
- `boulder_mole` was "Redcap Burrower" — Hodag is a closer mechanical fit
  for ON_TAKE_DAMAGE buff_def "stick rebounds" stand-your-ground play.

The "Phoenix V2 reservation" — `ashen_phoenix`, `solar_phoenix`,
`concord_phoenix` all retained their card_ids but lost their phoenix display
names. The phoenix display name is now reserved for V2, when a proper
`REVIVE_AT_HP` op is added to the engine and a real phoenix card becomes
mechanically possible.

## 12. V2 work items captured

- **`REVIVE_AT_HP` op** — new trigger op for a future phoenix-line card
  (a fresh `card_id`, not retrofitted onto existing legendary slot). Lore-
  perfect Bennu / Phoenix-of-Heliopolis carrier becomes possible.
- **Hindu Canon** — Garuda, Naga, Rakshasa, Kali, Hanuman, etc. Full roster,
  not tokenized (Sanzaru is the only South-Asian-adjacent slot in V1, kept
  in KAMI for Buddhist-Shinto syncretism).
- **Additional Canons** — Celtic (Tuatha Dé), Slavic (Rodnovery), Chinese
  (Shen), African (Orisha), Sumerian (Anunnaki), Polynesian (Atua).
- **`FACTION_SYNERGY` trigger op** — optional Canon-as-faction bonuses
  (e.g. "3+ AESIR cards in loadout → BUFF_ATK +1"). Punted from V1.
- **Cross-Canon evolution smoothing** — `shadeling → shadebishop` is the one
  chain that crosses Canons (KAMI → NETJER). Fix in V2 by either renaming
  `shadebishop` to a Kami death-priest or `shadeling` to an Egyptian
  death-spirit.
- **Diacritic standardization pass** — Nahuatl (Xiuhcoatl, Tlāhuiztli,
  Tzitzimitl), Norse (Skogsrå, Bergrisi, Jötunn), Greek (Aetós Diós,
  Glaukós-Owl). Currently mixed ASCII / accented; pick a house style.

## 13. Provenance

- Source-of-truth data: `tools/canon_rewrite/mapping.py` (200 entries).
- Idempotent rewrite script: `tools/canon_rewrite/apply.py`.
- Audit grading: `docs/canon_audit.md`.
- Rewrite ran 2026-04-23 against `monster-pivot` branch HEAD.
- Test gate after rewrite: `1315 passed, 1 skipped` (pytest, full suite).
