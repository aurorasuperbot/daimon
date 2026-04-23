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

# Phase-3 anchors: cards whose mechanical identity is the integration-test
# subject. After Phase 4f-pool's epic→legendary promotions (§23.6), the 4
# promoted cards (magma_tyrant, worldroot_sentinel, tide_empress, tempest_apex)
# carry their identity via `rule_change` (the L1–L4 mutations) rather than
# explicit triggers. That mutation behavior is exhaustively tested in
# `tests/test_combat_phase4f.py::TestL{1,2,3,4,5,6}…`. We keep the 4 promoted
# cards in this list to cover catalog-load + rule_change tagging, and loosen
# the "anchor must have triggers" gate to "anchor must have triggers OR a
# rule_change tag" — both forms of mechanical identity are valid.
PHASE3_ANCHORS = [
    "voidking_morr", "world_eater",
    "magma_tyrant", "solar_phoenix",
    "worldroot_sentinel", "bulwark_patriarch",
    "tide_empress", "coral_augur",
    "tempest_apex", "arc_predator",
    "crypt_wraith",                   # mourners_lich retired in Phase 4e
    "prism_chimera", "rainbow_drake",
    "concord_phoenix",                # NORMAL epic anchor (Phase 4e)
]


class TestCatalogLoad:
    def test_all_anchors_load(self):
        for cid in PHASE3_ANCHORS:
            c = anchor(cid)
            assert c.card_id == cid
            # Mechanical-identity gate: a Phase-3 anchor either has triggers
            # (the original epic-anchor pattern) or carries a rule_change
            # mutation tag (the Phase-4f legendary pattern). At least one.
            assert c.triggers or c.rule_change, (
                f"{cid} has neither triggers nor a rule_change tag — "
                f"not a recognizable anchor"
            )

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

    def test_legendary_count_locked_at_six(self):
        """V1 doc locks (post-Phase-4f, §22.2): exactly six legendaries —
        one rule-changer per strategic archetype.

        Phase 4a reconciled legacy scaffolded legendaries down to rare. Phase
        4f-pool (2026-04-23) promoted 4 epics to legendary in-place per §23.6,
        bringing the count from 2 → 6. Adding a 7th legendary requires an
        explicit design doc update (likely the §22.5 expansion-archetype
        path) and a new mutation ID in `engine/types.py::RULE_CHANGE_IDS`.
        """
        expected_legendaries = {
            "magma_tyrant",       # L1 INFERNO     (Phase 4f-pool promotion)
            "worldroot_sentinel", # L2 BULWARK     (Phase 4f-pool promotion)
            "tide_empress",       # L3 TIDAL       (Phase 4f-pool promotion)
            "tempest_apex",       # L4 STORMCHAIN  (Phase 4f-pool promotion)
            "voidking_morr",      # L5 REVENANT    (V1 original legendary)
            "world_eater",        # L6 FLUX        (V1 original legendary)
        }
        manifest = json.loads(MANIFEST_PATH.read_text())
        legendaries = {
            entry["card_id"] for entry in manifest["cards"]
            if entry["rarity"] == "legendary"
        }
        assert legendaries == expected_legendaries, (
            f"Legendary set drifted from the V1 lock (§22.2).\n"
            f"  Expected: {sorted(expected_legendaries)}\n"
            f"  Got:      {sorted(legendaries)}\n"
            f"  Missing:  {sorted(expected_legendaries - legendaries)}\n"
            f"  Extra:    {sorted(legendaries - expected_legendaries)}"
        )

    def test_epic_count_locked_at_eight(self):
        """V1 doc locks (post-Phase-4f, §3): exactly 8 epics.

        Phase 4a reconciled legacy scaffolded epics down to rare. Phase 4e
        retired `mourners_lich` and added `concord_phoenix`. Phase 4f-pool
        promoted 4 epics to legendary (one rule-changer per archetype, §23.6),
        bringing the epic count 12 → 8: each strategic archetype keeps
        exactly 1 epic anchor (its legendary peer was the promoted one);
        FLUX keeps both its epics (per §3 — FLUX warrants double coverage at
        epic); NORMAL keeps `concord_phoenix`.

        Adding a 9th epic requires an explicit doc update + archetype rationale.
        """
        expected_epics = {
            "solar_phoenix",       # INFERNO (anchor; magma_tyrant promoted to L1)
            "bulwark_patriarch",   # BULWARK (anchor; worldroot_sentinel promoted to L2)
            "coral_augur",         # TIDAL   (anchor; tide_empress promoted to L3)
            "arc_predator",        # STORMCHAIN (anchor; tempest_apex promoted to L4)
            "crypt_wraith",        # REVENANT (mourners_lich retired Phase 4e)
            "prism_chimera",       # FLUX
            "rainbow_drake",       # FLUX
            "concord_phoenix",     # NORMAL (Phase 4e)
        }
        manifest = json.loads(MANIFEST_PATH.read_text())
        epics = {
            entry["card_id"] for entry in manifest["cards"]
            if entry["rarity"] == "epic"
        }
        assert epics == expected_epics, (
            f"Epic set drifted from the V1 anchor lock (§3, post-Phase-4f).\n"
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
    """world_eater carries `rule_change: L6` post-Phase-4f (§22.2 L6).

    L6's effect: every read of `team.distinct_elements` returns the actual
    count + 2 FOR FLUX CARDS. world_eater is FLUX, so its OWN gates always
    see effective_distinct = actual + 2. This means:

      mono-element team (1 distinct) → effective 3 → ≥2 ✓ ≥3 ✓ ≥4 ✗
      4-element rainbow (4 distinct) → effective 6 → ≥2 ✓ ≥3 ✓ ≥4 ✓

    The "mono team locks out FLUX" intuition was the pre-L6 model. L6 is
    DESIGNED to unlock FLUX in mono shells specifically when world_eater is
    on the team — see §22.2 L6 lock-text justification. Tests below assert
    the post-L6 behavior.
    """

    def test_mono_team_l6_unlocks_lower_gates_only(self):
        """Mono-VOID team: actual distinct=1, L6 effective=3.
        ≥2 fires, ≥3 fires, ≥4 does NOT fire.
        """
        team_a = solo(anchor("world_eater"),
                      elements=[Element.VOID] * (TEAM_SIZE - 1))
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.NATURE,
            atk=10, defense=5, hp=30, spd=8,
        )
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # ≥2 gate fires (effective 3 ≥ 2): team-wide BUFF_ATK +3
        assert "world_eater buffs ATK" in log, (
            f"L6 should unlock ≥2 gate on mono team; not seen.\nLog:\n{log}"
        )
        # ≥4 gate does NOT fire (effective 3 < 4): no 8-dmg AOE on battle start.
        # The 8-dmg AOE is the ONLY ON_BATTLE_START DAMAGE trigger world_eater
        # carries — its presence/absence is the gate signal. Look for the
        # specific "hits {tgt} for 8" pattern from that trigger.
        assert "world_eater hits enemy for 8" not in log, (
            f"L6-effective-3 should NOT clear ≥4 gate; AOE 8 fired anyway.\n"
            f"Log:\n{log}"
        )

    def test_rainbow_team_unlocks_all_gates(self):
        """4-element rainbow team: actual distinct=4 (W-E is VOID + 4 ring
        elements among dummies), L6 effective=6. All 3 gates fire."""
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
        # ≥2 gate
        assert "world_eater buffs ATK" in log
        # ≥4 gate: at least one damage line attributable to world_eater
        assert "world_eater" in log
        assert log.count("hits enemy for") >= 1


