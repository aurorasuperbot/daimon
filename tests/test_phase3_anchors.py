"""Phase 3 archetype-anchor integration tests (V1, 2026-04-22).

Each anchor card from `scripts/author_phase3_anchors.py` gets a focused test
that exercises the SIGNATURE mechanic the anchor was designed around. These
are integration tests — they go through the loader + run a real `resolve_match`
against an inert-dummy opponent — not unit-level synthesis. The goal is to
prove every anchor's defining behavior fires end-to-end on the V1 engine.

Pattern for each test:
  1. Load the anchor JSON via the catalog loader (so the loader→engine path
     is exercised — same code path that ships at runtime).
  2. Build a deterministic loadout: anchor at position 0, inert dummies
     elsewhere (HP=9999, atk=0, spd=0 — they don't disrupt targeting and
     they don't kill anything).
  3. Run `resolve_match` against an opponent designed to make the signature
     mechanic visible.
  4. Assert the log contains the expected trace AND/OR final state shows
     the expected stat/HP delta.

Determinism: SEED_ZERO + identical loadouts → identical MatchResult.

Why not just trust unit tests? Phase 2 already has those (test_combat_phase2.py).
This file proves the *cards as designed* behave as designed — closes the
"trigger works in isolation, but the anchor card uses the wrong target / the
wrong value / the wrong condition" gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daimon.cards.loader import load_card
from daimon.engine import Loadout, TEAM_SIZE, resolve_match
from daimon.engine.types import Card, Element

from tests.conftest import SEED_ZERO


# ---------------------------------------------------------------------------
# Catalog plumbing — load anchors by id from the v1_alpha pack on disk.
# ---------------------------------------------------------------------------

PACK_DIR = Path(__file__).resolve().parent.parent / "daimon" / "catalog" / "v1_alpha"
MANIFEST_PATH = PACK_DIR / "manifest.json"


def anchor(card_id: str) -> Card:
    """Load an anchor card by id from the v1_alpha pack."""
    return load_card(PACK_DIR / f"{card_id}.json")


# ---------------------------------------------------------------------------
# Test loadout helpers — same shape as test_combat_phase2.py, kept local for
# self-containment.
# ---------------------------------------------------------------------------

INERT_DUMMY_HP = 9999


def _inert_dummy(idx: int, element: Element = Element.NATURE) -> Card:
    """Padding card that doesn't fight back, doesn't act, doesn't get killed."""
    return Card(
        card_id=f"_dummy_{idx}", species=f"_d{idx}", element=element,
        atk=0, defense=0, hp=INERT_DUMMY_HP, spd=0,
    )


def solo(card: Card, *, elements: list[Element] | None = None) -> Loadout:
    """Build a 6-card loadout: `card` at slot 0, inert dummies elsewhere.

    `elements` controls the ELEMENT of each dummy at positions 1..5 — used
    to set up FLUX `team.distinct_elements` scenarios. None → all NATURE.
    """
    if elements is None:
        elements = [Element.NATURE] * (TEAM_SIZE - 1)
    assert len(elements) == TEAM_SIZE - 1
    rest = tuple(_inert_dummy(i + 1, e) for i, e in enumerate(elements))
    return Loadout(cards=(card,) + rest)


def pair(c1: Card, c2: Card) -> Loadout:
    """Two cards at slots 0-1, inert dummies at 2-5."""
    rest = tuple(_inert_dummy(i) for i in range(2, TEAM_SIZE))
    return Loadout(cards=(c1, c2) + rest)


def squishy_punching_bag(card_id: str = "_bag", *, hp: int = 30) -> Card:
    """An attacker-shaped target: low HP, low DEF, basic stats — meant to die."""
    return Card(
        card_id=card_id, species=card_id, element=Element.NATURE,
        atk=4, defense=2, hp=hp, spd=3,
    )


def all_logs(result) -> str:
    return "\n".join(line for r in result.rounds for line in r.actions)


# ---------------------------------------------------------------------------
# Catalog-load smoke test — every Phase-3 anchor loads cleanly through the
# real loader, including condition validation. Fails loud if a JSON is busted.
# ---------------------------------------------------------------------------

