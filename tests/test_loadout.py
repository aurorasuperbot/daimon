"""Loadout validation tests."""

import pytest

from nullpoint.engine import Loadout, validate_loadout
from nullpoint.engine.types import Card, Slot


def _c(slot: Slot) -> Card:
    return Card(card_id=f"c_{slot.name}", slot=slot, atk=1, defense=1, hp=1, spd=1)


def test_valid_loadout_six_in_order():
    cards = tuple(_c(Slot(i)) for i in range(6))
    lo = Loadout(cards=cards)
    assert lo.by_slot(Slot.HEAD).slot == Slot.HEAD
    assert lo.by_slot(Slot.CORE).slot == Slot.CORE


def test_rejects_wrong_count():
    with pytest.raises(ValueError, match="exactly 6"):
        Loadout(cards=tuple(_c(Slot(i)) for i in range(5)))


def test_rejects_wrong_slot_order():
    cards = list(_c(Slot(i)) for i in range(6))
    cards[0], cards[1] = cards[1], cards[0]  # swap HEAD and TORSO positions
    with pytest.raises(ValueError, match="slot"):
        Loadout(cards=tuple(cards))


def test_rejects_non_card_in_slot():
    cards = [_c(Slot(i)) for i in range(6)]
    cards[0] = "not a card"  # type: ignore
    with pytest.raises(TypeError, match="not a Card"):
        Loadout(cards=tuple(cards))
