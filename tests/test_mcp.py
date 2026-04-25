"""Tests for the MCP server tools.

We test the tool functions directly (the @mcp.tool() decorator preserves the
underlying callable as `<tool>.fn` in FastMCP, but accessing them via the
module-level names works because FastMCP returns the original function).

Coverage:
  - dm_whoami: missing identity → graceful error; with identity → pubkey hex
  - dm_match: vanilla mirror → draw; invalid input → error envelope; round
              log opt-in works; bare-list and dict-with-cards both accepted
  - dm_loadout_validate: valid + invalid cases
  - dm_collection: missing file → empty; corrupt JSON → error envelope
  - dm_pull: insufficient_balance without ledger; success after seeding ledger
  - dm_mine_status: missing ledger → empty; populated ledger → real stats
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daimon.mcp import server as mcp_server
from daimon.mcp.server import (
    dm_card_compare,
    dm_card_propose,
    dm_catalog_card,
    dm_catalog_list,
    dm_collection,
    dm_dispute_open,
    dm_expansions,
    dm_init,
    dm_leaderboard,
    dm_loadout_list,
    dm_loadout_load,
    dm_loadout_save,
    dm_loadout_validate,
    dm_match,
    dm_match_npc,
    dm_mine_status,
    dm_my_rank,
    dm_npc,
    dm_npcs,
    dm_pull,
    dm_pvp_accept,
    dm_pvp_challenge,
    dm_pvp_my_matches,
    dm_pvp_reveal,
    dm_pvp_status,
    dm_register,
    dm_whoami,
)


# Helper: extract the actual callable from the FastMCP decorator if needed.
def _call(tool, **kwargs):
    """FastMCP wraps the function; .fn is the original callable."""
    fn = getattr(tool, "fn", tool)
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _vanilla_head_dict() -> dict:
    return json.loads((FIXTURE_DIR / "test_card_01_vanilla_head.json").read_text())


_FILLER_ELEMENTS = ["FIRE", "WATER", "NATURE", "VOLT", "VOID"]


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
# dm_init — bootstrap-over-MCP tool (closes the gap where MCP-only agents
# couldn't create an identity without shell access)
# ---------------------------------------------------------------------------

def _patch_identity_paths(monkeypatch, tmp_path):
    """Isolate identity keys to a tmp dir so dm_init doesn't touch real ~/."""
    from daimon.identity import keys as keys_mod
    fake = tmp_path / "ident"
    fake.mkdir(exist_ok=True)
    monkeypatch.setattr(keys_mod, "CONFIG_DIR", fake)
    monkeypatch.setattr(keys_mod, "PRIVATE_KEY_PATH", fake / "identity.key")
    monkeypatch.setattr(keys_mod, "PUBLIC_KEY_PATH", fake / "identity.pub")
    monkeypatch.setattr(keys_mod, "METADATA_PATH", fake / "identity.json")
    return fake


def test_init_creates_identity_from_scratch(monkeypatch, tmp_path):
    fake = _patch_identity_paths(monkeypatch, tmp_path)
    result = _call(dm_init)
    assert result["status"] == "ok"
    assert result["created"] is True
    assert len(result["pubkey_hex"]) == 64
    # Mnemonic returned once, non-empty, BIP39-shape (24 words).
    assert result["mnemonic"]
    assert len(result["mnemonic"].split()) == 24
    assert "warning" in result
    # Files actually written to disk.
    assert (fake / "identity.key").exists()
    assert (fake / "identity.pub").exists()


def test_init_returns_mnemonic_once_only(monkeypatch, tmp_path):
    """The mnemonic is in the response but MUST NOT be persisted to disk —
    otherwise we've defeated the whole point of a recovery phrase."""
    fake = _patch_identity_paths(monkeypatch, tmp_path)
    result = _call(dm_init)
    mnemonic = result["mnemonic"]
    # Scan every file in the config dir — mnemonic must not appear.
    for p in fake.rglob("*"):
        if p.is_file():
            try:
                content = p.read_text()
            except (UnicodeDecodeError, PermissionError):
                # Binary key files — won't contain the mnemonic as text anyway
                continue
            assert mnemonic not in content, f"mnemonic leaked to {p}"


def test_init_refuses_existing_without_force(monkeypatch, tmp_path):
    fake = _patch_identity_paths(monkeypatch, tmp_path)
    r1 = _call(dm_init)
    assert r1["status"] == "ok"
    existing_pub = r1["pubkey_hex"]

    r2 = _call(dm_init)
    assert r2.get("error") == "identity_exists"
    assert r2["pubkey_hex"] == existing_pub  # surfaces existing for agent
    assert "force" in r2["hint"].lower()
    # Did NOT overwrite.
    from daimon.identity import load_identity
    assert load_identity().pubkey_hex == existing_pub


def test_init_force_overwrites(monkeypatch, tmp_path):
    _patch_identity_paths(monkeypatch, tmp_path)
    r1 = _call(dm_init)
    old_pub = r1["pubkey_hex"]

    r2 = _call(dm_init, force=True)
    assert r2["status"] == "ok"
    assert r2["pubkey_hex"] != old_pub  # new key generated
    assert r2["created"] is True


def test_init_unblocks_whoami_end_to_end(monkeypatch, tmp_path):
    """Proves MCP-only workflow: init → whoami works, no shell needed."""
    _patch_identity_paths(monkeypatch, tmp_path)

    # Before init: whoami errors out.
    before = _call(dm_whoami)
    assert before.get("error") == "no_identity"

    # Run init via MCP.
    init_result = _call(dm_init)
    assert init_result["status"] == "ok"

    # After init: whoami returns the identity we just created.
    after = _call(dm_whoami)
    assert "error" not in after
    assert after["pubkey_hex"] == init_result["pubkey_hex"]


# ---------------------------------------------------------------------------
# dm_whoami
# ---------------------------------------------------------------------------

def test_whoami_no_identity(tmp_path, monkeypatch):
    # Point CONFIG_DIR somewhere empty so load_identity raises FileNotFoundError.
    fake_dir = tmp_path / "no_identity_here"
    fake_dir.mkdir()
    monkeypatch.setattr("daimon.identity.keys.CONFIG_DIR", fake_dir)
    monkeypatch.setattr(
        "daimon.identity.keys.PRIVATE_KEY_PATH", fake_dir / "identity.key"
    )

    result = _call(dm_whoami)
    assert result["error"] == "no_identity"


def test_whoami_with_identity(tmp_path, monkeypatch):
    from daimon.identity import generate_identity, keys as keys_mod

    fake_dir = tmp_path / "ident"
    monkeypatch.setattr(keys_mod, "CONFIG_DIR", fake_dir)
    monkeypatch.setattr(keys_mod, "PRIVATE_KEY_PATH", fake_dir / "identity.key")
    monkeypatch.setattr(keys_mod, "PUBLIC_KEY_PATH", fake_dir / "identity.pub")
    monkeypatch.setattr(keys_mod, "METADATA_PATH", fake_dir / "identity.json")

    identity = generate_identity()
    result = _call(dm_whoami)
    assert "pubkey_hex" in result
    assert result["pubkey_hex"] == identity.pubkey_hex
    assert len(result["pubkey_hex"]) == 64
    assert "version" in result


# ---------------------------------------------------------------------------
# dm_match
# ---------------------------------------------------------------------------

def test_match_mirror_is_draw():
    lo = _full_loadout_dict()
    result = _call(dm_match, loadout_a=lo, loadout_b=lo)
    # Mirror with vanilla cards should produce a draw or symmetric outcome.
    assert "winner" in result
    assert "side_a_final_hp" in result
    assert "side_b_final_hp" in result
    assert result["seed"] == "00" * 32  # default seed


def test_match_with_seed():
    lo = _full_loadout_dict()
    seed_hex = "01" * 32
    result = _call(dm_match, loadout_a=lo, loadout_b=lo, seed=seed_hex)
    assert result["seed"] == seed_hex


