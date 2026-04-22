#!/usr/bin/env python3
"""Phase 4b of V1 card design — author 75 new commons.

Brings the v1_alpha pack from 80 → 155 cards, hitting the V1 common target
of 98 (existing 23 + 75 new). Distribution per element after this script:

  FIRE:   5 → 20 commons (+15)
  WATER:  5 → 20 commons (+15)
  NATURE: 5 → 20 commons (+15)
  VOLT:   4 → 19 commons (+15)
  VOID:   4 → 19 commons (+15)
  Total:  23 → 98 commons (+75)  ✅ matches §3 V1 target

Design rules (locked in `docs/card_design_v1.md` §4):
  - Stat budget atk + def + hp/3 + spd ∈ [18, 22]
  - 0–1 triggers (this script gives EVERY new common exactly 1 trigger so we
    don't bloat the vanilla pool — current 23 vanilla / 98 target = 23.5%,
    which already sits comfortably under the 30% cap from §4)
  - Trigger values 2–3 (DOT durations also count; STUN value=1 is the only
    sensible STUN value but it's reserved for rare/epic — commons skip STUN
    and SILENCE entirely to avoid the "common with epic-tier disable" trap)

Per-element archetype mix (13 archetype-pure + 2 FLUX = 15 each):
  FIRE:   13 INFERNO + 2 FLUX (host=FIRE)
  WATER:  13 TIDAL   + 2 FLUX (host=WATER)
  NATURE: 13 BULWARK + 2 FLUX (host=NATURE)
  VOLT:   13 STORMCHAIN + 2 FLUX (host=VOLT)
  VOID:   13 REVENANT + 2 FLUX (host=VOID)

FLUX commons gate their trigger on `team.distinct_elements >= 2` (entry-tier
of the FLUX condition ladder; `>=3` and `>=4` reserved for higher rarities).

Species: each new common is its own species (singleton) for now. Phase 4c
authors uncommons that intentionally extend selected common lines into 2-tier
(C → U) species via the `species` field. Keeping commons singleton-by-default
means Phase 4c has full freedom to pick which lines deserve evolution.

Run once, idempotent (overwrites JSONs by design). Re-running on a pack that
already has these 75 entries is safe — manifest dedupe via card_id check.
"""

from __future__ import annotations

import json
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent.parent / "daimon" / "catalog" / "v1_alpha"
MANIFEST_PATH = PACK_DIR / "manifest.json"


# ---------------------------------------------------------------------------
# Helpers (mirror the Phase-3 author script).
# ---------------------------------------------------------------------------

def trig(when: str, op: str, target: str, value: int,
         condition: str | None = None) -> dict:
    t = {"when": when, "op": op, "target": target, "value": value}
    if condition is not None:
        t["condition"] = condition
    return t


def card(
    *,
    cid: str,
    element: str,
    atk: int,
    df: int,
    hp: int,
    spd: int,
    triggers: list[dict],
    name: str,
    flavor: str,
    archetype: str,
) -> dict:
    return {
        "card_id": cid,
        "species": cid,                       # singleton species (see module docstring)
        "element": element,
        "atk": atk,
        "def": df,
        "hp": hp,
        "spd": spd,
        "triggers": triggers,
        "name": name,
        "flavor": flavor,
        "rarity": "common",
        "archetype": archetype,
        "art": f"art/common/{cid}.png",
        "moves": [],
    }


# Convenience constants for the FLUX 2-element gate. Conditional gates above
# `>=2` are reserved for higher rarities (epic/legendary anchors already use
# `>=3` and `>=4`; see Phase-3 anchors).
FLUX2 = "team.distinct_elements >= 2"


# ---------------------------------------------------------------------------
# FIRE — 13 INFERNO + 2 FLUX
# Stat profile: aggressive (high atk/spd, low def/hp); budget 18-22.
# ---------------------------------------------------------------------------