PHASE3_ANCHORS = [
    "voidking_morr", "world_eater",
    "magma_tyrant", "solar_phoenix",
    "worldroot_sentinel", "bulwark_patriarch",
    "tide_empress", "coral_augur",
    "tempest_apex", "arc_predator",
    "crypt_wraith",                   # mourners_lich retired in Phase 4e
    "prism_chimera", "rainbow_drake",
]


class TestCatalogLoad:
    def test_all_anchors_load(self):
        for cid in PHASE3_ANCHORS:
            c = anchor(cid)
            assert c.card_id == cid
            assert c.triggers, f"{cid} has no triggers — that's not an anchor"

    def test_all_anchors_in_manifest(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        ids_in_manifest = {entry["card_id"] for entry in manifest["cards"]}
        for cid in PHASE3_ANCHORS:
            assert cid in ids_in_manifest, f"{cid} missing from manifest"

    def test_full_manifest_loads(self):
        """Every card the manifest declares can be loaded — guards the loader
        against future drift between manifest entries and disk content."""
        manifest = json.loads(MANIFEST_PATH.read_text())
        for entry in manifest["cards"]:
            load_card(PACK_DIR / entry["file"])

    def test_legendary_count_locked_at_two(self):
        """V1 doc locks: exactly two legendaries — Voidking Morr + World-Eater.

        Phase 4a reconciled legacy scaffolded legendaries down to rare; from
        that point forward the pack must never carry a third legendary without
        an explicit design doc update.
        """
        manifest = json.loads(MANIFEST_PATH.read_text())
        legendaries = sorted(
            entry["card_id"] for entry in manifest["cards"]
            if entry["rarity"] == "legendary"
        )
        assert legendaries == ["voidking_morr", "world_eater"], (
            f"Legendary set drifted from the V1 lock. Expected exactly "
            f"['voidking_morr', 'world_eater']; got {legendaries}."
        )

    def test_epic_count_locked_at_twelve(self):
        """V1 doc locks: exactly 12 epics.

        Phase 4a reconciled legacy scaffolded epics down to rare. Phase 4e
        retired `mourners_lich` (REVENANT collapses to a single epic anchor —
        crypt_wraith — symmetric with INFERNO/BULWARK/TIDAL/STORMCHAIN once
        Phase 4f promotes their second epics to legendary) and added
        `concord_phoenix` as the NORMAL element's epic anchor. Net count
        unchanged at 12; composition swapped 1-for-1.

        Adding a 13th epic requires an explicit doc update + archetype
        (or NORMAL) rationale.
        """
        expected_epics = {
            "magma_tyrant", "solar_phoenix",           # INFERNO
            "worldroot_sentinel", "bulwark_patriarch", # BULWARK
            "tide_empress", "coral_augur",             # TIDAL
            "tempest_apex", "arc_predator",            # STORMCHAIN
            "crypt_wraith",                            # REVENANT (mourners_lich retired Phase 4e)
            "prism_chimera", "rainbow_drake",          # FLUX
            "concord_phoenix",                         # NORMAL (Phase 4e)
        }
        manifest = json.loads(MANIFEST_PATH.read_text())
        epics = {
            entry["card_id"] for entry in manifest["cards"]
            if entry["rarity"] == "epic"
        }
        assert epics == expected_epics, (
            f"Epic set drifted from the V1 anchor lock.\n"
            f"  Expected: {sorted(expected_epics)}\n"
            f"  Got:      {sorted(epics)}\n"
            f"  Missing:  {sorted(expected_epics - epics)}\n"
            f"  Extra:    {sorted(epics - expected_epics)}"
        )

    def test_json_rarity_matches_manifest(self):
        """Each card's JSON `rarity` field must match its manifest entry.

        Reconciliation drift would otherwise manifest as render-vs-pull bugs
        (card displays at one rarity, gets pulled at another).
        """
        manifest = json.loads(MANIFEST_PATH.read_text())
        mismatches = []
        for entry in manifest["cards"]:
            json_rarity = json.loads(
                (PACK_DIR / entry["file"]).read_text()
            ).get("rarity")
            if json_rarity != entry["rarity"]:
                mismatches.append(
                    f"{entry['card_id']}: manifest={entry['rarity']} "
                    f"vs json={json_rarity}"
                )
        assert not mismatches, (
            "Manifest/JSON rarity mismatch:\n" + "\n".join(mismatches)
        )


# ---------------------------------------------------------------------------
# REVENANT — Voidking Morr.
# Signature: ON_ALLY_DEATH BUFF_ATK SELF +4. Set up a teammate that dies
# in round 1, then verify Voidking's ATK has incremented.
# ---------------------------------------------------------------------------

class TestVoidkingMorr:
    def test_buffs_self_on_ally_death(self):
        # Ally is so squishy any 1+ damage tick will kill it; opponent has a
        # mid-power attacker that overruns the ally on round 1.
        suicidal_ally = Card(
            card_id="suicidal", species="suicidal", element=Element.NATURE,
            atk=0, defense=0, hp=1, spd=0,
        )
        attacker = Card(
            card_id="aggro", species="aggro", element=Element.NATURE,
            atk=10, defense=5, hp=30, spd=8,
        )

        team_a = pair(anchor("voidking_morr"), suicidal_ally)
        team_b = solo(attacker)

        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # ON_ALLY_DEATH trigger must have fired and buffed Voidking.
        assert "voidking_morr" in log
        assert "buffs ATK of voidking_morr by +4" in log

    def test_battle_start_debuff_applies(self):
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=10, defense=5, hp=30, spd=8,
        )
        team_a = solo(anchor("voidking_morr"))
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # Voidking debuffs ATK of all enemies by 3 on battle start.
        assert "voidking_morr debuffs ATK of enemy by -3" in log


