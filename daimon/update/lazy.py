"""Lazy per-card art fetcher.

The runtime calls :func:`ensure_art_for` (or :func:`fetch_card` directly,
when the caller wants exceptions). Both consult the on-disk
:class:`daimon.update.manifest.Manifest` for the asset URL + sha256 of
the requested ``card_id`` and download the per-card tarball to
``art_pack_dir(pack_name) / <card_id> /`` if it isn't already there.

Each per-card tarball is small (~50–500 KB) and ships a flat layout::

    base.png
    manifest.json            # the card manifest, NOT the pack manifest
    variants/v0.png
    variants/v1.png
    ...

The lazy fetcher extracts a tarball into a unique staging dir, then
atomic-swaps it into ``art/<pack>/<card_id>/`` using the same
two-syscall pattern as the legacy pack-level swap. Concurrent fetches
of the same card_id (rare in practice — only two CLI invocations
rendering the same brand-new card simultaneously) waste bandwidth but
never corrupt the cache: each caller has its own staging dir and the
last rename wins.

Entry points::

    ensure_art_for(card_id)            # returns Path or None — never raises
    fetch_card(card_id, *, manifest)   # raises ArtUpdateError on failure
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional
from urllib.error import HTTPError, URLError

from daimon.update.fetcher import (
    ArtUpdateError,
    _http_open,
    atomic_swap,
    download_with_progress,
    safe_extract_tarball,
    sha256_file,
)
from daimon.update.manifest import (
    Manifest,
    load_manifest,
)
from daimon.update.paths import (
    ART_PACK_NAME,
    art_pack_dir,
    staging_dir,
)


# ---------------------------------------------------------------------------
# Cache predicate
# ---------------------------------------------------------------------------

def is_card_cached(
    card_id: str,
    *,
    pack_name: str = ART_PACK_NAME,
    art_root: Optional[Path] = None,
) -> bool:
    """True iff ``art/<pack>/<card_id>/`` exists with at least one PNG.

    Mirrors the contract of :func:`daimon.render.art.art_path_for` —
    a card directory with no PNG isn't useful, so we treat it as a
    cache miss. (Half-finished extractions can't actually leave that
    state thanks to the atomic-swap pattern; the check is defensive.)
    """
    root = art_root if art_root is not None else art_pack_dir(pack_name)
    card_dir = root / card_id
    if not card_dir.is_dir():
        return False
    if (card_dir / "base.png").is_file():
        return True
    variants = card_dir / "variants"
    if variants.is_dir():
        try:
            for entry in variants.iterdir():
                if entry.suffix.lower() == ".png" and entry.is_file():
                    return True
        except OSError:
            return False
    return False


# ---------------------------------------------------------------------------
# Per-card fetch
# ---------------------------------------------------------------------------

def _unique_staging(card_id: str) -> Path:
    """Pick a unique staging dir for one extraction.

    Uses PID + a monotonic counter so concurrent fetches of the same
    card never collide on the staging path. The dir is removed by the
    caller on success and left in place on crash (the cache mop-up
    runs on next startup — see :func:`cleanup_card_staging`).
    """
    base = staging_dir() / "cards"
    base.mkdir(parents=True, exist_ok=True)
    counter = 0
    while True:
        candidate = base / f"{card_id}.{os.getpid()}.{counter}"
        if not candidate.exists():
            return candidate
        counter += 1


_REPO_FROM_BASE_URL = re.compile(
    r"^https://github\.com/([^/]+/[^/]+)/releases/"
)


@functools.lru_cache(maxsize=8)
def _release_asset_api_urls(repo: str, tag: str) -> Dict[str, str]:
    """Fetch the release's full asset list once, return ``{name: api_url}``.

    Required for private-repo card downloads — GitHub refuses bearer auth
    on ``browser_download_url`` and only accepts the
    ``api.github.com/.../releases/assets/<id>`` + ``Accept: application/octet-stream``
    pattern. We pay one API call per (repo, tag) per process and cache
    the mapping; subsequent card fetches reuse it.

    Returns ``{}`` on any failure so callers fall back to the browser
    URL (which works for public repos with no token).
    """
    try:
        from daimon.update.api import _gh_get

        rel = _gh_get(f"/repos/{repo}/releases/tags/{tag}")
    except (HTTPError, URLError, OSError, ValueError):
        return {}
    if not isinstance(rel, dict):
        return {}
    out: Dict[str, str] = {}
    for asset in rel.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("url")
        if isinstance(name, str) and isinstance(url, str):
            out[name] = url
    return out


def _resolve_card_url(manifest: Manifest, asset_name: str) -> tuple[str, bool]:
    """Pick the right card download URL based on auth state.

    With a token we always prefer the API URL (private + public repos
    both work). Without a token we fall back to the manifest-constructed
    browser_download_url (public-repo path).

    Returns ``(url, octet_stream)``.
    """
    has_token = bool(
        os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    )
    if has_token:
        m_repo = _REPO_FROM_BASE_URL.match(manifest.asset_base_url)
        if m_repo:
            cache = _release_asset_api_urls(m_repo.group(1), manifest.pack_version)
            api_url = cache.get(asset_name)
            if api_url:
                return api_url, True
    # Fall back to browser_download_url (public-repo path).
    base = manifest.asset_base_url
    if not base.endswith("/"):
        base = base + "/"
    return base + asset_name, False


def cleanup_card_staging() -> None:
    """Sweep abandoned per-card staging dirs from prior crashed fetches."""
    base = staging_dir() / "cards"
    if not base.is_dir():
        return
    for child in base.iterdir():
        # Best-effort. Anything we can't read or remove is left alone — a
        # later run, or the user, can clean it up.
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_file():
                child.unlink()
        except OSError:
            pass


def fetch_card(
    card_id: str,
    *,
    manifest: Optional[Manifest] = None,
    pack_name: str = ART_PACK_NAME,
    show_progress: bool = False,
) -> Path:
    """Download + verify + extract the art for one card. Returns the live dir.

    Args:
        card_id: card identifier from the pack manifest.
        manifest: pre-loaded manifest. If ``None``, loads from disk; raises
            :class:`ArtUpdateError` if no manifest is installed.
        pack_name: which pack the card belongs to. Defaults to ``v1_alpha``.
        show_progress: print a one-liner to stderr while downloading. Off by
            default — chatty for normal renderer cache misses, useful for
            the explicit ``daimon prefetch`` command.

    Idempotent: if the card directory already contains art (per
    :func:`is_card_cached`), returns immediately without a network call.

    Raises:
        ArtUpdateError: manifest missing, card unknown, download or verify
            failed, extraction unsafe.
    """
    m = manifest if manifest is not None else load_manifest(pack_name)
    if m is None:
        raise ArtUpdateError(
            f"no manifest installed for pack {pack_name!r}; "
            "run `daimon onboard` or `daimon update` first"
        )

    if card_id not in m.cards:
        raise ArtUpdateError(
            f"card {card_id!r} is not in manifest {m.pack_version!r}"
        )

    live = art_pack_dir(pack_name) / card_id
    if is_card_cached(card_id, pack_name=pack_name):
        return live

    entry = m.cards[card_id]
    url, octet = _resolve_card_url(m, entry.asset_name)
    staging = _unique_staging(card_id)

    try:
        # Download tarball. octet=True means we resolved a private-repo
        # API URL above; octet=False means the manifest-constructed
        # browser_download_url (public-repo path).
        tarball = staging.with_suffix(".tar.gz")
        staging.parent.mkdir(parents=True, exist_ok=True)
        download_with_progress(
            url,
            tarball,
            expected_size=entry.size_bytes,
            label=f"daimon: fetching art for {card_id}",
            show_progress=show_progress,
            octet_stream=octet,
        )
        actual = sha256_file(tarball)
        if actual.lower() != entry.sha256.lower():
            try:
                tarball.unlink()
            except OSError:
                pass
            raise ArtUpdateError(
                f"sha256 mismatch on card {card_id!r}: "
                f"expected {entry.sha256}, got {actual}"
            )

        # Extract into a fresh dir.
        extract_dir = staging
        extract_dir.mkdir(parents=True, exist_ok=True)
        safe_extract_tarball(tarball, extract_dir)
        try:
            tarball.unlink()
        except OSError:
            pass

        # Identify the extracted card dir. The build script produces
        # tarballs whose contents are flat (base.png, manifest.json,
        # variants/) so the extraction lands directly under extract_dir.
        # As a defense-in-depth, also accept tarballs that wrap their
        # files in a single ``<card_id>/`` directory.
        if (extract_dir / "base.png").is_file() or (extract_dir / "variants").is_dir():
            staged_card = extract_dir
        else:
            nested = extract_dir / card_id
            if nested.is_dir():
                staged_card = nested
            else:
                raise ArtUpdateError(
                    f"card tarball for {card_id!r} has unexpected layout: "
                    f"no base.png, variants/, or {card_id}/ at top level"
                )

        atomic_swap(staged_card, live)
    finally:
        # Clean up everything else under staging — the swap moved the
        # one dir we wanted and shutil.rmtree handles partials safely.
        shutil.rmtree(staging, ignore_errors=True)

    return live


# ---------------------------------------------------------------------------
# Soft-fail wrapper for renderers
# ---------------------------------------------------------------------------

def ensure_art_for(
    card_id: str,
    *,
    pack_name: str = ART_PACK_NAME,
    show_progress: bool = False,
) -> Optional[Path]:
    """Resolve a card's live dir, fetching on cache miss. Soft-fails on errors.

    Renderers should prefer this over :func:`fetch_card` so a transient
    network blip doesn't crash a match — they'll render the placeholder
    and continue. The CLI surfaces a one-line warning to stderr when the
    miss happens but the fetch fails.

    Returns:
        Path to the card directory if the art is on disk (or just landed
        there), or ``None`` if the card isn't in the manifest, no manifest
        is installed, or the fetch failed and nothing was cached.
    """
    if is_card_cached(card_id, pack_name=pack_name):
        return art_pack_dir(pack_name) / card_id

    try:
        return fetch_card(
            card_id,
            pack_name=pack_name,
            show_progress=show_progress,
        )
    except ArtUpdateError as e:
        sys.stderr.write(f"daimon: art fetch for {card_id!r} failed: {e}\n")
        sys.stderr.flush()
        return None
    except (HTTPError, URLError, OSError) as e:
        sys.stderr.write(
            f"daimon: art fetch for {card_id!r} hit a network/IO error: {e}\n"
        )
        sys.stderr.flush()
        return None


__all__ = [
    "is_card_cached",
    "fetch_card",
    "ensure_art_for",
    "cleanup_card_staging",
]
