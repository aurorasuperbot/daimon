"""Shared test fixtures."""

from pathlib import Path

import pytest

from nullpoint.cards import load_card
from nullpoint.engine import Loadout
from nullpoint.engine.types import Card, Slot

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> Card:
    return load_card(FIXTURE_DIR / name)


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR


def make_filler(slot: Slot, card_id_suffix: str = "filler") -> Card:
    """Vanilla 5/5/20/5 filler for a given slot."""
    return Card(
        card_id=f"filler_{slot.name.lower()}_{card_id_suffix}",
        slot=slot,
        atk=5, defense=5, hp=20, spd=5,
    )


@pytest.fixture
def filler_loadout() -> Loadout:
    """6 vanilla filler cards. Useful as a baseline opponent."""
    return Loadout(cards=tuple(make_filler(Slot(i)) for i in range(6)))


@pytest.fixture
def vanilla_loadout() -> Loadout:
    """All-vanilla loadout from fixtures (test_card_01 in HEAD slot, fillers elsewhere)."""
    head = _load("test_card_01_vanilla_head.json")
    cards = [head] + [make_filler(Slot(i)) for i in range(1, 6)]
    return Loadout(cards=tuple(cards))


SEED_ZERO = b"\x00" * 32
SEED_ONE = b"\x00" * 31 + b"\x01"
