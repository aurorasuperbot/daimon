"""Identity: ed25519 keypairs + BIP39 mnemonic recovery + GitHub binding."""

from daimon.identity.bip39 import mnemonic_from_seed, seed_from_mnemonic
from daimon.identity.keys import (
    Identity,
    generate_identity,
    load_identity,
    restore_from_mnemonic,
    sign,
    verify,
)

__all__ = [
    "Identity",
    "generate_identity",
    "load_identity",
    "mnemonic_from_seed",
    "restore_from_mnemonic",
    "seed_from_mnemonic",
    "sign",
    "verify",
]
