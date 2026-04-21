"""Deterministic combat resolver.

Algorithm (V1):
  1. Build UnitState lists for both sides from loadouts.
  2. Fire ON_BATTLE_START triggers (side A first, by slot order, then side B).
  3. For each round (max 5):
       a. Fire ON_ROUND_START triggers.
       b. Build action queue: every alive unit acts, ordered by effective_spd
          (higher spd first; ties broken by side then slot — fully deterministic).
       c. For each acting unit (in order):
            - If dead, skip.
            - Pick target = lowest-HP enemy alive (ties broken by slot index).
            - If no target, side wins early.
            - Fire ON_ATTACK triggers on attacker.
            - Compute damage = max(0, atk - target.def). Apply shield first.
            - Fire ON_TAKE_DAMAGE triggers on target.
            - If target dies, fire ON_DEATH and ON_ALLY_DEATH triggers.
       d. Record RoundLog.
       e. End early if either side fully wiped.
  4. Determine winner by surviving HP totals.

The engine reads ZERO strings from cards. Only Card.atk/def/hp/spd/triggers.
Even card_id is opaque to combat — used only for trace logging.
"""

from __future__ import annotations

from typing import List, Optional

from nullpoint.engine.loadout import Loadout
from nullpoint.engine.rng import SeededRng
from nullpoint.engine.types import (
    EffectOp,
    MatchResult,
    RoundLog,
    Slot,
    TargetFilter,
    Trigger,
    TriggerWhen,
    UnitState,
)

ROUND_CAP = 5


def _build_units(loadout: Loadout, side: int) -> List[UnitState]:
    return [
        UnitState(card=c, slot=Slot(c.slot), side=side, hp=c.hp)
        for c in loadout.cards
    ]


def _alive(units: List[UnitState]) -> List[UnitState]:
    return [u for u in units if u.alive]


def _pick_targets(
    trigger: Trigger,
    actor: UnitState,
    allies: List[UnitState],
    enemies: List[UnitState],
    rng: SeededRng,
) -> List[UnitState]:
    alive_allies = _alive(allies)
    alive_enemies = _alive(enemies)
    if trigger.target == TargetFilter.SELF:
        return [actor] if actor.alive else []
    if trigger.target == TargetFilter.ALL_ALLIES:
        return alive_allies
    if trigger.target == TargetFilter.ALL_ENEMIES:
        return alive_enemies
    if trigger.target == TargetFilter.LOWEST_HP_ENEMY:
        if not alive_enemies:
            return []
        return [min(alive_enemies, key=lambda u: (u.hp, int(u.slot)))]
    if trigger.target == TargetFilter.HIGHEST_HP_ENEMY:
        if not alive_enemies:
            return []
        return [max(alive_enemies, key=lambda u: (u.hp, -int(u.slot)))]
    if trigger.target == TargetFilter.RANDOM_ENEMY:
        return [rng.choice(alive_enemies)] if alive_enemies else []
    if trigger.target == TargetFilter.RANDOM_ALLY:
        return [rng.choice(alive_allies)] if alive_allies else []
    return []


def _apply_effect(
    op: EffectOp,
    value: int,
    target: UnitState,
    log: List[str],
    actor_id: str,
) -> None:
    if not target.alive and op != EffectOp.HEAL:
        return
    if op == EffectOp.BUFF_ATK:
        target.atk_mod += value
        log.append(f"{actor_id} buffs ATK of {target.card.card_id} by +{value}")
    elif op == EffectOp.DEBUFF_ATK:
        target.atk_mod -= value
        log.append(f"{actor_id} debuffs ATK of {target.card.card_id} by -{value}")
    elif op == EffectOp.BUFF_DEF:
        target.def_mod += value
        log.append(f"{actor_id} buffs DEF of {target.card.card_id} by +{value}")
    elif op == EffectOp.DEBUFF_DEF:
        target.def_mod -= value
        log.append(f"{actor_id} debuffs DEF of {target.card.card_id} by -{value}")
    elif op == EffectOp.HEAL:
        if target.alive:
            target.hp += value
            log.append(f"{actor_id} heals {target.card.card_id} for {value}")
    elif op == EffectOp.DAMAGE:
        _take_damage(target, value, log, source=actor_id)
    elif op == EffectOp.ADD_SHIELD:
        target.shield += value
        log.append(f"{actor_id} shields {target.card.card_id} for {value}")
    elif op == EffectOp.BUFF_SPD:
        target.spd_mod += value
        log.append(f"{actor_id} buffs SPD of {target.card.card_id} by +{value}")


def _take_damage(target: UnitState, amount: int, log: List[str], source: str) -> None:
    if amount <= 0 or not target.alive:
        return
    absorbed = min(target.shield, amount)
    target.shield -= absorbed
    remaining = amount - absorbed
    target.hp -= remaining
    log.append(
        f"{source} hits {target.card.card_id} for {amount} "
        f"(shield absorbed {absorbed}, hp now {max(0, target.hp)})"
    )
    if target.hp <= 0:
        target.alive = False
        target.hp = 0