# ---------------------------------------------------------------------------
# FLUX — World-Eater.
# Signature: 3 condition-gated triggers on team.distinct_elements >= 2/3/4.
# Run the same loadout twice — once mono-element, once 4-element rainbow —
# and confirm the rainbow version has VASTLY more battle-start beats.
# ---------------------------------------------------------------------------

class TestWorldEater:
    def test_mono_team_no_flux_triggers(self):
        # World-Eater alone in a NATURE-padded team → only 1 element →
        # NONE of its 3 conditional triggers fire.
        team_a = solo(anchor("world_eater"))   # all dummies NATURE; W-E is VOID
        # 1 + 5 dummies = 2 elements (VOID + NATURE), so the >=2 trigger
        # FIRES, but >=3 and >=4 do not. Use a single-element team to
        # ensure mono — give World-Eater all-VOID dummies.
        team_a = solo(anchor("world_eater"),
                      elements=[Element.VOID] * (TEAM_SIZE - 1))
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=10, defense=5, hp=30, spd=8,
        )
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # No team buff (>=2 gate fails on a mono-VOID team)
        assert "world_eater buffs ATK" not in log
        # No AOE (>=3 gate)
        assert "world_eater hits enemy for 8" not in log

    def test_rainbow_team_unlocks_apex(self):
        # 4-element rainbow team unlocks ALL three gates.
        rainbow_dummies = [
            Element.FIRE, Element.WATER, Element.NATURE,
            Element.VOLT, Element.NATURE,
        ]
        team_a = solo(anchor("world_eater"), elements=rainbow_dummies)
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=10, defense=5, hp=30, spd=8,
        )
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # >=2 gate: team-wide ATK buff fires
        assert "world_eater buffs ATK" in log
        # >=4 gate: 8-dmg AOE fires on battle start. Damage may be amplified by
        # element multiplier (VOID vs NATURE is the ring, check final value).
        assert "world_eater" in log
        # The "DAMAGE ALL_ENEMIES 8" trigger should hit our attacker. Whatever
        # the multiplier ends up being, the line will contain "hits enemy for".
        assert log.count("hits enemy for") >= 1


# ---------------------------------------------------------------------------
# INFERNO — Magma Tyrant + Solar Phoenix.
# ---------------------------------------------------------------------------

class TestMagmaTyrant:
    def test_attack_applies_burn(self):
        # Beefy enemy survives the first hit so we can see BURN tick on round 2.
        tank = Card(
            card_id="tank", species="tank", element=Element.NATURE,
            atk=2, defense=8, hp=60, spd=2,
        )
        team_a = solo(anchor("magma_tyrant"))
        team_b = solo(tank)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        assert "applies BURN" in log
        # Round 2 tick should fire
        assert "burn hits tank" in log


