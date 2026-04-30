# PvP live smoke

End-to-end validation that the engine ↔ live arena workflow chain
works in production. Drives a real PvP cycle (challenge → accept →
two reveals → arbiter) against the actual `aurorasuperbot/daimon-arena`
GitHub repo and waits for settlement.

## When to run

- Before any release tag.
- After any change to `daimon/arena/encoding.py`,
  `daimon/arena/ops.py`, or any of the four arena workflows
  (`arbiter.yml`, `validator.yml`, `mining-audit.yml`,
  `trade-settle.yml`).
- After rotating `ENGINE_READ_TOKEN` or changing the engine pip URL.

## What it leaves behind

One settled match record at `matches/<issue>.json` plus a leaderboard
update with two synthetic pubkeys at the bottom. Pre-launch this is
fine — wipe `matches/` and reset `leaderboard.json` to empty rankings
before V1 ships.

## Prerequisites

- `gh` CLI authenticated as `aurorasuperbot` (or anyone with write
  access to the arena).
- Engine installed (`pip install daimon` or `uv pip install daimon`).
- Network access to `api.github.com`.
- The arena's `ENGINE_READ_TOKEN` secret must be set, otherwise the
  arbiter falls back to self-test only and never settles real matches.

## Run

```bash
cd daimon
python tools/pvp_live_smoke/run.py
```

Expect ~60-180s end to end (most of which is waiting for the GitHub
Actions runner to pick up the `issue_comment` trigger after the
second reveal).

## Exit codes

- `0` — match settled, record fetched, winner pubkey is one of the
  two synthetic identities, loadouts round-tripped.
- `1` — one of the `dm_pvp_*` MCP tools returned an error envelope.
- `2` — arbiter never settled within `PVP_SMOKE_MAX_WAIT` (default
  300s).

## Optional env

- `DAIMON_ARENA_REPO` — override the arena (e.g. for a fork).
- `PVP_SMOKE_POLL_SEC` — poll interval (default 30).
- `PVP_SMOKE_MAX_WAIT` — max wait for arbiter (default 300).

## What this caught (2026-04-27 first run)

The first live-smoke run on 2026-04-27 surfaced three production bugs
that the hermetic `tests/test_arena_smoke.py` could not have:

1. **`fetch_repo_file` was broken on private repos.** The engine tried
   `raw.githubusercontent.com` first and gave up on 404 — but private
   repos ALWAYS 404 from raw without auth. Fixed by routing through
   `gh api repos/<repo>/contents/<path>` exclusively. (Bonus: kills
   the post-settlement CDN-cache race on the matches/N.json read.)

2. **`update_leaderboard` was non-idempotent.** Three concurrent
   arbiter runs (one per `issue_comment`) would have triple-counted
   wins/losses if their pushes hadn't raced and rejected. Fixed in
   `arena/scripts/arbitrate.py` with a `settled_match_ids` guard;
   `write_match_record` now also refuses to overwrite a settled match
   with different content (cheat / arbiter-drift detection).

3. **`arbiter.yml` had no concurrency control.** Three runs raced on
   `git push`; one won, the rest hit `! [rejected]`. Even after adding
   a `concurrency: arbiter-issue-N` group, queued runs still rebuilt
   the same match because `actions/checkout` pins to the event-time
   SHA. Fixed by syncing to latest `main` after checkout + an early
   skip-if-settled exit when matches/N.json already on main.

Re-running the smoke after all three fixes produced a clean settle:
two arbiter runs, both `success`, no leaderboard double-count.

If the smoke ever regresses again, expect another lurking production
bug — that's the whole point of running it.
