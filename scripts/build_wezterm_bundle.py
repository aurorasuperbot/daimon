#!/usr/bin/env python3
"""Build a DAIMON-flavoured WezTerm bundle for one (OS, arch) target.

Downloads an official upstream WezTerm release artifact, extracts the binaries
we ship (``wezterm``, ``wezterm-gui``, plus a couple of useful helpers when
present), and re-packages them as a flat ``.tar.gz`` named per the convention
the installer expects:

    daimon-wezterm-{linux,macos,windows}-{x86_64,aarch64}.tar.gz

A ``.sha256`` sidecar is written alongside.

Why a separate bundle (vs. linking upstream releases directly)?
  * Stable filename / shape regardless of upstream packaging churn.
  * One archive per (os, arch) — no AppImages, no .pkg, no MSI.
  * SHA pinning under our control.
  * Adds room for future DAIMON-specific assets in the bundle.

Usage:
    python scripts/build_wezterm_bundle.py \\
        --wezterm-version 20240203-110809-5046fc22 \\
        --target-os linux \\
        --target-arch x86_64 \\
        --output-dir dist/

This script is stdlib-only (urllib + tarfile + zipfile + hashlib + tempfile)
so it runs on every GitHub Actions runner without ``pip install``.

Linux aarch64 is intentionally NOT supported here — upstream WezTerm has no
official Linux ARM build. Adding it would require a build-from-source job
which we deferred for V1.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Upstream WezTerm artifact catalogue
# ---------------------------------------------------------------------------
#
# Upstream release URLs follow:
#   https://github.com/wez/wezterm/releases/download/<TAG>/<ASSET>
#
# Where <TAG> is the upstream YYYYMMDD-HHMMSS-shorthash and <ASSET> is one of
# the platform-specific filenames below. We use the smallest-shape archive per
# platform that still contains the binaries we need.

_UPSTREAM_BASE = "https://github.com/wez/wezterm/releases/download/{tag}/{asset}"


def _upstream_asset(target_os: str, target_arch: str, version: str) -> str:
    if target_os == "linux":
        if target_arch != "x86_64":
            raise SystemExit(
                f"unsupported linux arch: {target_arch!r}. Upstream WezTerm "
                "ships no official Linux aarch64 binary; would need a build-"
                "from-source job (deferred for V1)."
            )
        # Ubuntu22 tarball: contains usr/bin/wezterm{,-gui,-mux-server} +
        # a single 'strip-ansi-escapes' helper. xz-compressed.
        # Note: upstream uses lowercase 'wezterm' + period separator (NOT
        # 'WezTerm-...-Ubuntu22.04.tar.xz' which is the Windows/macOS
        # naming pattern). Burned 2026-04-25 in wezterm-bundle-v1.0.
        return f"wezterm-{version}.Ubuntu22.04.tar.xz"
    if target_os == "macos":
        # Universal binary inside a .zip containing WezTerm.app bundle.
        # Upstream publishes one zip that includes both x86_64 and aarch64
        # slices; we ship the same binary for both arches.
        return f"WezTerm-macos-{version}.zip"
    if target_os == "windows":
        if target_arch != "x86_64":
            raise SystemExit(
                f"unsupported windows arch: {target_arch!r} (only x86_64 "
                "shipped upstream)."
            )
        return f"WezTerm-windows-{version}.zip"
    raise SystemExit(f"unknown target_os: {target_os!r}")


def _bundle_filename(target_os: str, target_arch: str) -> str:
    """The DAIMON-side bundle name the installer downloads."""
    return f"daimon-wezterm-{target_os}-{target_arch}.tar.gz"


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _download(url: str, dest: Path) -> None:
    """Stream ``url`` into ``dest`` (atomic via .partial rename)."""
    print(f"download: {url}")
    partial = dest.with_suffix(dest.suffix + ".partial")
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 (trusted gh URL)
        with open(partial, "wb") as fh:
            shutil.copyfileobj(resp, fh, length=1 << 20)
    partial.rename(dest)
    print(f"        -> {dest}  ({dest.stat().st_size:,} bytes)")


# ---------------------------------------------------------------------------
# Extraction — per platform we know the on-disk layout upstream ships.
# ---------------------------------------------------------------------------


def _extract_linux(archive: Path, out_dir: Path) -> list[Path]:
    """Extract Linux Ubuntu22 .tar.xz, return the binaries we keep."""
    keep_basenames = {
        "wezterm",
        "wezterm-gui",
        "wezterm-mux-server",
        "strip-ansi-escapes",
    }
    extracted: list[Path] = []
    with tarfile.open(archive, "r:xz") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            base = Path(m.name).name
            if base not in keep_basenames:
                continue
            # Path inside upstream tarball is e.g.
            # WezTerm-<v>-Ubuntu22.04/usr/bin/wezterm. Flatten to base name.
            target = out_dir / base
            with tf.extractfile(m) as src:  # type: ignore[union-attr]
                if src is None:
                    continue
                target.write_bytes(src.read())
            target.chmod(0o755)
            extracted.append(target)
    if not extracted:
        raise SystemExit(f"no binaries extracted from {archive}")
    return extracted


def _extract_macos(archive: Path, out_dir: Path) -> list[Path]:
    """Extract macOS .zip's WezTerm.app/Contents/MacOS/ binaries."""
    keep_basenames = {"wezterm", "wezterm-gui", "wezterm-mux-server"}
    extracted: list[Path] = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            posix = info.filename.replace("\\", "/")
            if "WezTerm.app/Contents/MacOS/" not in posix:
                continue
            base = Path(posix).name
            if base not in keep_basenames:
                continue
            target = out_dir / base
            with zf.open(info) as src:
                target.write_bytes(src.read())
            target.chmod(0o755)
            extracted.append(target)
    if not extracted:
        raise SystemExit(f"no binaries extracted from {archive}")
    return extracted


