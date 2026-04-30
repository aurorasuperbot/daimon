# Duel (PvP)

Async PvP over GitHub Issues using commit-reveal with Ed25519 signatures.
Matches are settled by an automated arbiter (GitHub Actions workflow) that
verifies signatures, enforces commit integrity, and runs the canonical engine
deterministically. The full E2E pipeline — challenge through settlement —
is live and tested against `aurorasuperbot/daimon-arena`.

## Repos

| Repo | Role |
|------|------|
| `aurorasuperbot/daimon` | Engine, arena client/ops/encoding, MCP tools, web UI |
| `aurorasuperbot/daimon-arena` | GitHub Issues state machine, arbiter script + workflow, match records, leaderboard |
| `aurorasuperbot/daimon-cards` | Card proposals (separate governance) |

## Protocol (commit-reveal, single loadout commitment)

The joint seed is **derived** from both players' commits + nonces, so neither
player can grind a favorable seed without revealing their loadout first.

All signing payloads are domain-separated by a protocol version prefix
(`daimon-pvp-v1`, `daimon-register-v1`, `daimon-dispute-v1`,
`daimon-card-propose-v1`) to prevent cross-context signature replay.

### Phase 1 — Challenge (Issue body)

Player A opens an Issue with label `match-challenge`. Body fields
(source: `arena/ops.py::pvp_challenge`):

```
challenger_pubkey:  <64-char hex Ed25519 pubkey>
opponent_pubkey:    <64-char hex Ed25519 pubkey>
opponent_handle:    <first 16 chars of opponent pubkey — display only>
pack_pin:           <oci-tag, e.g. starter-v1.0.0>
rule_set:           standard-v1
loadout_commit:     <sha256(canonical_json(loadout) || nonce_a), hex>
challenged_at:      <ISO-8601 UTC timestamp>
memo:               <optional free-text, max 280 chars>
protocol:           daimon-pvp-v1
```

`canonical_json` = `json.dumps(obj, sort_keys=True, separators=(",",":")).encode("utf-8")`.
`nonce_a` = 32 random bytes (hex), kept secret until reveal.
Loadout must contain exactly 6 cards.

Labels applied: `match-challenge`, `pvp`, `pending-accept`.

### Phase 2 — Accept (comment starting with `/accept`)

Opponent comments starting with `/accept`. Body fields
(source: `arena/ops.py::pvp_accept`):

```
/accept

opponent_pubkey:  <64-char hex — responder's own pubkey>
loadout_commit:   <sha256(canonical_json(loadout) || nonce_b), hex>
accepted_at:      <ISO-8601 UTC>
protocol:         daimon-pvp-v1
```

### Phase 3 — Reveal (one `/reveal` comment per player, either order)

Each player posts a `/reveal` comment. The arbiter matches reveals to
sides **by embedded pubkey**, not by comment arrival order — so either
player can reveal first. Body fields
(source: `arena/ops.py::pvp_reveal`):

````
/reveal

pubkey:     <64-char hex — the revealing player's pubkey>
nonce:      <64-char hex (32 bytes)>
signature:  <Ed25519 signature, hex>
protocol:   daimon-pvp-v1

```json
{"cards": [...full 6-card loadout...]}
```
````

Signature payload (must match `encoding.py::pvp_signing_payload` and
`arbitrate.py::signing_payload` byte-for-byte):

```
b"daimon-pvp-v1\n"
+ str(issue_number).encode()
+ b"\n"
+ canonical_json(loadout)
+ b"\n"
+ bytes.fromhex(nonce)
```

The arbiter rejects the reveal if:
- `sha256(canonical_json(loadout) || nonce) != loadout_commit` (tamper)
- The signature does not verify against the player's bound pubkey (forgery)
- The pubkey doesn't match either challenger or opponent on this Issue

### Phase 4 — Arbitration (automatic via GitHub Actions)

Joint seed derivation (source: `encoding.py::derive_joint_seed`):

