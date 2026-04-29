"""Combat tests for Phase-2 vocab additions (V1 expansion, 2026-04-22).

Coverage:
  - LIFESTEAL: damage + ceil(value/2) heal-back to attacker
  - APPLY_BURN / APPLY_POISON: status applies, DOT ticks at round start
  - APPLY_STUN: skips next action, ticks on consumption (NOT at round start)
  - APPLY_SILENCE: suppresses ALL triggers on the unit, including ON_DEATH
  - APPLY_TAUNT: overrides basic attack target priority
  - ON_KILL: fires on attacker when its attack KOs the target
  - ON_TURN_END: fires on actor after its action
  - ON_LOW_HP: one-shot fire when self.hp ≤ 25% of card.hp
  - ON_OPENING_ATTACK: fires only on the unit's first attack of the match
  - Trigger.condition gating: true→fires, false→suppressed

All scenarios use Element.NATURE on both sides except where element-multiplier
is the subject under test, so 1.0× damage stays the deterministic default.
"""

from __future__ import annotations

import pytest

from daimon.engine import Loadout, TEAM_SIZE, resolve_match
from daimon.engine.types import (
    Card,
    EffectOp,
    Element,
    StatusCondition,
    TargetFilter,
    Trigger,
    TriggerWhen,
)

from tests.conftest import SEED_ZERO, make_filler


# ---------------------------------------------------------------------------
# Tiny constructor helpers — local copies of test_combat.py's `mk` / `lo`
# for self-containment (these tests don't import from test_combat.py).
# ---------------------------------------------------------------------------

def mk(card_id: str, atk: int = 5, defense: int = 5, hp: int = 20, spd: int = 5,
       species: str | None = None, element: Element = Element.NATURE,
       triggers: tuple[Trigger, ...] = ()) -> Card:
    return Card(card_id=card_id, species=species or card_id, element=element,
                atk=atk, defense=defense, hp=hp, spd=spd, triggers=triggers)


# Inert dummies: enormous HP + zero attack + zero speed. Used to pad teams to
# TEAM_SIZE without disrupting LOWEST_HP_ENEMY targeting. Any named test card
# with hp < INERT_DUMMY_HP will naturally be the lowest-HP enemy on its side.
INERT_DUMMY_HP = 9999


def _inert_dummy(idx: int) -> Card:
    return Card(
        card_id=f"_dummy_{idx}", species=f"_d{idx}", element=Element.NATURE,
        atk=0, defense=0, hp=INERT_DUMMY_HP, spd=0,
    )


def solo(card: Card) -> Loadout:
    """Pad: `card` at position 0, inert dummies at positions 1..5."""
    rest = tuple(_inert_dummy(i) for i in range(1, TEAM_SIZE))
    return Loadout(cards=(card,) + rest)


def pair(c1: Card, c2: Card) -> Loadout:
    """Pad: `c1` at 0, `c2` at 1, inert dummies at 2..5."""
    rest = tuple(_inert_dummy(i) for i in range(2, TEAM_SIZE))
    return Loadout(cards=(c1, c2) + rest)


def joined_log(result, round_idx: int = 0) -> str:
    return "\n".join(result.rounds[round_idx].actions)


def all_logs(result) -> str:
    return "\n".join(line for r in result.rounds for line in r.actions)


# ---------------------------------------------------------------------------
# LIFESTEAL
# ---------------------------------------------------------------------------

class TestLifesteal:
    def test_lifesteal_heals_attacker(self):
        """LIFESTEAL value=10 → target takes 10 (post-DEF), attacker heals 5."""
        vamp = mk(
            "vamp", atk=0, defense=10, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_ATTACK, EffectOp.LIFESTEAL,
                              TargetFilter.LOWEST_HP_ENEMY, value=10),),
        )
        # hp=50 is below INERT_DUMMY_HP, so blood_bag is the lowest-HP enemy.
        target = mk("blood_bag", atk=0, defense=0, hp=50, spd=1)
        result = resolve_match(solo(vamp), solo(target), SEED_ZERO)

        log = all_logs(result)
        assert "vamp hits blood_bag for 10" in log
        assert "vamp drains 5 hp from blood_bag" in log

    def test_lifesteal_odd_value_rounds_up(self):
        """ceil(7/2) = 4, not 3."""
        vamp = mk(
            "v7", atk=0, defense=10, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_ATTACK, EffectOp.LIFESTEAL,
                              TargetFilter.LOWEST_HP_ENEMY, value=7),),
        )
        target = mk("t", atk=0, defense=0, hp=50, spd=1)
        result = resolve_match(solo(vamp), solo(target), SEED_ZERO)
        log = all_logs(result)
        assert "v7 drains 4 hp from t" in log