FIRE_COMMONS = [
    card(cid="flickerimp", element="FIRE", atk=6, df=2, hp=18, spd=6,
         triggers=[trig("ON_ATTACK", "DAMAGE", "RANDOM_ENEMY", 2)],
         name="Flickerimp", flavor="A spite the size of a candle.",
         archetype="INFERNO"),
    card(cid="embershrew", element="FIRE", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_KILL", "BUFF_ATK", "SELF", 2)],
         name="Embershrew", flavor="Bites once. Then twice as hard.",
         archetype="INFERNO"),
    card(cid="coalwhelp", element="FIRE", atk=6, df=3, hp=21, spd=4,
         triggers=[trig("ON_OPENING_ATTACK", "DAMAGE", "LOWEST_HP_ENEMY", 3)],
         name="Coalwhelp", flavor="Saves its hottest breath for the smallest target.",
         archetype="INFERNO"),
    card(cid="ashpup", element="FIRE", atk=5, df=2, hp=18, spd=7,
         triggers=[trig("ON_ATTACK", "APPLY_BURN", "RANDOM_ENEMY", 2)],
         name="Ashpup", flavor="Marks the wounded with smouldering bites.",
         archetype="INFERNO"),
    card(cid="magmaling", element="FIRE", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_KILL", "DAMAGE", "RANDOM_ENEMY", 2)],
         name="Magmaling", flavor="When a thing dies, the lava splashes.",
         archetype="INFERNO"),
    card(cid="soot_finch", element="FIRE", atk=4, df=2, hp=18, spd=8,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "SELF", 2)],
         name="Soot Finch", flavor="Sings the kettle into boiling.",
         archetype="INFERNO"),
    card(cid="cindermote", element="FIRE", atk=4, df=3, hp=18, spd=7,
         triggers=[trig("ON_TURN_END", "DAMAGE", "RANDOM_ENEMY", 2)],
         name="Cindermote", flavor="The spark that lingers after the swing.",
         archetype="INFERNO"),
    card(cid="brimling", element="FIRE", atk=5, df=2, hp=18, spd=7,
         triggers=[trig("ON_OPENING_ATTACK", "APPLY_BURN", "RANDOM_ENEMY", 2)],
         name="Brimling", flavor="Its first breath is the one to remember.",
         archetype="INFERNO"),
    card(cid="ignis_kit", element="FIRE", atk=6, df=2, hp=15, spd=7,
         triggers=[trig("ON_LOW_HP", "DAMAGE", "ALL_ENEMIES", 3)],
         name="Ignis Kit", flavor="Cornered fire is the kind that takes the room.",
         archetype="INFERNO"),
    card(cid="flarefly", element="FIRE", atk=4, df=3, hp=21, spd=6,
         triggers=[trig("ON_ATTACK", "DAMAGE", "LOWEST_HP_ENEMY", 3)],
         name="Flarefly", flavor="Always finds the wounded one.",
         archetype="INFERNO"),
    card(cid="emberhawk", element="FIRE", atk=5, df=2, hp=18, spd=7,
         triggers=[trig("ON_OPENING_ATTACK", "DAMAGE", "ALL_ENEMIES", 2)],
         name="Emberhawk", flavor="Dives once on every battlefield it sees.",
         archetype="INFERNO"),
    card(cid="cinder_serpent", element="FIRE", atk=6, df=3, hp=21, spd=4,
         triggers=[trig("ON_ATTACK", "DAMAGE", "RANDOM_ENEMY", 3)],
         name="Cinder Serpent", flavor="Coils through smoke; strikes from heat-haze.",
         archetype="INFERNO"),
    card(cid="lava_skink", element="FIRE", atk=4, df=4, hp=21, spd=5,
         triggers=[trig("ON_TAKE_DAMAGE", "APPLY_BURN", "RANDOM_ENEMY", 2)],
         name="Lava Skink", flavor="Strike it; it leaves embers under your fingernails.",
         archetype="INFERNO"),
    card(cid="flame_chimerlet", element="FIRE", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "SELF", 2,
                        condition=FLUX2)],
         name="Flame Chimerlet",
         flavor="Two heads, two tempers, one shared appetite.",
         archetype="FLUX"),
    card(cid="sunscale_drake", element="FIRE", atk=4, df=4, hp=21, spd=5,
         triggers=[trig("ON_BATTLE_START", "HEAL", "ALL_ALLIES", 2,
                        condition=FLUX2)],
         name="Sunscale Drake",
         flavor="Carries the warmth of every weather it has flown.",
         archetype="FLUX"),
]


