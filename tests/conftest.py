"""Shared test fixtures."""

from pathlib import Path

import pytest

from daimon.cards import load_card
from daimon.engine import Loadout, TEAM_SIZE
from daimon.engine.types import Card, Element

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> Card:
    return load_card(FIXTURE_DIR / name)


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR


# V2 filler monsters — one per element for variety, cycling.
_FILLER_ELEMENTS = (
    Element.FIRE,
    Element.WATER,
    Element.NATURE,
    Element.VOLT,
    Element.VOID,
    Element.FIRE,   # 6th position cycles back
)


def make_filler(position: int, card_id_suffix: str = "filler") -> Card:
    """Vanilla 5/5/20/5 filler at a given team position (0..5)."""
    if position < 0 or position >= TEAM_SIZE:
        raise ValueError(f"position must be 0..{TEAM_SIZE-1}")
    element = _FILLER_ELEMENTS[position]
    return Card(
        card_id=f"filler_{position}_{card_id_suffix}",
        species=f"filler_{position}",
        element=element,
        atk=5, defense=5, hp=20, spd=5,
    )


@pytest.fixture
def filler_loadout() -> Loadout:
    """6 vanilla filler monsters. Useful as a baseline opponent."""
    return Loadout(cards=tuple(make_filler(i) for i in range(TEAM_SIZE)))


@pytest.fixture
def vanilla_loadout() -> Loadout:
    """All-vanilla loadout from fixtures (test_card_01 at position 0, fillers elsewhere)."""
    lead = _load("test_card_01_vanilla_head.json")
    cards = [lead] + [make_filler(i) for i in range(1, TEAM_SIZE)]
    return Loadout(cards=tuple(cards))


SEED_ZERO = b"\x00" * 32
SEED_ONE = b"\x00" * 31 + b"\x01"
