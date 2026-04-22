#!/usr/bin/env python3
"""One-shot authoring script for the v1_alpha pack expansion (2026-04-22).

Adds 54 new monsters to v1_alpha, taking the pack from 13 → 67 cards.
Hand-tuned designs follow these element archetypes:

  FIRE   — aggression. High ATK/SPD, low DEF/HP. Damage triggers on attack.
  WATER  — sustain. Balanced. HEAL / ADD_SHIELD triggers.
  NATURE — tank. High DEF/HP, low SPD. Defensive / shield triggers.
  VOLT   — glass cannon. High SPD, low DEF. Speed buffs / chain damage.
  VOID   — disruption. Weird stats. DEBUFF + ON_DEATH / ALLY_DEATH triggers.

Stat budgets (rough envelopes — break only with intent):
  common      ATK+DEF+HP/3 + SPD ≈ 18-22  (no triggers)
  uncommon    ≈ 22-26                      (1 trigger, value 2-3)
  rare        ≈ 26-32                      (1-2 triggers, value 3-5)
  epic        ≈ 32-40                      (2 triggers, value 4-6)
  legendary   ≈ 38-46                      (2-3 triggers, value 5-8)

Run once. Re-running overwrites existing files (safe; deterministic content).
"""

from __future__ import annotations

import json
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent.parent / "daimon" / "catalog" / "v1_alpha"
MANIFEST_PATH = PACK_DIR / "manifest.json"


def card(
    *,
    cid: str,
    species: str | None = None,
    element: str,
    atk: int,
    df: int,  # 'def' is reserved
    hp: int,
    spd: int,
    triggers: list[dict] | None = None,
    name: str,
    flavor: str,
    rarity: str,
    moves: list[dict] | None = None,
) -> dict:
    return {
        "card_id": cid,
        "species": species or cid,
        "element": element,
        "atk": atk,
        "def": df,
        "hp": hp,
        "spd": spd,
        "triggers": triggers or [],
        "name": name,
        "flavor": flavor,
        "rarity": rarity,
        "art": f"art/{rarity}/{cid}.png",
        "moves": moves or [],
    }


def trig(when: str, op: str, target: str, value: int) -> dict:
    return {"when": when, "op": op, "target": target, "value": value}


