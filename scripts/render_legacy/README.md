# Legacy Render Scripts

Migrated from `/opt/agents/projects/agent-tcg/scripts/` (the pre-DAIMON design dir, archived 2026-04-21).

These scripts produced the V0 hybrid-render proof (terminal-cell frame + chafa-cascade art panel) that locked the design language for the render layer. They run standalone — no `daimon` package import needed.

## Files

| Script | What it does |
|---|---|
| `compose_legendary.py` | PIL composition: builds the legendary card frame at full resolution. Includes the 3 critical PIL fixes (anti-alias on mask, correct alpha channel order, font-rendering DPI). |
| `cascade_legendary.py` | Generates 7-tier renders of the legendary card for capability-cascade testing. |
| `hybrid_render.py` | Reference implementation of HYBRID render: frame as terminal cells, only art panel through chafa. |
| `simulate_tiers.py` | Iterates through all 7 chafa tiers and saves each as a PNG, useful for screenshots of the cascade. |

## Migration plan to `daimon/render/`

V1.1 will refactor these into the `daimon/render/` module:
- `compose_legendary.py` → `daimon/render/compose.py` (parameterized over card data, not hardcoded)
- `hybrid_render.py` → `daimon/render/hybrid.py` (reads Card object + art path, produces tier-appropriate output)
- `cascade_legendary.py` → folded into `daimon/render/cascade.py` capability detection + dispatch
- `simulate_tiers.py` → `tests/visual/` debug utility, not shipped

Until V1.1 lands, these stay as-is in `scripts/render_legacy/` so the proven setup isn't lost.
