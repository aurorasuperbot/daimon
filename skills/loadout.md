# Loadout — build, save, validate, edit a 6-card deck

A loadout is the fighting unit. **Exactly 6 cards, max 2 of the same
species.** Once a loadout is saved, you reference it by name everywhere
(`daimon match`, `daimon match-npc`, `dm_pvp_challenge`).

## Authoring workflow

```bash
daimon catalog list --rarity rare         # browse the pool
daimon catalog card voltcat_apex          # inspect one card
daimon loadout new --out ./my_volt.json   # scaffold a starter template (first 6 catalog cards)
daimon loadout edit my_volt               # interactive TUI editor (operates on the saved name)
daimon loadout validate ./my_volt.json    # structural check (takes a path)
daimon loadout save ./my_volt.json my_volt   # save under that name
daimon loadout list                       # see all saved
daimon loadout load my_volt               # print to stdout
```

`daimon loadout new` prints the template to stdout by default; `--out PATH`
writes it to a file. The output is showcase-format (`{"loadout_id": "...",
"loadout": ["card_id", ...]}`) — edit the array, then `daimon loadout save`
the file under a name.

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

TUI keys (split-pane catalog ⇄ loadout):

- `↑` / `↓` — move cursor in the focused pane
- `TAB` / `←` / `→` — swap focus between CATALOG and LOADOUT
- `ENTER` / `+` — add the cursor card to the first empty loadout slot
- `-` — drop the loadout slot under the cursor
- `s` — save and exit (refuses if the loadout is invalid)
- `q` / `ESC` — quit without saving

Live validation against `engine.Loadout` runs on every change (TEAM_SIZE=6,
≤2 same-species).

## Showcase loadouts (ship with the engine)

Browse `daimon/loadouts/showcase/` for 10 hand-built reference loadouts
covering every archetype:

```bash
ls daimon/loadouts/showcase/
# showcase_l1_inferno_burnstack.json    showcase_l6_syncretic_mono_void.json
# showcase_l2_bulwark_thorns.json       showcase_l7_prism_pantheon.json
# showcase_l3_tidal_trickle.json        showcase_l8_funeral_pyre.json
# showcase_l4_stormchain_tempo.json     showcase_l9_apex_predator.json
# showcase_l5_revenant_cascade.json     showcase_l10_worldroot_garden.json
# manifest.json
```

These are the canonical examples — point at them with `daimon match` to
get a fight going without hand-building from scratch:

```bash
daimon match \
  daimon/loadouts/showcase/showcase_l1_inferno_burnstack.json \
  daimon/loadouts/showcase/showcase_l2_bulwark_thorns.json
```

Showcase files use the wrapped format (`{"loadout_id": "...", "loadout":
["card_id", ...]}`) — `load_loadout_file` accepts that shape, the bare
list, and the `{"cards": [...]}` form interchangeably.

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
