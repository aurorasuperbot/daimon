"""Claude Code `PostToolUse` hook receiver.

Reads a JSON event from stdin (the shape Claude Code sends to PostToolUse
hooks), derives the mining inputs, computes the reward via the formula
module, and appends a signed entry to the ledger.

## Event shape (Claude Code PostToolUse)

```
{
  "session_id": "...",
  "transcript_path": "...",
  "cwd": "...",
  "hook_event_name": "PostToolUse",
  "tool_name": "Edit" | "Bash" | "Read" | ...,
  "tool_input": { ... tool-specific },
  "tool_response": { ... tool-specific result }
}
```

We are intentionally lenient: the schema is owned by Claude Code and may
shift. Anything we can't parse, we treat as a zero-reward no-op rather than
crash. The hook MUST NEVER fail the agent's tool loop.

## Idempotency

Claude Code may invoke a hook twice on retries. We compute an idempotency
key from `(session_id, tool_name, novelty_key)` so duplicate events within a
session collapse to one ledger entry.

## Time decay

`seconds_since_last_call` is computed from the timestamp of the most recent
ledger entry. First call after a fresh ledger gets the same decay as a
1-minute pause (1.0).

## Output

By default, prints nothing on success and silently exits 0 on errors. With
`--verbose`, prints a one-line JSON status to stdout. Errors that prevent
appending always go to stderr (so debugging is possible) but never raise.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from daimon.mining.formula import (
    MiningInput,
    compute_reward,
    make_novelty_key,
)
from daimon.mining import ledger as _ledger_mod
from daimon.mining.ledger import (
    append_mine_entry,
    get_recent_entries,
)

# Resolve at call time so tests can monkeypatch the module-level path.
def _default_ledger() -> Path:
    return _ledger_mod.LEDGER_PATH


LEDGER_PATH = _ledger_mod.LEDGER_PATH


# Tools we never reward (chat output, bookkeeping). Keep in sync with
# formula.BASE_VALUES — these have base 0 there too, but we short-circuit
# here to skip ledger work entirely.
SKIP_TOOLS = {"Reply", "TodoWrite", "ExitPlanMode"}


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------

def _coerce_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, sort_keys=True)
    except Exception:
        return str(v)


def _output_bytes(tool_response: Any) -> int:
    """Approximate stdout/return size from the tool_response field."""
    if tool_response is None:
        return 0
    if isinstance(tool_response, str):
        return len(tool_response.encode("utf-8"))
    if isinstance(tool_response, dict):
        # Common keys Claude Code uses for tool output payloads.
        for key in ("output", "stdout", "content", "text", "result"):
            if key in tool_response:
                return _output_bytes(tool_response[key])
        # Fall back to canonical-ish JSON length.
        try:
            return len(json.dumps(tool_response).encode("utf-8"))
        except Exception:
            return 0
    if isinstance(tool_response, list):
        return sum(_output_bytes(x) for x in tool_response)
    try:
        return len(str(tool_response).encode("utf-8"))
    except Exception:
        return 0


def _success(tool_response: Any) -> bool:
    """Did the tool call succeed? Default to True if we can't tell."""
    if isinstance(tool_response, dict):
        # Claude Code uses `is_error` / `error` / `success` depending on tool.
        if tool_response.get("is_error") is True:
            return False
        if tool_response.get("success") is False:
            return False
        if "error" in tool_response and tool_response["error"]:
            return False
    return True


def _novelty_parts(tool_name: str, tool_input: Any) -> tuple:
    """Pull stable, side-effect-correlated bits from tool_input.

    The goal: novelty_key should be the same when the agent does the same
    underlying work twice, and different when the work is different. We hash
    a small fingerprint per tool — never the full input (could be huge).
    """
    if not isinstance(tool_input, dict):
        return (tool_name, _coerce_str(tool_input)[:200])

    if tool_name in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        # File-targeted: path + content fingerprint
        path = _coerce_str(tool_input.get("file_path") or tool_input.get("notebook_path"))
        body = _coerce_str(
            tool_input.get("new_string")
            or tool_input.get("content")
            or tool_input.get("new_source")
            or tool_input.get("edits")
        )
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
        return (tool_name, path, body_hash)

    if tool_name in {"Read", "Glob", "Grep"}:
        path = _coerce_str(tool_input.get("file_path") or tool_input.get("path") or "")
        pattern = _coerce_str(tool_input.get("pattern") or tool_input.get("glob") or "")
        return (tool_name, path, pattern)

    if tool_name == "Bash":
        cmd = _coerce_str(tool_input.get("command", ""))
        cmd_hash = hashlib.sha256(cmd.encode("utf-8")).hexdigest()[:12]
        return (tool_name, cmd_hash)

    # Fallback: hash the whole tool_input
    blob = _coerce_str(tool_input)
    return (tool_name, hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16])


