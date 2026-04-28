"""GitHub Releases API client for art-pack lookups.

Stdlib-only (urllib.request) — we don't want to drag `requests` into a
core dep just for two HTTP calls per day.

Public surface:
  * ``ReleaseInfo`` — dataclass describing one release we can install.
  * ``gh_latest_release(repo, tag_prefix)`` — newest matching release, or None.
  * ``gh_release_by_tag(repo, tag)`` — explicit tag lookup (for pinning).

Auth: if ``GITHUB_TOKEN`` is set, send it as a Bearer token. Lifts the
anonymous rate-limit (60/hr → 5000/hr) and lets private repos work for
maintainers/CI without flipping public.

Errors are surfaced via ``ArtUpdateError`` (defined in fetcher.py and
re-exported through the package); this module raises ``URLError`` /
``HTTPError`` from urllib and lets callers decide whether to swallow.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from daimon.update.paths import (
    DEFAULT_ART_ASSET_NAME,
    DEFAULT_ART_TAG_PREFIX,
    parse_art_version,
    parse_version_tag,
)


GH_API_BASE = "https://api.github.com"
USER_AGENT = "daimon-update/1.0 (+https://github.com/aurorasuperbot/daimon)"
HTTP_TIMEOUT = 30  # seconds


@dataclass(frozen=True)
class ReleaseInfo:
    """Subset of a GitHub Release JSON we need to install one art pack.

    Attributes:
        tag: release tag (e.g. ``"art-v1.0"``).
        version: parsed ``(major, minor)`` tuple. None if tag is malformed.
        published_at: ISO-8601 timestamp string from the GH API.
        asset_url: ``browser_download_url`` for the tarball — works for
            public repos with no auth.
        asset_api_url: ``url`` field for the tarball — the API endpoint.
            Required for private-repo downloads (with ``Accept:
            application/octet-stream`` + bearer token).
        asset_size: tarball size in bytes (for progress bars / sanity check).
        sha256_url: ``browser_download_url`` for the ``<asset>.sha256``
            sidecar, or None if the release didn't ship one.
        sha256_api_url: ``url`` field for the sidecar (private-repo path).
        body: full release notes (markdown). Used to extract the sha256 if
            no sidecar is published.
    """
    tag: str
    version: Optional[tuple[int, int]]
    published_at: str
    asset_url: str
    asset_api_url: str
    asset_size: int
    sha256_url: Optional[str]
    sha256_api_url: Optional[str]
    body: str


def _auth_headers() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _gh_get(path: str) -> dict | list:
    """GET ``GH_API_BASE/path``. Returns parsed JSON. Raises HTTPError/URLError."""
    url = f"{GH_API_BASE}{path}"
    req = Request(url, headers=_auth_headers())
    with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _to_release_info(
    rel: dict,
    asset_name: str,
    tag_prefix: str = DEFAULT_ART_TAG_PREFIX,
) -> Optional[ReleaseInfo]:
    """Map a GH release JSON → ReleaseInfo, or None if the asset is missing.

    A release without our expected tarball asset is unusable (it might be
    a doc-only release, a draft, etc.); skip it. ``tag_prefix`` lets the
    bundle path (``wezterm-bundle-v``) parse its version correctly — the
    art path keeps the default and is backwards-compatible.
    """
    tag = rel.get("tag_name", "")
    if not tag:
        return None

    assets = rel.get("assets") or []
    tarball = next((a for a in assets if a.get("name") == asset_name), None)
    if not tarball:
        return None

    sidecar = next(
        (a for a in assets if a.get("name") == f"{asset_name}.sha256"),
        None,
    )

    return ReleaseInfo(
        tag=tag,
        version=parse_version_tag(tag, tag_prefix),
        published_at=rel.get("published_at") or "",
        asset_url=tarball.get("browser_download_url") or "",
        asset_api_url=tarball.get("url") or "",
        asset_size=int(tarball.get("size") or 0),
        sha256_url=(sidecar.get("browser_download_url") if sidecar else None),
        sha256_api_url=(sidecar.get("url") if sidecar else None),
        body=rel.get("body") or "",
    )


def gh_latest_release(
    repo: str,
    tag_prefix: str = DEFAULT_ART_TAG_PREFIX,
    asset_name: str = DEFAULT_ART_ASSET_NAME,
) -> Optional[ReleaseInfo]:
    """Find the highest-versioned ``<tag_prefix>X.Y`` release on ``repo``.

    Why list-and-filter instead of ``/releases/latest``? GitHub's "latest"
    endpoint returns the most-recently-published *non-prerelease* release,
    which conflates "published yesterday" with "highest version". For an
    art-pack we want highest semver — re-publishing a corrected ``art-v1.0``
    after ``art-v1.1`` shouldn't downgrade clients.

    Returns ``None`` if no parseable release exists. Raises ``URLError`` /
    ``HTTPError`` on network/auth failure — caller decides what to do.
    """
    payload = _gh_get(f"/repos/{repo}/releases?per_page=30")
    if not isinstance(payload, list):
        return None

    candidates: list[ReleaseInfo] = []
    for rel in payload:
        if not isinstance(rel, dict):
            continue
        if rel.get("draft"):
            continue
        tag = rel.get("tag_name", "")
        if not tag.startswith(tag_prefix):
            continue
        info = _to_release_info(rel, asset_name, tag_prefix=tag_prefix)
        if info and info.version is not None:
            candidates.append(info)

    if not candidates:
        return None

    candidates.sort(key=lambda r: r.version or (0, 0))
    return candidates[-1]


def gh_release_by_tag(
    repo: str,
    tag: str,
    asset_name: str = DEFAULT_ART_ASSET_NAME,
    tag_prefix: str = DEFAULT_ART_TAG_PREFIX,
) -> Optional[ReleaseInfo]:
    """Fetch one release by its exact tag (e.g. for ``DAIMON_PIN_ART``).

    Returns ``None`` if the tag exists but lacks the expected asset, or if
    the API returns 404. Other HTTP errors propagate.
    """
    try:
        payload = _gh_get(f"/repos/{repo}/releases/tags/{tag}")
    except HTTPError as e:
        if e.code == 404:
            return None
        raise
    if not isinstance(payload, dict):
        return None
    return _to_release_info(payload, asset_name, tag_prefix=tag_prefix)


__all__ = [
    "ReleaseInfo",
    "gh_latest_release",
    "gh_release_by_tag",
    "HTTPError",
    "URLError",
]
