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


def _load_github_username() -> Optional[str]:
    """Read ``github_username`` from local identity.json metadata."""
    import json
    from daimon.identity.keys import METADATA_PATH
    try:
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        return metadata.get("github_username")
    except Exception:
        return None


def _require_github_username() -> Dict[str, Any]:
    """Load github_username or return error envelope."""
    username = _load_github_username()
    if not username:
        return {"error": "not_registered",
                "message": "No github_username in identity.json — run dm_register first."}
    return {"_username": username}


# ---------------------------------------------------------------------------
# Arena state reads (Phase 2)
# ---------------------------------------------------------------------------

def arena_balance() -> Dict[str, Any]:
    """Fetch the server-side balance for the local player."""
    u = _require_github_username()
    if "error" in u:
        return u
    username = u["_username"]
    repo = arena_repo()
    res = client.fetch_player_balance(repo, username)
    if not res["ok"]:
        if res.get("error") == "not_found":
            return {"error": "not_found",
                    "message": f"no server state for {username}"}
        return _arena_error("arena_balance", res)
    content = res["content"]
    return {
        "status": "ok",
        "github_username": username,
        "balance": content.get("balance", 0),
        "last_daily_bonus": content.get("last_daily_bonus"),
    }


def arena_collection() -> Dict[str, Any]:
    """Fetch the server-side collection for the local player."""
    u = _require_github_username()
    if "error" in u:
        return u
    username = u["_username"]
    repo = arena_repo()
    res = client.fetch_player_collection(repo, username)
    if not res["ok"]:
        if res.get("error") == "not_found":
            return {"error": "not_found",
                    "message": f"no server state for {username}"}
        return _arena_error("arena_collection", res)
    content = res["content"]
    return {
        "status": "ok",
        "github_username": username,
        "serials": content.get("serials", []),
        "count": len(content.get("serials", [])),
    }


# ---------------------------------------------------------------------------
# Arena pull — claim pre-minted tickets (Phase 3)
# ---------------------------------------------------------------------------

