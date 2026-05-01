"""Tests for daimon.arena.crypto — ed25519→x25519 conversion + sealed encryption."""

from __future__ import annotations

import json
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
from cryptography.hazmat.primitives import serialization

from daimon.arena.crypto import (
    ed25519_pub_to_x25519,
    ed25519_priv_to_x25519,
    seal,
    unseal,
    seal_hex,
    unseal_hex,
)


def _make_ed25519(seed_byte: int = 0x42):
    priv = Ed25519PrivateKey.from_private_bytes(bytes([seed_byte]) * 32)
    return priv, priv.public_key()


class TestKeyConversion:
    def test_pub_to_x25519_returns_x25519_key(self):
        _, pub = _make_ed25519()
        x_pub = ed25519_pub_to_x25519(pub)
        assert isinstance(x_pub, X25519PublicKey)

    def test_pub_to_x25519_deterministic(self):
        _, pub = _make_ed25519()
        a = ed25519_pub_to_x25519(pub).public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        b = ed25519_pub_to_x25519(pub).public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        assert a == b

    def test_different_ed25519_keys_yield_different_x25519(self):
        _, pub_a = _make_ed25519(0x11)
        _, pub_b = _make_ed25519(0x22)
        xa = ed25519_pub_to_x25519(pub_a).public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        xb = ed25519_pub_to_x25519(pub_b).public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        assert xa != xb

    def test_priv_to_x25519_exchange_matches_pub_to_x25519(self):
        """The converted private key must agree with the converted public key
        in an ECDH exchange (i.e., both sides of the conversion are consistent)."""
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        priv, pub = _make_ed25519()
        x_priv = ed25519_priv_to_x25519(priv)
        x_pub = ed25519_pub_to_x25519(pub)

        # Generate an independent ephemeral key and do ECDH both ways.
        eph = X25519PrivateKey.generate()
        shared_via_priv = x_priv.exchange(eph.public_key())
        shared_via_pub = eph.exchange(x_pub)
        assert shared_via_priv == shared_via_pub


class TestSealUnseal:
    def test_round_trip(self):
        priv, pub = _make_ed25519()
        msg = b"hello world"
        blob = seal(msg, pub)
        assert unseal(blob, priv) == msg

    def test_round_trip_json_ticket(self):
        priv, pub = _make_ed25519()
        ticket = json.dumps({
            "index": 0, "card_id": "flame_imp", "rarity": "legendary",
            "serial": "abc-123", "edition": "1st", "cost": 100,
        }).encode()
        blob = seal(ticket, pub)
        recovered = json.loads(unseal(blob, priv))
        assert recovered["card_id"] == "flame_imp"
        assert recovered["rarity"] == "legendary"

    def test_hex_round_trip(self):
        priv, pub = _make_ed25519()
        msg = b"ticket data"
        hex_blob = seal_hex(msg, pub)
        assert isinstance(hex_blob, str)
        assert unseal_hex(hex_blob, priv) == msg

    def test_wrong_key_fails(self):
        _, pub = _make_ed25519(0x11)
        wrong_priv, _ = _make_ed25519(0x22)
        blob = seal(b"secret", pub)
        with pytest.raises(Exception):
            unseal(blob, wrong_priv)

    def test_tampered_blob_fails(self):
        priv, pub = _make_ed25519()
        blob = seal(b"secret", pub)
        tampered = bytearray(blob)
        tampered[-1] ^= 0xFF
        with pytest.raises(Exception):
            unseal(bytes(tampered), priv)

    def test_truncated_blob_fails(self):
        priv, pub = _make_ed25519()
        blob = seal(b"secret", pub)
        with pytest.raises(ValueError, match="too short"):
            unseal(blob[:10], priv)

    def test_each_seal_produces_different_ciphertext(self):
        """Ephemeral key + random nonce ensure IND-CPA."""
        _, pub = _make_ed25519()
        msg = b"same message"
        a = seal(msg, pub)
        b = seal(msg, pub)
        assert a != b

    def test_empty_plaintext(self):
        priv, pub = _make_ed25519()
        blob = seal(b"", pub)
        assert unseal(blob, priv) == b""

    def test_large_plaintext(self):
        priv, pub = _make_ed25519()
        msg = os.urandom(10_000)
        blob = seal(msg, pub)
        assert unseal(blob, priv) == msg


import os