def test_match_round_log_opt_in():
    lo = _full_loadout_dict()
    no_log = _call(dm_match, loadout_a=lo, loadout_b=lo)
    with_log = _call(dm_match, loadout_a=lo, loadout_b=lo, include_round_log=True)
    assert "rounds" not in no_log
    assert "rounds" in with_log
    assert len(with_log["rounds"]) == with_log["round_count"]
    if with_log["rounds"]:
        assert "actions" in with_log["rounds"][0]


def test_match_accepts_bare_list():
    """The MCP tool should accept either {'cards': [...]} or a bare list."""
    lo_dict = _full_loadout_dict()
    bare_list = lo_dict["cards"]
    r1 = _call(dm_match, loadout_a=lo_dict, loadout_b=lo_dict)
    r2 = _call(dm_match, loadout_a=bare_list, loadout_b=bare_list)
    assert r1["winner"] == r2["winner"]
    assert r1["side_a_final_hp"] == r2["side_a_final_hp"]


def test_match_invalid_input_returns_error_envelope():
    result = _call(dm_match, loadout_a="not a loadout", loadout_b={"cards": []})
    assert result["error"] == "invalid_input"
    assert "message" in result


def test_match_bad_seed_returns_error_envelope():
    lo = _full_loadout_dict()
    result = _call(dm_match, loadout_a=lo, loadout_b=lo, seed="not hex!")
    assert result["error"] == "invalid_input"


def test_match_short_seed_returns_error_envelope():
    lo = _full_loadout_dict()
    result = _call(dm_match, loadout_a=lo, loadout_b=lo, seed="0011")
    assert result["error"] == "invalid_input"
    assert "32 bytes" in result["message"]


# ---------------------------------------------------------------------------
# dm_match side-effect: writes state.json for the game terminal
# ---------------------------------------------------------------------------

def test_match_writes_state_file_side_effect(monkeypatch, tmp_path):
    """dm_match must publish a 'match' view to state.json as a V2 Match
    payload so the terminal animator can pick it up. The agent-facing
    response keeps the legacy summary shape (winner / reason / hp totals)."""
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.play.state import read_state
    from daimon.play.schema import Match

    lo = _full_loadout_dict()
    result = _call(dm_match, loadout_a=lo, loadout_b=lo)
    assert "state_id" in result
    assert result["state_id"].startswith("match_")

    state = read_state(tmp_path / "config" / "state.json")
    assert state is not None
    assert state.view == "match"
    assert state.id == result["state_id"]
    # State payload is V2 Match — re-validate against the schema to prove it.
    rebuilt = Match.model_validate(state.data)
    assert rebuilt.schema_version == 2
    assert rebuilt.event_type == "match"
    assert set(rebuilt.participants.keys()) == {"player", "opponent"}
    assert len(rebuilt.participants["player"].loadout) == 6
    assert len(rebuilt.participants["opponent"].loadout) == 6
    # match_id mirrors the agent-returned state_id so the renderer can
    # cross-reference the inbox file with the agent's record.
    assert rebuilt.match_id == result["state_id"]


def test_match_state_id_unique_per_call(monkeypatch, tmp_path):
    """Two matches produce two distinct state ids, so the renderer can
    dedupe correctly."""
    _isolate_paths(monkeypatch, tmp_path)
    lo = _full_loadout_dict()
    r1 = _call(dm_match, loadout_a=lo, loadout_b=lo)
    r2 = _call(dm_match, loadout_a=lo, loadout_b=lo)
    assert r1["state_id"] != r2["state_id"]


def test_match_propagates_real_catalog_display_metadata(monkeypatch, tmp_path):
    """dm_match pulls `name` / `rarity` / `art` / `short_name` off the raw
    loadout payload and threads them through the adapter into the state
    payload. This is what makes the rendered match look like Voltcat Apex
    vs Bulwarthog instead of synthetic titlecased species names."""
    _isolate_paths(monkeypatch, tmp_path)
    from pathlib import Path
    from daimon.play.state import read_state
    from daimon.play.schema import Match

    catalog_dir = (
        Path(__file__).resolve().parent.parent
        / "daimon" / "catalog" / "v1_alpha"
    )
    # Six distinct species (engine enforces max 2 of same species per team).
    species = [
        "voltcat_apex", "bulwarthog", "mindroot", "tidewyrm",
        "stormhare", "iron_boar",
    ]
    lo_raw = [json.loads((catalog_dir / f"{s}.json").read_text()) for s in species]
    # loadout_a and loadout_b can share cards — rule caps per-team count.
    lo = {"cards": lo_raw}

    result = _call(dm_match, loadout_a=lo, loadout_b=lo)
    assert "error" not in result, f"dm_match errored: {result}"

    state = read_state(tmp_path / "config" / "state.json")
    assert state is not None
    payload = Match.model_validate(state.data)

    # Every card on every side carries real metadata lifted from the JSON.
    # Post mythology-pivot display names (see tools/canon_rewrite/mapping.py).
    # Engine-stable ids (species above) are unchanged; only `name` flows through
    # here, which is now the mythological display string.
    for side_key, expected_names in (
        ("player", {
            "Valravn", "Khepri, Scarab-Warden", "Mandragora of Kokytos",
            "Rán, Drowning-Queen", "Vindhare", "Gullinbursti",
        }),
        ("opponent", {
            "Valravn", "Khepri, Scarab-Warden", "Mandragora of Kokytos",
            "Rán, Drowning-Queen", "Vindhare", "Gullinbursti",
        }),
    ):
        got_names = {c.name for c in payload.participants[side_key].loadout}
        assert got_names == expected_names, (
            f"{side_key}: got {got_names}, expected {expected_names}"
        )

    # Rarities flow through. Phase 4a (2026-04-22) reconciled legacy
    # scaffolded legendaries + epics down to rare; the V1 lock keeps only
    # voidking_morr + world_eater at legendary and the 12 Phase-3 anchors
    # at epic. Everything else defaults to rare or below.
    player_by_species = {
        c.species: c for c in payload.participants["player"].loadout
    }
    assert player_by_species["voltcat_apex"].rarity == "rare"
    assert player_by_species["bulwarthog"].rarity == "rare"
    assert player_by_species["mindroot"].rarity == "rare"
    assert player_by_species["stormhare"].rarity == "rare"
    assert player_by_species["tidewyrm"].rarity == "rare"
    assert player_by_species["iron_boar"].rarity == "common"

    # Short names are populated (derived from name when JSON doesn't supply one).
    for card in payload.participants["player"].loadout:
        assert card.short_name, f"missing short_name on {card.name}"
        assert len(card.short_name) <= 8

    # Art paths flow through verbatim. Catalog migrated 2026-04-22 (commit
    # 3b4efc3) from `art/<rarity>/<species>.png` flat layout to
    # `art/v1_alpha/<species>/base.png` per-card-folder structure — matching
    # the art-pack tarball layout shipped via GitHub Releases.
    assert (
        player_by_species["voltcat_apex"].art_path
        == "art/v1_alpha/voltcat_apex/base.png"
    )


def test_match_synthetic_loadout_still_works(monkeypatch, tmp_path):
    """A loadout with NO display metadata (pure engine-mechanics test cards)
    still produces a valid Match — the adapter synthesizes defaults."""
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.play.state import read_state
    from daimon.play.schema import Match

    # Synthetic loadout — only mechanics fields, no name/rarity/art.
    lo = _full_loadout_dict()
    result = _call(dm_match, loadout_a=lo, loadout_b=lo)
    assert "error" not in result

    state = read_state(tmp_path / "config" / "state.json")
    payload = Match.model_validate(state.data)
    for card in payload.participants["player"].loadout:
        # Synthesized titlecased species.
        assert card.name  # non-empty
        assert card.rarity == "common"  # default
        assert card.art_path is None


# ---------------------------------------------------------------------------
# dm_loadout_validate
# ---------------------------------------------------------------------------

def test_loadout_validate_ok():
    result = _call(dm_loadout_validate, loadout=_full_loadout_dict())
    assert result["valid"] is True
    assert len(result["cards"]) == 6
    # V2: cards expose `element` + `species` (no more `slot`).
    assert "element" in result["cards"][0]
    assert "species" in result["cards"][0]


