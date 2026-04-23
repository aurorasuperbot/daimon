"""Phase 4e-counters behavioural tests (V1, 2026-04-22).

Six rares were re-keyed in `scripts/author_phase4e_counters.py` as designated
counters for the six strategic archetypes:

    Card                Element  Counters       Mechanic
    forest_warden       NATURE   INFERNO        ON_LOW_HP HEAL big self
    maelstrom_serpent   WATER    BULWARK        ON_OPENING_ATTACK team-DEF-shred
    mindroot            VOID     TIDAL          ON_BATTLE_START AoE POISON
    bulwarthog          NATURE   STORMCHAIN     ON_ROUND_START team-DEF stack
    abyss_warden        VOID     REVENANT       ON_BATTLE_START AoE SILENCE
    stormhare           VOLT     FLUX           ON_OPENING_ATTACK target lowest-HP

Each test mirrors the Phase-3 anchor pattern (see `test_phase3_anchors.py`):
load the counter via the real catalog loader, build a deterministic loadout
that exercises the counter's signature mechanic, run `resolve_match` against
an opponent designed to make the counter visible, and assert the log carries
the expected trace.

These tests prove the *cards as authored on disk* fire the counter mechanics
the design doc claims. They will catch:

  - Re-keying drift (someone edits the card and forgets the counter intent).
  - Engine drift (someone changes the log format or the trigger ordering).
  - Status-suppression contract drift (e.g. SILENCE ceases to gate ON_DEATH).

Determinism: SEED_ZERO + identical loadouts → identical MatchResult.
"""

from __future__ import annotations

from pathlib import Path

from daimon.cards.loader import load_card
from daimon.engine import Loadout, TEAM_SIZE, resolve_match
from daimon.engine.types import Card, Element

from tests.conftest import SEED_ZERO


# ---------------------------------------------------------------------------
# Catalog plumbing — same shape as test_phase3_anchors.py.
# ---------------------------------------------------------------------------

PACK_DIR = Path(__file__).resolve().parent.parent / "daimon" / "catalog" / "v1_alpha"


def counter(card_id: str) -> Card:
    """Load a counter card by id from the v1_alpha pack."""
    return load_card(PACK_DIR / f"{card_id}.json")


# ---------------------------------------------------------------------------
# Loadout helpers — inert dummies that don't fight back, don't act, don't die.
# ---------------------------------------------------------------------------

INERT_DUMMY_HP = 9999


def _inert_dummy(idx: int, element: Element = Element.NATURE) -> Card:
    return Card(
        card_id=f"_dummy_{idx}", species=f"_d{idx}", element=element,
        atk=0, defense=0, hp=INERT_DUMMY_HP, spd=0,
    )


def solo(card: Card, *, elements: list[Element] | None = None) -> Loadout:
    """Counter at slot 0; dummies fill the rest. `elements` controls the
    distinct_elements profile of the rest of the team — for FLUX scenarios."""
    if elements is None:
        elements = [Element.NATURE] * (TEAM_SIZE - 1)
    assert len(elements) == TEAM_SIZE - 1
    rest = tuple(_inert_dummy(i + 1, e) for i, e in enumerate(elements))
    return Loadout(cards=(card,) + rest)


def all_logs(result) -> str:
    return "\n".join(line for r in result.rounds for line in r.actions)


# ===========================================================================
# Anti-INFERNO — Forest Warden
# ===========================================================================

