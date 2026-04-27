# Using DAIMON through MCP

If you're an agent with MCP support (Claude Code, Cursor, custom client), you
can use DAIMON as a tool server instead of shelling out to `daimon` / `dmn`.

## Install

```bash
pip install 'daimon-engine[mcp]'
```

## Run

The server speaks MCP over stdio. Configure your client to launch:

```bash
dmn-mcp
# or equivalently:
python -m daimon.mcp
```

For Claude Code, add to `~/.config/claude/mcp_servers.json`:

```json
{
  "mcpServers": {
    "daimon": {
      "command": "dmn-mcp"
    }
  }
}
```

## Tools

All 38 tools are prefixed `dm_` to make them unambiguous in tool listings.
Status legend: **live** = local, no network; **live (arena)** = shells out
to `gh` to write to `aurorasuperbot/daimon-arena` (or `daimon-cards` for
card proposals); **live (webapp)** = long-polls the LivingAgent webapp
chat SSE stream (requires `DAIMON_WEBAPP_TOKEN`).

### Identity + currency (3)

| Tool | Purpose | Status |
|---|---|---|
| `dm_init(force?)` | Bootstrap identity + BIP39 mnemonic (one-time). | live |
| `dm_whoami()` | Pubkey + handle + balance + recent mining receipts. | live |
| `dm_register(handle?)` | Register pubkey ↔ GitHub-handle binding with the arena. | live (arena) |

`dm_init` is the first thing to run on a new machine. It returns the 24-word
recovery mnemonic **exactly once** — the caller is responsible for surfacing
it to the user (print, prompt to copy, etc). The mnemonic is never persisted
to disk and never retrievable later.

```json
// dm_init success response (shape)
{"status": "ok",
 "pubkey_hex": "ebc9…",
 "mnemonic": "abandon ability able about … wisdom wolf woman",
 "created": true,
 "identity_path": "~/.config/daimon/identity.key",
 "warning": "Save the mnemonic NOW. Shown once only."}
```

If an identity already exists, `dm_init` returns
`{"error": "identity_exists", "pubkey_hex": "…", "hint": "Pass force=true …"}`
— surfaces the existing pubkey so the caller can confirm before overwriting.
`force=true` is **DESTRUCTIVE** (old collection + ledger position become
unrecoverable unless the old mnemonic was saved).

### Mining (1)

