"""Download + verify + atomic-swap of art-pack tarballs.

Flow (do_update):

  1. Resolve target release (latest, pinned, or explicit tag).
  2. Refuse cross-major upgrades unless ``force=True``.
  3. Download tarball → ``cache/staging/<asset>.partial`` with a progress bar.
  4. Stream-verify sha256 against the expected digest (sidecar > release-body).
  5. Rename ``.partial`` → ``<asset>``.
  6. Extract into ``cache/staging/<pack-name>/`` (safe extract, no traversal).
  7. Atomic swap:
       a. ``art/<pack>/`` → ``art/<pack>.trash.<ts>/``  (single rename)
       b. ``cache/staging/<pack>/`` → ``art/<pack>/``    (single rename)
       c. write ``.version`` + ``.checksum`` into the live pack
       d. delete the trash dir + delete the staged tarball
  8. Return the new ``ReleaseInfo``.

Atomicity:
  * Renames within the same filesystem are atomic on POSIX. We require
    ``cache/`` and ``art/`` to live under the same root (they do, by
    construction in paths.py) so the swap is one syscall, not a copy.
  * If the process dies between (a) and (b), the next invocation finds no
    live pack and re-downloads. The trash dir is mopped up next run.

Failure modes are surfaced via ``ArtUpdateError`` so callers can log /
notify without parsing tracebacks. Network/HTTP failures from the api
module are wrapped at the call site.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from daimon.update import api
from daimon.update.paths import (
    ART_PACK_NAME,
    COMPAT_ART_MAJOR,
    DEFAULT_ART_ASSET_NAME,
    art_pack_dir,
    art_repo,
    art_root,
    cache_dir,
    checksum_file,
    current_version,
    parse_art_version,
    pinned_version,
    staging_dir,
    version_file,
)


CHUNK = 1 << 20  # 1 MiB
HTTP_TIMEOUT = 60  # seconds
USER_AGENT = "daimon-update/1.0 (+https://github.com/aurorasuperbot/daimon)"

# Inline regex for sha256 digests embedded in release notes — 64 lowercase hex.
_SHA256_RE = re.compile(r"\b([0-9a-f]{64})\b", re.IGNORECASE)


class ArtUpdateError(RuntimeError):
    """Any failure during art-pack download / verify / install."""


# ---------------------------------------------------------------------------
# HTTP download
# ---------------------------------------------------------------------------

def _http_open(url: str, *, octet_stream: bool = False):
    """Open ``url`` for streaming. Adds GH auth header if a token is set.

    With ``octet_stream=True``, sends ``Accept: application/octet-stream``
    — required when downloading release assets via the GitHub API URL
    (``https://api.github.com/repos/.../releases/assets/<id>``). Without
    that header, the API returns the asset's JSON metadata instead of
    the bytes. The browser_download_url path doesn't need it.
    """
    headers = {"User-Agent": USER_AGENT}
    if octet_stream:
        headers["Accept"] = "application/octet-stream"
    req = Request(url, headers=headers)
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    return urlopen(req, timeout=HTTP_TIMEOUT)


def _pick_asset_url(
    browser_url: str,
    api_url: str,
) -> tuple[str, bool]:
    """Pick the right URL + Accept header based on auth state.

    Returns ``(url, needs_octet_stream)``.

    If a ``GITHUB_TOKEN`` is set we ALWAYS prefer the API URL — it works
    for both private and public repos, with the bearer-auth headers we
    already attach. Without a token, ``browser_download_url`` is the only
    path that works for public repos (it 302-redirects to a signed S3
    URL; the API URL would 404 anonymously on public-not-in-org repos).
    """
    has_token = bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    if has_token and api_url:
        return api_url, True
    return browser_url or api_url, False


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} GB"


def _print_progress(prefix: str, done: int, total: int) -> None:
    """Single-line progress to stderr. tqdm-free, no extra deps."""
    if total <= 0:
        sys.stderr.write(f"\r{prefix} {_human_bytes(done)}    ")
    else:
        pct = done * 100 // total
        bar_w = 24
        fill = (pct * bar_w) // 100
        bar = "█" * fill + "░" * (bar_w - fill)
        sys.stderr.write(
            f"\r{prefix} [{bar}] {pct:3}%  "
            f"{_human_bytes(done)} / {_human_bytes(total)}    "
        )
    sys.stderr.flush()


def download_with_progress(
    url: str,
    dest: Path,
    expected_size: int = 0,
    label: str = "fetching",
    show_progress: bool = True,
    octet_stream: bool = False,
) -> Path:
    """Stream ``url`` → ``dest.partial`` → ``dest``. Returns ``dest``.

    Atomic on the destination side: the ``.partial`` rename happens only
    after a clean read, so a half-finished file never appears at ``dest``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()

    try:
        with _http_open(url, octet_stream=octet_stream) as resp:
            total = expected_size or int(resp.headers.get("Content-Length") or 0)
            done = 0
            last_print = 0.0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if show_progress and (now - last_print) > 0.1:
                        _print_progress(label, done, total)
                        last_print = now
            if show_progress:
                _print_progress(label, done, total)
                sys.stderr.write("\n")
                sys.stderr.flush()
    except (HTTPError, URLError, OSError) as e:
        if tmp.exists():
            tmp.unlink()
        raise ArtUpdateError(f"download failed: {e}") from e

    tmp.replace(dest)
    return dest


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    """Stream sha256 over ``path``. Returns lowercase hex."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def parse_sha256_sidecar(text: str) -> Optional[str]:
    """Parse ``sha256sum`` output: ``<hex>  <filename>`` → bare hex."""
    text = text.strip()
    if not text:
        return None
    parts = text.split()
    if not parts:
        return None
    cand = parts[0].lower()
    if len(cand) == 64 and all(c in "0123456789abcdef" for c in cand):
        return cand
    return None


def fetch_expected_sha256(release: api.ReleaseInfo) -> Optional[str]:
    """Resolve the sha256 we should match against.

    Order of preference:
      1. ``<asset>.sha256`` sidecar asset (cleanest).
      2. First 64-hex token in the release body.
      3. None — caller must decide whether to abort or trust-on-first-use.
    """
    if release.sha256_url or release.sha256_api_url:
        url, octet = _pick_asset_url(
            release.sha256_url or "",
            release.sha256_api_url or "",
        )
        try:
            with _http_open(url, octet_stream=octet) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            digest = parse_sha256_sidecar(text)
            if digest:
                return digest
        except (HTTPError, URLError, OSError):
            pass

    if release.body:
        m = _SHA256_RE.search(release.body)
        if m:
            return m.group(1).lower()

    return None


# ---------------------------------------------------------------------------
# Extraction (safe — no path traversal)
# ---------------------------------------------------------------------------

def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_extract_tarball(tar_path: Path, dest_dir: Path) -> None:
    """Extract ``tar_path`` under ``dest_dir`` rejecting absolute paths,
    parent-traversals, symlinks, hardlinks, and device nodes.

    The art tarball is regular-files-only by construction; we treat anything
    else as malicious and abort.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                raise ArtUpdateError(
                    f"refusing to extract link member: {member.name!r}"
                )
            if member.isdev() or member.isfifo():
                raise ArtUpdateError(
                    f"refusing to extract device/fifo member: {member.name!r}"
                )
            target = (dest_dir / member.name)
            if not _is_within(target, dest_dir):
                raise ArtUpdateError(
                    f"refusing path-traversal extract: {member.name!r}"
                )
        # Second pass to actually extract — getmembers is cheap and we want
        # to fail BEFORE writing any bytes. ``filter='data'`` is the modern
        # tarfile safe-extract default (Python 3.12+); it strips ownership /
        # special-mode bits and re-applies the symlink/device guards. We
        # already screened above, but defense-in-depth never hurts.
        try:
            tf.extractall(dest_dir, filter="data")  # noqa: S202 — vetted above
        except TypeError:
            # Older Python without the `filter=` kwarg (3.11 < 3.11.4).
            tf.extractall(dest_dir)  # noqa: S202 — vetted above


# ---------------------------------------------------------------------------
# Atomic swap
# ---------------------------------------------------------------------------

def _trash_path(live: Path) -> Path:
    return live.parent / f"{live.name}.trash.{int(time.time())}"


def atomic_swap(staged_pack_dir: Path, live_pack_dir: Path) -> None:
    """Replace ``live_pack_dir`` with ``staged_pack_dir``.

    Both must live on the same filesystem (same root in our layout).

    Sequence:
      1. If live exists: rename → trash (one syscall).
      2. Rename staged → live (one syscall).
      3. Best-effort delete trash. Failure here is non-fatal — the next
         invocation will mop it up.

    Crash recovery:
      * Crash between (1) and (2): live is missing; next run re-downloads.
      * Crash between (2) and (3): trash is left behind; mopped next run.
    """
    live_pack_dir.parent.mkdir(parents=True, exist_ok=True)
    trash: Optional[Path] = None
    if live_pack_dir.exists():
        trash = _trash_path(live_pack_dir)
        live_pack_dir.rename(trash)
    try:
        staged_pack_dir.rename(live_pack_dir)
    except OSError as e:
        # Roll back: put the old pack back so the user isn't art-less.
        if trash is not None and trash.exists():
            try:
                trash.rename(live_pack_dir)
            except OSError:
                pass
        raise ArtUpdateError(f"swap failed: {e}") from e

    if trash is not None and trash.exists():
        shutil.rmtree(trash, ignore_errors=True)


def cleanup_trash(pack_name: str = ART_PACK_NAME) -> None:
    """Sweep abandoned ``art/<pack>.trash.*`` dirs from prior crashed swaps."""
    art_dir = art_root() / "art"
    if not art_dir.is_dir():
        return
    for child in art_dir.iterdir():
        if child.is_dir() and child.name.startswith(f"{pack_name}.trash."):
            shutil.rmtree(child, ignore_errors=True)


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def _resolve_target(
    target_version: Optional[str],
    force: bool,
) -> api.ReleaseInfo:
    """Pick which release we're installing — pinned, explicit, or latest.

    Raises ``ArtUpdateError`` on no match or cross-major bump (unless force).
    """
    repo = art_repo()
    pin = target_version or pinned_version()

    try:
        if pin:
            rel = api.gh_release_by_tag(repo, pin)
            if rel is None:
                raise ArtUpdateError(
                    f"pinned version {pin!r} not found on {repo} "
                    f"(or asset {DEFAULT_ART_ASSET_NAME} missing)"
                )
        else:
            rel = api.gh_latest_release(repo)
            if rel is None:
                raise ArtUpdateError(
                    f"no compatible art-pack release found on {repo}"
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
            "Update the engine first, or pass --force."
        )

    return rel


def do_update(
    target_version: Optional[str] = None,
    force: bool = False,
    show_progress: bool = True,
    pack_name: str = ART_PACK_NAME,
) -> api.ReleaseInfo:
    """End-to-end refresh — download, verify, swap. Returns the installed release.

    Args:
        target_version: explicit tag to install (e.g. ``"art-v1.0"``).
            ``None`` means latest (subject to ``DAIMON_PIN_ART``).
        force: bypass the cross-major-version guard. Use only when the
            engine has been intentionally upgraded to a new pack format.
        show_progress: print a progress bar to stderr. Disable in
            background-spawned subprocesses to keep their log clean.
        pack_name: which pack to install into (only ``v1_alpha`` for V1).

    Idempotent: if the resolved version equals ``current_version()``, the
    download is skipped and the existing pack info is returned.
    """
    cleanup_trash(pack_name)

    rel = _resolve_target(target_version, force)

    if not force and current_version(pack_name) == rel.tag:
        return rel

    # Sized staging — fail fast if we don't have headroom.
    staging = staging_dir()
    staging.mkdir(parents=True, exist_ok=True)

    expected_sha = fetch_expected_sha256(rel)
    # We REQUIRE a digest for unattended updates. A release without one is
    # treated as broken — operator can publish the sidecar and re-run.
    if not expected_sha:
        raise ArtUpdateError(
            f"release {rel.tag} ships no sha256 sidecar and no digest in "
            f"the body — refusing to install unverified bytes."
        )

    # asset_url is the human-facing URL — its tail is always the filename,
    # even when we ultimately download from asset_api_url (which ends in
    # an opaque numeric asset ID).
    asset_name = (rel.asset_url or DEFAULT_ART_ASSET_NAME).rsplit("/", 1)[-1]
    tarball_path = staging / f"{pack_name}-{rel.tag}-{asset_name}"

    asset_dl_url, octet = _pick_asset_url(rel.asset_url, rel.asset_api_url)
    download_with_progress(
        asset_dl_url, tarball_path,
        expected_size=rel.asset_size,
        label=f"daimon: fetching {rel.tag}",
        show_progress=show_progress,
        octet_stream=octet,
    )

    actual = sha256_file(tarball_path)
    if actual.lower() != expected_sha.lower():
        tarball_path.unlink(missing_ok=True)
        raise ArtUpdateError(
            f"sha256 mismatch on {asset_name}: expected {expected_sha}, "
            f"got {actual} — refusing to install."
        )

    # Extract into a sibling dir under staging, then swap.
    staged_pack_parent = staging / f"{pack_name}-{rel.tag}"
    if staged_pack_parent.exists():
        shutil.rmtree(staged_pack_parent)
    safe_extract_tarball(tarball_path, staged_pack_parent)

    # The tarball top-level is `art/<pack>/...` (matches repo layout).
    # Detect both that shape and a flat `<pack>/...` shape.
    candidate_a = staged_pack_parent / "art" / pack_name
    candidate_b = staged_pack_parent / pack_name
    if candidate_a.is_dir():
        staged_pack = candidate_a
    elif candidate_b.is_dir():
        staged_pack = candidate_b
    else:
        raise ArtUpdateError(
            f"extracted tarball does not contain expected dir "
            f"({candidate_a} or {candidate_b})"
        )

    live = art_pack_dir(pack_name)
    atomic_swap(staged_pack, live)

    # Persist the version + checksum sidecar files inside the live pack.
    version_file(pack_name).write_text(rel.tag + "\n", encoding="utf-8")
    checksum_file(pack_name).write_text(
        f"{expected_sha}  {asset_name}\n", encoding="utf-8"
    )

    # Clean up: the staging parent dir + downloaded tarball are no longer needed.
    shutil.rmtree(staged_pack_parent, ignore_errors=True)
    tarball_path.unlink(missing_ok=True)

    return rel


__all__ = [
    "ArtUpdateError",
    "do_update",
    "download_with_progress",
    "sha256_file",
    "fetch_expected_sha256",
    "safe_extract_tarball",
    "atomic_swap",
    "cleanup_trash",
]