class TestForestWardenAntiInferno:
    """Forest Warden's counter to INFERNO is a two-part play:
       (a) ON_BATTLE_START BUFF_DEF ALL_ALLIES 3   — softens opening burst.
       (b) ON_LOW_HP HEAL SELF 5                   — extends sustain past
                                                     INFERNO's burn-tick clock.
    """

    def test_battle_start_buffs_team_def(self):
        attacker = Card(
            card_id="enemy", species="enemy", element=Element.FIRE,
            atk=4, defense=2, hp=30, spd=2,
        )
        result = resolve_match(solo(counter("forest_warden")),
                               solo(attacker), SEED_ZERO)
        round1 = "\n".join(result.rounds[0].actions)
        # Trigger fires on every ally — _dummy_1 is one of the targets.
        assert "forest_warden buffs DEF of _dummy_1 by +3" in round1, round1

    def test_low_hp_self_heal_fires(self):
        # Heavy hitter calibrated to land Forest Warden inside the ON_LOW_HP
        # window on its first connect (instead of overshooting straight to 0).
        #
        # Math (this is the fixture's whole point — break it at your peril):
        #   - Forest Warden hp=24 → ON_LOW_HP threshold = 24 // 4 = 6 hp
        #     (engine: combat.py::_sweep_low_hp_triggers, condition
        #      `unit.hp <= unit.card.hp // _LOW_HP_DENOM`).
        #   - Forest Warden's own ON_BATTLE_START BUFF_DEF +3 lands first,
        #     bumping fw.def from 9 to 12 BEFORE bruiser's first attack.
        #   - Element matchup: FIRE vs NATURE = ×1.5 (FIRE strong on NATURE).
        #   - Damage formula (post-DEF, then ×element_mult, ceil-rounded):
        #       (bruiser.atk - fw.def_eff) × 1.5
        #     Solving for damage = 18 → bruiser.atk = (18/1.5) + 12 = 24.
        #   - First hit: fw.hp 24 → 6 (lands AT threshold, fires ON_LOW_HP),
        #     HEAL SELF 5 → fw.hp 6 → 11.
        #   - Second hit: fw.hp 11 → 0 (dies). One ON_LOW_HP fire, observed.
        #
        # Bruiser's huge HP (80) keeps it alive across the 5-round cap so
        # forest_warden's ON_LOW_HP window is the only thing under test —
        # the match doesn't end on bruiser-death side effects.
        bruiser = Card(
            card_id="bruiser", species="bruiser", element=Element.FIRE,
            atk=24, defense=5, hp=80, spd=10,
        )
        result = resolve_match(solo(counter("forest_warden")),
                               solo(bruiser), SEED_ZERO)
        log = all_logs(result)
        assert "heals forest_warden for 5" in log, log


# ===========================================================================
# Anti-BULWARK — Maelstrom Serpent
# ===========================================================================

class TestMaelstromSerpentAntiBulwark:
    """Armor-shred ALL_ENEMIES on the opening attack so a stacked BULWARK
    wall collapses on contact, then hammer the lowest-HP target every
    round — the squishy back-line BULWARK is trying to protect."""

    def test_opening_attack_shreds_team_defense(self):
        wall = Card(
            card_id="wall", species="wall", element=Element.NATURE,
            atk=2, defense=15, hp=80, spd=2,
        )
        result = resolve_match(solo(counter("maelstrom_serpent")),
                               solo(wall), SEED_ZERO)
        round1 = "\n".join(result.rounds[0].actions)
        # ON_OPENING_ATTACK DEBUFF_DEF ALL_ENEMIES 4 — the named wall AND
        # the opponent's dummies should all see a -4 DEF stamp.
        assert "maelstrom_serpent debuffs DEF of wall by -4" in round1, round1
        assert "maelstrom_serpent debuffs DEF of _dummy_1 by -4" in round1, round1


# ===========================================================================
# Anti-TIDAL — Mindroot
# ===========================================================================

class TestMindrootAntiTidal:
    """Battle-start AoE POISON puts a 3-round DOT on every TIDAL ally,
    bypassing the heal cycle: heal restores HP, poison nibbles it back."""

    def test_battle_start_poisons_entire_enemy_team(self):
        healer = Card(
            card_id="healer", species="healer", element=Element.WATER,
            atk=2, defense=4, hp=40, spd=4,
        )
        result = resolve_match(solo(counter("mindroot")),
                               solo(healer), SEED_ZERO)
        round1 = "\n".join(result.rounds[0].actions)
        assert "mindroot applies POISON (3r) to healer" in round1, round1
        # AoE — opponent's slot-1 dummy gets the same DOT.
        assert "mindroot applies POISON (3r) to _dummy_1" in round1, round1


# ===========================================================================
# Anti-STORMCHAIN — Bulwarthog
# ===========================================================================

class TestBulwarthogAntiStormchain:
    """ON_ROUND_START BUFF_DEF ALL_ALLIES 1 (small but stacks across
    rounds). Round-start triggers fire BEFORE actions, so STUN — which
    only cancels the unit's attack action — does NOT prevent them.
    Across a 5-round match this stacks to +5 team DEF."""

    def test_round_start_buffs_stack_each_round(self):
        # Inert opponent so the match runs the full 5-round cap.
        sandbag = Card(
            card_id="sandbag", species="sandbag", element=Element.VOLT,
            atk=0, defense=0, hp=INERT_DUMMY_HP, spd=0,
        )
        result = resolve_match(solo(counter("bulwarthog")),
                               solo(sandbag), SEED_ZERO)
        # Bulwarthog should be re-applying BUFF_DEF on every single round.
        # Across the 5-round cap the trigger fires 5 times per ally.
        per_round_hits_on_dummy_1 = sum(
            1 for r in result.rounds
            for line in r.actions
            if "bulwarthog buffs DEF of _dummy_1 by +1" in line
        )
        assert per_round_hits_on_dummy_1 >= 3, (
            f"Expected >=3 ON_ROUND_START stacks on _dummy_1 across the "
            f"5-round cap, got {per_round_hits_on_dummy_1}.\n"
            f"Logs:\n{all_logs(result)}"
        )