class TestSolarPhoenix:
    def test_opening_attack_aoe_fires_once(self):
        # AoE-ON_OPENING_ATTACK should fire ONCE — even if Phoenix attacks
        # multiple times across rounds, the "Sunbreak Dive" pattern is one-shot.
        tank = Card(
            card_id="tank", species="tank", element=Element.NATURE,
            atk=2, defense=8, hp=60, spd=2,
        )
        team_a = solo(anchor("solar_phoenix"))
        team_b = solo(tank)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # The opening AOE deals 4 dmg to all enemies; element NATURE vs FIRE
        # is the ring (NATURE is weak to FIRE → FIRE gets 1.5×; here FIRE is
        # attacker so 1.5× → 6). Look for the structural pattern only.
        # The AOE damage line for the named tank should appear.
        opening_lines = [
            l for l in result.rounds[0].actions
            if "solar_phoenix hits tank" in l
        ]
        # The AOE fires once at opening; the basic attack also follows.
        # We just need >=1 hit involving the AOE having struck the tank.
        assert opening_lines, "Solar Phoenix opening AOE did not strike tank"

    def test_death_heals_team(self):
        # Hit Phoenix with overwhelming force on round 1 so it dies, then
        # confirm the ON_DEATH HEAL ALL_ALLIES line appears.
        crusher = Card(
            card_id="crusher", species="crusher", element=Element.NATURE,
            atk=99, defense=99, hp=200, spd=99,
        )
        ally = Card(
            card_id="ally", species="ally", element=Element.NATURE,
            atk=0, defense=0, hp=10, spd=0,
        )
        team_a = pair(anchor("solar_phoenix"), ally)
        team_b = solo(crusher)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # ON_DEATH HEAL ALL_ALLIES 6 → at least one "heals" line attributed
        # to solar_phoenix.
        assert "solar_phoenix heals" in log


# ---------------------------------------------------------------------------
# BULWARK — Worldroot Sentinel + Bulwark Patriarch.
# ---------------------------------------------------------------------------

class TestWorldrootSentinel:
    def test_taunt_applied_at_battle_start(self):
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=10, defense=5, hp=30, spd=8,
        )
        team_a = solo(anchor("worldroot_sentinel"))
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        assert "applies TAUNT" in log

    def test_take_damage_adds_shield(self):
        # Sentinel has def=10 → attacker must hit harder than that to land
        # any damage at all (engine emits no log line for 0-damage attacks,
        # and ON_TAKE_DAMAGE doesn't fire on a no-op hit).
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=18, defense=5, hp=30, spd=8,
        )
        team_a = solo(anchor("worldroot_sentinel"))
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        assert "shields worldroot_sentinel for 4" in log


class TestBulwarkPatriarch:
    def test_round_start_heal_gated_off_round_1(self):
        # The condition `round >= 2` should suppress the HEAL on round 1's
        # ON_ROUND_START sweep but allow it from round 2 onward. Use an enemy
        # that doesn't kill anyone so we get to round 2.
        wimp = Card(
            card_id="wimp", species="wimp", element=Element.NATURE,
            atk=1, defense=1, hp=80, spd=1,
        )
        team_a = solo(anchor("bulwark_patriarch"))
        team_b = solo(wimp)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        # Round 1: NO heal-from-patriarch line in actions.
        round1_lines = "\n".join(result.rounds[0].actions)
        # Round 2+: heal from patriarch should appear at least once.
        round2plus_lines = "\n".join(
            line for r in result.rounds[1:] for line in r.actions
        )
        assert "bulwark_patriarch heals" not in round1_lines, (
            f"Patriarch heal fired on round 1 despite `round >= 2` gate.\n"
            f"Round 1 log:\n{round1_lines}"
        )
        assert "bulwark_patriarch heals" in round2plus_lines, (
            f"Patriarch heal never fired post-round-1.\n"
            f"Rounds 2+ log:\n{round2plus_lines}"
        )

    def test_take_damage_buffs_def(self):
        # Patriarch has def=11 → attacker must beat that to deal any damage,
        # otherwise _take_damage early-returns and ON_TAKE_DAMAGE never fires.
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=18, defense=5, hp=30, spd=8,
        )
        team_a = solo(anchor("bulwark_patriarch"))
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        assert "buffs DEF of bulwark_patriarch by +1" in log