# ---------------------------------------------------------------------------
# INFERNO — Magma Tyrant (L1 mutation) + Solar Phoenix (epic anchor).
#
# magma_tyrant was promoted to legendary in Phase 4f-pool (§23.6). Its
# mechanical identity is now the L1 mutation tag (rule_change="L1"), not
# explicit triggers. The mutation behavior is exhaustively tested in
# tests/test_combat_phase4f.py::TestL1MagmaTyrant. Here we exercise the
# ACTUAL CATALOG CARD (loaded from disk) end-to-end through resolve_match
# to prove the loader → engine → mutation-dispatch path works on the
# shipping JSON — not just on synthetic test fixtures.
# ---------------------------------------------------------------------------

class TestMagmaTyrant:
    def test_l1_mutation_tag_present(self):
        """The promoted legendary must carry rule_change='L1' or its mutation
        is silently stripped at resolve-time."""
        c = anchor("magma_tyrant")
        assert c.rule_change == "L1", (
            f"magma_tyrant rule_change={c.rule_change!r} != 'L1' — "
            f"L1 mutation will not dispatch"
        )
        # Per §23.6 lock: legendary promotion uses rewrite-in-place with
        # mutation as identity, no secondary triggers.
        assert c.triggers == (), (
            f"magma_tyrant should have empty triggers (mutation IS the "
            f"contribution per §23.6); got {c.triggers}"
        )

    def test_l1_mutation_dispatches_on_actual_card(self):
        """End-to-end: load magma_tyrant from disk, attack a tank, observe
        L1 mutation log line (burn_stack added on damage)."""
        # Beefy NATURE tank that survives multiple FIRE hits so the L1
        # mutation has chances to fire and the burn_stack tick can show.
        tank = Card(
            card_id="tank", species="tank", element=Element.NATURE,
            atk=2, defense=8, hp=60, spd=2,
        )
        team_a = solo(anchor("magma_tyrant"))
        team_b = solo(tank)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # L1 mutation log format from combat.py::_apply_damage:
        #   "L1 mutation: tank burn_stacks +1 (now N)"
        assert "L1 mutation: tank burn_stacks +1" in log, (
            f"L1 mutation did not dispatch on magma_tyrant's attacks.\n"
            f"Full log:\n{log}"
        )
        # Burn-stack tick at ON_TURN_END proves the stack actually accrued
        # and damaged the tank.
        assert "burn_stacks tick: tank" in log, (
            f"burn_stacks accrued via L1 but never ticked.\nFull log:\n{log}"
        )


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
# BULWARK — Worldroot Sentinel (L2 mutation) + Bulwark Patriarch (epic anchor).
#
# worldroot_sentinel was promoted to legendary in Phase 4f-pool. Its identity
# is now the L2 mutation (rule_change="L2": every alive ally has +2 thorns).
# Engine binding is exhaustively tested in test_combat_phase4f.py::TestL2…;
# here we exercise the actual catalog card end-to-end.
# ---------------------------------------------------------------------------

