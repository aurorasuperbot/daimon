"""DAIMON game-time bootstrap.

`daimon install` is the **single command** that takes a freshly-pip-installed
``daimon-engine`` and prepares it to play:

    pip install daimon-engine
    daimon install            # ← this module
    daimon init               # generate identity
    daimon shop               # ready to play

What the installer does:

  1. Detects OS + architecture.
  2. Resolves the matching ``daimon-wezterm-{os}-{arch}.tar.gz`` from
     the latest ``wezterm-bundle-v*`` release on ``aurorasuperbot/daimon``.
  3. Downloads the tarball + ``.sha256`` sidecar (streaming, with a
     progress bar).
  4. Verifies the digest before extraction.
  5. Atomically extracts into ``~/.daimon/bin/`` (renames a fresh staging
     dir into place; old install moves to ``bin.trash.<ts>`` and is
     deleted last).
  6. Writes the locked ``~/.daimon/etc/wezterm.lua`` (rewritten on every
     install so a daimon-engine upgrade refreshes config in lockstep).
  7. Smoke-tests with ``wezterm --version`` to confirm the binary is
     callable.
  8. Marks the install with ``~/.daimon/bin/.wezterm-version`` so a
     re-run can skip the network hop when already up-to-date.

The CLI command is wired in ``daimon/cli.py``; this module exposes the
core ``install_bundle()`` entry point + a few helpers for tests.
"""

from __future__ import annotations

from daimon.install.installer import (
    DEFAULT_BUNDLE_REPO,
    DEFAULT_BUNDLE_TAG_PREFIX,
    BundleInstallError,
    InstallReport,
    bundle_asset_name,
    detect_platform,
    install_bundle,
    is_up_to_date,
)

__all__ = [
    "DEFAULT_BUNDLE_REPO",
    "DEFAULT_BUNDLE_TAG_PREFIX",
    "BundleInstallError",
    "InstallReport",
    "bundle_asset_name",
    "detect_platform",
    "install_bundle",
    "is_up_to_date",
]
