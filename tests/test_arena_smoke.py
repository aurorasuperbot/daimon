"""End-to-end smoke test: full PvP cycle through the engine MCP into the arbiter.

This test exists to prove the engine ↔ arbiter contract is wired correctly.
The unit suites cover each piece in isolation:

  - ``test_arena_encoding.py``  pins canonical-bytes behavior
  - ``test_arena_state.py``     pins local secret-state persistence
  - ``test_arena_client.py``    pins the gh subprocess wrapper
  - ``test_mcp.py``             pins the MCP tool ↔ arena.ops glue
  - ``nullpoint-arena/scripts/test_arbitrate.py`` pins arbiter parsing/dispatch

What none of those covers: the *handoff* — that the bytes the engine emits
when calling ``dm_pvp_challenge`` / ``dm_pvp_accept`` / ``dm_pvp_reveal`` are
exactly the bytes the arbiter expects to parse off a GitHub Issue body. If
the two sides drift (say, the engine starts prefixing fields with a
different label, or the arbiter changes its regex) the unit tests will both
keep passing while production silently breaks.

So: simulate two players on two separate machines (each with their own
identity + pvp_state dir), drive the real MCP tool functions through a
queue-based fake of the gh CLI, capture the four canonical bodies the tools
post, then hand those bodies to the *actual* ``arbitrate()`` function from
the arena repo and assert the match resolves cleanly with the right
pubkeys + loadouts.

If this test fails, one of these is true:
  - The engine changed an emitted field name and the arbiter still expects
    the old one (or vice versa).
  - The signing payload format diverged between the two sides.
  - The pubkey-based reveal dispatch isn't routing correctly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest


# Add the arena repo's scripts/ directory to sys.path so we can import the
# *real* arbiter (not a copy). If the arena repo isn't sitting next to the
# engine repo (the canonical workspace layout), skip — this test only runs
# in dev where both repos are checked out.
_ARENA_SCRIPTS = (
    Path(__file__).resolve().parent.parent.parent / "nullpoint-arena" / "scripts"
)
if not (_ARENA_SCRIPTS / "arbitrate.py").exists():
    pytest.skip(
        f"arena repo not at {_ARENA_SCRIPTS} — skipping E2E smoke test",
        allow_module_level=True,
    )
sys.path.insert(0, str(_ARENA_SCRIPTS))


# Imports that depend on the path injection above must come after it.
from arbitrate import arbitrate as run_arbiter  # type: ignore  # noqa: E402

from daimon.arena import client as arena_client  # noqa: E402
from daimon.arena import state as arena_state  # noqa: E402
from daimon.identity import generate_identity, keys as identity_keys  # noqa: E402
from daimon.mcp.server import (  # noqa: E402
    dm_pvp_accept,
    dm_pvp_challenge,
    dm_pvp_reveal,
)


def _call(tool, **kwargs):
    """FastMCP wraps the function; .fn is the original callable. Bare
    functions still work (decorator may be transparent in some installs)."""
    fn = getattr(tool, "fn", tool)
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Loadout helper — V2 monster schema (species + element, no slot)
# ---------------------------------------------------------------------------

_ELEMENTS = ["FIRE", "WATER", "NATURE", "VOLT", "VOID", "NORMAL"]


def _filler_card(position: int, suffix: str) -> Dict[str, Any]:
    return {
        "card_id": f"smoke_{suffix}_pos{position}",
        "species": f"smoke_species_{position}",
        "element": _ELEMENTS[position],
        "atk": 5,
        "def": 5,
        "hp": 20,
        "spd": 5,
        "triggers": [],
    }


def _full_loadout(suffix: str) -> Dict[str, Any]:
    return {"cards": [_filler_card(i, suffix) for i in range(6)]}


# ---------------------------------------------------------------------------
# Two-player environment: each player gets their own config dir + pvp state.
# ---------------------------------------------------------------------------

class _Player:
    """A simulated machine: own identity, own pvp_state directory."""

    def __init__(self, name: str, root: Path):
        self.name = name
        self.cfg = root / f"{name}_config"
        self.cfg.mkdir(parents=True)
        self.pvp = root / f"{name}_pvp_state"
        self.pubkey_hex: str = ""

    def activate(self, monkeypatch) -> None:
        """Point the daimon module attrs at this player's dirs.

        Must be called BEFORE any tool invocation that should run as this
        player. Idempotent — re-calling swaps the active player.
        """
        monkeypatch.setattr(identity_keys, "CONFIG_DIR", self.cfg)
        monkeypatch.setattr(
            identity_keys, "PRIVATE_KEY_PATH", self.cfg / "identity.key")
        monkeypatch.setattr(
            identity_keys, "PUBLIC_KEY_PATH", self.cfg / "identity.pub")
        monkeypatch.setattr(
            identity_keys, "METADATA_PATH", self.cfg / "identity.json")
        monkeypatch.setattr(arena_state, "PVP_STATE_DIR", self.pvp)
        # DAIMON_INBOX is read by some helpers; keep it consistent with the
        # active player so anything else that consults it lands in the right
        # tmp tree (no real ~/.daimon writes).
        monkeypatch.setenv("DAIMON_INBOX", str(self.pvp.parent))

    def init_identity(self) -> None:
        ident = generate_identity(force=True)
        self.pubkey_hex = ident.pubkey_hex


# ---------------------------------------------------------------------------
# Fake gh dispatcher — captures every body the engine emits.
# ---------------------------------------------------------------------------

class _GhRecorder:
    """Record argvs + stdin bodies; reply with canned responses.

    A separate response queue lets each test call control what gh "would
    have" returned for create_issue (URL with issue number), comment_issue
    (URL with comment id), etc.
    """

    def __init__(self):
        self.calls = []  # list[(argv, body_text)]
        self._queue = []

    def queue(self, response: Dict[str, Any]) -> None:
        self._queue.append(response)

    def __call__(self, argv, input_text=None, timeout=20):
        self.calls.append((list(argv), input_text))
        if self._queue:
            return self._queue.pop(0)
        return {"ok": True, "stdout": "", "stderr": ""}


def _ok_create(issue_number: int) -> Dict[str, Any]:
    return {
        "ok": True,
        "stdout": (
            f"https://github.com/aurorasuperbot/daimon-arena/"
            f"issues/{issue_number}\n"
        ),
        "stderr": "",
    }


def _ok_comment(issue_number: int, comment_id: int = 999) -> Dict[str, Any]:
    return {
        "ok": True,
        "stdout": (
            f"https://github.com/aurorasuperbot/daimon-arena/"
            f"issues/{issue_number}#issuecomment-{comment_id}\n"
        ),
        "stderr": "",
    }


# ---------------------------------------------------------------------------
# The smoke test
# ---------------------------------------------------------------------------

ISSUE_NUMBER = 4242


def test_full_pvp_cycle_engine_to_arbiter(monkeypatch, tmp_path):
    """Drive challenge → accept → both reveals through the MCP tools, then
    feed the captured bodies into the real arbiter. Must produce ok=True."""
    # --- Setup: two players + one shared fake gh dispatcher ---
    alice = _Player("alice", tmp_path)
    bob = _Player("bob", tmp_path)

    gh = _GhRecorder()
    monkeypatch.setattr(arena_client, "_run", gh)

    # Each player generates an ed25519 identity inside their own CONFIG_DIR.
    alice.activate(monkeypatch)
    alice.init_identity()
    bob.activate(monkeypatch)
    bob.init_identity()

    assert alice.pubkey_hex != bob.pubkey_hex
    assert len(alice.pubkey_hex) == 64
    assert len(bob.pubkey_hex) == 64

    lo_alice = _full_loadout("alice")
    lo_bob = _full_loadout("bob")
    # Asymmetric so the match has a deterministic non-draw outcome (proves
    # the engine actually ran, not that we silently returned a draw).
    lo_bob["cards"][2]["atk"] = 12

    # --- Phase 1: alice opens the challenge ---
    alice.activate(monkeypatch)
    gh.queue(_ok_create(ISSUE_NUMBER))
    res = _call(dm_pvp_challenge,
        opponent_pubkey=bob.pubkey_hex, loadout=lo_alice
    )
    assert res["status"] == "ok", res
    assert res["issue_number"] == ISSUE_NUMBER

    # The body of the gh issue create call IS the challenge body the arbiter
    # will read off the Issue. Capture it verbatim.
    create_argv, challenge_body = gh.calls[-1]
    assert create_argv[:3] == ["gh", "issue", "create"]
    assert challenge_body is not None

    # --- Phase 2: bob accepts ---
    bob.activate(monkeypatch)
    gh.queue(_ok_comment(ISSUE_NUMBER, 1001))
    res = _call(dm_pvp_accept,challenge_id=str(ISSUE_NUMBER), loadout=lo_bob)
    assert res["status"] == "ok", res

    accept_argv, accept_body = gh.calls[-1]
    assert accept_argv[:3] == ["gh", "issue", "comment"]
    assert accept_body is not None
    # Body must start with the magic /accept marker the arbiter scans for.
    assert accept_body.lstrip().startswith("/accept")

    # --- Phase 3a: alice reveals ---
    alice.activate(monkeypatch)
    gh.queue(_ok_comment(ISSUE_NUMBER, 1002))
    res = _call(dm_pvp_reveal,challenge_id=str(ISSUE_NUMBER))
    assert res["status"] == "ok", res

    reveal_alice_argv, reveal_alice_body = gh.calls[-1]
    assert reveal_alice_argv[:3] == ["gh", "issue", "comment"]
    assert reveal_alice_body.lstrip().startswith("/reveal")
    # The reveal body MUST embed alice's pubkey — the arbiter dispatches
    # reveals to challenger/responder by this field, not by argv order.
    assert f"pubkey: {alice.pubkey_hex}" in reveal_alice_body

    # --- Phase 3b: bob reveals ---
    bob.activate(monkeypatch)
    gh.queue(_ok_comment(ISSUE_NUMBER, 1003))
    res = _call(dm_pvp_reveal,challenge_id=str(ISSUE_NUMBER))
    assert res["status"] == "ok", res

    reveal_bob_argv, reveal_bob_body = gh.calls[-1]
    assert f"pubkey: {bob.pubkey_hex}" in reveal_bob_body

    # --- Phase 4: feed the captured bodies into the real arbiter ---
    result = run_arbiter(
        ISSUE_NUMBER,
        challenge_body,
        accept_body,
        reveal_alice_body,
        reveal_bob_body,
    )
    assert result.ok, (
        f"arbiter rejected the engine-emitted bodies: errors={result.errors} "
        f"reason={result.reason}"
    )
    assert result.reason in ("wipe", "round_cap"), result.reason
    assert result.winner in (0, 1)  # not a draw — bob's atk=12 should bias
    assert result.challenger_pubkey == alice.pubkey_hex
    assert result.opponent_pubkey == bob.pubkey_hex
    assert len(result.seed_hex) == 64

    # The loadouts in the match record must round-trip — the arbiter parses
    # the JSON block out of each reveal body, so this proves the JSON block
    # the engine emitted is exactly what the arbiter reads.
    assert result.challenger_loadout == lo_alice
    assert result.opponent_loadout == lo_bob


def test_full_cycle_resolves_identically_when_reveals_swapped(
    monkeypatch, tmp_path
):
    """The same setup, but bob reveals before alice. Outcome MUST be
    identical — the no-grind property of the joint seed plus pubkey-based
    dispatch make comment-arrival order irrelevant.

    This is the one property the V1 PvP protocol can least afford to lose:
    if it ever broke, a player could observe the opponent's reveal and
    decide whether to reveal at all (selectively forfeit losing matches
    without taking a loss). Pin it.
    """
    alice = _Player("alice", tmp_path)
    bob = _Player("bob", tmp_path)

    gh = _GhRecorder()
    monkeypatch.setattr(arena_client, "_run", gh)

    alice.activate(monkeypatch)
    alice.init_identity()
    bob.activate(monkeypatch)
    bob.init_identity()

    lo_alice = _full_loadout("alice")
    lo_bob = _full_loadout("bob")
    lo_bob["cards"][2]["atk"] = 12

    alice.activate(monkeypatch)
    gh.queue(_ok_create(ISSUE_NUMBER))
    _call(dm_pvp_challenge,opponent_pubkey=bob.pubkey_hex, loadout=lo_alice)
    challenge_body = gh.calls[-1][1]

    bob.activate(monkeypatch)
    gh.queue(_ok_comment(ISSUE_NUMBER))
    _call(dm_pvp_accept,challenge_id=str(ISSUE_NUMBER), loadout=lo_bob)
    accept_body = gh.calls[-1][1]

    alice.activate(monkeypatch)
    gh.queue(_ok_comment(ISSUE_NUMBER))
    _call(dm_pvp_reveal,challenge_id=str(ISSUE_NUMBER))
    reveal_alice = gh.calls[-1][1]

    bob.activate(monkeypatch)
    gh.queue(_ok_comment(ISSUE_NUMBER))
    _call(dm_pvp_reveal,challenge_id=str(ISSUE_NUMBER))
    reveal_bob = gh.calls[-1][1]

    canonical = run_arbiter(
        ISSUE_NUMBER, challenge_body, accept_body, reveal_alice, reveal_bob,
    )
    swapped = run_arbiter(
        ISSUE_NUMBER, challenge_body, accept_body, reveal_bob, reveal_alice,
    )

    assert canonical.ok and swapped.ok
    assert canonical.winner == swapped.winner
    assert canonical.seed_hex == swapped.seed_hex
    assert canonical.side_a_final_hp == swapped.side_a_final_hp
    assert canonical.side_b_final_hp == swapped.side_b_final_hp


def test_full_cycle_arbiter_rejects_tampered_reveal(monkeypatch, tmp_path):
    """If alice tampers with her loadout JSON in the reveal AFTER signing,
    the arbiter MUST reject the match as cheat (commit-hash mismatch). This
    proves the commit-reveal mechanic still works through the real engine
    code path, not just the unit-test fixtures."""
    alice = _Player("alice", tmp_path)
    bob = _Player("bob", tmp_path)

    gh = _GhRecorder()
    monkeypatch.setattr(arena_client, "_run", gh)

    alice.activate(monkeypatch)
    alice.init_identity()
    bob.activate(monkeypatch)
    bob.init_identity()

    lo_alice = _full_loadout("alice")
    lo_bob = _full_loadout("bob")

    alice.activate(monkeypatch)
    gh.queue(_ok_create(ISSUE_NUMBER))
    _call(dm_pvp_challenge,opponent_pubkey=bob.pubkey_hex, loadout=lo_alice)
    challenge_body = gh.calls[-1][1]

    bob.activate(monkeypatch)
    gh.queue(_ok_comment(ISSUE_NUMBER))
    _call(dm_pvp_accept,challenge_id=str(ISSUE_NUMBER), loadout=lo_bob)
    accept_body = gh.calls[-1][1]

    alice.activate(monkeypatch)
    gh.queue(_ok_comment(ISSUE_NUMBER))
    _call(dm_pvp_reveal,challenge_id=str(ISSUE_NUMBER))
    reveal_alice = gh.calls[-1][1]

    bob.activate(monkeypatch)
    gh.queue(_ok_comment(ISSUE_NUMBER))
    _call(dm_pvp_reveal,challenge_id=str(ISSUE_NUMBER))
    reveal_bob = gh.calls[-1][1]

    # Surgically rewrite the JSON block in alice's reveal to crank her atk —
    # signature + commit-hash field stay the same, so verification fails.
    tampered_lo = json.loads(json.dumps(lo_alice))
    tampered_lo["cards"][0]["atk"] = 999
    head, sep, tail = reveal_alice.partition("```json\n")
    assert sep, "reveal body should have a fenced JSON block"
    json_end_marker = "\n```"
    json_block, _, after = tail.partition(json_end_marker)
    assert json_block, "reveal body should have JSON inside the fence"
    tampered_reveal = (
        head + sep + json.dumps(tampered_lo) + json_end_marker + after
    )

    result = run_arbiter(
        ISSUE_NUMBER, challenge_body, accept_body, tampered_reveal, reveal_bob,
    )
    assert not result.ok
    assert result.reason == "cheat"
    assert any("commit hash" in e for e in result.errors), result.errors
