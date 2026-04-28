"""Path resolution for the art-pack data dir + cache dir.

Layout (default, under ``$HOME``):

    ~/.daimon/                          DAIMON_ART_DIR root
      art/v1_alpha/                     art_pack_dir() — the live pack
        .version                          "art-v1.0"
        .checksum                         "<sha256>  manifest.json"
        .manifest.json                    manifest_path() — pack index
        <card_id>/                        populated lazily on demand
          base.png
          manifest.json
          variants/v0.png
          variants/v1.png ...
      cache/                            cache_dir() — staging + last-check
        last_check.json                   {ts, latest_seen, ...}
        prefetch_state.json               progress of background prefetcher
        staging/                          per-card download + extract scratch
          card_<card_id>.tar.gz.partial
          card_<card_id>_unpacked/...

Path precedence:
  1. ``DAIMON_ART_DIR`` env var — explicit override (used in tests, CI)
  2. ``XDG_DATA_HOME/daimon`` — XDG basedir spec
  3. ``~/.daimon`` — DAIMON-specific fallback (matches inbox dir convention)

Resolution happens lazily on each call (``art_root()``), NOT at import
time, because tests monkeypatch the env var. Callers that need a fixed
path within a single operation should snapshot the result.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


# Hard-coded for V1 alpha. When V2 ships a new pack format we'll bump this
# and the engine will refuse to load mismatched packs.
ART_PACK_NAME = "v1_alpha"

# Env-controlled, but defaulted here so tests can monkeypatch.
DEFAULT_ART_REPO = "aurorasuperbot/daimon-cards"
DEFAULT_ART_TAG_PREFIX = "art-v"
DEFAULT_ART_ASSET_NAME = "v1_alpha.tar.gz"

# Engine-side compat: refuse to auto-pull a major-version bump.
# Bump COMPAT_ART_MAJOR when a release ships a breaking pack format.
COMPAT_ART_MAJOR = 1

# Default rate-limit between background update checks. 24h is enough that
# 99% of CLI invocations do zero network work.
DEFAULT_CHECK_INTERVAL_HOURS = 24


def art_root() -> Path:
    """Resolve the DAIMON data root (parent of art/ and cache/).

    Precedence (first match wins):
      1. ``DAIMON_ART_DIR`` — explicit override
      2. ``XDG_DATA_HOME/daimon`` — XDG basedir spec
      3. ``~/.daimon`` — DAIMON-specific fallback (matches inbox dir)
    """
    env = os.environ.get("DAIMON_ART_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "daimon"
    return Path.home() / ".daimon"


def art_pack_dir(pack_name: str = ART_PACK_NAME) -> Path:
    """Live art pack dir: ``<art_root>/art/<pack_name>/``."""
    return art_root() / "art" / pack_name


def cache_dir() -> Path:
    """Cache + staging dir: ``<art_root>/cache/``."""
    return art_root() / "cache"


def staging_dir() -> Path:
    """Download/extract staging dir: ``<art_root>/cache/staging/``."""
    return cache_dir() / "staging"


def last_check_path() -> Path:
    """Cached update-check timestamp: ``<art_root>/cache/last_check.json``."""
    return cache_dir() / "last_check.json"


def update_log_path() -> Path:
    """Background update log: ``<art_root>/cache/update.log``."""
    return cache_dir() / "update.log"


def version_file(pack_name: str = ART_PACK_NAME) -> Path:
    return art_pack_dir(pack_name) / ".version"


def checksum_file(pack_name: str = ART_PACK_NAME) -> Path:
    return art_pack_dir(pack_name) / ".checksum"


def manifest_path(pack_name: str = ART_PACK_NAME) -> Path:
    """Pack index: ``art/<pack>/.manifest.json``.

    The manifest enumerates every card in the pack, keyed by card_id, with a
    per-card sha256 + asset name. It's the small (~50–100 KB) file fetched
    on first run; per-card art (PNG, manifest, variants) is downloaded
    lazily on demand against the entries here.
    """
    return art_pack_dir(pack_name) / ".manifest.json"


def prefetch_state_path() -> Path:
    """Progress file for the background prefetcher: ``cache/prefetch_state.json``.

    Persisted so a re-spawned prefetcher resumes where the previous run left
    off rather than re-fetching cards already on disk.
    """
    return cache_dir() / "prefetch_state.json"


def current_version(pack_name: str = ART_PACK_NAME) -> Optional[str]:
    """Read the installed pack version from ``art/<pack>/.version``.

    Returns the bare tag string (e.g. ``"art-v1.0"``) or ``None`` if no
    pack is installed.
    """
    p = version_file(pack_name)
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def expected_checksum(pack_name: str = ART_PACK_NAME) -> Optional[str]:
    """Read the expected sha256 of the installed pack tarball.

    File format matches ``sha256sum`` output: ``<hex>  <filename>``.
    Returns the bare hex digest or ``None`` if missing/malformed.
    """
    p = checksum_file(pack_name)
    if not p.is_file():
        return None
    try:
        line = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line:
        return None
    # Accept either "<hex>  <name>" (sha256sum format) or bare "<hex>".
    parts = line.split()
    digest = parts[0]
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest.lower()):
        return None
    return digest.lower()


def parse_version_tag(tag: str, prefix: str) -> Optional[tuple[int, int]]:
    """Parse ``<prefix>X.Y`` → ``(X, Y)``. Returns None on malformed input.

    Also accepts ``<prefix>X`` as ``(X, 0)`` for forward-compat. Generic
    over the prefix so it handles both art releases (``art-v``) and the
    WezTerm bundle releases (``wezterm-bundle-v``).
    """
    if not tag.startswith(prefix):
        return None
    rest = tag[len(prefix):]
    parts = rest.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return major, minor
    except (ValueError, IndexError):
        return None


def parse_art_version(tag: str) -> Optional[tuple[int, int]]:
    """Parse ``art-vX.Y`` → ``(X, Y)``. Returns None on malformed input.

    Thin wrapper preserved for back-compat — call sites that know they're
    parsing art-pack tags can keep the bare name; call sites that need
    bundle tags should call :func:`parse_version_tag` directly.
    """
    return parse_version_tag(tag, DEFAULT_ART_TAG_PREFIX)


def art_repo() -> str:
    """GitHub repo (``owner/name``) for the art-pack releases.

    Override via ``DAIMON_ART_REPO`` (used in tests, forks, CI).
    """
    return os.environ.get("DAIMON_ART_REPO") or DEFAULT_ART_REPO


def update_check_interval_hours() -> float:
    """Hours between background update checks. Override via env var."""
    raw = os.environ.get("DAIMON_UPDATE_CHECK_HOURS")
    if raw:
        try:
            v = float(raw)
            if v >= 0:
                return v
        except ValueError:
            pass
    return float(DEFAULT_CHECK_INTERVAL_HOURS)


def auto_update_enabled() -> bool:
    """``False`` if user opted out via ``DAIMON_NO_AUTO_UPDATE=1``."""
    return os.environ.get("DAIMON_NO_AUTO_UPDATE", "").strip() not in ("1", "true", "yes")


def pinned_version() -> Optional[str]:
    """Returns ``art-vX.Y`` if user pinned a specific version, else ``None``."""
    v = os.environ.get("DAIMON_PIN_ART", "").strip()
    return v or None
