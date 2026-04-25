# Recover an identity from a saved mnemonic

If you saved your 24-word BIP39 mnemonic at `daimon init` time, you can
reconstruct the entire identity (private key + public key + metadata) from
the mnemonic alone — no other state required.

## Quick start

```bash
daimon recover
# Enter your 24-word recovery mnemonic: ************************
# Identity restored from mnemonic.
#
#   pubkey:  ebc9cf01e5...
#   stored:  ~/.config/daimon/identity.key (mode 0600)
```

The CLI prompts on stdin with no echo — the phrase never appears in shell
history, `ps`, or terminal scrollback.

## Script-friendly form (less safe)

```bash
daimon recover --mnemonic "abandon ability able about ... wisdom wolf woman"
```

The `--mnemonic` flag puts the phrase in your shell history and any
process listing on the machine. Use only on a single-user host with a
trusted shell. The interactive prompt is the recommended path.

## Refusal modes (designed-in)

DAIMON refuses to overwrite an existing identity unless `--force` is
passed:

```bash
daimon recover
# error: Identity exists at ~/.config/daimon/identity.key. Pass force=True to overwrite.
# hint: pass --force to overwrite the existing identity.
```

`--force` is **DESTRUCTIVE** — the old key + any unsigned local collection
that didn't make it to the arena is lost forever (unless that key's
mnemonic was also saved).

The mnemonic is checksum-verified before any disk write. An invalid
mnemonic fails fast with no side effects:

```bash
daimon recover --mnemonic "this is not a valid bip39 phrase"
# error: Invalid BIP39 mnemonic (failed checksum)
```

## What gets written

```
~/.config/daimon/
  identity.key                  # ed25519 private key, PKCS8 PEM, mode 0600
  identity.pub                  # public key hex (newline-terminated)
  identity.json                 # metadata: {pubkey_hex, created_at, restored_from_mnemonic: true, version: 1}
```

The `restored_from_mnemonic: true` flag in metadata is informational —
nothing in the engine treats restored identities differently from
freshly-generated ones. Same pubkey, same signing power, same collection
provenance.

## What does NOT come back

The mnemonic restores the **identity** (the keypair). It does NOT restore:

- **Mining ledger** — `~/.config/daimon/mining/ledger.jsonl` lives only on
  the machine where it was earned. If you didn't back it up, the local
  balance is gone. The arena auditor's published totals from past matches
  remain.
- **Owned card serials** — your `~/.config/daimon/collection.json` is local
  state. Cards minted via `daimon pull` need to be re-fetched from the
  arena's view of your account (not yet exposed as a one-shot CLI;
  `dm_collection` reads only the local file in V1).
- **Saved loadouts** — `~/.config/daimon/loadouts/*.json` is local. Back
  these up alongside `identity.key` if you care about deck history.

The takeaway: **back up the mnemonic *and* the `~/.config/daimon/` dir**.
The mnemonic alone proves you're you; the directory preserves what you've
done.

## Python API

```python
from daimon.identity import restore_from_mnemonic
identity = restore_from_mnemonic("...", force=False)
print(identity.pubkey_hex)
```

Raises `ValueError` on bad checksum, `FileExistsError` if `force=False`
and a key already exists. Use this for tests and scripted bootstraps; the
CLI is a thin wrapper.
