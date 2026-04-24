"""Shared test fixtures."""

from pathlib import Path

import pytest

from daimon.cards import load_card
from daimon.engine import Loadout, TEAM_SIZE
from daimon.engine.types import Card, Element

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_art_autoupdate(monkeypatch, request):
    """Block the CLI's auto-fetch hook for every test by default.

    The ``main`` group callback in ``daimon.cli`` calls
    ``ensure_art_available()`` for non-pure subcommands. In the test sandbox
    that would try to hit the GitHub API on every CLI invocation — flaky,
    slow, and pollutes the user's real ``~/.daimon`` if env-isolation slips.

    We can't rely on ``DAIMON_NO_AUTO_UPDATE=1`` alone: by design, that flag
    does NOT short-circuit the FIRST-RUN sync download (users without art
    can't proceed). For tests we want the function to be a true no-op, so
    we monkeypatch it at the import site (``daimon.cli`` imports it lazily
    inside the callback, so we patch the source module).

    Skipped for ``tests/test_update.py`` — that file exercises the real
    update flow and needs the live function. We detect by module name so
    no per-fixture opt-out is required there.
    """
    if request.module.__name__.endswith("test_update"):
        return
    monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "1")
    import daimon.update as _update_pkg
    from daimon.update import checker as _checker
    _noop = lambda *_a, **_kw: None  # noqa: E731
    monkeypatch.setattr(_checker, "ensure_art_available", _noop)
    # The CLI does `from daimon.update import ensure_art_available`, which
    # binds against the re-export in __init__.py — patch that name too.
    monkeypatch.setattr(_update_pkg, "ensure_art_available", _noop)


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
