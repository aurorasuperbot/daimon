#!/usr/bin/env python3
"""Phase 3 of V1 card design — archetype anchors (2 legendaries + 12 epics).

Anchors prove each of the 6 archetypes plays distinctly *before* we fill the
200-card pool in Phase 4. Every anchor exercises at least one Phase-2 vocab
element (BURN/POISON/STUN/SILENCE/TAUNT/LIFESTEAL, ON_KILL/ON_TURN_END/
ON_LOW_HP/ON_OPENING_ATTACK, or a `condition` DSL gate) so we get end-to-end
proof the new vocabulary plays in real cards.

Design rules locked in `docs/card_design_v1.md`:
  - Stat budget: epic 32-40 (atk + def + hp/3 + spd), legendary 38-46.
  - Trigger value ceiling: epic 6, legendary 8 (DOT durations counted as values).
  - Only 2 legendaries in V1 (Voidking Morr + World-Eater). The previously
    scaffolded "legendaries" (voltcat_apex/storm_celestial/echo_lich/
    pyrotyrant/leviathan_prime/worldroot_colossus) stay in the pool at their
    declared rarity for now — Phase 4 reconciles to exactly 2 legendaries.

This script is one-shot, deterministic, idempotent. Re-running overwrites.

Run:
    .venv/bin/python scripts/author_phase3_anchors.py
"""

from __future__ import annotations

import json
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent.parent / "daimon" / "catalog" / "v1_alpha"
MANIFEST_PATH = PACK_DIR / "manifest.json"


# ---------------------------------------------------------------------------
# Helpers — extend the v1_alpha shape with optional `condition` on triggers.
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
    species: str | None = None,
    element: str,
    atk: int,
    df: int,                       # `def` is reserved
    hp: int,
    spd: int,
    triggers: list[dict] | None = None,
    name: str,
    flavor: str,
    rarity: str,
    archetype: str,                # render-only label, ignored by engine
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
        "archetype": archetype,
        "art": f"art/{rarity}/{cid}.png",
        "moves": moves or [],
    }


# ---------------------------------------------------------------------------
# 2 LEGENDARIES — set-defining icons
# ---------------------------------------------------------------------------

LEGENDARIES = [
    # REVENANT apex. Already scaffolded; this is the Phase 3 upgrade — keeps
    # the original Sovereign Pall + Throne of Silence beats but swaps the
    # ON_ATTACK single-target chip for the snowball ON_ALLY_DEATH BUFF_ATK
    # that the REVENANT archetype is built around. Stat budget unchanged.
    card(
        cid="voidking_morr",
        element="VOID",
        atk=13, df=8, hp=32, spd=7,        # 13+8+10.7+7 = 38.7  legendary lower-mid
        triggers=[
            trig("ON_BATTLE_START", "DEBUFF_ATK", "ALL_ENEMIES", 3),
            trig("ON_ALLY_DEATH",   "BUFF_ATK",   "SELF",        4),
            trig("ON_DEATH",        "DAMAGE",     "ALL_ENEMIES", 8),
        ],
        name="Voidking Morr",
        flavor="Crowned by what nobody wanted. Reigns regardless.",
        rarity="legendary",
        archetype="REVENANT",
        moves=[
            {"name": "Sovereign Pall",   "when": "ON_BATTLE_START"},
            {"name": "Feast on Loss",    "when": "ON_ALLY_DEATH"},
            {"name": "Throne of Silence","when": "ON_DEATH"},
        ],
    ),
    # FLUX apex. NEW. Power-level peak of the set, but every meaningful
    # trigger is condition-gated on team diversity. Played in a mono-element
    # team, it's a 14/9/42/6 vanilla — gigantic but inert. In a 4-element
    # rainbow team, it opens with team-wide +3 ATK, an 8-dmg AOE, and chip
    # damage every attack. The whole point of FLUX rendered as one card.
    card(
        cid="world_eater",
        element="VOID",
        atk=14, df=9, hp=42, spd=6,         # 14+9+14+6 = 43  near top of legendary
        triggers=[
            # 2-element gate: the entry-level FLUX bonus
            trig("ON_BATTLE_START", "BUFF_ATK", "ALL_ALLIES", 3,
                 condition="team.distinct_elements >= 2"),
            # 3-element gate: per-attack chip
            trig("ON_ATTACK", "DAMAGE", "ALL_ENEMIES", 4,
                 condition="team.distinct_elements >= 3"),
            # 4-element gate: the apex AOE — only fires for a true rainbow team
            trig("ON_BATTLE_START", "DAMAGE", "ALL_ENEMIES", 8,
                 condition="team.distinct_elements >= 4"),
        ],
        name="World-Eater",
        flavor="It does not hate the world. It is merely hungry, and the world is here.",
        rarity="legendary",
        archetype="FLUX",
        moves=[
            {"name": "Many-Hued Roar",   "when": "ON_BATTLE_START"},
            {"name": "Spectral Maw",     "when": "ON_ATTACK"},
            {"name": "World-Devouring",  "when": "ON_BATTLE_START"},
        ],
    ),
]


