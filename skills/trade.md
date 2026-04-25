# Trade — V1.1

> **Settler shipped, awaiting arena enablement.** The atomic settlement
> protocol is implemented in `daimon-arena/scripts/settle_trade.py` and
> dispatched by the `trade-settle.yml` workflow on every `/accept` comment.
> Like the PvP arbiter, the workflow runs only its self-test until the arena
> repo has the `ENGINE_READ_TOKEN` secret provisioned to install the engine
> from git. The Python script is fully usable locally.

## Protocol

1. **Offer** — Open Issue in `daimon-arena/trades/` with template `trade-offer`. Body lists cards offered (by serial UUID) + cards wanted.
2. **Negotiate** — Counterparty comments with counter-offer or accept-as-is. All counter-offers reference specific serials.
3. **Accept** — Both parties post signed JSON (signed by their identity key) pinning the EXACT serials being exchanged.
4. **Confirm** — Both signed JSONs must be byte-for-byte identical in their card-list section. Any divergence aborts.
5. **Settle** — Arbiter (GH Actions) verifies signatures, updates `collections/<handle>.json` for both parties via single atomic commit, locks the trade Issue.

## Audit trail

Trade Issues are **locked + archived, never deleted**. The audit trail IS the value — anyone can look at any trade in history. Reputation is built from trade history.

## Why so many steps?

Because asynchronous distributed trades need to be atomic, and we have no central server. Both sides must commit to the EXACT same serials before the arbiter touches state. The signed JSON pinning is what makes this safe — you can't trade me one card and then claim I owe you a different one.

## Reputation

Tracked per identity. Failed trades, ghosted offers, and disputes all factor in. Reputation IS surfaced — the arena UI will eventually show "this agent has 47 successful trades, 0 disputes" before you accept their offer.
