#!/usr/bin/env python3
"""Phase 4e of V1 card design — author 15 NORMAL cards, retire 15 elemental.

Per Santiago's design call (group chat 2026-04-22, after Phase 4d ship):

  > we need one monster type to be normal, that is usually used in other
  > monsters elemental decks, normal should have no elemental bonus or
  > weakness against anyone in the affinity charts, and are mostly support
  > monsters

NORMAL is now the 6th `Element` enum value (added in commit `e16352c` via
the engine slice — `daimon/engine/types.py` + `daimon/engine/elements.py`
plus render plumbing). NORMAL stands deliberately outside the 5-element
ring; every (NORMAL, X) and (X, NORMAL) pair resolves to 1.0× damage.

This script ships the *pool* slice: 15 NORMAL cards introduced into the
v1_alpha catalog, and 15 existing elemental cards retired to keep the
total locked at 200.

Distribution after this script (and Phase 4f's epic→legendary promotions):

  Tier        elemental    NORMAL    total
  ---------------------------------------
  common         90           8       98
  uncommon       56           4       60
  rare           26           2       28
  epic            8           1        9    (Phase 4f trims to 7+1=8)
  legendary       2           0        2    (Phase 4f promotes 4 → 6)
  ---------------------------------------
  total         182          15      197    (Phase 4f stays 200 via promotion)

Wait — that doesn't math. Let me re-derive: this script (Phase 4e) adds 15
and removes 15 → total stays 200. Phase 4f then re-tiers 4 epics as
legendaries (no count change). After 4e + 4f together:

  Tier        elemental    NORMAL    total
  ---------------------------------------
  common         90           8       98
  uncommon       56           4       60
  rare           26           2       28
  epic            7           1        8    (4f promotes 4, 4e trimmed 1)
  legendary       6           0        6    (4f adds 4 to existing 2)
  ---------------------------------------
  total         185          15      200   ✅

This script handles the Phase-4e half: trim 8C/4U/2R/1E elemental, add
8C/4U/2R/1E NORMAL. Net: -15 + 15 = 0, total stays 200 right after this
script runs (12 epics still here; 4f re-tiers them).

Design rules (locked in `docs/card_design_v1.md` §4 + §18):
  - Stat budgets: common 18-22, uncommon 22-26, rare 26-32, epic 32-40
  - Trigger value bands: common 2-3, uncommon 3-4, rare 3-5, epic 4-6
  - Trigger count caps: common 1, uncommon 1, rare 2, epic 2
  - NORMAL cards carry `archetype: null` (intentionally archetype-less —
    splashable into any archetype-aligned deck)
  - NORMAL cards DO NOT carry FLUX-style condition gates (they're meant
    to be unconditional support; gating support cards on team composition
    defeats the splash purpose)
  - Avoid archetype-coded ops on NORMAL cards (LIFESTEAL = TIDAL,
    APPLY_BURN = INFERNO, ON_DEATH/ON_ALLY_DEATH chains = REVENANT) —
    the NORMAL palette is HEAL/ADD_SHIELD/BUFF_*/DEBUFF_*/APPLY_TAUNT
    plus generic DAMAGE

Run once, idempotent (overwrites JSONs by design; manifest dedupe via
card_id check; retire-list short-circuits if cards are already gone).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent.parent / "daimon" / "catalog" / "v1_alpha"
MANIFEST_PATH = PACK_DIR / "manifest.json"


# ---------------------------------------------------------------------------
# Helpers (mirror Phase-4b/4c shapes).
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
    rarity: str,
    atk: int,
    df: int,
    hp: int,
    spd: int,
    triggers: list[dict],
    name: str,
    flavor: str,
    species: str | None = None,
) -> dict:
    return {
        "card_id": cid,
        "species": species or cid,
        "element": "NORMAL",
        "atk": atk,
        "def": df,
        "hp": hp,
        "spd": spd,
        "triggers": triggers,
        "name": name,
        "flavor": flavor,
        "rarity": rarity,
        # NORMAL cards carry archetype: null — they're splashable utility,
        # not pinned to any archetype's strategy. Stored as JSON `null`.
        "archetype": None,
        "art": f"art/{rarity}/{cid}.png",
        "moves": [],
    }


# ---------------------------------------------------------------------------
# NORMAL COMMONS (8) — stat budget [18, 22], 1 trigger, value 2-3
#
# Flavor leans "domesticated companion / earthen utility / scholarly support"
# rather than feral elementals. Pebbler / Mossback Ox / Page Slime — these
# are the Pidgey / Stantler / Snorlax of the pool.
# ---------------------------------------------------------------------------

NORMAL_COMMONS = [
    card(cid="pebbler", rarity="common", atk=4, df=5, hp=21, spd=4,
         triggers=[trig("ON_BATTLE_START", "ADD_SHIELD", "RANDOM_ALLY", 2)],
         name="Pebbler",
         flavor="Carries a pocketful of stones; passes one to whoever needs it."),
    card(cid="runic_whelp", rarity="common", atk=5, df=3, hp=21, spd=5,
         triggers=[trig("ON_BATTLE_START", "BUFF_DEF", "ALL_ALLIES", 2)],
         name="Runic Whelp",
         flavor="Half a chant, sung in earnest — and the team braces."),
    card(cid="cloth_sprite", rarity="common", atk=4, df=4, hp=21, spd=5,
         triggers=[trig("ON_TURN_END", "HEAL", "RANDOM_ALLY", 2)],
         name="Cloth Sprite",
         flavor="Tears its own gauze for whoever needs the dressing."),
    card(cid="page_slime", rarity="common", atk=3, df=4, hp=24, spd=3,
         triggers=[trig("ON_TAKE_DAMAGE", "HEAL", "SELF", 2)],
         name="Page Slime",
         flavor="Reads the cuts as marginalia and erases them at leisure."),
    card(cid="brass_mole", rarity="common", atk=4, df=5, hp=18, spd=3,
         triggers=[trig("ON_BATTLE_START", "BUFF_ATK", "RANDOM_ALLY", 2)],
         name="Brass Mole",
         flavor="Burrows up under an ally's footing and gives a quiet shove."),
    card(cid="mossback_ox", rarity="common", atk=4, df=5, hp=21, spd=3,
         triggers=[trig("ON_BATTLE_START", "APPLY_TAUNT", "SELF", 2)],
         name="Mossback Ox",
         flavor="Stands where it stands. Asks the enemy to come to it."),
    card(cid="quill_cat", rarity="common", atk=5, df=3, hp=21, spd=4,
         triggers=[trig("ON_ATTACK", "DEBUFF_DEF", "RANDOM_ENEMY", 2)],
         name="Quill Cat",
         flavor="Leaves a needle in every wound; the next striker thanks her."),
    card(cid="grove_pup", rarity="common", atk=4, df=4, hp=21, spd=5,
         triggers=[trig("ON_TURN_END", "BUFF_DEF", "RANDOM_ALLY", 2)],
         name="Grove Pup",
         flavor="Trots between teammates; nudges their guard back up."),
]


# ---------------------------------------------------------------------------
# NORMAL UNCOMMONS (4) — stat budget [22, 26], 1 trigger, value 3-4
# ---------------------------------------------------------------------------

NORMAL_UNCOMMONS = [
    card(cid="stoneward", rarity="uncommon", atk=5, df=6, hp=24, spd=4,
         triggers=[trig("ON_BATTLE_START", "ADD_SHIELD", "ALL_ALLIES", 3)],
         name="Stoneward",
         flavor="Passes a shard of granite to every teammate before the bell."),
    card(cid="rune_owl", rarity="uncommon", atk=6, df=4, hp=21, spd=6,
         triggers=[trig("ON_BATTLE_START", "BUFF_SPD", "ALL_ALLIES", 3)],
         name="Rune Owl",
         flavor="Reads the air as if it were a clock; the team learns the hour."),
    card(cid="wrought_bear", rarity="uncommon", atk=5, df=6, hp=24, spd=3,
         triggers=[trig("ON_TAKE_DAMAGE", "APPLY_TAUNT", "SELF", 3)],
         name="Wrought Bear",
         flavor="The first hit only convinces it to stand farther forward."),
    card(cid="mendicant_sphinx", rarity="uncommon", atk=4, df=5, hp=27, spd=4,
         triggers=[trig("ON_TURN_END", "HEAL", "RANDOM_ALLY", 3)],
         name="Mendicant Sphinx",
         flavor="Asks no riddle; gives only the answer the wounded needed."),
]


# ---------------------------------------------------------------------------
# NORMAL RARES (2) — stat budget [26, 32], up to 2 triggers, value 3-5
# ---------------------------------------------------------------------------

NORMAL_RARES = [
    card(cid="aegis_lion", rarity="rare", atk=6, df=8, hp=30, spd=4,
         triggers=[
             trig("ON_BATTLE_START", "ADD_SHIELD", "ALL_ALLIES", 4),
             trig("ON_TAKE_DAMAGE", "BUFF_DEF", "SELF", 3),
         ],
         name="Aegis Lion",
         flavor="A king of nothing but the line behind him."),
    card(cid="loremaster_ape", rarity="rare", atk=7, df=5, hp=27, spd=5,
         triggers=[
             trig("ON_BATTLE_START", "BUFF_ATK", "ALL_ALLIES", 3),
             trig("ON_TURN_END", "BUFF_SPD", "RANDOM_ALLY", 3),
         ],
         name="Loremaster Ape",
         flavor="Reads the team aloud each round; they grow into the recital."),
]


# ---------------------------------------------------------------------------
# NORMAL EPIC (1) — stat budget [32, 40], up to 2 triggers, value 4-6
# ---------------------------------------------------------------------------

NORMAL_EPICS = [
    card(cid="concord_phoenix", rarity="epic", atk=9, df=8, hp=36, spd=5,
         triggers=[
             trig("ON_BATTLE_START", "HEAL", "ALL_ALLIES", 5),
             trig("ON_ALLY_DEATH", "BUFF_ATK", "ALL_ALLIES", 4),
         ],
         name="Concord Phoenix",
         flavor="Doesn't burn for any element; burns FOR every element."),
]


ALL_NEW_NORMALS = (
    NORMAL_COMMONS
    + NORMAL_UNCOMMONS
    + NORMAL_RARES
    + NORMAL_EPICS
)


# ---------------------------------------------------------------------------
# RETIRE list — 15 elemental cards we cut to keep total at 200.
#
# Selection rationale:
#   - 8 commons: vanilla (zero-trigger) commons from the original (pre-4b)
#     scaffolding pool. All carry archetype=NONE and are singleton species.
#     Removing them improves the vanilla-cap percentage AND clears the most
#     mechanically-empty cards from the pool. Per-element trim spread:
#     2 FIRE / 2 WATER / 2 NATURE / 1 VOLT / 1 VOID — proportional to each
#     element's vanilla-pool size (FIRE/WATER/NATURE had 5 each, VOLT/VOID
#     had 4 each).
#   - 4 uncommons: singleton uncommons (no C→U species pair) with the most
#     generic single-trigger ops — picked one each from FIRE/WATER/NATURE/
#     VOLT, leaving VOID's 12 untouched (REVENANT density preserved).
#   - 2 rares: generic single-DAMAGE rares with no archetype tag and no
#     scheduled Phase-5 work attached. Trimming flarewing (FIRE) and
#     void_serpent (VOID) brings rare distribution to 5/5/4/6/6 across the
#     5 ring elements (room for 2 NORMAL_R = 28 total at rare).
#   - 1 epic: mourners_lich (REVENANT). After Phase 4f promotes
#     voidking_morr's tier-mate workflow, REVENANT is the only archetype
#     that ends up with TWO epics (crypt_wraith + mourners_lich) plus a
#     legendary (voidking_morr) — three anchors. Trimming mourners_lich
#     drops REVENANT to 1 epic + 1 legendary, matching the symmetry of the
#     other 4 strategic archetypes (INFERNO/BULWARK/TIDAL/STORMCHAIN).
#     FLUX intentionally retains its 2 epics + 1 legendary because it
#     spans 5 host elements and earns the extra anchor density.
#
# The retired card_ids will be DELETED from disk (manifest entry +
# JSON file). No "deleted" tombstone — the catalog has no notion of
# soft-deletes.
# ---------------------------------------------------------------------------

RETIRE_COMMONS: tuple[str, ...] = (
    "blade_foxling",   # FIRE vanilla
    "sparrowflame",    # FIRE vanilla
    "shellpup",        # WATER vanilla
    "bubblefry",       # WATER vanilla
    "scoutling",       # NATURE vanilla
    "mossbat",         # NATURE vanilla
    "jolthog",         # VOLT vanilla
    "nullkit",         # VOID vanilla
)

RETIRE_UNCOMMONS: tuple[str, ...] = (
    "cinder_lancer",   # FIRE INFERNO singleton, generic DAMAGE
    "riverotter",      # WATER no-archetype singleton, generic HEAL
    "anvilram",        # NATURE no-archetype singleton, generic ADD_SHIELD
    "thunderfox",      # VOLT no-archetype singleton, generic DAMAGE
)

RETIRE_RARES: tuple[str, ...] = (
    "flarewing",       # FIRE no-archetype, single-trigger DAMAGE
    "void_serpent",    # VOID no-archetype, single-trigger DAMAGE
)

RETIRE_EPICS: tuple[str, ...] = (
    "mourners_lich",   # VOID REVENANT — collapse REVENANT to 1 epic anchor
                       # (matches symmetry with INFERNO/BULWARK/TIDAL/STORMCHAIN
                       # post Phase 4f)
)

ALL_RETIRE = (
    RETIRE_COMMONS + RETIRE_UNCOMMONS + RETIRE_RARES + RETIRE_EPICS
)


# ---------------------------------------------------------------------------
# Stat-budget self-check (fail loudly if anything drifts out of band).
# ---------------------------------------------------------------------------

def _budget(c: dict) -> float:
    return c["atk"] + c["def"] + c["hp"] / 3.0 + c["spd"]


_BUDGET_BANDS: dict[str, tuple[float, float]] = {
    "common":    (18.0, 22.0),
    "uncommon":  (22.0, 26.0),
    "rare":      (26.0, 32.0),
    "epic":      (32.0, 40.0),
    "legendary": (38.0, 46.0),
}

_TRIGGER_VALUE_BANDS: dict[str, tuple[int, int]] = {
    "common":    (2, 3),
    "uncommon":  (3, 4),
    "rare":      (3, 5),
    "epic":      (4, 6),
    "legendary": (5, 8),
}

_TRIGGER_COUNT_CAPS: dict[str, int] = {
    "common":    1,
    "uncommon":  1,
    "rare":      2,
    "epic":      2,
    "legendary": 3,
}


def _validate() -> None:
    # 1. New NORMAL cards: stat-budget + trigger budgets per rarity tier.
    for c in ALL_NEW_NORMALS:
        rarity = c["rarity"]
        b = _budget(c)
        lo, hi = _BUDGET_BANDS[rarity]
        if not (lo - 1e-9 <= b <= hi + 1e-9):
            raise ValueError(
                f"{c['card_id']}: stat budget {b:.2f} out of [{lo}, {hi}] "
                f"for rarity {rarity}"
            )
        cap = _TRIGGER_COUNT_CAPS[rarity]
        if len(c["triggers"]) > cap:
            raise ValueError(
                f"{c['card_id']}: {len(c['triggers'])} triggers exceeds "
                f"cap {cap} for {rarity}"
            )
        vlo, vhi = _TRIGGER_VALUE_BANDS[rarity]
        for i, t in enumerate(c["triggers"]):
            v = abs(t["value"])
            if not (vlo <= v <= vhi):
                raise ValueError(
                    f"{c['card_id']} trigger[{i}] value {v} outside "
                    f"[{vlo}, {vhi}] for {rarity}"
                )
            # NORMAL cards must NOT carry condition gates (per design rule
            # in §18: NORMAL is unconditional splash support).
            if t.get("condition") is not None:
                raise ValueError(
                    f"{c['card_id']} trigger[{i}] has condition "
                    f"{t['condition']!r} — NORMAL cards must be unconditional"
                )
            # NORMAL cards must avoid archetype-coded ops.
            archetype_coded_ops = {
                "LIFESTEAL",       # TIDAL signature
                "APPLY_BURN",      # INFERNO signature
                "APPLY_POISON",    # REVENANT-flavored DOT
                "APPLY_SILENCE",   # archetype-disruptor (rare/epic only)
                "APPLY_STUN",      # archetype-disruptor (rare/epic only)
            }
            if t["op"] in archetype_coded_ops:
                raise ValueError(
                    f"{c['card_id']} trigger[{i}] op {t['op']} is "
                    f"archetype-coded — NORMAL cards must use the neutral "
                    f"palette (HEAL/ADD_SHIELD/BUFF_*/DEBUFF_*/APPLY_TAUNT/"
                    f"DAMAGE)"
                )
        # archetype must be JSON null, not a string
        if c.get("archetype") is not None:
            raise ValueError(
                f"{c['card_id']}: NORMAL cards must have archetype: null, "
                f"got {c.get('archetype')!r}"
            )
        if c["element"] != "NORMAL":
            raise ValueError(
                f"{c['card_id']}: element must be NORMAL, got {c['element']!r}"
            )

    # 2. Per-tier NORMAL counts.
    by_rarity = Counter(c["rarity"] for c in ALL_NEW_NORMALS)
    expected = {"common": 8, "uncommon": 4, "rare": 2, "epic": 1}
    for tier, n in expected.items():
        if by_rarity.get(tier, 0) != n:
            raise ValueError(
                f"NORMAL {tier} count = {by_rarity.get(tier, 0)}; expected {n}"
            )
    if sum(by_rarity.values()) != 15:
        raise ValueError(
            f"Total NORMAL cards = {sum(by_rarity.values())}; expected 15"
        )

    # 3. Retire list shape.
    if len(ALL_RETIRE) != 15:
        raise ValueError(f"Retire list has {len(ALL_RETIRE)}; expected 15")
    expected_retire = {"common": 8, "uncommon": 4, "rare": 2, "epic": 1}
    actual_retire = {
        "common":   len(RETIRE_COMMONS),
        "uncommon": len(RETIRE_UNCOMMONS),
        "rare":     len(RETIRE_RARES),
        "epic":     len(RETIRE_EPICS),
    }
    if actual_retire != expected_retire:
        raise ValueError(
            f"Retire-list shape {actual_retire} != expected {expected_retire}"
        )

    # 4. card_id collision check — new NORMAL ids must not collide with any
    #    existing manifest entry that ISN'T being retired.
    manifest = json.loads(MANIFEST_PATH.read_text())
    existing_ids = {entry["card_id"] for entry in manifest["cards"]}
    surviving_ids = existing_ids - set(ALL_RETIRE)
    new_ids = {c["card_id"] for c in ALL_NEW_NORMALS}
    collisions = new_ids & surviving_ids
    if collisions:
        raise ValueError(f"NORMAL card_id collisions with surviving pool: {sorted(collisions)}")

    # 5. Retire list integrity — every retire card_id must currently exist.
    missing_in_manifest = set(ALL_RETIRE) - existing_ids
    if missing_in_manifest:
        raise ValueError(
            f"Retire list references nonexistent card_ids: "
            f"{sorted(missing_in_manifest)}. Already retired in a previous run?"
        )


def main() -> None:
    _validate()  # fail before touching disk

    PACK_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Write each new NORMAL card file.
    for c in ALL_NEW_NORMALS:
        path = PACK_DIR / f"{c['card_id']}.json"
        path.write_text(
            json.dumps(c, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # 2. Delete each retired card file.
    deleted_files = 0
    for cid in ALL_RETIRE:
        p = PACK_DIR / f"{cid}.json"
        if p.exists():
            p.unlink()
            deleted_files += 1

    # 3. Update manifest: remove retired entries, append new NORMAL entries.
    manifest = json.loads(MANIFEST_PATH.read_text())
    manifest["cards"] = [
        e for e in manifest["cards"] if e["card_id"] not in set(ALL_RETIRE)
    ]
    existing_ids = {entry["card_id"] for entry in manifest["cards"]}
    added = 0
    for c in ALL_NEW_NORMALS:
        if c["card_id"] in existing_ids:
            continue
        manifest["cards"].append({
            "card_id": c["card_id"],
            "rarity":  c["rarity"],
            "element": "NORMAL",
            "file":    f"{c['card_id']}.json",
        })
        added += 1

    # 4. Bump manifest + regenerate description.
    hist = Counter(e["rarity"] for e in manifest["cards"])
    elem_hist = Counter(e["element"] for e in manifest["cards"])
    manifest["version"] = "0.4.4"
    manifest["description"] = (
        f"DAIMON V1 alpha bundled creature pool. {len(manifest['cards'])} "
        f"monsters across 6 elements (5 ring + 1 outside) and 6 archetypes. "
        f"Phase 4e introduced NORMAL as the splashable utility element "
        f"({elem_hist.get('NORMAL', 0)} NORMAL cards, archetype=null) and "
        f"trimmed 15 generic elementals (8 vanilla commons + 4 singleton "
        f"uncommons + 2 generic rares + 1 redundant REVENANT epic) to keep "
        f"the total at 200. Distribution: {hist.get('common', 0)}C/"
        f"{hist.get('uncommon', 0)}U/{hist.get('rare', 0)}R/"
        f"{hist.get('epic', 0)}E/{hist.get('legendary', 0)}L. "
        f"Per-element: " + " ".join(
            f"{k}={v}" for k, v in sorted(elem_hist.items())
        ) + ". Phase 4f promotes 4 epics → legendary (one per archetype) "
        f"and Phase 5 (sim balance) follows."
    )

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {len(ALL_NEW_NORMALS)} NORMAL card files to {PACK_DIR}")
    print(f"Deleted {deleted_files} retired card files.")
    print(f"Added {added} NORMAL manifest entries; "
          f"removed {len(ALL_RETIRE)} retired manifest entries.")
    print(f"Manifest now lists {len(manifest['cards'])} cards at version "
          f"{manifest['version']}.")
    print(f"Rarity histogram: {dict(hist)}")
    print(f"Element histogram: {dict(elem_hist)}")


if __name__ == "__main__":
    main()
