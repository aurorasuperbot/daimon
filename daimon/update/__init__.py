"""DAIMON art-pack auto-update — lazy per-card fetcher.

Handles release-tag resolution, manifest fetch, per-card lazy download,
and rate-limited update checks against the cards repo at
``aurorasuperbot/daimon-cards`` (``art-vX.Y`` tags).

The runtime never downloads the entire 1.6 GB pack at once. Onboard
fetches a small ``.manifest.json`` (~50-100 KB), and each card's PNG
bytes land on disk the first time the renderer asks for that card.

Public entry points (importable from ``daimon.update``):

  * ``ensure_art_available(blocking=False)`` — CLI startup hook.
    First-run: synchronous manifest fetch (small, fast).
    Subsequent runs: rate-limited background check (returns immediately).
  * ``fetch_manifest(target_version=None, force=False)`` — explicit
    manifest refresh, used by ``daimon update``.
  * ``ensure_art_for(card_id)`` — per-card lazy fetch wrapper used by
    every renderer; soft-fails to ``None`` so a transient network blip
    never crashes a match.
  * ``fetch_card(card_id)`` — strict per-card fetch (raises
    :class:`ArtUpdateError` on failure); used by the explicit prefetcher.
  * ``current_version()`` / ``art_root()`` — introspection helpers.

Architecture lives in the submodules:

  * ``daimon.update.paths``    — CONFIG/DATA dir resolution + env overrides
  * ``daimon.update.api``      — GitHub Releases API client
  * ``daimon.update.fetcher``  — HTTP + sha256 + extraction + atomic-swap primitives
  * ``daimon.update.manifest`` — pack manifest data model + fetcher
  * ``daimon.update.lazy``     — per-card JIT fetcher + ``ensure_art_for``
  * ``daimon.update.checker``  — rate-limited check + background spawn

Env vars:
  * ``DAIMON_NO_AUTO_UPDATE=1``   opt out of network calls entirely
  * ``DAIMON_PIN_ART=art-v1.0``   refuse to auto-upgrade past this tag
  * ``DAIMON_UPDATE_CHECK_HOURS`` override the 24h check rate-limit
  * ``DAIMON_ART_DIR``            override art root dir
  * ``DAIMON_ART_REPO``           override the GH repo (default: aurorasuperbot/daimon-cards)
  * ``GITHUB_TOKEN``              higher API rate limit / private repo auth
"""

from __future__ import annotations

from daimon.update.checker import ensure_art_available, spawn_background_check
from daimon.update.fetcher import ArtUpdateError
from daimon.update.lazy import (
    ensure_art_for,
    fetch_card,
    is_card_cached,
)
from daimon.update.manifest import (
    Manifest,
    ManifestDiff,
    diff_manifests,
    fetch_manifest,
    load_manifest,
)
from daimon.update.paths import (
    art_pack_dir,
    art_root,
    cache_dir,
    current_version,
    expected_checksum,
    last_check_path,
    manifest_path,
)

__all__ = [
    "ensure_art_available",
    "spawn_background_check",
    "ArtUpdateError",
    "fetch_manifest",
    "load_manifest",
    "Manifest",
    "ManifestDiff",
    "diff_manifests",
    "ensure_art_for",
    "fetch_card",
    "is_card_cached",
    "art_root",
    "art_pack_dir",
    "current_version",
    "expected_checksum",
    "cache_dir",
    "last_check_path",
    "manifest_path",
]