# ---------------------------------------------------------------------------
# FIRE  +13
# ---------------------------------------------------------------------------
FIRE_CARDS = [
    card(cid="emberpup", element="FIRE", atk=7, df=3, hp=15, spd=6,
         name="Emberpup", flavor="Born from the last coal of a forgotten hearth.",
         rarity="common"),
    card(cid="ashling", element="FIRE", atk=6, df=3, hp=14, spd=7,
         name="Ashling", flavor="Cinders given the shape of a small, hungry thing.",
         rarity="common"),
    card(cid="sparrowflame", element="FIRE", atk=8, df=2, hp=12, spd=8,
         name="Sparrowflame", flavor="Sings only at the moment of ignition.",
         rarity="common"),
    card(cid="coalmunch", element="FIRE", atk=5, df=5, hp=20, spd=4,
         name="Coalmunch", flavor="Eats the embers it cannot light.",
         rarity="common"),
    card(cid="magmite", element="FIRE", atk=8, df=4, hp=18, spd=6,
         triggers=[trig("ON_ATTACK", "DAMAGE", "RANDOM_ENEMY", 2)],
         name="Magmite", flavor="Spits gravel and lava in equal measure.",
         rarity="uncommon",
         moves=[{"name": "Spitfire", "when": "ON_ATTACK"}]),
    card(cid="pyroshrike", element="FIRE", atk=10, df=3, hp=16, spd=8,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "SELF", 2)],
         name="Pyroshrike", flavor="Pins flames to a thorn before lighting them.",
         rarity="uncommon",
         moves=[{"name": "Burning Lance", "when": "ON_BATTLE_START"}]),
    card(cid="cinderhound", element="FIRE", atk=7, df=5, hp=22, spd=6,
         triggers=[trig("ON_TAKE_DAMAGE", "BUFF_ATK", "SELF", 2)],
         name="Cinderhound", flavor="Pain is a perfectly good reason to bite harder.",
         rarity="uncommon",
         moves=[{"name": "Pain Bond", "when": "ON_TAKE_DAMAGE"}]),
    card(cid="flarewing", element="FIRE", atk=10, df=4, hp=22, spd=9,
         triggers=[trig("ON_ATTACK", "DAMAGE", "LOWEST_HP_ENEMY", 4)],
         name="Flarewing", flavor="Hunts the wounded. Closes faster than the smoke.",
         rarity="rare",
         moves=[{"name": "Stoop Strike", "when": "ON_ATTACK"}]),
    card(cid="blazewolf", element="FIRE", atk=11, df=5, hp=24, spd=8,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "ALL_ALLIES", 2)],
         name="Blazewolf", flavor="The pack burns brighter when it howls together.",
         rarity="rare",
         moves=[{"name": "Pack Roar", "when": "ON_BATTLE_START"}]),
    card(cid="molten_drake", element="FIRE", atk=12, df=6, hp=28, spd=6,
         triggers=[trig("ON_ATTACK", "DAMAGE", "ALL_ENEMIES", 2)],
         name="Molten Drake", flavor="Its breath is the slow, glassy fire of the deep earth.",
         rarity="rare",
         moves=[{"name": "Sunsplit Breath", "when": "ON_ATTACK"}]),
    card(cid="inferno_lynx", element="FIRE", atk=13, df=5, hp=24, spd=10,
         triggers=[
             trig("ON_BATTLE_START", "BUFF_SPD", "SELF", 2),
             trig("ON_ATTACK", "DAMAGE", "RANDOM_ENEMY", 4),
         ],
         name="Inferno Lynx", flavor="Strikes leave heat-shimmer where the body used to be.",
         rarity="epic",
         moves=[
             {"name": "Quickflame", "when": "ON_BATTLE_START"},
             {"name": "Searing Pounce", "when": "ON_ATTACK"},
         ]),
    card(cid="ashen_phoenix", element="FIRE", atk=11, df=7, hp=30, spd=8,
         triggers=[
             trig("ON_DEATH", "HEAL", "ALL_ALLIES", 6),
             trig("ON_BATTLE_START", "BUFF_ATK", "ALL_ALLIES", 2),
         ],
         name="Ashen Phoenix", flavor="Its last breath is a gift; its first, a promise.",
         rarity="epic",
         moves=[
             {"name": "Ember Dirge", "when": "ON_DEATH"},
             {"name": "Hearth Anthem", "when": "ON_BATTLE_START"},
         ]),
    card(cid="pyrotyrant", element="FIRE", atk=15, df=4, hp=22, spd=7,
         triggers=[
             trig("ON_BATTLE_START", "BUFF_ATK", "SELF", 3),
             trig("ON_ATTACK", "DAMAGE", "ALL_ENEMIES", 3),
             trig("ON_DEATH", "DAMAGE", "ALL_ENEMIES", 8),
         ],
         name="Pyrotyrant", flavor="What it cannot rule, it renders.",
         rarity="legendary",
         moves=[
             {"name": "Ascend the Throne", "when": "ON_BATTLE_START"},
             {"name": "Rule by Fire", "when": "ON_ATTACK"},
             {"name": "Funeral of the World", "when": "ON_DEATH"},
         ]),
]