# ---------------------------------------------------------------------------
# 12 EPICS — 2 anchors per archetype.
# ---------------------------------------------------------------------------

INFERNO_EPICS = [
    # Aggro snowball: every attack burns; every kill grows ATK.
    # In a 6-card team you expect ~2 kills by round 3 → +6 ATK accumulated.
    card(
        cid="magma_tyrant",
        element="FIRE",
        atk=12, df=5, hp=24, spd=8,         # 12+5+8+8 = 33
        triggers=[
            trig("ON_ATTACK", "APPLY_BURN", "RANDOM_ENEMY", 2),
            trig("ON_KILL",   "BUFF_ATK",   "SELF",         3),
        ],
        name="Magma Tyrant",
        flavor="Counts subjects by the embers they leave behind.",
        rarity="epic",
        archetype="INFERNO",
        moves=[
            {"name": "Cinder Bite",    "when": "ON_ATTACK"},
            {"name": "Throne of Ash",  "when": "ON_KILL"},
        ],
    ),
    # Alpha-strike + legacy — opens the match with a 4-dmg AOE, dies into a
    # team-wide 6 heal. Defines the "burst now, fade later" INFERNO tempo.
    card(
        cid="solar_phoenix",
        element="FIRE",
        atk=11, df=5, hp=26, spd=8,         # 11+5+8.7+8 = 32.7
        triggers=[
            trig("ON_OPENING_ATTACK", "DAMAGE", "ALL_ENEMIES", 4),
            trig("ON_DEATH",          "HEAL",   "ALL_ALLIES",  6),
        ],
        name="Solar Phoenix",
        flavor="The first cry of dawn; the last warmth of dusk.",
        rarity="epic",
        archetype="INFERNO",
        moves=[
            {"name": "Sunbreak Dive",  "when": "ON_OPENING_ATTACK"},
            {"name": "Last Light",     "when": "ON_DEATH"},
        ],
    ),
]

BULWARK_EPICS = [
    # The TAUNT anchor. Pulls enemy basic attacks onto itself for 3 rounds,
    # then refreshes its own shield every time it gets hit. Locks down enemy
    # threat targeting while teammates outlast.
    card(
        cid="worldroot_sentinel",
        element="NATURE",
        atk=8, df=10, hp=32, spd=4,         # 8+10+10.7+4 = 32.7
        triggers=[
            trig("ON_BATTLE_START", "APPLY_TAUNT", "SELF", 3),
            trig("ON_TAKE_DAMAGE",  "ADD_SHIELD",  "SELF", 4),
        ],
        name="Worldroot Sentinel",
        flavor="A wall is a tree that has decided not to grow leaves.",
        rarity="epic",
        archetype="BULWARK",
        moves=[
            {"name": "Stand Forth",     "when": "ON_BATTLE_START"},
            {"name": "Bark Reinforces", "when": "ON_TAKE_DAMAGE"},
        ],
    ),
    # Late-game peak — the round>=2 condition gates the heal so it doesn't
    # waste itself opening, but from round 2 onward it pumps 3 hp/round into
    # the whole team. Pairs with a tank-up ON_TAKE_DAMAGE BUFF_DEF on self.
    card(
        cid="bulwark_patriarch",
        element="NATURE",
        atk=8, df=11, hp=30, spd=3,         # 8+11+10+3 = 32
        triggers=[
            trig("ON_ROUND_START", "HEAL",     "ALL_ALLIES", 3,
                 condition="round >= 2"),
            trig("ON_TAKE_DAMAGE", "BUFF_DEF", "SELF",       1),
        ],
        name="Bulwark Patriarch",
        flavor="Old enough to remember when the storms were the children.",
        rarity="epic",
        archetype="BULWARK",
        moves=[
            {"name": "Patient Verdure", "when": "ON_ROUND_START"},
            {"name": "Set the Roots",   "when": "ON_TAKE_DAMAGE"},
        ],
    ),
]

