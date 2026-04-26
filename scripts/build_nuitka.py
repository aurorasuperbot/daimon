#!/usr/bin/env python3
"""Build the standalone DAIMON binary distribution with Nuitka.

Produces a directory layout the package-manager pipeline (winget /
Scoop / Brew / AppImage / .deb / .rpm) can re-package without
post-processing:

    dist/daimon-<os>-<arch>/
        daimon[.exe]                     # the engine binary
        dmn-mcp[.exe]                    # the MCP stdio server entry point
        daimon-bundled-wezterm/          # WezTerm binaries packed at build time
            wezterm[.exe]
            wezterm-gui[.exe]
            .wezterm-version
        ...other Nuitka data...

The on-disk path layout matches what
:func:`daimon.render.wezterm_bundle.bundled_wezterm_dir` resolves at
runtime — that resolver is the only consumer, and it walks
``Path(sys.executable).parent / "daimon-bundled-wezterm"`` (or
``sys._MEIPASS`` for PyInstaller fallback). Keeping the data-dir name
in lockstep with the resolver is the whole point of giving it a
constant in :mod:`daimon.render.wezterm_bundle`.

This script:
  1. Resolves the WezTerm bundle for the build host (uses
     :mod:`scripts.build_wezterm_bundle`'s download primitives).
  2. Extracts the bundle into a build-tree staging dir.
  3. Invokes Nuitka with ``--standalone`` + ``--include-data-dir`` so
     the bundle ships INSIDE the standalone tree.
  4. Stages a tiny ``dmn-mcp`` shim alongside the main daimon binary
     (Nuitka builds one entry point per invocation; the MCP server
     gets its own binary in a sibling pass).
  5. Emits a sha256 sidecar for the directory's tarball / zip when the
     ``--archive`` flag is set.

Stdlib + Nuitka only — no extra build deps. Code-signing is opt-in
via ``--codesign-identity`` (macOS) and ``--windows-cert`` (Windows);
the GitHub Actions workflow wires those up via repo secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Must match daimon.render.wezterm_bundle._BUNDLED_WEZTERM_DIRNAME.
BUNDLED_WEZTERM_DIRNAME = "daimon-bundled-wezterm"

# Nuitka entry points. We build two separate binaries — the main CLI and
# the MCP stdio server — because Nuitka's --standalone mode produces
# one entry point per invocation. They share data directories via
# --include-data-dir, but each binary has its own .dist tree.
_ENTRY_POINTS = {
    "daimon": "daimon.cli:main",
    "dmn-mcp": "daimon.mcp.server:run_stdio",
}


# ---------------------------------------------------------------------------
# WezTerm bundle staging
# ---------------------------------------------------------------------------

def stage_wezterm_bundle(
    *,
    target_os: str,
    target_arch: str,
    wezterm_version: str,
    work_dir: Path,
) -> Path:
    """Download + extract the matching WezTerm bundle for this build host.

    Returns the absolute path to the extracted directory. The path is
    later passed to Nuitka as
    ``--include-data-dir=<staged>=daimon-bundled-wezterm``.

    Reuses ``scripts/build_wezterm_bundle.py`` as a subprocess so the
    download + extract + repack pipeline stays in one place. We then
    re-extract the produced tarball into the layout Nuitka expects.
    """
    bundle_dir = work_dir / "wezterm_bundle_src"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parent.parent
    cmd = [
        sys.executable,
        str(repo_root / "scripts" / "build_wezterm_bundle.py"),
        "--wezterm-version", wezterm_version,
        "--target-os", target_os,
        "--target-arch", target_arch,
        "--output-dir", str(bundle_dir),
    ]
    print(f"[build_nuitka] staging wezterm bundle: {' '.join(cmd)}", flush=True)
    subprocess.check_call(cmd)

    out_tar = bundle_dir / f"daimon-wezterm-{target_os}-{target_arch}.tar.gz"
    if not out_tar.exists():
        raise RuntimeError(
            f"build_wezterm_bundle did not produce {out_tar} — "
            "did the upstream catalogue change shape?"
        )

    extract_dir = work_dir / "wezterm_extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_tar, "r:gz") as tf:
        tf.extractall(extract_dir, filter="data")

    # Drop a version marker the runtime resolver picks up from
    # ``version_marker_path()``.
    (extract_dir / ".wezterm-version").write_text(
        wezterm_version + "\n", encoding="utf-8"
    )
    return extract_dir


# ---------------------------------------------------------------------------
# Nuitka invocation
# ---------------------------------------------------------------------------

def run_nuitka(
    *,
    entry_module: str,
    output_filename: str,
    output_dir: Path,
    embedded_wezterm_dir: Path,
    codesign_identity: Optional[str] = None,
    windows_cert: Optional[Path] = None,
) -> Path:
    """Invoke Nuitka in --standalone mode and return the produced binary path.

    Args:
        entry_module: dotted module path of the script Nuitka will compile
            (e.g. ``"daimon.cli"``).
        output_filename: bare filename (no path) of the produced binary.
            Nuitka adds ``.exe`` on Windows automatically.
        output_dir: where Nuitka writes the ``<entry>.dist/`` tree.
        embedded_wezterm_dir: source directory that gets baked in as
            ``daimon-bundled-wezterm/`` inside the .dist tree.
        codesign_identity: optional macOS codesigning identity. Skipped
            on non-Darwin builds. Pass via env on the runner.
        windows_cert: optional path to a .pfx for Windows Authenticode
            signing. Skipped on non-Windows builds.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--remove-output",
        f"--output-dir={output_dir}",
        f"--output-filename={output_filename}",
        # Bake the WezTerm dir into the standalone tree alongside the
        # main binary. The `=` separates source from destination
        # (relative to the .dist root).
        f"--include-data-dir={embedded_wezterm_dir}={BUNDLED_WEZTERM_DIRNAME}",
        # Pull in the data files that ship in the wheel.
        "--include-package-data=daimon",
        # Some daimon submodules are loaded dynamically (e.g. catalog
        # JSON loaders read sibling __init__.py paths). --follow-imports
        # is the default but spelling it out documents intent.
        "--follow-imports",
        # mcp.server.fastmcp pulls in pydantic which Nuitka mis-detects
        # as optional. Force-include the whole transitive surface.
        "--include-package=pydantic",
        "--include-package=pydantic_core",
        "--include-package=mcp",
        # Smaller cold-start for CLI entry points that don't need a TUI.
        "--lto=yes",
        "-m", entry_module,
    ]

    # Code-signing is opt-in. Nuitka has no built-in flag — we sign the
    # binary post-build. The flag is plumbed through so the GitHub
    # Actions workflow can pass it in from a secret without hardcoding
    # an unsigned build.
    if platform.system() == "Darwin" and codesign_identity:
        # Nuitka >= 1.9 has --macos-signed-app-name; fall through to
        # codesign as a known-good baseline on older versions.
        cmd.append(f"--macos-signed-app-name={codesign_identity}")

    print(f"[build_nuitka] running: {' '.join(cmd)}", flush=True)
    subprocess.check_call(cmd)

    # Nuitka writes <entry>.dist/<output_filename>.
    suffix = ".exe" if platform.system() == "Windows" else ""
    binary = output_dir / f"{entry_module.replace('.', '_')}.dist" / (
        output_filename + suffix
    )
    if not binary.exists():
        # Nuitka's dist-dir naming has shifted across versions; do a
        # best-effort scan.
        candidates = list(output_dir.glob(f"*.dist/{output_filename}{suffix}"))
        if candidates:
            binary = candidates[0]
        else:
            raise RuntimeError(
                f"Nuitka did not produce {binary} or any "
                f"{output_filename}{suffix} under {output_dir}"
            )

    if platform.system() == "Windows" and windows_cert and windows_cert.exists():
        _sign_windows(binary, windows_cert)
    elif platform.system() == "Darwin" and codesign_identity:
        _sign_macos(binary, codesign_identity)

    return binary