def _fire_triggers_for_unit(
    when: TriggerWhen,
    unit: UnitState,
    allies: List[UnitState],
    enemies: List[UnitState],
    rng: SeededRng,
    log: List[str],
) -> None:
    if not unit.alive and when != TriggerWhen.ON_DEATH:
        return
    for trig in unit.card.triggers:
        if trig.when != when:
            continue
        for tgt in _pick_targets(trig, unit, allies, enemies, rng):
            _apply_effect(trig.op, trig.value, tgt, log, unit.card.card_id)


def _fire_triggers_all_units(
    when: TriggerWhen,
    side_a: List[UnitState],
    side_b: List[UnitState],
    rng: SeededRng,
    log: List[str],
) -> None:
    """Deterministic order: side A by slot, then side B by slot."""
    for u in sorted(side_a, key=lambda x: int(x.slot)):
        _fire_triggers_for_unit(when, u, side_a, side_b, rng, log)
    for u in sorted(side_b, key=lambda x: int(x.slot)):
        _fire_triggers_for_unit(when, u, side_b, side_a, rng, log)


def _ally_death_triggers(
    dying: UnitState,
    side: List[UnitState],
    enemies: List[UnitState],
    rng: SeededRng,
    log: List[str],
) -> None:
    for u in sorted(side, key=lambda x: int(x.slot)):
        if u is dying or not u.alive:
            continue
        _fire_triggers_for_unit(TriggerWhen.ON_ALLY_DEATH, u, side, enemies, rng, log)


def _resolve_action(
    actor: UnitState,
    allies: List[UnitState],
    enemies: List[UnitState],
    rng: SeededRng,
    log: List[str],
) -> None:
    if not actor.alive:
        return
    alive_enemies = _alive(enemies)
    if not alive_enemies:
        return

    # ON_ATTACK triggers fire BEFORE the actual attack
    _fire_triggers_for_unit(TriggerWhen.ON_ATTACK, actor, allies, enemies, rng, log)

    # Re-resolve target after triggers (target may have died or been added to)
    alive_enemies = _alive(enemies)
    if not alive_enemies:
        return
    target = min(alive_enemies, key=lambda u: (u.hp, int(u.slot)))

    raw_dmg = max(0, actor.effective_atk - target.effective_def)
    pre_alive = target.alive
    _take_damage(target, raw_dmg, log, source=actor.card.card_id)

    # ON_TAKE_DAMAGE on target
    if pre_alive and raw_dmg > 0:
        _fire_triggers_for_unit(
            TriggerWhen.ON_TAKE_DAMAGE, target, enemies, allies, rng, log
        )

    # Death triggers
    if pre_alive and not target.alive:
        _fire_triggers_for_unit(
            TriggerWhen.ON_DEATH, target, enemies, allies, rng, log
        )
        _ally_death_triggers(target, enemies, allies, rng, log)


def _hp_total(units: List[UnitState]) -> int:
    return sum(max(0, u.hp) for u in units if u.alive)


def resolve_match(
    loadout_a: Loadout,
    loadout_b: Loadout,
    seed: bytes,
) -> MatchResult:
    """Deterministically resolve a match.

    Same (loadout_a, loadout_b, seed) ALWAYS produces the same MatchResult.
    """
    if not isinstance(seed, bytes) or len(seed) != 32:
        raise ValueError("seed must be exactly 32 bytes")

    rng = SeededRng(seed)
    side_a = _build_units(loadout_a, side=0)
    side_b = _build_units(loadout_b, side=1)

    rounds: List[RoundLog] = []

    # Battle start triggers (one-shot)
    start_log: List[str] = []
    _fire_triggers_all_units(
        TriggerWhen.ON_BATTLE_START, side_a, side_b, rng, start_log
    )

    winner: Optional[int] = None
    reason = "round_cap"

    for r in range(1, ROUND_CAP + 1):
        round_log = RoundLog(round_number=r)
        if r == 1:
            round_log.actions.extend(start_log)

        # Round start triggers
        _fire_triggers_all_units(
            TriggerWhen.ON_ROUND_START, side_a, side_b, rng, round_log.actions
        )

        # Build action queue: all alive units, sorted by spd desc, then side, then slot
        actors = [u for u in side_a + side_b if u.alive]
        actors.sort(key=lambda u: (-u.effective_spd, u.side, int(u.slot)))

        for actor in actors:
            if not actor.alive:
                continue
            allies = side_a if actor.side == 0 else side_b
            enemies = side_b if actor.side == 0 else side_a
            _resolve_action(actor, allies, enemies, rng, round_log.actions)

        round_log.side_a_hp_total = _hp_total(side_a)
        round_log.side_b_hp_total = _hp_total(side_b)
        rounds.append(round_log)

        a_alive = bool(_alive(side_a))
        b_alive = bool(_alive(side_b))
        if not a_alive and not b_alive:
            winner = None
            reason = "double_wipe"
            break
        if not a_alive:
            winner = 1
            reason = "wipe"
            break
        if not b_alive:
            winner = 0
            reason = "wipe"
            break

    final_a = _hp_total(side_a)
    final_b = _hp_total(side_b)

    if winner is None and reason == "round_cap":
        if final_a > final_b:
            winner = 0
        elif final_b > final_a:
            winner = 1
        else:
            winner = None
            reason = "draw"

    return MatchResult(
        seed=seed,
        rounds=rounds,
        winner=winner,
        side_a_final_hp=final_a,
        side_b_final_hp=final_b,
        reason=reason,
    )
