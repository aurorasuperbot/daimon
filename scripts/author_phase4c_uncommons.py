#!/usr/bin/env python3
"""Phase 4c of V1 card design — author 45 new uncommons.

Brings the v1_alpha pack from 155 → 200 cards, hitting the V1 uncommon target
of 60 (existing 15 + 45 new) and locking the final 200-card count.

Per-element shape (9 each):
  FIRE / WATER / NATURE / VOLT / VOID — each gains 9 uncommons
  (7 archetype-pure + 2 FLUX), mirroring the Phase-4b commons structure.

Design rules (locked in `docs/card_design_v1.md` §4):
  - Stat budget atk + def + hp/3 + spd ∈ [22, 26]
  - Exactly 1 trigger (uncommons differ from commons by *value*, not count)
  - Trigger values 3–4

Species lines: most uncommons here intentionally extend a Phase-4b common
into a 2-tier evolution line via the `species` field (e.g. `ashpup` C →
`ash_strider` U both share `species="ashpup"`). The 4 archetype-pure plus
2 FLUX uncommons that are NOT extensions are written as singletons (their
own species). This produces the §5 mix of multi-tier lines + singletons
without overcommitting to species shape before Phase 5 balance tuning.

Run once, idempotent. Re-running on a pack that already has these 45
entries is a no-op (manifest dedupe via card_id check).
"""

from __future__ import annotations

import json
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent.parent / "daimon" / "catalog" / "v1_alpha"
MANIFEST_PATH = PACK_DIR / "manifest.json"


def trig(when: str, op: str, target: str, value: int,
         condition: str | None = None) -> dict:
    t = {"when": when, "op": op, "target": target, "value": value}
    if condition is not None:
        t["condition"] = condition
    return t


def card(
    *,
    cid: str,
    species: str | None,
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
        "species": species or cid,
        "element": element,
        "atk": atk,
        "def": df,
        "hp": hp,
        "spd": spd,
        "triggers": triggers,
        "name": name,
        "flavor": flavor,
        "rarity": "uncommon",
        "archetype": archetype,
        "art": f"art/uncommon/{cid}.png",
        "moves": [],
    }


FLUX2 = "team.distinct_elements >= 2"


# ---------------------------------------------------------------------------
# FIRE — 7 INFERNO + 2 FLUX
# Profile: 7/3/21/7 = 24 (or 6/4/24/4 = 22 / 7/4/21/4 = 22) — aggressive band.
# Three uncommons (ash_strider, magma_warden, coalbreaker, ember_raptor)
# extend Phase-4b commons into 2-tier species lines.
# ---------------------------------------------------------------------------

