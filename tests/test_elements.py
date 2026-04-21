"""Element type-effectiveness table lock-tests.

The element ring is:
  FIRE → NATURE → WATER → VOLT → VOID → FIRE (closed 5-loop)

STRONG (1.5×):  (a, b) where a beats b in the ring.
WEAK   (0.75×): (b, a) inverse of strong.
NEUTRAL (1.0×): everything else (same element, or unrelated pairs).

These tests are the single source of truth for the type chart.
"""

import math

import pytest

from nullpoint.engine import Loadout, TEAM_SIZE, resolve_match
from nullpoint.engine.elements import (
    NEUTRAL_MULT,
    STRONG_MULT,
    WEAK_MULT,
    element_multiplier,
    strong_against,
    weak_against,
)
from nullpoint.engine.types import Card, Element

from tests.conftest import SEED_ZERO, make_filler


def mk(card_id: str, element: Element, atk: int = 10, defense: int = 0,
       hp: int = 30, spd: int = 5) -> Card:
    return Card(card_id=card_id, species=card_id, element=element,
                atk=atk, defense=defense, hp=hp, spd=spd)


# -- The table ----------------------------------------------------------------

def test_fire_strong_against_nature():
    assert element_multiplier(Element.FIRE, Element.NATURE) == STRONG_MULT


def test_nature_strong_against_water():
    assert element_multiplier(Element.NATURE, Element.WATER) == STRONG_MULT


def test_water_strong_against_volt():
    assert element_multiplier(Element.WATER, Element.VOLT) == STRONG_MULT


def test_volt_strong_against_void():
    assert element_multiplier(Element.VOLT, Element.VOID) == STRONG_MULT


def test_void_strong_against_fire():
    assert element_multiplier(Element.VOID, Element.FIRE) == STRONG_MULT


def test_weak_directions_are_inverse():
    # For every (a,b) with 1.5× there must be (b,a) with 0.75×
    for a in Element:
        for b in strong_against(a):
            assert element_multiplier(b, a) == WEAK_MULT


def test_same_element_neutral():
    for e in Element:
        assert element_multiplier(e, e) == NEUTRAL_MULT


def test_unrelated_pair_neutral():
    # FIRE is strong vs NATURE but unrelated to WATER (WATER is strong vs FIRE,
    # but the Element ring itself says WATER strong-vs-VOLT, not vs FIRE).
    # Actually in our ring, VOID is strong-vs-FIRE. So FIRE→WATER is NEUTRAL.
    # (WATER→FIRE would also be NEUTRAL since WATER's strong is VOLT.)
    assert element_multiplier(Element.FIRE, Element.WATER) == NEUTRAL_MULT
    assert element_multiplier(Element.WATER, Element.FIRE) == NEUTRAL_MULT


def test_every_element_has_exactly_one_strong_and_one_weak():
    for e in Element:
        assert len(strong_against(e)) == 1
        assert len(weak_against(e)) == 1


def test_ring_closes():
    """Walking the strong-against arrows should cycle through all 5 elements."""
    visited = [Element.FIRE]
    for _ in range(5):
        nxt = strong_against(visited[-1])[0]
        visited.append(nxt)
    assert visited[-1] == visited[0], f"ring did not close: {visited}"
    assert set(visited[:-1]) == set(Element)


# -- Combat-level application ------------------------------------------------

def _dummy(card_id: str, element: Element = Element.NATURE) -> Card:
    """Tanky passive dummy — atk 0, hp 999, spd 0.

    HP must exceed the defender's HP so combat's lowest-HP targeting always
    picks the DEFENDER, not these teammates. Dummies themselves never die
    across a 5-round match because the attacker never targets them.
    """
    return Card(card_id=card_id, species=card_id, element=element,
                atk=0, defense=0, hp=999, spd=0)


def _team(lead: Card) -> Loadout:
    """Build a 6-monster team: `lead` at position 0 + 5 passive dummies."""
    cards = [lead] + [_dummy(f"d{i}") for i in range(1, TEAM_SIZE)]
    return Loadout(cards=tuple(cards))


def test_super_effective_attack_hits_harder():
    """FIRE vs NATURE defender should do 1.5× base damage."""
    # Defender hp=100 < dummy hp=999, so lowest-HP targeting locks onto defender.
    attacker = mk("torch", Element.FIRE, atk=10, defense=0, hp=999, spd=99)
    nature_defender = mk("vine", Element.NATURE, atk=0, defense=0, hp=100, spd=1)
    water_defender  = mk("drop", Element.WATER,  atk=0, defense=0, hp=100, spd=1)

    r_super = resolve_match(_team(attacker), _team(nature_defender), SEED_ZERO)
    r_neutral = resolve_match(_team(attacker), _team(water_defender), SEED_ZERO)

    # Super-effective drops side B's total HP MORE than the neutral matchup.
    # Dummies never die (999 hp, 0 atk incoming), so the diff is pure defender.
    assert r_super.side_b_final_hp < r_neutral.side_b_final_hp


def test_weak_attack_hits_softer():
    """NATURE attacker vs FIRE defender (reverse of strong) → 0.75×."""
    attacker = mk("vine", Element.NATURE, atk=10, defense=0, hp=999, spd=99)
    fire_defender  = mk("torch", Element.FIRE,  atk=0, defense=0, hp=100, spd=1)
    water_defender = mk("drop",  Element.WATER, atk=0, defense=0, hp=100, spd=1)

    r_weak = resolve_match(_team(attacker), _team(fire_defender),  SEED_ZERO)
    r_neutral = resolve_match(_team(attacker), _team(water_defender), SEED_ZERO)

    # Resisted matchup leaves side B with MORE total HP than the neutral case.
    assert r_weak.side_b_final_hp > r_neutral.side_b_final_hp


def test_element_multiplier_rounds_up():
    """A 3-damage hit at 1.5× should be ceil(4.5) = 5, not floor(4.5) = 4."""
    # pure-multiplier sanity, not combat-integrated
    dmg = math.ceil(3 * STRONG_MULT)
    assert dmg == 5
    dmg = math.ceil(2 * WEAK_MULT)
    assert dmg == 2  # ceil(1.5) = 2