# ---------------------------------------------------------------------------
# APPLY_BURN / APPLY_POISON — DOT ticks
# ---------------------------------------------------------------------------

class TestApplyBurn:
    def test_burn_application_logs(self):
        burner = mk(
            "fire", atk=0, defense=10, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_ATTACK, EffectOp.APPLY_BURN,
                              TargetFilter.LOWEST_HP_ENEMY, value=2),),
        )
        target = mk("t", atk=0, defense=0, hp=50, spd=1)
        result = resolve_match(solo(burner), solo(target), SEED_ZERO)
        log = all_logs(result)
        assert "fire applies BURN (2r) to t" in log

    def test_burn_ticks_3_dmg_per_round(self):
        burner = mk(
            "fire", atk=0, defense=10, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_BATTLE_START, EffectOp.APPLY_BURN,
                              TargetFilter.LOWEST_HP_ENEMY, value=3),),
        )
        target = mk("t", atk=0, defense=0, hp=50, spd=1)
        result = resolve_match(solo(burner), solo(target), SEED_ZERO)
        # Burn applied at battle-start (duration=3). Ticks at start of rounds 1, 2, 3.
        log = all_logs(result)
        burn_hits = [l for l in log.splitlines() if "burn hits t for 3" in l]
        assert len(burn_hits) == 3, f"expected 3 burn ticks, got {len(burn_hits)}"


class TestApplyPoison:
    def test_poison_ticks_2_dmg_per_round(self):
        """POISON is the lower-magnitude alternative to BURN."""
        poisoner = mk(
            "tox", atk=0, defense=10, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_BATTLE_START, EffectOp.APPLY_POISON,
                              TargetFilter.LOWEST_HP_ENEMY, value=3),),
        )
        target = mk("t", atk=0, defense=0, hp=50, spd=1)
        result = resolve_match(solo(poisoner), solo(target), SEED_ZERO)
        log = all_logs(result)
        poison_hits = [l for l in log.splitlines() if "poison hits t for 2" in l]
        assert len(poison_hits) == 3, f"expected 3 poison ticks, got {len(poison_hits)}"


# ---------------------------------------------------------------------------
# APPLY_STUN
# ---------------------------------------------------------------------------

class TestApplyStun:
    def test_stun_skips_target_action(self):
        """Stunned units skip their next action."""
        stunner = mk(
            "stunner", atk=0, defense=10, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_BATTLE_START, EffectOp.APPLY_STUN,
                              TargetFilter.LOWEST_HP_ENEMY, value=1),),
        )
        # hp=50 < INERT_DUMMY_HP so target is the lowest-HP enemy on side B.
        target = mk("t", atk=99, defense=0, hp=50, spd=50)
        result = resolve_match(solo(stunner), solo(target), SEED_ZERO)

        round1 = joined_log(result, 0)
        assert "t is stunned, skips action" in round1
        assert "t hits stunner" not in round1
        # Duration=1 → consumed by round 1 skip; round 2 should NOT skip again.
        if len(result.rounds) >= 2:
            assert "t is stunned" not in joined_log(result, 1)


# ---------------------------------------------------------------------------
# APPLY_SILENCE — gates ALL triggers on the unit
# ---------------------------------------------------------------------------

class TestApplySilence:
    def test_silence_blocks_on_attack(self):
        # Silenced unit's ON_ATTACK trigger must not fire.
        silencer = mk(
            "silencer", atk=0, defense=10, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_BATTLE_START, EffectOp.APPLY_SILENCE,
                              TargetFilter.LOWEST_HP_ENEMY, value=2),),
        )
        # screamer is the lowest-HP enemy (50 < INERT_DUMMY_HP).
        screamer = mk(
            "screamer", atk=1, defense=0, hp=50, spd=50,
            triggers=(Trigger(TriggerWhen.ON_ATTACK, EffectOp.HEAL,
                              TargetFilter.SELF, value=17),),
        )
        result = resolve_match(solo(silencer), solo(screamer), SEED_ZERO)
        log = all_logs(result)
        assert "silencer applies SILENCE (2r) to screamer" in log
        # In round 1, silence is active; ON_ATTACK heal-17 must not fire.
        assert "heals screamer for 17" not in joined_log(result, 0)


# ---------------------------------------------------------------------------
# APPLY_TAUNT — basic attack target override
# ---------------------------------------------------------------------------