FIRE_UNCOMMONS = [
    # ashpup (C) → ash_strider (U): BURN duration up from 2 → 3 rounds
    card(cid="ash_strider", species="ashpup", element="FIRE",
         atk=7, df=3, hp=21, spd=7,
         triggers=[trig("ON_ATTACK", "APPLY_BURN", "RANDOM_ENEMY", 3)],
         name="Ash Strider",
         flavor="Walks where the heat has done its work first.",
         archetype="INFERNO"),
    # magmaling (C) → magma_warden (U): ON_KILL chip 2 → 3
    card(cid="magma_warden", species="magmaling", element="FIRE",
         atk=7, df=4, hp=21, spd=4,
         triggers=[trig("ON_KILL", "DAMAGE", "RANDOM_ENEMY", 3)],
         name="Magma Warden",
         flavor="Each fallen enemy lights the next one up.",
         archetype="INFERNO"),
    # coalwhelp (C) → coalbreaker (U): ON_OPENING_ATTACK chip 3 → 4
    card(cid="coalbreaker", species="coalwhelp", element="FIRE",
         atk=7, df=3, hp=21, spd=7,
         triggers=[trig("ON_OPENING_ATTACK", "DAMAGE", "LOWEST_HP_ENEMY", 4)],
         name="Coalbreaker",
         flavor="The first hit is always the one it remembers.",
         archetype="INFERNO"),
    # emberhawk (C) → ember_raptor (U): ON_OPENING_ATTACK AOE 2 → 3
    card(cid="ember_raptor", species="emberhawk", element="FIRE",
         atk=7, df=3, hp=21, spd=7,
         triggers=[trig("ON_OPENING_ATTACK", "DAMAGE", "ALL_ENEMIES", 3)],
         name="Ember Raptor",
         flavor="Dives once at the start; the rest is bookkeeping.",
         archetype="INFERNO"),
    # Singleton: ON_ATTACK BURN onto LOWEST_HP_ENEMY
    card(cid="blazefiend", species=None, element="FIRE",
         atk=6, df=4, hp=21, spd=7,
         triggers=[trig("ON_ATTACK", "APPLY_BURN", "LOWEST_HP_ENEMY", 3)],
         name="Blazefiend",
         flavor="Picks the limp; sets the limp on fire.",
         archetype="INFERNO"),
    # Singleton: ON_KILL DAMAGE all (small AOE on snowball)
    card(cid="cinder_lancer", species=None, element="FIRE",
         atk=7, df=3, hp=21, spd=7,
         triggers=[trig("ON_KILL", "DAMAGE", "ALL_ENEMIES", 3)],
         name="Cinder Lancer",
         flavor="Cleans the room with the same swing it used to enter.",
         archetype="INFERNO"),
    # Singleton: ON_OPENING_ATTACK AOE — opening alpha-strike at uncommon power
    card(cid="flarelord", species=None, element="FIRE",
         atk=7, df=4, hp=21, spd=4,
         triggers=[trig("ON_OPENING_ATTACK", "DAMAGE", "ALL_ENEMIES", 4)],
         name="Flarelord",
         flavor="Walks in like a daybreak. Leaves like a noon.",
         archetype="INFERNO"),
    # FLUX: extends `flame_chimerlet` C — same BUFF_ATK ALL_ALLIES gate, value 3
    card(cid="flame_chimera_adept", species="flame_chimerlet", element="FIRE",
         atk=6, df=4, hp=21, spd=6,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "ALL_ALLIES", 3,
                        condition=FLUX2)],
         name="Flame Chimera Adept",
         flavor="Has learned what its small selves only sang about.",
         archetype="FLUX"),
    # FLUX: extends `sunscale_drake` C — heal ALL_ALLIES gate, value 3
    card(cid="sunscale_serpent", species="sunscale_drake", element="FIRE",
         atk=6, df=4, hp=24, spd=4,
         triggers=[trig("ON_BATTLE_START", "HEAL", "ALL_ALLIES", 3,
                        condition=FLUX2)],
         name="Sunscale Serpent",
         flavor="Coils warmth into the team it travels with.",
         archetype="FLUX"),
]


# ---------------------------------------------------------------------------
# WATER — 7 TIDAL + 2 FLUX
# Profile: 5/5/24/6 = 24 — sustain band.
# ---------------------------------------------------------------------------

WATER_UNCOMMONS = [
    # brineling (C) → brineprince (U): LIFESTEAL value up
    card(cid="brineprince", species="brineling", element="WATER",
         atk=6, df=4, hp=24, spd=6,
         triggers=[trig("ON_ATTACK", "LIFESTEAL", "HIGHEST_HP_ENEMY", 3)],
         name="Brineprince",
         flavor="Drinks salt like wine; every wound is a vintage.",
         archetype="TIDAL"),
    # tidefry (C) → tidewatcher (U): HEAL ALL_ALLIES value up
    card(cid="tidewatcher", species="tidefry", element="WATER",
         atk=4, df=5, hp=27, spd=6,
         triggers=[trig("ON_BATTLE_START", "HEAL", "ALL_ALLIES", 3)],
         name="Tidewatcher",
         flavor="Counts the team in by the sound of the surf.",
         archetype="TIDAL"),
    # shellfin (C) → shellguard (U): ADD_SHIELD on take damage value up
    card(cid="shellguard", species="shellfin", element="WATER",
         atk=4, df=6, hp=24, spd=5,
         triggers=[trig("ON_TAKE_DAMAGE", "ADD_SHIELD", "SELF", 3)],
         name="Shellguard",
         flavor="Owes another plate to the next blow that asks.",
         archetype="TIDAL"),
    # Singleton: HIGHEST_HP_ENEMY LIFESTEAL — alternate brineling line at value 4
    card(cid="sea_warden", species=None, element="WATER",
         atk=5, df=5, hp=24, spd=6,
         triggers=[trig("ON_ATTACK", "LIFESTEAL", "HIGHEST_HP_ENEMY", 4)],
         name="Sea Warden",
         flavor="Lives off the largest of the day's mistakes.",
         archetype="TIDAL"),
    # Singleton: round-gated team heal (uncommon-tier of saltsprite C pattern but bigger value)
    card(cid="tide_chanter", species=None, element="WATER",
         atk=4, df=5, hp=27, spd=5,
         triggers=[trig("ON_ROUND_START", "HEAL", "ALL_ALLIES", 3,
                        condition="round >= 2")],
         name="Tide Chanter",
         flavor="Patient with the pain; insistent with the song.",
         archetype="TIDAL"),
    # Singleton: ON_ATTACK HEAL random ally
    card(cid="coral_priest", species=None, element="WATER",
         atk=5, df=4, hp=24, spd=6,
         triggers=[trig("ON_ATTACK", "HEAL", "RANDOM_ALLY", 3)],
         name="Coral Priest",
         flavor="Bites once for the fight; once for the friend.",
         archetype="TIDAL"),
    # Singleton: ON_KILL team heal — sustain compound on snowball
    card(cid="abyssbreaker", species=None, element="WATER",
         atk=6, df=4, hp=24, spd=5,
         triggers=[trig("ON_KILL", "HEAL", "ALL_ALLIES", 3)],
         name="Abyssbreaker",
         flavor="Ends the depth; gives back the breath.",
         archetype="TIDAL"),
    # FLUX: extends `mistchimera` C
    card(cid="mistchimera_adept", species="mistchimera", element="WATER",
         atk=5, df=5, hp=24, spd=5,
         triggers=[trig("ON_BATTLE_START", "HEAL", "ALL_ALLIES", 3,
                        condition=FLUX2)],
         name="Mist Chimera Adept",
         flavor="Three throats; one chorus; richer harmony in mixed company.",
         archetype="FLUX"),
    # FLUX: extends `tidemerger` C
    card(cid="tide_synth", species="tidemerger", element="WATER",
         atk=6, df=4, hp=24, spd=5,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "SELF", 3,
                        condition=FLUX2)],
         name="Tide Synth",
         flavor="Tunes its appetite to the keys around it.",
         archetype="FLUX"),
]


