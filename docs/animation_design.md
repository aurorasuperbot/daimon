# DAIMON Combat Animation Design

**Status**: locked 2026-04-22.
**Scope**: terminal-native HUD only. PIL renderer + future HTML replay reuse the same primitives.

## Why this exists

Players (humans + AI agents alike) are watching a deterministic match resolve. Without animation, the screen reads as a flat log dump and the cause→effect of triggers is invisible. With animation, each engine event becomes a visible cause and a visible effect. That is the entire job of this layer.

## Research synthesis (April 2026)

Surveyed the design language of major TCG / auto-battler / roguelike titles to extract a portable vocabulary:

| Source | Lesson taken |
|--------|--------------|
| **Hearthstone (Dominic Camuglia)** | "VFX is how we communicate mechanics in a very intuitive way." Effect intensity must scale with mechanical intensity — heavy spells *look* heavy. |
| **Hearthstone targeting** | Glow on hover; large arrow source→target. Ceremony before resolution reads as deliberate. |
| **Slay the Spire intent system** | Telegraph what's about to happen *before* it happens. Players need a turn of warning to plan. |
| **TFT / Underlords** | Per-ability visual signatures must be **easily recognizable** so a board can be read at a glance. |
| **"Juice it or lose it" (Steve Swink / Martin Jonasson)** | Screen shake, hit-pause, color flash, particle bursts — the small layered cues are what make impacts *feel*. |
| **Wayline "juice problem"** | Over-juiced feedback harms readability. Effects must serve clarity first, spectacle second. |
| **Dust effects (game-feel canon)** | Persistent residue makes a moving thing feel grounded in its world. |

The single non-obvious lesson is the Slay-the-Spire one: telegraph intent **before** resolution. We have it for free in the engine — we know the next action before we render it. Use it.

## Translation problem

Terminal has no GPU, no real particles, no sub-frame screen-shake. Effects must compose from cells, ANSI escapes, and integer timing. The mapping:

| GPU primitive | Terminal-native equivalent |
|---|---|
| Glow / aura | Reverse-video pulse on the tile + bright color border |
| Connection line | Box-drawing chars (`─`, `═`, `╱`, `╲`) drawn between actor and target rows for ~250ms |
| Particle burst | Rapid character cycle on target (`* ✦ + ·`) over 4–6 frames |
| Color flash | ANSI background-color flash for 100ms (red on damage, gold on buff) |
| Screen shake | Offset HUD content by ±1 column for 2–3 frames (subtle — terminal can't fake camera kick without nausea) |
| Hit-pause | Sleep the playback loop for 80–150ms scaled by damage% of max HP |
| Telegraph (intent) | Brighten + bold the actor tile before damage applies; arrow appears toward target |
| Sound cue | `\a` BEL by default (opt-in via env), structured `Cue` enum so audio backend can plug in later |

## Vocabulary — 10 primitives

Each primitive is a class subclassing `Primitive` (in `daimon/play/primitives.py`) with `applies_to(action)`, `window(action)`, `emit(action, t_ms)`. Renderers consume the `ActiveEffect` / `ConnectionLine` outputs and paint accordingly.

### Always-on (V1 default)

| # | Primitive | When | Visual signature | Status |
|---|-----------|------|------------------|--------|
| 1 | `intent` | t = -200..0 (pre-action) | Actor tile bold + bright border, arrow appears toward target | **NEW** |
| 2 | `color_flash` | t = 0..400 | Actor red/gold/cyan flash → target flash 100ms later | shipped |
| 3 | `connection_line` | t = 0..450 | Box-drawing line actor row → target row | shipped |
| 4 | `overlay_icon` | t = 0..400 | Kind icon (💥 ✨ 🛡 ☠ etc) overlay on actor + target tiles | shipped |
| 5 | `hp_tick` | t = 200..550 | HP bar sweeps from old → new value with color-pulse | shipped |
| 6 | `shake` | t = 100..350, on damage ≥ 8 | ±1 col target tile offset (sine), gated by damage threshold | promoted from stub |
| 7 | `pulse` | t = 0..500, on BUFF/SHIELD | Expanding ring (radius/intensity) on actor | promoted from stub |
| 8 | `glow` | persistent | Always-on subtle bright border on legendary cards | promoted from stub (was no-op) |
| 9 | `zap` | t = 0..300, on element triggers | Element-color cycle on target (FIRE=red, WATER=cyan, NATURE=green, VOLT=yellow, VOID=magenta) | **NEW** |
| 10 | `hit_pause` | injects pause hint at t=0 | Stretch playback loop by 80–150ms, scaled by damage% | **NEW** |

### Cue layer (sound spec, audio deferred)

Each primitive optionally emits a `Cue` enum value. The terminal backend can opt to play `\a` BEL (env: `DAIMON_AUDIO=bell`) or stay silent (default). Future audio backends plug into the same emission stream.

```
CUE_HIT       — strike landed
CUE_KO        — combatant fell
CUE_BUFF      — buff or shield gained
CUE_DEBUFF    — status applied
CUE_ROUND     — round transition
CUE_OUTCOME   — match concluded
```

## Architecture rules (preserved from prior locked spec)

- **Renderers stay dumb.** They paint `ActiveEffect` + `ConnectionLine` + (new) `pause_ms` + (new) `cues`. They do not interpret game state.
- **Adding a primitive = subclass + register.** No `frame.py` changes, no `render.py` changes for net-new primitive types of an existing emission `kind`.
- **Per-side painting order** is registry order. Stable for tests.
- **Primitives are pure**: `(Action, t_ms) → emissions`. No global state, no I/O.

## What this design *does not* do (V1 boundary)

- No animated card art (PIL renderer territory; out of scope for terminal HUD)
- No GPU-style particle systems (terminal can't sustain them readably)
- No background music; only discrete cues
- No skip-animation toggle (playback already has speed control 0.25× — 4×; redundant)

## Open V1.x extensions (post-launch)

- `crit_zoom` — temporary +2 vertical-cell tile size on crits
- `chain_lightning_arc` — multi-target connection line for AoE triggers
- `aura_overlap` — when two glows touch, blend colors instead of last-write-wins

## Acceptance criteria (V1)

1. Every `ActionKind` has a distinct visual signature (color × icon × shake × pulse combo) and is recognizable on the second viewing.
2. A KO is unmistakable from a heavy hit — `crush` modifier on KO + 150ms hit-pause + skull overlay.
3. Match readable in monochrome mode (`--no-color`) — color is layered on top of structural cues, not the only signal.
4. `daimon play demo` showcases every primitive in under 30 seconds.
5. All primitives have unit tests + a single end-to-end integration test that verifies primitive ordering against a known fixture match.

## Sources

- [Hearthstone VFX (Game Hub)](https://www.gameshub.com/news/features/making-magic-behind-the-vfx-that-empowers-the-players-of-hearthstone-262603-2144/)
- [Slay the Spire intent system](https://slaythespire.wiki.gg/wiki/Intent)
- [GameAnalytics — squeezing more juice](https://www.gameanalytics.com/blog/squeezing-more-juice-out-of-your-game-design)
- [Wayline — the juice problem](https://www.wayline.io/blog/the-juice-problem-how-exaggerated-feedback-is-harming-game-design)
- [TerminalTextEffects (reference impl, not used directly)](https://github.com/ChrisBuilds/terminaltexteffects)
