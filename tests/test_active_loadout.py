"""Tests for ``daimon.loadouts.active`` + the dm_loadout_* MCP wiring.

The active-loadout pointer is a single small JSON file at
``~/.config/daimon/loadout_meta.json`` (see :mod:`daimon.loadouts.active`
for the rationale: pointer-by-name, atomic writes, conservative empty-on-
corruption reads). On top of that pointer, the MCP layer wires:

  * ``dm_loadout_set(name)``        — explicit pointer swap
  * ``dm_loadout_get_active()``     — inspect the pointer (with stale-detect)
  * ``dm_loadout_clear_active()``   — explicit unset
  * ``dm_loadout_save(loadout, name)`` — auto-sets active on FIRST save
  * ``dm_loadout_list()``           — flags active per entry + top-level
  * ``dm_match_npc(npc_id, loadout=None, ...)`` — defaults to active
  * ``_saved_loadouts_summary()``   — flags active in the home payload

## Coverage map

  * Engine layer (``daimon.loadouts.active``):
      - round-trip: set → get → clear
      - missing file → None
      - corrupt JSON → None (no raise)
      - missing-version → None
      - validate_exists honours the saved-loadout file's existence
      - atomic write leaves no .tmp behind
      - set/clear are idempotent
      - clear writes a fully-formed doc (visible for debugging)

  * MCP layer:
      - dm_loadout_set: success envelope, name validation, unknown-loadout
      - dm_loadout_get_active: never-set, set-and-exists, set-but-deleted
      - dm_loadout_clear_active: previous-pointer surfaced
      - dm_loadout_save: first save auto-sets, subsequent saves don't
      - dm_loadout_list: active flag + top-level pointer
      - dm_match_npc: defaults to active, explicit loadout still wins,
        no_active_loadout error when omitted with no pointer,
        active_loadout_missing on stale pointer,
        active_loadout_corrupt on bad JSON
      - _saved_loadouts_summary: active flag flows into dm_home payload
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest


# Helper: extract the actual callable from the FastMCP decorator if needed.
def _call(tool, **kwargs):
    """FastMCP wraps the function; .fn is the original callable."""
    fn = getattr(tool, "fn", tool)
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Fixtures — mirror the per-test isolation pattern in test_mcp.py and
# test_onboarding_stages.py so we can run side-by-side without leaking
# state into the user's real ~/.config/daimon.
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_paths(monkeypatch, tmp_path):
    """Redirect identity/loadouts/state into a temp dir.

    Returns the resolved CONFIG_DIR for tests that need to inspect it
    directly (e.g. asserting the meta file landed there).
    """
    from daimon.identity import keys as identity_keys
    from daimon.mining import buffer as buffer_mod
    from daimon.mining import ledger as ledger_mod
    from daimon.quests import state as quests_state
    from daimon import collection as collection_mod
    from daimon.mcp import server as mcp_server

    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH",
                        cfg / "collection.json")
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH",
                        cfg / "collection.json")
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", cfg / "loadouts")
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH", cfg / "mine_buffer.jsonl")
    # Ledger + quests state — without these, dm_match_npc / dm_pull leak
    # quest_reward entries to the user's real ~/.config/daimon/mining_ledger.jsonl
    # because evaluate_and_claim() reads ledger_mod.LEDGER_PATH at call time.
    # (Caught 2026-04-29 when a real player's ledger was corrupted by these
    # leaked entries during a test run.)
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(mcp_server, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(quests_state, "QUESTS_PATH", cfg / "daily_quests.json")
    monkeypatch.setenv("DAIMON_STATE", str(cfg / "state.json"))
    return cfg


# Build helpers — copied from test_mcp.py so we don't cross-import a
# private fixture set. A 6-card loadout with 5 distinct elements is
# enough for `dm_loadout_save` to validate cleanly.

FIXTURE_DIR = Path(__file__).parent / "fixtures"
_FILLER_ELEMENTS = ["FIRE", "WATER", "NATURE", "VOLT", "VOID"]


def _vanilla_head_dict() -> dict:
    return json.loads(
        (FIXTURE_DIR / "test_card_01_vanilla_head.json").read_text(encoding="utf-8")
    )


def _filler_card_dict(position: int, suffix: str = "f") -> dict:
    element = _FILLER_ELEMENTS[position % len(_FILLER_ELEMENTS)]
    return {
        "card_id": f"filler_{position}_{suffix}",
        "species": f"filler_{position}",
        "element": element,
        "atk": 5,
        "def": 5,
        "hp": 20,
        "spd": 5,
        "triggers": [],
    }


def _full_loadout_dict() -> dict:
    lead = _vanilla_head_dict()
    cards = [lead] + [_filler_card_dict(i) for i in range(1, 6)]
    return {"cards": cards}


# ---------------------------------------------------------------------------
# Engine layer — daimon.loadouts.active
# ---------------------------------------------------------------------------


def test_get_returns_none_when_meta_missing(isolated_paths):
    from daimon.loadouts.active import get_active_loadout_name
    assert get_active_loadout_name() is None
    assert get_active_loadout_name(validate_exists=False) is None


def test_set_then_get_round_trip(isolated_paths):
    from daimon.loadouts.active import (
        get_active_loadout_name,
        set_active_loadout_name,
    )
    # validate_exists=False because we haven't created the saved-loadout
    # file yet — we're testing the pointer in isolation.
    set_active_loadout_name("aggro_volt")
    assert get_active_loadout_name(validate_exists=False) == "aggro_volt"


def test_set_writes_atomically_no_tmp_left_behind(isolated_paths):
    from daimon.loadouts.active import (
        _meta_path,
        set_active_loadout_name,
    )
    set_active_loadout_name("aggro_volt")
    meta = _meta_path()
    assert meta.exists()
    tmp = meta.with_suffix(meta.suffix + ".tmp")
    assert not tmp.exists(), f"stray tmp file: {tmp}"
    # Doc shape on disk
    doc = json.loads(meta.read_text(encoding="utf-8"))
    assert doc == {"version": 1, "active_loadout": "aggro_volt"}


def test_set_overwrites_previous(isolated_paths):
    from daimon.loadouts.active import (
        get_active_loadout_name,
        set_active_loadout_name,
    )
    set_active_loadout_name("first")
    set_active_loadout_name("second")
    assert get_active_loadout_name(validate_exists=False) == "second"


def test_clear_writes_null_pointer(isolated_paths):
    from daimon.loadouts.active import (
        _meta_path,
        clear_active_loadout,
        get_active_loadout_name,
        set_active_loadout_name,
    )
    set_active_loadout_name("placeholder")
    clear_active_loadout()
    assert get_active_loadout_name(validate_exists=False) is None
    # Doc still on disk for debug visibility — body is null, not missing.
    doc = json.loads(_meta_path().read_text(encoding="utf-8"))
    assert doc == {"version": 1, "active_loadout": None}


def test_clear_when_unset_is_a_noop(isolated_paths):
    from daimon.loadouts.active import (
        clear_active_loadout,
        get_active_loadout_name,
    )
    clear_active_loadout()
    clear_active_loadout()  # idempotent
    assert get_active_loadout_name(validate_exists=False) is None


def test_set_rejects_empty_name(isolated_paths):
    from daimon.loadouts.active import set_active_loadout_name
    with pytest.raises(ValueError):
        set_active_loadout_name("")


def test_set_rejects_non_string(isolated_paths):
    from daimon.loadouts.active import set_active_loadout_name
    with pytest.raises(ValueError):
        set_active_loadout_name(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        set_active_loadout_name(123)  # type: ignore[arg-type]


def test_get_returns_none_on_corrupt_json(isolated_paths):
    """A previous half-flushed write should NOT strand the user with a
    raised exception — the reader treats unparseable JSON as 'unset'."""
    from daimon.loadouts.active import _meta_path, get_active_loadout_name
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not valid json")
    assert get_active_loadout_name(validate_exists=False) is None


def test_get_returns_none_on_stale_version(isolated_paths):
    """Bumping the schema version means old docs read as empty (next
    write reformats cleanly)."""
    from daimon.loadouts.active import (
        ACTIVE_META_VERSION,
        _meta_path,
        get_active_loadout_name,
    )
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    stale = {"version": ACTIVE_META_VERSION + 99, "active_loadout": "old"}
    p.write_text(json.dumps(stale))
    assert get_active_loadout_name(validate_exists=False) is None


def test_get_returns_none_on_non_object_root(isolated_paths):
    from daimon.loadouts.active import _meta_path, get_active_loadout_name
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[1, 2, 3]")
    assert get_active_loadout_name(validate_exists=False) is None


def test_get_returns_none_on_blank_file(isolated_paths):
    from daimon.loadouts.active import _meta_path, get_active_loadout_name
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("   \n  \n")
    assert get_active_loadout_name(validate_exists=False) is None


def test_validate_exists_true_returns_none_when_loadout_file_missing(
    isolated_paths,
):
    """The pointer can outlive the file (user `rm`'d it). Default reader
    treats that as 'unset' so the home card doesn't render a dead chip."""
    from daimon.loadouts.active import (
        get_active_loadout_name,
        set_active_loadout_name,
    )
    set_active_loadout_name("ghost")
    # No file at ~/.config/daimon/loadouts/ghost.json
    assert get_active_loadout_name() is None
    # validate_exists=False still surfaces the raw pointer for diagnostics.
    assert get_active_loadout_name(validate_exists=False) == "ghost"


def test_validate_exists_true_returns_name_when_loadout_file_present(
    isolated_paths,
):
    from daimon.loadouts.active import (
        _saved_loadout_path,
        get_active_loadout_name,
        set_active_loadout_name,
    )
    set_active_loadout_name("realdeck")
    target = _saved_loadout_path("realdeck")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"name": "realdeck", "cards": []}))
    assert get_active_loadout_name() == "realdeck"