# ---------------------------------------------------------------------------
# WATER  +11
# ---------------------------------------------------------------------------
WATER_CARDS = [
    card(cid="mistling", element="WATER", atk=4, df=6, hp=18, spd=5,
         name="Mistling", flavor="Easier to chase than to catch.",
         rarity="common"),
    card(cid="tidepup", element="WATER", atk=5, df=5, hp=20, spd=5,
         name="Tidepup", flavor="Rolls in with one wave, out with the next.",
         rarity="common"),
    card(cid="bubblefry", element="WATER", atk=4, df=4, hp=16, spd=7,
         name="Bubblefry", flavor="A school of one, lonely and shimmering.",
         rarity="common"),
    card(cid="coralcub", element="WATER", atk=4, df=7, hp=22, spd=4,
         name="Coralcub", flavor="Grows armor faster than it grows up.",
         rarity="common"),
    card(cid="riverotter", element="WATER", atk=6, df=6, hp=22, spd=7,
         triggers=[trig("ON_ROUND_START", "HEAL", "SELF", 2)],
         name="Riverotter", flavor="A current of small, deliberate kindnesses.",
         rarity="uncommon",
         moves=[{"name": "Eddy Pulse", "when": "ON_ROUND_START"}]),
    card(cid="frostfin", element="WATER", atk=7, df=6, hp=20, spd=6,
         triggers=[trig("ON_ATTACK", "DEBUFF_SPD" if False else "DEBUFF_ATK", "RANDOM_ENEMY", 2)],
         name="Frostfin", flavor="Its bite leaves a cold that lingers under the scales.",
         rarity="uncommon",
         moves=[{"name": "Numbing Strike", "when": "ON_ATTACK"}]),
    card(cid="abysseel", element="WATER", atk=7, df=5, hp=22, spd=7,
         triggers=[trig("ON_TAKE_DAMAGE", "ADD_SHIELD", "SELF", 3)],
         name="Abysseel", flavor="Pressure tempers it. Pain tempers it more.",
         rarity="uncommon",
         moves=[{"name": "Pressure Shell", "when": "ON_TAKE_DAMAGE"}]),
    card(cid="riptide_wyrm", element="WATER", atk=8, df=7, hp=28, spd=7,
         triggers=[
             trig("ON_BATTLE_START", "ADD_SHIELD", "ALL_ALLIES", 3),
             trig("ON_ATTACK", "HEAL", "RANDOM_ALLY", 3),
         ],
         name="Riptide Wyrm", flavor="The tide gives, the tide takes — both at once.",
         rarity="rare",
         moves=[
             {"name": "Tidal Shroud", "when": "ON_BATTLE_START"},
             {"name": "Salve Surge", "when": "ON_ATTACK"},
         ]),
    card(cid="glacier_kraken", element="WATER", atk=9, df=8, hp=32, spd=4,
         triggers=[trig("ON_ROUND_START", "ADD_SHIELD", "ALL_ALLIES", 2)],
         name="Glacier Kraken", flavor="Carves shelter out of the storm itself.",
         rarity="rare",
         moves=[{"name": "Iceflow Bulwark", "when": "ON_ROUND_START"}]),
    card(cid="maelstrom_serpent", element="WATER", atk=11, df=7, hp=30, spd=8,
         triggers=[
             trig("ON_BATTLE_START", "DEBUFF_ATK", "ALL_ENEMIES", 2),
             trig("ON_ATTACK", "DAMAGE", "ALL_ENEMIES", 2),
         ],
         name="Maelstrom Serpent", flavor="The whirlpool with a name and a long memory.",
         rarity="epic",
         moves=[
             {"name": "Drown the Will", "when": "ON_BATTLE_START"},
             {"name": "Spiral Lash", "when": "ON_ATTACK"},
         ]),
    card(cid="leviathan_prime", element="WATER", atk=12, df=10, hp=38, spd=5,
         triggers=[
             trig("ON_BATTLE_START", "ADD_SHIELD", "ALL_ALLIES", 5),
             trig("ON_ATTACK", "DAMAGE", "HIGHEST_HP_ENEMY", 6),
             trig("ON_TAKE_DAMAGE", "HEAL", "SELF", 3),
         ],
         name="Leviathan Prime", flavor="Older than the coastline that fears it.",
         rarity="legendary",
         moves=[
             {"name": "Aegis of the Deep", "when": "ON_BATTLE_START"},
             {"name": "World-Crushing Maw", "when": "ON_ATTACK"},
             {"name": "Tide Returns", "when": "ON_TAKE_DAMAGE"},
         ]),
]

