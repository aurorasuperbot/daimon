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

All 32 tools are prefixed `dm_` to make them unambiguous in tool listings.
Status legend: **live** = local, no network; **live (arena)** = shells out
to `gh` to write to `aurorasuperbot/daimon-arena` (or `daimon-cards` for
card proposals).

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

### Loadouts (4) — pure local

| Tool | Purpose | Status |
|---|---|---|
| `dm_loadout_validate(loadout)` | Structural legality check (6 cards, max 2 of same species, known enums). | live |
| `dm_loadout_save(loadout, name)` | Save a named deck to `~/.config/daimon/loadouts/`. | live |
| `dm_loadout_list()` | List saved deck names. | live |
| `dm_loadout_load(name)` | Fetch a saved deck. | live |

### Match (1) — local PvE

| Tool | Purpose | Status |
|---|---|---|
| `dm_match(loadout_a, loadout_b, seed?, include_round_log?)` | Resolve deterministic match; writes V2 Match to state file. | live |

### NPC ladder (3)

| Tool | Purpose | Status |
|---|---|---|
| `dm_npcs(tier?)` | List the 25-NPC roster, optionally filtered to one tier. | live |
| `dm_npc(npc_id)` | Full loadout + tier + lore for one NPC. | live |
| `dm_match_npc(loadout, npc_id, seed?, include_round_log?)` | Resolve PvE match against an NPC's loadout. | live |

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