def _seconds_since_last(path: Optional[Path] = None) -> float:
    """Wall-clock delta from the last mine entry. Defaults to 60s if none."""
    if path is None:
        path = _default_ledger()
    recent = get_recent_entries(limit=8, path=path)
    for e in reversed(recent):
        if e.get("kind") != "mine":
            continue
        ts = e.get("ts")
        if not isinstance(ts, str):
            continue
        try:
            t = _dt.datetime.fromisoformat(ts)
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dt.timezone.utc)
        delta = (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds()
        return max(0.0, delta)
    return 60.0


def _idempotency_key(session_id: str, tool_name: str,
                     novelty_key: str) -> str:
    h = hashlib.sha256()
    h.update(session_id.encode("utf-8"))
    h.update(b"|")
    h.update(tool_name.encode("utf-8"))
    h.update(b"|")
    h.update(novelty_key.encode("utf-8"))
    return h.hexdigest()[:24]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_event(event: Dict[str, Any],
                  *,
                  ledger_path: Optional[Path] = None,
                  ) -> Dict[str, Any]:
    """Process a parsed Claude Code hook event. Returns a status dict.

    Status keys:
      action:        "minted" | "skipped" | "deduped" | "noop" | "error"
      reward:        int (currency awarded, 0 on skip/error)
      tool_name:     str
      reason:        str (human-readable)
    """
    if ledger_path is None:
        ledger_path = _default_ledger()
    if not isinstance(event, dict):
        return {"action": "error", "reward": 0, "tool_name": "",
                "reason": "event is not an object"}

    tool_name = _coerce_str(event.get("tool_name"))
    if not tool_name:
        return {"action": "skipped", "reward": 0, "tool_name": "",
                "reason": "missing tool_name"}

    if tool_name in SKIP_TOOLS:
        return {"action": "skipped", "reward": 0, "tool_name": tool_name,
                "reason": "tool in skip list"}

    # Don't mine our own MCP tools — keeps the loop clean.
    if tool_name.startswith("mcp__") and "daimon" in tool_name.lower():
        return {"action": "skipped", "reward": 0, "tool_name": tool_name,
                "reason": "self-mining is forbidden"}

    tool_input = event.get("tool_input") or {}
    tool_response = event.get("tool_response")
    session_id = _coerce_str(event.get("session_id") or "no_session")

    success = _success(tool_response)
    output_bytes = _output_bytes(tool_response)

    novelty_parts = _novelty_parts(tool_name, tool_input)
    novelty_key = make_novelty_key(*novelty_parts)
    idem_key = _idempotency_key(session_id, tool_name, novelty_key)

    # Pre-check idempotency so retries report "deduped" rather than "noop"
    # (which would happen because the formula penalizes rapid repeats).
    for prior in get_recent_entries(limit=64, path=ledger_path):
        if prior.get("idempotency_key") == idem_key:
            return {"action": "deduped", "reward": 0,
                    "tool_name": tool_name,
                    "reason": "idempotency_key already in ledger"}

    inp = MiningInput(
        tool_name=tool_name,
        success=success,
        output_bytes=output_bytes,
        elapsed_ms=int(event.get("duration_ms") or 0),
        novelty_key=novelty_key,
        seconds_since_last_call=_seconds_since_last(ledger_path),
    )
    out = compute_reward(inp)

    if out.reward <= 0:
        return {"action": "noop", "reward": 0, "tool_name": tool_name,
                "reason": "formula returned 0", "factors": out.factors}

    try:
        entry = append_mine_entry(
            tool_name=tool_name,
            amount=out.reward,
            factors=out.factors,
            novelty_key=novelty_key,
            idempotency_key=idem_key,
            path=ledger_path,
        )
    except FileNotFoundError as e:
        return {"action": "error", "reward": 0, "tool_name": tool_name,
                "reason": f"no identity ({e}); run `daimon init`"}
    except Exception as e:  # noqa: BLE001 — never raise from a hook
        return {"action": "error", "reward": 0, "tool_name": tool_name,
                "reason": f"{type(e).__name__}: {e}"}

    if entry is None:
        return {"action": "deduped", "reward": 0, "tool_name": tool_name,
                "reason": "idempotency_key already in ledger"}

    return {
        "action": "minted",
        "reward": out.reward,
        "tool_name": tool_name,
        "reason": "ok",
        "factors": out.factors,
    }


def main(argv: Optional[list] = None) -> int:
    """CLI entrypoint. Reads stdin JSON, processes, returns 0 always.

    Exit code 0 always — we never want a hook failure to break the agent's
    tool loop. Errors are written to stderr (visible in `claude --debug`).
    """
    import argparse

    p = argparse.ArgumentParser(prog="daimon mine receipt")
    p.add_argument("--verbose", action="store_true",
                   help="Emit a one-line JSON status to stdout.")
    p.add_argument("--ledger", default=None,
                   help="Override ledger path (testing).")
    args = p.parse_args(argv)

    ledger_path = Path(args.ledger) if args.ledger else _default_ledger()

    try:
        raw = sys.stdin.read()
    except Exception as e:  # noqa: BLE001
        print(f"daimon mine receipt: stdin read failed: {e}", file=sys.stderr)
        return 0

    if not raw.strip():
        if args.verbose:
            print(json.dumps({"action": "noop", "reason": "empty stdin"}))
        return 0

    try:
        event = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"daimon mine receipt: bad JSON: {e}", file=sys.stderr)
        if args.verbose:
            print(json.dumps({"action": "error", "reason": f"bad json: {e}"}))
        return 0

    try:
        status = process_event(event, ledger_path=ledger_path)
    except Exception as e:  # noqa: BLE001 — defense in depth
        print(f"daimon mine receipt: unhandled {type(e).__name__}: {e}",
              file=sys.stderr)
        if args.verbose:
            print(json.dumps({"action": "error",
                              "reason": f"{type(e).__name__}: {e}"}))
        return 0

    if args.verbose:
        print(json.dumps(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