# ---------------------------------------------------------------------------
# WATER — 13 TIDAL + 2 FLUX
# Stat profile: balanced/sustaining (medium atk/def, healthy hp); budget 18-22.
# ---------------------------------------------------------------------------

WATER_COMMONS = [
    card(cid="brineling", element="WATER", atk=4, df=4, hp=21, spd=5,
         triggers=[trig("ON_ATTACK", "LIFESTEAL", "HIGHEST_HP_ENEMY", 2)],
         name="Brineling", flavor="Drinks the salt from every wound.",
         archetype="TIDAL"),
    card(cid="tidefry", element="WATER", atk=4, df=3, hp=21, spd=6,
         triggers=[trig("ON_BATTLE_START", "HEAL", "ALL_ALLIES", 2)],
         name="Tidefry", flavor="Rolls in with the morning current.",
         archetype="TIDAL"),
    card(cid="seapup", element="WATER", atk=4, df=4, hp=21, spd=5,
         triggers=[trig("ON_ATTACK", "HEAL", "RANDOM_ALLY", 2)],
         name="Seapup", flavor="Bites the foe; spits the bandage.",
         archetype="TIDAL"),
    card(cid="dewfin", element="WATER", atk=5, df=3, hp=21, spd=5,
         triggers=[trig("ON_TURN_END", "HEAL", "SELF", 2)],
         name="Dewfin", flavor="Ends every move a little newer than it began.",
         archetype="TIDAL"),
    card(cid="mistray", element="WATER", atk=3, df=5, hp=21, spd=5,
         triggers=[trig("ON_TAKE_DAMAGE", "HEAL", "SELF", 2)],
         name="Mist Ray", flavor="Wears its own fog as a balm.",
         archetype="TIDAL"),
    card(cid="coralwhelp", element="WATER", atk=4, df=4, hp=21, spd=5,
         triggers=[trig("ON_KILL", "HEAL", "ALL_ALLIES", 2)],
         name="Coralwhelp", flavor="Each kill blooms a brief reef inside the team.",
         archetype="TIDAL"),
    card(cid="shellfin", element="WATER", atk=3, df=5, hp=21, spd=5,
         triggers=[trig("ON_TAKE_DAMAGE", "ADD_SHIELD", "SELF", 2)],
         name="Shellfin", flavor="Grows another plate every time it is asked.",
         archetype="TIDAL"),
    card(cid="abyss_minnow", element="WATER", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_ATTACK", "LIFESTEAL", "RANDOM_ENEMY", 2)],
         name="Abyss Minnow", flavor="Smaller things below feed on smaller things still.",
         archetype="TIDAL"),
    card(cid="spring_otter", element="WATER", atk=4, df=3, hp=21, spd=6,
         triggers=[trig("ON_BATTLE_START", "HEAL", "SELF", 3)],
         name="Spring Otter", flavor="Begins each tale already washed.",
         archetype="TIDAL"),
    card(cid="krakenling", element="WATER", atk=5, df=4, hp=21, spd=4,
         triggers=[trig("ON_ATTACK", "DAMAGE", "LOWEST_HP_ENEMY", 2)],
         name="Krakenling", flavor="Eight tantrums in a child-sized package.",
         archetype="TIDAL"),
    card(cid="saltsprite", element="WATER", atk=3, df=4, hp=21, spd=6,
         triggers=[trig("ON_ROUND_START", "HEAL", "RANDOM_ALLY", 2,
                        condition="round >= 2")],
         name="Saltsprite", flavor="Patient with the wound; impatient with the bleeding.",
         archetype="TIDAL"),
    card(cid="surfling", element="WATER", atk=4, df=3, hp=21, spd=6,
         triggers=[trig("ON_OPENING_ATTACK", "HEAL", "SELF", 3)],
         name="Surfling", flavor="The first wave restores it; the rest are for show.",
         archetype="TIDAL"),
    card(cid="tide_imp", element="WATER", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_ATTACK", "LIFESTEAL", "LOWEST_HP_ENEMY", 2)],
         name="Tide Imp", flavor="Picks the easiest cup to drink from.",
         archetype="TIDAL"),
    card(cid="mistchimera", element="WATER", atk=4, df=4, hp=21, spd=5,
         triggers=[trig("ON_BATTLE_START", "HEAL", "ALL_ALLIES", 2,
                        condition=FLUX2)],
         name="Mist Chimera",
         flavor="Three throats, one mercy — when the team isn't already alone.",
         archetype="FLUX"),
    card(cid="tidemerger", element="WATER", atk=5, df=3, hp=21, spd=5,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "SELF", 2,
                        condition=FLUX2)],
         name="Tidemerger",
         flavor="Borrows menace from any element kept nearby.",
         archetype="FLUX"),
]


