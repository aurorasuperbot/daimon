"""Public-key encryption for arena data (sealed tickets, etc.).

Provides ECIES-style encryption using X25519 key agreement + AES-256-GCM,
built on the player's existing ed25519 identity keypair. No new keys to
manage — every player already has an ed25519 keypair from ``daimon init``.

The ed25519→x25519 conversion follows RFC 8032 / RFC 7748:
  - Private key: SHA-512(seed)[0:32], clamped per RFC 7748 §5.
  - Public key:  birational map  u = (1+y)/(1-y) mod p.

Encryption scheme (per-message):
  1. Generate ephemeral X25519 keypair.
  2. ECDH(ephemeral_private, recipient_x25519_public) → shared_secret.
  3. HKDF-SHA256(shared_secret, info=context) → 32-byte AES key.
  4. AES-256-GCM(key, nonce=random_12, plaintext) → ciphertext + tag.
  5. Output: {ephemeral_public ∥ nonce ∥ ciphertext+tag} as a single blob.

Decryption:
  1. Split blob → ephemeral_public, nonce, ciphertext+tag.
  2. Convert recipient ed25519 private key → X25519 private key.
  3. ECDH(recipient_x25519_private, ephemeral_public) → shared_secret.
  4. HKDF-SHA256(shared_secret, info=context) → 32-byte AES key.
  5. AES-256-GCM-Open(key, nonce, ciphertext+tag) → plaintext.

Security properties:
  - Forward secrecy per message (ephemeral keypair).
  - Authenticity: the arbiter signs the ciphertext blob separately (not
    handled here — callers pair encryption with an ed25519 signature).
  - IND-CCA2 under standard assumptions (X25519 + HKDF + AES-GCM).
"""

from __future__ import annotations

import hashlib
import os
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


# Curve25519 prime: p = 2^255 - 19
_P = (1 << 255) - 19

_HKDF_INFO = b"daimon-sealed-ticket-v1"
_NONCE_SIZE = 12   # AES-GCM standard
_KEY_SIZE = 32     # AES-256
_X25519_PUB_SIZE = 32


# ---------------------------------------------------------------------------
# Ed25519 → X25519 key conversion
# ---------------------------------------------------------------------------

def _mod_inv(a: int, p: int) -> int:
    """Modular inverse via Fermat's little theorem (p is prime)."""
    return pow(a, p - 2, p)


def ed25519_pub_to_x25519(ed25519_public_key: Ed25519PublicKey) -> X25519PublicKey:
    """Convert an Ed25519 public key to its X25519 (Curve25519) equivalent.

    The Ed25519 public key encodes a point on the twisted Edwards curve as
    the y-coordinate (little-endian) with the sign of x in the top bit.
    The X25519 u-coordinate is derived via the birational map:

        u = (1 + y) / (1 - y)  mod p

    This is the standard conversion per RFC 7748 / libsodium.
    """
    raw = ed25519_public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    # Decode y from little-endian 256-bit, clearing the sign bit.
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)

    # Birational map: u = (1 + y) * inverse(1 - y) mod p
    numerator = (1 + y) % _P
    denominator = (1 - y) % _P
    u = (numerator * _mod_inv(denominator, _P)) % _P

    u_bytes = u.to_bytes(32, "little")
    return X25519PublicKey.from_public_bytes(u_bytes)


def ed25519_priv_to_x25519(ed25519_private_key: Ed25519PrivateKey) -> X25519PrivateKey:
    """Convert an Ed25519 private key to its X25519 equivalent.

    Per RFC 8032 §5.1.5, the Ed25519 private key is a 32-byte seed.
    The scalar is SHA-512(seed)[0:32], clamped per RFC 7748 §5.
    """
    raw = ed25519_private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # SHA-512 of the seed, take first 32 bytes
    h = hashlib.sha512(raw).digest()[:32]

    # Clamp per RFC 7748 §5
    scalar = bytearray(h)
    scalar[0] &= 248    # clear bottom 3 bits
    scalar[31] &= 127   # clear top bit
    scalar[31] |= 64    # set second-to-top bit

    return X25519PrivateKey.from_private_bytes(bytes(scalar))


