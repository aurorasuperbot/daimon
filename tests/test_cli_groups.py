"""Tests for the Phase 3 CLI groups: ``daimon collection`` / ``catalog`` /
``loadout``.

These mirror MCP tools that already exist (``dm_collection``,
``dm_catalog_*``, ``dm_loadout_*``) — the tests assert that the CLI surface
produces the same logical results so an end-user typing ``daimon`` doesn't
hit the ~30% capability gap the playtest exposed.

Each command is exercised in both human-readable and ``--json`` mode.
``DAIMON_NO_AUTO_UPDATE=1`` is set everywhere so the ``ensure_art_available``
hook never makes a network call. ``DAIMON_HOME`` (via ``XDG_CONFIG_HOME``)
sandboxes the per-user state to a tmpdir so tests can't see or pollute the
real ``~/.config/daimon``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from daimon.cli import main as cli_main


# ---------------------------------------------------------------------------
# Sandbox fixture — every test gets a fresh CONFIG_DIR.
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_home(monkeypatch, tmp_path: Path) -> Path:
    """Point CONFIG_DIR at a tmpdir + opt out of network calls."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "1")
    # Force re-resolution of CONFIG_DIR — the module caches it at import time.
    import importlib

    from daimon.identity import keys as ikeys
    importlib.reload(ikeys)
    # Reload anything that snapshotted CONFIG_DIR at import time.
    from daimon import collection as col_mod
    importlib.reload(col_mod)
    from daimon.mcp import server as srv
    importlib.reload(srv)
    return tmp_path


def _run(*args: str) -> "click.testing.Result":
    runner = CliRunner()
    return runner.invoke(cli_main, list(args), catch_exceptions=False)


# ---------------------------------------------------------------------------
# `daimon collection`
# ---------------------------------------------------------------------------

class TestCollection:
    def test_empty_collection_human(self, isolated_home: Path):
        r = _run("collection")
        assert r.exit_code == 0, r.output
        assert "empty collection" in r.output.lower()

    def test_empty_collection_json(self, isolated_home: Path):
        r = _run("collection", "--json")
        assert r.exit_code == 0, r.output
        doc = json.loads(r.output)
        assert doc["count"] == 0
        assert doc["serials"] == []
        assert doc["rarity_counts"] == {}

    def test_populated_collection_groups_by_card(self, isolated_home: Path):
        # Seed the collection.json directly to avoid coupling to the pull
        # ledger machinery — we're testing the CLI, not the mint flow.
        config_dir = isolated_home / "daimon"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "collection.json").write_text(json.dumps({
            "pubkey_hex": "deadbeef",
            "serials": [
                {"serial": "uuid-1", "card_id": "magma_tyrant",
                 "pack": "v1_alpha", "rarity": "legendary",
                 "minted_at": "2026-01-01T00:00:00Z", "minted_via": "pull"},
                {"serial": "uuid-2", "card_id": "magma_tyrant",
                 "pack": "v1_alpha", "rarity": "legendary",
                 "minted_at": "2026-01-02T00:00:00Z", "minted_via": "pull"},
                {"serial": "uuid-3", "card_id": "iron_boar",
                 "pack": "v1_alpha", "rarity": "common",
                 "minted_at": "2026-01-03T00:00:00Z", "minted_via": "pull"},
            ],
        }))

        r = _run("collection", "--json")
        assert r.exit_code == 0, r.output
        doc = json.loads(r.output)
        assert doc["count"] == 3
        assert doc["unique_cards"] == 2
        assert doc["rarity_counts"] == {"legendary": 2, "common": 1}

        # Human view groups by card_id and shows x2 for the duplicates.
        r2 = _run("collection")
        assert "magma_tyrant" in r2.output
        assert "x2" in r2.output
        assert "iron_boar" in r2.output

    def test_filter_by_rarity(self, isolated_home: Path):
        config_dir = isolated_home / "daimon"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "collection.json").write_text(json.dumps({
            "serials": [
                {"serial": "x", "card_id": "magma_tyrant", "rarity": "legendary",
                 "pack": "v1_alpha", "minted_at": "x", "minted_via": "pull"},
                {"serial": "y", "card_id": "iron_boar", "rarity": "common",
                 "pack": "v1_alpha", "minted_at": "x", "minted_via": "pull"},
            ],
        }))
        r = _run("collection", "--rarity", "legendary", "--json")
        doc = json.loads(r.output)
        assert doc["count"] == 1
        assert doc["serials"][0]["card_id"] == "magma_tyrant"

    def test_filter_by_card(self, isolated_home: Path):
        config_dir = isolated_home / "daimon"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "collection.json").write_text(json.dumps({
            "serials": [
                {"serial": "a", "card_id": "magma_tyrant", "rarity": "legendary",
                 "pack": "v1_alpha", "minted_at": "x", "minted_via": "pull"},
                {"serial": "b", "card_id": "iron_boar", "rarity": "common",
                 "pack": "v1_alpha", "minted_at": "x", "minted_via": "pull"},
            ],
        }))
        r = _run("collection", "--card", "iron_boar", "--json")
        doc = json.loads(r.output)
        assert doc["count"] == 1
        assert doc["serials"][0]["card_id"] == "iron_boar"