# ---------------------------------------------------------------------------
# NATURE — 13 BULWARK + 2 FLUX
# Stat profile: defensive (low atk, high def/hp, low spd); budget 18-22.
# ---------------------------------------------------------------------------

NATURE_COMMONS = [
    card(cid="mossling", element="NATURE", atk=3, df=5, hp=24, spd=3,
         triggers=[trig("ON_TAKE_DAMAGE", "ADD_SHIELD", "SELF", 2)],
         name="Mossling", flavor="Patches itself with whatever the rock will spare.",
         archetype="BULWARK"),
    card(cid="barkpup", element="NATURE", atk=3, df=6, hp=21, spd=4,
         triggers=[trig("ON_BATTLE_START", "BUFF_DEF", "SELF", 2)],
         name="Barkpup", flavor="Hardens at the first whiff of trouble.",
         archetype="BULWARK"),
    card(cid="sproutkin", element="NATURE", atk=3, df=5, hp=24, spd=3,
         triggers=[trig("ON_TAKE_DAMAGE", "HEAL", "SELF", 2)],
         name="Sproutkin", flavor="Roots that drink the blow and grow on it.",
         archetype="BULWARK"),
    card(cid="thornling", element="NATURE", atk=4, df=4, hp=21, spd=4,
         triggers=[trig("ON_TAKE_DAMAGE", "DAMAGE", "RANDOM_ENEMY", 2)],
         name="Thornling", flavor="The first lesson of hedgerows.",
         archetype="BULWARK"),
    card(cid="ironseed", element="NATURE", atk=3, df=6, hp=21, spd=4,
         triggers=[trig("ON_BATTLE_START", "APPLY_TAUNT", "SELF", 2)],
         name="Ironseed", flavor="Roots first; lets everything else grow around it.",
         archetype="BULWARK"),
    card(cid="boulder_mole", element="NATURE", atk=3, df=5, hp=21, spd=4,
         triggers=[trig("ON_TAKE_DAMAGE", "BUFF_DEF", "SELF", 2)],
         name="Boulder Mole", flavor="Tunnels deeper every time the ground complains.",
         archetype="BULWARK"),
    card(cid="brambleling", element="NATURE", atk=4, df=4, hp=21, spd=4,
         triggers=[trig("ON_BATTLE_START", "BUFF_DEF", "ALL_ALLIES", 2)],
         name="Brambleling", flavor="Sets the hedge before the attackers know there is one.",
         archetype="BULWARK"),
    card(cid="forest_cub", element="NATURE", atk=3, df=5, hp=21, spd=5,
         triggers=[trig("ON_TURN_END", "HEAL", "SELF", 2)],
         name="Forest Cub", flavor="Sleeps it off, even between blows.",
         archetype="BULWARK"),
    card(cid="mosscat", element="NATURE", atk=4, df=4, hp=21, spd=5,
         triggers=[trig("ON_BATTLE_START", "HEAL", "ALL_ALLIES", 2)],
         name="Moss Cat", flavor="Purrs in chlorophyll.",
         archetype="BULWARK"),
    card(cid="rootsnake", element="NATURE", atk=5, df=3, hp=21, spd=5,
         triggers=[trig("ON_ATTACK", "BUFF_DEF", "SELF", 2)],
         name="Rootsnake", flavor="Sinks deeper after every strike.",
         archetype="BULWARK"),
    card(cid="petalwing", element="NATURE", atk=4, df=4, hp=21, spd=5,
         triggers=[trig("ON_OPENING_ATTACK", "HEAL", "ALL_ALLIES", 2)],
         name="Petalwing", flavor="The first flap drops pollen that mends.",
         archetype="BULWARK"),
    card(cid="stonepup", element="NATURE", atk=3, df=6, hp=21, spd=3,
         triggers=[trig("ON_DEATH", "HEAL", "ALL_ALLIES", 3)],
         name="Stonepup", flavor="Crumbles into mineral that the others drink.",
         archetype="BULWARK"),
    card(cid="mosshound", element="NATURE", atk=4, df=5, hp=21, spd=3,
         triggers=[trig("ON_TAKE_DAMAGE", "ADD_SHIELD", "SELF", 3)],
         name="Mosshound", flavor="Shakes off the blow with green flecks of bark.",
         archetype="BULWARK"),
    card(cid="verdant_chimerlet", element="NATURE", atk=4, df=4, hp=21, spd=4,
         triggers=[trig("ON_BATTLE_START", "BUFF_DEF", "ALL_ALLIES", 2,
                        condition=FLUX2)],
         name="Verdant Chimerlet",
         flavor="Three saplings in conference; better when there's weather to share.",
         archetype="FLUX"),
    card(cid="prism_seedling", element="NATURE", atk=3, df=4, hp=24, spd=3,
         triggers=[trig("ON_BATTLE_START", "HEAL", "ALL_ALLIES", 2,
                        condition=FLUX2)],
         name="Prism Seedling",
         flavor="Blooms different each time the team's colors change.",
         archetype="FLUX"),
]


