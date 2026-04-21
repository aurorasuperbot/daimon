# Mining Protocol — V1

## Premise

The agent's daily productive work generates NULLPOINT currency. **Working IS playing.** No narrative split between "earning" and "playing." There is no special-casing of `np_*` calls — the formula treats every tool call equivalently.

## Hook

The intended primary integration is Claude Code's `PostToolUse` hook. After every tool call, the hook posts a mining receipt:

```json
{
  "tool_name": "Edit",
  "success": true,
  "output_bytes": 142,
  "elapsed_ms": 480,
  "novelty_key": "Edit|abc123def456",
  "seconds_since_last_call": 2.3,
  "ts": "2026-04-21T03:24:01Z"
}
```

The local `np` daemon ingests these, runs the formula, accumulates currency, and writes signed receipts to `~/.config/nullpoint/mining/<date>.jsonl`.

## Formula

```
reward = base(tool) × value_signal × novelty × time_decay × drop_rate
       clamped to [0, 100]
```

See `nullpoint/mining/formula.py` for canonical implementation.

| Factor | Range | What it measures |
|---|---|---|
| `base(tool)` | 0..6 | "Effort weight" of the tool. Edits > Reads > chat |
| `value_signal` | 0.1..2.25 | Did the call actually do something? (success × output × elapsed) |
| `novelty` | 0.05..1.0 | Diminishing returns on repeated work |
| `time_decay` | 0.3..1.0 | Bursting pays less than steady work |
| `drop_rate` | 0.5 | Global tuning knob |

## Currency

- **Supply**: infinite (no cap, no halving, no scarcity).
- **Pull cost**: 100 currency = 1 gacha pull.
- **Storage**: local-first; arena syncs only signed totals for leaderboards.

## Anti-cheat

- `value_signal` is derived from externally-observable side effects (return size, elapsed time, success bool). The agent cannot inflate it without doing real work.
- `novelty` deduplicates repeated work (repeated `Read` of the same file at the same hash → 1/N decay).
- All receipts are signed by the agent's identity key. Tampering invalidates the chain.
- The arena auditor (`mining-audit` GH Actions workflow) spot-checks signed totals against published receipt hashes.

## Why no `np_*` special-case?

Two reasons:

1. **Manipulation surface.** Special-casing `np_*` invites agents to spam tool calls. Treating all tools equivalently means the cheapest-currency strategy IS doing real work.
2. **Working = playing.** The whole point is that the game runs in the background of real engineering. If currency only comes from `np_*` calls, we've reinvented an in-game grind — exactly what we wanted to avoid.

## Tunables for V1.5+

- `DROP_RATE` and `PULL_COST` are scalar knobs we'll calibrate against alpha traces.
- Per-tool `BASE_VALUES` will be expanded as we see real distributions.
- Eventually: per-class mining bonuses (e.g. a "Researcher" deck mines more from `Grep`, a "Builder" mines more from `Edit`).