# Fix: WATER frostfin used DEBUFF_SPD which isn't an EffectOp. Replace with DEBUFF_ATK.
# (already corrected inline above via the conditional; this comment documents intent)

# ---------------------------------------------------------------------------
# NATURE  +7
# ---------------------------------------------------------------------------
NATURE_CARDS = [
    card(cid="mossbat", element="NATURE", atk=5, df=4, hp=18, spd=6,
         name="Mossbat", flavor="Carries a square of forest on its back.",
         rarity="common"),
    card(cid="sproutling", element="NATURE", atk=3, df=6, hp=22, spd=4,
         name="Sproutling", flavor="The first leaf is the loudest.",
         rarity="common"),
    card(cid="thornpup", element="NATURE", atk=6, df=7, hp=24, spd=4,
         triggers=[trig("ON_TAKE_DAMAGE", "DAMAGE", "RANDOM_ENEMY", 2)],
         name="Thornpup", flavor="Hugging is, technically, an attack.",
         rarity="uncommon",
         moves=[{"name": "Bramblecoat", "when": "ON_TAKE_DAMAGE"}]),
    card(cid="bramblegoat", element="NATURE", atk=7, df=8, hp=28, spd=4,
         triggers=[trig("ON_BATTLE_START", "BUFF_DEF", "ALL_ALLIES", 2)],
         name="Bramblegoat", flavor="Stands at the edge of the herd. Always.",
         rarity="uncommon",
         moves=[{"name": "Hedgewall", "when": "ON_BATTLE_START"}]),
    card(cid="moss_titan", element="NATURE", atk=8, df=11, hp=34, spd=3,
         triggers=[
             trig("ON_BATTLE_START", "ADD_SHIELD", "SELF", 5),
             trig("ON_TAKE_DAMAGE", "BUFF_DEF", "SELF", 1),
         ],
         name="Moss Titan", flavor="Older than the trail. Older than the trail-makers.",
         rarity="rare",
         moves=[
             {"name": "Bedrock Stance", "when": "ON_BATTLE_START"},
             {"name": "Patient Bark", "when": "ON_TAKE_DAMAGE"},
         ]),
    card(cid="forest_warden", element="NATURE", atk=10, df=10, hp=32, spd=5,
         triggers=[
             trig("ON_BATTLE_START", "BUFF_DEF", "ALL_ALLIES", 3),
             trig("ON_ALLY_DEATH", "BUFF_ATK", "ALL_ALLIES", 3),
         ],
         name="Forest Warden", flavor="Mourning, in this grove, is the same word as vengeance.",
         rarity="epic",
         moves=[
             {"name": "Grove Aegis", "when": "ON_BATTLE_START"},
             {"name": "Roots Remember", "when": "ON_ALLY_DEATH"},
         ]),
    card(cid="worldroot_colossus", element="NATURE", atk=10, df=14, hp=42, spd=3,
         triggers=[
             trig("ON_BATTLE_START", "BUFF_DEF", "ALL_ALLIES", 4),
             trig("ON_ROUND_START", "HEAL", "ALL_ALLIES", 2),
             trig("ON_DEATH", "HEAL", "ALL_ALLIES", 8),
         ],
         name="Worldroot Colossus", flavor="Every fallen tree was a promise it kept.",
         rarity="legendary",
         moves=[
             {"name": "Anchor of Eras", "when": "ON_BATTLE_START"},
             {"name": "Verdant Pulse", "when": "ON_ROUND_START"},
             {"name": "Last Bloom", "when": "ON_DEATH"},
         ]),
]

