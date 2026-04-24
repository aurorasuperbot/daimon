# Install

```bash
pip install daimon
daimon --version
```

## First match — automatic art-pack fetch

The first time you run a command that needs card art (`daimon match`,
`daimon pull`, `daimon play`, `daimon render`), DAIMON fetches the
matching art-pack from GitHub Releases — about **900 MB**, one-time:

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

## Manual update controls

```bash
daimon update              # check + install if newer
daimon update --check      # report status only
daimon update --version art-v1.0
daimon update --force      # reinstall current version
```

## Pinning / opting out

```bash
# Reproducible installs (CI, regression tests):
export DAIMON_PIN_ART=art-v1.0

# Disable auto-update entirely (you can still run `daimon update` manually):
export DAIMON_NO_AUTO_UPDATE=1

# Override where art lives (default ~/.daimon):
export DAIMON_ART_DIR=/path/to/art
```

## Path layout

```
~/.daimon/
  art/v1_alpha/                 # the live pack
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
```

## If the install fails

- Python 3.11+ required
- On Debian/Ubuntu you may need `python3-dev` for the `cryptography` build
- Network errors during the art-pack fetch: re-run `daimon update` (downloads
  resume cleanly — partial downloads use a `.partial` suffix and never
  pollute the live pack)
- Re-run pip with `pip install -v daimon` to see what's failing
- For air-gapped installs, manually fetch the tarball:
  ```bash
  gh release download art-v1.0 --repo aurorasuperbot/daimon-cards \
    --pattern 'v1_alpha.tar.gz*' --dir ~/.daimon/cache/staging/
  daimon update --version art-v1.0  # uses the cached tarball
  ```
