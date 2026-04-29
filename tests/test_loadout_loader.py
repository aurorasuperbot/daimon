"""Tests for the unified ``daimon.loadouts.load_loadout_file`` /
``loadout_from_data`` entry points.

These cover the three on-disk shapes the CLI / MCP must accept:

  1. Bare list of card-pack dicts (legacy compact form).
  2. ``{"cards": [...]}`` full stat-block dict.
  3. ``{"loadout_id": ..., "loadout": ["card_id", ...]}`` showcase format.

The point of having one loader is that ``daimon match``, ``daimon match-npc``,
``dm_match``, and ``dm_match_npc`` all dispatch identically — playtest
discovered the showcase shape was silently mis-detected as shape #2 and
crashed deep in ``load_card_dict`` with "card must be a JSON object".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daimon.engine import Loadout, TEAM_SIZE
from daimon.loadouts import (
    load_loadout_file,
    loadout_from_data,
)
from daimon.loadouts.loader import _is_showcase_shape


# ---------------------------------------------------------------------------
# Fixtures: a tiny but valid card-pack dict so we don't depend on the catalog
# for shape #1 / #2 tests.
# ---------------------------------------------------------------------------

def _stub_card(idx: int) -> dict:
    """Synthesize a minimal card-pack dict accepted by ``load_card_dict``."""
    return {
        "card_id": f"stub_{idx}",
        "species": f"stub_species_{idx}",
        "element": "NORMAL",
        "atk": 5,
        "def": 5,
        "hp": 20,
        "spd": 5,
        "triggers": [],
    }


def _six_stub_cards() -> list[dict]:
    return [_stub_card(i) for i in range(TEAM_SIZE)]


# ---------------------------------------------------------------------------
# Shape detection — the disambiguation rule that the playtest bug exposed.
# ---------------------------------------------------------------------------

class TestShapeDetection:
    def test_showcase_shape_requires_loadout_key_with_strings(self):
        # All six entries are strings → showcase
        assert _is_showcase_shape({"loadout": ["a", "b", "c", "d", "e", "f"]})

    def test_cards_dict_is_not_showcase_even_with_loadout_key(self):
        # A pathological file that has BOTH `cards` AND a `loadout` key
        # of mixed types is not showcase (entries aren't all strings).
        data = {
            "loadout": [_stub_card(0)],  # list of dicts, not strings
            "cards": _six_stub_cards(),
        }
        assert not _is_showcase_shape(data)

    def test_bare_list_is_not_showcase(self):
        assert not _is_showcase_shape(_six_stub_cards())

    def test_empty_loadout_list_is_not_showcase(self):
        assert not _is_showcase_shape({"loadout": []})


# ---------------------------------------------------------------------------
# loadout_from_data — in-memory dispatcher used by both CLI and MCP.
# ---------------------------------------------------------------------------

class TestLoadoutFromData:
    def test_bare_list(self):
        lo, raw = loadout_from_data(_six_stub_cards())
        assert isinstance(lo, Loadout)
        assert len(lo.cards) == TEAM_SIZE
        assert len(raw) == TEAM_SIZE
        assert raw[0]["card_id"] == "stub_0"

    def test_cards_dict(self):
        lo, raw = loadout_from_data({"name": "test", "cards": _six_stub_cards()})
        assert isinstance(lo, Loadout)
        assert len(lo.cards) == TEAM_SIZE
        assert raw[5]["card_id"] == "stub_5"

    def test_showcase_dict_against_real_catalog(self):
        # Use a real bundled showcase — exercises the catalog lookup path.
        showcase = {
            "loadout_id": "showcase_l1_inferno_burnstack",
            "name": "test",
            "demonstrates": "L1",
            "loadout": [
                "magma_tyrant", "ash_strider", "blazefiend",
                "coalbreaker", "ember_raptor", "magma_warden",
            ],
        }
        lo, raw = loadout_from_data(showcase)
        assert isinstance(lo, Loadout)
        assert len(lo.cards) == TEAM_SIZE
        # Raw payloads should carry display fields (name, rarity) so the
        # HUD adapter can render them.
        assert all(isinstance(c, dict) for c in raw)
        assert raw[0]["card_id"] == "magma_tyrant"
        # And the engine got real cards out the other end.
        assert lo.cards[0].card_id == "magma_tyrant"

    def test_showcase_wrong_card_count_rejected(self):
        bad = {
            "loadout_id": "synthetic",
            "loadout": ["magma_tyrant", "ash_strider"],  # only 2
        }
        with pytest.raises(ValueError, match="6 card_ids"):
            loadout_from_data(bad)

    def test_showcase_unknown_card_id_rejected(self):
        bad = {
            "loadout_id": "synthetic",
            "loadout": [
                "magma_tyrant", "ash_strider", "blazefiend",
                "coalbreaker", "ember_raptor", "no_such_card",
            ],
        }
        with pytest.raises(ValueError, match="not in catalog"):
            loadout_from_data(bad)

    def test_unrecognized_top_level_rejected(self):
        with pytest.raises(ValueError, match="unrecognized format"):
            loadout_from_data({"some_other_key": 42})

    def test_cards_value_must_be_list(self):
        with pytest.raises(ValueError, match="`cards` must be a list"):
            loadout_from_data({"cards": "not a list"})

    def test_empty_card_list_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            loadout_from_data([])

    def test_card_list_with_non_dict_entries_rejected(self):
        # The exact bug from the playtest: showcase loadout fed as bare-
        # list path → entries are strings → reject with a helpful error.
        bad = ["card_id_1", "card_id_2", "card_id_3",
               "card_id_4", "card_id_5", "card_id_6"]
        with pytest.raises(ValueError, match="card object"):
            loadout_from_data({"cards": bad})

    def test_source_label_appears_in_error(self):
        with pytest.raises(ValueError, match="my_team.json"):
            loadout_from_data({"cards": "bad"}, source="my_team.json")


# ---------------------------------------------------------------------------
# load_loadout_file — file-IO wrapper.
# ---------------------------------------------------------------------------

class TestLoadLoadoutFile:
    def test_missing_file_raises_filenotfound(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_loadout_file(tmp_path / "nope.json")

    def test_invalid_json_raises_valueerror(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            load_loadout_file(p)

    def test_loads_bare_list(self, tmp_path: Path):
        p = tmp_path / "team.json"
        p.write_text(json.dumps(_six_stub_cards()), encoding="utf-8")
        lo, raw = load_loadout_file(p)
        assert len(lo.cards) == TEAM_SIZE
        assert raw[0]["card_id"] == "stub_0"

    def test_loads_cards_dict(self, tmp_path: Path):
        p = tmp_path / "team.json"
        p.write_text(
            json.dumps({"name": "test", "cards": _six_stub_cards()}),
            encoding="utf-8",
        )
        lo, raw = load_loadout_file(p)
        assert len(lo.cards) == TEAM_SIZE

    def test_loads_bundled_showcase(self):
        # The L1 inferno showcase ships in the package — this proves an
        # end-user can hand `daimon match-npc` the bundled file and have
        # it Just Work (the playtest's failure case).
        from importlib import resources

        showcase_path = (
            Path(str(resources.files("daimon.loadouts.showcase")))
            / "showcase_l1_inferno_burnstack.json"
        )
        assert showcase_path.is_file(), "bundled showcase missing"
        lo, raw = load_loadout_file(showcase_path)
        assert len(lo.cards) == TEAM_SIZE
        assert lo.cards[0].card_id == "magma_tyrant"

    def test_error_message_includes_path(self, tmp_path: Path):
        import re
        p = tmp_path / "team.json"
        p.write_text(json.dumps({"junk": True}), encoding="utf-8")
        with pytest.raises(ValueError, match=re.escape(str(p))):
            load_loadout_file(p)


# ---------------------------------------------------------------------------
# CLI integration — match-npc accepts all three shapes against a real NPC.
# ---------------------------------------------------------------------------

class TestCliMatchNpc:
    """End-to-end coverage proving the playtest crash is fixed.

    Each test feeds a different on-disk shape to ``daimon match-npc`` and
    asserts the command exits 0 with a "winner:" line. We don't pin a
    specific outcome (NPC RNG depends on the random seed); we only care
    that the loader didn't reject the file.
    """

    def _run(self, *args: str):
        from click.testing import CliRunner

        from daimon.cli import main as cli_main

        runner = CliRunner()
        # Use a fixed seed so the result is deterministic.
        return runner.invoke(
            cli_main, ["match-npc", *args, "--seed", "00" * 32],
            catch_exceptions=False,
        )

    def test_match_npc_accepts_bare_list(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "1")
        p = tmp_path / "team.json"
        p.write_text(json.dumps(_six_stub_cards()), encoding="utf-8")
        result = self._run(str(p), "sparring_sam")
        assert result.exit_code == 0, result.output
        assert "winner:" in result.output

    def test_match_npc_accepts_cards_dict(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "1")
        p = tmp_path / "team.json"
        p.write_text(
            json.dumps({"name": "stubs", "cards": _six_stub_cards()}),
            encoding="utf-8",
        )
        result = self._run(str(p), "sparring_sam")
        assert result.exit_code == 0, result.output
        assert "winner:" in result.output

    def test_match_npc_accepts_showcase_shape(self, monkeypatch):
        """The exact case that crashed the playtest."""
        monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "1")
        from importlib import resources

        showcase_path = (
            Path(str(resources.files("daimon.loadouts.showcase")))
            / "showcase_l1_inferno_burnstack.json"
        )
        result = self._run(str(showcase_path), "sparring_sam")
        assert result.exit_code == 0, result.output
        assert "winner:" in result.output
        assert "Sparring Sam" in result.output or "sparring_sam" in result.output


# ---------------------------------------------------------------------------
# MCP parity — _resolve_loadout_payload accepts the same three shapes.
# ---------------------------------------------------------------------------

class TestMcpLoadoutPayload:
    def test_mcp_accepts_bare_list(self):
        from daimon.mcp.server import _resolve_loadout_payload

        lo, raw = _resolve_loadout_payload(_six_stub_cards(), "loadout_a")
        assert len(lo.cards) == TEAM_SIZE
        assert len(raw) == TEAM_SIZE

    def test_mcp_accepts_cards_dict(self):
        from daimon.mcp.server import _resolve_loadout_payload

        lo, raw = _resolve_loadout_payload(
            {"cards": _six_stub_cards()}, "loadout_a"
        )
        assert len(lo.cards) == TEAM_SIZE

    def test_mcp_accepts_showcase_shape(self):
        from daimon.mcp.server import _resolve_loadout_payload

        showcase = {
            "loadout_id": "x",
            "loadout": [
                "magma_tyrant", "ash_strider", "blazefiend",
                "coalbreaker", "ember_raptor", "magma_warden",
            ],
        }
        lo, raw = _resolve_loadout_payload(showcase, "loadout_a")
        assert lo.cards[0].card_id == "magma_tyrant"
        assert len(raw) == TEAM_SIZE
