"""Identity tests — generate, sign, verify, BIP39 round-trip."""

import os
from pathlib import Path

import pytest

from nullpoint.identity import (
    generate_identity,
    load_identity,
    mnemonic_from_seed,
    seed_from_mnemonic,
    sign,
    verify,
)
from nullpoint.identity.keys import (
    PRIVATE_KEY_PATH,
    PUBLIC_KEY_PATH,
    METADATA_PATH,
    restore_from_mnemonic,
    _seed_to_identity,
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Redirect ~/.config/nullpoint to a tmp dir for test isolation."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    # Re-import to pick up new HOME
    import importlib
    import nullpoint.identity.keys as kmod
    importlib.reload(kmod)

    yield kmod
    importlib.reload(kmod)  # reset


def test_seed_to_identity_deterministic():
    seed = b"\x42" * 32
    a = _seed_to_identity(seed)
    b = _seed_to_identity(seed)
    assert a.pubkey_hex == b.pubkey_hex


def test_sign_and_verify_round_trip():
    seed = os.urandom(32)
    identity = _seed_to_identity(seed)
    data = b"hello, nullpoint"
    sig = sign(identity, data)
    assert verify(identity.pubkey_hex, data, sig) is True


def test_verify_rejects_tampered_data():
    seed = os.urandom(32)
    identity = _seed_to_identity(seed)
    sig = sign(identity, b"original")
    assert verify(identity.pubkey_hex, b"tampered", sig) is False


def test_verify_rejects_bad_signature():
    seed = os.urandom(32)
    identity = _seed_to_identity(seed)
    assert verify(identity.pubkey_hex, b"data", b"\x00" * 64) is False


def test_verify_rejects_wrong_pubkey():
    seed_a = b"\x01" * 32
    seed_b = b"\x02" * 32
    identity_a = _seed_to_identity(seed_a)
    identity_b = _seed_to_identity(seed_b)
    sig = sign(identity_a, b"data")
    assert verify(identity_b.pubkey_hex, b"data", sig) is False


def test_bip39_round_trip():
    """Mnemonic → seed → mnemonic → seed produces identical seeds."""
    entropy = b"\xab" * 32
    m = mnemonic_from_seed(entropy)
    s1 = seed_from_mnemonic(m)
    # Same mnemonic must always produce same seed
    s2 = seed_from_mnemonic(m)
    assert s1 == s2
    assert len(s1) == 32


def test_bip39_invalid_mnemonic():
    with pytest.raises(ValueError, match="Invalid"):
        seed_from_mnemonic("not a valid mnemonic phrase")


def test_bip39_24_words():
    """Generated mnemonics from 32 bytes of entropy are 24 words."""
    entropy = b"\x00" * 32
    m = mnemonic_from_seed(entropy)
    assert len(m.split()) == 24


def test_generate_identity_writes_files(isolated_config):
    kmod = isolated_config
    identity = kmod.generate_identity()
    assert kmod.PRIVATE_KEY_PATH.exists()
    assert kmod.PUBLIC_KEY_PATH.exists()
    assert kmod.METADATA_PATH.exists()
    assert kmod.PRIVATE_KEY_PATH.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
    assert kmod.PUBLIC_KEY_PATH.read_text().strip() == identity.pubkey_hex
    assert identity.mnemonic and len(identity.mnemonic.split()) == 24


def test_generate_identity_refuses_overwrite(isolated_config):
    kmod = isolated_config
    kmod.generate_identity()
    with pytest.raises(FileExistsError):
        kmod.generate_identity()


def test_load_identity_round_trip(isolated_config):
    kmod = isolated_config
    gen = kmod.generate_identity()
    loaded = kmod.load_identity()
    assert loaded.pubkey_hex == gen.pubkey_hex
    # Signatures from loaded key verify against gen pubkey
    sig = loaded.sign_bytes(b"x")
    assert verify(gen.pubkey_hex, b"x", sig)


def test_restore_from_mnemonic_recovers_same_identity(isolated_config):
    kmod = isolated_config
    original = kmod.generate_identity()
    mnemonic = original.mnemonic
    # Wipe and restore
    kmod.PRIVATE_KEY_PATH.unlink()
    kmod.PUBLIC_KEY_PATH.unlink()
    kmod.METADATA_PATH.unlink()
    restored = kmod.restore_from_mnemonic(mnemonic)
    assert restored.pubkey_hex == original.pubkey_hex
