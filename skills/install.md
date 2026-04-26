# Install

DAIMON ships in two distribution shapes; pick whichever matches how
you got here:

* **Package-manager install** (recommended for end users) — the
  WezTerm bundle is baked into the binary at build time, so there's
  nothing to bootstrap separately. Install via your OS package
  manager and run `daimon onboard`.
* **Source install** (`pip install daimon-engine`) — the engine ships
  pure-Python; the WezTerm bundle is downloaded on first
  `daimon onboard` run.

Either way, **one command sets everything up**:

```bash
daimon onboard
```

It folds the previous four-step bootstrap (`daimon install` →
`daimon init` → `daimon mine install-hook` → MCP wiring) into a
single interactive flow:

1. Generate an ed25519 identity + 24-word BIP39 recovery mnemonic.
2. Display the mnemonic and require you to type "I have saved my
   recovery phrase" before continuing. The mnemonic is also written
   to `~/.config/daimon/recovery.txt` (mode 0600) as a backstop.
3. Fetch the card manifest + the starter cards' art (small, blocking).
   Spawn a detached background prefetcher for the rest of the cards.
4. Atomically write the daimon `mcpServers` entry + PostToolUse
   mining hook into `~/.claude/settings.json` (one read, one backup,
   one write).

Verify with `daimon doctor`:

```bash
daimon doctor
# == DAIMON doctor ==
# [bundle]      embedded   (daimon-bundled-wezterm/)
# [art]         art-v1.0   (12 cards cached, prefetcher pid 4242)
# [identity]    yes        (~/.config/daimon/identity.key)
# [claude code] wired      (~/.claude/settings.json — mcpServers.daimon, PostToolUse hook)
```

> **Note:** the PyPI distribution name is `daimon-engine` (the bare
> `daimon` name on PyPI is owned by an unrelated project). The import
> name and CLI command are still `daimon` / `dmn`.

## Re-running is safe

`daimon onboard` is idempotent — re-running preserves an existing
identity and Claude Code wiring, refreshes a stale manifest, and
fetches any cards that have appeared since the last run. Pass
`--force` to overwrite the identity (DESTRUCTIVE — your collection
ledger position is lost unless you have the mnemonic).

## Skipping pieces

```bash
daimon onboard --no-claude-code     # don't write to ~/.claude/settings.json
daimon onboard --no-prefetch        # don't spawn the background card fetcher
daimon onboard --yes                # skip the mnemonic confirmation gate (CI)
daimon onboard --json               # emit the result envelope as JSON
```

## Why a bundled terminal?

DAIMON renders card art via the **Kitty Graphics Protocol**, which
works only on terminals that implement it AND at the DPI / cell-size
/ colour-space we designed for. Rather than degrading to ASCII /
half-block fallbacks across twenty-some terminals, DAIMON ships its
own WezTerm binary so every player sees pixel-perfect art at the
locked render parameters.

* **Binary distributions** ship WezTerm inside the standalone tree
  (under `daimon-bundled-wezterm/` next to the `daimon` binary).
  Nothing to download.
* **`pip install daimon-engine`** users get WezTerm fetched on first
  `daimon onboard` run from `aurorasuperbot/daimon` GitHub Releases
  (sha256-verified, atomic swap into `~/.daimon/bin/`).

When you launch an interactive command (`daimon shop`,
`daimon collection`, `daimon loadout edit`, `daimon play`), DAIMON
re-execs into the bundled WezTerm window. This happens automatically;
the env var `DAIMON_INSIDE_TERMINAL=1` is set inside the relaunched
process so nested commands skip the relaunch.

To render in your current terminal anyway, pass `--in-place`:

```bash
daimon shop --in-place           # use the host terminal (degraded art)
```

## HUD auto-spawn

The first `dm_match` / `dm_pull` MCP call (or `daimon match` /
`daimon pull` from your shell) auto-spawns a spectator HUD window in
the bundled WezTerm so you see the result animate. Opt out with:

```bash
export DAIMON_NO_AUTO_HUD=1      # never auto-spawn the HUD
```

## Lazy art

Card art is fetched JIT, per-card, the first time a card needs to
render — then cached locally. Onboarding fetches a small
`manifest.json` (~50KB) plus the **starter** cards' art (the cards
your first ten pulls might surface). A detached background
prefetcher lands the rest while you play.

To pin an art pack version (CI, regression tests):

```bash
export DAIMON_PIN_ART=art-v1.0
```

To opt out of background fetches entirely:

```bash
export DAIMON_NO_AUTO_UPDATE=1
```

## Path layout

```
~/.daimon/
  bin/                          # source-install WezTerm (only for `pip install`)
    wezterm                     # binary distros put this under daimon-bundled-wezterm/
    wezterm-gui
    .wezterm-version
  etc/
    wezterm.lua                 # locked render config
  art/v1_alpha/                 # per-card cache, populated lazily
    manifest.json
    <card_id>/
      base.png
      manifest.json
  cache/
    staging/                    # JIT-fetch scratch
    prefetch_state.json         # background prefetcher resume state

~/.config/daimon/
  identity.key                  # ed25519 private key (chmod 600)
  identity.pub                  # public key
  recovery.txt                  # 24-word BIP39 mnemonic (chmod 600 on POSIX)
  play.pid                      # HUD process PID (for auto-spawn dedup)
  state.json                    # the file the HUD watches
```

## If onboarding fails

* Python 3.11+ required for the `pip install` route.
* On Debian/Ubuntu you may need `python3-dev` for the `cryptography`
  build.
* Network errors during manifest fetch: re-run `daimon onboard`. The
  manifest fetch is the only blocking network call; per-card art
  fetches happen JIT and never block the CLI.
* Linux aarch64 is not supported — upstream WezTerm has no official
  ARM build.
* Re-run with `daimon onboard --json` to get a structured envelope
  showing exactly which step failed.

## Removing DAIMON

```bash
rm -rf ~/.daimon                # bundled WezTerm + art cache
rm -rf ~/.config/daimon         # identity keys (KEEP A BACKUP — see identity.md)
# remove the daimon mcpServers entry + PostToolUse hook:
daimon mine uninstall-hook      # also removes the MCP entry
# package-manager install:
brew uninstall daimon           # or `winget uninstall ...`, `scoop uninstall ...`, etc.
# pip install:
pip uninstall daimon-engine
```
