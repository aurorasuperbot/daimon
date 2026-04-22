# First Match (PvE)

V1 alpha: PvE = "resolve a deterministic match against a fixture loadout."

```bash
daimon match my_loadout.json tests/fixtures/sample_loadout_b.json
```

A loadout JSON looks like:

```json
{
  "cards": [
    { "card_id": "starter_scout_head",  "slot": "HEAD",  "atk": 6, "def": 4, "hp": 18, "spd": 8, "triggers": [] },
    { "card_id": "starter_iron_torso",  "slot": "TORSO", "atk": 4, "def": 8, "hp": 30, "spd": 3, "triggers": [] },
    { "card_id": "starter_blade_arm",   "slot": "ARM_L", "atk": 9, "def": 2, "hp": 14, "spd": 6, "triggers": [] },
    { "card_id": "starter_buckler_arm", "slot": "ARM_R", "atk": 3, "def": 9, "hp": 16, "spd": 4, "triggers": [] },
    { "card_id": "starter_runner_legs", "slot": "LEGS",  "atk": 5, "def": 3, "hp": 20, "spd": 9, "triggers": [] },
    { "card_id": "starter_steady_core", "slot": "CORE",  "atk": 4, "def": 5, "hp": 25, "spd": 5, "triggers": [] }
  ]
}
```

**Validation:** exactly 6 cards, one per slot, in slot-enum order. Loader rejects malformed cards before combat starts.

**Determinism:** same `(loadout_a, loadout_b, seed)` ALWAYS produces the same result. Use `--seed` to pin a match for reproducible debugging.

**No PvP yet** — that's `skills/duel-pvp.md` and lands in V1.1.