def _extract_windows(archive: Path, out_dir: Path) -> list[Path]:
    """Extract Windows .zip's wezterm{,-gui}.exe + DLL deps."""
    extracted: list[Path] = []
    # Windows builds need wezterm.exe + wezterm-gui.exe + every DLL alongside
    # them so a relocated install still finds dependencies. Take everything
    # from the top-level archive directory.
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            posix = info.filename.replace("\\", "/")
            base = Path(posix).name
            # Skip licence/changelog text — bundled README is in our pkg.
            if base.lower() in {"license", "license.md", "license.txt",
                                "readme", "readme.md", "readme.txt"}:
                continue
            target = out_dir / base
            with zf.open(info) as src:
                target.write_bytes(src.read())
            extracted.append(target)
    if not any(p.name.lower() == "wezterm.exe" for p in extracted):
        raise SystemExit(f"wezterm.exe not found in {archive}")
    return extracted


def _extract(target_os: str, archive: Path, out_dir: Path) -> list[Path]:
    if target_os == "linux":
        return _extract_linux(archive, out_dir)
    if target_os == "macos":
        return _extract_macos(archive, out_dir)
    if target_os == "windows":
        return _extract_windows(archive, out_dir)
    raise SystemExit(f"unknown target_os: {target_os!r}")


# ---------------------------------------------------------------------------
# Re-package as flat .tar.gz + .sha256
# ---------------------------------------------------------------------------


def _make_bundle(staging_dir: Path, out_path: Path) -> None:
    """Tar staging_dir contents (flat, no nested folder) into out_path."""
    with tarfile.open(out_path, "w:gz") as tf:
        for entry in sorted(staging_dir.iterdir()):
            # arcname=entry.name flattens — extract drops files directly into
            # ~/.daimon/bin/ with no surrounding directory.
            tf.add(entry, arcname=entry.name)
    print(f"bundle: {out_path}  ({out_path.stat().st_size:,} bytes)")


def _write_sha256(bundle: Path) -> Path:
    h = hashlib.sha256()
    with open(bundle, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    sidecar = bundle.with_suffix(bundle.suffix + ".sha256")
    sidecar.write_text(f"{h.hexdigest()}  {bundle.name}\n", encoding="utf-8")
    print(f"sha256: {sidecar}  ({h.hexdigest()})")
    return sidecar


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wezterm-version", required=True,
                   help="Upstream WezTerm release tag (e.g. 20240203-110809-5046fc22).")
    p.add_argument("--target-os", required=True,
                   choices=["linux", "macos", "windows"])
    p.add_argument("--target-arch", required=True,
                   choices=["x86_64", "aarch64"])
    p.add_argument("--output-dir", required=True, type=Path,
                   help="Where to drop the .tar.gz + .sha256 sidecar.")
    args = p.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    asset = _upstream_asset(args.target_os, args.target_arch, args.wezterm_version)
    url = _UPSTREAM_BASE.format(tag=args.wezterm_version, asset=asset)

    with tempfile.TemporaryDirectory(prefix="daimon-bundle-") as scratch:
        scratch = Path(scratch)
        archive = scratch / asset
        _download(url, archive)

        staging = scratch / "staging"
        staging.mkdir()
        _extract(args.target_os, archive, staging)

        bundle = args.output_dir / _bundle_filename(args.target_os, args.target_arch)
        _make_bundle(staging, bundle)
        _write_sha256(bundle)

    return 0


if __name__ == "__main__":
    sys.exit(main())
