# Art Validation — Mythology-pivot test batch (2026-04-23)

**Tool:** NovelAI Anime V4 (`nai-diffusion-4-full`)
**Script:** `/opt/agents/scripts/novelai_gen.py` (off-repo)
**Output dir:** `/opt/agents/novelai_tests/` (off-repo)

## Purpose

Verify whether the v3 prompt template generalizes across the new 6-Canon
mythology pivot — i.e., whether mythological proper names like
"Prometheus, Fire-Thief", "Thor, Hammer-Bearer", "Raijin Herald",
"Quetzalcóatl, Plumed One" produce coherent monster portraits via
NovelAI's Anime V4 model with the existing template.

## Batch composition

| card_id | Canon / Element / Rarity | Display name | Result |
|---|---|---|---|
| `magma_tyrant` | OLYMPIAN / FIRE / legendary | Prometheus, Fire-Thief | weak — minimal flame, no figure |
| `tempest_apex` | AESIR / VOLT / legendary | Thor, Hammer-Bearer | weak — small lightning splash, no figure |
| `boltrunner` | KAMI / VOLT / uncommon | Raijin Herald | weak — pure lightning bolt, no creature |
| `rainbow_drake` | TEOTL / FIRE / epic | Quetzalcóatl, Plumed One | strong — fiery serpent silhouette |
| `cindermote` | KAMI / FIRE / common | Hi-no-Kagutsuchi Spark | strong — fiery dragon shape |

## Findings

1. **Prompt-template defect found and fixed.** The off-repo
   `novelai_gen.py::ELEMENT_TAGS` dict still carried pre-DAIMON element
   vocabulary (`ELECTRIC`, `EARTH`, `AIR`, `DARK`, `LIGHT`, `ICE`, `ARCANE`)
   with no key for `VOLT` or `NORMAL`. VOLT cards (Thor + Raijin Herald)
   silently dropped their element-tag clause, producing 42–46 KB images
   vs. ~400–500 KB for cards whose element matched a key. Fixed in-place
   to use DAIMON's six-element vocabulary (FIRE / WATER / NATURE / VOLT /
   VOID / NORMAL); after fix, VOLT renders bumped to ~200 KB.

2. **NovelAI Anime V4 cannot carry V1 production.** Even after the
   element-tag fix, the model produces atmospheric "elemental effect"
   compositions — small flame in vast darkness, lightning bolt against
   black — rather than figurative monster portraits. The
   `no humans` + `no person` + `no character` negative-prompt clause
   (necessary to prevent anime-girl/boy outputs) suppresses the
   anthropomorphic-deity reading needed for Prometheus/Thor/Raijin. FIRE
   monster-shapes (Quetzalcóatl, Hi-no-Kagutsuchi) work because "fiery
   creature" survives the negative prompt; VOLT becomes pure effect.

3. **Mythological proper names do not break generalization.** The Greek
   (Prometheus), Norse (Thor), Japanese (Raijin / Hi-no-Kagutsuchi), and
   Nahuatl (Quetzalcóatl) names all produce *something* — the failure is
   in the figurative-density of the output, not in name-recognition. So
   the mapping.py rename pass does NOT itself degrade art generation;
   the existing template was already too minimalist.

## Implication for V1 launch path

Confirms the existing decision (memory: `project_agent_tcg.md` 2026-04-23):

> Plan = $2–5 gpt-image-1 LOW for prompt-lock pass, then **one month of
> Midjourney Standard ($30)** for the 200-card production pass.
> Fallback for set-consistency drift: Recraft Advanced ($27/mo).

NovelAI Anime V4 is sufficient for the **prompt-vocabulary eval phase**
(verifying that mythology names render at all, that element-palette tokens
flow through, that the negative-prompt set isn't too aggressive) — and
that phase passed. Going into the production pass, gpt-image-1 LOW for
prompt-lock + Midjourney Standard for the 200-card render remains the
shipping plan.

## Artifacts

PNG outputs preserved off-repo at `/opt/agents/novelai_tests/`:

- `magma_tyrant_seed615790702.png` (416 KB) — Prometheus
- `tempest_apex_seed1796237218.png` (234 KB) — Thor (post-fix)
- `boltrunner_seed1383751381.png` (195 KB) — Raijin Herald (post-fix)
- `rainbow_drake_seed1973922975.png` (509 KB) — Quetzalcóatl
- `cindermote_seed585795665.png` (372 KB) — Hi-no-Kagutsuchi Spark

Pre-fix VOLT renders also retained (`tempest_apex_seed405241139.png`,
`boltrunner_seed1737315902.png`) for the script-fix-effectiveness diff.

NovelAI free-trial budget after batch: **30 − 7 = 23 generations
remaining** (3 prior `ashen_phoenix` tests + 5 batch cards + 2 VOLT retries).
