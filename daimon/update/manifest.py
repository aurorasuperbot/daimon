"""Art-pack manifest — per-card index used by the lazy fetcher.

A manifest is a small JSON document (typically ~50-100 KB for the V1 pack
of 200 cards) listing every card in a release by ``card_id`` along with
the per-card asset name and sha256. It is what the runtime fetches on
first-run (or after a release-tag bump) and persists at
``art/<pack>/.manifest.json``. Per-card art is then fetched on demand via
:func:`daimon.update.lazy.fetch_card`.

Schema v1::

    {
      "schema_version": 1,
      "pack_version": "art-v1.0",         // GitHub Release tag
      "pack_name":    "v1_alpha",         // ART_PACK_NAME
      "asset_base_url": "https://github.com/<repo>/releases/download/<tag>/",
      "card_count":   200,
      "starter_card_ids": ["voltcat_apex", ...],   // ~10 IDs prefetched at onboard
      "cards": {
        "voltcat_apex": {
          "asset_name": "card_voltcat_apex.tar.gz",
          "sha256":     "abc123...64hex",
          "size_bytes": 65432
        },
        ...
      }
    }

Why a manifest at all instead of constructing per-card URLs by convention?

  * **Verifiability.** The runtime must verify per-card downloads against a
    sha256 that itself comes from a trusted source. Putting the digests in
    the manifest (which is itself sha-verified via a sidecar) gives every
    per-card download integrity without an extra HTTP round-trip per card.

  * **Incremental updates.** Comparing two manifests by ``cards[id].sha256``
    tells the runtime exactly which cards changed between releases. Only
    those need re-fetching; everything else stays cached.

  * **Forward-compat.** The manifest can be served from a CDN later
    without changing the runtime — only ``asset_base_url`` shifts.

The build script that produces a manifest lives at
``scripts/build_art_manifest.py``. It is the only writer; runtime code
only ever reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional
from urllib.error import HTTPError, URLError

from daimon.update import api
from daimon.update.fetcher import (
    ArtUpdateError,
    _pick_asset_url,
    download_with_progress,
    fetch_expected_sha256,
    sha256_file,
)
from daimon.update.paths import (
    ART_PACK_NAME,
    COMPAT_ART_MAJOR,
    art_repo,
    checksum_file,
    manifest_path,
    pinned_version,
    staging_dir,
    version_file,
)


SCHEMA_VERSION = 1
MANIFEST_ASSET_NAME = "manifest.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardEntry:
    """One card's slot in the manifest."""
    asset_name: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "asset_name": self.asset_name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, object], *, card_id: str) -> "CardEntry":
        try:
            asset_name = str(d["asset_name"])
            sha256 = str(d["sha256"]).lower()
            size_bytes = int(d["size_bytes"])
        except (KeyError, TypeError, ValueError) as e:
            raise ArtUpdateError(
                f"manifest: card {card_id!r} entry malformed: {e}"
            ) from e
        if len(sha256) != 64 or not all(c in "0123456789abcdef" for c in sha256):
            raise ArtUpdateError(
                f"manifest: card {card_id!r} sha256 not 64-hex: {sha256!r}"
            )
        if size_bytes < 0:
            raise ArtUpdateError(
                f"manifest: card {card_id!r} size_bytes is negative: {size_bytes}"
            )
        if not asset_name or "/" in asset_name or "\\" in asset_name:
            raise ArtUpdateError(
                f"manifest: card {card_id!r} asset_name unsafe: {asset_name!r}"
            )
        return cls(asset_name=asset_name, sha256=sha256, size_bytes=size_bytes)