# ===========================================================================
# Anti-REVENANT — Abyss Warden
# ===========================================================================

class TestAbyssWardenAntiRevenant:
    """Abyss Warden's anti-REVENANT keystone: ON_BATTLE_START APPLY_SILENCE
    ALL_ENEMIES 3r. SILENCE is the *only* status that suppresses ON_DEATH
    triggers (combat.py::_fire_triggers_for_unit). Silencing the enemy
    team at battle-start neuters REVENANT's value engine for the silence
    window."""

    def test_battle_start_silences_entire_enemy_team(self):
        revenant = Card(
            card_id="revenant", species="revenant", element=Element.VOID,
            atk=3, defense=3, hp=30, spd=3,
        )
        result = resolve_match(solo(counter("abyss_warden")),
                               solo(revenant), SEED_ZERO)
        round1 = "\n".join(result.rounds[0].actions)
        assert "abyss_warden applies SILENCE (3r) to revenant" in round1, round1
        assert "abyss_warden applies SILENCE (3r) to _dummy_1" in round1, round1

    def test_silence_actually_suppresses_a_revenant_death_trigger(self):
        """End-to-end proof: a card with an ON_DEATH trigger that's silenced
        at battle-start does NOT fire its death rattle when killed.

        We construct a synthetic 'mock revenant' whose ON_DEATH BUFF_ATK
        SELF would fire visibly without silence (an obvious log line). With
        Abyss Warden in play, the silence preempts the trigger."""
        from daimon.engine.types import Trigger, TriggerWhen, EffectOp, TargetFilter

        # Mock revenant: easy to kill, has a recognizable death rattle.
        mock_revenant = Card(
            card_id="mock_rev", species="mock_rev", element=Element.VOID,
            atk=2, defense=2, hp=8, spd=2,
            triggers=(
                Trigger(when=TriggerWhen.ON_DEATH, op=EffectOp.BUFF_ATK,
                        target=TargetFilter.ALL_ALLIES, value=4),
            ),
        )
        # Crusher to make the kill happen on round 1.
        crusher = Card(
            card_id="crusher", species="crusher", element=Element.NATURE,
            atk=99, defense=99, hp=200, spd=99,
        )
        # Player team: Abyss Warden + crusher (so the kill happens fast).
        # Enemy team: just the mock revenant.
        team_a = Loadout(cards=(counter("abyss_warden"), crusher) +
                                tuple(_inert_dummy(i) for i in range(2, TEAM_SIZE)))
        team_b = Loadout(cards=(mock_revenant,) +
                                tuple(_inert_dummy(i + 1) for i in range(TEAM_SIZE - 1)))
        result = resolve_match(team_a, team_b, SEED_ZERO)
        log = all_logs(result)
        # The mock revenant's death rattle (BUFF_ATK ALL_ALLIES) should
        # have been silenced — its log signature must NOT appear.
        assert "mock_rev buffs ATK" not in log, (
            f"ON_DEATH trigger fired despite SILENCE — counter mechanic broken.\n"
            f"Log:\n{log}"
        )


# ===========================================================================
# Anti-FLUX — Stormhare
# ===========================================================================

class TestStormhareAntiFlux:
    """Race-burst that kills FLUX squishies before their distinct_elements
    gates assemble. Two parts:
       (a) ON_OPENING_ATTACK DAMAGE LOWEST_HP_ENEMY 4 — surgical first-hit.
       (b) ON_BATTLE_START BUFF_SPD ALL_ALLIES 1 — whole-team tempo lead.
    """

    def test_battle_start_buffs_team_speed(self):
        target = Card(
            card_id="target", species="target", element=Element.NATURE,
            atk=4, defense=2, hp=30, spd=2,
        )
        result = resolve_match(solo(counter("stormhare")),
                               solo(target), SEED_ZERO)
        round1 = "\n".join(result.rounds[0].actions)
        assert "stormhare buffs SPD of _dummy_1 by +1" in round1, round1

    def test_opening_attack_targets_lowest_hp_enemy(self):
        # FLUX-shaped opponent: low-HP catalyst at slot 0 vs sturdier filler.
        catalyst = Card(
            card_id="catalyst", species="catalyst", element=Element.FIRE,
            atk=4, defense=2, hp=8, spd=2,    # the low-HP target
        )
        result = resolve_match(solo(counter("stormhare")),
                               solo(catalyst), SEED_ZERO)
        round1 = "\n".join(result.rounds[0].actions)
        # ON_OPENING_ATTACK DAMAGE LOWEST_HP_ENEMY 4 — catalyst (HP=8) is
        # the lowest-HP enemy (dummies are HP=9999), so catalyst is hit.
        assert "stormhare hits catalyst" in round1, round1