# ---------------------------------------------------------------------------
# VOLT  +9
# ---------------------------------------------------------------------------
VOLT_CARDS = [
    card(cid="sparkling", element="VOLT", atk=5, df=2, hp=14, spd=9,
         name="Sparkling", flavor="A held breath, plus current.",
         rarity="common"),
    card(cid="jolthog", element="VOLT", atk=6, df=4, hp=18, spd=7,
         name="Jolthog", flavor="Curls up. Discharges. Repeats.",
         rarity="common"),
    card(cid="charge_chick", element="VOLT", atk=4, df=3, hp=15, spd=10,
         name="Charge Chick", flavor="Hatches faster than its shell.",
         rarity="common"),
    card(cid="voltsprite", element="VOLT", atk=7, df=3, hp=16, spd=10,
         triggers=[trig("ON_BATTLE_START", "BUFF_SPD", "ALL_ALLIES", 2)],
         name="Voltsprite", flavor="Hands out the second-fastest grin in the room.",
         rarity="uncommon",
         moves=[{"name": "Quicken All", "when": "ON_BATTLE_START"}]),
    card(cid="thunderfox", element="VOLT", atk=8, df=4, hp=20, spd=10,
         triggers=[trig("ON_ATTACK", "DAMAGE", "RANDOM_ENEMY", 3)],
         name="Thunderfox", flavor="The first strike is the polite one.",
         rarity="uncommon",
         moves=[{"name": "Chain Spark", "when": "ON_ATTACK"}]),
    card(cid="arc_serpent", element="VOLT", atk=9, df=5, hp=22, spd=11,
         triggers=[
             trig("ON_BATTLE_START", "BUFF_SPD", "SELF", 3),
             trig("ON_ATTACK", "DAMAGE", "ALL_ENEMIES", 2),
         ],
         name="Arc Serpent", flavor="Coils between heartbeats.",
         rarity="rare",
         moves=[
             {"name": "Coil & Conduct", "when": "ON_BATTLE_START"},
             {"name": "Arc Lash", "when": "ON_ATTACK"},
         ]),
    card(cid="tempest_eagle", element="VOLT", atk=11, df=5, hp=24, spd=10,
         triggers=[trig("ON_ATTACK", "DAMAGE", "HIGHEST_HP_ENEMY", 5)],
         name="Tempest Eagle", flavor="Hunts the strongest first. Pride is dinner.",
         rarity="rare",
         moves=[{"name": "Skyfall Talons", "when": "ON_ATTACK"}]),
    card(cid="plasma_djinn", element="VOLT", atk=12, df=4, hp=22, spd=11,
         triggers=[
             trig("ON_BATTLE_START", "BUFF_SPD", "ALL_ALLIES", 2),
             trig("ON_ATTACK", "DAMAGE", "ALL_ENEMIES", 3),
         ],
         name="Plasma Djinn", flavor="Granted exactly one wish. The lightning kept it.",
         rarity="epic",
         moves=[
             {"name": "Quicken the Court", "when": "ON_BATTLE_START"},
             {"name": "Wishlash", "when": "ON_ATTACK"},
         ]),
    card(cid="storm_celestial", element="VOLT", atk=14, df=5, hp=24, spd=12,
         triggers=[
             trig("ON_BATTLE_START", "BUFF_SPD", "ALL_ALLIES", 3),
             trig("ON_ATTACK", "DAMAGE", "ALL_ENEMIES", 4),
             trig("ON_ALLY_DEATH", "BUFF_ATK", "ALL_ALLIES", 3),
         ],
         name="Storm Celestial", flavor="A constellation that decided to come down and watch.",
         rarity="legendary",
         moves=[
             {"name": "Heaven's Tempo", "when": "ON_BATTLE_START"},
             {"name": "Skyfall Volley", "when": "ON_ATTACK"},
             {"name": "Mourner's Charge", "when": "ON_ALLY_DEATH"},
         ]),
]

