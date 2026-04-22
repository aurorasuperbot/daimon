# Duel (PvP)

> **V1 alpha:** the arbiter (`daimon-arena/scripts/arbitrate.py` + the
> `arbiter.yml` workflow) is wired up and end-to-end tested. The cross-repo
> engine pull requires `ENGINE_READ_TOKEN` in the arena repo's secrets — until
> that's provisioned, the workflow runs only its self-test and skips real
> matches. The Python script is fully usable locally.

## Protocol (commit-reveal, single loadout commitment)

We collapsed the two-phase protocol (joint seed + loadout) into one: the joint
seed is **derived** from the four loadout-commit data points, so neither player
can grind it without revealing their loadout first.

### Phase 1 — Challenge (Issue body)

Player A opens an Issue using the `Match Challenge` template. Body MUST contain:

```
challenger_pubkey: <hex>
opponent_handle:   <gh-handle>
pack_pin:          <oci-tag, e.g. starter-v1.0.0>
loadout_commit:    <sha256(canonical_json(loadout) || nonce_a)>
```

`canonical_json` = JSON with sorted keys + no whitespace + UTF-8.
`nonce_a` = 32 random bytes (hex), kept secret until reveal.

### Phase 2 — Accept (first comment)

Opponent comments starting with `/accept`. Body MUST contain:

```
opponent_pubkey: <hex>
loadout_commit:  <sha256(canonical_json(loadout) || nonce_b)>
```

### Phase 3 — Reveal (one comment per player, starts with `/reveal`)

Each player posts:

````
/reveal
nonce: <32-byte hex>
signature: <ed25519 hex>

```json
{"cards": [...full loadout...]}
```
````

Signature is over the canonical bytes:

```
b"daimon-pvp-v1\n" + str(issue).encode() + b"\n"
+ canonical_json(loadout) + b"\n" + bytes.fromhex(nonce)
```

The arbiter rejects the reveal if either:
- `sha256(canonical_json(loadout) || nonce) != loadout_commit` (tamper), or
- the signature does not verify against the player's bound pubkey (forgery).

### Phase 4 — Arbitration (automatic)

Joint seed = `sha256("daimon-pvp-seed-v1\n" + issue + commit_a + commit_b + nonce_a + nonce_b)`.

The arbiter runs `resolve_match(loadout_a, loadout_b, seed)`, writes `matches/<issue>.json`, updates `leaderboard.json`, posts a comment with the outcome, locks the Issue (audit trail), and closes it.

## Cheat detection (3-tier)

| Tier | Signal | Outcome |
|---|---|---|
| 1 — canonical | Engine output disagrees with a player's local replay | Arbiter wins; player must investigate (engine version pinned via `ENGINE_READ_TOKEN`) |
| 2 — no-show | Player commits but never reveals before deadline | Strike appended to `disputes/no-show/<pubkey>.json` |
| 3 — fraud | Commit hash or signature fails | Receipt written to `disputes/<issue>.json`; pubkey enters Wall of Shame |

## Local testing

```bash
cd daimon-arena
PYTHONPATH=../daimon python scripts/arbitrate.py --self-test
PYTHONPATH=../daimon python scripts/test_arbitrate.py
```

Self-test runs a synthetic full protocol round-trip with two ed25519 identities
and proves both happy-path AND tamper-detection work.
