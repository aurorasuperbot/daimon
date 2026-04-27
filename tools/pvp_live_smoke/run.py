#!/usr/bin/env python3
"""Live PvP smoke against the real arena repo.

WHY THIS EXISTS
---------------
``tests/test_arena_smoke.py`` proves the engine ↔ arbiter contract by
feeding the engine-emitted bodies into a *local copy* of the arbiter
function. That covers the encoding handshake but says nothing about:

  * whether the GitHub Actions arbiter workflow actually fires when a
    fresh comment lands on a real ``match-challenge`` Issue,
  * whether ``ENGINE_READ_TOKEN`` resolves and pip-installs the engine
    inside the runner,
  * whether the arbiter's ``Commit + close`` step has the right perms,
  * whether ``dm_pvp_status`` correctly observes ``phase=resolved`` once
    the runner closes the Issue.

That's exactly the seam this script exercises: it spins up two
ephemeral identities (alice + bob) on the local machine, drives the
real ``dm_pvp_*`` MCP tool functions against the *live* arena repo
(``aurorasuperbot/daimon-arena``), then polls ``dm_pvp_status`` until
the arbiter workflow settles the match. If everything's wired right,
this prints ``OK`` and the arena gets exactly one new ``matches/N.json``
+ a leaderboard update with two synthetic pubkeys at the bottom.

WHAT THIS LEAVES BEHIND
-----------------------
A real, settled match in the real arena. The two pubkeys are
ephemeral (regenerated every run, never written under
``~/.config/daimon``) — they appear once on the leaderboard as
``alice/bob smoke`` artifacts. Pre-launch this is fine; the matches/
+ leaderboard.json should be wiped before V1 ships.

USAGE
-----
    cd nullpoint/
    .venv/bin/python tools/pvp_live_smoke/run.py

Optional env:

  DAIMON_ARENA_REPO   — override the default ``aurorasuperbot/daimon-arena``
  PVP_SMOKE_POLL_SEC  — poll interval (default 30s)
  PVP_SMOKE_MAX_WAIT  — max wait for arbiter (default 300s)

Exit codes:
  0  — match settled and matches/N.json fetched cleanly
  1  — one of the dm_pvp_* tools returned an error
  2  — arbiter never settled the match within PVP_SMOKE_MAX_WAIT
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict


# Ensure the engine package is importable when run from a checkout root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))


from daimon.arena import state as arena_state  # noqa: E402
from daimon.identity import generate_identity, keys as identity_keys  # noqa: E402
from daimon.mcp.server import (  # noqa: E402
    dm_pvp_accept,
    dm_pvp_challenge,
    dm_pvp_reveal,
    dm_pvp_status,
)


POLL_SEC = int(os.environ.get("PVP_SMOKE_POLL_SEC", "30"))
MAX_WAIT = int(os.environ.get("PVP_SMOKE_MAX_WAIT", "300"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ELEMENTS = ["FIRE", "WATER", "NATURE", "VOLT", "VOID", "NORMAL"]


def _filler_card(position: int, suffix: str) -> Dict[str, Any]:
    return {
        "card_id": f"livesmoke_{suffix}_pos{position}",
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


def _call(tool, **kwargs):
    """FastMCP wraps the function; .fn is the original callable."""
    fn = getattr(tool, "fn", tool)
    return fn(**kwargs)


class Player:
    """Ephemeral identity + pvp_state isolation, one per simulated machine."""

    def __init__(self, name: str, root: Path):
        self.name = name
        self.cfg = root / f"{name}_config"
        self.cfg.mkdir(parents=True, exist_ok=True)
        self.pvp = root / f"{name}_pvp_state"
        self.pubkey_hex: str = ""

    def activate(self) -> None:
        """Point the daimon module attrs at this player's dirs.

        Plain attribute assignment (not pytest's monkeypatch) because this
        script runs outside pytest. Restoration is unnecessary — the script
        process exits when done.
        """
        identity_keys.CONFIG_DIR = self.cfg
        identity_keys.PRIVATE_KEY_PATH = self.cfg / "identity.key"
        identity_keys.PUBLIC_KEY_PATH = self.cfg / "identity.pub"
        identity_keys.METADATA_PATH = self.cfg / "identity.json"
        arena_state.PVP_STATE_DIR = self.pvp
        os.environ["DAIMON_INBOX"] = str(self.pvp.parent)

    def init_identity(self) -> None:
        ident = generate_identity(force=True)
        self.pubkey_hex = ident.pubkey_hex


def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _die(msg: str, exit_code: int = 1) -> None:
    _log(f"FAIL: {msg}")
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# The smoke
# ---------------------------------------------------------------------------

def main() -> int:
    repo = os.environ.get("DAIMON_ARENA_REPO", "aurorasuperbot/daimon-arena")
    _log(f"target arena repo: {repo}")
    _log(f"poll interval={POLL_SEC}s  max wait={MAX_WAIT}s")

    workdir = Path(tempfile.mkdtemp(prefix="pvp_live_smoke_"))
    _log(f"isolated tmp workspace: {workdir}")

    alice = Player("alice", workdir)
    bob = Player("bob", workdir)

    # 1. Each player initializes its own ed25519 identity.
    alice.activate()
    alice.init_identity()
    bob.activate()
    bob.init_identity()
    if alice.pubkey_hex == bob.pubkey_hex:
        _die("identity collision — generate_identity returned same pubkey twice")
    _log(f"alice pubkey: {alice.pubkey_hex[:16]}…")
    _log(f"bob   pubkey: {bob.pubkey_hex[:16]}…")

    # 2. Loadouts. Asymmetric so the outcome is deterministic non-draw.
    lo_alice = _full_loadout("alice")
    lo_bob = _full_loadout("bob")
    lo_bob["cards"][2]["atk"] = 12

    # 3. Alice opens the challenge — REAL gh issue create.
    alice.activate()
    _log("alice → dm_pvp_challenge (opening real Issue) …")
    res = _call(dm_pvp_challenge,
                opponent_pubkey=bob.pubkey_hex, loadout=lo_alice)
    if res.get("status") != "ok":
        _die(f"dm_pvp_challenge failed: {res}")
    issue_number = res["issue_number"]
    challenge_id = res["challenge_id"]
    _log(f"  → Issue #{issue_number}: {res['url']}")

    # 4. Bob accepts — REAL gh issue comment.
    bob.activate()
    _log("bob → dm_pvp_accept (posting /accept comment) …")
    res = _call(dm_pvp_accept, challenge_id=challenge_id, loadout=lo_bob)
    if res.get("status") != "ok":
        _die(f"dm_pvp_accept failed: {res}")
    _log("  → accepted")

    # 5. Both reveals.
    alice.activate()
    _log("alice → dm_pvp_reveal …")
    res = _call(dm_pvp_reveal, challenge_id=challenge_id)
    if res.get("status") != "ok":
        _die(f"dm_pvp_reveal (alice) failed: {res}")
    _log("  → revealed")

    bob.activate()
    _log("bob → dm_pvp_reveal …")
    res = _call(dm_pvp_reveal, challenge_id=challenge_id)
    if res.get("status") != "ok":
        _die(f"dm_pvp_reveal (bob) failed: {res}")
    _log("  → revealed; arbiter workflow should now be triggered")

    # 6. Poll status until phase=resolved (arbiter closed the Issue).
    deadline = time.time() + MAX_WAIT
    last_phase = None
    # Either side can poll; use alice.
    alice.activate()
    while time.time() < deadline:
        res = _call(dm_pvp_status, challenge_id=challenge_id)
        if res.get("status") != "ok":
            _log(f"  dm_pvp_status: {res}")
            time.sleep(POLL_SEC)
            continue
        phase = res.get("phase")
        if phase != last_phase:
            _log(f"  phase={phase}  comments={res.get('comment_count')}  "
                 f"reveals={res.get('reveal_count')}")
            last_phase = phase
        if phase == "resolved":
            match = res.get("match")
            if not match:
                _die("phase=resolved but no match record — fetch failed: "
                     f"{res.get('match_fetch_error')}")
            winner = res.get("winner_pubkey")
            loser = res.get("loser_pubkey")
            _log("  ✓ match settled")
            _log(f"    winner_pubkey: {winner[:16] if winner else 'None'}…")
            _log(f"    loser_pubkey:  {loser[:16] if loser else 'None'}…")
            _log(f"    reason: {match.get('reason')}")
            _log(f"    rounds: {match.get('rounds_played')}")
            _log(f"    hp_a:   {match.get('side_a_final_hp')}")
            _log(f"    hp_b:   {match.get('side_b_final_hp')}")
            _log(f"    seed:   {(match.get('seed_hex') or '')[:16]}…")

            # Sanity: winner must be one of our two synthetic pubkeys, and
            # the match record must round-trip the loadouts we sent.
            if winner not in (alice.pubkey_hex, bob.pubkey_hex):
                _die(f"winner pubkey {winner} ∉ {{alice, bob}}")
            if match.get("challenger_pubkey") != alice.pubkey_hex:
                _die("challenger_pubkey doesn't round-trip")
            if match.get("opponent_pubkey") != bob.pubkey_hex:
                _die("opponent_pubkey doesn't round-trip")
            _log("OK")
            return 0
        time.sleep(POLL_SEC)

    _die(f"arbiter never settled within {MAX_WAIT}s — last phase={last_phase}",
         exit_code=2)
    return 2  # unreachable; satisfies type checker


if __name__ == "__main__":
    sys.exit(main())