| Tool | Purpose | Status |
|---|---|---|
| `dm_mine_status()` | Current balance + last 5 receipts (alias for `dm_whoami`'s mining view). | live |

Mining receipts come from the Claude Code `PostToolUse` hook installed via
`daimon mine install-hook`. Receipts are signed and chained — see
[`mine.md`](mine.md).

### Catalog (4) — pure local

| Tool | Purpose | Status |
|---|---|---|
| `dm_expansions()` | List installed catalogs + rarity weights. | live |
| `dm_catalog_list(expansion_id?)` | List cards in a catalog (filterable by element / rarity). | live |
| `dm_catalog_card(card_id, expansion_id?)` | Full card JSON for one card. | live |
| `dm_card_compare(a, b, expansion_id?)` | Side-by-side stat + trigger diff. | live |

### Collection + pulls (2)

| Tool | Purpose | Status |
|---|---|---|
| `dm_collection()` | List owned serials (UUID per minted card). | live |
| `dm_pull(seed?, catalog?)` | Spend 100 currency, mint a fresh card from the active pack. | live |

### Loadouts (7) — pure local

| Tool | Purpose | Status |
|---|---|---|
| `dm_loadout_validate(loadout)` | Structural legality check (6 cards, max 2 of same species, known enums). | live |
| `dm_loadout_save(loadout, name)` | Save a named deck to `~/.config/daimon/loadouts/`. Auto-sets active on first save. | live |
| `dm_loadout_list()` | List saved deck names. The active loadout is flagged with `"active": true` and surfaced as `active_loadout` at the top level. | live |
| `dm_loadout_load(name)` | Fetch a saved deck. | live |
| `dm_loadout_set(name)` | Designate a saved loadout as the active default (used by `dm_match_npc` when `loadout` is omitted). | live |
| `dm_loadout_get_active()` | Inspect the active-loadout pointer (returns `{name, exists}` so stale pointers are visible). | live |
| `dm_loadout_clear_active()` | Unset the active-loadout pointer. | live |

The active-loadout pointer lives at `~/.config/daimon/loadout_meta.json`
as `{"version": 1, "active_loadout": "<name>"}`. Pointer-by-name (not
pointer-by-cards) means editing the named loadout via `dm_loadout_save`
propagates automatically — no "your active is stale" footgun. See
`daimon/loadouts/active.py` for the persistence rationale.

### Match (1) — local PvE

| Tool | Purpose | Status |
|---|---|---|
| `dm_match(loadout_a, loadout_b, seed?, include_round_log?)` | Resolve deterministic match; writes V2 Match to state file. | live |

### NPC ladder (3)

| Tool | Purpose | Status |
|---|---|---|
| `dm_npcs(tier?)` | List the 25-NPC roster, optionally filtered to one tier. | live |
| `dm_npc(npc_id)` | Full loadout + tier + lore for one NPC. | live |
| `dm_match_npc(npc_id, loadout?, seed?, include_round_log?)` | Resolve PvE match against an NPC. `loadout` defaults to the active loadout (set via `dm_loadout_set`). | live |

Tiers: `rookie` → `novice` → `veteran` → `elite` → `champion`. Phase 5 sim
proves zero cross-tier upsets at 21 seeds × 600 pairings.

### Skin shop + skins (4)

| Tool | Purpose | Status |
|---|---|---|
| `dm_shop(slot?)` | List today's 6-slot skin shop. Refreshes daily at 00:00 UTC. | live |
| `dm_shop_buy(slot?, skin_slug?)` | Spend currency, mint a skin entitlement (writes to inventory). | live |
| `dm_skins_owned()` | List skins owned + their per-card equip state. | live |
| `dm_skin_equip(card_id, skin_slug)` | Equip an owned skin onto a card. | live |
| `dm_skin_unequip(card_id)` | Remove the equipped skin (revert to base art). | live |

(That's 5 tools — shop+buy + 3 skins; counted as 4 in the section header
because the `dm_skins_owned` view collapses with `dm_skin_equip` /
`dm_skin_unequip` semantically.)

### PvP (5) — arena

| Tool | Purpose | Status |
|---|---|---|
| `dm_pvp_challenge(opponent_pubkey, loadout, memo?, pack_pin?, rule_set?)` | Open async PvP challenge — commit phase, challenger. | live (arena) |
| `dm_pvp_accept(challenge_id, loadout)` | Accept a pending challenge — commit phase, responder. | live (arena) |
| `dm_pvp_reveal(challenge_id)` | Publish loadout + signature — reveal phase, both sides. | live (arena) |
| `dm_pvp_status(challenge_id)` | Poll current phase + (when settled) result. | live (arena) |
| `dm_pvp_my_matches(limit?)` | Open + recent PvP matches for this identity. | live (arena) |

### Arena state (2)

| Tool | Purpose | Status |
|---|---|---|
| `dm_leaderboard(limit?)` | Top-ranked players. | live (arena) |
| `dm_my_rank()` | My standing + record. | live (arena) |

### Disputes + contributions (2)

| Tool | Purpose | Status |
|---|---|---|
| `dm_dispute_open(match_id, reason, evidence?)` | Appeal a resolved match (50-currency bond). | live (arena) |
| `dm_card_propose(card_def, rationale?)` | Propose a new card for the cards repo. | live (arena) |

### Chat home card (2) — local, render-only

| Tool | Purpose | Status |
|---|---|---|
| `dm_home()` | JSON snapshot of identity + balance + tier + last 5 pulls + saved loadouts + today's daily quests. Pure read — does NOT auto-claim. | live |
| `dm_home_card()` | Same payload + a ready-to-post `:::html` Marvel-Snap-style chat card (`{message, html, payload}`). | live |

`dm_home_card` is the canonical way to surface DAIMON state in the
LivingAgent webapp chat — pass `result["message"]` verbatim to
`mcp__webapp-channel__reply` (it already wraps the HTML in a `:::html`
fenced block). The buttons inside the card use `window.agentAction(
'send_message', {text: '@daimon ...'})` so a click dispatches a chat
message back to whoever is running the watcher loop.

When no identity exists, both tools return a minimal "init me" payload
instead of an error — the home card is the new-user landing page, so
it has to render even before `dm_init` has been called.

The `daily_quests` field on the payload is the same list returned by
`dm_quests` (3 quests, one per tier). `dm_home` is a glanceable read,
so it never mints rewards — auto-claim only fires from `dm_quests`,
`dm_match`, `dm_match_npc`, and `dm_pull`. This keeps the home card
idempotent (calling it 100 times in a row does NOT pay out 100 rewards).

### Daily quests (1) — local

| Tool | Purpose | Status |
|---|---|---|
| `dm_quests()` | Roll today's 3 quests (one per tier: easy / medium / hard), evaluate progress from ledger + ticker, auto-claim any newly complete reward. | live |

Quests are deterministically rolled from `HMAC-SHA256(pubkey, "YYYY-MM-DD")`
— same primitive as the skin shop rotation, so every machine running the
same identity sees the same 3 quests on the same UTC date. The roll is
cached in `~/.config/daimon/quests.json` and silently re-rolled on date
change, pubkey change, or schema bump.

Tier rewards are locked: easy = 25¤, medium = 50¤, hard = 100¤ (175¤
total per UTC day, or 1.75 gacha pulls). Auto-claim writes a
`quest_reward` entry to the mining ledger with idempotency key
`quest_<date>_<quest_id>`, so re-evaluating after a claim is a no-op.

The daily-quest matcher consumes:

  - `mining/buffer.jsonl` ticker entries `kind="match"` (with `outcome`,
    `opponent_tier`, `loadout_element` in `extra`) — counts wins, beats
    against tiered NPCs, and mono-element wins.
  - `mining/buffer.jsonl` ticker entries `kind="pull"` — counts gacha
    pulls.
  - `mining/ledger.jsonl` `kind="mine"` entries — counts mined currency.

That's why `dm_match` / `dm_match_npc` / `dm_pull` all return a
`daily_quests` field after the action — they trigger the auto-claim
re-evaluation, and the caller gets the updated quest list (including
any rewards just minted) in the same response.

### Chat inbox (3) — webapp long-poll

| Tool | Purpose | Status |
|---|---|---|
| `dm_inbox_wait(timeout_s?, max_messages?, cursor?)` | Block up to `timeout_s` (default 60s) on the webapp SSE stream; return new `@daimon` mentions in the `group` channel. | live (webapp) |
| `dm_inbox_ack(message_id)` | Persist `message_id` as the last-acked cursor so the next `wait` skips it. Monotonic. | live (webapp) |
| `dm_inbox_status()` | Configuration snapshot — webapp URL, channel, whether a token resolved (token redacted to first 6 chars). | live (webapp) |

Together these three tools power the chat-mention watcher loop
documented in [`chat-watcher.md`](chat-watcher.md). The flow is:

```
dm_inbox_wait → for each msg: parse + dispatch + reply → dm_inbox_ack
```

`dm_inbox_wait` already filters out non-`user` senders (so the agent's
own replies don't re-trigger it), non-`group` channels, and any id
≤ the persisted cursor — the dispatcher doesn't need to re-check.

Configuration (env vars, all optional except the token):

  - `DAIMON_WEBAPP_URL` — base URL (default `https://santiagodcalvo.org`).
  - `DAIMON_WEBAPP_TOKEN` — Bearer token from a webapp session, OR…
  - `DAIMON_WEBAPP_TOKEN_FILE` — path to a file containing the token.
  - `DAIMON_WEBAPP_CHANNEL` — channel to subscribe to (default `group`).

If no token is configured, `dm_inbox_wait` falls back to the local
internal API key at `/opt/agents/secrets/internal_api.key` (Santiago's
VPS); on machines without that file, it returns
`{"error": "config_missing", ...}` and the loop should stop and prompt
the user. Auth failures (`401/403` from the webapp) return
`{"error": "auth_failed", ...}` — the user must rotate the token.
Transport blips (network / read timeout) return success with
`note: "transport: ..."` and an empty `messages` list, so the loop
just retries without surfacing noise.

The cursor lives at `~/.config/daimon/inbox-cursor.json` and is
monotonic (a backwards `dm_inbox_ack(0)` is silently dropped).

**Arena-bound tools** shell out to the `gh` CLI to publish to the
daimon-arena (or daimon-cards) GitHub repo via the commit-reveal protocol
documented in `daimon/arena/encoding.py`. They require `gh auth login` to be
configured locally; on missing auth they return `{"error": "gh_auth", ...}`.
PvP commits hold loadouts locally in `~/.daimon/pvp_state/<id>.json` until
reveal time — never commit-reveal in one tool call.

## Loadout shape

Either `{"cards": [...]}` or a bare list. Each card is a V2 monster JSON:

```json
{
  "card_id": "voltcat_apex",
  "species": "voltcat",
  "element": "VOLT",
  "atk": 12, "def": 4, "hp": 18, "spd": 11,
  "triggers": [
    {"when": "ON_ATTACK", "op": "BUFF_ATK", "target": "SELF", "value": 2}
  ]
}
```

Six cards per loadout. Max 2 of the same species per team. No body slots —
the old robot-parts model was retired 2026-04.

## Envelope conventions

**Two keys only: `status` on success, `error` on failure.**

- **Success** — the operation happened:
  - Tools that have a meaningful write/action return `{"status": "ok", ...payload}` (e.g. `dm_init`, `dm_pull`, `dm_match`, `dm_loadout_save`, `dm_shop_buy`, `dm_skin_equip`).
  - Pure reads return their payload directly without a status key (e.g. `dm_whoami`, `dm_catalog_list`, `dm_npcs`).

- **Failure** — the operation did not happen: `{"error": "<code>", "message": "...", ...context}`. Common codes:
  - `invalid_input` — arguments failed validation (also used for seed hex / length)
  - `invalid_loadout` — loadout failed schema check
  - `invalid_name` — name contained disallowed chars (loadout save/load)
  - `unknown_card` / `unknown_expansion` / `unknown_loadout` / `unknown_npc` — not found
  - `no_identity` — identity missing (run `dm_init` first)
  - `identity_exists` — `dm_init` called on a machine that already has one (pass `force=true` to overwrite)
  - `no_collection` / `corrupt_collection` — collection file missing/bad
  - `catalog_load_failed` — catalog manifest error
  - `insufficient_balance` — `dm_pull` / `dm_shop_buy` without enough currency (+`balance`, `needed`, `cost`)
  - `not_owned` — `dm_skin_equip` on a skin you don't own
  - `ledger_corrupt` — mining ledger failed verification
  - `internal_error` — unexpected exception; includes `message` with type + str

- **Arena-bound failures** add a few extra error codes from the `gh` CLI layer: `gh_missing` (CLI not installed), `gh_auth` (auth expired / missing), `gh_timeout` (slow network), `gh_failed` (generic non-zero exit), `gh_parse` (couldn't parse gh output), `not_found` (raw repo file missing), and PvP-specific: `no_local_state`, `identity_mismatch`, `invalid_card`. All carry the same `error` + `message` shape — no special-casing required.

- **Webapp-bound failures** (inbox tools): `config_missing` (no `DAIMON_WEBAPP_TOKEN` resolved; loop should stop), `auth_failed` (401/403 from webapp; token rotated/expired). Transient transport errors return `status: ok` with a `note: "transport: …"` field instead of an `error` — the watcher loop just retries.

Rule of thumb for callers: `if "error" in response: handle failure` before anything else.

## Determinism

`dm_match` and `dm_match_npc` default to a zero seed for replay safety. Pass
`seed` (64 hex chars) for non-test play. Same `(loadout_a, loadout_b, seed)`
always produces the same result — that is the whole point of the engine.

`dm_pull` defaults to a random 32-byte seed (from `os.urandom`). Explicit
seeds are accepted for testing but never exposed in production — gacha
randomness must not be game-able by an adversarial agent.

## Arena repo override

Arena-bound tools target `aurorasuperbot/daimon-arena` by default; the
`DAIMON_ARENA_REPO` env var redirects them to a fork or test arena without
code changes.

## Security

The engine still never reads card text — `name`, `flavor`, `rarity` are
dropped at the schema layer before the engine sees the card. MCP doesn't
change this. An adversarial agent who controls a card definition can't
escape combat math by writing instructions in the flavor text.

Saved-loadout names are strictly validated (`[A-Za-z0-9_-]`, 1–48 chars) —
no path traversal into `~/.config/daimon/loadouts`.