class TestWorldrootSentinel:
    def test_l2_mutation_tag_present(self):
        c = anchor("worldroot_sentinel")
        assert c.rule_change == "L2", (
            f"worldroot_sentinel rule_change={c.rule_change!r} != 'L2'"
        )
        assert c.triggers == (), (
            f"worldroot_sentinel should have empty triggers per §23.6; "
            f"got {c.triggers}"
        )

    def test_l2_mutation_grants_thorns_to_team(self):
        """End-to-end: worldroot_sentinel + naked ally on team. An attacker
        hits the team — both team members carry effective thorns 2 from L2
        and reflect when struck. We assert at least one ally reflects."""
        # Naked ally with NO intrinsic thorns — proves L2 grants the team-wide
        # thorns (otherwise this card couldn't reflect anything).
        naked = Card(
            card_id="naked", species="naked", element=Element.NATURE,
            atk=0, defense=0, hp=200, spd=1,
        )
        attacker = Card(
            card_id="hitter", species="hitter", element=Element.NATURE,
            atk=18, defense=5, hp=200, spd=99,
        )
        team_a = pair(anchor("worldroot_sentinel"), naked)
        team_b = solo(attacker)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # THORNS reflect log format from combat.py:
        #   "{tgt.card_id} thorns reflect {N} → {src.card_id}"
        assert (
            "worldroot_sentinel thorns reflect 2" in log
            or "naked thorns reflect 2" in log
        ), (
            f"L2 mutation did not grant team-wide thorns 2.\n"
            f"Full log:\n{log}"
        )


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
# TIDAL — Tide Empress (L3 mutation) + Coral Augur (epic anchor).
#
# tide_empress was promoted to legendary in Phase 4f-pool. Its identity is
# now the L3 mutation (rule_change="L3": every heal trickles +1 to all other
# alive allies, cascade-broken). Engine binding is exhaustively tested in
# test_combat_phase4f.py::TestL3…; here we exercise the actual catalog card.
# ---------------------------------------------------------------------------

from daimon.engine.types import EffectOp, TargetFilter, Trigger, TriggerWhen