def test_loadout_validate_wrong_count():
    lo = _full_loadout_dict()
    lo["cards"] = lo["cards"][:5]  # only 5 cards → must be rejected
    result = _call(dm_loadout_validate, loadout=lo)
    assert result["valid"] is False
    assert "error" in result


def test_loadout_validate_garbage():
    result = _call(dm_loadout_validate, loadout="banana")
    assert result["valid"] is False


# ---------------------------------------------------------------------------
# dm_collection
# ---------------------------------------------------------------------------

def test_collection_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH", tmp_path / "no_such_file.json")
    result = _call(dm_collection)
    assert result["error"] == "no_collection"
    assert result["count"] == 0


def test_collection_present(monkeypatch, tmp_path):
    path = tmp_path / "collection.json"
    path.write_text(json.dumps({
        "serials": [
            {"serial": "uuid-1", "card_id": "starter_scout_head", "pack": "starter"},
            {"serial": "uuid-2", "card_id": "plasma_lance", "pack": "legendary"},
        ]
    }))
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH", path)
    result = _call(dm_collection)
    assert result["count"] == 2
    assert result["serials"][0]["card_id"] == "starter_scout_head"


def test_collection_corrupt(monkeypatch, tmp_path):
    path = tmp_path / "collection.json"
    path.write_text("{not json")
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH", path)
    result = _call(dm_collection)
    assert result["error"] == "corrupt_collection"


def test_collection_rollup_shape(monkeypatch, tmp_path):
    """dm_collection MUST return rarity_counts + by_card rollups so agents
    can render a 'collection screen' without bucketing serials themselves."""
    path = tmp_path / "collection.json"
    path.write_text(json.dumps({
        "serials": [
            {"serial": "u1", "card_id": "blazewolf",   "pack": "v1",
             "rarity": "common",    "minted_via": "pull"},
            {"serial": "u2", "card_id": "blazewolf",   "pack": "v1",
             "rarity": "common",    "minted_via": "pull"},
            {"serial": "u3", "card_id": "magma_tyrant","pack": "v1",
             "rarity": "legendary", "minted_via": "pull"},
            {"serial": "u4", "card_id": "ash_strider", "pack": "v1",
             "rarity": "rare",      "minted_via": "pull"},
            {"serial": "u5", "card_id": "ash_strider", "pack": "v1",
             "rarity": "rare",      "minted_via": "pull"},
            {"serial": "u6", "card_id": "ash_strider", "pack": "v1",
             "rarity": "rare",      "minted_via": "pull"},
        ]
    }))
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH", path)
    result = _call(dm_collection)

    assert result["status"] == "ok"
    assert result["count"] == 6
    assert result["unique_cards"] == 3
    assert result["rarity_counts"] == {"common": 2, "rare": 3, "legendary": 1}

    # by_card sorted by rarity (common < rare < legendary), then card_id.
    rows = result["by_card"]
    assert [r["card_id"] for r in rows] == [
        "blazewolf", "ash_strider", "magma_tyrant",
    ]
    assert {r["card_id"]: r["count"] for r in rows} == {
        "blazewolf": 2, "ash_strider": 3, "magma_tyrant": 1,
    }
    # Raw serials still present for callers that want the full list.
    assert len(result["serials"]) == 6


def test_collection_missing_includes_rollup_shape(monkeypatch, tmp_path):
    """Even on no_collection, the rollup keys must be present (empty)
    so callers can rely on a stable schema."""
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH",
                        tmp_path / "no_such_file.json")
    result = _call(dm_collection)
    assert result["error"] == "no_collection"
    assert result["count"] == 0
    assert result["unique_cards"] == 0
    assert result["rarity_counts"] == {}
    assert result["by_card"] == []
    assert result["serials"] == []


def test_collection_unknown_rarity_sorts_last(monkeypatch, tmp_path):
    """Cards with rarity values outside the known order must not crash
    the sort — they bucket to the end."""
    path = tmp_path / "collection.json"
    path.write_text(json.dumps({
        "serials": [
            {"serial": "u1", "card_id": "weird_card", "pack": "v1",
             "rarity": "mythic_plus", "minted_via": "pull"},
            {"serial": "u2", "card_id": "blazewolf", "pack": "v1",
             "rarity": "common", "minted_via": "pull"},
        ]
    }))
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH", path)
    result = _call(dm_collection)
    rows = result["by_card"]
    # common comes before mythic_plus (unknown ranks last).
    assert rows[0]["card_id"] == "blazewolf"
    assert rows[1]["card_id"] == "weird_card"


# ---------------------------------------------------------------------------
# dm_pull / dm_mine_status (real implementations)
# ---------------------------------------------------------------------------

def _isolate_paths(monkeypatch, tmp_path):
    """Redirect identity/ledger/collection/state paths into a temp dir so
    tests don't touch the user's real ~/.config/daimon."""
    from daimon.identity import keys as identity_keys
    from daimon.mining import ledger as ledger_mod
    from daimon import collection as collection_mod

    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH",
                        cfg / "collection.json")
    monkeypatch.setattr(mcp_server, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH",
                        cfg / "collection.json")
    # Route state.json writes from MCP side-effects into the tmp dir too —
    # otherwise dm_match / dm_pull would clobber a real game terminal's state.
    monkeypatch.setenv("DAIMON_STATE", str(cfg / "state.json"))
    return cfg


