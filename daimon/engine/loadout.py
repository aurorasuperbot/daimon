"""Loadout: a team of 6 monsters. Validation is structural.

V2 (monster pivot): no more slot concept. A loadout is an ordered list of
6 monster cards. Position in the list = battle position (0..5). Players choose
their own positioning, so a front-line tank at position 0 vs a glass cannon at
position 0 is a strategic choice, not a forced anatomy.

Rules enforced:
  - Exactly 6 cards.
  - No duplicate card_id within a team (can't run 6× Voltcat Apex).
  - Species duplication is allowed in V2 (evolution families) but gated to
    at most 2 of the same species to keep matchups varied.
"""

from __future__ import annotations

from dataclasses import dataclass

from daimon.engine.types import Card, TEAM_SIZE


MAX_SAME_SPECIES = 2  # at most 2 monsters from the same species family


@dataclass(frozen=True)
class Loadout:
    """Team of exactly TEAM_SIZE monster cards. Order = battle position."""
    cards: tuple[Card, ...]

    def __post_init__(self) -> None:
        validate_loadout(self.cards)

    def by_position(self, position: int) -> Card:
        if position < 0 or position >= TEAM_SIZE:
            raise IndexError(f"position {position} out of range [0, {TEAM_SIZE})")
        return self.cards[position]


def validate_loadout(cards: tuple[Card, ...]) -> None:
    if len(cards) != TEAM_SIZE:
        raise ValueError(
            f"Loadout must have exactly {TEAM_SIZE} cards, got {len(cards)}"
        )
    seen_ids: set[str] = set()
    species_counts: dict[str, int] = {}
    for i, card in enumerate(cards):
        if not isinstance(card, Card):
            raise TypeError(f"Loadout position {i} is not a Card")
        if card.card_id in seen_ids:
            raise ValueError(
                f"duplicate card_id {card.card_id!r} at position {i} "
                f"(already used earlier in team)"
            )
        seen_ids.add(card.card_id)
        species_counts[card.species] = species_counts.get(card.species, 0) + 1
        if species_counts[card.species] > MAX_SAME_SPECIES:
            raise ValueError(
                f"too many {card.species!r} in team "
                f"(max {MAX_SAME_SPECIES}, got {species_counts[card.species]})"
            )
