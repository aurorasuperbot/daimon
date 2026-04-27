"""Tests for ``daimon.onboard.stages`` — the 5-stage product flow detector.

The detector is pure read-over-filesystem: it walks five gates in order
(identity → manifest → collection → match → mining hook) and returns the
first one still open as an :class:`OnboardingState`. The home card and
the ``dm_onboarding_status`` MCP tool both call ``detect_stage()``.

## Isolation

Every test that builds up state monkeypatches:

  * ``daimon.identity.keys.PRIVATE_KEY_PATH`` → tmp identity.key path
  * ``daimon.collection.COLLECTION_PATH``     → tmp collection.json path
  * ``daimon.mining.buffer.BUFFER_PATH``      → tmp mine_buffer.jsonl path
  * ``daimon.mining.installer.DEFAULT_SETTINGS_PATH`` → tmp settings.json
  * ``DAIMON_ART_DIR`` env                    → tmp art root (so
                                                 ``manifest_path()``
                                                 resolves under tmp)

The stages.py module imports each constant LAZILY inside its probe
helpers (``from daimon.identity.keys import PRIVATE_KEY_PATH`` happens
on every call, not at module load), so monkeypatched values take
effect immediately.

## Coverage

  * Per-stage gate tests prove each probe correctly detects its
    own state in isolation.
  * The full progression test walks the same tmp dir through all six
    states sequentially, verifying that adding the right artifact
    advances exactly one stage.
  * Defensive tests prove ``detect_stage`` never raises on broken
    fixtures (corrupt settings.json, malformed collection, missing
    npcs roster).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from daimon.onboard import (
    STAGE_ORDER,
    TOTAL_STAGES,
    OnboardingStage,
    OnboardingState,
    detect_stage,
    stage_index,
)


# ---------------------------------------------------------------------------
# Isolation fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_paths(monkeypatch, tmp_path):
    """Redirect every onboarding-relevant FS read into a temp dir.

    Returns a small dict of resolved tmp paths so individual tests can
    populate fixtures without re-deriving paths.
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    art = tmp_path / "art"
    art.mkdir()
    claude = tmp_path / "claude"
    claude.mkdir()

    # Identity: stages._identity_present() inspects PRIVATE_KEY_PATH only.
    from daimon.identity import keys as identity_keys
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")

    # Collection: stages._collection_count() calls collection.count() →
    # load_collection(None) which reads collection.COLLECTION_PATH.
    from daimon import collection as collection_mod
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH", cfg / "collection.json")

    # Mining buffer: stages._has_played_match() calls buffer.tail()
    # which defaults to buffer.BUFFER_PATH.
    from daimon.mining import buffer as buffer_mod
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH", cfg / "mine_buffer.jsonl")

    # Mining hook installer: stages._mining_hook_installed() reads
    # installer.DEFAULT_SETTINGS_PATH.
    from daimon.mining import installer as installer_mod
    monkeypatch.setattr(
        installer_mod, "DEFAULT_SETTINGS_PATH", claude / "settings.json"
    )

    # Art manifest: stages._manifest_installed() calls manifest_path()
    # which uses art_root() which honors DAIMON_ART_DIR env.
    monkeypatch.setenv("DAIMON_ART_DIR", str(art))

    return {
        "cfg": cfg,
        "art": art,
        "claude": claude,
        "identity_key": cfg / "identity.key",
        "collection": cfg / "collection.json",
        "buffer": cfg / "mine_buffer.jsonl",
        "settings": claude / "settings.json",
    }


# ---------------------------------------------------------------------------
# Fixture builders — minimal artifacts that flip a single gate
# ---------------------------------------------------------------------------

def _write_identity(paths: Dict[str, Path]) -> None:
    """Touch the identity file. Detector only checks .exists()."""
    paths["identity_key"].write_bytes(b"fake-pem-bytes")


def _write_manifest(paths: Dict[str, Path]) -> None:
    """Create art/<pack>/.manifest.json under the tmp DAIMON_ART_DIR."""
    from daimon.update.paths import manifest_path
    mp = manifest_path()
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps({"version": "art-test", "cards": {}}))


def _write_collection_with_one_card(paths: Dict[str, Path]) -> None:
    """Write a collection with a single fake serial."""
    paths["collection"].write_text(json.dumps({
        "pubkey_hex": "abc123",
        "serials": [{
            "serial": "00000000-0000-0000-0000-000000000001",
            "card_id": "fake_card",
            "pack": "v1_alpha",
            "rarity": "COMMON",
            "minted_at": "2026-01-01T00:00:00+00:00",
            "minted_via": "pull",
        }],
    }))