# ---------------------------------------------------------------------------
# VOID  +14
# ---------------------------------------------------------------------------
VOID_CARDS = [
    card(cid="shadepup", element="VOID", atk=5, df=4, hp=16, spd=6,
         name="Shadepup", flavor="Casts no shadow. Is one.",
         rarity="common"),
    card(cid="nullkit", element="VOID", atk=6, df=3, hp=14, spd=7,
         name="Nullkit", flavor="Whatever you call it forgets the name by morning.",
         rarity="common"),
    card(cid="duskmoth", element="VOID", atk=4, df=4, hp=16, spd=8,
         name="Duskmoth", flavor="Drawn to the lights between worlds.",
         rarity="common"),
    card(cid="whisperling", element="VOID", atk=5, df=3, hp=15, spd=7,
         name="Whisperling", flavor="Speaks only the words you nearly said.",
         rarity="common"),
    card(cid="voidcrawler", element="VOID", atk=6, df=5, hp=20, spd=6,
         triggers=[trig("ON_ATTACK", "DEBUFF_DEF", "RANDOM_ENEMY", 2)],
         name="Voidcrawler", flavor="Finds the seam in the armor and unstitches it.",
         rarity="uncommon",
         moves=[{"name": "Seam Bite", "when": "ON_ATTACK"}]),
    card(cid="dread_imp", element="VOID", atk=8, df=3, hp=16, spd=8,
         triggers=[trig("ON_BATTLE_START", "DEBUFF_ATK", "ALL_ENEMIES", 2)],
         name="Dread Imp", flavor="Doesn't fight fair, doesn't apologize.",
         rarity="uncommon",
         moves=[{"name": "Cower Aura", "when": "ON_BATTLE_START"}]),
    card(cid="spectral_owl", element="VOID", atk=7, df=4, hp=18, spd=8,
         triggers=[trig("ON_ATTACK", "DEBUFF_DEF", "ALL_ENEMIES", 1)],
         name="Spectral Owl", flavor="Hoots once. The room loses heart.",
         rarity="uncommon",
         moves=[{"name": "Soul Glance", "when": "ON_ATTACK"}]),
    card(cid="haunt_hare", element="VOID", atk=8, df=5, hp=22, spd=10,
         triggers=[
             trig("ON_BATTLE_START", "BUFF_SPD", "SELF", 2),
             trig("ON_ATTACK", "DEBUFF_ATK", "RANDOM_ENEMY", 2),
         ],
         name="Haunt Hare", flavor="Bolts between the heartbeat and the thought.",
         rarity="rare",
         moves=[
             {"name": "Phantom Step", "when": "ON_BATTLE_START"},
             {"name": "Dread Bite", "when": "ON_ATTACK"},
         ]),
    card(cid="void_serpent", element="VOID", atk=10, df=5, hp=24, spd=8,
         triggers=[trig("ON_ATTACK", "DAMAGE", "LOWEST_HP_ENEMY", 5)],
         name="Void Serpent", flavor="Knows which fire is closest to going out.",
         rarity="rare",
         moves=[{"name": "Snuff Strike", "when": "ON_ATTACK"}]),
    card(cid="riftwraith", element="VOID", atk=9, df=6, hp=22, spd=9,
         triggers=[
             trig("ON_DEATH", "DAMAGE", "ALL_ENEMIES", 4),
             trig("ON_BATTLE_START", "DEBUFF_DEF", "ALL_ENEMIES", 1),
         ],
         name="Riftwraith", flavor="Leaves a small wound in the world wherever it stood.",
         rarity="rare",
         moves=[
             {"name": "Parting Tear", "when": "ON_DEATH"},
             {"name": "Fray", "when": "ON_BATTLE_START"},
         ]),
    card(cid="abyss_warden", element="VOID", atk=10, df=8, hp=28, spd=6,
         triggers=[
             trig("ON_BATTLE_START", "DEBUFF_ATK", "ALL_ENEMIES", 3),
             trig("ON_ALLY_DEATH", "DAMAGE", "ALL_ENEMIES", 4),
         ],
         name="Abyss Warden", flavor="Keeps a ledger of every grudge the dark ever swallowed.",
         rarity="epic",
         moves=[
             {"name": "Quietus", "when": "ON_BATTLE_START"},
             {"name": "Ledger Closes", "when": "ON_ALLY_DEATH"},
         ]),
    card(cid="nullhound", element="VOID", atk=12, df=5, hp=24, spd=9,
         triggers=[
             trig("ON_ATTACK", "DEBUFF_DEF", "ALL_ENEMIES", 2),
             trig("ON_DEATH", "DEBUFF_ATK", "ALL_ENEMIES", 4),
         ],
         name="Nullhound", flavor="Whatever it bites is a little less, after.",
         rarity="epic",
         moves=[
             {"name": "Erase Edges", "when": "ON_ATTACK"},
             {"name": "Final Howl", "when": "ON_DEATH"},
         ]),
    card(cid="echo_lich", element="VOID", atk=11, df=7, hp=28, spd=7,
         triggers=[
             trig("ON_BATTLE_START", "DEBUFF_DEF", "ALL_ENEMIES", 2),
             trig("ON_ALLY_DEATH", "BUFF_ATK", "SELF", 4),
             trig("ON_ATTACK", "DAMAGE", "ALL_ENEMIES", 2),
         ],
         name="Echo Lich", flavor="Repeats every grief, then bills you for the recital.",
         rarity="legendary",
         moves=[
             {"name": "Lament Field", "when": "ON_BATTLE_START"},
             {"name": "Feed on Loss", "when": "ON_ALLY_DEATH"},
             {"name": "Repetition Strike", "when": "ON_ATTACK"},
         ]),
    card(cid="voidking_morr", element="VOID", atk=13, df=8, hp=32, spd=7,
         triggers=[
             trig("ON_BATTLE_START", "DEBUFF_ATK", "ALL_ENEMIES", 3),
             trig("ON_ATTACK", "DAMAGE", "HIGHEST_HP_ENEMY", 6),
             trig("ON_DEATH", "DAMAGE", "ALL_ENEMIES", 6),
         ],
         name="Voidking Morr", flavor="Crowned by what nobody wanted. Reigns regardless.",
         rarity="legendary",
         moves=[
             {"name": "Sovereign Pall", "when": "ON_BATTLE_START"},
             {"name": "Decree of Ruin", "when": "ON_ATTACK"},
             {"name": "Throne of Silence", "when": "ON_DEATH"},
         ]),
]