# ---------------------------------------------------------------------------
# VOLT — 13 STORMCHAIN + 2 FLUX
# Stat profile: glass-cannon (high atk/spd, low def/hp); budget 18-22.
# ---------------------------------------------------------------------------

VOLT_COMMONS = [
    card(cid="zapling", element="VOLT", atk=5, df=2, hp=15, spd=8,
         triggers=[trig("ON_BATTLE_START", "BUFF_SPD", "SELF", 3)],
         name="Zapling", flavor="The first out of the gate, always.",
         archetype="STORMCHAIN"),
    card(cid="boltkit", element="VOLT", atk=5, df=2, hp=15, spd=8,
         triggers=[trig("ON_KILL", "BUFF_SPD", "SELF", 2)],
         name="Boltkit", flavor="Each kill sharpens its line.",
         archetype="STORMCHAIN"),
    card(cid="arc_pup", element="VOLT", atk=5, df=2, hp=18, spd=7,
         triggers=[trig("ON_ATTACK", "DAMAGE", "LOWEST_HP_ENEMY", 3)],
         name="Arc Pup", flavor="Always finds the weakest spark.",
         archetype="STORMCHAIN"),
    card(cid="plasma_kit", element="VOLT", atk=6, df=2, hp=15, spd=7,
         triggers=[trig("ON_OPENING_ATTACK", "DAMAGE", "RANDOM_ENEMY", 3)],
         name="Plasma Kit", flavor="Burns through the first thing it sees.",
         archetype="STORMCHAIN"),
    card(cid="thunderfly", element="VOLT", atk=4, df=2, hp=15, spd=9,
         triggers=[trig("ON_BATTLE_START", "BUFF_SPD", "ALL_ALLIES", 2)],
         name="Thunderfly", flavor="The team buzzes a little louder when it joins.",
         archetype="STORMCHAIN"),
    card(cid="galekit", element="VOLT", atk=5, df=3, hp=15, spd=7,
         triggers=[trig("ON_KILL", "DAMAGE", "LOWEST_HP_ENEMY", 3)],
         name="Galekit", flavor="The kill is the wind; the next kill is the next wind.",
         archetype="STORMCHAIN"),
    card(cid="spark_imp", element="VOLT", atk=4, df=3, hp=18, spd=7,
         triggers=[trig("ON_ATTACK", "BUFF_SPD", "SELF", 2)],
         name="Spark Imp", flavor="Faster after the swing than before.",
         archetype="STORMCHAIN"),
    card(cid="arcweasel", element="VOLT", atk=5, df=2, hp=18, spd=7,
         triggers=[trig("ON_OPENING_ATTACK", "BUFF_SPD", "ALL_ALLIES", 2)],
         name="Arcweasel", flavor="The first dash is for the team.",
         archetype="STORMCHAIN"),
    card(cid="boltbat", element="VOLT", atk=4, df=2, hp=15, spd=9,
         triggers=[trig("ON_ATTACK", "DAMAGE", "RANDOM_ENEMY", 3)],
         name="Boltbat", flavor="Echolocates by the smell of ozone.",
         archetype="STORMCHAIN"),
    card(cid="shockling", element="VOLT", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_KILL", "BUFF_SPD", "ALL_ALLIES", 2)],
         name="Shockling", flavor="A small death; a faster team.",
         archetype="STORMCHAIN"),
    card(cid="stormpup", element="VOLT", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_BATTLE_START", "BUFF_SPD", "SELF", 2)],
         name="Stormpup", flavor="Born during the squall; never quite calmed.",
         archetype="STORMCHAIN"),
    card(cid="flashfox", element="VOLT", atk=4, df=2, hp=18, spd=8,
         triggers=[trig("ON_LOW_HP", "BUFF_SPD", "SELF", 3)],
         name="Flashfox", flavor="Wounded fox runs faster than fed fox.",
         archetype="STORMCHAIN"),
    card(cid="arc_kit", element="VOLT", atk=5, df=2, hp=15, spd=8,
         triggers=[trig("ON_ATTACK", "DAMAGE", "LOWEST_HP_ENEMY", 2)],
         name="Arc Kit", flavor="The smallest current arrives first.",
         archetype="STORMCHAIN"),
    card(cid="prismbolt", element="VOLT", atk=5, df=2, hp=18, spd=7,
         triggers=[trig("ON_BATTLE_START", "BUFF_SPD", "ALL_ALLIES", 2,
                        condition=FLUX2)],
         name="Prismbolt",
         flavor="Sees the team's colors; runs faster when there's more than one.",
         archetype="FLUX"),
    card(cid="spectral_volt", element="VOLT", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "SELF", 2,
                        condition=FLUX2)],
         name="Spectral Volt",
         flavor="Steals a watt from every neighbor that isn't kin.",
         archetype="FLUX"),
]