class TestApplyTaunt:
    def test_taunt_redirects_basic_attack(self):
        """A taunting unit must be the basic-attack target even if it isn't
        the lowest-HP enemy."""
        taunter = mk(
            "tank", atk=0, defense=0, hp=100, spd=0,
            triggers=(Trigger(TriggerWhen.ON_BATTLE_START, EffectOp.APPLY_TAUNT,
                              TargetFilter.SELF, value=3),),
        )
        fragile = mk("fragile", atk=0, defense=0, hp=5, spd=0)
        striker = mk("striker", atk=10, defense=0, hp=50, spd=99)

        # Side A: tank + fragile + 4 inert dummies; side B: striker + 5 dummies.
        result = resolve_match(pair(taunter, fragile), solo(striker), SEED_ZERO)

        round1 = joined_log(result, 0)
        # Without taunt, fragile (hp=5) would be lowest HP. Taunt forces tank.
        assert "striker hits tank" in round1
        assert "striker hits fragile" not in round1
        assert "tank applies TAUNT" in round1


# ---------------------------------------------------------------------------
# ON_KILL — fires on attacker when its attack KOs the target
# ---------------------------------------------------------------------------

class TestOnKill:
    def test_on_kill_fires_after_ko(self):
        # Attacker buffs its own ATK by +5 ON_KILL. atk=10 vs hp=1 = KO.
        executioner = mk(
            "exec", atk=10, defense=0, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_KILL, EffectOp.BUFF_ATK,
                              TargetFilter.SELF, value=5),),
        )
        weakling = mk("weak", atk=0, defense=0, hp=1, spd=1)
        result = resolve_match(solo(executioner), solo(weakling), SEED_ZERO)
        log = all_logs(result)
        assert "exec buffs ATK of exec by +5" in log

    def test_on_kill_does_not_fire_on_non_ko(self):
        executioner = mk(
            "exec", atk=5, defense=0, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_KILL, EffectOp.BUFF_ATK,
                              TargetFilter.SELF, value=5),),
        )
        # Tanky target — attacker can't KO it; ON_KILL must not fire across the
        # whole match. Use solo() so dummies (hp=9999) don't get one-shot either.
        tank = mk("tank", atk=0, defense=10, hp=999, spd=1)
        result = resolve_match(solo(executioner), solo(tank), SEED_ZERO)
        log = all_logs(result)
        assert "exec buffs ATK" not in log


# ---------------------------------------------------------------------------
# ON_TURN_END — fires on actor after its action
# ---------------------------------------------------------------------------

class TestOnTurnEnd:
    def test_on_turn_end_fires_after_action(self):
        actor = mk(
            "selfheal", atk=5, defense=0, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_TURN_END, EffectOp.HEAL,
                              TargetFilter.SELF, value=11),),
        )
        dummy = mk("d", atk=0, defense=0, hp=999, spd=1)
        result = resolve_match(solo(actor), solo(dummy), SEED_ZERO)
        log = all_logs(result)
        heals = [l for l in log.splitlines() if "selfheal heals selfheal for 11" in l]
        # Should fire at least 5 times across 5 rounds (round cap)
        assert len(heals) >= 5, f"only {len(heals)} ON_TURN_END heals: {heals}"


# ---------------------------------------------------------------------------
# ON_LOW_HP — one-shot at ≤25% of card.hp
# ---------------------------------------------------------------------------

class TestOnLowHp:
    def test_on_low_hp_fires_once(self):
        """Drop into ≤25% with one big hit → ON_LOW_HP fires once.
        Subsequent hits must NOT re-fire it."""
        guardian = mk(
            "guard", atk=0, defense=0, hp=100, spd=1,
            triggers=(Trigger(TriggerWhen.ON_LOW_HP, EffectOp.HEAL,
                              TargetFilter.SELF, value=23),),
        )
        # Threshold = 100 // 4 = 25. atk=80 vs def=0 → hp 100→20 (≤25).
        bruiser = mk("br", atk=80, defense=0, hp=200, spd=99)
        result = resolve_match(solo(guardian), solo(bruiser), SEED_ZERO)
        log = all_logs(result)
        heals = [l for l in log.splitlines() if "guard heals guard for 23" in l]
        assert len(heals) == 1, f"expected 1 ON_LOW_HP fire, got {len(heals)}"

    def test_on_low_hp_does_not_fire_above_threshold(self):
        # Sized so the 100-round stalemate guard caps total damage at 500,
        # leaving guardian at 500 hp — well above its 250 threshold (hp/4).
        guardian = mk(
            "guard", atk=0, defense=0, hp=1000, spd=1,
            triggers=(Trigger(TriggerWhen.ON_LOW_HP, EffectOp.HEAL,
                              TargetFilter.SELF, value=23),),
        )
        nibbler = mk("nib", atk=5, defense=0, hp=200, spd=99)
        result = resolve_match(solo(guardian), solo(nibbler), SEED_ZERO)
        log = all_logs(result)
        assert "guard heals guard for 23" not in log


# ---------------------------------------------------------------------------
# ON_OPENING_ATTACK — fires only on the unit's first attack
# ---------------------------------------------------------------------------