# ---------------------------------------------------------------------------
# NATURE — 7 BULWARK + 2 FLUX
# Profile: 4/7/27/4 = 24 — defensive band.
# ---------------------------------------------------------------------------

NATURE_UNCOMMONS = [
    # mossling (C) → mossbear (U): ADD_SHIELD value up
    card(cid="mossbear", species="mossling", element="NATURE",
         atk=4, df=7, hp=27, spd=4,
         triggers=[trig("ON_TAKE_DAMAGE", "ADD_SHIELD", "SELF", 3)],
         name="Mossbear",
         flavor="The forest lends bark to whatever is its bear today.",
         archetype="BULWARK"),
    # barkpup (C) → barkguard (U): BUFF_DEF self value up
    card(cid="barkguard", species="barkpup", element="NATURE",
         atk=4, df=7, hp=24, spd=5,
         triggers=[trig("ON_BATTLE_START", "BUFF_DEF", "SELF", 3)],
         name="Barkguard",
         flavor="Hardens before the first axe is even named.",
         archetype="BULWARK"),
    # thornling (C) → thornserpent (U): retaliate damage up
    card(cid="thornserpent", species="thornling", element="NATURE",
         atk=5, df=5, hp=27, spd=5,
         triggers=[trig("ON_TAKE_DAMAGE", "DAMAGE", "RANDOM_ENEMY", 3)],
         name="Thornserpent",
         flavor="Coils through the hedge; the hedge bites back twice.",
         archetype="BULWARK"),
    # Singleton: team-wide DEF buff
    card(cid="bramble_warden", species=None, element="NATURE",
         atk=4, df=6, hp=27, spd=5,
         triggers=[trig("ON_BATTLE_START", "BUFF_DEF", "ALL_ALLIES", 3)],
         name="Bramble Warden",
         flavor="Sets the briar before the song of the first arrow.",
         archetype="BULWARK"),
    # Singleton: large self DEF stack
    card(cid="stone_titan", species=None, element="NATURE",
         atk=4, df=6, hp=27, spd=4,
         triggers=[trig("ON_TAKE_DAMAGE", "BUFF_DEF", "SELF", 3)],
         name="Stone Titan",
         flavor="Each blow leaves another mineral coat.",
         archetype="BULWARK"),
    # Singleton: end-of-turn self heal extender
    card(cid="forest_keeper", species=None, element="NATURE",
         atk=4, df=6, hp=27, spd=4,
         triggers=[trig("ON_TURN_END", "HEAL", "SELF", 3)],
         name="Forest Keeper",
         flavor="Tends the wound the way it tends a sapling.",
         archetype="BULWARK"),
    # Singleton: longer TAUNT (3 rounds) — anchors enemy targeting
    card(cid="root_warden", species=None, element="NATURE",
         atk=4, df=6, hp=27, spd=4,
         triggers=[trig("ON_BATTLE_START", "APPLY_TAUNT", "SELF", 3)],
         name="Root Warden",
         flavor="Stays where the team needs the line drawn.",
         archetype="BULWARK"),
    # FLUX: extends `verdant_chimerlet` C — team DEF buff at value 3
    card(cid="verdant_chimera", species="verdant_chimerlet", element="NATURE",
         atk=4, df=6, hp=27, spd=4,
         triggers=[trig("ON_BATTLE_START", "BUFF_DEF", "ALL_ALLIES", 3,
                        condition=FLUX2)],
         name="Verdant Chimera",
         flavor="Three saplings agree on the weather; the team thickens.",
         archetype="FLUX"),
    # FLUX: extends `prism_seedling` C — team heal at value 3
    card(cid="prism_grove", species="prism_seedling", element="NATURE",
         atk=4, df=5, hp=30, spd=4,
         triggers=[trig("ON_BATTLE_START", "HEAL", "ALL_ALLIES", 3,
                        condition=FLUX2)],
         name="Prism Grove",
         flavor="Blooms in whatever colors its companions are wearing.",
         archetype="FLUX"),
]