```
sha256(
  b"daimon-pvp-seed-v1\n"
  + str(issue_number).encode() + b"\n"
  + commit_a.encode() + b"\n"
  + commit_b.encode() + b"\n"
  + nonce_a.encode() + b"\n"
  + nonce_b.encode()
)
```

Nonces are bound to challenger/opponent sides (not comment order), so the
seed is stable regardless of who reveals first.

The arbiter (`scripts/arbitrate.py`):
1. Parses all 4 phase bodies (challenge, accept, reveal×2)
2. Dispatches reveals to correct sides by embedded pubkey
3. Verifies commit hashes and Ed25519 signatures for both sides
4. Builds engine `Loadout` objects (exactly 6 cards each, validated via `load_card_dict`)
5. Derives the joint seed
6. Runs `resolve_match(loadout_a, loadout_b, seed)` — deterministic engine resolution
7. Writes `matches/<issue>.json` (idempotent — refuses to overwrite different content)
8. Updates `leaderboard.json` (idempotent via `settled_match_ids` guard)
9. Posts outcome comment, locks + closes the Issue

## Arbiter workflow (`arbiter.yml`)

Triggers on `issue_comment` (created) for Issues with `match-challenge` label.
Also supports `workflow_dispatch` for manual re-runs.

Key design:

- **Per-issue serialization**: `concurrency: arbiter-issue-${{ issue_number }}` with
  `cancel-in-progress: false` — runs queue rather than race.
- **Sync-to-main**: after checkout, `git fetch origin main && git reset --hard origin/main`
  to see artifacts from prior arbiter runs (actions/checkout pins to event-time SHA).
- **Skip-if-settled**: if `matches/<issue>.json` already exists on main, exit cleanly.
- **Comment filtering**: Python parser filters by `author_association != "NONE"` to
  prevent unauthenticated users from injecting fake `/accept` or `/reveal` comments.
- **Comment cap**: fetches max 100 per page (`?per_page=100`), Python-side cap at 200.
- **Retry push**: up to 3 attempts with `git pull --rebase` between — handles sibling
  workflows landing commits between checkout and push.
- **Engine install**: `pip install "git+...@v2.1.0"` via `ENGINE_READ_TOKEN` secret
  (fine-grained PAT scoped to `repo:read` on `aurorasuperbot/daimon`). The token is
  injected via `git config --global url.insteadOf` and cleaned up immediately after.

## Cheat detection

| Signal | Outcome |
|--------|---------|
| Commit hash mismatch (loadout tampered after commit) | `reason: "cheat"`, errors logged in match record |
| Signature fails verification | `reason: "cheat"`, match record written with errors |
| Reveal pubkey doesn't match either side | `reason: "cheat"` |
| Duplicate reveals from same pubkey | `reason: "cheat"` |
| Loadout fails engine schema (`load_card_dict`) | `reason: "invalid_loadout"` |
| Loadout card count != 6 | `reason: "invalid_loadout"` |

The match record (`matches/<issue>.json`) is the canonical receipt — it
includes all errors, both loadouts, seed, round count, and HP totals.
The leaderboard is NOT updated for failed/cheated matches.

## Security hardening (2026-04-30)

### Web layer (`daimon/web/`)
- **CORS**: `_LocalOriginMiddleware` rejects non-localhost `Origin` headers
- **Session tokens**: `_dev/eval` and `_dev/goto` require per-process `X-Session-Token`
  (obtained via same-origin `GET /api/_dev/token`)
- **Rate limiting**: sliding-window `_RateLimiter` — 3/min for Issue creation
  (register, challenge), 6/min for comments (accept, reveal)
- **Input validation**: Pydantic `Field` constraints on all PvP models — pubkey
  must be exactly 64 hex chars, loadout_name max 48, memo max 280, challenge_id
  digits-only max 16