def test_meta_path_lives_under_config_dir(isolated_paths):
    """Path resolution honours the monkeypatched CONFIG_DIR (lazy import).
    Otherwise tests would silently write to the user's real config."""
    from daimon.loadouts.active import _meta_path
    cfg = isolated_paths
    assert _meta_path() == cfg / "loadout_meta.json"


# ---------------------------------------------------------------------------
# MCP layer — dm_loadout_set / dm_loadout_get_active / dm_loadout_clear_active
# ---------------------------------------------------------------------------


def test_dm_loadout_get_active_when_never_set(isolated_paths):
    from daimon.mcp.server import dm_loadout_get_active
    r = _call(dm_loadout_get_active)
    assert r["status"] == "ok"
    assert r["active_loadout"] is None
    assert r["exists"] is False


def test_dm_loadout_set_succeeds_for_existing_loadout(isolated_paths):
    from daimon.mcp.server import dm_loadout_save, dm_loadout_set
    save = _call(dm_loadout_save, loadout=_full_loadout_dict(), name="alpha")
    assert save["status"] == "ok"
    # The auto-set on first-save means alpha is already active. We
    # explicitly re-set to prove dm_loadout_set works idempotently and
    # surfaces the previous pointer.
    r = _call(dm_loadout_set, name="alpha")
    assert r["status"] == "ok"
    assert r["active_loadout"] == "alpha"
    assert r["previous"] == "alpha"