@dataclass(frozen=True)
class Manifest:
    """Parsed pack manifest. Immutable; serializable round-trip via JSON."""
    schema_version: int
    pack_version: str
    pack_name: str
    asset_base_url: str
    starter_card_ids: tuple[str, ...]
    cards: Dict[str, CardEntry]

    @property
    def card_count(self) -> int:
        return len(self.cards)

    def card_url(self, card_id: str) -> str:
        """Absolute URL for a card's tarball asset."""
        try:
            entry = self.cards[card_id]
        except KeyError as e:
            raise ArtUpdateError(f"manifest: unknown card_id {card_id!r}") from e
        base = self.asset_base_url
        if not base.endswith("/"):
            base = base + "/"
        return base + entry.asset_name

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "pack_version": self.pack_version,
            "pack_name": self.pack_name,
            "asset_base_url": self.asset_base_url,
            "card_count": self.card_count,
            "starter_card_ids": list(self.starter_card_ids),
            "cards": {cid: entry.to_dict() for cid, entry in sorted(self.cards.items())},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> "Manifest":
        try:
            schema_version = int(d["schema_version"])
        except (KeyError, TypeError, ValueError) as e:
            raise ArtUpdateError(f"manifest: missing/invalid schema_version: {e}") from e
        if schema_version != SCHEMA_VERSION:
            raise ArtUpdateError(
                f"manifest: schema_version {schema_version} unsupported "
                f"(this engine reads v{SCHEMA_VERSION}). Update daimon-engine."
            )

        try:
            pack_version = str(d["pack_version"])
            pack_name = str(d["pack_name"])
            asset_base_url = str(d["asset_base_url"])
            cards_raw = d["cards"]
        except KeyError as e:
            raise ArtUpdateError(f"manifest: missing field {e}") from e
        if not isinstance(cards_raw, Mapping):
            raise ArtUpdateError("manifest: cards must be an object")
        if not asset_base_url:
            raise ArtUpdateError("manifest: asset_base_url is empty")

        starter_raw = d.get("starter_card_ids", [])
        if not isinstance(starter_raw, list) or not all(isinstance(s, str) for s in starter_raw):
            raise ArtUpdateError("manifest: starter_card_ids must be a list of strings")
        starter = tuple(starter_raw)

        cards: Dict[str, CardEntry] = {}
        for card_id, entry_raw in cards_raw.items():
            if not isinstance(card_id, str):
                raise ArtUpdateError(f"manifest: non-string card_id key {card_id!r}")
            if not isinstance(entry_raw, Mapping):
                raise ArtUpdateError(f"manifest: card {card_id!r} entry not an object")
            cards[card_id] = CardEntry.from_dict(entry_raw, card_id=card_id)

        for cid in starter:
            if cid not in cards:
                raise ArtUpdateError(
                    f"manifest: starter_card_ids references unknown card {cid!r}"
                )

        return cls(
            schema_version=schema_version,
            pack_version=pack_version,
            pack_name=pack_name,
            asset_base_url=asset_base_url,
            starter_card_ids=starter,
            cards=cards,
        )

    @classmethod
    def from_json(cls, raw: str) -> "Manifest":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ArtUpdateError(f"manifest: not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise ArtUpdateError("manifest: top-level must be an object")
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Disk IO — load + atomic write
# ---------------------------------------------------------------------------

def load_manifest(pack_name: str = ART_PACK_NAME) -> Optional[Manifest]:
    """Read the on-disk manifest. Returns None if no manifest is installed.

    Validates the schema as it loads — a corrupt manifest raises
    :class:`ArtUpdateError` so the caller can refuse-and-redownload rather
    than silently render with stale digests.
    """
    p = manifest_path(pack_name)
    if not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ArtUpdateError(f"manifest: read failed: {e}") from e
    return Manifest.from_json(raw)


def write_manifest(m: Manifest, *, pack_name: Optional[str] = None) -> Path:
    """Atomically write the manifest to ``art/<pack>/.manifest.json``.

    Same tempfile + rename pattern as ``write_last_check`` — a crash
    mid-write never produces a half-baked file at the live path.
    """
    target_pack = pack_name or m.pack_name or ART_PACK_NAME
    p = manifest_path(target_pack)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(m.to_json() + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


# ---------------------------------------------------------------------------
# Network — fetch a manifest from a release
# ---------------------------------------------------------------------------

def _resolve_release(target_version: Optional[str], force: bool) -> api.ReleaseInfo:
    """Pick the release we'll fetch the manifest from.

    The :mod:`daimon.update.api` helpers describe one *primary* asset per
    release. For lazy art, the primary asset is the manifest (per-card
    tarballs are sibling assets fetched lazily, by URL convention from
    ``manifest.asset_base_url``). Pass ``asset_name=MANIFEST_ASSET_NAME``
    so the resolver populates ``ReleaseInfo`` with the manifest's URLs.

    Resolution order:
      1. Explicit ``target_version`` wins.
      2. ``DAIMON_PIN_ART`` next.
      3. Latest compatible release otherwise.
      4. Cross-major upgrades require ``force=True``.
    """
    repo = art_repo()
    pin = target_version or pinned_version()

    try:
        if pin:
            rel = api.gh_release_by_tag(repo, pin, asset_name=MANIFEST_ASSET_NAME)
            if rel is None:
                raise ArtUpdateError(
                    f"pinned version {pin!r} not found on {repo} "
                    f"(or {MANIFEST_ASSET_NAME!r} asset missing)"
                )
        else:
            rel = api.gh_latest_release(repo, asset_name=MANIFEST_ASSET_NAME)
            if rel is None:
                raise ArtUpdateError(
                    f"no compatible art-pack release with a "
                    f"{MANIFEST_ASSET_NAME!r} asset found on {repo}"
                )
    except (HTTPError, URLError) as e:
        raise ArtUpdateError(f"GitHub API error: {e}") from e

    if rel.version is None:
        raise ArtUpdateError(f"release tag {rel.tag!r} is not a valid art-vX.Y")

    major, _ = rel.version
    if major != COMPAT_ART_MAJOR and not force:
        raise ArtUpdateError(
            f"refusing auto-upgrade: release {rel.tag} is major v{major}, "
            f"engine supports major v{COMPAT_ART_MAJOR}. "
            "Update the engine first, or pass force=True."
        )

    return rel


def fetch_manifest(
    target_version: Optional[str] = None,
    *,
    force: bool = False,
    show_progress: bool = False,
    pack_name: str = ART_PACK_NAME,
) -> Manifest:
    """Resolve, download, verify, and persist the manifest for a release.

    Returns the parsed :class:`Manifest`. The manifest is written to
    ``art/<pack>/.manifest.json`` and ``.version`` + ``.checksum`` are
    refreshed alongside.

    Idempotent: if the on-disk manifest's ``pack_version`` already matches
    the resolved release tag, no network call is made beyond release
    resolution and the on-disk manifest is returned. Pass ``force=True``
    to skip the short-circuit (e.g. to repair a corrupt local copy).
    """
    # Short-circuit if the local manifest already matches the resolved tag.
    rel = _resolve_release(target_version, force)
    if not force:
        local = load_manifest(pack_name)
        if local is not None and local.pack_version == rel.tag:
            return local

    # The api resolver was invoked with asset_name=MANIFEST_ASSET_NAME so
    # ``rel.asset_url`` / ``rel.sha256_url`` already point at the manifest
    # and its sidecar (no second API call needed).
    asset_url, octet = _pick_asset_url(rel.asset_url, rel.asset_api_url)

    # ``fetch_expected_sha256`` checks the sidecar first then falls back
    # to the release body — both paths give us the manifest's digest.
    expected_sha = fetch_expected_sha256(rel)
    if expected_sha is None:
        raise ArtUpdateError(
            f"release {rel.tag} ships no sha256 for {MANIFEST_ASSET_NAME!r} — "
            f"refusing to install unverified manifest."
        )

    staging = staging_dir()
    staging.mkdir(parents=True, exist_ok=True)
    tmp_path = staging / f"manifest-{rel.tag}.json"

    # Stream download to a tempfile, sha-verify, then parse.
    download_with_progress(
        asset_url,
        tmp_path,
        expected_size=0,  # Manifest is small; we don't need a Content-Length-driven bar.
        label=f"daimon: fetching manifest for {rel.tag}",
        show_progress=show_progress,
        octet_stream=octet,
    )
    actual_sha = sha256_file(tmp_path)
    if actual_sha.lower() != expected_sha.lower():
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise ArtUpdateError(
            f"manifest sha256 mismatch: expected {expected_sha}, got {actual_sha}"
        )

    raw = tmp_path.read_text(encoding="utf-8")
    try:
        manifest = Manifest.from_json(raw)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    # Sanity: the manifest must declare the same pack_version as the
    # release tag we resolved. Mismatch means the release was assembled
    # incorrectly — abort rather than masking the bug.
    if manifest.pack_version != rel.tag:
        raise ArtUpdateError(
            f"manifest declares pack_version {manifest.pack_version!r} but "
            f"was published in release {rel.tag!r} — refusing to install"
        )

    # Persist manifest + version/checksum sidecars.
    write_manifest(manifest, pack_name=pack_name)
    version_file(pack_name).write_text(rel.tag + "\n", encoding="utf-8")
    checksum_file(pack_name).write_text(
        f"{expected_sha}  {MANIFEST_ASSET_NAME}\n", encoding="utf-8"
    )

    return manifest


# ---------------------------------------------------------------------------
# Manifest diff — used by the prefetcher to skip unchanged cards
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ManifestDiff:
    """Result of comparing two manifests for incremental update planning."""
    added: tuple[str, ...]      # in new, not in old
    removed: tuple[str, ...]    # in old, not in new
    changed: tuple[str, ...]    # sha256 changed between manifests
    unchanged: tuple[str, ...]  # same sha256 — no re-fetch needed

    @property
    def needs_fetch(self) -> tuple[str, ...]:
        """Cards whose art the runtime should (re-)fetch."""
        return self.added + self.changed


def diff_manifests(old: Optional[Manifest], new: Manifest) -> ManifestDiff:
    """Compute incremental fetch plan from old → new.

    When ``old`` is None (fresh install), every card in ``new`` lands in
    ``added``. When ``old`` and ``new`` declare the same pack_version,
    every card lands in ``unchanged`` (the runtime should still verify
    on-disk presence card-by-card, but no network work is needed).
    """
    if old is None:
        return ManifestDiff(
            added=tuple(sorted(new.cards.keys())),
            removed=(),
            changed=(),
            unchanged=(),
        )
    old_ids = set(old.cards.keys())
    new_ids = set(new.cards.keys())
    added = tuple(sorted(new_ids - old_ids))
    removed = tuple(sorted(old_ids - new_ids))
    changed = []
    unchanged = []
    for cid in sorted(old_ids & new_ids):
        if old.cards[cid].sha256 != new.cards[cid].sha256:
            changed.append(cid)
        else:
            unchanged.append(cid)
    return ManifestDiff(
        added=added,
        removed=removed,
        changed=tuple(changed),
        unchanged=tuple(unchanged),
    )


__all__ = [
    "SCHEMA_VERSION",
    "MANIFEST_ASSET_NAME",
    "CardEntry",
    "Manifest",
    "ManifestDiff",
    "load_manifest",
    "write_manifest",
    "fetch_manifest",
    "diff_manifests",
]
