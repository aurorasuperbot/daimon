"""Unit tests for daimon.arena.encoding — the byte-level protocol surface.

Every function in this file MUST produce the same bytes the arbiter
(`daimon-arena/scripts/arbitrate.py`) produces for the same inputs.
A drift between engine + arbiter here is a silent consensus bug — players
would sign one payload and the arbiter would verify another. So the tests
below pin the *exact* bytes the arbiter expects, not just "some stable
encoding". When the arbiter updates its protocol version or layout, these
tests are the tripwire.
"""

from __future__ import annotations

import hashlib

from daimon.arena import encoding


# ---------------------------------------------------------------------------
# canonical_json — the building block every other function depends on
# ---------------------------------------------------------------------------

def test_canonical_json_is_sorted_no_whitespace():
    obj = {"b": 2, "a": 1, "nested": {"z": True, "m": [1, 2]}}
    got = encoding.canonical_json(obj)
    # Sorted keys, no whitespace, utf-8 bytes.
    assert got == b'{"a":1,"b":2,"nested":{"m":[1,2],"z":true}}'


def test_canonical_json_determinism():
    a = {"x": 1, "y": [3, 2, 1]}
    b = {"y": [3, 2, 1], "x": 1}
    assert encoding.canonical_json(a) == encoding.canonical_json(b)


def test_canonical_json_unicode_safe():
    # UTF-8, not escaped — json.dumps default is ensure_ascii=True so we
    # verify the bytes ASCII-escape cleanly (what the arbiter parses too).
    got = encoding.canonical_json({"name": "Doompaw Doppia"})
    assert b"Doompaw Doppia" in got


# ---------------------------------------------------------------------------
# loadout_commit_hash — SHA-256(canonical(loadout) || nonce_bytes)
# ---------------------------------------------------------------------------

def test_loadout_commit_hash_matches_manual_computation():
    loadout = {"cards": [{"card_id": "foo", "hp": 10}]}
    nonce_hex = "ab" * 32
    expected = hashlib.sha256(
        encoding.canonical_json(loadout) + bytes.fromhex(nonce_hex)
    ).hexdigest()
    assert encoding.loadout_commit_hash(loadout, nonce_hex) == expected


def test_loadout_commit_hash_nonce_changes_output():
    loadout = {"cards": []}
    n1 = "00" * 32
    n2 = "01" * 32
    assert (encoding.loadout_commit_hash(loadout, n1)
            != encoding.loadout_commit_hash(loadout, n2))


def test_loadout_commit_hash_key_order_invariant():
    l1 = {"cards": [{"atk": 1, "hp": 10}]}
    l2 = {"cards": [{"hp": 10, "atk": 1}]}
    n = "de" * 32
    assert (encoding.loadout_commit_hash(l1, n)
            == encoding.loadout_commit_hash(l2, n))


# ---------------------------------------------------------------------------
# Domain-separated signing payloads — layout MUST match arbitrate.py
# ---------------------------------------------------------------------------

def test_pvp_signing_payload_layout():
    loadout = {"cards": [{"card_id": "x"}]}
    nonce_hex = "ff" * 32
    got = encoding.pvp_signing_payload(42, loadout, nonce_hex)
    expected = (
        b"daimon-pvp-v1\n"
        + b"42\n"
        + encoding.canonical_json(loadout) + b"\n"
        + bytes.fromhex(nonce_hex)
    )
    assert got == expected


def test_register_signing_payload_layout():
    got = encoding.register_signing_payload(
        pubkey_hex="ab" * 32, handle="aurora", ts_iso="2026-04-24T00:00:00Z",
    )
    expected = (
        b"daimon-register-v1\n"
        + ("ab" * 32).encode() + b"\n"
        + b"aurora\n"
        + b"2026-04-24T00:00:00Z"
    )
    assert got == expected


def test_dispute_signing_payload_layout():
    got = encoding.dispute_signing_payload(
        match_id="42", reason="hp wrong", ts_iso="2026-04-24T00:00:00Z",
    )
    expected = (
        b"daimon-dispute-v1\n42\nhp wrong\n2026-04-24T00:00:00Z"
    )
    assert got == expected


def test_card_propose_signing_payload_layout():
    card_def = {"card_id": "z", "hp": 10}
    got = encoding.card_propose_signing_payload(
        card_def, ts_iso="2026-04-24T00:00:00Z",
    )
    expected = (
        b"daimon-card-propose-v1\n"
        + encoding.canonical_json(card_def) + b"\n"
        + b"2026-04-24T00:00:00Z"
    )
    assert got == expected