def arena_pull() -> Dict[str, Any]:
    """Claim the next pre-minted pull ticket from the arena.

    Flow:
      1. Fetch pending tickets from server
      2. Find ticket at ``next_claim_index``
      3. Unseal the encrypted ticket with player's ed25519 private key
      4. Verify arbiter signature against published arbiter pubkey
      5. Open pull-claim Issue (async confirmation, but ticket data
         returned immediately)
      6. Return card data for the UI

    Returns on success::
        {"status": "ok", "card_id": "...", "rarity": "...",
         "serial": "...", "pack": "...", "cost": 100,
         "ticket_index": 0, "issue_number": 42, ...}

    Returns on failure::
        {"error": "no_tickets", "hint": "tickets refilling shortly"}
        {"error": "not_registered", ...}
        {"error": "unseal_failed", ...}
        {"error": "unsigned_response", ...}
    """
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    u = _require_github_username()
    if "error" in u:
        return u
    username = u["_username"]

    repo = arena_repo()

    # 1. Fetch pending tickets
    tickets_res = client.fetch_player_tickets(repo, username)
    if not tickets_res["ok"]:
        if tickets_res.get("error") == "not_found":
            return {"error": "no_tickets",
                    "message": f"no ticket file for {username}",
                    "hint": "tickets refilling shortly — try again in a few minutes"}
        return _arena_error("arena_pull", tickets_res)

    ticket_data = tickets_res["content"]
    tickets = ticket_data.get("tickets", [])
    next_idx = ticket_data.get("next_claim_index", 0)

    # 2. Find ticket at next_claim_index
    ticket_entry = None
    for t in tickets:
        if t.get("index") == next_idx:
            ticket_entry = t
            break

    if ticket_entry is None:
        return {"error": "no_tickets",
                "message": f"no unclaimed ticket at index {next_idx}",
                "hint": "tickets refilling shortly — try again in a few minutes"}

    # 3. Unseal the encrypted ticket
    sealed_hex = ticket_entry.get("sealed_hex")
    if not sealed_hex:
        return {"error": "invalid_ticket",
                "message": "ticket entry has no sealed_hex field"}

    try:
        from daimon.arena.crypto import unseal_hex
        import json as _json
        plaintext_bytes = unseal_hex(sealed_hex, identity.private_key)
        inner = _json.loads(plaintext_bytes)
    except Exception as e:
        return {"error": "unseal_failed",
                "message": f"could not decrypt ticket: {type(e).__name__}: {e}",
                "hint": "ticket may have been encrypted for a different key"}

    ticket_content = inner.get("ticket", inner)
    arbiter_sig = inner.get("arbiter_sig", "")

    # 4. Verify arbiter signature
    arbiter_res = client.fetch_arbiter_pubkey(repo)
    if not arbiter_res["ok"]:
        return _arena_error("arena_pull", arbiter_res)
    arbiter_pubkey_hex = arbiter_res["content"].get("pubkey_hex", "")

    if not encoding.verify_ticket_signature(
        ticket_content, arbiter_sig, arbiter_pubkey_hex
    ):
        return {"error": "unsigned_response",
                "message": "ticket arbiter signature verification failed",
                "hint": "the ticket may have been tampered with"}

    # 5. Open pull-claim Issue
    ts = _now_iso()
    claim_payload = encoding.pull_claim_signing_payload(
        username, next_idx, ts)
    sig_hex = identity.sign_bytes(claim_payload).hex()

    body = encoding.format_kv_body([
        ("claim_type", "pull"),
        ("github_username", username),
        ("pubkey_hex", identity.pubkey_hex),
        ("ticket_index", str(next_idx)),
        ("claimed_at", ts),
        ("signature", sig_hex),
        ("protocol", encoding.PROTOCOL_VERSION_PULL_CLAIM),
    ])

    issue_res = client.create_issue(
        repo, f"pull-claim: {username} ticket #{next_idx}",
        body, labels=["pull-claim", "pending-arbiter"])
    if not issue_res["ok"]:
        return _arena_error("arena_pull", issue_res)

    # 6. Return ticket data immediately
    return {
        "status": "ok",
        "card_id": ticket_content.get("card_id"),
        "rarity": ticket_content.get("rarity"),
        "serial": ticket_content.get("serial"),
        "pack": ticket_content.get("pack", "v1_alpha"),
        "cost": ticket_content.get("cost", 100),
        "edition": ticket_content.get("edition", "1st"),
        "seed_hex": ticket_content.get("seed_hex", ""),
        "ticket_index": next_idx,
        "issue_number": issue_res["issue_number"],
        "url": issue_res["url"],
        "phase": "pending-arbiter",
    }


# ---------------------------------------------------------------------------
# Quest & tier claims (Phase 6)
# ---------------------------------------------------------------------------

def arena_claim_quest(quest_id: str, date_str: str) -> Dict[str, Any]:
    """Open a quest-claim Issue for server-side quest reward.

    The arbiter verifies quest completion from arena state (match Issues,
    pull-claim Issues, etc.) and credits the reward to the player's balance.

    Returns on success::
        {"status": "ok", "quest_id": "...", "date": "...",
         "issue_number": 42, "url": "..."}
    """
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    u = _require_github_username()
    if "error" in u:
        return u
    username = u["_username"]

    repo = arena_repo()

    ts = _now_iso()
    payload = encoding.quest_claim_signing_payload(
        username, quest_id, date_str, ts)
    sig_hex = identity.sign_bytes(payload).hex()

    body = encoding.format_kv_body([
        ("claim_type", "quest"),
        ("github_username", username),
        ("pubkey_hex", identity.pubkey_hex),
        ("quest_id", quest_id),
        ("quest_date", date_str),
        ("claimed_at", ts),
        ("signature", sig_hex),
        ("protocol", encoding.PROTOCOL_VERSION_QUEST_CLAIM),
    ])

    issue_res = client.create_issue(
        repo, f"quest-claim: {username} {quest_id} ({date_str})",
        body, labels=["quest-claim", "pending-arbiter"])
    if not issue_res["ok"]:
        return _arena_error("arena_claim_quest", issue_res)

    return {
        "status": "ok",
        "quest_id": quest_id,
        "date": date_str,
        "issue_number": issue_res["issue_number"],
        "url": issue_res["url"],
        "phase": "pending-arbiter",
    }