# ---------------------------------------------------------------------------
# VOLT — 7 STORMCHAIN + 2 FLUX
# Profile: 6/3/18/9 = 24 — glass cannon band.
# ---------------------------------------------------------------------------

VOLT_UNCOMMONS = [
    # zapling (C) → zapdrake (U): BUFF_SPD self value up
    card(cid="zapdrake", species="zapling", element="VOLT",
         atk=6, df=3, hp=21, spd=8,
         triggers=[trig("ON_BATTLE_START", "BUFF_SPD", "SELF", 4)],
         name="Zapdrake",
         flavor="Out of the gate before the gate is finished opening.",
         archetype="STORMCHAIN"),
    # boltkit (C) → boltrunner (U): on_kill BUFF_SPD value up
    card(cid="boltrunner", species="boltkit", element="VOLT",
         atk=6, df=3, hp=21, spd=8,
         triggers=[trig("ON_KILL", "BUFF_SPD", "SELF", 3)],
         name="Boltrunner",
         flavor="Runs faster after every name it crosses out.",
         archetype="STORMCHAIN"),
    # Singleton: ON_ATTACK chip damage uncommon-tier
    card(cid="arc_lancer", species=None, element="VOLT",
         atk=6, df=3, hp=21, spd=8,
         triggers=[trig("ON_ATTACK", "DAMAGE", "LOWEST_HP_ENEMY", 4)],
         name="Arc Lancer",
         flavor="Picks the soft spark; turns it into a dim one.",
         archetype="STORMCHAIN"),
    # Singleton: ON_OPENING_ATTACK chip
    card(cid="plasma_hound", species=None, element="VOLT",
         atk=7, df=3, hp=18, spd=8,
         triggers=[trig("ON_OPENING_ATTACK", "DAMAGE", "RANDOM_ENEMY", 4)],
         name="Plasma Hound",
         flavor="Burns the first scent of the battlefield off something.",
         archetype="STORMCHAIN"),
    # Singleton: team SPD buff bigger
    card(cid="galelord", species=None, element="VOLT",
         atk=5, df=3, hp=21, spd=9,
         triggers=[trig("ON_BATTLE_START", "BUFF_SPD", "ALL_ALLIES", 3)],
         name="Galelord",
         flavor="The team flies a half-second nearer the front for it.",
         archetype="STORMCHAIN"),
    # Singleton: ON_KILL team SPD buff
    card(cid="shock_runner", species=None, element="VOLT",
         atk=6, df=3, hp=21, spd=8,
         triggers=[trig("ON_KILL", "BUFF_SPD", "ALL_ALLIES", 3)],
         name="Shock Runner",
         flavor="Each kill is a tailwind for everyone behind.",
         archetype="STORMCHAIN"),
    # Singleton: ON_ATTACK self SPD buff (snowballing speed)
    card(cid="spark_serpent", species=None, element="VOLT",
         atk=6, df=3, hp=21, spd=8,
         triggers=[trig("ON_ATTACK", "BUFF_SPD", "SELF", 3)],
         name="Spark Serpent",
         flavor="Each strike sharpens the line of the next.",
         archetype="STORMCHAIN"),
    # FLUX: extends `prismbolt` C
    card(cid="prism_strider", species="prismbolt", element="VOLT",
         atk=6, df=3, hp=21, spd=8,
         triggers=[trig("ON_BATTLE_START", "BUFF_SPD", "ALL_ALLIES", 3,
                        condition=FLUX2)],
         name="Prism Strider",
         flavor="Knows the team by its colors; runs proportionally.",
         archetype="FLUX"),
    # FLUX: extends `spectral_volt` C
    card(cid="spectral_charge", species="spectral_volt", element="VOLT",
         atk=7, df=3, hp=21, spd=7,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "SELF", 3,
                        condition=FLUX2)],
         name="Spectral Charge",
         flavor="Borrows watts from every neighbor that isn't kin.",
         archetype="FLUX"),
]


