"""Local secret state for in-flight PvP matches.

When a player runs ``dm_pvp_challenge`` or ``dm_pvp_accept`` they commit
the SHA-256 of their loadout to a public Issue. The plaintext loadout +
nonce can't be revealed in the same call (then there'd be nothing to
"reveal" later) — so we stash them locally, keyed by issue number, until
the player runs ``dm_pvp_reveal``.

Storage layout::

    ~/.daimon/pvp_state/<issue_number>.json
    {
      "issue_number": 42,
      "side": "challenger" | "responder",
      "nonce": "<64 hex chars>",
      "loadout": { "cards": [...] },
      "pubkey_hex": "<owner>",
      "opponent_pubkey": "<other side, if known>",
      "created_at": "ISO-8601 UTC"
    }

The directory is mode 0700 and individual files mode 0600. The nonce is
a one-time secret — leaking it before reveal lets an opponent compute
your loadout from your commit hash. Treat with the same care as the
private key (which is in a sibling directory at ``~/.config/daimon``).

Cleanup: state files persist after a match resolves. ``cleanup_resolved()``
prunes them once the corresponding match record exists in
``daimon-arena/matches/``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional


def _resolve_state_dir() -> Path:
    """Resolve the PvP state directory.

    Precedence:
      1. ``DAIMON_INBOX`` env var (matches the existing inbox-dir override
         pattern used by the play module)
      2. ``~/.daimon/pvp_state``
    """
    env = os.environ.get("DAIMON_INBOX")
    if env:
        return Path(env).expanduser() / "pvp_state"
    return Path.home() / ".daimon" / "pvp_state"


PVP_STATE_DIR = _resolve_state_dir()


def _ensure_dir() -> None:
    PVP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(PVP_STATE_DIR, 0o700)
    except OSError:
        # Best-effort — on shared filesystems chmod can fail silently.
        pass


def _path_for(issue_number: int) -> Path:
    return PVP_STATE_DIR / f"{issue_number}.json"


def save(issue_number: int,
         side: str,
         nonce: str,
         loadout: Any,
         pubkey_hex: str,
         opponent_pubkey: Optional[str] = None) -> Path:
    """Persist secret state for an in-flight match.

    Args:
      issue_number: arena Issue number (also the match id).
      side: ``"challenger"`` if we opened the Issue, else ``"responder"``.
      nonce: 64-char hex (32 bytes random).
      loadout: full loadout dict ``{"cards": [...]}``.
      pubkey_hex: our pubkey (sanity check on load).
      opponent_pubkey: known after accept, optional at challenge time.

    Returns the path written. Overwrites any existing file (a player can
    re-issue a challenge if they cancel the first one).
    """
    if side not in ("challenger", "responder"):
        raise ValueError(f"side must be 'challenger' or 'responder', got {side!r}")
    _ensure_dir()
    path = _path_for(issue_number)
    record = {
        "issue_number": int(issue_number),
        "side": side,
        "nonce": nonce,
        "loadout": loadout,
        "pubkey_hex": pubkey_hex,
        "opponent_pubkey": opponent_pubkey,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(record, indent=2, sort_keys=True),
                    encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def load(issue_number: int) -> Optional[Dict[str, Any]]:
    """Load secret state for an in-flight match, or ``None`` if missing."""
    path = _path_for(issue_number)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def delete(issue_number: int) -> bool:
    """Remove secret state for a resolved (or cancelled) match.

    Returns True if a file was removed, False if nothing was there.
    """
    path = _path_for(issue_number)
    if path.is_file():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False


def list_pending() -> list:
    """List issue numbers we have local secret state for."""
    if not PVP_STATE_DIR.is_dir():
        return []
    out = []
    for p in PVP_STATE_DIR.iterdir():
        if p.suffix == ".json" and p.stem.isdigit():
            out.append(int(p.stem))
    return sorted(out)
