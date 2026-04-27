# Chat watcher loop — react to `@daimon` mentions in the LivingAgent webapp

You're an agent (probably Claude Code) running on the user's machine,
with both the DAIMON MCP server (`dmn-mcp`) and the webapp-channel MCP
server installed. Your job in this mode is to:

1. Wait for chat messages addressed to `@daimon` in the user's webapp.
2. Parse the intent.
3. Invoke the right `dm_*` tool.
4. Post the result back to chat.
5. Loop.

This file is the canonical procedure for that loop.

## Prerequisites — verify before starting

```
dm_inbox_status       # → confirms webapp URL + token resolved
```

If `token_resolved: false`, the user needs to point DAIMON at their
webapp credentials. Two env vars matter:

  * `DAIMON_WEBAPP_URL` — base URL of the LivingAgent deployment
    (default `https://santiagodcalvo.org`; override per deployment).
  * `DAIMON_WEBAPP_TOKEN` — Bearer token. Either:
    - the JWT from an active webapp session (browser DevTools →
      Application → Local Storage → `auth_token`), OR
    - a long-lived internal API key (see `/opt/agents/secrets/`
      on the VPS, or whatever path your deployment uses).
  * `DAIMON_WEBAPP_TOKEN_FILE` — alternative to inlining the token:
    point this at a file containing the bearer token.
  * `DAIMON_WEBAPP_CHANNEL` — only needed if the user is on a
    non-default deployment whose chat channel isn't called `group`.

Once the env is set, re-run `dm_inbox_status`. When `token_resolved:
true`, you're ready to enter the loop.

## The loop (pseudo-code)

```
while True:
    result = dm_inbox_wait(timeout_s=60, max_messages=10)

    if result.get("error"):
        # Structured failure — surface to user, do NOT silently retry.
        # auth_failed → "rotate DAIMON_WEBAPP_TOKEN"
        # config_missing → "set DAIMON_WEBAPP_TOKEN"
        break

    for msg in result["messages"]:
        try:
            handle_mention(msg)        # parse + dispatch + reply
        finally:
            dm_inbox_ack(msg["id"])    # always advance cursor, even on dispatch failure
                                        # (otherwise you'll re-process forever)
```

`dm_inbox_wait` blocks on the SSE stream up to `timeout_s`. It returns
early as soon as ANY mention arrives (down to message granularity), so
the loop is responsive without polling. A `note: "transport: …"` in
the envelope means a network blip — empty `messages`, just retry.

## `handle_mention(msg)` — the dispatch table

`msg["text"]` is the full chat message (e.g. `"@daimon match-npc
Sparring Sam"`). Strip the mention token, parse what's left.

V1 grammar — match these patterns first, fall through to natural-
language interpretation only if none match.

| User text                         | Tool to call                       | Reply with                              |
|-----------------------------------|------------------------------------|------------------------------------------|
| `@daimon home`                    | `dm_home_card()`                   | the `message` field, verbatim            |
| `@daimon pull`                    | `dm_pull(seed=...)`                | post a small `:::html` pull-result card  |
| `@daimon match-npc <Name>`        | `dm_match_npc(npc_id, loadout?)` — `loadout` defaults to active | post the post-match summary              |
| `@daimon init`                    | `dm_init()`                        | post pubkey + mnemonic warning           |
| `@daimon show my collection`      | `dm_collection()`                  | post a compact summary                   |
| `@daimon quests` / `@daimon daily`| `dm_quests()`                      | post the 3 quests + claimed-rewards diff |
| `@daimon status` / `@daimon`      | `dm_home_card()`                   | same as `home`                           |

For patterns NOT in this table (e.g. `@daimon play someone fun` or
`@daimon what's my best loadout?`), interpret the intent yourself
using the same tool surface — but stick to the available `dm_*` tools,
don't invent endpoints.

## Posting replies

Always post via `mcp__webapp-channel__reply` (the user's local
webapp-channel MCP). Default channel is `group`.

For tools that return a ready-to-post message (`dm_home_card` returns
`{message: ":::html\n…\n:::"}`), pass `result.message` as the `text`
arg verbatim — DO NOT re-wrap or modify.

For tools that return raw data (`dm_pull`, `dm_match_npc`), construct
a brief reply:

  - **Successful pull:** "🎴 Pulled **`<card_id>`** ([`<rarity>`])
    — balance now `<balance_after>`¤"
  - **Match win:** "✅ Beat `<opponent>` in `<rounds>` rounds. Your
    record vs them: `<W>-<L>-<D>`."
  - **Match loss:** "❌ Lost to `<opponent>` in `<rounds>`. Try
    `@daimon home` for a recommended next opponent."

Keep replies concise — chat is a stream, not a wall.

## What NOT to react to

`dm_inbox_wait` already filters out:
  * Messages from any sender other than `user` (so Coda's / your own
    chat replies that quote `@daimon` won't trigger you).
  * Messages on channels other than `group` (default — overridable via
    `DAIMON_WEBAPP_CHANNEL`).
  * Messages with id `<=` the persisted cursor (so restarts don't
    re-process old mentions).

You don't need to add your own filtering on top.

## Failure handling

If a `handle_mention` call fails (tool error, network hiccup, parser
miss), STILL call `dm_inbox_ack(msg.id)` — otherwise you'll re-process
the same failing message forever. Post a short error reply:

  > "Sorry, couldn't run `<command>` — `<error_slug>`. Try
  > `@daimon home` for a status snapshot."

If `dm_inbox_wait` itself returns an `error:`, STOP the loop and
surface to the user. Don't paper over auth failures by retrying — a
rotated/expired token is the user's problem to fix.

## Cost discipline

The loop is ~free when idle (one `dm_inbox_wait` call per minute that
returns empty), but each mention can fan out into 2-5 tool calls
(parse → dispatch → reply → ack). Don't dispatch the same mention
multiple times. Don't pre-emptively `dm_home` on every mention — only
when explicitly asked.

## Stopping the loop

The user's signal to stop is normally implicit (closing Claude Code).
If they type `@daimon stop watcher` or `@daimon quiet`, exit the loop
cleanly:
  1. Reply: "👋 Watcher stopped. `@daimon` mentions won't be reacted
     to until you restart Claude Code."
  2. Call `dm_inbox_ack(msg.id)`.
  3. Break.