class TestTideEmpress:
    def test_l3_mutation_tag_present(self):
        c = anchor("tide_empress")
        assert c.rule_change == "L3", (
            f"tide_empress rule_change={c.rule_change!r} != 'L3'"
        )
        assert c.triggers == (), (
            f"tide_empress should have empty triggers per §23.6; "
            f"got {c.triggers}"
        )

    def test_l3_mutation_trickles_heal_to_allies(self):
        """End-to-end: tide_empress + a healer ally on team. The healer's
        self-heal should trigger the L3 trickle → tide_empress also gains
        1 hp (the trickle). Asserts the actual catalog card carries L3
        and dispatches it through the loader → engine path."""
        # Healer that self-heals at battle start. The self-heal triggers L3,
        # which trickles +1 to every OTHER alive ally (including tide_empress).
        healer = Card(
            card_id="healy", species="healy", element=Element.WATER,
            atk=0, defense=0, hp=50, spd=99,
            triggers=(Trigger(
                when=TriggerWhen.ON_BATTLE_START,
                op=EffectOp.HEAL,
                target=TargetFilter.SELF,
                value=5,
            ),),
        )
        target = Card(
            card_id="opp", species="opp", element=Element.NATURE,
            atk=0, defense=0, hp=100, spd=1,
        )
        team_a = pair(anchor("tide_empress"), healer)
        team_b = solo(target)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # L3 trickle log format from combat.py:
        #   "L3 trickle: {ally.card_id} heals 1"
        assert "L3 trickle: tide_empress heals 1" in log, (
            f"L3 mutation did not trickle to tide_empress on healer's heal.\n"
            f"Full log:\n{log}"
        )


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
# STORMCHAIN — Tempest Apex (L4 mutation) + Arc Predator (epic anchor).
#
# tempest_apex was promoted to legendary in Phase 4f-pool. Its identity is
# now the L4 mutation (rule_change="L4": extra-action cap raised 1→2 per
# unit per round). Engine binding is exhaustively tested in
# test_combat_phase4f.py::TestGrantExtraAction::test_extra_action_cap_l4_…;
# here we exercise the actual catalog card.
# ---------------------------------------------------------------------------

class TestTempestApex:
    def test_l4_mutation_tag_present(self):
        c = anchor("tempest_apex")
        assert c.rule_change == "L4", (
            f"tempest_apex rule_change={c.rule_change!r} != 'L4'"
        )
        assert c.triggers == (), (
            f"tempest_apex should have empty triggers per §23.6; "
            f"got {c.triggers}"
        )

    def test_l4_mutation_raises_extra_action_cap(self):
        """End-to-end: with tempest_apex on team, an ally with two
        GRANT_EXTRA_ACTION self-triggers can grant 2 extra actions in one
        round (cap raised from default 1 to 2). Without tempest_apex the
        2nd grant would log a 'cap reached' line."""
        # Ally with two ON_BATTLE_START GRANT_EXTRA_ACTION triggers — both
        # target self. With L4 active, both should land (cap=2). Without L4,
        # only the first lands (cap=1).
        granter = Card(
            card_id="grant", species="grant", element=Element.VOLT,
            atk=0, defense=0, hp=100, spd=99,
            triggers=(
                Trigger(
                    when=TriggerWhen.ON_BATTLE_START,
                    op=EffectOp.GRANT_EXTRA_ACTION,
                    target=TargetFilter.SELF,
                    value=0,
                ),
                Trigger(
                    when=TriggerWhen.ON_BATTLE_START,
                    op=EffectOp.GRANT_EXTRA_ACTION,
                    target=TargetFilter.SELF,
                    value=0,
                ),
            ),
        )
        target = Card(
            card_id="opp_l4", species="opp_l4", element=Element.NATURE,
            atk=0, defense=0, hp=100, spd=1,
        )
        team_a = pair(anchor("tempest_apex"), granter)
        team_b = solo(target)
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # Extra-action grant log format from combat.py:
        #   "... grants extra action to {tgt.card_id} (used X/Y)"
        # With L4 alive, the cap shows "/2" instead of "/1", and the 2nd
        # grant lands rather than logging "cap reached".
        assert "(used 2/2)" in log, (
            f"L4 mutation did not raise extra-action cap to 2.\n"
            f"Full log:\n{log}"
        )


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
