"""Element type-effectiveness table.

V2 (2026-04-21): five elements in a closed rock-paper-scissors-plus-void loop.

Ring:  Fire → Nature → Water → Volt → Void → Fire (closes the loop)

Rules:
  - Attacker element strong-against defender element: 1.5× DAMAGE
  - Attacker element weak-against defender element: 0.75× DAMAGE
  - Same element or unrelated pair: 1.0× DAMAGE
  - Multiplier applies to post-DEF damage at the moment of hit.
  - Rounding: math.ceil — a 2-damage hit with 1.5× becomes 3, not 3.0.

The effectiveness map is the single source of truth. One lookup per hit;
cheap, deterministic, test-locked.
"""

from __future__ import annotations

from daimon.engine.types import Element


# (attacker, defender) → multiplier.
# Symmetric: if A is strong vs B (1.5), B is weak vs A (0.75).
# Closed 5-ring: FIRE → NATURE → WATER → VOLT → VOID → FIRE
_STRONG_AGAINST: tuple[tuple[Element, Element], ...] = (
    (Element.FIRE, Element.NATURE),
    (Element.NATURE, Element.WATER),
    (Element.WATER, Element.VOLT),
    (Element.VOLT, Element.VOID),
    (Element.VOID, Element.FIRE),
)

STRONG_MULT = 1.5
WEAK_MULT = 0.75
NEUTRAL_MULT = 1.0


def _build_table() -> dict[tuple[int, int], float]:
    t: dict[tuple[int, int], float] = {}
    for atk, defn in _STRONG_AGAINST:
        t[(int(atk), int(defn))] = STRONG_MULT
        t[(int(defn), int(atk))] = WEAK_MULT
    return t


_EFFECTIVENESS: dict[tuple[int, int], float] = _build_table()


def element_multiplier(attacker: Element, defender: Element) -> float:
    """Return the damage multiplier for attacker's element vs defender's element.

    Returns 1.0 for neutral pairs (same element, or unrelated pairs).
    Returns 1.5 if attacker is strong against defender.
    Returns 0.75 if attacker is weak against defender.
    """
    return _EFFECTIVENESS.get((int(attacker), int(defender)), NEUTRAL_MULT)


def strong_against(attacker: Element) -> tuple[Element, ...]:
    """Which elements does `attacker` deal super-effective damage to?"""
    return tuple(d for (a, d), m in _EFFECTIVENESS.items()
                 if a == int(attacker) and m == STRONG_MULT)


def weak_against(attacker: Element) -> tuple[Element, ...]:
    """Which elements resist `attacker`'s damage?"""
    return tuple(d for (a, d), m in _EFFECTIVENESS.items()
                 if a == int(attacker) and m == WEAK_MULT)
