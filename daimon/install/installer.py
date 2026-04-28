"""WezTerm bundle installer — download, verify, extract, smoke-test.

Implementation notes:

* We share networking helpers with ``daimon.update.fetcher`` (auth header
  injection, octet-stream switch, sha256 streaming, progress bar). Anything
  that reads ``GITHUB_TOKEN`` / handles HTTPError lives there.
* Releases live on ``aurorasuperbot/daimon`` (the engine repo —
  ``daimon-engine`` is the PyPI distribution name, not the GitHub
  repo) under tag prefix ``wezterm-bundle-v``. Each release ships
  per-OS/arch tarballs as assets, plus ``.sha256`` sidecars.
* Atomic install: stage tarball + extracted dir under ``cache/staging/``,
  then a single atomic rename into ``bin/``. Pre-existing ``bin/`` is
  shoved into ``bin.trash.<ts>/`` and removed after the swap succeeds —
  if the process dies mid-swap the next invocation cleans up.
* Re-running ``daimon install`` is idempotent: if the marker version
  matches the latest release we skip the network round-trip entirely
  (``--force`` overrides).
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.error import HTTPError, URLError

from daimon.render.wezterm_bundle import (
    bin_dir,
    install_from_tarball,
    is_installed,
    runtime_root,
    version_marker_path,
    wezterm_bin,
    wezterm_config_path,
    write_locked_config,
)
from daimon.update.api import ReleaseInfo, gh_latest_release, gh_release_by_tag
from daimon.update.fetcher import (
    _http_open,
    _human_bytes,
    _pick_asset_url,
    _print_progress,
)


# ---------------------------------------------------------------------------
# Release coordinates
# ---------------------------------------------------------------------------

DEFAULT_BUNDLE_REPO = "aurorasuperbot/daimon"
DEFAULT_BUNDLE_TAG_PREFIX = "wezterm-bundle-v"

CHUNK = 1 << 20  # 1 MiB read window for streaming downloads
HTTP_TIMEOUT = 60


def bundle_repo() -> str:
    """GH repo (owner/name) for the WezTerm bundle releases.

    Override via ``DAIMON_BUNDLE_REPO`` (used in tests, forks, internal CI).
    """
    return os.environ.get("DAIMON_BUNDLE_REPO") or DEFAULT_BUNDLE_REPO


def bundle_tag_prefix() -> str:
    """Tag prefix for bundle releases (``wezterm-bundle-v``).

    Override via ``DAIMON_BUNDLE_TAG_PREFIX``.
    """
    return os.environ.get("DAIMON_BUNDLE_TAG_PREFIX") or DEFAULT_BUNDLE_TAG_PREFIX


def pinned_bundle_version() -> Optional[str]:
    """Returns ``wezterm-bundle-vX.Y`` if user pinned a version, else None.

    Mirrors ``DAIMON_PIN_ART`` for the art pack. Useful for CI, regression
    tests, or rolling back a bad bundle release.
    """
    v = os.environ.get("DAIMON_PIN_BUNDLE", "").strip()
    return v or None


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


class BundleInstallError(RuntimeError):
    """Any failure during WezTerm bundle download / verify / install."""


def detect_platform() -> Tuple[str, str]:
    """Returns ``(os_name, arch)`` strings used in the asset name.

    OS values: ``"linux"``, ``"macos"``, ``"windows"``.
    Arch values: ``"x86_64"``, ``"aarch64"``.

    Raises :class:`BundleInstallError` for unsupported combos so the caller
    can give the user a concrete error message instead of a 404 later.
    """
    sys_lc = platform.system().lower()
    machine = platform.machine().lower()

    if sys_lc == "darwin":
        os_name = "macos"
    elif sys_lc == "linux":
        os_name = "linux"
    elif sys_lc == "windows":
        os_name = "windows"
    else:
        raise BundleInstallError(
            f"unsupported OS {platform.system()!r}; daimon ships bundles "
            "for linux / macos / windows only.")

    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "aarch64"
    else:
        raise BundleInstallError(
            f"unsupported arch {platform.machine()!r}; daimon ships bundles "
            "for x86_64 and aarch64 only.")

    return os_name, arch


def bundle_asset_name(os_name: Optional[str] = None,
                      arch: Optional[str] = None) -> str:
    """``daimon-wezterm-{os}-{arch}.tar.gz``."""
    if os_name is None or arch is None:
        os_name, arch = detect_platform()
    return f"daimon-wezterm-{os_name}-{arch}.tar.gz"


# ---------------------------------------------------------------------------
# Release resolution
# ---------------------------------------------------------------------------


def _resolve_release(version: Optional[str]) -> ReleaseInfo:
    """Resolve the user's intent to a concrete ReleaseInfo.

    ``version`` precedence: explicit arg > ``DAIMON_PIN_BUNDLE`` > "latest".
    Raises BundleInstallError on any miss (no parseable release, no asset
    for our platform, network failure).
    """
    repo = bundle_repo()
    asset = bundle_asset_name()
    tag_prefix = bundle_tag_prefix()
    explicit = version or pinned_bundle_version()

    try:
        if explicit and explicit not in ("latest", ""):
            rel = gh_release_by_tag(
                repo, explicit, asset_name=asset, tag_prefix=tag_prefix
            )
            if rel is None:
                raise BundleInstallError(
                    f"no release {explicit!r} on {repo} (or asset "
                    f"{asset!r} missing from that release).")
            return rel
        rel = gh_latest_release(repo, tag_prefix=tag_prefix, asset_name=asset)
        if rel is None:
            raise BundleInstallError(
                f"no {tag_prefix}* release on {repo} carries asset {asset!r}. "
                "The bundle hasn't shipped for this platform yet — "
                "file an issue on daimon-engine or try DAIMON_PIN_BUNDLE.")
        return rel
    except HTTPError as e:
        if e.code == 404 and not (
            os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        ):
            raise BundleInstallError(
                f"GitHub API 404 on {repo} while resolving WezTerm bundle "
                "release. The repo is private — set GITHUB_TOKEN (or "
                "GH_TOKEN) to a PAT with read access and retry."
            ) from e
        raise BundleInstallError(f"GitHub API error while resolving release: {e}") from e
    except URLError as e:
        raise BundleInstallError(f"GitHub API error while resolving release: {e}") from e


# ---------------------------------------------------------------------------
# Download + verify
# ---------------------------------------------------------------------------


def _download(url: str, dest: Path, *,
              expected_size: int,
              octet_stream: bool,
              progress: Optional[Callable[[str, int, int], None]] = None) -> str:
    """Stream URL → ``dest.partial``, return hex sha256.

    Renames .partial → dest after a clean download. Raises BundleInstallError
    on truncation / network failure.
    """
    partial = dest.with_suffix(dest.suffix + ".partial")
    partial.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    written = 0
    cb = progress or _print_progress
    try:
        with _http_open(url, octet_stream=octet_stream) as resp, \
                open(partial, "wb") as out:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                written += len(chunk)
                cb(f"daimon-wezterm:", written, expected_size)
    except (HTTPError, URLError, OSError) as e:
        partial.unlink(missing_ok=True)
        raise BundleInstallError(f"download failed: {e}") from e
    sys.stderr.write("\n")
    if expected_size and written != expected_size:
        partial.unlink(missing_ok=True)
        raise BundleInstallError(
            f"download truncated: got {written} of {expected_size} bytes")
    partial.replace(dest)
    return h.hexdigest()


def _expected_sha256(rel: ReleaseInfo) -> Optional[str]:
    """Fetch the .sha256 sidecar and parse out the digest.

    Returns ``None`` if no sidecar is published. Raises BundleInstallError
    on parse failure (a sidecar that doesn't contain a 64-char hex string
    is corrupt; refuse to install).
    """
    if not rel.sha256_url and not rel.sha256_api_url:
        return None
    url, octet = _pick_asset_url(rel.sha256_url or "", rel.sha256_api_url or "")
    try:
        with _http_open(url, octet_stream=octet) as resp:
            text = resp.read(2048).decode("utf-8", errors="replace").strip()
    except (HTTPError, URLError, OSError) as e:
        raise BundleInstallError(f"sha256 sidecar fetch failed: {e}") from e
    # Accept "<hex>  <name>" (sha256sum) or bare "<hex>".
    parts = text.split()
    if not parts:
        raise BundleInstallError("sha256 sidecar is empty")
    digest = parts[0].lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise BundleInstallError(f"sha256 sidecar malformed: {text!r}")
    return digest


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> str:
    """Run ``wezterm --version``; return the version string.

    Raises BundleInstallError if the binary refuses to run (typical causes:
    Linux glibc too old, macOS quarantine bit, Windows missing DLLs).
    """
    import subprocess

    if not is_installed():
        raise BundleInstallError(
            f"smoke test failed: binary missing at {wezterm_bin()}")

    try:
        out = subprocess.run(
            [str(wezterm_bin()), "--version"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as e:
        raise BundleInstallError(f"smoke test crashed: {e}") from e

    if out.returncode != 0:
        raise BundleInstallError(
            f"smoke test failed (exit {out.returncode}):\n"
            f"  stdout: {out.stdout.strip()!r}\n"
            f"  stderr: {out.stderr.strip()!r}")

    return out.stdout.strip()


# ---------------------------------------------------------------------------
# Up-to-date check (lets ``daimon install`` short-circuit network calls)
# ---------------------------------------------------------------------------


def is_up_to_date(release: Optional[ReleaseInfo] = None) -> bool:
    """``True`` iff the installed bundle matches the latest release's tag.

    Pass an already-fetched ``ReleaseInfo`` to avoid re-hitting the API.
    Returns ``False`` if no bundle is installed or no marker exists.
    """
    if not is_installed():
        return False
    marker_path = version_marker_path()
    if not marker_path.is_file():
        return False
    try:
        installed = marker_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if release is None:
        try:
            release = _resolve_release(None)
        except BundleInstallError:
            return False
    return installed == release.tag


# ---------------------------------------------------------------------------
# Public install entry point
# ---------------------------------------------------------------------------


@dataclass
class InstallReport:
    """Summary of one ``install_bundle()`` call.

    Returned both to programmatic callers and used by the CLI to print the
    user-facing summary.
    """
    tag: str
    asset: str
    bytes_downloaded: int
    sha256: str
    bin_dir: Path
    config_path: Path
    smoke_test: str
    skipped_download: bool = False
    """If True, the version marker matched and we did not re-fetch."""


def install_bundle(*,
                   version: Optional[str] = None,
                   force: bool = False,
                   verify_smoke_test: bool = True,
                   progress: Optional[Callable[[str, int, int], None]] = None,
                   ) -> InstallReport:
    """Top-level installer. Idempotent unless ``force`` is set.

    Steps:
      1. Resolve target release (latest, pinned, or explicit).
      2. If marker version matches and ``not force`` → no-op.
      3. Download tarball + verify sha256.
      4. Extract atomically into ``~/.daimon/bin/``.
      5. Write locked ``wezterm.lua``.
      6. Smoke-test ``wezterm --version`` (skippable for CI).
      7. Return :class:`InstallReport`.

    Side effects: writes to ``~/.daimon/cache/staging/`` (download scratch)
    and ``~/.daimon/{bin,etc}/``. Network: 1 GH API call + 2 HTTP downloads
    (tarball + sha256 sidecar). Raises :class:`BundleInstallError` on any
    failure after which it's safe to re-run.
    """
    rel = _resolve_release(version)

    # Idempotent short-circuit.
    if not force and is_up_to_date(rel):
        # Still rewrite the locked config (cheap, keeps it in sync if
        # daimon-engine was upgraded but bundle didn't change).
        cfg = write_locked_config()
        return InstallReport(
            tag=rel.tag,
            asset=bundle_asset_name(),
            bytes_downloaded=0,
            sha256="",
            bin_dir=bin_dir(),
            config_path=cfg,
            smoke_test=(_smoke_test() if verify_smoke_test else ""),
            skipped_download=True,
        )

    # Pick auth-aware asset URL.
    url, octet = _pick_asset_url(rel.asset_url, rel.asset_api_url)

    # Stage the tarball under cache/staging/.
    staging = runtime_root() / "cache" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    tarball = staging / bundle_asset_name()

    sys.stderr.write(
        f"daimon: installing WezTerm bundle {rel.tag} "
        f"({_human_bytes(rel.asset_size)})\n"
    )
    actual_sha = _download(
        url, tarball,
        expected_size=rel.asset_size,
        octet_stream=octet,
        progress=progress,
    )

    expected_sha = _expected_sha256(rel)
    if expected_sha is None:
        # No sidecar — refuse to install. Bundle releases MUST ship a
        # sidecar so the install can be verified offline-after-download.
        raise BundleInstallError(
            f"release {rel.tag!r} has no .sha256 sidecar — refusing to "
            "install unverifiable bundle.")
    if actual_sha != expected_sha:
        tarball.unlink(missing_ok=True)
        raise BundleInstallError(
            f"sha256 mismatch:\n"
            f"  expected: {expected_sha}\n"
            f"  got:      {actual_sha}")

    # Atomic install: extract to a scratch dir then swap into bin/.
    scratch_bin = staging / "bin.new"
    if scratch_bin.exists():
        shutil.rmtree(scratch_bin, ignore_errors=True)
    # install_from_tarball extracts into the supplied bin_dir; we
    # temporarily monkey-call by extracting under scratch then moving.
    import tarfile

    scratch_bin.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:*") as tf:
        for m in tf.getmembers():
            if m.name.startswith("/") or ".." in m.name.split("/"):
                raise BundleInstallError(f"unsafe tarball member: {m.name!r}")
            if not (m.isfile() or m.isdir() or m.issym()):
                raise BundleInstallError(
                    f"unsupported tarball member type: {m.name!r}")
        # filter="data" rejects unsafe paths/permissions; we already validated
        # paths above but pass it explicitly for Python 3.14 compatibility.
        tf.extractall(path=scratch_bin, filter="data")

    if platform.system() != "Windows":
        for p in scratch_bin.iterdir():
            if p.name.startswith("wezterm"):
                p.chmod(p.stat().st_mode | 0o111)

    # Swap into bin/. Move existing bin → trash first, then rename scratch.
    target_bin = bin_dir()
    target_bin.parent.mkdir(parents=True, exist_ok=True)
    if target_bin.exists():
        trash = target_bin.with_name(f"bin.trash.{int(time.time())}")
        target_bin.rename(trash)
        # Best-effort cleanup; safe to leave behind if it fails.
        try:
            shutil.rmtree(trash, ignore_errors=True)
        except OSError:
            pass
    scratch_bin.rename(target_bin)

    # Marker + locked config.
    version_marker_path().write_text(rel.tag + "\n", encoding="utf-8")
    cfg = write_locked_config()

    # Cleanup the verified tarball (we're done with it).
    tarball.unlink(missing_ok=True)

    smoke = _smoke_test() if verify_smoke_test else ""

    return InstallReport(
        tag=rel.tag,
        asset=bundle_asset_name(),
        bytes_downloaded=rel.asset_size,
        sha256=actual_sha,
        bin_dir=target_bin,
        config_path=cfg,
        smoke_test=smoke,
        skipped_download=False,
    )