- **Query constraints**: `ge=1, le=100` on leaderboard/matches limit params
- **Path traversal**: `/art/{card_id}` validated against `^[A-Za-z0-9_\-]+$`
- **Error sanitization**: exception details not leaked to client

### Arena client (`daimon/arena/client.py`)
- Path validation: rejects `..` and leading `/` in `fetch_repo_file`
- URL-safe ref encoding: `urllib.parse.quote(ref)` prevents ref injection
- Subprocess timeout: 20s default, hard kill on timeout
- No secrets in error messages

### Arena ops (`daimon/arena/ops.py`)
- Loadout validation: exactly 6 cards enforced before signing
- Identity pubkey redaction: only first 16 chars exposed in error messages
- Safe `__repr__` on `Identity` dataclass (no private key leakage)

### Arbiter (`scripts/arbitrate.py`)
- Input body size cap: 64 KiB max per phase body
- Card count enforcement: exactly 6 cards before per-card validation
- Positive-int validation on `issue_number` in `write_match_record`
- `settled_match_ids` normalized to int on load (prevents type confusion)
- Idempotent writes: refuses to overwrite existing match with different content

## Leaderboard + tiers

Tiers are wins-based (source: `arena/ops.py::TIER_THRESHOLDS`):

| Tier | Wins required |
|------|--------------|
| Champion | 50+ |
| Elite | 25+ |
| Veteran | 10+ |
| Novice | 3+ |
| Rookie | 0+ |

`leaderboard.json` structure:
```json
{
  "version": 1,
  "entries": { "<pubkey>": {"wins": N, "losses": N, "draws": N}, ... },
  "settled_match_ids": [4, 5, ...],
  "last_updated": "ISO-8601"
}
```

Ranking: wins descending, then losses ascending as tiebreaker.

## Local state

In-flight match secrets stored at `~/.daimon/pvp_state/<issue_number>.json`
(source: `arena/state.py`). Directory mode 0700, file mode 0600. Contains
nonce, loadout, side, and owner pubkey. Cleaned up after settlement.

## HTTP API (web UI)

8 PvP endpoints on the local web server (source: `web/routes.py`):

| Method | Path | Backend |
|--------|------|---------|
| GET | `/api/pvp/leaderboard?limit=` | `dm_leaderboard` |
| GET | `/api/pvp/my-rank` | `dm_my_rank` |
| GET | `/api/pvp/matches?limit=` | `dm_pvp_my_matches` |
| GET | `/api/pvp/status/{challenge_id}` | `dm_pvp_status` |
| POST | `/api/pvp/register` | `dm_register` |
| POST | `/api/pvp/challenge` | `dm_pvp_challenge` (resolves loadout by name first) |
| POST | `/api/pvp/accept` | `dm_pvp_accept` (resolves loadout by name first) |
| POST | `/api/pvp/reveal` | `dm_pvp_reveal` |

## Additional protocol surfaces

Beyond PvP, the signing framework supports (source: `encoding.py`):

- **Identity registration** (`daimon-register-v1`): binds pubkey to handle
- **Match disputes** (`daimon-dispute-v1`): signed appeals with bond amount
- **Card proposals** (`daimon-card-propose-v1`): signed card definitions to cards repo

## Local testing

```bash
# Arbiter self-test (runs synthetic full protocol round-trip)
cd daimon-arena
python scripts/arbitrate.py --self-test

# Engine test suite (includes arena encoding tests)
cd daimon
uv run pytest tests/ -x
```

The self-test exercises happy-path resolution, reveal-order-swap (proves
pubkey-based dispatch), and tamper detection (commit hash mismatch).

## E2E validation

Live smoke test tooling at `tools/pvp_live_smoke/`. Drives a real PvP cycle
against the actual arena repo and waits for arbiter settlement. See
`tools/pvp_live_smoke/README.md` for prerequisites and run instructions.

First successful E2E settlement: Issue #4 on `aurorasuperbot/daimon-arena`
(2026-04-30) — bob won by wipe, 0/120 HP, 18 rounds.