def _append_match_buffer_event(paths: Dict[str, Path]) -> None:
    """Append one kind=match entry — flips _has_played_match() to True."""
    paths["buffer"].write_text(json.dumps({
        "ts": "2026-01-01T00:00:00+00:00",
        "kind": "match",
        "amount": 0,
        "balance_after": 0,
    }) + "\n")


def _install_mining_hook(paths: Dict[str, Path]) -> None:
    """Write a settings.json with the daimon PostToolUse hook installed."""
    from daimon.mining.installer import HOOK_OWNER
    paths["settings"].write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {
                    "_owner": HOOK_OWNER,
                    "matcher": ".*",
                    "hooks": [
                        {"type": "command", "command": "daimon mine receipt"},
                    ],
                },
            ],
        },
    }))


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------

def test_stage_order_is_complete():
    """STAGE_ORDER lists every enum member exactly once, in declaration order."""
    assert tuple(STAGE_ORDER) == (
        OnboardingStage.BOOTSTRAP,
        OnboardingStage.ASSET_LOAD,
        OnboardingStage.FIRST_PULL,
        OnboardingStage.FIRST_MATCH,
        OnboardingStage.MINING_HOOK,
        OnboardingStage.GRADUATED,
    )
    # Each enum member appears in the order tuple
    assert set(STAGE_ORDER) == set(OnboardingStage)


def test_total_stages_excludes_graduated():
    """TOTAL_STAGES is the number of NON-graduated steps (5)."""
    assert TOTAL_STAGES == len(STAGE_ORDER) - 1
    assert TOTAL_STAGES == 5


def test_stage_index_round_trips():
    """stage_index returns 0..5 corresponding to STAGE_ORDER position."""
    for i, stage in enumerate(STAGE_ORDER):
        assert stage_index(stage) == i


def test_onboarding_stage_is_string_enum():
    """The enum inherits from str so JSON serialisation is direct."""
    assert OnboardingStage.BOOTSTRAP == "bootstrap"
    assert OnboardingStage.GRADUATED.value == "graduated"
    # JSON serialises the .value (str) cleanly:
    assert json.dumps(OnboardingStage.FIRST_PULL.value) == '"first_pull"'


def test_onboarding_state_to_dict_round_trip():
    """to_dict() emits a stable JSON-shaped dict."""
    state = OnboardingState(
        stage=OnboardingStage.FIRST_MATCH,
        step=4,
        total=5,
        title="t",
        blurb="b",
        cta_label="L",
        cta_message="M",
        signals={"k": "v"},
    )
    d = state.to_dict()
    assert d == {
        "stage": "first_match",
        "step": 4,
        "total": 5,
        "title": "t",
        "blurb": "b",
        "cta_label": "L",
        "cta_message": "M",
        "signals": {"k": "v"},
    }
    # to_dict copies the signals dict — mutating the result doesn't reach
    # back into the dataclass (which is frozen, but signals is a dict so
    # without the explicit copy a caller could still mutate the inner ref).
    d["signals"]["k"] = "tampered"
    assert state.signals == {"k": "v"}


# ---------------------------------------------------------------------------
# Per-stage gate tests
# ---------------------------------------------------------------------------

def test_stage_bootstrap_when_nothing_exists(isolated_paths):
    """Empty config dir + empty art dir → BOOTSTRAP state."""
    state = detect_stage()
    assert state.stage is OnboardingStage.BOOTSTRAP
    assert state.step == 1
    assert state.total == 5
    assert state.cta_message == "@daimon onboard"
    assert state.cta_label
    assert state.title
    assert state.blurb
    assert state.signals == {"identity_present": False}


def test_stage_asset_load_when_identity_but_no_manifest(isolated_paths):
    """Identity present + manifest missing → ASSET_LOAD."""
    _write_identity(isolated_paths)
    state = detect_stage()
    assert state.stage is OnboardingStage.ASSET_LOAD
    assert state.step == 2
    assert state.cta_message == "@daimon onboard"
    assert state.signals["identity_present"] is True
    assert state.signals["manifest_installed"] is False


def test_stage_first_pull_when_collection_empty(isolated_paths):
    """Identity + manifest, but collection has zero serials → FIRST_PULL."""
    _write_identity(isolated_paths)
    _write_manifest(isolated_paths)
    state = detect_stage()
    assert state.stage is OnboardingStage.FIRST_PULL
    assert state.step == 3
    assert state.cta_message == "@daimon pull"
    assert state.signals["collection_count"] == 0


