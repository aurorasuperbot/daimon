# Loadout — build, save, validate, edit a 6-card deck

A loadout is the fighting unit. **Exactly 6 cards, max 2 of the same
species.** Once a loadout is saved, you reference it by name everywhere
(`daimon match`, `daimon match-npc`, `dm_pvp_challenge`).

## Authoring workflow

```bash
daimon catalog list --rarity rare         # browse the pool
daimon catalog card voltcat_apex          # inspect one card
daimon loadout new my_volt --from-template volt_burst   # scaffold from a template
daimon loadout edit my_volt               # interactive TUI editor
daimon loadout validate my_volt           # structural check
daimon loadout save ./my_volt.json my_volt   # save under that name
daimon loadout list                       # see all saved
daimon loadout load my_volt               # print to stdout
```

## Validate

```bash
daimon loadout validate my_volt.json
# OK: 6 cards, all enums known, no species exceeds 2.
```

Validation rules:

- exactly 6 cards
- each card has `card_id`, `element`, `atk` / `def` / `hp` / `spd`,
  and a (possibly empty) `triggers` list
- max 2 cards of the same `species`
- triggers have known `when` / `op` enum values
- malformed cards reject the entire loadout — no partial loads

The MCP `dm_loadout_validate` tool runs the same check.

## Save / load

Saved loadouts live at `~/.config/daimon/loadouts/<name>.json`. Names
must match `[A-Za-z0-9_-]{1,48}` — no path traversal, no spaces.

```bash
daimon loadout save ./scratch_loadout.json volt_burst
daimon loadout load volt_burst > /tmp/volt.json
daimon loadout list
# - volt_burst
# - bulwark_fortress
# - tide_chant
```

## Edit interactively

```bash
daimon loadout edit volt_burst        # auto-launches WezTerm
daimon loadout edit volt_burst --in-place    # current terminal
```

TUI keys: `←→↑↓` move focus, `enter` swap a card, `s` save, `v` validate
in-place, `q` quit (prompts if unsaved).

## Showcase loadouts (ship with the engine)

Browse `daimon/loadouts/showcase/` for hand-built reference loadouts
covering every archetype:

```bash
ls daimon/loadouts/showcase/
# l1_inferno_aggro.json    l6_volt_tempo.json
# l2_bulwark_wall.json     l7_...
# ...
```

These are the canonical examples — point at them with `daimon match` to
get a fight going without hand-building from scratch:

```bash
daimon match daimon/loadouts/showcase/l1_inferno_aggro.json daimon/loadouts/showcase/l2_bulwark_wall.json
```

## Loadout shape (V2 monster JSON)

```json
{
  "cards": [
    {
      "card_id": "voltcat_apex",
      "species": "voltcat",
      "element": "VOLT",
      "rarity": "rare",
      "atk": 12, "def": 4, "hp": 18, "spd": 11,
      "triggers": [
        {"when": "ON_ATTACK", "op": "BUFF_ATK", "target": "SELF", "value": 2}
      ]
    },
    "..."
  ]
}
```

Either `{"cards": [...]}` or a bare list works. There are no body slots
— the old robot-parts pivot was retired in 2026-04. Each card stands on
its own as a monster.

## See also

- [`first-match.md`](first-match.md) — fight your first match
- [`collection.md`](collection.md) — browse your owned cards
- [`mcp.md`](mcp.md) — the `dm_loadout_*` MCP tools (validate/save/load/list)