def arena_claim_tier_up(tier: str) -> Dict[str, Any]:
    """Open a tier-claim Issue for server-side tier-up reward.

    The arbiter checks the leaderboard for the player's win count,
    verifies the tier hasn't been claimed before, and credits the reward.

    Returns on success::
        {"status": "ok", "tier": "...",
         "issue_number": 42, "url": "..."}
    """
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    u = _require_github_username()
    if "error" in u:
        return u
    username = u["_username"]

    repo = arena_repo()

    ts = _now_iso()
    payload = encoding.tier_claim_signing_payload(username, tier, ts)
    sig_hex = identity.sign_bytes(payload).hex()

    body = encoding.format_kv_body([
        ("claim_type", "tier_up"),
        ("github_username", username),
        ("pubkey_hex", identity.pubkey_hex),
        ("tier", tier),
        ("claimed_at", ts),
        ("signature", sig_hex),
        ("protocol", encoding.PROTOCOL_VERSION_TIER_CLAIM),
    ])

    issue_res = client.create_issue(
        repo, f"tier-claim: {username} -> {tier}",
        body, labels=["tier-claim", "pending-arbiter"])
    if not issue_res["ok"]:
        return _arena_error("arena_claim_tier_up", issue_res)

    return {
        "status": "ok",
        "tier": tier,
        "issue_number": issue_res["issue_number"],
        "url": issue_res["url"],
        "phase": "pending-arbiter",
    }


# ---------------------------------------------------------------------------
# Migration: local state → server state (one-time)
# ---------------------------------------------------------------------------

MIGRATION_BALANCE_CAP = 10000


def arena_migrate() -> Dict[str, Any]:
    """One-time migration of local balance + collection to the arena.

    Reads local ledger balance (capped at 10000 to prevent abuse) and
    local collection, then opens a migration Issue. The arbiter validates
    card_ids against the catalog and writes the state.

    Returns on success::
        {"status": "ok", "balance": int, "card_count": int,
         "issue_number": int, "url": str}
    """
    import hashlib

    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    u = _require_github_username()
    if "error" in u:
        return u
    username = u["_username"]

    repo = arena_repo()

    # Check if already migrated
    profile_res = client.fetch_repo_file(
        repo, f"players/{username}.json")
    if profile_res["ok"]:
        profile = profile_res["content"]
        if profile.get("migrated"):
            return {"error": "already_migrated",
                    "message": "state has already been migrated to the arena"}

    # Read local balance
    from daimon.mining.ledger import get_balance
    local_balance = get_balance()
    capped_balance = min(local_balance, MIGRATION_BALANCE_CAP)

    # Read local collection
    from daimon.collection import load_collection
    collection = load_collection()
    serials = collection.get("serials", [])
    card_ids = [s.get("card_id", "") for s in serials if isinstance(s, dict)]

    # Build collection payload for the Issue
    collection_payload = {
        "card_ids": card_ids,
        "serial_count": len(serials),
    }
    collection_json = encoding.canonical_json(collection_payload)
    collection_hash = hashlib.sha256(collection_json).hexdigest()

    ts = _now_iso()
    payload = encoding.migration_signing_payload(
        username, capped_balance, collection_hash, ts)
    sig_hex = identity.sign_bytes(payload).hex()

    body_lines = [
        ("claim_type", "migration"),
        ("github_username", username),
        ("pubkey_hex", identity.pubkey_hex),
        ("balance", str(capped_balance)),
        ("original_balance", str(local_balance)),
        ("collection_hash", collection_hash),
        ("card_count", str(len(card_ids))),
        ("migrated_at", ts),
        ("signature", sig_hex),
        ("protocol", encoding.PROTOCOL_VERSION_MIGRATION),
    ]

    body = encoding.format_kv_body(body_lines)
    body += f"\n\n```json\n{collection_json.decode()}\n```\n"

    issue_res = client.create_issue(
        repo, f"migration: {username}",
        body, labels=["migration", "pending-arbiter"])
    if not issue_res["ok"]:
        return _arena_error("arena_migrate", issue_res)

    return {
        "status": "ok",
        "balance": capped_balance,
        "original_balance": local_balance,
        "card_count": len(card_ids),
        "collection_hash": collection_hash,
        "issue_number": issue_res["issue_number"],
        "url": issue_res["url"],
        "phase": "pending-arbiter",
    }


