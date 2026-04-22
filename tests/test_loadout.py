"""Loadout validation tests (V2 — monster pivot).

In V2 a team is 6 monsters with positions 0..5 (no slot enum).
Rules:
  - exactly 6 cards
  - no duplicate card_id within a team
  - at most 2 of the same species per team
"""

import pytest

from daimon.engine import Loadout, TEAM_SIZE
from daimon.engine.types import Card, Element


def _c(pos: int, species: str | None = None, element: Element = Element.NATURE) -> Card:
    """Helper: build a vanilla monster for position `pos`."""
    sp = species if species is not None else f"mon_{pos}"
    return Card(
        card_id=f"mon_{pos}_{sp}",
        species=sp,
        element=element,
        atk=1, defense=1, hp=1, spd=1,
    )


def test_valid_loadout_six_monsters():
    cards = tuple(_c(i) for i in range(TEAM_SIZE))
    lo = Loadout(cards=cards)
    assert lo.by_position(0).species == "mon_0"
    assert lo.by_position(5).species == "mon_5"


def test_rejects_wrong_count():
    with pytest.raises(ValueError, match="exactly 6"):
        Loadout(cards=tuple(_c(i) for i in range(5)))


def test_rejects_non_card_in_position():
    cards = [_c(i) for i in range(TEAM_SIZE)]
    cards[0] = "not a card"  # type: ignore
    with pytest.raises(TypeError, match="not a Card"):
        Loadout(cards=tuple(cards))


def test_rejects_duplicate_card_id():
    # Two different positions, same card_id → rejected
    c = _c(0, species="x")
    cards = (c, c) + tuple(_c(i, species=f"sp{i}") for i in range(2, TEAM_SIZE))
    with pytest.raises(ValueError, match="duplicate card_id"):
        Loadout(cards=cards)


def test_rejects_more_than_two_same_species():
    # Three monsters sharing species — each with unique card_id to pass the dup-id rule
    same_species = [
        Card(card_id=f"mon_{i}_shared", species="shared", element=Element.FIRE,
             atk=1, defense=1, hp=1, spd=1)
        for i in range(3)
    ]
    rest = [_c(i, species=f"other{i}") for i in range(3)]
    cards = tuple(same_species + rest)
    with pytest.raises(ValueError, match="too many 'shared'"):
        Loadout(cards=cards)


def test_allows_two_of_same_species():
    # Two of the same species is fine — it's the "evolution family" use case.
    two_boars = [
        Card(card_id=f"boar_{i}", species="boar", element=Element.NATURE,
             atk=1, defense=1, hp=1, spd=1)
        for i in range(2)
    ]
    rest = [_c(i + 2, species=f"other{i}") for i in range(4)]
    lo = Loadout(cards=tuple(two_boars + rest))
    assert lo.cards[0].species == "boar"
    assert lo.cards[1].species == "boar"


def test_position_bounds():
    cards = tuple(_c(i) for i in range(TEAM_SIZE))
    lo = Loadout(cards=cards)
    with pytest.raises(IndexError):
        lo.by_position(-1)
    with pytest.raises(IndexError):
        lo.by_position(TEAM_SIZE)
