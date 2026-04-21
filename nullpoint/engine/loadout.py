"""Loadout: 6 cards, one per slot. Validation is structural."""

from __future__ import annotations

from dataclasses import dataclass

from nullpoint.engine.types import Card, SLOT_COUNT, Slot


@dataclass(frozen=True)
class Loadout:
    """Six cards, exactly one per Slot. Order matches Slot enum."""
    cards: tuple[Card, ...]

    def __post_init__(self) -> None:
        validate_loadout(self.cards)

    def by_slot(self, slot: Slot) -> Card:
        return self.cards[int(slot)]


def validate_loadout(cards: tuple[Card, ...]) -> None:
    if len(cards) != SLOT_COUNT:
        raise ValueError(
            f"Loadout must have exactly {SLOT_COUNT} cards, got {len(cards)}"
        )
    for i, card in enumerate(cards):
        if not isinstance(card, Card):
            raise TypeError(f"Loadout slot {i} is not a Card")
        if int(card.slot) != i:
            raise ValueError(
                f"Loadout slot {i} expects {Slot(i).name}, "
                f"got card with slot={Slot(card.slot).name}"
            )
