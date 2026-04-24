"""DAIMON art-pack auto-update.

Handles download, verification, atomic install, and rate-limited update
checks for the binary art-pack shipped via GitHub Releases on
``aurorasuperbot/daimon-cards`` (``art-vX.Y`` tags).

Public entry points (importable from ``daimon.update``):

  * ``ensure_art_available(blocking=False)`` — call from CLI startup.
    First-run: synchronous download + extract.
    Subsequent runs: rate-limited background check (returns immediately).
  * ``do_update(target_version=None, force=False)`` — explicit refresh,
    used by ``daimon update`` CLI command.
  * ``current_version()`` / ``art_root()`` — introspection helpers.

Architecture and env vars are documented in the package's submodules:

  * ``daimon.update.paths``   — CONFIG/DATA dir resolution + env overrides
  * ``daimon.update.api``     — GitHub Releases API client
  * ``daimon.update.fetcher`` — download + checksum + atomic swap
  * ``daimon.update.checker`` — rate-limited check + background spawn

Env vars:
  * ``DAIMON_NO_AUTO_UPDATE=1``   opt out of background checks entirely
  * ``DAIMON_PIN_ART=art-v1.0``   refuse to auto-upgrade past this tag
  * ``DAIMON_UPDATE_CHECK_HOURS`` override the 24h check rate-limit
  * ``DAIMON_ART_DIR``            override art root dir
  * ``DAIMON_ART_REPO``           override the GH repo (default: aurorasuperbot/daimon-cards)
  * ``GITHUB_TOKEN``              use for higher API rate limit / private repo auth
"""

from __future__ import annotations

from daimon.update.checker import ensure_art_available, spawn_background_check
from daimon.update.fetcher import do_update, ArtUpdateError
from daimon.update.paths import (
    art_root,
    art_pack_dir,
    current_version,
    expected_checksum,
    cache_dir,
    last_check_path,
)

__all__ = [
    "ensure_art_available",
    "spawn_background_check",
    "do_update",
    "ArtUpdateError",
    "art_root",
    "art_pack_dir",
    "current_version",
    "expected_checksum",
    "cache_dir",
    "last_check_path",
]