def test_stage_first_match_when_cards_but_no_match(isolated_paths):
    """Identity + manifest + ≥1 card, no match in buffer → FIRST_MATCH."""
    _write_identity(isolated_paths)
    _write_manifest(isolated_paths)
    _write_collection_with_one_card(isolated_paths)
    state = detect_stage()
    assert state.stage is OnboardingStage.FIRST_MATCH
    assert state.step == 4
    # The CTA is "@daimon battle <NPC name>" — Sparring Sam is the
    # canonical first opponent (rank-1 Rookie). The probe falls back
    # to other Rookies if she's missing, so we accept any non-empty
    # opponent here rather than pinning the literal name.
    assert state.cta_message.startswith("@daimon battle ")
    assert state.signals["matches_played"] == 0
    assert state.signals["first_match_opponent"]
    # CTA label should reference the opponent name
    assert state.cta_label.startswith("Battle ")


def test_stage_mining_hook_when_played_but_hook_missing(isolated_paths):
    """Identity + manifest + cards + match record, no hook → MINING_HOOK."""
    _write_identity(isolated_paths)
    _write_manifest(isolated_paths)
    _write_collection_with_one_card(isolated_paths)
    _append_match_buffer_event(isolated_paths)
    state = detect_stage()
    assert state.stage is OnboardingStage.MINING_HOOK
    assert state.step == 5
    assert state.cta_message == "@daimon install mining hook"
    assert state.signals["hook_installed"] is False
    assert state.signals["collection_count"] == 1


def test_stage_graduated_when_everything_present(isolated_paths):
    """All five gates cleared → GRADUATED with empty CTA."""
    _write_identity(isolated_paths)
    _write_manifest(isolated_paths)
    _write_collection_with_one_card(isolated_paths)
    _append_match_buffer_event(isolated_paths)
    _install_mining_hook(isolated_paths)
    state = detect_stage()
    assert state.stage is OnboardingStage.GRADUATED
    assert state.step == 6
    assert state.title == ""
    assert state.blurb == ""
    assert state.cta_label == ""
    assert state.cta_message == ""
    # Signals capture the cleared-gate snapshot for telemetry/debugging
    assert state.signals == {
        "identity_present": True,
        "manifest_installed": True,
        "collection_count": 1,
        "matches_played": True,
        "hook_installed": True,
    }


# ---------------------------------------------------------------------------
# End-to-end progression test — single tmp dir walked through every state
# ---------------------------------------------------------------------------

def test_full_progression_walks_every_stage_in_order(isolated_paths):
    """Adding the right artifact between detect_stage() calls advances
    exactly one stage at a time.

    This is the integration-level proof that the gates are independent
    AND ordered correctly: each ``_write_*`` step flips exactly the
    next gate, never two at once.
    """
    expected_sequence = [
        (OnboardingStage.BOOTSTRAP, None),
        (OnboardingStage.ASSET_LOAD, _write_identity),
        (OnboardingStage.FIRST_PULL, _write_manifest),
        (OnboardingStage.FIRST_MATCH, _write_collection_with_one_card),
        (OnboardingStage.MINING_HOOK, _append_match_buffer_event),
        (OnboardingStage.GRADUATED, _install_mining_hook),
    ]

    for expected_stage, advance_fn in expected_sequence:
        if advance_fn is not None:
            advance_fn(isolated_paths)
        state = detect_stage()
        assert state.stage is expected_stage, (
            f"after applying {advance_fn.__name__ if advance_fn else 'nothing'}, "
            f"expected {expected_stage} but got {state.stage}"
        )


# ---------------------------------------------------------------------------
# Defensive: detect_stage NEVER raises
# ---------------------------------------------------------------------------

def test_detect_stage_with_corrupt_settings_treats_hook_as_uninstalled(
    isolated_paths,
):
    """A non-JSON settings.json shouldn't crash the detector — it should
    register as 'hook not installed' so the user gets the install prompt
    rather than a broken UI.
    """
    _write_identity(isolated_paths)
    _write_manifest(isolated_paths)
    _write_collection_with_one_card(isolated_paths)
    _append_match_buffer_event(isolated_paths)
    isolated_paths["settings"].write_text("{not valid json}")
    state = detect_stage()
    assert state.stage is OnboardingStage.MINING_HOOK
    assert state.signals["hook_installed"] is False


def test_detect_stage_with_settings_missing_hooks_key_treats_as_uninstalled(
    isolated_paths,
):
    """settings.json that's valid JSON but has no `hooks` key still gates
    the player at MINING_HOOK — the daimon hook is provably absent.
    """
    _write_identity(isolated_paths)
    _write_manifest(isolated_paths)
    _write_collection_with_one_card(isolated_paths)
    _append_match_buffer_event(isolated_paths)
    isolated_paths["settings"].write_text(json.dumps({"theme": "dark"}))
    state = detect_stage()
    assert state.stage is OnboardingStage.MINING_HOOK


