"""High-level arena operations.

One function per agent-facing operation. The MCP tools in
:mod:`daimon.mcp.server` are thin shims over these — input validation
lives in the tool layer, signing / network I/O / state persistence lives
here.

Every function returns a JSON-serializable dict in one of two envelopes::

    {"status": "ok", ...domain fields...}
    {"error": "<category>", "message": "...", ...optional context...}

The error categories mirror the patterns established by neighboring tools
(``no_identity``, ``invalid_input``, ``not_found``) plus a few
arena-specific ones (``arena_unreachable``, ``unsigned_response``).
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any, Dict, List, Optional

from daimon.arena import client, encoding, state
from daimon.identity import load_identity, verify

# ---------------------------------------------------------------------------
# Repo / label constants — overridable via env so test arenas can point
# elsewhere without code changes.
# ---------------------------------------------------------------------------

DEFAULT_ARENA_REPO = "aurorasuperbot/daimon-arena"
DEFAULT_CARDS_REPO = "aurorasuperbot/daimon-cards"


def arena_repo() -> str:
    return os.environ.get("DAIMON_ARENA_REPO", DEFAULT_ARENA_REPO)


def cards_repo() -> str:
    return os.environ.get("DAIMON_CARDS_REPO", DEFAULT_CARDS_REPO)


# Tier thresholds (wins-based, mirror the existing 5-tier NPC roster).
# Documented in skill_npcs.md; these are the canonical thresholds for
# computing arena tier from raw wins/losses on the leaderboard read path.
TIER_THRESHOLDS = (
    ("Champion", 50),
    ("Elite", 25),
    ("Veteran", 10),
    ("Novice", 3),
    ("Rookie", 0),
)


def tier_of(wins: int) -> str:
    """Map a wins count to a tier label."""
    for name, threshold in TIER_THRESHOLDS:
        if wins >= threshold:
            return name
    return "Rookie"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _new_nonce_hex() -> str:
    return os.urandom(32).hex()


_REQUIRED_CARD_COUNT = 6


def _validated_loadout(loadout: Any) -> Dict[str, Any]:
    """Coerce input into a canonical ``{"cards": [...]}`` dict for hashing.

    Accepts either ``{"cards": [...]}`` or a bare list. Enforces exactly
    6 cards — rejects early instead of letting the engine do it, preventing
    an attacker from stuffing thousands of card objects into a commit hash.
    Raises ``ValueError`` on garbage.
    """
    if isinstance(loadout, dict):
        if "cards" not in loadout or not isinstance(loadout["cards"], list):
            raise ValueError("loadout dict must have 'cards' list")
        cards = loadout["cards"]
    elif isinstance(loadout, list):
        cards = loadout
    else:
        raise ValueError(
            f"loadout must be dict or list, got {type(loadout).__name__}"
        )
    if len(cards) != _REQUIRED_CARD_COUNT:
        raise ValueError(
            f"loadout must have exactly {_REQUIRED_CARD_COUNT} cards, "
            f"got {len(cards)}"
        )
    return {"cards": cards}


def _load_identity_or_error() -> Dict[str, Any]:
    """Load local identity, returning a structured envelope on failure.

    On success returns ``{"_identity": Identity}``; on failure returns the
    standard ``{"error": "no_identity", ...}`` envelope.
    """
    try:
        identity = load_identity()
    except FileNotFoundError:
        return {"error": "no_identity",
                "hint": "Run `daimon init` first."}
    return {"_identity": identity}


# ---------------------------------------------------------------------------
# Identity registration
# ---------------------------------------------------------------------------

def register(handle: Optional[str] = None) -> Dict[str, Any]:
    """Open an identity-registration Issue.

    The Issue body carries a signed assertion ``{pubkey, handle, ts}`` so
    the arena (or any human reader) can prove the registration was made by
    the holder of the private key.
    """
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    handle = (handle or "").strip() or "(auto)"
    ts = _now_iso()
    payload = encoding.register_signing_payload(identity.pubkey_hex, handle, ts)
    sig_hex = identity.sign_bytes(payload).hex()

    body = encoding.format_kv_body([
        ("pubkey_hex", identity.pubkey_hex),
        ("handle", handle),
        ("signed_at", ts),
        ("signature", sig_hex),
        ("protocol", encoding.PROTOCOL_VERSION_REGISTER),
    ])

    repo = arena_repo()
    title = f"register: {identity.pubkey_hex[:16]}…"
    res = client.create_issue(repo, title, body,
                              labels=["identity", "pending-arbiter"])
    if not res["ok"]:
        return _arena_error("register", res)

    return {
        "status": "ok",
        "issue_number": res["issue_number"],
        "url": res["url"],
        "pubkey_hex": identity.pubkey_hex,
        "handle": handle,
        "phase": "pending-arbiter",
    }


# ---------------------------------------------------------------------------
# PvP commit-reveal
# ---------------------------------------------------------------------------

def pvp_challenge(opponent_pubkey: str,
                  loadout: Any,
                  memo: Optional[str] = None,
                  pack_pin: str = "starter-v1.0.0",
                  rule_set: str = "standard-v1") -> Dict[str, Any]:
    """Open a PvP challenge against ``opponent_pubkey``.

    Generates a fresh nonce, computes commit = SHA-256(canonical(loadout) ||
    nonce), opens an Issue carrying just the commit. Saves the loadout +
    nonce locally for later reveal.

    The Issue body includes ``opponent_pubkey`` (the responder will need to
    prove they are this pubkey when they accept). ``opponent_handle`` is
    NOT included — pubkey is the canonical identifier.
    """
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    try:
        loadout_canon = _validated_loadout(loadout)
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    nonce = _new_nonce_hex()
    commit = encoding.loadout_commit_hash(loadout_canon, nonce)
    ts = _now_iso()

    body = encoding.format_kv_body([
        ("challenger_pubkey", identity.pubkey_hex),
        ("opponent_pubkey", opponent_pubkey),
        # opponent_handle is required by the arbiter parser even though we
        # don't have a pubkey→handle index yet. Stash the pubkey-truncation
        # so the body is human-readable.
        ("opponent_handle", opponent_pubkey[:16]),
        ("pack_pin", pack_pin),
        ("rule_set", rule_set),
        ("loadout_commit", commit),
        ("challenged_at", ts),
        ("memo", memo or ""),
        ("protocol", encoding.PROTOCOL_VERSION_PVP),
    ])

    repo = arena_repo()
    title = "pvp-challenge"
    res = client.create_issue(repo, title, body,
                              labels=["match-challenge", "pvp",
                                      "pending-accept"])
    if not res["ok"]:
        return _arena_error("pvp_challenge", res)

    issue_number = res["issue_number"]
    state.save(
        issue_number=issue_number,
        side="challenger",
        nonce=nonce,
        loadout=loadout_canon,
        pubkey_hex=identity.pubkey_hex,
        opponent_pubkey=opponent_pubkey,
    )

    return {
        "status": "ok",
        "challenge_id": str(issue_number),
        "issue_number": issue_number,
        "url": res["url"],
        "loadout_commit": commit,
        "phase": "pending-accept",
        "next_step": (
            "wait for opponent to dm_pvp_accept, then run "
            "dm_pvp_reveal(challenge_id) to publish your loadout"
        ),
    }


def pvp_accept(challenge_id: str, loadout: Any) -> Dict[str, Any]:
    """Accept a pending challenge — post our commit + save loadout for reveal.

    Posts a ``/accept`` comment carrying just the commit hash + responder
    pubkey. The full loadout is held locally until ``dm_pvp_reveal``.
    """
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    if not isinstance(challenge_id, str) or not challenge_id.isdigit():
        return {"error": "invalid_input",
                "message": "challenge_id must be a numeric Issue number"}
    issue_number = int(challenge_id)

    try:
        loadout_canon = _validated_loadout(loadout)
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    nonce = _new_nonce_hex()
    commit = encoding.loadout_commit_hash(loadout_canon, nonce)
    ts = _now_iso()

    # Comment body MUST start with "/accept" so the arbiter's comment-walker
    # picks it up as the accept phase. Keep that on its own line.
    body = "/accept\n\n" + encoding.format_kv_body([
        ("opponent_pubkey", identity.pubkey_hex),
        ("loadout_commit", commit),
        ("accepted_at", ts),
        ("protocol", encoding.PROTOCOL_VERSION_PVP),
    ])

    repo = arena_repo()
    res = client.comment_issue(repo, issue_number, body)
    if not res["ok"]:
        return _arena_error("pvp_accept", res)

    state.save(
        issue_number=issue_number,
        side="responder",
        nonce=nonce,
        loadout=loadout_canon,
        pubkey_hex=identity.pubkey_hex,
        opponent_pubkey=None,  # we only know the challenger, not by pubkey-from-issue here
    )

    return {
        "status": "ok",
        "challenge_id": challenge_id,
        "loadout_commit": commit,
        "phase": "pending-arbiter",
        "next_step": "run dm_pvp_reveal(challenge_id) to publish your loadout",
    }


def pvp_reveal(challenge_id: str) -> Dict[str, Any]:
    """Post our reveal comment for an in-flight challenge.

    Reads our local state (nonce + loadout), signs the canonical reveal
    payload, and posts a ``/reveal`` comment. The body includes our pubkey
    so the arbiter can match this reveal to the correct side without
    relying on comment ordering.
    """
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    if not isinstance(challenge_id, str) or not challenge_id.isdigit():
        return {"error": "invalid_input",
                "message": "challenge_id must be a numeric Issue number"}
    issue_number = int(challenge_id)

    record = state.load(issue_number)
    if record is None:
        return {"error": "no_local_state",
                "message": f"no saved nonce/loadout for issue {issue_number}",
                "hint": ("you can only reveal a match you opened with "
                         "dm_pvp_challenge or accepted with dm_pvp_accept")}

    if record.get("pubkey_hex") != identity.pubkey_hex:
        stored = record.get("pubkey_hex", "")
        return {"error": "identity_mismatch",
                "message": ("local state was created under a different "
                            "identity"),
                "stored_pubkey_prefix": stored[:16] if stored else "",
                "current_pubkey_prefix": identity.pubkey_hex[:16]}

    nonce = record["nonce"]
    loadout_canon = record["loadout"]
    payload = encoding.pvp_signing_payload(issue_number, loadout_canon, nonce)
    sig_hex = identity.sign_bytes(payload).hex()

    body = "/reveal\n\n" + encoding.format_kv_body([
        ("pubkey", identity.pubkey_hex),
        ("nonce", nonce),
        ("signature", sig_hex),
        ("protocol", encoding.PROTOCOL_VERSION_PVP),
    ], json_block=loadout_canon)

    repo = arena_repo()
    res = client.comment_issue(repo, issue_number, body)
    if not res["ok"]:
        return _arena_error("pvp_reveal", res)

    return {
        "status": "ok",
        "challenge_id": challenge_id,
        "phase": "revealed",
        "next_step": ("wait for opponent's reveal + arbiter settlement, "
                      "then poll with dm_pvp_status"),
    }


def pvp_status(challenge_id: str) -> Dict[str, Any]:
    """Read the current phase + (if settled) result for a challenge."""
    if not isinstance(challenge_id, str) or not challenge_id.isdigit():
        return {"error": "invalid_input",
                "message": "challenge_id must be a numeric Issue number"}
    issue_number = int(challenge_id)
    repo = arena_repo()

    res = client.view_issue(repo, issue_number)
    if not res["ok"]:
        return _arena_error("pvp_status", res)
    issue = res["issue"]

    # Determine phase from labels + comment shape.
    label_names = {l.get("name", "") for l in issue.get("labels", [])}
    state_str = issue.get("state", "OPEN")
    comments = issue.get("comments", []) or []

    has_accept = any(_starts_with_directive(c.get("body", ""), "/accept")
                     for c in comments)
    reveal_count = sum(1 for c in comments
                       if _starts_with_directive(c.get("body", ""), "/reveal"))
    if state_str == "CLOSED":
        phase = "resolved"
    elif reveal_count >= 2:
        phase = "pending-arbiter"
    elif has_accept:
        phase = "revealing"
    else:
        phase = "pending-accept"

    out: Dict[str, Any] = {
        "status": "ok",
        "challenge_id": challenge_id,
        "issue_number": issue_number,
        "url": issue.get("url"),
        "title": issue.get("title"),
        "labels": sorted(label_names),
        "issue_state": state_str.lower(),
        "phase": phase,
        "comment_count": len(comments),
        "reveal_count": reveal_count,
    }

    # When resolved, fetch the canonical match record from matches/<id>.json.
    if phase == "resolved":
        match_res = client.fetch_repo_file(repo, f"matches/{issue_number}.json")
        if match_res["ok"]:
            record = match_res["content"]
            out["match"] = record
            out["winner_pubkey"] = _winner_pubkey(record)
            out["loser_pubkey"] = _loser_pubkey(record)
        else:
            out["match_fetch_error"] = match_res.get("message", "unknown")
    return out


def pvp_my_matches(limit: int = 20) -> Dict[str, Any]:
    """List PvP matches involving the local identity.

    Strategy: list all Issues with the ``pvp`` label and filter by pubkey
    found in the body kv pairs (challenger_pubkey or opponent_pubkey).
    GitHub's search API has no ``involves`` operator for arbitrary text,
    so client-side filtering is the only correct approach.
    """
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]
    my_pk = identity.pubkey_hex.lower()
    repo = arena_repo()

    # Pull more than `limit` to leave headroom for filtering, but cap at 100
    # to keep the gh call fast.
    fetch_n = min(100, max(limit * 2, limit + 10))
    res = client.list_issues(repo, labels=["pvp"], state="all", limit=fetch_n)
    if not res["ok"]:
        return _arena_error("pvp_my_matches", res)

    matches: List[Dict[str, Any]] = []
    for issue in res["issues"]:
        body = issue.get("body") or ""
        kv = encoding.parse_kv_body(body)
        challenger = (kv.get("challenger_pubkey") or "").strip().lower()
        opponent = (kv.get("opponent_pubkey") or "").strip().lower()
        if my_pk not in (challenger, opponent):
            continue
        role = "challenger" if challenger == my_pk else "responder"
        opp_pk = opponent if role == "challenger" else challenger
        labels = {l.get("name", "") for l in issue.get("labels", [])}
        state_str = (issue.get("state") or "OPEN").lower()
        if state_str == "closed":
            phase = "resolved"
        elif "pending-arbiter" in labels:
            phase = "pending-arbiter"
        else:
            phase = "pending-accept"
        matches.append({
            "challenge_id": str(issue.get("number")),
            "issue_number": issue.get("number"),
            "phase": phase,
            "role": role,
            "opponent_pubkey": opp_pk,
            "url": issue.get("url"),
            "updated_at": issue.get("updatedAt"),
        })
        if len(matches) >= limit:
            break
    return {"status": "ok", "matches": matches, "count": len(matches)}


# ---------------------------------------------------------------------------
# Leaderboard + my_rank
# ---------------------------------------------------------------------------

def leaderboard(limit: int = 25) -> Dict[str, Any]:
    """Read the full leaderboard, ranked by wins (then losses ascending)."""
    repo = arena_repo()
    res = client.fetch_repo_file(repo, "leaderboard.json")
    if not res["ok"]:
        if res.get("error") == "not_found":
            return {"status": "ok", "ranks": [], "count": 0,
                    "updated_at": None,
                    "note": "leaderboard.json not yet created"}
        return _arena_error("leaderboard", res)

    data = res["content"] if isinstance(res["content"], dict) else {}
    entries = data.get("entries", {}) or {}
    ranks = []
    for pk, rec in entries.items():
        wins = int(rec.get("wins", 0))
        losses = int(rec.get("losses", 0))
        draws = int(rec.get("draws", 0))
        ranks.append({
            "pubkey_hex": pk,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "tier": tier_of(wins),
        })
    # Sort by wins desc, then losses asc as tiebreaker.
    ranks.sort(key=lambda r: (-r["wins"], r["losses"]))
    for i, r in enumerate(ranks, 1):
        r["rank"] = i
    return {
        "status": "ok",
        "updated_at": data.get("last_updated"),
        "ranks": ranks[:limit],
        "count": min(len(ranks), limit),
        "total_players": len(ranks),
    }


def my_rank() -> Dict[str, Any]:
    """Return the local identity's rank + record."""
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    full = leaderboard(limit=1000)
    if full.get("status") != "ok":
        return full
    my_pk = identity.pubkey_hex.lower()
    for r in full.get("ranks", []):
        if r["pubkey_hex"].lower() == my_pk:
            return {
                "status": "ok",
                "pubkey_hex": identity.pubkey_hex,
                "rank": r["rank"],
                "tier": r["tier"],
                "wins": r["wins"],
                "losses": r["losses"],
                "draws": r["draws"],
                "total_players": full["total_players"],
            }
    return {
        "status": "ok",
        "pubkey_hex": identity.pubkey_hex,
        "rank": None,
        "tier": "Rookie",
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "total_players": full.get("total_players", 0),
        "note": "no matches yet — play one to enter the leaderboard",
    }


