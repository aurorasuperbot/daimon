"""Phase 5 NPC tier roster — designed against the v1_alpha 200-card pool.

This file is the SINGLE SOURCE OF TRUTH for the 25-NPC roster. Run
`python tools/phase5/regen_npcs.py` to materialise the JSON files under
`daimon/npcs/<tier>/<npc_id>.json` from this dict.

Design principles (per manifest tier rules):

  rookie    All-commons (or 5C + 1U). Mostly zero-trigger 'vanilla' commons.
            No archetype synergy. Beginner-safe punching bags with HP and
            slot variety. 5 NPCs ~ 5 stat-shape silhouettes.

  novice    Mostly commons + 2 uncommons. One archetype HINT per NPC, light
            single-trigger setups. Each novice introduces ONE archetype.

  veteran   Rare anchor + uncommons. 2-3 trigger cards in concert. Each
            veteran COMMITS to the archetype the matching novice hinted at.

  elite     Epic anchor + rares + 3-4 triggers per round. Five distinct
            archetype apexes (BULWARK, REVENANT, TIDAL, STORMCHAIN, FLUX).

  champion  Legendary anchor + epic + tight synergy. Endgame fight, each
            champion builds AROUND its legendary's stat profile.

Naming legend (5 NPCs per tier, ordered):
  rookie:    sparring_sam, hedgerow_hannah, spark_kid_sora, tidepool_tom, sundown_si
  novice:    watchman_wren, owl_eyed_olive, forge_hand_fran, static_sky, gentle_goro
  veteran:   bramble_beth, quickfoot_quinn, rust_priest_rhea, stormrider_sven, rainmaker_reka
  elite:     iron_shield_ira, mind_eater_mox, tide_priest_telos, volt_priest_vex, storm_warden_wynn
  champion:  apex_king_atlas, doom_paw_doppia, mythbreaker_marn, stormcrown_sienna, voidwalker_vance
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# ROSTER
# ---------------------------------------------------------------------------
# Each entry: (npc_id, name, flavor, bio, loadout)
# loadout = exactly 6 catalog card_ids, max 2 of same species.
# ---------------------------------------------------------------------------

ROSTER = {
    # ------------------------------------------------------------------ ROOKIE
    "rookie": [
        {
            "npc_id": "sparring_sam",
            "name": "Sparring Sam",
            "flavor": "Trains with what's at hand.",
            "bio": (
                "Sam grabbed the first six cards he saw and signed up. There's no "
                "plan, no archetype, no triggers — just six honest monsters that "
                "hit until something falls over. Use this fight to learn how slot "
                "order matters."
            ),
            "loadout": [
                "iron_boar",   # NATURE tank, vanilla
                "emberpup",    # FIRE glass, vanilla
                "mistling",    # WATER medium, vanilla
                "dashmouse",   # VOLT speedster, vanilla
                "geodeling",   # NATURE tank, vanilla
                "sproutling",  # NATURE filler, vanilla
            ],
        },
        {
            "npc_id": "hedgerow_hannah",
            "name": "Hedgerow Hannah",
            "flavor": "Walks the long path.",
            "bio": (
                "Patient gardener-turned-trainer. Her team is a hedge: thick, low "
                "and refuses to move. No triggers will fire. You'll have to chip "
                "through. Watch how high HP + low SPD plays out across 5 rounds."
            ),
            "loadout": [
                "iron_boar",     # NATURE wall
                "geodeling",     # NATURE wall
                "coralcub",      # WATER def-7 wall
                "mossback_ox",   # NORMAL slow wall
                "pebbler",       # NORMAL stocky
                "sproutling",    # NATURE chip
            ],
        },
        {
            "npc_id": "spark_kid_sora",
            "name": "Spark-kid Sora",
            "flavor": "Loud and harmless.",
            "bio": (
                "Sora wants to go FAST and doesn't care about defense. Every card "
                "rolls high SPD but folds in 1-2 hits. Focus the priority targets "
                "before they unload."
            ),
            "loadout": [
                "dashmouse",     # VOLT speedster
                "charge_chick",  # VOLT spd=10 vanilla
                "sparkling",     # VOLT spd=9 vanilla
                "emberpup",      # FIRE atk-7 glass
                "whisperling",   # VOID spd=7 vanilla
                "mistling",      # WATER ballast
            ],
        },
        {
            "npc_id": "tidepool_tom",
            "name": "Tidepool Tom",
            "flavor": "Floats wherever.",
            "bio": (
                "Tom fishes more than he trains. Mostly water vanillas with no "
                "bite — but enough HP to take a few rounds off your own clock. "
                "The geodeling on the bench is the only defensive anchor."
            ),
            "loadout": [
                "mistling",   # WATER vanilla
                "tidepup",    # WATER vanilla atk=5
                "coralcub",   # WATER def-7 wall
                "geodeling",  # NATURE wall
                "iron_boar",  # NATURE wall
                "emberpup",   # FIRE glass cannon
            ],
        },
        {
            "npc_id": "sundown_si",
            "name": "Sundown Si",
            "flavor": "Old eye, slow hand.",
            "bio": (
                "Si trains by lamplight. The team leans VOID for spookiness but "
                "no triggers will land — these are all vanilla apprentices. A "
                "tutorial-tier introduction to the VOID element ring (weak vs FIRE)."
            ),
            "loadout": [
                "whisperling",  # VOID glass
                "duskmoth",     # VOID glass spd=8
                "shadepup",     # VOID glass
                "iron_boar",    # NATURE wall ballast
                "geodeling",    # NATURE wall ballast
                "mistling",     # WATER medium
            ],
        },
    ],

    # ------------------------------------------------------------------ NOVICE
    "novice": [
        {
            "npc_id": "watchman_wren",
            "name": "Watchman Wren",
            "flavor": "Stands in the gate.",
            "bio": (
                "Introduces BULWARK. Wren's team blooms tiny ON_BATTLE_START shields "
                "and grinds DEF on hits. One uncommon thornpup anchors the wall."
            ),
            "loadout": [
                "barkpup",      # NATURE C BULWARK ON_BATTLE_START
                "ironseed",     # NATURE C BULWARK ON_BATTLE_START
                "mossling",     # NATURE C BULWARK ON_TAKE_DAMAGE
                "thornpup",     # NATURE U thorny ON_TAKE_DAMAGE
                "iron_boar",    # NATURE C ballast
                "geodeling",    # NATURE C ballast
            ],
        },
        {
            "npc_id": "owl_eyed_olive",
            "name": "Owl-eyed Olive",
            "flavor": "Sees the strike before it lands.",
            "bio": (
                "Introduces STORMCHAIN. VOLT speedsters anchored on tempest_eagle "
                "(atk=11 spd=10, the 'occasional rare' the novice rule allows). "
                "plasma_hound + stoneward + geodeling supply tempo and a wall to "
                "survive the rookie tank matchup."
            ),
            "loadout": [
                "plasma_kit",    # VOLT C STORMCHAIN ON_OPENING_ATTACK atk=6
                "stormpup",      # VOLT C STORMCHAIN ON_BATTLE_START
                "geodeling",     # NATURE C wall (def=5 hp=25) — STORMCHAIN can't out-tank without
                "tempest_eagle", # VOLT R (atk=11 spd=10) — occasional rare punch
                "plasma_hound",  # VOLT U STORMCHAIN ON_OPENING_ATTACK atk=7
                "stoneward",     # NORMAL U wall (def=6 hp=24) ON_BATTLE_START
            ],
        },
        {
            "npc_id": "forge_hand_fran",
            "name": "Forge-hand Fran",
            "flavor": "Stoke the bellows.",
            "bio": (
                "Introduces INFERNO. Burns hot early but runs out of HP. The "
                "magmite uncommon supplies the only real punch beyond vanilla."
            ),
            "loadout": [
                "ashpup",         # FIRE C INFERNO ON_ATTACK
                "brimling",       # FIRE C INFERNO ON_OPENING_ATTACK
                "cinder_serpent", # FIRE C INFERNO ON_ATTACK
                "magmite",         # FIRE U ON_ATTACK
                "emberpup",        # FIRE C glass cannon
                "iron_boar",       # NATURE C ballast
            ],
        },
        {
            "npc_id": "static_sky",
            "name": "Static Sky",
            "flavor": "Hair on end.",
            "bio": (
                "STORMCHAIN with one rare (voltcat_apex atk=14, the 'occasional "
                "rare' the novice rule allows). galelord + zapdrake uncommons "
                "BUFF_SPD the team; stoneward supplies the only DEF wall."
            ),
            "loadout": [
                "plasma_kit",     # VOLT C STORMCHAIN ON_OPENING_ATTACK atk=6
                "zapling",        # VOLT C STORMCHAIN ON_BATTLE_START
                "iron_boar",      # NATURE C wall (def=8 hp=30) — pairs with voltcat_apex glass
                "voltcat_apex",   # VOLT R (atk=14 def=3 hp=18) — occasional rare punch
                "galelord",       # VOLT U STORMCHAIN ON_BATTLE_START BUFF_SPD ALL
                "stoneward",      # NORMAL U wall (def=6 hp=24) ON_BATTLE_START
            ],
        },
        {
            "npc_id": "gentle_goro",
            "name": "Gentle Goro",
            "flavor": "Tide in, tide out.",
            "bio": (
                "Introduces TIDAL. Soft heals and slow chip. The tidewatcher uncommon "
                "doubles up on ON_BATTLE_START to tilt the opener."
            ),
            "loadout": [
                "brineling",    # WATER C TIDAL ON_ATTACK
                "seapup",       # WATER C TIDAL ON_ATTACK
                "mistray",      # WATER C TIDAL ON_TAKE_DAMAGE
                "tidewatcher",  # WATER U TIDAL ON_BATTLE_START
                "tidepup",      # WATER C vanilla
                "geodeling",    # NATURE C ballast
            ],
        },
    ],

    # ----------------------------------------------------------------- VETERAN
    "veteran": [
        {
            "npc_id": "bramble_beth",
            "name": "Bramble Beth",
            "flavor": "Roots over rocks.",
            "bio": (
                "BULWARK COMMITTED. moss_titan rare anchors a NATURE wall that "
                "thickens every round. Bramble_warden + mossbear spread ON_BATTLE_START "
                "and ON_TAKE_DAMAGE buffs across the team."
            ),
            "loadout": [
                "moss_titan",      # NATURE R BULWARK anchor (def=11 hp=34)
                "bramble_warden",  # NATURE U BULWARK ON_BATTLE_START
                "mossbear",        # NATURE U BULWARK ON_TAKE_DAMAGE
                "barkpup",         # NATURE C BULWARK ON_BATTLE_START
                "mossling",        # NATURE C BULWARK ON_TAKE_DAMAGE
                "brambleling",     # NATURE C BULWARK ON_BATTLE_START
            ],
        },
        {
            "npc_id": "quickfoot_quinn",
            "name": "Quickfoot Quinn",
            "flavor": "First to strike.",
            "bio": (
                "STORMCHAIN COMMITTED. Two rare anchors — arc_serpent and stormhare — "
                "both spd>=11. Three uncommons (shock_runner, plasma_hound, boltrunner) "
                "stack ON_KILL + ON_OPENING_ATTACK chains. If you don't out-tempo, "
                "you lose the opening exchange."
            ),
            "loadout": [
                "arc_serpent",    # VOLT R anchor (atk=9 spd=11) ON_BATTLE_START + ON_ATTACK
                "stormhare",      # VOLT R anchor (spd=11) ON_OPENING_ATTACK + ON_BATTLE_START
                "shock_runner",   # VOLT U STORMCHAIN ON_KILL
                "plasma_hound",   # VOLT U STORMCHAIN ON_OPENING_ATTACK atk=7
                "boltrunner",     # VOLT U STORMCHAIN ON_KILL atk=6
                "stormpup",       # VOLT C STORMCHAIN ON_BATTLE_START
            ],
        },
        {
            "npc_id": "rust_priest_rhea",
            "name": "Rust-priest Rhea",
            "flavor": "Burn the joints loose.",
            "bio": (
                "INFERNO COMMITTED. blazewolf rare anchors with raw atk=11. Two "
                "uncommons (magma_warden ON_KILL + ash_strider ON_ATTACK) compound "
                "trigger density. Squishy team — race them before the burns stack."
            ),
            "loadout": [
                "blazewolf",     # FIRE R anchor (atk=11)
                "magma_warden",  # FIRE U INFERNO ON_KILL
                "ash_strider",   # FIRE U INFERNO ON_ATTACK
                "ashpup",        # FIRE C INFERNO ON_ATTACK
                "brimling",      # FIRE C INFERNO ON_OPENING_ATTACK
                "ignis_kit",     # FIRE C INFERNO ON_LOW_HP
            ],
        },
        {
            "npc_id": "stormrider_sven",
            "name": "Stormrider Sven",
            "flavor": "Rides the front.",
            "bio": (
                "Tempo VOLT — tempest_eagle + plasma_djinn double rare anchors "
                "(both atk>=11 spd>=10). arc_lancer keys off ON_EXTRA_ACTION_GRANTED "
                "for a chain reaction. Mid-veteran power — 2 rares + 3 uncommons."
            ),
            "loadout": [
                "tempest_eagle",  # VOLT R anchor (atk=11 spd=10) ON_ATTACK
                "plasma_djinn",   # VOLT R (atk=12 spd=11) ON_BATTLE_START + ON_ATTACK
                "arc_lancer",     # VOLT U STORMCHAIN ON_EXTRA_ACTION_GRANTED
                "glimmerowl",     # VOLT U ON_BATTLE_START atk=7 def=5
                "spark_serpent",  # VOLT U STORMCHAIN ON_ATTACK atk=6
                "arc_pup",        # VOLT C STORMCHAIN ON_ATTACK
            ],
        },
        {
            "npc_id": "rainmaker_reka",
            "name": "Rainmaker Reka",
            "flavor": "She brought the weather.",
            "bio": (
                "TIDAL COMMITTED. Two rare anchors — tidewyrm + glacier_kraken — "
                "stack ON_ROUND_START effects. Brineprince uncommon supplies the "
                "ON_ATTACK pressure. Stalls hard, then closes."
            ),
            "loadout": [
                "tidewyrm",        # WATER R anchor ON_ROUND_START
                "glacier_kraken",  # WATER R anchor (atk=9 def=8 hp=32) ON_ROUND_START
                "brineprince",     # WATER U TIDAL ON_ATTACK
                "tide_chanter",    # WATER U TIDAL ON_ROUND_START
                "seapup",          # WATER C TIDAL ON_ATTACK
                "brineling",       # WATER C TIDAL ON_ATTACK
            ],
        },
    ],

    # ------------------------------------------------------------------- ELITE
    "elite": [
        {
            "npc_id": "iron_shield_ira",
            "name": "Iron-shield Ira",
            "flavor": "Patience in plate.",
            "bio": (
                "BULWARK epic. bulwark_patriarch (def=11 hp=30, ON_ROUND_START + "
                "ON_TAKE_DAMAGE) leads a NATURE fortress that healed and re-thorned "
                "before you could push damage. worldroot_colossus on the bench has "
                "ON_DEATH revenge — don't kill it last."
            ),
            "loadout": [
                "bulwark_patriarch",   # NATURE EPIC anchor
                "worldroot_colossus",  # NATURE R (def=14 hp=42) ON_BATTLE_START + ON_DEATH
                "moss_titan",          # NATURE R BULWARK
                "thornpup",            # NATURE U thorns ON_TAKE_DAMAGE
                "barkguard",           # NATURE U BULWARK ON_BATTLE_START
                "ironseed",            # NATURE C BULWARK ON_BATTLE_START
            ],
        },
        {
            "npc_id": "mind_eater_mox",
            "name": "Mind-eater Mox",
            "flavor": "Feeds on the gap.",
            "bio": (
                "REVENANT epic. crypt_wraith (ON_ALLY_DEATH + ON_DEATH) turns every "
                "fallen unit into a payoff. mindroot + abyss_warden rares plus three "
                "REVENANT supports build a death-chain that punishes any kill order."
            ),
            "loadout": [
                "crypt_wraith",    # VOID EPIC anchor
                "mindroot",        # VOID R (atk=8 spd=10) ON_BATTLE_START + ON_ATTACK
                "abyss_warden",    # VOID R (atk=9 def=7 hp=24) ON_BATTLE_START + ON_ATTACK
                "shadebishop",     # VOID U REVENANT ON_ALLY_DEATH
                "wraith_prince",   # VOID U REVENANT ON_DEATH
                "wraithling",      # VOID C REVENANT ON_DEATH
            ],
        },
        {
            "npc_id": "tide_priest_telos",
            "name": "Tide-priest Telos",
            "flavor": "Read the current.",
            "bio": (
                "TIDAL epic. coral_augur opens at full HP and only triggers if "
                "kept full — Telos' team layers heals to keep her there. "
                "leviathan_prime (atk=12 def=10 hp=38) is the wall behind her."
            ),
            "loadout": [
                "coral_augur",        # WATER EPIC anchor
                "leviathan_prime",    # WATER R wall
                "maelstrom_serpent",  # WATER R ON_OPENING_ATTACK + ON_ATTACK
                "tide_chanter",       # WATER U TIDAL ON_ROUND_START
                "abyssbreaker",       # WATER U TIDAL ON_KILL
                "brineling",          # WATER C TIDAL ON_ATTACK
            ],
        },
        {
            "npc_id": "volt_priest_vex",
            "name": "Volt-priest Vex",
            "flavor": "Sermon by induction.",
            "bio": (
                "STORMCHAIN epic. arc_predator (atk=13 spd=11, ON_KILL + ON_ATTACK) "
                "snowballs on every kill. arc_serpent + tempest_eagle + storm_celestial "
                "back it up — three rares plus the epic = relentless tempo. Burst it "
                "down before the third kill compounds the chain."
            ),
            "loadout": [
                "arc_predator",     # VOLT EPIC anchor
                "arc_serpent",      # VOLT R STORMCHAIN
                "tempest_eagle",    # VOLT R high-tempo
                "storm_celestial",  # VOLT R (atk=14 spd=12) ON_BATTLE_START + ON_ATTACK + ON_ALLY_DEATH
                "arc_lancer",       # VOLT U STORMCHAIN ON_EXTRA_ACTION_GRANTED
                "plasma_hound",     # VOLT U STORMCHAIN ON_OPENING_ATTACK
            ],
        },
        {
            "npc_id": "storm_warden_wynn",
            "name": "Storm-warden Wynn",
            "flavor": "Two skies, one stride.",
            "bio": (
                "FLUX epic. prism_chimera anchors an all-elements team where every "
                "monster fires ON_BATTLE_START. echo_lich rare adds ON_ALLY_DEATH "
                "AND ON_BATTLE_START AND ON_ATTACK — a triple-trigger payoff."
            ),
            "loadout": [
                "prism_chimera",    # NATURE EPIC FLUX anchor
                "echo_lich",        # VOID R triple-trigger
                "prism_grove",      # NATURE U FLUX ON_BATTLE_START hp=30
                "shade_prismatic",  # VOID U FLUX ON_BATTLE_START
                "spectral_charge",  # VOLT U FLUX ON_BATTLE_START
                "tide_synth",       # WATER U FLUX ON_BATTLE_START
            ],
        },
    ],

    # ---------------------------------------------------------------- CHAMPION
    "champion": [
        {
            "npc_id": "apex_king_atlas",
            "name": "Apex-king Atlas",
            "flavor": "Speed and spear.",
            "bio": (
                "tempest_apex (atk=14 spd=14) is the fastest legendary alive. "
                "arc_predator epic doubles the kill-chain. pyrotyrant rare adds "
                "FIRE finisher coverage so even WATER walls melt. Four rares + epic "
                "+ legendary — tries to end the match before round 2."
            ),
            "loadout": [
                "tempest_apex",     # VOLT LEGE anchor
                "arc_predator",     # VOLT EPIC
                "storm_celestial",  # VOLT R (atk=14 spd=12) ON_BATTLE_START + ON_ATTACK + ON_ALLY_DEATH
                "plasma_djinn",     # VOLT R (atk=12 spd=11) ON_BATTLE_START + ON_ATTACK
                "pyrotyrant",       # FIRE R (atk=15) — cross-element finisher vs WATER walls
                "stormhare",        # VOLT R high-tempo
            ],
        },
        {
            "npc_id": "doom_paw_doppia",
            "name": "Doom-paw Doppia",
            "flavor": "A mouth on each side.",
            "bio": (
                "world_eater (atk=14 hp=42, ON_BATTLE_START ×2 + ON_ATTACK) eats "
                "the field whole. worldroot_colossus (def=14 hp=42) walls the "
                "answer-shots. crypt_wraith epic + nullhound + echo_lich + "
                "moss_titan round out the cross-element bruiser core. Designed "
                "for total board control — no individual unit gets to live."
            ),
            "loadout": [
                "world_eater",         # VOID LEGE anchor (FLUX)
                "crypt_wraith",        # VOID EPIC REVENANT
                "worldroot_colossus",  # NATURE R wall (def=14 hp=42) ON_DEATH revenge
                "moss_titan",          # NATURE R wall (def=11 hp=34)
                "nullhound",           # VOID R (atk=12) ON_ATTACK + ON_DEATH
                "echo_lich",           # VOID R triple-trigger
            ],
        },
        {
            "npc_id": "mythbreaker_marn",
            "name": "Mythbreaker Marn",
            "flavor": "Old kings die loudly.",
            "bio": (
                "voidking_morr (ON_BATTLE_START + ON_ALLY_DEATH + ON_DEATH) is the "
                "deepest legendary in the pool. crypt_wraith epic + four VOID rares "
                "(echo_lich, nullhound, riftwraith, mindroot) turn every death into "
                "a wave of payoffs. The most synergistic champion in the field."
            ),
            "loadout": [
                "voidking_morr",  # VOID LEGE anchor (3 triggers)
                "crypt_wraith",   # VOID EPIC REVENANT
                "echo_lich",      # VOID R triple-trigger (atk=11 def=7 hp=28)
                "nullhound",      # VOID R (atk=12 spd=9) ON_ATTACK + ON_DEATH
                "riftwraith",     # VOID R ON_DEATH + ON_BATTLE_START
                "aegis_lion",     # NORMAL R (def=8 hp=30) wall vs VOLT
            ],
        },
        {
            "npc_id": "stormcrown_sienna",
            "name": "Stormcrown Sienna",
            "flavor": "She walks in fire.",
            "bio": (
                "magma_tyrant (atk=14 hp=30) is the cleanest stat-anchor in the "
                "pool. solar_phoenix epic + molten_drake + ashen_phoenix + "
                "inferno_lynx stack four FIRE rares of pure burn pressure. Every "
                "kill costs you HP via phoenix ON_DEATH revenge."
            ),
            "loadout": [
                "magma_tyrant",     # FIRE LEGE anchor
                "solar_phoenix",    # FIRE EPIC ON_OPENING_ATTACK + ON_DEATH
                "concord_phoenix",  # NORMAL EPIC (def=8 hp=36) wall vs VOLT
                "rainbow_drake",    # FIRE EPIC FLUX ON_BATTLE_START + ON_KILL
                "ashen_phoenix",    # FIRE R ON_DEATH + ON_BATTLE_START
                "inferno_lynx",     # FIRE R (atk=13 spd=10) ON_BATTLE_START + ON_ATTACK
            ],
        },
        {
            "npc_id": "voidwalker_vance",
            "name": "Voidwalker Vance",
            "flavor": "Walks where light won't.",
            "bio": (
                "tide_empress (atk=12 def=9 hp=36) is the bulkiest legendary. "
                "Vance pairs her with REVENANT engine — coral_augur epic stays at "
                "full HP behind two front-line tanks while VOID payoffs accumulate."
            ),
            "loadout": [
                "tide_empress",   # WATER LEGE anchor
                "coral_augur",    # WATER EPIC TIDAL
                "leviathan_prime",# WATER R (def=10 hp=38)
                "abyss_warden",   # VOID R ON_BATTLE_START + ON_ATTACK
                "echo_lich",      # VOID R triple-trigger
                "tidewyrm",       # WATER R ON_ROUND_START
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# TIER ORDER (for ranks + manifest order)
# ---------------------------------------------------------------------------

TIER_ORDER = ["rookie", "novice", "veteran", "elite", "champion"]
TIER_RANK = {t: i + 1 for i, t in enumerate(TIER_ORDER)}
TIER_LABEL = {
    "rookie": "Rookie",
    "novice": "Novice",
    "veteran": "Veteran",
    "elite": "Elite",
    "champion": "Champion",
}
TIER_RULE = {
    "rookie":   "All-commons or 5 commons + 1 uncommon. No deep synergy. Beginner-safe.",
    "novice":   "Commons + 2 uncommons, occasional rare. Light single-trigger setups.",
    "veteran":  "Rares anchor; 2-3 trigger cards in concert.",
    "elite":    "Epic anchor + rares; 3-4 triggers fire per round.",
    "champion": "Legendary anchor + epics + tight synergy. Endgame fight.",
}

ROSTER_VERSION = "v1_alpha"
ROSTER_DESCRIPTION = (
    "DAIMON V1 alpha NPC tier roster (Phase 5 retune against the 200-card v1_alpha pool). "
    "25 named opponents across 5 tiers (Rookie -> Champion). Each NPC's loadout is drawn "
    "from the v1_alpha catalog. Difficulty climbs by trigger-density, archetype synergy "
    "and rarity-anchored stats — never by raw stat inflation outside the catalog. "
    "Used by dm_match_npc + `daimon match-npc`."
)
