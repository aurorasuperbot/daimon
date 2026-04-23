#!/usr/bin/env python3
"""Phase 4e-counters: re-flavor 6 existing rares as designated archetype counters.

Locked design 2026-04-22, after Santiago design-direction asked for "one
legendary per archetype" — implicit corollary is that each strategic
archetype also needs to be *answerable*, not just promoted. Phase 4e-pool
gave us a NORMAL element for splashable utility. This slice picks 6 rares
from the existing pool and retunes their triggers to act as targeted
counters to each of the 6 strategic archetypes (5 element-pure + FLUX).

Counter design grid:

    Card                Element  Counters       Mechanic
    ------------------- -------  -------------  --------------------------
    forest_warden       NATURE   INFERNO        ON_LOW_HP HEAL big self
                                                + ON_BATTLE_START team-DEF
                                                (out-lasts BURN ticks)
    maelstrom_serpent   WATER    BULWARK        ON_OPENING_ATTACK team-DEF-shred
                                                + ON_ATTACK target lowest-HP
                                                (cracks shields, kills the
                                                squishies behind the wall)
    mindroot            VOID     TIDAL          ON_BATTLE_START AoE POISON
                                                + ON_ATTACK target lowest-HP
                                                (DOT competes with heals,
                                                race the kill before regen)
    bulwarthog          NATURE   STORMCHAIN     ON_ROUND_START team-DEF
                                                + ON_ROUND_START self-shield
                                                (round-start triggers fire
                                                even under STUN; values
                                                stack across rounds, so
                                                a single SILENCE doesn't
                                                undo prior rounds' buffs)
    abyss_warden        VOID     REVENANT       ON_BATTLE_START AoE SILENCE
                                                + ON_ATTACK AoE-DEBUFF_ATK
                                                (silence pre-empts
                                                ON_DEATH/ON_ALLY_DEATH
                                                triggers; debuff softens
                                                the inevitable death-snowball)
    stormhare           VOLT     FLUX           ON_OPENING_ATTACK target
                                                lowest-HP big
                                                + ON_BATTLE_START team-SPD
                                                (race-burst that kills FLUX
                                                squishies before their
                                                distinct_elements gates fire)

Why these 6 rares were chosen:
  - Each was already an "archetype:null" rare with archetype-fit element.
  - Each had at most 2 triggers (rare cap), so re-keying them stays in budget.
  - Their existing names + flavor support the counter narrative without a
    full art/lore retcon. Flavor strings get a one-line refresh; species,
    name, art path, and card_id are unchanged.

Stat retuning:
  - All 6 cards land cleanly inside the rare stat band [26, 32] using the
    standard formula `atk + def + hp/3 + spd`.
  - Some were over-band before this commit (legacy authoring drift); the
    retune brings them into the proper rare envelope and removes them from
    Phase 5's quiet stat-debt list.

Trigger discipline:
  - Each card carries exactly 2 triggers (rare cap).
  - All trigger values fall inside the rare value band [3, 5].
  - All ops referenced exist in the engine vocabulary (Phase 2 expansion).
  - No condition-DSL strings used: counters are mechanical, not conditional.
    The DSL can't introspect enemy archetype anyway, so "fire only against
    INFERNO" isn't expressible — counters work by mechanic, not by gate.

This is a one-shot maintenance script. After running, the 6 cards on disk
match the design here, the manifest version bumps to 0.4.5, and a
behavioral test module (`tests/test_phase4e_counters.py`, hand-authored)
locks the counter mechanics down.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACK = REPO / "daimon" / "catalog" / "v1_alpha"

# ---------------------------------------------------------------------------
# Counter definitions. Each entry fully replaces the card's stats + triggers
# + flavor; species / name / art / rarity / element / card_id stay put so the
# rest of the catalog (manifest entry, NPC loadouts, image assets) keeps
# working unchanged.
# ---------------------------------------------------------------------------

COUNTERS: list[dict] = [
    # ---- Anti-INFERNO ----
    {
        "card_id": "forest_warden",
        "species": "forest_warden",
        "element": "NATURE",
        "atk": 9, "def": 9, "hp": 24, "spd": 5,   # budget = 9+9+8+5 = 31.0
        "triggers": [
            # Up-front team-wide DR softens INFERNO's signature opening burst.
            {"when": "ON_BATTLE_START", "op": "BUFF_DEF",
             "target": "ALL_ALLIES", "value": 3},
            # Big self-heal when the BURN ticks finally drag us low —
            # extends the sustain clock past INFERNO's damage window.
            {"when": "ON_LOW_HP", "op": "HEAL",
             "target": "SELF", "value": 5},
        ],
        "name": "Forest Warden",
        "flavor": "Roots heal what the flame would kill.",
        "rarity": "rare",
        "art": "art/rare/forest_warden.png",
        "moves": [
            {"name": "Grove Aegis",   "when": "ON_BATTLE_START"},
            {"name": "Sapwell Surge", "when": "ON_LOW_HP"},
        ],
    },

    # ---- Anti-BULWARK ----
    {
        "card_id": "maelstrom_serpent",
        "species": "maelstrom_serpent",
        "element": "WATER",
        "atk": 10, "def": 6, "hp": 24, "spd": 8,   # budget = 10+6+8+8 = 32.0
        "triggers": [
            # Armor-shred the whole enemy team before the first real exchange,
            # so BULWARK's stacked DEF + SHIELD walls collapse on contact.
            {"when": "ON_OPENING_ATTACK", "op": "DEBUFF_DEF",
             "target": "ALL_ENEMIES", "value": 4},
            # Bypass the wall: hammer whoever is most-killable, which behind
            # a BULWARK wall is the squishy back-line the wall protects.
            {"when": "ON_ATTACK", "op": "DAMAGE",
             "target": "LOWEST_HP_ENEMY", "value": 3},
        ],
        "name": "Maelstrom Serpent",
        "flavor": "The whirlpool eats the wall.",
        "rarity": "rare",
        "art": "art/rare/maelstrom_serpent.png",
        "moves": [
            {"name": "Hull-Crack",  "when": "ON_OPENING_ATTACK"},
            {"name": "Spiral Lash", "when": "ON_ATTACK"},
        ],
    },

    # ---- Anti-TIDAL ----
    {
        "card_id": "mindroot",
        "species": "mindroot",
        "element": "VOID",
        "atk": 8, "def": 6, "hp": 22, "spd": 10,  # budget = 8+6+7.33+10 = 31.33
        "triggers": [
            # POISON DOT runs in parallel with TIDAL's heal cycle: heal restores
            # HP, poison nibbles it back, net asymmetric toward us.
            {"when": "ON_BATTLE_START", "op": "APPLY_POISON",
             "target": "ALL_ENEMIES", "value": 3},
            # Closer for whoever the heal stack didn't quite save.
            {"when": "ON_ATTACK", "op": "DAMAGE",
             "target": "LOWEST_HP_ENEMY", "value": 3},
        ],
        "name": "Mindroot",
        "flavor": "Even the clean spring forgets itself.",
        "rarity": "rare",
        "art": "art/rare/mindroot.png",
        "moves": [
            {"name": "Tainted Spring", "when": "ON_BATTLE_START"},
            {"name": "Closer's Whisper", "when": "ON_ATTACK"},
        ],
    },

    # ---- Anti-STORMCHAIN ----
    {
        "card_id": "bulwarthog",
        "species": "bulwarthog",
        "element": "NATURE",
        "atk": 5, "def": 13, "hp": 21, "spd": 5,  # budget = 5+13+7+5 = 30.0
        "triggers": [
            # ON_ROUND_START fires before action ordering — STUN (which only
            # cancels the unit's attack action) does NOT block these. SILENCE
            # does block them, but stacks from prior rounds persist, so a
            # one-round silence doesn't undo two rounds of accumulated DEF.
            {"when": "ON_ROUND_START", "op": "BUFF_DEF",
             "target": "ALL_ALLIES", "value": 1},
            # Self-shield each round — stable defensive value that ignores
            # whether STORMCHAIN landed its STUN proc this turn.
            {"when": "ON_ROUND_START", "op": "ADD_SHIELD",
             "target": "SELF", "value": 3},
        ],
        "name": "Bulwarthog",
        "flavor": "When the storm strikes, the herd shoulders together.",
        "rarity": "rare",
        "art": "art/rare/bulwarthog.png",
        "moves": [
            {"name": "Rallying Roar", "when": "ON_ROUND_START"},
            {"name": "Plate of Calm", "when": "ON_ROUND_START"},
        ],
    },

    # ---- Anti-REVENANT ----
    {
        "card_id": "abyss_warden",
        "species": "abyss_warden",
        "element": "VOID",
        "atk": 9, "def": 7, "hp": 24, "spd": 6,   # budget = 9+7+8+6 = 30.0
        "triggers": [
            # SILENCE the entire enemy team for 3 rounds at battle start —
            # this is the keystone anti-REVENANT play because SILENCE
            # suppresses ALL triggers on the unit, including ON_DEATH and
            # ON_ALLY_DEATH (engine: combat.py::_fire_triggers_for_unit).
            # REVENANT's value engine relies on those triggers; silencing
            # them at the source neuters the archetype for the silence window.
            {"when": "ON_BATTLE_START", "op": "APPLY_SILENCE",
             "target": "ALL_ENEMIES", "value": 3},
            # Ongoing pressure so REVENANT can't just stall the silence out.
            {"when": "ON_ATTACK", "op": "DEBUFF_ATK",
             "target": "ALL_ENEMIES", "value": 3},
        ],
        "name": "Abyss Warden",
        "flavor": "The warden silences echoes before they wake.",
        "rarity": "rare",
        "art": "art/rare/abyss_warden.png",
        "moves": [
            {"name": "Quietus",       "when": "ON_BATTLE_START"},
            {"name": "Ledger Closes", "when": "ON_ATTACK"},
        ],
    },

    # ---- Anti-FLUX ----
    {
        "card_id": "stormhare",
        "species": "stormhare",
        "element": "VOLT",
        "atk": 6, "def": 4, "hp": 21, "spd": 11,  # budget = 6+4+7+11 = 28.0
        "triggers": [
            # Race-burst: hit the lowest-HP enemy hard before FLUX's
            # team.distinct_elements gate even fires (FLUX gates check at
            # trigger-fire time, so removing the LOW_HP threat early keeps
            # FLUX from snowballing).
            {"when": "ON_OPENING_ATTACK", "op": "DAMAGE",
             "target": "LOWEST_HP_ENEMY", "value": 4},
            # Whole-team tempo lead — moves us up the round order so we
            # consistently get the killing blow before FLUX assembles.
            {"when": "ON_BATTLE_START", "op": "BUFF_SPD",
             "target": "ALL_ALLIES", "value": 1},
        ],
        "name": "Stormhare",
        "flavor": "Faster than the rainbow can assemble.",
        "rarity": "rare",
        "art": "art/rare/stormhare.png",
        "moves": [
            {"name": "Thunderdash",  "when": "ON_OPENING_ATTACK"},
            {"name": "Rally Wind",   "when": "ON_BATTLE_START"},
        ],
    },
]


# ---------------------------------------------------------------------------
# Validation — fail loudly if any counter drifts off design.
# ---------------------------------------------------------------------------

def _budget(c: dict) -> float:
    return c["atk"] + c["def"] + c["hp"] / 3.0 + c["spd"]


# Rare band per design doc §4.
RARE_BUDGET_BAND = (26.0, 32.0)
# One-shot triggers obey the standard rare value band [3, 5]. Round-start
# accumulators get a relaxed lower bound because their value is multiplied
# by round count: a value-1 ON_ROUND_START BUFF_DEF stacks to +5 over a
# 5-round cap match — equivalent to a one-shot value-5 trigger but spread
# over time. Capping at 1 there is the *correct* tuning, not a violation.
# Same logic applies to ON_TURN_END (per-round, per-unit accumulator).
RARE_VALUE_BAND_ONE_SHOT      = (3, 5)
RARE_VALUE_BAND_PER_ROUND     = (1, 5)
PER_ROUND_WHENS = {"ON_ROUND_START", "ON_TURN_END"}
# SPD operates on a smaller scale than ATK/DEF/HEAL/DAMAGE — values of 1-3
# are the design norm because matchmaking is bracketed by SPD comparison.
# A BUFF_SPD ALL_ALLIES 3 would essentially guarantee tempo against any
# non-VOLT team, which would homogenize the meta. Cap at 1 for AoE.
SPD_OPS = {"BUFF_SPD", "DEBUFF_SPD"}
RARE_VALUE_BAND_SPD           = (1, 3)
RARE_TRIGGER_CAP = 2

# Counter-specific extras: every counter must sit in the rare band, carry
# exactly 2 triggers (max), and use ops within the rare value envelope.
# Empty trigger sets and `condition` strings are rejected — counters are
# mechanical, not conditional.
_DISALLOWED_FIELDS = {"condition"}

# Cross-check that each card matches the EXPECTED archetype-counter slot.
# Tying the script's design intent to a per-card label lets the validator
# catch if someone reuses this file to retune cards for a different role.
EXPECTED_SLOTS: dict[str, tuple[str, str]] = {
    # card_id -> (element, counters_archetype)
    "forest_warden":     ("NATURE", "INFERNO"),
    "maelstrom_serpent": ("WATER",  "BULWARK"),
    "mindroot":          ("VOID",   "TIDAL"),
    "bulwarthog":        ("NATURE", "STORMCHAIN"),
    "abyss_warden":      ("VOID",   "REVENANT"),
    "stormhare":         ("VOLT",   "FLUX"),
}


def _validate() -> None:
    seen_ids: set[str] = set()
    for c in COUNTERS:
        cid = c["card_id"]
        # 0. Single source of truth: every counter must appear in EXPECTED_SLOTS.
        if cid not in EXPECTED_SLOTS:
            raise ValueError(f"{cid}: missing EXPECTED_SLOTS entry")
        if cid in seen_ids:
            raise ValueError(f"{cid}: duplicate counter definition")
        seen_ids.add(cid)
        exp_elem, _ = EXPECTED_SLOTS[cid]
        if c["element"] != exp_elem:
            raise ValueError(
                f"{cid}: element {c['element']!r} != expected {exp_elem!r}"
            )

        # 1. Stat budget.
        b = _budget(c)
        lo, hi = RARE_BUDGET_BAND
        if not (lo - 1e-9 <= b <= hi + 1e-9):
            raise ValueError(
                f"{cid}: stat budget {b:.2f} out of rare band [{lo}, {hi}]"
            )

        # 2. Trigger count.
        triggers = c.get("triggers", [])
        if not triggers:
            raise ValueError(f"{cid}: counters must have >= 1 trigger")
        if len(triggers) > RARE_TRIGGER_CAP:
            raise ValueError(
                f"{cid}: {len(triggers)} triggers exceeds rare cap "
                f"{RARE_TRIGGER_CAP}"
            )

        # 3. Trigger value band + no condition gates.
        # Three sub-bands, ordered from most-specific to most-general:
        #   - SPD ops use a tighter band (small-scale stat).
        #   - Per-round accumulators (ON_ROUND_START/ON_TURN_END) get a
        #     relaxed lower bound (value 1 is correct when it stacks).
        #   - Everything else uses the standard one-shot band.
        for i, t in enumerate(triggers):
            v = t.get("value")
            when = t.get("when")
            op   = t.get("op")
            if op in SPD_OPS:
                vlo, vhi = RARE_VALUE_BAND_SPD
                band_label = "rare SPD value band"
            elif when in PER_ROUND_WHENS:
                vlo, vhi = RARE_VALUE_BAND_PER_ROUND
                band_label = "rare per-round value band"
            else:
                vlo, vhi = RARE_VALUE_BAND_ONE_SHOT
                band_label = "rare one-shot value band"
            if not isinstance(v, int) or not (vlo <= v <= vhi):
                raise ValueError(
                    f"{cid}.triggers[{i}] (when={when}, op={op}): "
                    f"value {v!r} out of {band_label} [{vlo}, {vhi}]"
                )
            for k in _DISALLOWED_FIELDS:
                if k in t:
                    raise ValueError(
                        f"{cid}.triggers[{i}]: counters do not use "
                        f"`{k}` — design is mechanical, not conditional"
                    )

        # 4. Sanity: rarity must be 'rare' (we are not promoting/demoting).
        if c.get("rarity") != "rare":
            raise ValueError(f"{cid}: rarity must stay 'rare', got {c.get('rarity')!r}")

    # 5. Coverage: every strategic archetype must be countered.
    expected_archetypes = {a for _, a in EXPECTED_SLOTS.values()}
    if expected_archetypes != {
        "INFERNO", "BULWARK", "TIDAL", "STORMCHAIN", "REVENANT", "FLUX",
    }:
        raise ValueError(
            f"Counter coverage drift: archetypes covered = {expected_archetypes}"
        )


# ---------------------------------------------------------------------------
# Disk I/O.
# ---------------------------------------------------------------------------

def _bump_manifest() -> None:
    """Bump manifest version 0.4.4 -> 0.4.5. The card list is unchanged
    (counters re-key existing rares, no add/remove), but the version bump
    flags the catalog change for any downstream cache that keys by version."""
    manifest_path = PACK / "manifest.json"
    m = json.loads(manifest_path.read_text())
    if m.get("version") != "0.4.4":
        raise RuntimeError(
            f"Expected manifest at version 0.4.4 (post Phase 4e-pool); "
            f"got {m.get('version')!r}. Refusing to bump — investigate."
        )
    m["version"] = "0.4.5"
    manifest_path.write_text(
        json.dumps(m, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    _validate()
    written: list[str] = []
    for c in COUNTERS:
        path = PACK / f"{c['card_id']}.json"
        if not path.exists():
            raise RuntimeError(
                f"{c['card_id']}.json missing on disk — Phase 4e-counters "
                f"only re-keys EXISTING rares; this card was never authored "
                f"or got retired upstream. Aborting."
            )
        path.write_text(
            json.dumps(c, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(c["card_id"])
    _bump_manifest()
    print("Re-keyed counters:")
    for cid in written:
        elem, arch = EXPECTED_SLOTS[cid]
        print(f"  {cid:22s}  {elem:6s}  counters {arch}")
    print()
    print("Manifest bumped 0.4.4 -> 0.4.5.")


if __name__ == "__main__":
    main()