# ---------------------------------------------------------------------------
# Identity registration
# ---------------------------------------------------------------------------

def register(handle: Optional[str] = None) -> Dict[str, Any]:
    """Open an identity-registration Issue.

    The Issue body carries a signed assertion ``{pubkey, handle, ts}`` so
    the arena (or any human reader) can prove the registration was made by
    the holder of the private key.

    If ``gh auth login`` has been run, the Issue also carries the GitHub
    username + id. On success the local ``identity.json`` metadata is
    enriched with ``github_username`` and ``avatar_url`` so future
    ``dm_whoami`` / ``dm_home`` calls can display them without a roundtrip.
    """
    id_or_err = _load_identity_or_error()
    if "error" in id_or_err:
        return id_or_err
    identity = id_or_err["_identity"]

    gh_user = client.get_github_user()
    if not gh_user["ok"]:
        return {
            "error": "gh_auth",
            "message": (
                "Could not resolve GitHub user — run `gh auth login` first. "
                f"({gh_user.get('message', 'unknown')})"
            ),
        }
    github_login = gh_user["login"]
    github_id = gh_user["id"]
    avatar_url = gh_user.get("avatar_url", "")

    handle = (handle or "").strip() or github_login
    ts = _now_iso()
    payload = encoding.register_signing_payload(identity.pubkey_hex, handle, ts)
    sig_hex = identity.sign_bytes(payload).hex()

    body = encoding.format_kv_body([
        ("pubkey_hex", identity.pubkey_hex),
        ("handle", handle),
        ("github_username", github_login),
        ("github_id", str(github_id)),
        ("signed_at", ts),
        ("signature", sig_hex),
        ("protocol", encoding.PROTOCOL_VERSION_REGISTER),
    ])

    repo = arena_repo()
    title = f"register: {github_login} ({identity.pubkey_hex[:16]}…)"
    res = client.create_issue(repo, title, body,
                              labels=["identity", "pending-arbiter"])
    if not res["ok"]:
        return _arena_error("register", res)

    _save_github_metadata(github_login, avatar_url)

    return {
        "status": "ok",
        "issue_number": res["issue_number"],
        "url": res["url"],
        "pubkey_hex": identity.pubkey_hex,
        "handle": handle,
        "github_username": github_login,
        "phase": "pending-arbiter",
    }


def _save_github_metadata(github_username: str, avatar_url: str) -> None:
    """Enrich local ``identity.json`` with GitHub account fields."""
    import json
    from daimon.identity.keys import METADATA_PATH
    try:
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        metadata = {}
    metadata["github_username"] = github_username
    metadata["avatar_url"] = avatar_url
    METADATA_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def fetch_player_profile(github_username: str) -> Dict[str, Any]:
    """Fetch a player's profile from the arena repo.

    Reads ``players/{github_username}.json`` — written by the arbiter
    when it processes a registration Issue.
    """
    if not github_username or not isinstance(github_username, str):
        return {"error": "invalid_input",
                "message": "github_username must be a non-empty string"}
    repo = arena_repo()
    res = client.fetch_repo_file(repo, f"players/{github_username}.json")
    if not res["ok"]:
        if res.get("error") == "not_found":
            return {"error": "not_found",
                    "message": f"no arena profile for {github_username}"}
        return _arena_error("fetch_player_profile", res)
    return {"status": "ok", "profile": res["content"]}


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