# ---------------------------------------------------------------------------
# VOID — 7 REVENANT + 2 FLUX
# Profile: 6/4/21/7 = 24 — death-economy band.
# ---------------------------------------------------------------------------

VOID_UNCOMMONS = [
    # shadeling (C) → shadebishop (U): ally-death BUFF_ATK value up
    card(cid="shadebishop", species="shadeling", element="VOID",
         atk=6, df=4, hp=21, spd=7,
         triggers=[trig("ON_ALLY_DEATH", "BUFF_ATK", "SELF", 3)],
         name="Shadebishop",
         flavor="Reads the obituary; sharpens accordingly.",
         archetype="REVENANT"),
    # wraithling (C) → wraith_prince (U): ON_DEATH AOE value up
    card(cid="wraith_prince", species="wraithling", element="VOID",
         atk=6, df=4, hp=21, spd=7,
         triggers=[trig("ON_DEATH", "DAMAGE", "ALL_ENEMIES", 3)],
         name="Wraith Prince",
         flavor="A larger death; a wider splash.",
         archetype="REVENANT"),
    # Singleton: ON_BATTLE_START enemy ATK debuff stronger
    card(cid="dread_warden", species=None, element="VOID",
         atk=6, df=4, hp=21, spd=7,
         triggers=[trig("ON_BATTLE_START", "DEBUFF_ATK", "ALL_ENEMIES", 3)],
         name="Dread Warden",
         flavor="Its first whisper takes a finger of strength from each.",
         archetype="REVENANT"),
    # Singleton: ally-death enemy ATK debuff
    card(cid="crypt_seer", species=None, element="VOID",
         atk=5, df=4, hp=24, spd=7,
         triggers=[trig("ON_ALLY_DEATH", "DEBUFF_ATK", "ALL_ENEMIES", 3)],
         name="Crypt Seer",
         flavor="Reads the team's losses to the enemy's grip.",
         archetype="REVENANT"),
    # Singleton: POISON apply with longer duration
    card(cid="ghoul_imp", species=None, element="VOID",
         atk=6, df=4, hp=21, spd=7,
         triggers=[trig("ON_ATTACK", "APPLY_POISON", "RANDOM_ENEMY", 3)],
         name="Ghoul Imp",
         flavor="Bites once. Dies in instalments.",
         archetype="REVENANT"),
    # Singleton: ON_DEATH enemy ATK debuff bigger
    card(cid="shadow_warden", species=None, element="VOID",
         atk=6, df=4, hp=21, spd=7,
         triggers=[trig("ON_DEATH", "DEBUFF_ATK", "ALL_ENEMIES", 4)],
         name="Shadow Warden",
         flavor="Falls — and the enemy menace goes with it.",
         archetype="REVENANT"),
    # Singleton: ally-death enemy DEF debuff
    card(cid="dirge_lich", species=None, element="VOID",
         atk=6, df=4, hp=21, spd=7,
         triggers=[trig("ON_ALLY_DEATH", "DEBUFF_DEF", "ALL_ENEMIES", 3)],
         name="Dirge Lich",
         flavor="Sings; the enemy plates thin to listen.",
         archetype="REVENANT"),
    # FLUX: extends `void_chimerlet` C
    card(cid="void_chimera", species="void_chimerlet", element="VOID",
         atk=6, df=4, hp=21, spd=7,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "SELF", 3,
                        condition=FLUX2)],
         name="Void Chimera",
         flavor="Two grins, sharper for variety in their company.",
         archetype="FLUX"),
    # FLUX: extends `shadeprism` C
    card(cid="shade_prismatic", species="shadeprism", element="VOID",
         atk=5, df=4, hp=24, spd=6,
         triggers=[trig("ON_BATTLE_START", "DEBUFF_ATK", "ALL_ENEMIES", 3,
                        condition=FLUX2)],
         name="Shade Prismatic",
         flavor="Refracts what the team doesn't need into the eyes that face it.",
         archetype="FLUX"),
]