TIDAL_EPICS = [
    # The LIFESTEAL anchor. Every attack pumps damage into the highest-HP
    # enemy AND heals itself for ceil(6/2) = 3. Outvalues the grind.
    card(
        cid="tide_empress",
        element="WATER",
        atk=10, df=7, hp=28, spd=7,         # 10+7+9.3+7 = 33.3
        triggers=[
            trig("ON_ATTACK",       "LIFESTEAL", "HIGHEST_HP_ENEMY", 6),
            trig("ON_BATTLE_START", "HEAL",      "ALL_ALLIES",       3),
        ],
        name="Tide Empress",
        flavor="The current never asks; it merely remembers everything it took.",
        rarity="epic",
        archetype="TIDAL",
        moves=[
            {"name": "Brine Thirst",  "when": "ON_ATTACK"},
            {"name": "Salt Blessing", "when": "ON_BATTLE_START"},
        ],
    ),
    # Conditional sustain combo — heals an ally for 4 ONLY while at full HP.
    # Forces TIDAL to play the "stay pristine" line: shields up, heals back.
    card(
        cid="coral_augur",
        element="WATER",
        atk=8, df=8, hp=28, spd=7,          # 8+8+9.3+7 = 32.3
        triggers=[
            trig("ON_ATTACK",      "HEAL",       "RANDOM_ALLY", 4,
                 condition="self.hp == self.hp_max"),
            trig("ON_TAKE_DAMAGE", "ADD_SHIELD", "SELF",        3),
        ],
        name="Coral Augur",
        flavor="Reads the fate of the reef in the pattern of held breath.",
        rarity="epic",
        archetype="TIDAL",
        moves=[
            {"name": "Pristine Tide", "when": "ON_ATTACK"},
            {"name": "Shellward",     "when": "ON_TAKE_DAMAGE"},
        ],
    ),
]

STORMCHAIN_EPICS = [
    # Alpha-strike + team SPD chain. Exists at HIGH_SPD=12 so it almost
    # always opens the match; ON_OPENING_ATTACK + 5 AOE is the chain igniter.
    # New name `tempest_apex` to avoid colliding with scaffolded
    # `storm_celestial` (which keeps its current legendary scaffolding).
    card(
        cid="tempest_apex",
        element="VOLT",
        atk=12, df=5, hp=24, spd=12,        # 12+5+8+12 = 37
        triggers=[
            trig("ON_BATTLE_START",  "BUFF_SPD", "ALL_ALLIES",  3),
            trig("ON_OPENING_ATTACK","DAMAGE",   "ALL_ENEMIES", 5),
        ],
        name="Tempest Apex",
        flavor="The eye of the storm is a small, deliberate predator.",
        rarity="epic",
        archetype="STORMCHAIN",
        moves=[
            {"name": "Galemark",      "when": "ON_BATTLE_START"},
            {"name": "Skyfall Salvo", "when": "ON_OPENING_ATTACK"},
        ],
    ),
    # The kill-chain. Every kill pumps SPD; chained kills compound priority.
    # New name `arc_predator` to avoid colliding with scaffolded `voltcat_apex`.
    card(
        cid="arc_predator",
        element="VOLT",
        atk=13, df=4, hp=22, spd=11,        # 13+4+7.3+11 = 35.3
        triggers=[
            trig("ON_KILL",   "BUFF_SPD", "SELF",            3),
            trig("ON_ATTACK", "DAMAGE",   "LOWEST_HP_ENEMY", 5),
        ],
        name="Arc Predator",
        flavor="Hunts the weakest spark, then leaves on a faster line.",
        rarity="epic",
        archetype="STORMCHAIN",
        moves=[
            {"name": "Bolt Hunger", "when": "ON_KILL"},
            {"name": "Cull Strike", "when": "ON_ATTACK"},
        ],
    ),
]

REVENANT_EPICS = [
    # SILENCE-on-ally-death. When a teammate dies, slap SILENCE 2r on a
    # random enemy — kills enemy ON_DEATH cascades (bypasses the entire
    # mid-game trigger plan of opposing REVENANT/INFERNO).
    card(
        cid="crypt_wraith",
        element="VOID",
        atk=10, df=6, hp=24, spd=8,         # 10+6+8+8 = 32
        triggers=[
            trig("ON_ALLY_DEATH", "APPLY_SILENCE", "RANDOM_ENEMY",     2),
            trig("ON_DEATH",      "DAMAGE",        "LOWEST_HP_ENEMY",  5),
        ],
        name="Crypt Wraith",
        flavor="Speaks only the names of those who can no longer answer.",
        rarity="epic",
        archetype="REVENANT",
        moves=[
            {"name": "Hush of Mourning", "when": "ON_ALLY_DEATH"},
            {"name": "Parting Wail",     "when": "ON_DEATH"},
        ],
    ),
    # Sacrifice-snowball + lingering pain. New name `mourners_lich` to avoid
    # colliding with scaffolded `echo_lich`. Ally death pumps SELF.atk; own
    # death debuffs the enemy team's atk for the rest of the game.
    card(
        cid="mourners_lich",
        element="VOID",
        atk=11, df=7, hp=26, spd=7,         # 11+7+8.7+7 = 33.7
        triggers=[
            trig("ON_ALLY_DEATH", "BUFF_ATK",   "SELF",        4),
            trig("ON_DEATH",      "DEBUFF_ATK", "ALL_ENEMIES", 4),
        ],
        name="Mourner's Lich",
        flavor="Each name in its ledger is a promise of payment, eventually.",
        rarity="epic",
        archetype="REVENANT",
        moves=[
            {"name": "Eulogy Feast",   "when": "ON_ALLY_DEATH"},
            {"name": "Last Reminder",  "when": "ON_DEATH"},
        ],
    ),
]