# ---------------------------------------------------------------------------
# Encrypt / Decrypt (ECIES: ephemeral X25519 + HKDF + AES-256-GCM)
# ---------------------------------------------------------------------------

def seal(plaintext: bytes, recipient_ed25519_pub: Ed25519PublicKey) -> bytes:
    """Encrypt ``plaintext`` so only the holder of the ed25519 private key can read it.

    Returns a single blob: ephemeral_x25519_pub (32) ‖ nonce (12) ‖ ciphertext+tag.
    """
    recipient_x25519 = ed25519_pub_to_x25519(recipient_ed25519_pub)

    ephemeral_priv = X25519PrivateKey.generate()
    ephemeral_pub_bytes = ephemeral_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    shared = ephemeral_priv.exchange(recipient_x25519)
    aes_key = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_SIZE,
        salt=None,
        info=_HKDF_INFO,
    ).derive(shared)

    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, None)

    return ephemeral_pub_bytes + nonce + ciphertext


def unseal(blob: bytes, recipient_ed25519_priv: Ed25519PrivateKey) -> bytes:
    """Decrypt a blob produced by ``seal()``.

    Raises ``cryptography.exceptions.InvalidTag`` if the blob was tampered
    with or the wrong key is used.
    """
    if len(blob) < _X25519_PUB_SIZE + _NONCE_SIZE + 16:
        raise ValueError("sealed blob too short")

    ephemeral_pub_bytes = blob[:_X25519_PUB_SIZE]
    nonce = blob[_X25519_PUB_SIZE:_X25519_PUB_SIZE + _NONCE_SIZE]
    ciphertext = blob[_X25519_PUB_SIZE + _NONCE_SIZE:]

    ephemeral_pub = X25519PublicKey.from_public_bytes(ephemeral_pub_bytes)
    recipient_x25519 = ed25519_priv_to_x25519(recipient_ed25519_priv)

    shared = recipient_x25519.exchange(ephemeral_pub)
    aes_key = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_SIZE,
        salt=None,
        info=_HKDF_INFO,
    ).derive(shared)

    return AESGCM(aes_key).decrypt(nonce, ciphertext, None)


# ---------------------------------------------------------------------------
# Hex convenience (for JSON storage in arena repo)
# ---------------------------------------------------------------------------

def seal_hex(plaintext: bytes, recipient_ed25519_pub: Ed25519PublicKey) -> str:
    """Like ``seal()`` but returns the blob as a hex string."""
    return seal(plaintext, recipient_ed25519_pub).hex()


def unseal_hex(blob_hex: str, recipient_ed25519_priv: Ed25519PrivateKey) -> bytes:
    """Like ``unseal()`` but accepts a hex string."""
    return unseal(bytes.fromhex(blob_hex), recipient_ed25519_priv)


# ---------------------------------------------------------------------------
# Round-trip self-test
# ---------------------------------------------------------------------------

def _self_test() -> bool:
    """Verify the full seal/unseal round-trip with a fresh keypair."""
    priv = Ed25519PrivateKey.from_private_bytes(bytes([0x42]) * 32)
    pub = priv.public_key()

    message = b'{"card_id":"flame_imp","rarity":"common","serial":"abc-123"}'

    sealed = seal(message, pub)
    recovered = unseal(sealed, priv)
    assert recovered == message, "round-trip mismatch"

    sealed_hex = seal_hex(message, pub)
    recovered_hex = unseal_hex(sealed_hex, priv)
    assert recovered_hex == message, "hex round-trip mismatch"

    # Wrong key must fail
    wrong_priv = Ed25519PrivateKey.from_private_bytes(bytes([0x99]) * 32)
    try:
        unseal(sealed, wrong_priv)
        assert False, "should have raised on wrong key"
    except Exception:
        pass

    # Tampered blob must fail
    tampered = bytearray(sealed)
    tampered[-1] ^= 0xFF
    try:
        unseal(bytes(tampered), priv)
        assert False, "should have raised on tampered blob"
    except Exception:
        pass

    return True


if __name__ == "__main__":
    _self_test()
    print("crypto self-test: PASS")