# ---------------------------------------------------------------------------
# Disputes + card proposals
# ---------------------------------------------------------------------------

def dispute_open(match_id: str,
                 reason: str,
                 evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Open a dispute Issue against a resolved match."""
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    if not isinstance(match_id, str) or not match_id.strip():
        return {"error": "invalid_input",
                "message": "match_id must be non-empty string"}
    if not isinstance(reason, str) or not reason.strip():
        return {"error": "invalid_input",
                "message": "reason must be non-empty string"}

    ts = _now_iso()
    payload = encoding.dispute_signing_payload(match_id, reason, ts)
    sig_hex = identity.sign_bytes(payload).hex()

    kv_pairs = [
        ("disputant_pubkey", identity.pubkey_hex),
        ("subject", match_id),
        ("reason", reason.replace("\n", " ").strip()),
        ("opened_at", ts),
        ("bond_amount", "50"),
        ("signature", sig_hex),
        ("protocol", encoding.PROTOCOL_VERSION_DISPUTE),
    ]
    body = encoding.format_kv_body(kv_pairs, json_block=evidence or {})

    repo = arena_repo()
    title = f"[dispute] appeal of {match_id}"
    res = client.create_issue(repo, title, body,
                              labels=["dispute-appeal", "needs-review"])
    if not res["ok"]:
        return _arena_error("dispute_open", res)

    return {
        "status": "ok",
        "issue_number": res["issue_number"],
        "url": res["url"],
        "match_id": match_id,
        "bond_amount": 50,
        "phase": "pending-review",
        "note": ("bond is recorded in the Issue body; spend mechanic on the "
                 "engine side is V1.1 (currently the bond is documentation-"
                 "only — arbiter will refund/forfeit when the spend layer "
                 "lands)"),
    }


def card_propose(card_def: Dict[str, Any],
                 rationale: Optional[str] = None) -> Dict[str, Any]:
    """Open a card-proposal Issue on the cards repo."""
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    if not isinstance(card_def, dict):
        return {"error": "invalid_input",
                "message": "card_def must be a JSON object"}

    # Best-effort schema check using the engine's card loader.
    try:
        from daimon.cards import load_card_dict
        load_card_dict(card_def)
        schema_ok = True
        schema_error: Optional[str] = None
    except (ValueError, TypeError) as e:
        schema_ok = False
        schema_error = str(e)
    # Schema failure is a hard error — proposing a card the engine can't
    # load wastes everyone's time. Surface it immediately so the agent
    # can fix the dict before opening an Issue.
    if not schema_ok:
        return {"error": "invalid_card",
                "message": "card_def fails engine schema check",
                "schema_error": schema_error}

    ts = _now_iso()
    payload = encoding.card_propose_signing_payload(card_def, ts)
    sig_hex = identity.sign_bytes(payload).hex()

    kv_pairs = [
        ("proposer_pubkey", identity.pubkey_hex),
        ("proposed_at", ts),
        ("rationale", (rationale or "").replace("\n", " ").strip()),
        ("signature", sig_hex),
        ("protocol", encoding.PROTOCOL_VERSION_CARD_PROPOSE),
    ]
    body = encoding.format_kv_body(kv_pairs, json_block=card_def)

    repo = cards_repo()
    title = f"[card-proposal] {card_def.get('card_id', '(no id)')}"
    res = client.create_issue(repo, title, body,
                              labels=["card-proposal", "design"])
    if not res["ok"]:
        return _arena_error("card_propose", res)

    return {
        "status": "ok",
        "issue_number": res["issue_number"],
        "url": res["url"],
        "card_id": card_def.get("card_id"),
        "phase": "pending-review",
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _starts_with_directive(comment_body: str, directive: str) -> bool:
    """Test whether the first non-empty line equals the directive token."""
    for line in comment_body.splitlines():
        s = line.strip()
        if not s:
            continue
        return s.lower() == directive.lower() or s.lower().startswith(
            directive.lower() + " "
        )
    return False


def _winner_pubkey(match_record: Dict[str, Any]) -> Optional[str]:
    winner = match_record.get("winner")
    if winner == 0:
        return match_record.get("challenger_pubkey")
    if winner == 1:
        return match_record.get("opponent_pubkey")
    return None


def _loser_pubkey(match_record: Dict[str, Any]) -> Optional[str]:
    winner = match_record.get("winner")
    if winner == 0:
        return match_record.get("opponent_pubkey")
    if winner == 1:
        return match_record.get("challenger_pubkey")
    return None


def _arena_error(op: str, client_result: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a client-layer failure into an ops-layer envelope.

    Keeps the agent-facing error shape consistent with neighboring tools
    (``error`` + ``message``) while preserving the underlying category for
    debugging.
    """
    return {
        "error": client_result.get("error", "arena_unreachable"),
        "message": (
            f"{op} failed: {client_result.get('message', 'unknown error')}"
        ),
        "underlying_category": client_result.get("error"),
        "exit_code": client_result.get("exit_code"),
    }