def _sign_windows(binary: Path, cert_pfx: Path) -> None:
    """Sign with signtool from the Windows SDK; password via env."""
    pw = os.environ.get("WINDOWS_CERT_PASSWORD", "")
    cmd = [
        "signtool", "sign",
        "/fd", "SHA256",
        "/td", "SHA256",
        "/tr", "http://timestamp.digicert.com",
        "/f", str(cert_pfx),
    ]
    if pw:
        cmd.extend(["/p", pw])
    cmd.append(str(binary))
    print(f"[build_nuitka] signtool {binary}", flush=True)
    subprocess.check_call(cmd)


def _sign_macos(binary: Path, identity: str) -> None:
    """Sign with macOS codesign; identity is a Developer ID or '-' for ad-hoc."""
    cmd = [
        "codesign", "--force", "--options=runtime",
        "--sign", identity,
        str(binary),
    ]
    print(f"[build_nuitka] codesign {binary}", flush=True)
    subprocess.check_call(cmd)


# ---------------------------------------------------------------------------
# Archive packaging
# ---------------------------------------------------------------------------

def archive_dist(dist_dir: Path, archive_path: Path) -> Path:
    """Pack the standalone tree into a .tar.gz (POSIX) or .zip (Windows).

    Returns the archive path. Also writes ``<archive>.sha256`` in
    ``sha256sum -c`` format.
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(dist_dir.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(dist_dir.parent))
    else:
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(dist_dir, arcname=dist_dir.name)

    # Sidecar.
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    sidecar = archive_path.with_suffix(archive_path.suffix + ".sha256")
    sidecar.write_text(
        f"{digest}  {archive_path.name}\n", encoding="utf-8"
    )
    return archive_path


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target-os", required=True,
                   choices=("linux", "macos", "windows"))
    p.add_argument("--target-arch", required=True,
                   choices=("x86_64", "aarch64"))
    p.add_argument("--wezterm-version", required=True,
                   help="Upstream WezTerm release tag to bundle "
                        "(e.g. 20240203-110809-5046fc22).")
    p.add_argument("--output-dir", default="dist",
                   help="Where the .dist tree + archive lands.")
    p.add_argument("--archive", action="store_true",
                   help="Pack the .dist tree into a .tar.gz / .zip.")
    p.add_argument("--codesign-identity", default=None,
                   help="macOS codesigning identity (Developer ID Application). "
                        "Pass '-' for ad-hoc signing in CI dry-runs.")
    p.add_argument("--windows-cert", type=Path, default=None,
                   help="Path to a .pfx certificate for Windows Authenticode "
                        "signing. Password via WINDOWS_CERT_PASSWORD env.")
    args = p.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="daimon-build-") as work:
        work_dir = Path(work)
        embedded = stage_wezterm_bundle(
            target_os=args.target_os,
            target_arch=args.target_arch,
            wezterm_version=args.wezterm_version,
            work_dir=work_dir,
        )

        # Build each entry point. Nuitka emits one .dist tree per call;
        # we merge the dmn-mcp binary into the daimon dist tree so a
        # single distribution archive ships both.
        produced_binaries = []
        for binary_name, dotted in _ENTRY_POINTS.items():
            mod_path, _, _ = dotted.partition(":")
            entry_output_dir = output_dir / f"_{binary_name}_build"
            binary = run_nuitka(
                entry_module=mod_path,
                output_filename=binary_name,
                output_dir=entry_output_dir,
                embedded_wezterm_dir=embedded,
                codesign_identity=args.codesign_identity,
                windows_cert=args.windows_cert,
            )
            produced_binaries.append((binary_name, binary))

        # Merge: the first binary's .dist tree is canonical (because it
        # contains the embedded WezTerm); the second binary's executable
        # gets copied next to it.
        canonical_name, canonical_bin = produced_binaries[0]
        canonical_dist = canonical_bin.parent
        # Rename to the conventional dist directory.
        final_dist = output_dir / f"daimon-{args.target_os}-{args.target_arch}"
        if final_dist.exists():
            shutil.rmtree(final_dist)
        shutil.copytree(canonical_dist, final_dist)

        for binary_name, binary in produced_binaries[1:]:
            shutil.copy2(binary, final_dist / binary.name)

        # Drop the per-entry build scratch dirs to keep the output dir clean.
        for binary_name, _ in produced_binaries:
            shutil.rmtree(output_dir / f"_{binary_name}_build", ignore_errors=True)

        if args.archive:
            ext = ".zip" if args.target_os == "windows" else ".tar.gz"
            archive_path = output_dir / (final_dist.name + ext)
            archive_dist(final_dist, archive_path)
            print(f"[build_nuitka] archive: {archive_path}")

    print(f"[build_nuitka] done — {final_dist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