# ---------------------------------------------------------------------------
# `daimon catalog`
# ---------------------------------------------------------------------------

class TestCatalog:
    def test_expansions_lists_v1_alpha(self, isolated_home: Path):
        r = _run("catalog", "expansions")
        assert r.exit_code == 0, r.output
        assert "v1_alpha" in r.output

    def test_expansions_json_shape(self, isolated_home: Path):
        r = _run("catalog", "expansions", "--json")
        doc = json.loads(r.output)
        assert doc["count"] >= 1
        ids = [e["pack_id"] for e in doc["expansions"]]
        assert "v1_alpha" in ids
        # Rarity counts present and non-empty.
        v1 = next(e for e in doc["expansions"] if e["pack_id"] == "v1_alpha")
        assert v1["card_count"] > 0
        assert "legendary" in v1["rarity_counts"]

    def test_list_full_catalog(self, isolated_home: Path):
        r = _run("catalog", "list", "--json")
        doc = json.loads(r.output)
        assert doc["count"] == 200  # V1 alpha is locked at 200 cards
        assert all("card_id" in c for c in doc["cards"])

    def test_list_filtered_by_rarity(self, isolated_home: Path):
        r = _run("catalog", "list", "--rarity", "legendary", "--json")
        doc = json.loads(r.output)
        assert doc["count"] == 6  # V1 alpha: 6 legendary mutations
        assert all(c["rarity"] == "legendary" for c in doc["cards"])

    def test_list_filtered_by_element(self, isolated_home: Path):
        r = _run("catalog", "list", "--element", "FIRE", "--json")
        doc = json.loads(r.output)
        assert doc["count"] == 37  # locked count from catalog manifest
        assert all(c["element"] == "FIRE" for c in doc["cards"])

    def test_card_detail_human(self, isolated_home: Path):
        r = _run("catalog", "card", "magma_tyrant")
        assert r.exit_code == 0, r.output
        assert "magma_tyrant" in r.output
        assert "FIRE" in r.output
        assert "legendary" in r.output

    def test_card_detail_json(self, isolated_home: Path):
        r = _run("catalog", "card", "magma_tyrant", "--json")
        doc = json.loads(r.output)
        assert doc["card_id"] == "magma_tyrant"
        assert doc["rarity"] == "legendary"
        assert doc["payload"]["element"] == "FIRE"

    def test_card_unknown_id_exits_nonzero(self, isolated_home: Path):
        r = _run("catalog", "card", "no_such_card")
        assert r.exit_code != 0
        assert "unknown card" in r.output.lower()

    def test_compare_two_legendaries(self, isolated_home: Path):
        r = _run("catalog", "compare", "magma_tyrant", "tempest_apex", "--json")
        doc = json.loads(r.output)
        # Same rarity, different element.
        assert doc["a"]["card_id"] == "magma_tyrant"
        assert doc["b"]["card_id"] == "tempest_apex"
        assert "spd" in doc["diff"]
        assert doc["diff"]["spd"]["delta"] == doc["b"]["spd"] - doc["a"]["spd"]
        # They have at least some triggers somewhere in the partition.
        td = doc["trigger_diff"]
        assert "shared" in td and "a_only" in td and "b_only" in td

    def test_compare_unknown_card_exits_nonzero(self, isolated_home: Path):
        r = _run("catalog", "compare", "magma_tyrant", "no_such_card")
        assert r.exit_code != 0
        assert "no_such_card" in r.output


# ---------------------------------------------------------------------------
# `daimon loadout`
# ---------------------------------------------------------------------------

