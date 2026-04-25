# Install

DAIMON has **three** one-time bootstrap steps. Run them in order; each is
idempotent so re-running is safe.

```bash
pip install daimon-engine        # 1. engine + CLI + MCP server (~5 MB)
daimon install                   # 2. bundled WezTerm + locked config (~30 MB)
daimon init                      # 3. ed25519 identity + 24-word recovery phrase
```

Verify with `daimon doctor`:

```bash
daimon doctor
# == DAIMON doctor ==
#
# [bundle]
#   installed:  yes  (wezterm-bundle-v1.0)
#   bin:        ~/.daimon/bin/wezterm
#   config:     ~/.daimon/etc/wezterm.lua
#
# [art pack]
#   installed:  art-v1.0  (auto-fetched on first match)
#   pack dir:   ~/.daimon/art/v1_alpha
#
# [identity]
#   generated:  yes
#   key:        ~/.config/daimon/identity.key
```

> **Note:** the PyPI distribution name is `daimon-engine` (the bare `daimon`
> name on PyPI is owned by an unrelated project). The import name and CLI
> command are still `daimon` / `dmn`.

## Why a bundled terminal? (`daimon install`)

DAIMON renders card art via the **Kitty Graphics Protocol**, which works only
on terminals that implement it AND at the DPI / cell-size / colour-space we
designed for. Rather than downgrading to ASCII / half-block fallbacks across
twenty-some terminals, DAIMON ships its own WezTerm binary so every player
sees pixel-perfect art at the locked render parameters.

`daimon install`:

1. Detects your OS+arch (linux/macos/windows × x86_64/aarch64).
2. Downloads the matching `daimon-wezterm-{os}-{arch}.tar.gz` from
   `aurorasuperbot/daimon-engine` GitHub Releases.
3. Verifies the SHA-256 against the published `.sha256` sidecar.
4. Atomically swaps the new binary into `~/.daimon/bin/`.
5. Writes the locked Lua config to `~/.daimon/etc/wezterm.lua`.
6. Smoke-tests the binary with `wezterm --version`.

Re-running with the same release tag is a no-op (skips the download). Pass
`--force` to re-fetch.

```bash
daimon install                   # latest release, idempotent
daimon install --version wezterm-bundle-v1.0   # pin to a specific release
daimon install --force           # re-download even if up to date
daimon install --no-smoke-test   # skip `wezterm --version` (CI only)
```

When you launch an interactive command (`daimon shop`, `daimon collection`,
`daimon loadout edit`, `daimon play`), DAIMON re-execs into the bundled
WezTerm window. This happens automatically; the env var
`DAIMON_INSIDE_TERMINAL=1` is set inside the relaunched process so nested
commands skip the relaunch.

To render in your current terminal anyway, pass `--in-place`:

```bash
daimon shop --in-place           # use the host terminal (degraded art)
```

The auto-relaunch is also bypassed when:

- stdout is piped (`daimon shop | jq` always uses text mode)
- you pass `--no-tui` / `--json` (forces text/JSON output)
- on Linux, neither `$DISPLAY` nor `$WAYLAND_DISPLAY` is set
- the bundle isn't installed yet (a hint is printed; the in-place TUI runs)

## First match — automatic art-pack fetch

After `daimon install` finishes, the first command that needs card art
(`daimon match`, `daimon pull`, `daimon play`, `daimon render`) fetches
the matching art-pack from GitHub Releases — about **900 MB**, one-time:

```bash
$ daimon match tests/fixtures/sample_loadout_a.json tests/fixtures/sample_loadout_b.json --seed 01...
daimon: no art pack installed — fetching latest (art-v*)...
daimon: fetching art-v1.0 [████████████] 100%  908.0 MB / 908.0 MB
daimon: installed art-v1.0 into ~/.daimon/art/v1_alpha/
seed:     0000...0001
winner:   1
reason:   side_a hp <= 0
hp_a:     0
hp_b:     54
rounds:   3
```

The same seed gives the same result, every time.

## Subsequent runs — silent background updates

After the first install, every CLI invocation does a **rate-limited (24 h)
background check** for newer `art-v*` releases. You'll never see a download
in the foreground; if a new release is found it's downloaded and atomically
swapped in by a detached process. Your next invocation sees the new pack.

The bundled WezTerm itself does NOT auto-update — re-run `daimon install`
when you upgrade `daimon-engine` if release notes call for a new bundle.

## Manual update controls

```bash
# Art pack
daimon update              # check + install if newer
daimon update --check      # report status only
daimon update --version art-v1.0
daimon update --force      # reinstall current version

# WezTerm bundle
daimon install             # idempotent — no-op if up to date
daimon install --force     # re-download
```

## Pinning / opting out

```bash
# Reproducible installs (CI, regression tests):
export DAIMON_PIN_ART=art-v1.0
export DAIMON_PIN_BUNDLE=wezterm-bundle-v1.0

# Disable auto-update entirely (you can still run `daimon update` manually):
export DAIMON_NO_AUTO_UPDATE=1

# Override where art + binaries live (default ~/.daimon):
export DAIMON_ART_DIR=/path/to/runtime
```

## Path layout

```
~/.daimon/
  bin/                          # bundled WezTerm (managed by `daimon install`)
    wezterm                     # or wezterm.exe on Windows
    wezterm-gui
    .wezterm-version            # marker file — release tag
  etc/
    wezterm.lua                 # locked render config (rewritten on every install)
  art/v1_alpha/                 # the live pack (managed by `daimon update`)
    .version                    # "art-v1.0"
    .checksum                   # sha256 of the source tarball
    <card_id>/
      base.png
      manifest.json
      variants/v0.png ...
  cache/
    last_check.json             # rate-limit state
    update.log                  # background-check log
    staging/                    # download scratch (cleaned after install)

~/.config/daimon/
  identity.key                  # ed25519 private key (chmod 600)
  identity.pub                  # public key
  recovery.txt                  # 24-word BIP39 mnemonic
```

## If the install fails

- Python 3.11+ required.
- On Debian/Ubuntu you may need `python3-dev` for the `cryptography` build.
- `daimon install` failures: re-run with `daimon install --force` to re-fetch.
  The download is sha-verified, so corruption mid-flight is detected.
- `daimon install` on Linux ARM (aarch64) is not supported — upstream WezTerm
  has no official Linux ARM build.
- Network errors during the art-pack fetch: re-run `daimon update` (downloads
  resume cleanly — partial downloads use a `.partial` suffix and never
  pollute the live pack).
- Re-run pip with `pip install -v daimon-engine` to see what's failing.
- For air-gapped installs, manually fetch the bundle:
  ```bash
  gh release download wezterm-bundle-v1.0 --repo aurorasuperbot/daimon-engine \
    --pattern 'daimon-wezterm-linux-x86_64.tar.gz*' --dir ~/.daimon/cache/staging/
  daimon install --version wezterm-bundle-v1.0   # uses the cached tarball
  ```

## Removing DAIMON

```bash
rm -rf ~/.daimon                # bundled WezTerm + art pack + caches
rm -rf ~/.config/daimon         # identity keys (KEEP A BACKUP — see identity.md)
pip uninstall daimon-engine
```