def test_dm_loadout_set_unknown_loadout_envelope(isolated_paths):
    from daimon.mcp.server import dm_loadout_set
    r = _call(dm_loadout_set, name="never_saved")
    assert r["error"] == "unknown_loadout"
    assert r["name"] == "never_saved"
    assert "status" not in r


def test_dm_loadout_set_invalid_name_envelope(isolated_paths):
    from daimon.mcp.server import dm_loadout_set
    r = _call(dm_loadout_set, name="../../etc/passwd")
    assert r["error"] == "invalid_name"
    assert "status" not in r


def test_dm_loadout_set_swap_surfaces_previous(isolated_paths):
    from daimon.mcp.server import dm_loadout_save, dm_loadout_set
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="first")
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="second")
    # First save auto-set "first". Swap to "second" and verify diff.
    r = _call(dm_loadout_set, name="second")
    assert r["active_loadout"] == "second"
    assert r["previous"] == "first"


def test_dm_loadout_get_active_reports_stale_pointer(isolated_paths):
    """Active set, but the underlying file was deleted by hand."""
    from daimon.mcp.server import (
        LOADOUTS_DIR,
        dm_loadout_get_active,
        dm_loadout_save,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="ghost")
    # Remove the loadout file but leave the pointer intact.
    (LOADOUTS_DIR / "ghost.json").unlink()
    r = _call(dm_loadout_get_active)
    assert r["status"] == "ok"
    assert r["active_loadout"] == "ghost"
    assert r["exists"] is False


def test_dm_loadout_clear_active_returns_previous(isolated_paths):
    from daimon.mcp.server import (
        dm_loadout_clear_active,
        dm_loadout_get_active,
        dm_loadout_save,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="clearme")
    r = _call(dm_loadout_clear_active)
    assert r["status"] == "ok"
    assert r["previous"] == "clearme"
    after = _call(dm_loadout_get_active)
    assert after["active_loadout"] is None


