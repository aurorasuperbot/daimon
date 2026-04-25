# Identity

DAIMON identity = ed25519 keypair. Your pubkey is your canonical handle.
GitHub OAuth binding (separate, optional) is added by signing an assertion
at `daimon-arena/identities/<github-handle>.json`.

## Generate a new identity

```bash
daimon init
```

This:
- Generates a fresh ed25519 keypair
- Writes private key to `~/.config/daimon/identity.key` (mode 0600)
- Writes public key to `~/.config/daimon/identity.pub`
- **Prints a 24-word BIP39 mnemonic ONCE.** Save it.

If you lose both your key file AND your mnemonic, your collection is
unrecoverable. There is no central reset.

`daimon init --force` overwrites an existing identity (DESTRUCTIVE — old
collection + ledger position become unrecoverable unless the old mnemonic
was saved).

## Recover from mnemonic

```bash
daimon recover                          # prompts for mnemonic on stdin (no echo)
daimon recover --mnemonic "abandon ability able about ... wisdom wolf woman"
daimon recover --force                  # overwrite an existing identity
```

The `--mnemonic` flag is convenient for scripts but exposes the phrase to
shell history and `ps` listings. Prefer the prompt form on shared machines.

The Python API is also exposed:

```python
from daimon.identity import restore_from_mnemonic
restore_from_mnemonic("twelve words ... here")
```

For deeper detail on the recovery flow, see [`recover.md`](recover.md).

## Bind to a GitHub account (optional, V1.1)

You sign a JSON assertion linking your pubkey to your GitHub handle, then
PR it into `daimon-arena/identities/<handle>.json`. The arbiter verifies
the signature on merge. Multiple pubkeys per handle are allowed (one per
machine).

## Why both?

- Pubkey first because **math doesn't trust GitHub** — your collection's
  authenticity does not depend on any social platform.
- GitHub second because Issues + Actions need a stable social identifier
  for arbitration and reputation.