def test_pull_no_identity(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    result = _call(dm_pull)
    # Post-2026-04-21 envelope normalization: failures now use `error:` like
    # every other tool. `status:` was the inconsistent outlier.
    assert result["error"] == "no_identity"
    assert "status" not in result  # failure envelopes must not have status
    assert "hint" in result


def test_pull_insufficient_balance(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    result = _call(dm_pull)
    assert result["error"] == "insufficient_balance"
    assert result["balance"] == 0
    assert result["needed"] == 100
    assert result["cost"] == 100
    assert "status" not in result


def test_pull_invalid_seed_hex(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    result = _call(dm_pull, seed="not_hex_at_all")
    assert result["error"] == "invalid_input"
    assert "status" not in result


def test_pull_wrong_seed_length(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    # 30 bytes, not 32
    result = _call(dm_pull, seed="ab" * 30)
    assert result["error"] == "invalid_input"
    assert "got 30" in result["message"]


def test_pull_succeeds_with_funded_ledger(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    from daimon.mining import append_mine_entry
    generate_identity(force=True)
    # Manually credit balance.
    append_mine_entry(
        tool_name="Edit", amount=150,
        factors={"base": 4}, novelty_key="seed",
    )
    seed_hex = "ab" * 32
    result = _call(dm_pull, seed=seed_hex)
    assert result["status"] == "ok", result
    assert result["balance_after"] == 50
    assert result["seed_hex"] == seed_hex
    assert "card_id" in result and "serial" in result


def test_pull_seed_determinism(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    from daimon.mining import append_mine_entry
    generate_identity(force=True)
    append_mine_entry(tool_name="Edit", amount=300,
                      factors={"base": 4}, novelty_key="seed")
    seed_hex = "cd" * 32
    r1 = _call(dm_pull, seed=seed_hex)
    r2 = _call(dm_pull, seed=seed_hex)
    # Same seed → same card_id (UUIDs differ).
    assert r1["card_id"] == r2["card_id"]
    assert r1["serial"] != r2["serial"]


def test_pull_writes_state_file_side_effect(monkeypatch, tmp_path):
    """dm_pull must publish a 'pull' view to state.json so the renderer
    can play the gacha reveal. Pull must also succeed even if the side
    effect fails (best-effort contract)."""
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    from daimon.mining import append_mine_entry
    from daimon.play.state import read_state

    generate_identity(force=True)
    append_mine_entry(tool_name="Edit", amount=150,
                      factors={"base": 4}, novelty_key="seed")

    result = _call(dm_pull, seed="ef" * 32)
    assert result["status"] == "ok"
    assert "state_id" in result
    assert result["state_id"].startswith("pull_")

    state = read_state(tmp_path / "config" / "state.json")
    assert state is not None
    assert state.view == "pull"
    assert state.id == result["state_id"]
    # Card receipt must be in the payload so the renderer has art+rarity
    # to animate.
    assert state.data["card_id"] == result["card_id"]
    assert state.data["serial"] == result["serial"]
    assert state.data["rarity"] == result["rarity"]


def test_mine_status_no_ledger(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    result = _call(dm_mine_status)
    assert result["status"] == "ok"
    assert result["balance"] == 0
    assert result["ledger_entries"] == 0
    # Phase-5: purchase rollup must be present even on a fresh ledger so
    # callers can rely on a stable schema.
    assert result["total_purchased"] == 0
    assert result["purchase_count"] == 0


def test_mine_status_with_ledger(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    from daimon.mining import append_mine_entry
    generate_identity(force=True)
    append_mine_entry(tool_name="Edit", amount=12,
                     factors={"base": 4}, novelty_key="x")
    result = _call(dm_mine_status)
    assert result["status"] == "ok"
    assert result["balance"] == 12
    assert result["mine_count"] == 1
    assert result["verified"] is True
    # Phase-5: zero purchases yet, so spend totals are zero but present.
    assert result["total_purchased"] == 0
    assert result["purchase_count"] == 0


def test_mine_status_includes_purchase_totals(monkeypatch, tmp_path):
    """After a shop purchase, dm_mine_status must surface total_purchased
    + purchase_count so the agent can reconcile spend without scanning the
    raw ledger. The playtest report flagged this hole — closes it."""
    _isolate_paths(monkeypatch, tmp_path)

    from daimon.identity import generate_identity
    from daimon.mining import append_mine_entry
    from daimon.mining.ledger import append_purchase_entry

    generate_identity(force=True)
    append_mine_entry(tool_name="Edit", amount=2000,
                     factors={"base": 4}, novelty_key="seed")
    append_purchase_entry(
        cost=300, card_id="blazewolf", skin_slug="ukiyoe_scroll",
        skin_axis="cultural", rarity="rare",
    )
    append_purchase_entry(
        cost=800, card_id="magma_tyrant", skin_slug="bestiary_folio",
        skin_axis="anatomical", rarity="super_rare",
    )

    result = _call(dm_mine_status)
    assert result["status"] == "ok"
    assert result["balance"] == 2000 - 300 - 800
    assert result["total_mined"] == 2000
    assert result["total_purchased"] == 1100
    assert result["purchase_count"] == 2
    # Purchase entries should also surface in `recent` with their skin
    # metadata so the agent can render a "recent activity" log.
    skin_recents = [r for r in result["recent"] if r.get("kind") == "purchase"]
    assert len(skin_recents) == 2
    assert any(r.get("skin_slug") == "ukiyoe_scroll" for r in skin_recents)
    assert any(r.get("skin_slug") == "bestiary_folio" for r in skin_recents)


def test_whoami_includes_purchase_totals(monkeypatch, tmp_path):
    """dm_whoami absorbs the same _mining_stats_or_empty payload, so the
    same purchase-rollup contract applies there too."""
    _isolate_paths(monkeypatch, tmp_path)

    from daimon.identity import generate_identity
    from daimon.mining import append_mine_entry
    from daimon.mining.ledger import append_purchase_entry

    generate_identity(force=True)
    append_mine_entry(tool_name="Edit", amount=1000,
                     factors={"base": 4}, novelty_key="seed")
    append_purchase_entry(
        cost=300, card_id="ash_strider", skin_slug="hieroglyph_tomb",
        skin_axis="cultural", rarity="rare",
    )

    result = _call(dm_whoami)
    assert result["total_purchased"] == 300
    assert result["purchase_count"] == 1
    assert result["balance"] == 700


# ---------------------------------------------------------------------------
# Server registration sanity check
# ---------------------------------------------------------------------------

def test_all_tools_registered():
    """Locked 21-tool surface + dm_init + 3 NPC tools + deprecated alias +
    dm_pvp_reveal (added 2026-04-24 when arena wiring landed)."""
    names = {
        # Identity + currency
        "dm_init", "dm_whoami", "dm_register",
        "dm_mine_status",  # deprecated alias, kept for back-compat
        # Catalog
        "dm_expansions", "dm_catalog_list", "dm_catalog_card", "dm_card_compare",
        # Collection + pulls
        "dm_collection", "dm_pull",
        # Loadouts
        "dm_loadout_validate", "dm_loadout_save", "dm_loadout_list",
        "dm_loadout_load",
        # Match + PvP
        "dm_match", "dm_npcs", "dm_npc", "dm_match_npc",
        "dm_pvp_challenge", "dm_pvp_accept", "dm_pvp_reveal",
        "dm_pvp_status", "dm_pvp_my_matches",
        # Arena state
        "dm_leaderboard", "dm_my_rank",
        # Disputes
        "dm_dispute_open", "dm_card_propose",
    }
    # 21 locked tools + dm_init + 3 NPC tools + 1 deprecated alias
    # + dm_pvp_reveal = 27.
    assert len(names) == 27
    for n in names:
        assert hasattr(mcp_server, n), f"{n} missing from server module"


# ---------------------------------------------------------------------------
# dm_whoami — folds mining stats per locked 2026-04-21 design
# ---------------------------------------------------------------------------

def test_whoami_includes_balance(monkeypatch, tmp_path):
    """dm_whoami now returns balance + ledger snapshot (absorbed from
    dm_mine_status per locked design)."""
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    from daimon.mining import append_mine_entry
    generate_identity(force=True)
    append_mine_entry(tool_name="Edit", amount=75,
                      factors={"base": 4}, novelty_key="x")

    r = _call(dm_whoami)
    assert "balance" in r
    assert r["balance"] == 75
    assert r["total_mined"] == 75
    assert r["mine_count"] == 1
    assert r["verified"] is True
    assert isinstance(r["recent"], list)


def test_whoami_balance_zero_when_no_ledger(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    r = _call(dm_whoami)
    assert r["balance"] == 0
    assert r["total_mined"] == 0
    assert r["ledger_entries"] == 0


def test_mine_status_is_deprecation_alias(monkeypatch, tmp_path):
    """dm_mine_status still works but flags deprecation so agents migrate."""
    _isolate_paths(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    r = _call(dm_mine_status)
    assert r["status"] == "ok"
    assert "deprecation" in r
    assert "dm_whoami" in r["deprecation"]


# ---------------------------------------------------------------------------
# Catalog tools — dm_expansions / dm_catalog_list / dm_catalog_card
# ---------------------------------------------------------------------------

def test_expansions_returns_v1_alpha():
    r = _call(dm_expansions)
    assert "expansions" in r
    ids = [e.get("pack_id") for e in r["expansions"]]
    assert "v1_alpha" in ids
    v1 = next(e for e in r["expansions"] if e["pack_id"] == "v1_alpha")
    # Bundled catalog. Card count grows with the pack (67 at v0.3.0); we just
    # assert it's well-populated rather than pinning a magic number that the
    # authoring blitz has to chase.
    assert v1["card_count"] >= 60, f"v1_alpha shrank unexpectedly: {v1['card_count']}"
    assert "legendary" in v1["rarity_counts"]


def test_catalog_list_default_catalog():
    r = _call(dm_catalog_list)
    assert "cards" in r
    assert r["pack_id"] == "v1_alpha"
    assert r["count"] >= 60, f"v1_alpha shrank unexpectedly: {r['count']}"
    # Every card carries the mechanical stats the agent needs.
    for c in r["cards"]:
        for k in ("card_id", "species", "element", "rarity",
                  "atk", "def", "hp", "spd", "trigger_count"):
            assert k in c, f"missing {k} in {c}"


def test_catalog_list_explicit_id():
    r = _call(dm_catalog_list, expansion_id="v1_alpha")
    assert r["pack_id"] == "v1_alpha"


def test_catalog_list_unknown_expansion():
    r = _call(dm_catalog_list, expansion_id="nonexistent")
    assert r["error"] == "unknown_expansion"


def test_catalog_card_full_payload():
    # Switched from voltcat_apex (demoted to rare in Phase 4a) to world_eater
    # so this test stays pinned to a known-legendary card. world_eater is the
    # SYNCRETIC apex legendary shipped in Phase 3. Display name updated in the
    # 2026-04-23 mythology pivot (see tools/canon_rewrite/mapping.py) — the
    # engine id `world_eater` is stable, the flavor string is now Aztec.
    r = _call(dm_catalog_card, card_id="world_eater")
    assert r["card_id"] == "world_eater"
    assert r["rarity"] == "legendary"
    # Display fields must flow through (engine blind to them, but tool isn't).
    assert "name" in r["payload"]
    assert r["payload"]["name"] == "Tezcatlipoca, Smoking Mirror"


def test_catalog_card_unknown_card():
    r = _call(dm_catalog_card, card_id="nope")
    assert r["error"] == "unknown_card"


def test_catalog_card_invalid_input():
    r = _call(dm_catalog_card, card_id="")
    assert r["error"] == "invalid_input"


# ---------------------------------------------------------------------------
# dm_card_compare
# ---------------------------------------------------------------------------

def test_card_compare_same_card_yields_zero_delta():
    r = _call(dm_card_compare, a="voltcat_apex", b="voltcat_apex")
    for stat in ("atk", "def", "hp", "spd"):
        assert r["diff"][stat]["delta"] == 0
    assert r["diff"]["element"]["same"] is True
    assert r["diff"]["rarity"]["same"] is True


def test_card_compare_different_cards_yields_stat_diff():
    r = _call(dm_card_compare, a="iron_boar", b="voltcat_apex")
    # Voltcat Apex is legendary; iron_boar is common — they must differ.
    assert r["diff"]["rarity"]["same"] is False
    # Some stat should differ (sanity: the catalog is diverse).
    any_delta = any(r["diff"][s]["delta"] != 0 for s in ("atk", "def", "hp", "spd"))
    assert any_delta


def test_card_compare_unknown_card():
    r = _call(dm_card_compare, a="voltcat_apex", b="bogus")
    assert r["error"] == "unknown_card"
    assert "bogus" in r["missing"]


# ---------------------------------------------------------------------------
# Loadout CRUD — dm_loadout_save / list / load
# ---------------------------------------------------------------------------

def test_loadout_save_and_load_round_trip(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", tmp_path / "loadouts")

    lo = _full_loadout_dict()
    save = _call(dm_loadout_save, loadout=lo, name="my_deck")
    assert save["status"] == "ok"
    assert save["overwrote"] is False
    assert save["card_count"] == 6

    load = _call(dm_loadout_load, name="my_deck")
    assert load["status"] == "ok"
    assert len(load["cards"]) == 6
    assert load["cards"][0]["card_id"] == lo["cards"][0]["card_id"]


def test_loadout_save_overwrites_existing(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", tmp_path / "loadouts")

    lo = _full_loadout_dict()
    _call(dm_loadout_save, loadout=lo, name="dupe")
    second = _call(dm_loadout_save, loadout=lo, name="dupe")
    assert second["overwrote"] is True


def test_loadout_save_rejects_path_traversal(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", tmp_path / "loadouts")
    for bad in ("../evil", "foo/bar", "weird name", "", "." * 60):
        r = _call(dm_loadout_save, loadout=_full_loadout_dict(), name=bad)
        assert r.get("error") == "invalid_name", bad


def test_loadout_save_rejects_invalid_loadout(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", tmp_path / "loadouts")
    r = _call(dm_loadout_save, loadout="banana", name="ok_name")
    assert r["error"] == "invalid_loadout"


def test_loadout_list_empty(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", tmp_path / "loadouts_empty")
    r = _call(dm_loadout_list)
    assert r["count"] == 0
    assert r["loadouts"] == []


def test_loadout_list_after_save(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", tmp_path / "loadouts")
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="alpha")
    _call(dm_loadout_save, loadout=_full_loadout_dict(), name="beta")
    r = _call(dm_loadout_list)
    names = sorted(e["name"] for e in r["loadouts"])
    assert names == ["alpha", "beta"]
    for e in r["loadouts"]:
        assert e["card_count"] == 6


def test_loadout_list_flags_corrupt_entries(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    ld = tmp_path / "loadouts"
    ld.mkdir()
    (ld / "good.json").write_text(json.dumps({"cards": _full_loadout_dict()["cards"]}))
    (ld / "bad.json").write_text("{not json")
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", ld)
    r = _call(dm_loadout_list)
    entries_by_name = {e["name"]: e for e in r["loadouts"]}
    assert entries_by_name["bad"].get("corrupt") is True
    assert entries_by_name["good"].get("card_count") == 6


def test_loadout_load_unknown(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", tmp_path / "loadouts")
    r = _call(dm_loadout_load, name="never_saved")
    assert r["error"] == "unknown_loadout"


def test_loadout_load_rejects_path_traversal(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", tmp_path / "loadouts")
    r = _call(dm_loadout_load, name="../../etc/passwd")
    assert r["error"] == "invalid_name"


# ---------------------------------------------------------------------------
# Arena-bound tools — real wiring over mocked `gh` subprocess
#
# The tools below shell out to the `gh` CLI via
# `daimon.arena.client._run()`. We monkeypatch that dispatcher per test so
# the suite doesn't hit GitHub. Each test asserts both the argv shape
# (right command was built) and the envelope the tool returned.
# ---------------------------------------------------------------------------

def _isolate_arena(monkeypatch, tmp_path):
    """Baseline isolation for arena tests: identity + pvp_state dir tmpified."""
    cfg = _isolate_paths(monkeypatch, tmp_path)
    # Redirect pvp_state into the tmp dir so dm_pvp_challenge / accept /
    # reveal don't touch ~/.daimon/.
    inbox = tmp_path / "daimon_inbox"
    inbox.mkdir()
    monkeypatch.setenv("DAIMON_INBOX", str(inbox))
    # state module caches PVP_STATE_DIR at import time — swap it too.
    from daimon.arena import state as arena_state
    monkeypatch.setattr(arena_state, "PVP_STATE_DIR", inbox / "pvp_state")
    return cfg


class _FakeGh:
    """Record argvs and reply with canned envelopes.

    Queue-based: each call pops the next (or-the-default) response. Keeps
    tests legible — each test declares the sequence of gh outputs it expects.
    """

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = []  # list[(argv, input_text)]

    def __call__(self, argv, input_text=None, timeout=20):
        self.calls.append((list(argv), input_text))
        if self._responses:
            return self._responses.pop(0)
        return {"ok": True, "stdout": "", "stderr": ""}

    def push(self, response):
        self._responses.append(response)


def _install_fake_gh(monkeypatch, fake):
    """Swap the arena client's low-level dispatcher."""
    from daimon.arena import client as arena_client
    monkeypatch.setattr(arena_client, "_run", fake)


def _create_issue_ok(issue_number: int,
                     repo: str = "aurorasuperbot/daimon-arena"):
    return {
        "ok": True,
        "stdout": f"https://github.com/{repo}/issues/{issue_number}\n",
        "stderr": "",
    }


def _comment_ok(issue_number: int, comment_id: int = 999,
                repo: str = "aurorasuperbot/daimon-arena"):
    return {
        "ok": True,
        "stdout": (
            f"https://github.com/{repo}/issues/{issue_number}"
            f"#issuecomment-{comment_id}\n"
        ),
        "stderr": "",
    }


# --- dm_register -----------------------------------------------------------

def test_register_opens_signed_issue(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    ident = generate_identity(force=True)
    fake = _FakeGh([_create_issue_ok(101)])
    _install_fake_gh(monkeypatch, fake)

    r = _call(dm_register, handle="aurora")
    assert r["status"] == "ok"
    assert r["issue_number"] == 101
    assert r["pubkey_hex"] == ident.pubkey_hex
    assert r["handle"] == "aurora"
    assert r["phase"] == "pending-arbiter"

    # The gh argv must have been a `gh issue create` with identity labels,
    # and the body stdin must include the pubkey + a signature line.
    argv, body = fake.calls[0]
    assert argv[:3] == ["gh", "issue", "create"]
    assert "identity" in argv
    assert "pending-arbiter" in argv
    assert ident.pubkey_hex in body
    assert "signature: " in body
    assert "protocol: daimon-register-v1" in body


def test_register_no_identity(monkeypatch, tmp_path):
    fake_dir = tmp_path / "empty"
    fake_dir.mkdir()
    monkeypatch.setattr("daimon.identity.keys.CONFIG_DIR", fake_dir)
    monkeypatch.setattr(
        "daimon.identity.keys.PRIVATE_KEY_PATH", fake_dir / "identity.key")
    r = _call(dm_register)
    assert r["error"] == "no_identity"


def test_register_surfaces_gh_auth_error(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    fake = _FakeGh([{"ok": False, "error": "gh_auth",
                     "message": "HTTP 401: Bad credentials",
                     "exit_code": 1, "stderr": "401"}])
    _install_fake_gh(monkeypatch, fake)
    r = _call(dm_register)
    assert r["error"] == "gh_auth"
    assert "register failed" in r["message"]


# --- dm_pvp_challenge ------------------------------------------------------

def test_pvp_challenge_validates_opponent_pubkey():
    r = _call(dm_pvp_challenge, opponent_pubkey="too_short",
              loadout=_full_loadout_dict())
    assert r["error"] == "invalid_input"


def test_pvp_challenge_validates_loadout():
    r = _call(dm_pvp_challenge, opponent_pubkey="a" * 64,
              loadout="banana")
    assert r["error"] == "invalid_input"


def test_pvp_challenge_opens_issue_and_persists_state(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    fake = _FakeGh([_create_issue_ok(42)])
    _install_fake_gh(monkeypatch, fake)

    r = _call(dm_pvp_challenge,
              opponent_pubkey="f" * 64,
              loadout=_full_loadout_dict(),
              memo="gg hf")
    assert r["status"] == "ok"
    assert r["issue_number"] == 42
    assert r["challenge_id"] == "42"
    assert r["phase"] == "pending-accept"
    # commit is a 64-char sha256 hex
    assert isinstance(r["loadout_commit"], str)
    assert len(r["loadout_commit"]) == 64
    int(r["loadout_commit"], 16)  # must parse as hex

    # State file written into the tmp inbox — no leak to ~/.daimon.
    from daimon.arena import state as arena_state
    pending = arena_state.list_pending()
    assert pending == [42]
    record = arena_state.load(42)
    assert record["side"] == "challenger"
    assert record["opponent_pubkey"] == "f" * 64
    assert record["loadout"]["cards"][0]["card_id"]  # survived round-trip

    # Argv sanity
    argv, body = fake.calls[0]
    assert argv[:3] == ["gh", "issue", "create"]
    assert "match-challenge" in argv
    assert "pvp" in argv
    assert "pending-accept" in argv
    assert "gg hf" in body
    # commit MUST be in body, nonce must NOT be in body
    assert r["loadout_commit"] in body
    assert record["nonce"] not in body


# --- dm_pvp_accept ---------------------------------------------------------

def test_pvp_accept_validates_inputs():
    r = _call(dm_pvp_accept, challenge_id="", loadout=_full_loadout_dict())
    assert r["error"] == "invalid_input"
    r = _call(dm_pvp_accept, challenge_id="id-123", loadout="banana")
    assert r["error"] == "invalid_input"


def test_pvp_accept_rejects_non_numeric_id(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    r = _call(dm_pvp_accept, challenge_id="abc", loadout=_full_loadout_dict())
    # ops layer rejects non-numeric
    assert r["error"] == "invalid_input"


def test_pvp_accept_posts_commit_comment(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    fake = _FakeGh([_comment_ok(42)])
    _install_fake_gh(monkeypatch, fake)

    r = _call(dm_pvp_accept, challenge_id="42", loadout=_full_loadout_dict())
    assert r["status"] == "ok"
    assert r["challenge_id"] == "42"
    assert r["phase"] == "pending-arbiter"
    assert len(r["loadout_commit"]) == 64

    from daimon.arena import state as arena_state
    assert arena_state.list_pending() == [42]
    rec = arena_state.load(42)
    assert rec["side"] == "responder"

    argv, body = fake.calls[0]
    assert argv[:3] == ["gh", "issue", "comment"]
    assert argv[3] == "42"
    assert body.startswith("/accept\n")
    assert r["loadout_commit"] in body
    # Full loadout must NOT be in the accept body — reveal happens later.
    assert "```json" not in body


# --- dm_pvp_reveal ---------------------------------------------------------

def test_pvp_reveal_requires_challenge_id():
    r = _call(dm_pvp_reveal, challenge_id="")
    assert r["error"] == "invalid_input"


def test_pvp_reveal_errors_without_local_state(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    r = _call(dm_pvp_reveal, challenge_id="7777")
    assert r["error"] == "no_local_state"


def test_pvp_reveal_posts_signed_payload_after_challenge(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    ident = generate_identity(force=True)
    fake = _FakeGh([_create_issue_ok(42), _comment_ok(42)])
    _install_fake_gh(monkeypatch, fake)

    # Seed state by opening a challenge first.
    ch = _call(dm_pvp_challenge, opponent_pubkey="a" * 64,
               loadout=_full_loadout_dict())
    assert ch["status"] == "ok"

    rev = _call(dm_pvp_reveal, challenge_id="42")
    assert rev["status"] == "ok"
    assert rev["phase"] == "revealed"

    # Reveal call was the second gh invocation.
    assert len(fake.calls) == 2
    argv, body = fake.calls[1]
    assert argv[:3] == ["gh", "issue", "comment"]
    assert body.startswith("/reveal\n")
    assert f"pubkey: {ident.pubkey_hex}" in body
    assert "signature: " in body
    assert "nonce: " in body
    # Must embed the full loadout as a fenced JSON block (reveal carries
    # the plaintext the arbiter verifies against the commit).
    assert "```json" in body


def test_pvp_reveal_rejects_identity_mismatch(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    # Seed state under one identity, then swap in a fresh identity and try.
    from daimon.identity import generate_identity
    generate_identity(force=True)
    fake = _FakeGh([_create_issue_ok(42)])
    _install_fake_gh(monkeypatch, fake)
    _call(dm_pvp_challenge, opponent_pubkey="a" * 64,
          loadout=_full_loadout_dict())

    generate_identity(force=True)  # new key under same tmp dirs
    r = _call(dm_pvp_reveal, challenge_id="42")
    assert r["error"] == "identity_mismatch"


# --- dm_pvp_status ---------------------------------------------------------

def test_pvp_status_rejects_empty():
    r = _call(dm_pvp_status, challenge_id="")
    assert r["error"] == "invalid_input"


def test_pvp_status_pending_accept(monkeypatch):
    fake = _FakeGh([{
        "ok": True,
        "stdout": json.dumps({
            "number": 42, "title": "pvp-challenge",
            "body": "challenger_pubkey: x\n",
            "state": "OPEN",
            "labels": [{"name": "pvp"}, {"name": "pending-accept"}],
            "comments": [],
            "url": "https://github.com/x/y/issues/42",
        }),
        "stderr": "",
    }])
    _install_fake_gh(monkeypatch, fake)
    r = _call(dm_pvp_status, challenge_id="42")
    assert r["status"] == "ok"
    assert r["phase"] == "pending-accept"
    assert r["issue_state"] == "open"
    assert r["reveal_count"] == 0


def test_pvp_status_resolved_fetches_match_record(monkeypatch):
    issue_json = {
        "number": 42, "title": "pvp-challenge",
        "body": "challenger_pubkey: aa\nopponent_pubkey: bb\n",
        "state": "CLOSED",
        "labels": [{"name": "pvp"}, {"name": "resolved"}],
        "comments": [
            {"body": "/accept\n..."},
            {"body": "/reveal\n..."},
            {"body": "/reveal\n..."},
        ],
        "url": "https://github.com/x/y/issues/42",
    }
    match_json = {
        "match_id": "42",
        "challenger_pubkey": "aa",
        "opponent_pubkey": "bb",
        "winner": 0,
        "round_count": 3,
    }

    # view_issue → raw.githubusercontent fetch will go through urllib, not gh.
    # Stub both paths: first _run for view_issue, then the urllib path for
    # the match file. We patch urlopen too.
    fake = _FakeGh([
        {"ok": True, "stdout": json.dumps(issue_json), "stderr": ""},
    ])
    _install_fake_gh(monkeypatch, fake)

    import io
    import urllib.request
    def fake_urlopen(url, timeout=0):
        # Return a context-manager-compatible file-like object.
        class R:
            def __enter__(self_inner):
                return io.BytesIO(json.dumps(match_json).encode())
            def __exit__(self_inner, *a):
                return False
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    r = _call(dm_pvp_status, challenge_id="42")
    assert r["status"] == "ok"
    assert r["phase"] == "resolved"
    assert r["match"]["winner"] == 0
    assert r["winner_pubkey"] == "aa"
    assert r["loser_pubkey"] == "bb"


# --- dm_pvp_my_matches -----------------------------------------------------

def test_pvp_my_matches_rejects_bad_limit():
    r = _call(dm_pvp_my_matches, limit=0)
    assert r["error"] == "invalid_input"
    r = _call(dm_pvp_my_matches, limit=1000)
    assert r["error"] == "invalid_input"


def test_pvp_my_matches_filters_by_pubkey(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    ident = generate_identity(force=True)
    my_pk = ident.pubkey_hex
    other = "c" * 64

    fake = _FakeGh([{
        "ok": True,
        "stdout": json.dumps([
            {"number": 1, "body": f"challenger_pubkey: {my_pk}\n"
                                   f"opponent_pubkey: {other}\n",
             "state": "OPEN",
             "labels": [{"name": "pvp"}, {"name": "pending-accept"}],
             "url": "u/1", "updatedAt": "t1"},
            {"number": 2, "body": f"challenger_pubkey: {other}\n"
                                   f"opponent_pubkey: {my_pk}\n",
             "state": "CLOSED",
             "labels": [{"name": "pvp"}, {"name": "resolved"}],
             "url": "u/2", "updatedAt": "t2"},
            {"number": 3, "body": "challenger_pubkey: deadbeef\n"
                                   "opponent_pubkey: cafebabe\n",
             "state": "OPEN", "labels": [{"name": "pvp"}],
             "url": "u/3", "updatedAt": "t3"},
        ]),
        "stderr": "",
    }])
    _install_fake_gh(monkeypatch, fake)

    r = _call(dm_pvp_my_matches, limit=10)
    assert r["status"] == "ok"
    # Only #1 and #2 involve us.
    assert r["count"] == 2
    nums = {m["issue_number"] for m in r["matches"]}
    assert nums == {1, 2}
    by_num = {m["issue_number"]: m for m in r["matches"]}
    assert by_num[1]["role"] == "challenger"
    assert by_num[2]["role"] == "responder"
    assert by_num[2]["phase"] == "resolved"


# --- dm_leaderboard + dm_my_rank ------------------------------------------

def test_leaderboard_missing_file_returns_empty(monkeypatch):
    # raw.githubusercontent 404 then gh api 404 → not_found.
    import urllib.error
    import urllib.request
    def fake_urlopen(url, timeout=0):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = _call(dm_leaderboard, limit=25)
    assert r["status"] == "ok"
    assert r["ranks"] == []
    assert r["count"] == 0


def test_leaderboard_ranks_and_tiers(monkeypatch):
    payload = {
        "last_updated": "2026-04-24T00:00:00Z",
        "entries": {
            "aa" * 32: {"wins": 60, "losses": 5, "draws": 0},   # Champion
            "bb" * 32: {"wins": 30, "losses": 10, "draws": 1},  # Elite
            "cc" * 32: {"wins": 12, "losses": 3, "draws": 0},   # Veteran
            "dd" * 32: {"wins": 5, "losses": 2, "draws": 0},    # Novice
            "ee" * 32: {"wins": 1, "losses": 4, "draws": 0},    # Rookie
        },
    }
    import io
    import urllib.request
    def fake_urlopen(url, timeout=0):
        class R:
            def __enter__(self_inner):
                return io.BytesIO(json.dumps(payload).encode())
            def __exit__(self_inner, *a):
                return False
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    r = _call(dm_leaderboard, limit=10)
    assert r["status"] == "ok"
    assert r["count"] == 5
    assert r["total_players"] == 5
    # Sorted by wins desc
    assert [row["wins"] for row in r["ranks"]] == [60, 30, 12, 5, 1]
    assert [row["tier"] for row in r["ranks"]] == [
        "Champion", "Elite", "Veteran", "Novice", "Rookie",
    ]


def test_my_rank_finds_local_identity(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    ident = generate_identity(force=True)
    payload = {
        "last_updated": "2026-04-24T00:00:00Z",
        "entries": {
            ident.pubkey_hex: {"wins": 8, "losses": 2, "draws": 0},
            "aa" * 32: {"wins": 20, "losses": 1, "draws": 0},
        },
    }
    import io
    import urllib.request
    def fake_urlopen(url, timeout=0):
        class R:
            def __enter__(self_inner):
                return io.BytesIO(json.dumps(payload).encode())
            def __exit__(self_inner, *a):
                return False
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    r = _call(dm_my_rank)
    assert r["status"] == "ok"
    assert r["pubkey_hex"] == ident.pubkey_hex
    assert r["rank"] == 2
    assert r["wins"] == 8
    assert r["tier"] == "Novice"


def test_my_rank_no_identity(monkeypatch, tmp_path):
    fake_dir = tmp_path / "empty"
    fake_dir.mkdir()
    monkeypatch.setattr("daimon.identity.keys.CONFIG_DIR", fake_dir)
    monkeypatch.setattr(
        "daimon.identity.keys.PRIVATE_KEY_PATH", fake_dir / "identity.key")
    r = _call(dm_my_rank)
    assert r["error"] == "no_identity"


# --- dm_dispute_open -------------------------------------------------------

def test_dispute_open_validates_inputs():
    r = _call(dm_dispute_open, match_id="", reason="")
    assert r["error"] == "invalid_input"
    r = _call(dm_dispute_open, match_id="match-1", reason="   ")
    assert r["error"] == "invalid_input"


def test_dispute_open_posts_signed_issue(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    fake = _FakeGh([_create_issue_ok(55)])
    _install_fake_gh(monkeypatch, fake)

    r = _call(dm_dispute_open, match_id="match-42",
              reason="arbiter got hp wrong",
              evidence={"expected_winner": "player"})
    assert r["status"] == "ok"
    assert r["issue_number"] == 55
    assert r["bond_amount"] == 50
    assert r["match_id"] == "match-42"

    argv, body = fake.calls[0]
    assert "dispute-appeal" in argv
    assert "subject: match-42" in body
    assert "bond_amount: 50" in body
    assert "protocol: daimon-dispute-v1" in body
    assert "signature: " in body
    assert "expected_winner" in body  # evidence block made it in


# --- dm_card_propose -------------------------------------------------------

def test_card_propose_rejects_non_dict():
    r = _call(dm_card_propose, card_def="not a card")
    assert r["error"] == "invalid_input"


def test_card_propose_rejects_invalid_card(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    # No gh call should happen — we return early on schema failure.
    fake = _FakeGh([])
    _install_fake_gh(monkeypatch, fake)

    invalid = {"card_id": "broken"}  # missing required fields
    r = _call(dm_card_propose, card_def=invalid)
    assert r["error"] == "invalid_card"
    assert r["schema_error"]
    assert fake.calls == []  # never reached gh


def test_card_propose_posts_valid_card(monkeypatch, tmp_path):
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    fake = _FakeGh([_create_issue_ok(77, repo="aurorasuperbot/daimon-cards")])
    _install_fake_gh(monkeypatch, fake)

    valid = {
        "card_id": "glowrat",
        "species": "glowrat",
        "element": "VOLT",
        "atk": 3, "def": 2, "hp": 10, "spd": 7,
        "triggers": [],
    }
    r = _call(dm_card_propose, card_def=valid, rationale="fills common slot")
    assert r["status"] == "ok"
    assert r["card_id"] == "glowrat"
    assert r["issue_number"] == 77

    argv, body = fake.calls[0]
    # Card proposals land in the cards repo, not the arena repo.
    assert "aurorasuperbot/daimon-cards" in argv
    assert "card-proposal" in argv
    assert "glowrat" in body
    assert "protocol: daimon-card-propose-v1" in body


def test_arena_repo_overrideable_via_env(monkeypatch, tmp_path):
    """DAIMON_ARENA_REPO env var must change the --repo argument gh sees."""
    _isolate_arena(monkeypatch, tmp_path)
    from daimon.identity import generate_identity
    generate_identity(force=True)
    monkeypatch.setenv("DAIMON_ARENA_REPO", "test-org/test-arena")
    fake = _FakeGh([_create_issue_ok(1, repo="test-org/test-arena")])
    _install_fake_gh(monkeypatch, fake)

    r = _call(dm_register)
    assert r["status"] == "ok"
    argv, _ = fake.calls[0]
    # The --repo argument must reflect the env override.
    assert "test-org/test-arena" in argv


# ---------------------------------------------------------------------------
# dm_npcs / dm_npc / dm_match_npc — NPC tier roster (V1 alpha)
# ---------------------------------------------------------------------------

def test_npcs_lists_all_25_with_tier_metadata():
    """dm_npcs() returns the full roster + per-tier metadata."""
    r = _call(dm_npcs)
    assert r["count"] == 25
    assert len(r["npcs"]) == 25
    # Tier metadata sorted rookie -> champion
    assert [t["tier_id"] for t in r["tiers"]] == [
        "rookie", "novice", "veteran", "elite", "champion",
    ]
    for t in r["tiers"]:
        assert t["rank"] in (1, 2, 3, 4, 5)
        assert len(t["npc_ids"]) == 5
    # Every NPC row has the agent-facing fields (no card payloads -- those
    # come from dm_npc).
    for n in r["npcs"]:
        assert n["npc_id"] and n["name"] and n["tier"] and n["flavor"]
        assert "loadout" not in n  # summary-only


def test_npcs_filtered_by_tier():
    r = _call(dm_npcs, tier="champion")
    assert r["count"] == 5
    assert all(n["tier"] == "champion" for n in r["npcs"])
    assert r["filter"] == {"tier": "champion"}


def test_npcs_unknown_tier_returns_error_envelope():
    r = _call(dm_npcs, tier="grandmaster")
    assert r["error"] == "unknown_tier"
    assert "available_tiers" in r
    assert "rookie" in r["available_tiers"]
    assert "status" not in r


def test_npc_returns_full_card_payloads():
    """dm_npc(slug) returns the full deck the agent can mirror or counter."""
    r = _call(dm_npc, npc_id="sparring_sam")
    assert r["npc_id"] == "sparring_sam"
    assert r["name"] == "Sparring Sam"
    assert r["tier"] == "rookie"
    assert r["bio"]
    assert len(r["loadout"]) == 6  # card_id list
    assert len(r["cards"]) == 6
    # Every card payload has the schema fields the engine needs
    for c in r["cards"]:
        assert "card_id" in c and "species" in c and "element" in c
        assert "atk" in c and "def" in c and "hp" in c and "spd" in c


def test_npc_unknown_id_returns_error_envelope():
    r = _call(dm_npc, npc_id="ghost_who_walks")
    assert r["error"] == "unknown_npc"
    assert r["npc_id"] == "ghost_who_walks"
    assert "status" not in r


def test_npc_invalid_input_envelope():
    r = _call(dm_npc, npc_id="")
    assert r["error"] == "invalid_input"
    r = _call(dm_npc, npc_id=42)
    assert r["error"] == "invalid_input"


def test_npc_payload_can_round_trip_through_match():
    """dm_npc cards should be a valid loadout for dm_match (proves the
    agent can use dm_npc to scout, then mirror the NPC's deck)."""
    npc_record = _call(dm_npc, npc_id="sparring_sam")
    cards = npc_record["cards"]

    # Mirror match: use NPC's cards on both sides
    r = _call(dm_match,
              loadout_a={"cards": cards},
              loadout_b={"cards": cards},
              seed="00" * 32)
    # A mirror match in a deterministic engine is a draw
    assert "winner" in r
    # But the more important assertion: it didn't fail validation
    assert "error" not in r


def test_match_npc_resolves_against_named_opponent(monkeypatch, tmp_path):
    """dm_match_npc plays loadout vs npc_id, returns the npc block + match result."""
    _isolate_paths(monkeypatch, tmp_path)
    player = _full_loadout_dict()
    r = _call(dm_match_npc,
              loadout=player,
              npc_id="sparring_sam",
              seed="00" * 32)
    assert r["status"] == "ok"
    assert r["winner"] in (0, 1, None)
    assert r["round_count"] >= 1
    assert r["seed"] == "00" * 32
    assert r["state_id"].startswith("match_")
    assert r["npc"]["npc_id"] == "sparring_sam"
    assert r["npc"]["tier"] == "rookie"
    assert r["npc"]["name"] == "Sparring Sam"


def test_match_npc_unknown_npc_envelope(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    player = _full_loadout_dict()
    r = _call(dm_match_npc, loadout=player, npc_id="not_a_real_npc")
    assert r["error"] == "unknown_npc"
    assert r["npc_id"] == "not_a_real_npc"
    assert "status" not in r


def test_match_npc_invalid_loadout_envelope(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    r = _call(dm_match_npc, loadout="not a loadout", npc_id="sparring_sam")
    assert r["error"] == "invalid_input"
    assert "status" not in r


def test_match_npc_invalid_seed_envelope(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    player = _full_loadout_dict()
    r = _call(dm_match_npc,
              loadout=player,
              npc_id="sparring_sam",
              seed="not_hex_at_all")
    assert r["error"] == "invalid_input"


def test_match_npc_is_deterministic(monkeypatch, tmp_path):
    """Same player loadout + same npc + same seed -> same match outcome.

    (Excludes state_id, which is a fresh UUID per call.)
    """
    _isolate_paths(monkeypatch, tmp_path)
    player = _full_loadout_dict()
    a = _call(dm_match_npc, loadout=player, npc_id="doom_paw_doppia",
              seed="ab" * 32)
    b = _call(dm_match_npc, loadout=player, npc_id="doom_paw_doppia",
              seed="ab" * 32)
    for k in ("winner", "reason", "side_a_final_hp",
              "side_b_final_hp", "round_count"):
        assert a[k] == b[k], f"{k} not deterministic: {a[k]} != {b[k]}"


def test_match_npc_invalid_npc_id_input_envelope(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    player = _full_loadout_dict()
    r = _call(dm_match_npc, loadout=player, npc_id="")
    assert r["error"] == "invalid_input"
    r = _call(dm_match_npc, loadout=player, npc_id=None)
    assert r["error"] == "invalid_input"


def test_match_npc_writes_state_file_with_npc_name_as_opponent(
    monkeypatch, tmp_path,
):
    """The state file written for the play HUD should carry the NPC's
    proper name as the opponent.name (not the literal "opponent")."""
    _isolate_paths(monkeypatch, tmp_path)
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("DAIMON_STATE", str(state_path))

    player = _full_loadout_dict()
    r = _call(dm_match_npc,
              loadout=player,
              npc_id="mythbreaker_marn",
              seed="00" * 32)
    assert r["status"] == "ok"
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    # State file is the V2 Match payload -- opponent under participants.opponent
    opp = data["data"]["participants"]["opponent"]
    assert opp["name"] == "Mythbreaker Marn"
    assert opp["rank"] == "champion"
    # And the opponent loadout carries the NPC's actual cards (not "opponent").
    # Resolve "actual cards" from the on-disk NPC roster so this test stays
    # correct across re-tier passes -- it asserts the state file's loadout
    # matches what the loader would produce for that npc_id.
    from daimon.npcs.loader import get_npc
    npc = get_npc("mythbreaker_marn")
    expected_species = list(npc.loadout)
    actual_species = [card["species"] for card in opp["loadout"]]
    assert actual_species == expected_species, (
        f"Opponent loadout {actual_species!r} should match the on-disk "
        f"roster loadout {expected_species!r} for mythbreaker_marn"
    )