# ---------------------------------------------------------------------------
# VOID — 13 REVENANT + 2 FLUX
# Stat profile: balanced/morbid (medium atk, modest def/hp, medium spd);
# triggers usually fire from death events.
# ---------------------------------------------------------------------------

VOID_COMMONS = [
    card(cid="shadeling", element="VOID", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_ALLY_DEATH", "BUFF_ATK", "SELF", 2)],
         name="Shadeling", flavor="Each lost name is fed to the next swing.",
         archetype="REVENANT"),
    card(cid="nullsprite", element="VOID", atk=4, df=3, hp=18, spd=6,
         triggers=[trig("ON_ATTACK", "DEBUFF_ATK", "RANDOM_ENEMY", 2)],
         name="Nullsprite", flavor="Steals a finger of strength with every brush.",
         archetype="REVENANT"),
    card(cid="wraithling", element="VOID", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_DEATH", "DAMAGE", "ALL_ENEMIES", 2)],
         name="Wraithling", flavor="A small death that splashes outward.",
         archetype="REVENANT"),
    card(cid="dread_kit", element="VOID", atk=4, df=3, hp=18, spd=6,
         triggers=[trig("ON_BATTLE_START", "DEBUFF_ATK", "ALL_ENEMIES", 2)],
         name="Dread Kit", flavor="Its first whisper trims a knife from every enemy.",
         archetype="REVENANT"),
    card(cid="cryptmoth", element="VOID", atk=4, df=3, hp=18, spd=6,
         triggers=[trig("ON_ALLY_DEATH", "DEBUFF_ATK", "ALL_ENEMIES", 2)],
         name="Cryptmoth", flavor="Eats the grief; spits the chill.",
         archetype="REVENANT"),
    card(cid="shadowpup", element="VOID", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_DEATH", "DEBUFF_ATK", "ALL_ENEMIES", 3)],
         name="Shadowpup", flavor="Falls — and so does the menace of those who watched.",
         archetype="REVENANT"),
    card(cid="ghostfin", element="VOID", atk=5, df=3, hp=18, spd=5,
         triggers=[trig("ON_ATTACK", "APPLY_POISON", "RANDOM_ENEMY", 2)],
         name="Ghostfin", flavor="Bites cold; stays cold.",
         archetype="REVENANT"),
    card(cid="voidling", element="VOID", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_KILL", "DEBUFF_ATK", "ALL_ENEMIES", 2)],
         name="Voidling", flavor="A removed life is a removed lesson.",
         archetype="REVENANT"),
    card(cid="miasma_imp", element="VOID", atk=4, df=3, hp=18, spd=6,
         triggers=[trig("ON_ATTACK", "DEBUFF_DEF", "LOWEST_HP_ENEMY", 2)],
         name="Miasma Imp", flavor="Picks the weakest plate to thin.",
         archetype="REVENANT"),
    card(cid="spectral_kit", element="VOID", atk=4, df=3, hp=21, spd=5,
         triggers=[trig("ON_TURN_END", "APPLY_POISON", "RANDOM_ENEMY", 2)],
         name="Spectral Kit", flavor="Leaves a chill in the corner of someone's lung.",
         archetype="REVENANT"),
    card(cid="dirgebat", element="VOID", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_ALLY_DEATH", "DEBUFF_DEF", "ALL_ENEMIES", 2)],
         name="Dirgebat", flavor="Sings the eulogy; the enemy thins to hear it.",
         archetype="REVENANT"),
    card(cid="hollowpup", element="VOID", atk=4, df=3, hp=21, spd=5,
         triggers=[trig("ON_DEATH", "HEAL", "ALL_ALLIES", 3)],
         name="Hollowpup", flavor="Ends with a gift it had been hoarding.",
         archetype="REVENANT"),
    card(cid="silentmoth", element="VOID", atk=4, df=3, hp=21, spd=5,
         triggers=[trig("ON_ATTACK", "DEBUFF_ATK", "LOWEST_HP_ENEMY", 2)],
         name="Silent Moth", flavor="Folds the loudest enemy into a smaller voice.",
         archetype="REVENANT"),
    card(cid="void_chimerlet", element="VOID", atk=5, df=3, hp=18, spd=6,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "SELF", 2,
                        condition=FLUX2)],
         name="Void Chimerlet",
         flavor="Two grins, one purpose; sharper when the team is mixed.",
         archetype="FLUX"),
    card(cid="shadeprism", element="VOID", atk=4, df=3, hp=21, spd=5,
         triggers=[trig("ON_BATTLE_START", "DEBUFF_ATK", "ALL_ENEMIES", 2,
                        condition=FLUX2)],
         name="Shadeprism",
         flavor="Refracts the light it doesn't need into the eyes of those it does.",
         archetype="FLUX"),
]


