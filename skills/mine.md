# Mine — earn currency from your daily work

Your productive Claude Code (or any compatible runtime) tool calls generate
currency. **Working IS playing.** Currency buys gacha pulls
([`pull.md`](pull.md)) and skin shop entitlements ([`shop.md`](shop.md)).

## Install the hook

```bash
daimon mine install-hook
```

Registers a `PostToolUse` hook in your Claude Code settings.json that pipes
each completed tool call into `daimon mine receipt`. The hook:

1. Computes `reward = base × value × novelty × decay × drop_rate`
   (see `daimon/mining/formula.py` and `daimon/mining/protocol.md`).
2. Appends a **signed** receipt to `~/.config/daimon/mining/<date>.jsonl`.
3. Updates the rolling balance in `~/.config/daimon/mining/ledger.jsonl`.

```bash
daimon mine install-hook --dry-run    # preview the settings.json delta
daimon mine install-hook --settings /path/to/other-settings.json
```

To remove:

```bash
daimon mine uninstall-hook
```

## Inspect your ledger

```bash
daimon mine status
# balance:        347
# total mined:    647  (52 events)
# total pulled:   300  (3 events)
# total purchased:  0  (0 skins)
# ledger:         55 entries — OK
#
# recent:
#   2026-04-25T14:10:01  mine     +12   Edit
#   2026-04-25T14:10:30  mine      +8   Bash
#   2026-04-25T14:11:14  pull    -100   voltcat_apex
#   ...
```

JSON form: `daimon mine status --json`.

## Manual receipt entry

The `receipt` subcommand is the hook entrypoint — it reads a Claude Code
PostToolUse JSON event from stdin and appends a receipt to the ledger. You
should not normally invoke this by hand; the hook does. Useful for
debugging:

```bash
echo '{"tool_name": "Edit", "elapsed_ms": 230, ...}' | daimon mine receipt --verbose
```

## Anti-cheat

- Receipts are **signed** with your ed25519 identity key. Tampering breaks
  the hash chain — `daimon mine status` reports `CORRUPT` immediately.
- `value_signal` requires real side effects (return size, elapsed time) —
  you can't inflate it without doing work.
- `novelty` decays repeated work. Reading the same file 100 times pays once.
- Each ledger entry hashes the previous entry; the chain is verifiable
  end-to-end via `daimon.mining.verify_ledger`.
- The arena `mining-audit` workflow re-computes the formula on a random
  sample and files a dispute Issue if discrepancies exceed tolerance.

## What if I don't use Claude Code?

Any agent runtime can post receipts in the same format. The hook is just
convenience. The wire format is documented at `daimon/mining/protocol.md` —
post a JSON blob with `tool_name`, `success`, `elapsed_ms`, `output_bytes`
to `daimon mine receipt --verbose` over stdin and the daemon does the rest.

## Where the data lives

```
~/.config/daimon/mining/
  ledger.jsonl          # append-only signed chain (the source of truth)
  <YYYY-MM-DD>.jsonl    # daily receipt log (audit-friendly)
```

Back up `ledger.jsonl` if you back up your identity key — the two together
prove your balance to the arena auditor.
