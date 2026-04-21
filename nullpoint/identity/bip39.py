"""BIP39 mnemonic ↔ ed25519 seed.

We use a 24-word English mnemonic (256 bits of entropy). The first 32 bytes
of the BIP39-derived 64-byte seed are used directly as the ed25519 private
seed. This mirrors how SLIP-0010 / Ledger wallets do ed25519 derivation
without HD chains (we don't need HD — one identity per agent).

The mnemonic alone is sufficient to recover the identity. Treat it like a
password.
"""

from __future__ import annotations

from typing import Tuple

from mnemonic import Mnemonic


_MNEMO = Mnemonic("english")


def generate_seed_with_mnemonic() -> Tuple[bytes, str]:
    """Generate a fresh 32-byte ed25519 seed + its 24-word mnemonic."""
    mnemonic = _MNEMO.generate(strength=256)
    seed = seed_from_mnemonic(mnemonic)
    return seed, mnemonic


def seed_from_mnemonic(mnemonic: str, passphrase: str = "") -> bytes:
    """BIP39 mnemonic → 32-byte ed25519 seed."""
    if not _MNEMO.check(mnemonic):
        raise ValueError("Invalid BIP39 mnemonic (failed checksum)")
    full_seed = _MNEMO.to_seed(mnemonic, passphrase=passphrase)
    return full_seed[:32]


def mnemonic_from_seed(entropy: bytes) -> str:
    """Convert 16/20/24/28/32 bytes of entropy into a BIP39 mnemonic.

    For the standard 24-word recovery mnemonic, pass 32 bytes.
    """
    return _MNEMO.to_mnemonic(entropy)
