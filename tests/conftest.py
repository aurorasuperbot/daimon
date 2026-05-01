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


@pytest.fixture(autouse=True)
def _isolate_real_state_paths(monkeypatch, tmp_path_factory, request):
    """Defense-in-depth: redirect every "lives at ~/.config/daimon/" path to
    a per-test tmp dir for ALL tests, so a forgotten ``_isolate_paths`` call
    can't pollute the user's real ledger / quests / collection / loadouts.

    Why autouse rather than per-test: there are 100+ tests (test_mcp.py
    alone has 115) and even one missed ``_isolate_paths`` call lets a tool
    like ``dm_match`` write a ``quest_reward`` to the real
    ``~/.config/daimon/mining_ledger.jsonl`` (the auto-claim flow inside
    ``_refresh_and_claim_quests`` runs for every play action). Caught in
    the wild — see git log around the ledger truncation.

    Per-test tmp dirs (`tmp_path_factory.mktemp`) so nothing survives across
    the suite. Sets the env vars (DAIMON_HOME / XDG_CONFIG_HOME) AND
    monkeypatches every module-level path constant — necessary because
    several modules cache CONFIG_DIR / LEDGER_PATH at import time.

    Tests that opt into a specific tmp dir via their own ``_isolate_paths``
    helper still work — monkeypatch values just stack and the per-test
    helper wins.
    """
    sandbox = tmp_path_factory.mktemp("daimon-sandbox")
    cfg = sandbox / "config"
    cfg.mkdir()

    # Env-level redirect: any *future* import / fresh subprocess sees the
    # tmp paths via the canonical resolver. We only set XDG_CONFIG_HOME (the
    # second-priority branch in ``_resolve_config_dir``) — NOT DAIMON_HOME.
    # Some tests (e.g. test_cli_groups.py::isolated_home) set their own
    # XDG_CONFIG_HOME and reload modules to re-resolve CONFIG_DIR; setting
    # DAIMON_HOME here would override their explicit setenv since DAIMON_HOME
    # has higher precedence in the resolver. The module-level monkeypatches
    # below are the actual safety net — env vars are belt-and-suspenders.
    xdg = sandbox / "xdg"
    xdg.mkdir()
    (xdg / "daimon").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("DAIMON_STATE", str(cfg / "state.json"))
    monkeypatch.setenv("DAIMON_INBOX", str(sandbox / "inbox"))

    # Module-level redirect: rebind every cached path constant. Imports are
    # local so we don't drag the whole world into conftest at collect time;
    # each module is already imported by the test that needs it anyway.
    from daimon.identity import keys as identity_keys
    from daimon.mining import ledger as ledger_mod
    from daimon.mining import buffer as buffer_mod
    from daimon import collection as collection_mod
    from daimon import imprint as imprint_mod
    from daimon import match_history as match_history_mod
    from daimon.quests import state as quests_state

    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH",
                        cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH",
                        cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH",
                        cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH",
                        cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH",
                        cfg / "mine_buffer.jsonl")
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH",
                        cfg / "collection.json")
    monkeypatch.setattr(quests_state, "QUESTS_PATH",
                        cfg / "daily_quests.json")
    monkeypatch.setattr(imprint_mod, "IMPRINT_STATS_PATH",
                        cfg / "imprint_stats.json")
    monkeypatch.setattr(match_history_mod, "MATCH_HISTORY_PATH",
                        cfg / "match_history.jsonl")

    # MCP server has its own copies of LEDGER_PATH/COLLECTION_PATH/LOADOUTS_DIR
    # bound at import time. Patch them too. ``dm_match`` etc. all read these
    # globals (not ``ledger_mod.LEDGER_PATH``) for some operations.
    try:
        from daimon.mcp import server as mcp_server
    except Exception:  # noqa: BLE001
        mcp_server = None
    if mcp_server is not None:
        monkeypatch.setattr(mcp_server, "LEDGER_PATH",
                            cfg / "mining_ledger.jsonl")
        monkeypatch.setattr(mcp_server, "COLLECTION_PATH",
                            cfg / "collection.json")
        monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", cfg / "loadouts")

    # Arena state caches PVP_STATE_DIR at import time too.
    try:
        from daimon.arena import state as arena_state
        monkeypatch.setattr(arena_state, "PVP_STATE_DIR",
                            sandbox / "inbox" / "pvp_state")
    except Exception:  # noqa: BLE001
        pass


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