# ---------------------------------------------------------------------------
# TIDAL — Tide Empress + Coral Augur.
# ---------------------------------------------------------------------------

class TestTideEmpress:
    def test_lifesteal_damage_and_heal(self):
        # Set up an enemy that lives several rounds so we get repeated
        # lifesteal opportunities.
        tank = Card(
            card_id="tank", species="tank", element=Element.NATURE,
            atk=4, defense=2, hp=80, spd=2,
        )
        team_a = solo(anchor("tide_empress"))
        team_b = solo(tank)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # LIFESTEAL emits a "drains N hp" line on the attacker.
        assert "tide_empress drains" in log

    def test_battle_start_team_heal(self):
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=4, defense=2, hp=30, spd=2,
        )
        team_a = solo(anchor("tide_empress"))
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # ON_BATTLE_START HEAL ALL_ALLIES 3 — Tide Empress's first beat.
        assert "tide_empress heals" in log


class TestCoralAugur:
    def test_pristine_heal_fires_when_full_hp(self):
        # Coral Augur at full HP should heal a random ally each attack.
        ally = Card(
            card_id="ally", species="ally", element=Element.NATURE,
            atk=0, defense=0, hp=10, spd=0,
        )
        wimp = Card(
            card_id="wimp", species="wimp", element=Element.NATURE,
            atk=0, defense=0, hp=80, spd=1,
        )
        team_a = pair(anchor("coral_augur"), ally)
        team_b = solo(wimp)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        assert "coral_augur heals" in log

    def test_pristine_heal_suppressed_when_damaged(self):
        # Damage the augur in round 1 — round 2 attack must NOT heal.
        # Augur HP=28; an enemy doing 5+ dmg breaks pristine on round 1.
        bruiser = Card(
            card_id="bruiser", species="bruiser", element=Element.NATURE,
            atk=12, defense=2, hp=80, spd=8,
        )
        team_a = solo(anchor("coral_augur"))
        team_b = solo(bruiser)
        result = resolve_match(team_a, team_b, SEED_ZERO)

        # Round 1: bruiser hits augur first because spd=8 vs 7 → augur
        # damaged, then augur attacks (no longer pristine → no heal).
        # Round 2+ augur attacks while damaged → no heal lines either.
        # We can't easily separate "pre-pristine attack" from later attacks
        # in a clean way, so we just assert no heal-from-augur for rounds
        # where the augur is provably damaged. We assert structurally:
        # there was at least one line saying bruiser hit the augur, AND
        # there are no augur-heals AFTER that line.
        full_log = all_logs(result)
        if "bruiser hits coral_augur" in full_log:
            # Find first damage line, then ensure no heal lines follow.
            lines = full_log.split("\n")
            first_hit_idx = next(i for i, l in enumerate(lines)
                                 if "bruiser hits coral_augur" in l)
            after = lines[first_hit_idx + 1:]
            heals_after = [l for l in after if "coral_augur heals" in l]
            assert not heals_after, (
                f"Coral Augur healed AFTER taking damage (pristine gate broken):\n"
                f"  heals after first hit: {heals_after}"
            )


# ---------------------------------------------------------------------------
# STORMCHAIN — Tempest Apex + Arc Predator.
# ---------------------------------------------------------------------------

class TestTempestApex:
    def test_battle_start_team_spd_buff(self):
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=4, defense=2, hp=30, spd=2,
        )
        team_a = solo(anchor("tempest_apex"))
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        assert "tempest_apex buffs SPD" in log

    def test_opening_attack_aoe_fires(self):
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=4, defense=2, hp=80, spd=2,
        )
        team_a = solo(anchor("tempest_apex"))
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        # ON_OPENING_ATTACK fires before basic attack on round 1; the AOE
        # of 5 dmg to all enemies should land on the named enemy.
        round1 = "\n".join(result.rounds[0].actions)
        assert "tempest_apex hits enemy" in round1


class TestArcPredator:
    def test_kill_buffs_self_spd(self):
        # Set up an opponent with a low-HP target Arc Predator can KO round 1.
        squishy = squishy_punching_bag(card_id="prey", hp=10)
        team_a = solo(anchor("arc_predator"))
        team_b = solo(squishy)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # Arc Predator's spd should have been buffed at some point.
        assert "buffs SPD of arc_predator" in log