class TestLoadout:
    def test_list_empty(self, isolated_home: Path):
        r = _run("loadout", "list")
        assert r.exit_code == 0, r.output
        assert "no saved loadouts" in r.output.lower()

    def test_list_empty_json(self, isolated_home: Path):
        r = _run("loadout", "list", "--json")
        doc = json.loads(r.output)
        assert doc == {"loadouts": [], "count": 0}

    def test_new_to_stdout(self, isolated_home: Path):
        r = _run("loadout", "new")
        assert r.exit_code == 0, r.output
        doc = json.loads(r.output)
        assert doc["loadout_id"] == "my_loadout"
        assert len(doc["loadout"]) == 6
        assert all(isinstance(x, str) for x in doc["loadout"])

    def test_new_to_file(self, isolated_home: Path, tmp_path: Path):
        out = tmp_path / "scaffold.json"
        r = _run("loadout", "new", "--out", str(out))
        assert r.exit_code == 0, r.output
        assert out.is_file()
        doc = json.loads(out.read_text())
        assert "loadout" in doc and len(doc["loadout"]) == 6

    def test_new_refuses_to_overwrite(self, isolated_home: Path, tmp_path: Path):
        out = tmp_path / "exists.json"
        out.write_text("{}")
        r = _run("loadout", "new", "--out", str(out))
        assert r.exit_code != 0
        assert "already exists" in r.output.lower()

    def test_validate_showcase_format(self, isolated_home: Path):
        from importlib import resources

        showcase_path = (
            Path(str(resources.files("daimon.loadouts.showcase")))
            / "showcase_l1_inferno_burnstack.json"
        )
        r = _run("loadout", "validate", str(showcase_path), "--json")
        assert r.exit_code == 0, r.output
        doc = json.loads(r.output)
        assert doc["valid"] is True
        assert doc["card_count"] == 6
        assert doc["cards"][0]["card_id"] == "magma_tyrant"

    def test_validate_invalid_file(self, isolated_home: Path, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        r = _run("loadout", "validate", str(bad), "--json")
        assert r.exit_code != 0
        doc = json.loads(r.output)
        assert doc["valid"] is False

    def test_save_and_load_roundtrip(self, isolated_home: Path, tmp_path: Path):
        # 1. Generate a starter template.
        scaffold = tmp_path / "team.json"
        r1 = _run("loadout", "new", "--out", str(scaffold))
        assert r1.exit_code == 0, r1.output

        # 2. Save it under a name.
        r2 = _run("loadout", "save", str(scaffold), "my_team")
        assert r2.exit_code == 0, r2.output
        assert "saved" in r2.output.lower()

        # 3. List should now include it.
        r3 = _run("loadout", "list", "--json")
        listed = json.loads(r3.output)
        assert listed["count"] == 1
        assert listed["loadouts"][0]["name"] == "my_team"
        assert listed["loadouts"][0]["card_count"] == 6

        # 4. Load by name returns the saved doc.
        r4 = _run("loadout", "load", "my_team", "--json")
        loaded = json.loads(r4.output)
        assert loaded["name"] == "my_team"
        assert len(loaded["cards"]) == 6
        # Saved form is full stat-block (showcase resolved through catalog).
        assert all(isinstance(c, dict) for c in loaded["cards"])
        assert "card_id" in loaded["cards"][0]

    def test_save_rejects_invalid_loadout(self, isolated_home: Path, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"junk": True}))
        r = _run("loadout", "save", str(bad), "should_fail")
        assert r.exit_code != 0
        assert "unrecognized format" in r.output.lower()

    def test_save_rejects_bad_name(self, isolated_home: Path, tmp_path: Path):
        scaffold = tmp_path / "team.json"
        _run("loadout", "new", "--out", str(scaffold))
        r = _run("loadout", "save", str(scaffold), "../escape")
        assert r.exit_code != 0
        assert "invalid name" in r.output.lower()

    def test_load_unknown_name(self, isolated_home: Path):
        r = _run("loadout", "load", "no_such_loadout")
        assert r.exit_code != 0
        assert "unknown loadout" in r.output.lower()

    def test_save_then_match_npc_uses_saved_format(self, isolated_home: Path,
                                                    tmp_path: Path):
        """Round-trip: save a showcase loadout, then play it via match-npc.

        Proves the saved on-disk form (which is the resolved cards-dict
        form, not the original showcase form) is itself accepted by the
        unified loader. Catches regressions where save-format and
        load-format drift apart.
        """
        from importlib import resources

        showcase = (
            Path(str(resources.files("daimon.loadouts.showcase")))
            / "showcase_l1_inferno_burnstack.json"
        )

        # Save the showcase under a name (showcase → resolved stat-block).
        r1 = _run("loadout", "save", str(showcase), "my_inferno")
        assert r1.exit_code == 0, r1.output

        # Now find the saved file and feed it back into match-npc.
        saved = isolated_home / "daimon" / "loadouts" / "my_inferno.json"
        assert saved.is_file()

        r2 = _run("match-npc", str(saved), "sparring_sam",
                  "--seed", "00" * 32)
        assert r2.exit_code == 0, r2.output
        assert "winner:" in r2.output