ALL_NEW_COMMONS = (
    FIRE_COMMONS
    + WATER_COMMONS
    + NATURE_COMMONS
    + VOLT_COMMONS
    + VOID_COMMONS
)


# ---------------------------------------------------------------------------
# Stat-budget self-check (fail loudly if anything drifts out of band).
# ---------------------------------------------------------------------------

def _budget(c: dict) -> float:
    return c["atk"] + c["def"] + c["hp"] / 3.0 + c["spd"]


def _validate() -> None:
    by_elem: dict[str, int] = {}
    by_archetype: dict[str, int] = {}
    for c in ALL_NEW_COMMONS:
        b = _budget(c)
        if not (18.0 - 1e-9 <= b <= 22.0 + 1e-9):
            raise ValueError(
                f"{c['card_id']}: stat budget {b:.2f} out of [18,22]"
            )
        if len(c["triggers"]) != 1:
            raise ValueError(
                f"{c['card_id']}: phase4b commons must have exactly 1 trigger "
                f"(has {len(c['triggers'])})"
            )
        for i, t in enumerate(c["triggers"]):
            v = abs(t["value"])
            if not (2 <= v <= 3):
                raise ValueError(
                    f"{c['card_id']} trigger[{i}] value {v} outside [2,3]"
                )
        by_elem[c["element"]] = by_elem.get(c["element"], 0) + 1
        by_archetype[c["archetype"]] = by_archetype.get(c["archetype"], 0) + 1

    # Per-element shape: 15 each
    for elem in ("FIRE", "WATER", "NATURE", "VOLT", "VOID"):
        n = by_elem.get(elem, 0)
        if n != 15:
            raise ValueError(f"element {elem} has {n} commons; expected 15")

    # 13 archetype-pure + 2 FLUX per element = 5 archetypes × 13 + 1 FLUX × 10
    if by_archetype.get("FLUX", 0) != 10:
        raise ValueError(f"FLUX commons = {by_archetype.get('FLUX')}; expected 10")
    for arch in ("INFERNO", "BULWARK", "TIDAL", "STORMCHAIN", "REVENANT"):
        if by_archetype.get(arch, 0) != 13:
            raise ValueError(f"{arch} commons = {by_archetype.get(arch)}; expected 13")

    if len(ALL_NEW_COMMONS) != 75:
        raise ValueError(f"total = {len(ALL_NEW_COMMONS)}; expected 75")


