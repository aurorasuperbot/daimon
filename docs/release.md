# DAIMON Release Pipeline

How a new version of DAIMON gets from `monster-pivot` to a user's
machine. There are three independent release artefacts, each driven
by its own tag + workflow:

| Tag pattern         | Workflow                       | Produces                                   |
|---------------------|--------------------------------|--------------------------------------------|
| `daimon-vX.Y.Z`     | `release-binaries.yml`         | Standalone Nuitka binaries (4 platforms)   |
| `art-vX.Y`          | `art-manifest.yml`             | `manifest.json` + per-card tarballs        |
| `wezterm-bundle-vX.Y` | `wezterm-bundle.yml`         | WezTerm tarballs (legacy `pip install` path)|

The three are decoupled on purpose: bumping the cards doesn't require
recompiling binaries, bumping the engine doesn't invalidate the art
cache, and the WezTerm bundle is pinned per engine release.

## 1. Binary release (`daimon-vX.Y.Z`)

Triggered by pushing a `daimon-vX.Y.Z` tag.

**What runs**: `release-binaries.yml` builds standalone Nuitka
distributions on four native runners:

* `ubuntu-latest`     → `daimon-linux-x86_64.tar.gz`
* `macos-13`          → `daimon-macos-x86_64.tar.gz`     (Intel)
* `macos-14`          → `daimon-macos-aarch64.tar.gz`    (Apple Silicon)
* `windows-latest`    → `daimon-windows-x86_64.zip`

Each archive contains:

```
daimon-{os}-{arch}/
    daimon[.exe]                     # Click CLI entry
    dmn-mcp[.exe]                    # FastMCP stdio server
    daimon-bundled-wezterm/          # WezTerm + locked .wezterm-version
    ...other Nuitka data...
```

The runtime resolver in `daimon.render.wezterm_bundle.bundled_wezterm_dir`
walks `Path(sys.executable).parent / "daimon-bundled-wezterm"` —
that's the contract between `scripts/build_nuitka.py` and the
runtime.

### Code-signing

Configured via repo secrets; the workflow no-ops gracefully when
they're unset (V1 ships unsigned).

| Platform | Secrets                                                                           |
|----------|-----------------------------------------------------------------------------------|
| Windows  | `WINDOWS_CERT_BASE64`, `WINDOWS_CERT_PASSWORD`                                    |
| macOS    | `MACOS_CODESIGN_CERT_BASE64`, `MACOS_CODESIGN_CERT_PASSWORD`, `MACOS_CODESIGN_IDENTITY` |

### Distribution manifests

After the release lands, bump the per-package-manager manifests
under `packaging/` and submit PRs upstream. See
`packaging/README.md` for the per-manifest checklist; the bumps are
mechanical (version + sha256s) and a future
`scripts/bump_packaging.py` will automate them.

## 2. Art manifest release (`art-vX.Y`)

Triggered by pushing an `art-vX.Y` tag.

**What runs**: `art-manifest.yml` checks out the repo (which must
contain the source card art under `art_source/` — pluggable via
`workflow_dispatch` input) and runs
`scripts/build_art_manifest.py`. The script produces:

* `manifest.json` — small (~50KB), enumerates every card with
  `card_id → {asset_name, sha256, size_bytes}` and a
  `starter_card_ids` list for prefetch.
* `card_<card_id>.tar.gz` — one per card, deterministic (zero mtime,
  fixed user/group), ~50–500KB.
* `.sha256` sidecars next to every asset.

The runtime fetches the manifest on every `daimon onboard` run and
JIT-fetches per-card tarballs the first time each card needs to
render. There is no monolithic art pack any more.

## 3. WezTerm bundle release (`wezterm-bundle-vX.Y`)

Triggered by pushing a `wezterm-bundle-vX.Y` tag.

**What runs**: `wezterm-bundle.yml` (pre-existing) downloads the
upstream WezTerm artifact for each (os, arch), repackages it as
`daimon-wezterm-{os}-{arch}.tar.gz`, and uploads it as a release
asset. This release is what `pip install daimon-engine` users get
when they run `daimon onboard` (the binary distribution route bakes
the bundle into the Nuitka tree at build time, so it doesn't read
this release).

## End-to-end release checklist

For a coordinated release that bumps everything:

1. `git tag wezterm-bundle-vX.Y && git push --tags` — produces the
   tarballs Nuitka will embed.
2. Update `WEZTERM_VERSION` in `release-binaries.yml` + `wezterm-bundle.yml`
   to point at the new bundle.
3. `git tag daimon-vX.Y.Z && git push --tags` — produces the
   standalone binaries.
4. `git tag art-vX.Y && git push --tags` — produces the lazy-art
   manifest + tarballs.
5. Bump `packaging/*` manifests and open the upstream PRs.

If only the cards changed, step 4 alone is sufficient — existing
installs auto-fetch the new manifest on their next onboarding run.

## Rollback

Every release is additive: GitHub Releases are immutable once
published, the runtime resolves "latest" via the GitHub API, and a
bad release can be marked `prerelease: true` (or deleted) to fall
back to the previous version. Local installs continue working
against the cached manifest until they explicitly re-fetch.

There is **no** manual data migration step — the manifest carries
its `pack_version`, the runtime stores it, and a refresh just lays
down a new manifest alongside the cached cards. Cards that are no
longer in the new manifest stay cached locally (no sweep) until the
user explicitly clears `~/.daimon/art/`.