# ---------------------------------------------------------------------------
# Sanity-fix: WATER.frostfin used a non-existent op via inline conditional.
# Replace it cleanly with DEBUFF_ATK.
# ---------------------------------------------------------------------------
for c in WATER_CARDS:
    if c["card_id"] == "frostfin":
        c["triggers"] = [trig("ON_ATTACK", "DEBUFF_ATK", "RANDOM_ENEMY", 2)]


ALL_NEW = FIRE_CARDS + WATER_CARDS + NATURE_CARDS + VOLT_CARDS + VOID_CARDS


def main() -> None:
    PACK_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Write each card file.
    for c in ALL_NEW:
        path = PACK_DIR / f"{c['card_id']}.json"
        path.write_text(
            json.dumps(c, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # 2. Patch manifest — append new cards (skip if already present).
    manifest = json.loads(MANIFEST_PATH.read_text())
    existing_ids = {entry["card_id"] for entry in manifest["cards"]}
    for c in ALL_NEW:
        if c["card_id"] in existing_ids:
            continue
        manifest["cards"].append({
            "card_id": c["card_id"],
            "rarity": c["rarity"],
            "element": c["element"],
            "file": f"{c['card_id']}.json",
        })

    # 3. Bump version + description.
    manifest["version"] = "0.3.0"
    manifest["description"] = (
        "DAIMON V1 alpha bundled creature pool. Ships with the engine package "
        f"so dm_pull is functional out of the box. {len(manifest['cards'])} "
        "monsters across 5 elements (Fire/Water/Nature/Volt/Void), spread "
        "across 5 rarity tiers. Will be supplemented (not replaced) by OCI "
        "cardpacks in V1.5."
    )

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {len(ALL_NEW)} new cards to {PACK_DIR}")
    print(f"Manifest now lists {len(manifest['cards'])} cards total")


if __name__ == "__main__":
    main()