def main() -> None:
    _validate()  # fail before touching disk

    PACK_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Write each card file.
    for c in ALL_NEW_COMMONS:
        path = PACK_DIR / f"{c['card_id']}.json"
        path.write_text(
            json.dumps(c, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # 2. Append manifest entries (idempotent — skip if card_id already there).
    manifest = json.loads(MANIFEST_PATH.read_text())
    existing_ids = {entry["card_id"] for entry in manifest["cards"]}
    added = 0
    for c in ALL_NEW_COMMONS:
        if c["card_id"] in existing_ids:
            continue
        manifest["cards"].append({
            "card_id": c["card_id"],
            "rarity":  "common",
            "element": c["element"],
            "file":    f"{c['card_id']}.json",
        })
        added += 1

    # 3. Bump manifest + regenerate description from a fresh histogram.
    from collections import Counter
    hist = Counter(e["rarity"] for e in manifest["cards"])
    manifest["version"] = "0.4.2"
    manifest["description"] = (
        f"DAIMON V1 alpha bundled creature pool. {len(manifest['cards'])} "
        f"monsters across 5 elements and 6 archetypes. Phase 4b authored 75 "
        f"new commons (15 per element; 13 archetype-pure + 2 FLUX each). "
        f"Current: {hist.get('common', 0)}C/{hist.get('uncommon', 0)}U/"
        f"{hist.get('rare', 0)}R/{hist.get('epic', 0)}E/{hist.get('legendary', 0)}L. "
        f"Phase 4c authors uncommons to reach the 200-card V1 target "
        f"(98C/60U/28R/12E/2L)."
    )

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {len(ALL_NEW_COMMONS)} new common card files to {PACK_DIR}")
    print(f"Added {added} new manifest entries.")
    print(f"Manifest now lists {len(manifest['cards'])} cards at version "
          f"{manifest['version']}.")
    print(f"Distribution: {dict(hist)}")


if __name__ == "__main__":
    main()