FLUX_EPICS = [
    # 2-element gate buffs the team; 3-element gate adds an AOE chip per atk.
    # NATURE host so it benefits BULWARK shells when slotted into them.
    card(
        cid="prism_chimera",
        element="NATURE",
        atk=11, df=7, hp=28, spd=7,         # 11+7+9.3+7 = 34.3
        triggers=[
            trig("ON_BATTLE_START", "BUFF_ATK", "ALL_ALLIES", 3,
                 condition="team.distinct_elements >= 2"),
            trig("ON_ATTACK",       "DAMAGE",   "ALL_ENEMIES", 3,
                 condition="team.distinct_elements >= 3"),
        ],
        name="Prism Chimera",
        flavor="Every head bargains with a different season; together they win.",
        rarity="epic",
        archetype="FLUX",
        moves=[
            {"name": "Many-Voiced Vow", "when": "ON_BATTLE_START"},
            {"name": "Spectral Lash",   "when": "ON_ATTACK"},
        ],
    ),
    # FIRE host. 2-element gate heals the team; 3-element gate shields all
    # allies on each kill. Plays as INFERNO-supplement when slotted into
    # FIRE teams that splash a second/third element.
    card(
        cid="rainbow_drake",
        element="FIRE",
        atk=11, df=6, hp=28, spd=8,         # 11+6+9.3+8 = 34.3
        triggers=[
            trig("ON_BATTLE_START", "HEAL",       "ALL_ALLIES", 4,
                 condition="team.distinct_elements >= 2"),
            trig("ON_KILL",         "ADD_SHIELD", "ALL_ALLIES", 3,
                 condition="team.distinct_elements >= 3"),
        ],
        name="Rainbow Drake",
        flavor="Its scales remember every weather it has ever flown through.",
        rarity="epic",
        archetype="FLUX",
        moves=[
            {"name": "Many-Hued Pulse",  "when": "ON_BATTLE_START"},
            {"name": "Iridescent Ward",  "when": "ON_KILL"},
        ],
    ),
]


ALL_ANCHORS = (
    LEGENDARIES
    + INFERNO_EPICS
    + BULWARK_EPICS
    + TIDAL_EPICS
    + STORMCHAIN_EPICS
    + REVENANT_EPICS
    + FLUX_EPICS
)


def main() -> None:
    PACK_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Write each card file (overwrites by design — script is idempotent).
    for c in ALL_ANCHORS:
        path = PACK_DIR / f"{c['card_id']}.json"
        path.write_text(
            json.dumps(c, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # 2. Append new card_ids to manifest. Existing entries (voidking_morr)
    # are left in place — only the file content changed for those.
    manifest = json.loads(MANIFEST_PATH.read_text())
    existing_ids = {entry["card_id"] for entry in manifest["cards"]}
    added = 0
    for c in ALL_ANCHORS:
        if c["card_id"] in existing_ids:
            continue
        manifest["cards"].append({
            "card_id": c["card_id"],
            "rarity":  c["rarity"],
            "element": c["element"],
            "file":    f"{c['card_id']}.json",
        })
        added += 1

    # 3. Bump version + description (count card files on disk to stay honest).
    manifest["version"] = "0.4.0"
    total = len(manifest["cards"])
    manifest["description"] = (
        f"DAIMON V1 alpha bundled creature pool. {total} monsters across 5 "
        "elements (Fire/Water/Nature/Volt/Void) and 6 archetypes "
        "(INFERNO/BULWARK/TIDAL/STORMCHAIN/REVENANT/FLUX). Phase-3 anchors "
        "ship at 0.4.0 — 2 set legendaries (Voidking Morr + World-Eater) "
        "plus 12 archetype epic anchors. Phase 4 fills the pool to 200 "
        "and reconciles legacy scaffolded rarities."
    )

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {len(ALL_ANCHORS)} anchor cards to {PACK_DIR}")
    print(f"Added {added} new manifest entries (overwrote {len(ALL_ANCHORS) - added} existing).")
    print(f"Manifest now lists {total} cards total at version {manifest['version']}.")


if __name__ == "__main__":
    main()
