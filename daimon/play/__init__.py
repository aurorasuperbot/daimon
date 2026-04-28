"""daimon.play — match rendering, file-watcher inbox, battle UI.

V1 alpha scope:
  - `schema` — Pydantic models for the match.json wire protocol (locked 2026-04-22)
  - `frame`  — BattleFrame = computed renderable state at time t
  - `pil_renderer` — PIL-based PNG renderer (mockup + marketing screenshots)
  - `animator` + `primitives` — the 4 default animation primitives + registry
  - `cli` — `daimon play-render` commands

Future (V1.x):
  - daimon.ui TUI renderer — consumes the SAME BattleFrame + primitive registry
  - HTML replay exporter — ditto
  - File-watcher inbox dispatcher

Architecture invariant: the match JSON wire protocol and the Primitive registry
are the two extension points. Renderers are interchangeable, data model stays.
"""

from daimon.play.schema import Match, Action, Round, Outcome  # noqa: F401
