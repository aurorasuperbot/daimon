"""Ed25519 keypair management.

Identity = ed25519 private + public key. Stored at:
  ~/.config/daimon/identity.key   (private, 0600)
  ~/.config/daimon/identity.pub   (public)
  ~/.config/daimon/identity.json  (metadata)

Private key never leaves disk. Public key is the canonical identifier.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _resolve_config_dir() -> Path:
    """Resolve the DAIMON config directory.

    Precedence (first match wins):
      1. ``DAIMON_HOME`` env var — DAIMON-specific override (used for sandbox
         testing, CI, alternate-identity isolation).
      2. ``XDG_CONFIG_HOME/daimon`` — standard XDG basedir spec.
      3. ``~/.config/daimon`` — XDG default fallback.

    The override is read at import time, NOT each call — once daimon is
    imported, the path is fixed for the process. Tests that need a different
    path should monkeypatch ``CONFIG_DIR`` (and the derived path constants)
    on this module the same way ``test_mcp.py::_isolate_paths`` does.
    """
    env = os.environ.get("DAIMON_HOME")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "daimon"
    return Path.home() / ".config" / "daimon"


CONFIG_DIR = _resolve_config_dir()
_LEGACY_CONFIG_DIR = CONFIG_DIR.parent / "nullpoint"


def _migrate_legacy_config_dir() -> None:
    """One-shot migration: <basedir>/nullpoint → <basedir>/daimon.

    Runs at import time. No-op if the new dir already exists or the legacy
    dir doesn't. Best-effort: never raises (a stale config is recoverable;
    a crashing import is not).
    """
    try:
        if _LEGACY_CONFIG_DIR.exists() and not CONFIG_DIR.exists():
            CONFIG_DIR.parent.mkdir(parents=True, exist_ok=True)
            _LEGACY_CONFIG_DIR.rename(CONFIG_DIR)
    except Exception:
        # Migration is convenience, not correctness. Silently skip on failure.
        pass


_migrate_legacy_config_dir()

PRIVATE_KEY_PATH = CONFIG_DIR / "identity.key"
PUBLIC_KEY_PATH = CONFIG_DIR / "identity.pub"
METADATA_PATH = CONFIG_DIR / "identity.json"


@dataclass
class Identity:
    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey
    public_bytes: bytes        # 32-byte raw pubkey
    pubkey_hex: str            # canonical hex identifier
    mnemonic: Optional[str] = None  # only set on fresh generate

    def sign_bytes(self, data: bytes) -> bytes:
        return self.private_key.sign(data)


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)


def _seed_to_identity(seed32: bytes, mnemonic: Optional[str] = None) -> Identity:
    if len(seed32) != 32:
        raise ValueError("seed must be 32 bytes")
    priv = Ed25519PrivateKey.from_private_bytes(seed32)
    pub = priv.public_key()
    pub_bytes = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return Identity(
        private_key=priv,
        public_key=pub,
        public_bytes=pub_bytes,
        pubkey_hex=pub_bytes.hex(),
        mnemonic=mnemonic,
    )


def generate_identity(force: bool = False) -> Identity:
    """Generate a fresh ed25519 identity with BIP39 mnemonic.

    Writes:
      identity.key  (private, mode 0600)
      identity.pub  (public)
      identity.json (metadata: pubkey_hex, created_at)

    The mnemonic is RETURNED but NOT WRITTEN — caller must show it to user
    once. We never persist the recovery phrase.
    """
    from daimon.identity.bip39 import generate_seed_with_mnemonic

    _ensure_config_dir()
    if PRIVATE_KEY_PATH.exists() and not force:
        raise FileExistsError(
            f"Identity already exists at {PRIVATE_KEY_PATH}. "
            "Pass force=True to overwrite (DESTRUCTIVE)."
        )

    seed, mnemonic = generate_seed_with_mnemonic()
    identity = _seed_to_identity(seed, mnemonic=mnemonic)

    # Write private key
    priv_pem = identity.private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    PRIVATE_KEY_PATH.write_bytes(priv_pem)
    os.chmod(PRIVATE_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)

    # Write public key
    PUBLIC_KEY_PATH.write_text(identity.pubkey_hex + "\n", encoding="utf-8")

    # Write metadata
    import datetime as dt
    metadata = {
        "pubkey_hex": identity.pubkey_hex,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "version": 1,
    }
    METADATA_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return identity


def load_identity() -> Identity:
    """Load identity from default config path."""
    if not PRIVATE_KEY_PATH.exists():
        raise FileNotFoundError(
            f"No identity at {PRIVATE_KEY_PATH}. Run `daimon init` first."
        )
    pem = PRIVATE_KEY_PATH.read_bytes()
    priv = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise ValueError("identity key is not ed25519")
    pub = priv.public_key()
    pub_bytes = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return Identity(
        private_key=priv,
        public_key=pub,
        public_bytes=pub_bytes,
        pubkey_hex=pub_bytes.hex(),
    )


def restore_from_mnemonic(mnemonic: str, force: bool = False) -> Identity:
    """Restore identity from a BIP39 mnemonic. Writes to default config path."""
    from daimon.identity.bip39 import seed_from_mnemonic

    _ensure_config_dir()
    if PRIVATE_KEY_PATH.exists() and not force:
        raise FileExistsError(
            f"Identity exists at {PRIVATE_KEY_PATH}. Pass force=True to overwrite."
        )

    seed = seed_from_mnemonic(mnemonic)
    identity = _seed_to_identity(seed, mnemonic=mnemonic)

    priv_pem = identity.private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    PRIVATE_KEY_PATH.write_bytes(priv_pem)
    os.chmod(PRIVATE_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)
    PUBLIC_KEY_PATH.write_text(identity.pubkey_hex + "\n", encoding="utf-8")

    import datetime as dt
    metadata = {
        "pubkey_hex": identity.pubkey_hex,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "restored_from_mnemonic": True,
        "version": 1,
    }
    METADATA_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return identity


def sign(identity: Identity, data: bytes) -> bytes:
    return identity.sign_bytes(data)


def verify(pubkey_hex: str, data: bytes, signature: bytes) -> bool:
    """Verify signature against a hex-encoded pubkey. Never raises on bad sig."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        pub.verify(signature, data)
        return True
    except Exception:
        return False