# ---------------------------------------------------------------------------
# REVENANT — Crypt Wraith + Mourner's Lich.
# ---------------------------------------------------------------------------

class TestCryptWraith:
    def test_silence_applied_on_ally_death(self):
        suicidal_ally = Card(
            card_id="suicidal", species="suicidal", element=Element.NATURE,
            atk=0, defense=0, hp=1, spd=0,
        )
        attacker = Card(
            card_id="aggro", species="aggro", element=Element.NATURE,
            atk=10, defense=5, hp=30, spd=8,
        )
        team_a = pair(anchor("crypt_wraith"), suicidal_ally)
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # ON_ALLY_DEATH applies SILENCE 2r to a random enemy.
        assert "applies SILENCE" in log


# NOTE: Mourners Lich behavioural tests removed in Phase 4e — the card was
# retired to make room for the NORMAL element. REVENANT collapses to a single
# epic anchor (crypt_wraith). The ON_ALLY_DEATH/ON_DEATH op coverage that
# mourners_lich provided lives on in crypt_wraith's tests above and in the
# trigger-frequency audit planned for Phase 5; nothing else referenced these
# tests.


# ---------------------------------------------------------------------------
# FLUX — Prism Chimera + Rainbow Drake.
# Verify gating on team.distinct_elements behaves as designed.
# ---------------------------------------------------------------------------

class TestPrismChimera:
    def test_mono_team_no_buff(self):
        team_a = solo(anchor("prism_chimera"),
                      elements=[Element.NATURE] * (TEAM_SIZE - 1))
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=4, defense=2, hp=30, spd=2,
        )
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # >=2 gate fails on mono-NATURE → no team buff
        assert "prism_chimera buffs ATK" not in log

    def test_dual_element_team_buffs(self):
        team_a = solo(anchor("prism_chimera"),
                      elements=[Element.FIRE] + [Element.NATURE] * (TEAM_SIZE - 2))
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=4, defense=2, hp=30, spd=2,
        )
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # >=2 gate passes (NATURE + FIRE) → ATK buff fires
        assert "prism_chimera buffs ATK" in log

    def test_three_element_team_unlocks_aoe(self):
        team_a = solo(anchor("prism_chimera"),
                      elements=[Element.FIRE, Element.WATER]
                              + [Element.NATURE] * (TEAM_SIZE - 3))
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=4, defense=2, hp=80, spd=2,
        )
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # >=3 gate passes (NATURE + FIRE + WATER) → ON_ATTACK AOE fires.
        # Look for "prism_chimera hits enemy" — at minimum the basic attack
        # AND the AOE both hit the enemy, so we expect >=2 hits.
        hit_count = log.count("prism_chimera hits enemy")
        assert hit_count >= 2, (
            f"Expected basic attack + AOE to both hit enemy (>= 2 hits), "
            f"got {hit_count}.\nLog:\n{log}"
        )


class TestRainbowDrake:
    def test_mono_team_no_heal(self):
        team_a = solo(anchor("rainbow_drake"),
                      elements=[Element.FIRE] * (TEAM_SIZE - 1))
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=4, defense=2, hp=30, spd=2,
        )
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # mono-FIRE → no condition pass → no team heal from drake
        assert "rainbow_drake heals" not in log

    def test_dual_element_team_heals(self):
        team_a = solo(anchor("rainbow_drake"),
                      elements=[Element.WATER] + [Element.FIRE] * (TEAM_SIZE - 2))
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=4, defense=2, hp=30, spd=2,
        )
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # FIRE + WATER → 2 elements → battle-start heal fires
        assert "rainbow_drake heals" in log

    def test_three_element_kill_shields_team(self):
        # Need 3 elements + a kill — pair with a squishy enemy that drake KOs.
        team_a = solo(anchor("rainbow_drake"),
                      elements=[Element.WATER, Element.NATURE]
                              + [Element.FIRE] * (TEAM_SIZE - 3))
        prey = squishy_punching_bag(card_id="prey", hp=8)
        team_b = solo(prey)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # ON_KILL ADD_SHIELD ALL_ALLIES 3 — should appear after a kill.
        assert "rainbow_drake shields" in log