def test_protocol_versions_are_distinct():
    """Domain separation — no two surfaces share a label."""
    labels = {
        encoding.PROTOCOL_VERSION_PVP,
        encoding.PROTOCOL_VERSION_REGISTER,
        encoding.PROTOCOL_VERSION_DISPUTE,
        encoding.PROTOCOL_VERSION_CARD_PROPOSE,
        encoding.SEED_LABEL,
    }
    assert len(labels) == 5


def test_signing_payloads_differ_across_surfaces():
    """A signature on one surface's payload MUST NOT verify on another."""
    # Same underlying text but different protocol labels → different bytes.
    ts = "2026-04-24T00:00:00Z"
    reg = encoding.register_signing_payload("aa" * 32, "foo", ts)
    disp = encoding.dispute_signing_payload("foo", ts, ts)
    assert reg != disp


# ---------------------------------------------------------------------------
# Joint seed derivation — mixes BOTH sides' commits + nonces
# ---------------------------------------------------------------------------

def test_derive_joint_seed_deterministic():
    s1 = encoding.derive_joint_seed(42, "a" * 64, "b" * 64, "c" * 64, "d" * 64)
    s2 = encoding.derive_joint_seed(42, "a" * 64, "b" * 64, "c" * 64, "d" * 64)
    assert s1 == s2
    assert len(s1) == 32


def test_derive_joint_seed_any_input_change_flips_output():
    base = encoding.derive_joint_seed(42, "a" * 64, "b" * 64, "c" * 64, "d" * 64)
    # Every field participates in the hash.
    assert encoding.derive_joint_seed(43, "a" * 64, "b" * 64,
                                      "c" * 64, "d" * 64) != base
    assert encoding.derive_joint_seed(42, "0" * 64, "b" * 64,
                                      "c" * 64, "d" * 64) != base
    assert encoding.derive_joint_seed(42, "a" * 64, "0" * 64,
                                      "c" * 64, "d" * 64) != base
    assert encoding.derive_joint_seed(42, "a" * 64, "b" * 64,
                                      "0" * 64, "d" * 64) != base
    assert encoding.derive_joint_seed(42, "a" * 64, "b" * 64,
                                      "c" * 64, "0" * 64) != base


# ---------------------------------------------------------------------------
# Body format + parse — round trip
# ---------------------------------------------------------------------------

def test_format_kv_body_simple():
    got = encoding.format_kv_body([("a", 1), ("b", "hello"), ("c", "x y z")])
    assert got == "a: 1\nb: hello\nc: x y z"


def test_parse_kv_body_basic():
    text = "a: 1\nb: hello world\nextraneous line that's not kv\nc: yes"
    got = encoding.parse_kv_body(text)
    assert got == {"a": "1", "b": "hello world", "c": "yes"}


def test_parse_kv_body_lowercases_keys():
    text = "Pubkey_Hex: abc\nHANDLE: x"
    got = encoding.parse_kv_body(text)
    assert got == {"pubkey_hex": "abc", "handle": "x"}


def test_parse_kv_body_last_duplicate_wins():
    text = "a: 1\na: 2\na: 3"
    got = encoding.parse_kv_body(text)
    assert got == {"a": "3"}


def test_format_then_parse_round_trip():
    pairs = [("pubkey_hex", "ab" * 32), ("handle", "aurora"),
             ("signed_at", "2026-04-24T00:00:00+00:00")]
    text = encoding.format_kv_body(pairs)
    parsed = encoding.parse_kv_body(text)
    for k, v in pairs:
        assert parsed[k] == str(v)


# ---------------------------------------------------------------------------
# JSON block embedding + extraction
# ---------------------------------------------------------------------------

def test_format_kv_body_with_json_block():
    got = encoding.format_kv_body(
        [("a", 1)], json_block={"cards": [{"card_id": "x"}]},
    )
    assert got.startswith("a: 1\n\n```json\n")
    assert got.endswith("\n```")
    # Must be parseable back.
    extracted = encoding.extract_json_block(got)
    assert extracted == {"cards": [{"card_id": "x"}]}


def test_extract_json_block_no_block_returns_none():
    assert encoding.extract_json_block("no fence here") is None


def test_extract_json_block_malformed_returns_none():
    text = "```json\n{not valid json\n```"
    assert encoding.extract_json_block(text) is None


def test_extract_json_block_tolerates_plain_fence():
    """The arbiter's regex accepts ``` without ``json`` language tag too."""
    text = "preamble\n```\n{\"x\": 1}\n```\ntrailer"
    assert encoding.extract_json_block(text) == {"x": 1}
