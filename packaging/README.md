# Distribution manifests

Per-package-manager manifests that point users at the binaries
produced by `.github/workflows/release-binaries.yml`. Each release
of DAIMON requires updating the version pin + sha256 in every
manifest below; the workflow itself does not push to upstream
package registries (those are PR-based with manual review).

| Package manager  | File                                  | Upstream submission                    |
|------------------|---------------------------------------|----------------------------------------|
| winget           | `winget/aurorasuperbot.daimon.yaml`   | <https://github.com/microsoft/winget-pkgs> |
| Scoop            | `scoop/daimon.json`                   | scoop bucket repo (run `scoop bucket add`)  |
| Homebrew         | `homebrew/daimon.rb`                  | tap repo (`brew tap aurorasuperbot/daimon`) |
| AppImage         | `appimage/daimon.AppImageBuilder.yml` | host on releases page (uploaded by CI)      |
| Debian / Ubuntu  | `debian/daimon.control`               | host .deb on releases (or PPA)              |
| RPM (Fedora etc) | `rpm/daimon.spec`                     | host .rpm on releases (or COPR)             |

## Release checklist

1. Tag `daimon-vX.Y.Z` to fire `release-binaries.yml` →
   produces `daimon-{os}-{arch}.{tar.gz,zip}` + `.sha256` per OS/arch.
2. For each manifest in this directory:
   - Bump the version string.
   - Replace the recorded `sha256` with the value from the matching
     `.sha256` sidecar.
   - Replace any URL pinning the previous tag.
3. Open a PR to each upstream registry (winget-pkgs, scoop bucket,
   tap repo). The AppImage / .deb / .rpm artifacts are uploaded as
   release assets by CI; no upstream PR needed.

The version + sha bumps are mechanical; a future iteration will add
`scripts/bump_packaging.py` to do them automatically once the V1
release cadence stabilises.
