# Duel (PvP) — V1.1

> **Not yet implemented.** This file describes the planned protocol so reviewers can sanity-check it; nothing here is callable in V0.1 alpha.

## Protocol (commit-reveal)

1. **Challenge** — Player A opens an Issue in `nullpoint-arena/queue/` with template `match-challenge`, includes their pubkey + match parameters.
2. **Accept** — Player B comments with their pubkey + signed acceptance.
3. **Joint seed** — both players publish a 16-byte commitment hash (SHA-256 of secret + pubkey).
4. **Reveal** — both players reveal their secrets. Joint seed = SHA-256(secret_a || secret_b). Match seed = first 32 bytes of that.
5. **Loadout commit** — both players publish SHA-256 of their loadout JSON, signed.
6. **Loadout reveal** — both players publish actual loadouts. Hashes must match commits.
7. **Arbitration** — GH Actions workflow runs canonical engine on both loadouts + joint seed, posts result, signs it, and writes to `matches/<id>.json`.

## Cheat detection (3-tier)

- **Tier 1 (canonical arbiter)**: arbiter is the source of truth. Local replays must match.
- **Tier 2 (reputation strike)**: failure to reveal, mismatched hashes, or signature failures cost reputation.
- **Tier 3 (receipt-of-shame)**: persistent cheaters get a `disputes/<handle>.md` entry, pinned in the Wall of Shame.

## Why commit-reveal on loadouts?

So neither player can pick a counter-loadout AFTER seeing the other's. The hash commits both players to a single loadout BEFORE either is revealed. This is why the engine is deterministic: replay must be byte-identical.