class TestOnOpeningAttack:
    def test_opens_with_buff_only_once(self):
        opener = mk(
            "open", atk=5, defense=0, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_OPENING_ATTACK, EffectOp.BUFF_ATK,
                              TargetFilter.SELF, value=3),),
        )
        dummy = mk("d", atk=0, defense=0, hp=999, spd=1)
        result = resolve_match(solo(opener), solo(dummy), SEED_ZERO)
        log = all_logs(result)
        buffs = [l for l in log.splitlines() if "open buffs ATK of open by +3" in l]
        assert len(buffs) == 1


# ---------------------------------------------------------------------------
# Trigger.condition gating
# ---------------------------------------------------------------------------

class TestConditionGating:
    def test_condition_true_fires(self):
        actor = mk(
            "true", atk=5, defense=0, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_TURN_END, EffectOp.HEAL,
                              TargetFilter.SELF, value=7,
                              condition="self.hp > 0"),),
        )
        dummy = mk("d", atk=0, defense=0, hp=999, spd=1)
        result = resolve_match(solo(actor), solo(dummy), SEED_ZERO)
        log = all_logs(result)
        assert "true heals true for 7" in log

    def test_condition_false_suppresses(self):
        actor = mk(
            "false", atk=5, defense=0, hp=30, spd=99,
            triggers=(Trigger(TriggerWhen.ON_TURN_END, EffectOp.HEAL,
                              TargetFilter.SELF, value=7,
                              condition="self.hp < 0"),),
        )
        dummy = mk("d", atk=0, defense=0, hp=999, spd=1)
        result = resolve_match(solo(actor), solo(dummy), SEED_ZERO)
        log = all_logs(result)
        assert "false heals false for 7" not in log

    def test_condition_dynamic_low_hp(self):
        """Condition gates on a dynamic context value (self.hp threshold)."""
        survivor = mk(
            "surv", atk=0, defense=0, hp=100, spd=1,
            triggers=(Trigger(TriggerWhen.ON_TURN_END, EffectOp.HEAL,
                              TargetFilter.SELF, value=8,
                              condition="self.hp < self.hp_max // 2"),),
        )
        # 30 dmg/round vs hp=100. R1: hp=70 (no heal). R2: hp=40 (<50, heal fires).
        striker = mk("hit", atk=30, defense=0, hp=999, spd=99)
        result = resolve_match(solo(survivor), solo(striker), SEED_ZERO)
        log = all_logs(result)
        assert "surv heals surv for 8" in log


# ---------------------------------------------------------------------------
# Loader integration — `condition` field round-trips through JSON.
# ---------------------------------------------------------------------------

class TestLoaderConditionField:
    def test_loader_accepts_valid_condition(self):
        from daimon.cards.loader import load_card_dict
        card = load_card_dict({
            "card_id": "c", "species": "s", "element": "FIRE",
            "atk": 1, "def": 1, "hp": 10, "spd": 1,
            "triggers": [{
                "when": "ON_TURN_END", "op": "HEAL",
                "target": "SELF", "value": 1,
                "condition": "self.hp > 0",
            }],
        })
        assert card.triggers[0].condition == "self.hp > 0"

    def test_loader_rejects_invalid_condition(self):
        from daimon.cards.loader import load_card_dict
        with pytest.raises(ValueError, match="condition invalid"):
            load_card_dict({
                "card_id": "c", "species": "s", "element": "FIRE",
                "atk": 1, "def": 1, "hp": 10, "spd": 1,
                "triggers": [{
                    "when": "ON_TURN_END", "op": "HEAL",
                    "target": "SELF", "value": 1,
                    "condition": "__import__('os')",
                }],
            })

    def test_loader_accepts_new_op_names(self):
        from daimon.cards.loader import load_card_dict
        card = load_card_dict({
            "card_id": "c", "species": "s", "element": "FIRE",
            "atk": 1, "def": 1, "hp": 10, "spd": 1,
            "triggers": [
                {"when": "ON_ATTACK", "op": "APPLY_BURN",
                 "target": "LOWEST_HP_ENEMY", "value": 2},
                {"when": "ON_KILL", "op": "LIFESTEAL",
                 "target": "LOWEST_HP_ENEMY", "value": 5},
                {"when": "ON_LOW_HP", "op": "APPLY_TAUNT",
                 "target": "SELF", "value": 1},
            ],
        })
        assert card.triggers[0].op == EffectOp.APPLY_BURN
        assert card.triggers[1].op == EffectOp.LIFESTEAL
        assert card.triggers[1].when == TriggerWhen.ON_KILL
        assert card.triggers[2].when == TriggerWhen.ON_LOW_HP