def test_detect_stage_with_settings_unrelated_hook_treats_as_uninstalled(
    isolated_paths,
):
    """A user-installed PostToolUse hook from another tool shouldn't
    accidentally satisfy the daimon-mining gate.
    """
    _write_identity(isolated_paths)
    _write_manifest(isolated_paths)
    _write_collection_with_one_card(isolated_paths)
    _append_match_buffer_event(isolated_paths)
    isolated_paths["settings"].write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {"type": "command", "command": "echo other-tool"},
                    ],
                },
            ],
        },
    }))
    state = detect_stage()
    assert state.stage is OnboardingStage.MINING_HOOK


def test_detect_stage_with_legacy_unowned_daimon_hook_is_satisfied(
    isolated_paths,
):
    """Pre-`_owner`-tag installs of the daimon hook are detected by
    command-string match (legacy fallback in ``_has_daimon_hook``).
    """
    _write_identity(isolated_paths)
    _write_manifest(isolated_paths)
    _write_collection_with_one_card(isolated_paths)
    _append_match_buffer_event(isolated_paths)
    isolated_paths["settings"].write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {"type": "command", "command": "daimon mine receipt"},
                    ],
                },
            ],
        },
    }))
    state = detect_stage()
    assert state.stage is OnboardingStage.GRADUATED


def test_detect_stage_with_corrupt_collection_treats_as_empty(isolated_paths):
    """A corrupt collection.json shouldn't crash the detector. The probe
    swallows the parse error and reports collection_count=0, which gates
    the player at FIRST_PULL (the safe choice — they need a fresh start).
    """
    _write_identity(isolated_paths)
    _write_manifest(isolated_paths)
    isolated_paths["collection"].write_text("{not valid json}")
    state = detect_stage()
    assert state.stage is OnboardingStage.FIRST_PULL
    assert state.signals["collection_count"] == 0


def test_detect_stage_returns_state_dataclass(isolated_paths):
    """Sanity: the public return type is the OnboardingState dataclass."""
    state = detect_stage()
    assert isinstance(state, OnboardingState)
    assert isinstance(state.stage, OnboardingStage)
    assert isinstance(state.signals, dict)


# ---------------------------------------------------------------------------
# dm_home + dm_onboarding_status integration
# ---------------------------------------------------------------------------

def test_dm_onboarding_status_emits_envelope(isolated_paths):
    """The MCP tool wraps ``detect_stage()`` in a ``status: ok`` envelope."""
    from daimon.mcp.server import dm_onboarding_status
    # FastMCP wraps tool functions; pull the underlying callable.
    fn = getattr(dm_onboarding_status, "fn", dm_onboarding_status)
    result = fn()
    assert result["status"] == "ok"
    assert result["stage"] == OnboardingStage.BOOTSTRAP.value
    assert result["step"] == 1
    assert result["total"] == 5
    assert result["cta_message"] == "@daimon onboard"
    assert "signals" in result


def test_dm_home_includes_onboarding_field(isolated_paths):
    """``dm_home`` carries the same onboarding payload under the
    ``onboarding`` field — agents that already called dm_home don't
    need a second call.
    """
    # dm_home returns no_identity envelope when there's no identity, so
    # we need to spin one up first.
    from daimon.identity import generate_identity
    generate_identity(force=True)

    from daimon.mcp.server import dm_home
    fn = getattr(dm_home, "fn", dm_home)
    result = fn()
    assert "onboarding" in result
    onboarding = result["onboarding"]
    # We have an identity but nothing else — should be ASSET_LOAD.
    assert onboarding["stage"] == OnboardingStage.ASSET_LOAD.value
    assert onboarding["step"] == 2
    assert onboarding["cta_message"] == "@daimon onboard"


def test_dm_home_onboarding_field_when_graduated(isolated_paths):
    """When all five gates are clear, the onboarding field still ships
    as a GRADUATED snapshot (not None) — the renderer is responsible
    for hiding it.
    """
    # Spin up identity via the canonical path so it's a real key.
    from daimon.identity import generate_identity
    generate_identity(force=True)
    _write_manifest(isolated_paths)
    _write_collection_with_one_card(isolated_paths)
    _append_match_buffer_event(isolated_paths)
    _install_mining_hook(isolated_paths)

    from daimon.mcp.server import dm_home
    fn = getattr(dm_home, "fn", dm_home)
    result = fn()
    assert result["onboarding"]["stage"] == OnboardingStage.GRADUATED.value
    assert result["onboarding"]["title"] == ""
    assert result["onboarding"]["cta_message"] == ""