# ---------------------------------------------------------------------------
# dm_loadout_save auto-set semantics
# ---------------------------------------------------------------------------


def test_dm_loadout_save_first_save_auto_sets_active(isolated_paths):
    from daimon.mcp.server import dm_loadout_get_active, dm_loadout_save
    r = _call(dm_loadout_save,
              loadout=_full_loadout_dict(), name="firstever")
    assert r["status"] == "ok"
    assert r["set_active"] is True
    after = _call(dm_loadout_get_active)
    assert after["active_loadout"] == "firstever"


def test_dm_loadout_save_subsequent_does_not_reassign(isolated_paths):
    from daimon.mcp.server import dm_loadout_get_active, dm_loadout_save
    r1 = _call(dm_loadout_save,
               loadout=_full_loadout_dict(), name="alpha")
    r2 = _call(dm_loadout_save,
               loadout=_full_loadout_dict(), name="beta")
    assert r1["set_active"] is True
    # Second save did NOT swap the active — explicit dm_loadout_set is
    # required for that. This protects users who already have a curated
    # active loadout from accidental overwrites when saving experiments.
    assert r2["set_active"] is False
    after = _call(dm_loadout_get_active)
    assert after["active_loadout"] == "alpha"


def test_dm_loadout_save_after_clear_re_auto_sets(isolated_paths):
    """After a clear, the very next save behaves like a first-save again."""
    from daimon.mcp.server import (
        dm_loadout_clear_active,
        dm_loadout_get_active,
        dm_loadout_save,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="alpha")
    _call(dm_loadout_clear_active)
    r = _call(dm_loadout_save,
              loadout=_full_loadout_dict(), name="beta")
    assert r["set_active"] is True
    after = _call(dm_loadout_get_active)
    assert after["active_loadout"] == "beta"


# ---------------------------------------------------------------------------
# dm_loadout_list flagging
# ---------------------------------------------------------------------------


def test_dm_loadout_list_flags_active_per_entry_and_top_level(isolated_paths):
    from daimon.mcp.server import (
        dm_loadout_list,
        dm_loadout_save,
        dm_loadout_set,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="alpha")
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="beta")
    _call(dm_loadout_set, name="beta")

    r = _call(dm_loadout_list)
    assert r["count"] == 2
    assert r["active_loadout"] == "beta"
    by_name = {entry["name"]: entry for entry in r["loadouts"]}
    assert by_name["alpha"]["active"] is False
    assert by_name["beta"]["active"] is True


def test_dm_loadout_list_no_loadouts_dir_still_returns_active_pointer(
    isolated_paths,
):
    """Even with no loadouts dir, the top-level active_loadout pointer
    is honest about what's set (which can happen if the user `rm -rf`'d
    the loadouts dir but didn't clear the pointer)."""
    from daimon.loadouts.active import set_active_loadout_name
    from daimon.mcp.server import dm_loadout_list
    set_active_loadout_name("ghost")
    r = _call(dm_loadout_list)
    assert r["count"] == 0
    assert r["loadouts"] == []
    assert r["active_loadout"] == "ghost"


# ---------------------------------------------------------------------------
# _saved_loadouts_summary — used by dm_home
# ---------------------------------------------------------------------------


def test_saved_loadouts_summary_flags_active(isolated_paths):
    from daimon.mcp.server import (
        _saved_loadouts_summary,
        dm_loadout_save,
        dm_loadout_set,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="aggro")
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="control")
    _call(dm_loadout_set, name="control")
    summary = _saved_loadouts_summary()
    by_name = {row["name"]: row for row in summary}
    assert by_name["aggro"]["active"] is False
    assert by_name["control"]["active"] is True
    # Card-count surfaces unchanged for non-active too
    assert by_name["aggro"]["card_count"] == 6


def test_saved_loadouts_summary_no_active_set_is_all_false(isolated_paths):
    from daimon.mcp.server import (
        _saved_loadouts_summary,
        dm_loadout_clear_active,
        dm_loadout_save,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="alpha")
    _call(dm_loadout_clear_active)
    summary = _saved_loadouts_summary()
    assert all(row["active"] is False for row in summary)


# ---------------------------------------------------------------------------
# dm_match_npc — defaults to active loadout
# ---------------------------------------------------------------------------


