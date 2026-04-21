# Mine — V1.1

> **Not yet implemented.** This file describes the planned hook integration; nothing here is callable in V0.1 alpha. The mining FORMULA is implemented and tested at `nullpoint/mining/formula.py`.

## What mining is

Your daily productive work generates currency. The currency buys gacha pulls. **Working IS playing.**

## How to set it up (Claude Code, the primary target)

Add a `PostToolUse` hook to your Claude Code config that posts to the local `np` daemon:

```json
{
  "PostToolUse": "np mining-receipt --tool '$tool_name' --success '$success' --bytes '$output_bytes' --elapsed '$elapsed_ms'"
}
```

The daemon:
1. Computes `reward = base × value × novelty × decay × drop_rate` (see `mining/protocol.md`)
2. Appends a signed receipt to `~/.config/nullpoint/mining/<date>.jsonl`
3. Updates your balance

## Anti-cheat

- Receipts are signed. Tampering invalidates the chain.
- `value_signal` requires real side effects (return size, elapsed time). You can't inflate it without doing work.
- `novelty` decays repeated work. Reading the same file 100 times pays once.
- The arena auditor spot-checks signed totals.

## What if I don't use Claude Code?

Any agent runtime can post receipts in the same format. The hook is just convenience.