ALL_NEW_UNCOMMONS = (
    FIRE_UNCOMMONS
    + WATER_UNCOMMONS
    + NATURE_UNCOMMONS
    + VOLT_UNCOMMONS
    + VOID_UNCOMMONS
)


# ---------------------------------------------------------------------------
# Self-check: budgets, trigger counts, value bands, per-element shape.
# ---------------------------------------------------------------------------

def _budget(c: dict) -> float:
    return c["atk"] + c["def"] + c["hp"] / 3.0 + c["spd"]


def _validate() -> None:
    by_elem: dict[str, int] = {}
    by_archetype: dict[str, int] = {}
    for c in ALL_NEW_UNCOMMONS:
        b = _budget(c)
        if not (22.0 - 1e-9 <= b <= 26.0 + 1e-9):
            raise ValueError(
                f"{c['card_id']}: stat budget {b:.2f} out of [22,26]"
            )
        if len(c["triggers"]) != 1:
            raise ValueError(
                f"{c['card_id']}: phase4c uncommons must have exactly 1 trigger "
                f"(has {len(c['triggers'])})"
            )
        for i, t in enumerate(c["triggers"]):
            v = abs(t["value"])
            if not (3 <= v <= 4):
                raise ValueError(
                    f"{c['card_id']} trigger[{i}] value {v} outside [3,4]"
                )
        by_elem[c["element"]] = by_elem.get(c["element"], 0) + 1
        by_archetype[c["archetype"]] = by_archetype.get(c["archetype"], 0) + 1

    for elem in ("FIRE", "WATER", "NATURE", "VOLT", "VOID"):
        n = by_elem.get(elem, 0)
        if n != 9:
            raise ValueError(f"element {elem} has {n} uncommons; expected 9")

    if by_archetype.get("FLUX", 0) != 10:
        raise ValueError(f"FLUX uncommons = {by_archetype.get('FLUX')}; expected 10")
    for arch in ("INFERNO", "BULWARK", "TIDAL", "STORMCHAIN", "REVENANT"):
        if by_archetype.get(arch, 0) != 7:
            raise ValueError(f"{arch} uncommons = {by_archetype.get(arch)}; expected 7")

    if len(ALL_NEW_UNCOMMONS) != 45:
        raise ValueError(f"total = {len(ALL_NEW_UNCOMMONS)}; expected 45")


def main() -> None:
    _validate()

    PACK_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Write each card file.
    for c in ALL_NEW_UNCOMMONS:
        path = PACK_DIR / f"{c['card_id']}.json"
        path.write_text(
            json.dumps(c, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # 2. Append manifest entries (idempotent).
    manifest = json.loads(MANIFEST_PATH.read_text())
    existing_ids = {entry["card_id"] for entry in manifest["cards"]}
    added = 0
    for c in ALL_NEW_UNCOMMONS:
        if c["card_id"] in existing_ids:
            continue
        manifest["cards"].append({
            "card_id": c["card_id"],
            "rarity":  "uncommon",
            "element": c["element"],
            "file":    f"{c['card_id']}.json",
        })
        added += 1

    # 3. Bump manifest + regenerate description from a fresh histogram.
    from collections import Counter
    hist = Counter(e["rarity"] for e in manifest["cards"])
    manifest["version"] = "0.4.3"
    manifest["description"] = (
        f"DAIMON V1 alpha bundled creature pool. {len(manifest['cards'])} "
        f"monsters across 5 elements and 6 archetypes — V1 200-card target "
        f"reached. Phase 4c authored 45 new uncommons (9 per element; 7 "
        f"archetype-pure + 2 FLUX each). Distribution: "
        f"{hist.get('common', 0)}C/{hist.get('uncommon', 0)}U/"
        f"{hist.get('rare', 0)}R/{hist.get('epic', 0)}E/{hist.get('legendary', 0)}L. "
        f"Phase 5 (sim balance) follows."
    )

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {len(ALL_NEW_UNCOMMONS)} new uncommon card files to {PACK_DIR}")
    print(f"Added {added} new manifest entries.")
    print(f"Manifest now lists {len(manifest['cards'])} cards at version "
          f"{manifest['version']}.")
    print(f"Distribution: {dict(hist)}")


if __name__ == "__main__":
    main()