def test_match_npc_uses_active_when_loadout_omitted(isolated_paths):
    from daimon.mcp.server import (
        dm_loadout_save,
        dm_match_npc,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="defender")
    # First save auto-set "defender" as active. Now omit loadout and play.
    r = _call(dm_match_npc, npc_id="sparring_sam", seed="00" * 32)
    assert r["status"] == "ok"
    assert r["used_active_loadout"] == "defender"
    assert r["winner"] in (0, 1, None)


def test_match_npc_explicit_loadout_overrides_active(isolated_paths):
    from daimon.mcp.server import (
        dm_loadout_save,
        dm_match_npc,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="active_one")
    explicit = _full_loadout_dict()
    r = _call(dm_match_npc,
              npc_id="sparring_sam",
              loadout=explicit,
              seed="00" * 32)
    assert r["status"] == "ok"
    # used_active_loadout MUST be None — the caller passed an explicit
    # loadout, so we never even consult the pointer.
    assert r.get("used_active_loadout") is None


def test_match_npc_no_active_set_returns_hint(isolated_paths):
    from daimon.mcp.server import dm_match_npc
    r = _call(dm_match_npc, npc_id="sparring_sam", seed="00" * 32)
    assert r["error"] == "no_active_loadout"
    assert "hint" in r
    assert "dm_loadout_save" in r["hint"]
    assert "dm_loadout_set" in r["hint"]


def test_match_npc_stale_active_pointer_is_explicit_error(isolated_paths):
    """Pointer set, file deleted by hand → distinct error code so the
    UI can offer a self-corrective action (clear pointer / pick another)."""
    from daimon.mcp.server import (
        LOADOUTS_DIR,
        dm_loadout_save,
        dm_match_npc,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="ghost")
    (LOADOUTS_DIR / "ghost.json").unlink()
    r = _call(dm_match_npc, npc_id="sparring_sam", seed="00" * 32)
    assert r["error"] == "active_loadout_missing"
    assert r["name"] == "ghost"
    assert "hint" in r


def test_match_npc_corrupt_active_loadout_envelope(isolated_paths):
    """The on-disk loadout JSON is invalid — distinct error code lets
    callers tell 'no pointer' apart from 'pointer points at trash'."""
    from daimon.mcp.server import (
        LOADOUTS_DIR,
        dm_loadout_save,
        dm_match_npc,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="bad")
    (LOADOUTS_DIR / "bad.json").write_text("{ not valid json")
    r = _call(dm_match_npc, npc_id="sparring_sam", seed="00" * 32)
    assert r["error"] == "active_loadout_corrupt"
    assert r["name"] == "bad"


def test_match_npc_active_with_bad_cards_field_envelope(isolated_paths):
    from daimon.mcp.server import (
        LOADOUTS_DIR,
        dm_loadout_save,
        dm_match_npc,
    )
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="bad")
    (LOADOUTS_DIR / "bad.json").write_text(
        json.dumps({"name": "bad", "cards": "not a list"})
    )
    r = _call(dm_match_npc, npc_id="sparring_sam", seed="00" * 32)
    assert r["error"] == "active_loadout_corrupt"
    assert r["name"] == "bad"


def test_match_npc_unknown_npc_short_circuits_active_resolution(
    isolated_paths,
):
    """The NPC lookup happens BEFORE we resolve the player loadout.
    Validates we don't burn cycles on the active-pointer dance just to
    reject a bad npc_id."""
    from daimon.mcp.server import dm_match_npc
    # No active set; if this short-circuited correctly, we get
    # unknown_npc, not no_active_loadout.
    r = _call(dm_match_npc, npc_id="not_a_real_npc")
    assert r["error"] == "unknown_npc"


# ---------------------------------------------------------------------------
# dm_home carries the active flag end-to-end
# ---------------------------------------------------------------------------


def test_dm_home_payload_carries_active_flag(isolated_paths):
    """dm_home → saved_loadouts → flagged with active boolean. This is
    what the chat home card ultimately reads to render the ACTIVE chip
    distinction."""
    from daimon.mcp.server import (
        dm_home,
        dm_init,
        dm_loadout_save,
        dm_loadout_set,
    )
    # dm_home requires an identity to render the full payload.
    _call(dm_init)
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="aggro")
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="midrange")
    _call(dm_loadout_set, name="midrange")

    payload = _call(dm_home)
    saved = payload.get("saved_loadouts") or []
    assert saved, "saved_loadouts should be populated"
    by_name = {row["name"]: row for row in saved}
    assert by_name["aggro"]["active"] is False
    assert by_name["midrange"]["active"] is True
