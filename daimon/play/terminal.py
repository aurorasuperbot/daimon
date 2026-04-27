"""Game-terminal process — the single reader of ``state.json``.

Long-running Python process spawned by ``daimon play``. Watches
``~/.config/daimon/state.json`` via watchdog, dispatches on the ``view``
content field, and produces a PNG frame per state change under
``~/.config/daimon/renders/``.

Architecture (locked 2026-04-21):

    [ MCP tool (agent side) ]        [ GameTerminal (this module) ]
             │                                 │
             │  write_state(view, data)        │  watchdog fires on state.json
             ├──────────────────────► state.json ──────────────────────►
             │                                 │  read_state()
             │                                 │  should_render(state, last_id)?
             │                                 │  HANDLERS[view](state, out_dir)
             │                                 │  persist last_rendered_id

The MCP tool never talks to this process. Filesystem is the whole bridge.

Rendering scope in A.2:
  - ``match`` view gets a real renderer (summary still-frame from the state
    payload). The full BattleFrame animation loop lands in A.4.
  - The other seven views (``pull``, ``inspect``, ``collection``, ``loadout``,
    ``leaderboard``, ``rank``, ``idle``) get a placeholder renderer that
    writes an identifiable "TODO: <view>" PNG. This keeps the dispatcher
    fully exercised today without blocking on per-view art.

Dedupe contract:
  - Each state carries an ``id`` (from ``play.state.write_state``).
  - The terminal persists ``last_rendered_id`` to a sidecar file so restarting
    the process does NOT re-render a state it already showed. A bootstrapping
    reader with no sidecar will render whatever's currently in state.json once,
    then stop — which matches "fresh install, first-run renders current state."

Thread model:
  - watchdog owns a dispatch thread; callbacks land there.
  - A single ``threading.Lock`` serializes handler invocations + the
    last-rendered-id sidecar write so the sidecar is always consistent with
    what's on disk in ``renders/``.
  - Handlers themselves must be fast + non-blocking. If a handler raises,
    the error is logged; the terminal keeps running. A crashing handler does
    NOT advance ``last_rendered_id`` — the next state change will re-attempt.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import (
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from daimon.play.state import (
    GameState,
    KNOWN_VIEWS,
    read_state,
    resolve_state_path,
    should_render,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Renders dir lives under the shared CONFIG_DIR — env-overridable via
# DAIMON_HOME / XDG_CONFIG_HOME (resolved in identity.keys at import time).
from daimon.identity.keys import CONFIG_DIR as _CONFIG_DIR  # noqa: E402

DEFAULT_RENDERS_DIR = _CONFIG_DIR / "renders"
LAST_ID_SIDECAR_NAME = ".last_rendered_id"

# ---------------------------------------------------------------------------
# Animation tuning (A.4.c)
# ---------------------------------------------------------------------------

# Frames are sampled every N ms within each 600ms action beat. 50ms = 20fps,
# the lowest bound that still feels animated. Smaller intervals = smoother
# playback but more PNGs on disk. Per-call override available via the
# ANIMATION_FRAME_INTERVAL_MS module attr or by passing interval_ms to the
# animator helper directly.
ANIMATION_FRAME_INTERVAL_MS = 50

# Bumped when the manifest JSON shape changes in an incompatible way. Players
# (the WezTerm slideshow loop) refuse manifests with a higher version.
MANIFEST_SCHEMA_VERSION = 1


def resolve_renders_dir(override: Optional[Path | str] = None) -> Path:
    """Resolve the render output dir.

    Precedence: explicit arg > ``DAIMON_RENDERS`` env > XDG default.
    Does NOT create the directory — callers invoke ``ensure_renders`` if they
    need it to exist.
    """
    import os
    if override is not None:
        return Path(override).expanduser().resolve()
    env = os.environ.get("DAIMON_RENDERS")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_RENDERS_DIR


def ensure_renders(renders_dir: Path) -> Path:
    """Create the renders dir if missing. Idempotent."""
    renders_dir.mkdir(parents=True, exist_ok=True)
    return renders_dir


# ---------------------------------------------------------------------------
# Handler type
# ---------------------------------------------------------------------------

# Handlers take (GameState, renders_dir) and return the output Path they
# wrote (or None if the view doesn't produce a file — e.g. pure audio later).
# Raising propagates to the terminal, which logs + skips advancing last_id.
Handler = Callable[[GameState, Path], Optional[Path]]


# ---------------------------------------------------------------------------
# View handlers — one real (match), seven placeholders
# ---------------------------------------------------------------------------

def _render_placeholder_png(
    out_path: Path,
    title: str,
    subtitle: str,
    lines: Optional[list[str]] = None,
) -> Path:
    """Minimal PNG used by every not-yet-implemented view renderer.

    Black-ish background, gold title, dim subtitle, monospace body. Uses only
    PIL — intentionally does NOT go through pil_renderer so placeholder frames
    stay cheap and cannot break when that module changes.
    """
    from PIL import Image, ImageDraw, ImageFont

    W, H = 960, 540
    BG = (14, 18, 26)
    FG = (220, 225, 235)
    FG_DIM = (130, 138, 152)
    TITLE_GOLD = (230, 198, 90)

    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    font_bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    ]

    def _load(paths: list[str], size: int):
        for p in paths:
            if Path(p).exists():
                return ImageFont.truetype(p, size)
        return ImageFont.load_default()

    font = _load(font_candidates, 18)
    font_bold = _load(font_bold_candidates, 28)

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Title — centered, bold, gold
    tw = draw.textlength(title, font=font_bold)
    draw.text(((W - tw) // 2, 60), title, fill=TITLE_GOLD, font=font_bold)

    # Subtitle — dim, centered
    sw = draw.textlength(subtitle, font=font)
    draw.text(((W - sw) // 2, 110), subtitle, fill=FG_DIM, font=font)

    # Body lines — left-aligned, mono
    if lines:
        y = 170
        for line in lines:
            draw.text((60, y), line, fill=FG, font=font)
            y += 26

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def _summarize_loadout(loadout: list[dict]) -> list[str]:
    """Render one-line card summaries for the still-frame body.

    Expects the shape produced by ``_card_to_jsonable`` in the MCP server
    (card_id, species, element, atk, def, hp, spd). Missing fields fall back
    to ``?`` so this never raises on a minor schema drift.
    """
    out: list[str] = []
    for i, card in enumerate(loadout):
        if not isinstance(card, dict):
            out.append(f"  [{i}] <malformed>")
            continue
        species = card.get("species", card.get("card_id", "?"))
        element = card.get("element", "?")
        atk = card.get("atk", "?")
        hp = card.get("hp", "?")
        out.append(f"  [{i}] {species:<14} {element:<6} atk={atk} hp={hp}")
    return out


def _is_match_payload(data: dict) -> bool:
    """Detect a `play.schema.Match` payload (vs the legacy summary dict).

    The wire schema mandates schema_version=2, event_type='match', a
    'participants' dict, and a 'rounds' list. Presence of all four = Match
    payload; missing any = legacy dm_match shape (still supported for
    backward compatibility until the engine→Match adapter lands in A.4.b).
    """
    if not isinstance(data, dict):
        return False
    return (
        data.get("schema_version") == 2
        and data.get("event_type") == "match"
        and isinstance(data.get("participants"), dict)
        and isinstance(data.get("rounds"), list)
    )


def _frame_milestones(beat_ms: int, interval_ms: int) -> list[int]:
    """Return the t_ms milestones to sample within one action beat.

    For the default 600ms beat at 50ms intervals: [0, 50, 100, ..., 550].
    Always includes t=0 (beat start, primitives ON) and excludes t=beat_ms
    (which is t=0 of the next beat — sampling it would double-count).
    """
    if interval_ms <= 0:
        raise ValueError(f"interval_ms must be > 0, got {interval_ms}")
    return list(range(0, beat_ms, interval_ms))


def _render_match_animated(
    state: GameState,
    renders_dir: Path,
    *,
    interval_ms: int = ANIMATION_FRAME_INTERVAL_MS,
) -> Path:
    """Walk every (round, action) × t_ms milestone, paint a frame, write a manifest.

    Frames live under ``renders_dir/match_<state_id>/frame_NNNN.png``; the
    manifest sits at ``renders_dir/match_<state_id>_manifest.json`` and is
    the canonical "this match's render bundle" handle. Returning the manifest
    path means the dispatcher's existing out_path semantics still hold (one
    artifact path per dispatch) while the animation lives in a sibling dir.

    The player loop (WezTerm slideshow, downstream of A.4.c) consumes the
    manifest: read → iterate frames in order → display each for
    ``frame_interval_ms``, honoring ``render_hints.pacing_multiplier`` from
    the Match payload if present.
    """
    # Imported lazily — the play module pulls in PIL + pydantic only when a
    # match actually arrives, keeping CLI-only imports cheap.
    from daimon.play.art_render import prewarm_card_art
    from daimon.play.frame import ACTION_BEAT_MS, build_mid_action_frame
    from daimon.play.pil_renderer import render_frame_to_png
    from daimon.play.schema import Match

    match = Match.model_validate(state.data)
    state_id = state.id
    frames_dir = renders_dir / f"match_{state_id}"
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Pre-warm every card's art in parallel before the render loop fans
    # out. A 12-card match (6 per side) without this would pay 12
    # sequential ``ensure_art_for`` round-trips inside the *first* frame
    # — perceptible hitch on a fresh install before the prefetch
    # subprocess has filled the cache. With pre-warm, the loop's
    # ``resolve_card_art`` calls are stat-only.
    loadout_card_ids = [
        lc.species
        for participant in match.participants.values()
        for lc in participant.loadout
    ]
    prewarm_card_art(loadout_card_ids)

    milestones = _frame_milestones(ACTION_BEAT_MS, interval_ms)

    frames_meta: list[dict] = []
    elapsed_ms = 0
    frame_index = 0

    for r in match.rounds:
        for action_index in range(len(r.actions)):
            for t_ms in milestones:
                frame = build_mid_action_frame(
                    match=match,
                    round_number=r.round,
                    action_index=action_index,
                    t_ms=t_ms,
                )
                frame_path = frames_dir / f"frame_{frame_index:04d}.png"
                render_frame_to_png(frame, frame_path, match=match)
                frames_meta.append({
                    "index": frame_index,
                    # Path stored relative to the manifest's parent (renders_dir)
                    # so the bundle is portable: copy the renders dir, manifest
                    # still resolves.
                    "path": f"match_{state_id}/{frame_path.name}",
                    "round": r.round,
                    "action_index": action_index,
                    "t_ms": t_ms,
                    "elapsed_ms": elapsed_ms,
                })
                frame_index += 1
                elapsed_ms += interval_ms

    manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "state_id": state_id,
        "match_id": match.match_id,
        "winner": match.outcome.winner.value,
        "frame_interval_ms": interval_ms,
        "total_frames": len(frames_meta),
        "total_duration_ms": elapsed_ms,
        "frames_dir": frames_dir.name,
        "frames": frames_meta,
    }

    manifest_path = renders_dir / f"match_{state_id}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest_path


def _render_match_summary(state: GameState, renders_dir: Path) -> Path:
    """Legacy summary still-frame renderer — pre-A.4.b dm_match payloads.

    Kept until ``play/adapter.py`` lands and ``dm_match`` writes a real
    schema.Match payload. Removing this is part of A.4.b's definition of
    done; until then it preserves backward compatibility for the existing
    MCP tool, the existing tests, and any state.json files written by the
    older codepath.
    """
    data = state.data
    winner = data.get("winner")
    reason = data.get("reason", "?")
    side_a_hp = data.get("side_a_final_hp", "?")
    side_b_hp = data.get("side_b_final_hp", "?")
    round_count = data.get("round_count", "?")
    seed = data.get("seed", "?")
    loadout_a = data.get("loadout_a", []) or []
    loadout_b = data.get("loadout_b", []) or []

    winner_label = {0: "SIDE A", 1: "SIDE B"}.get(winner, "DRAW")

    body: list[str] = [
        f"winner:      {winner_label}",
        f"reason:      {reason}",
        f"final HP:    A={side_a_hp}  B={side_b_hp}",
        f"rounds:      {round_count}",
        f"seed:        {seed[:16] + '…' if isinstance(seed, str) and len(seed) > 16 else seed}",
        "",
        f"SIDE A ({len(loadout_a)} cards):",
    ]
    body.extend(_summarize_loadout(loadout_a))
    body.append("")
    body.append(f"SIDE B ({len(loadout_b)} cards):")
    body.extend(_summarize_loadout(loadout_b))

    out_path = renders_dir / f"match_{state.id}.png"
    return _render_placeholder_png(
        out_path=out_path,
        title="DAIMON — MATCH RESOLVED",
        subtitle=f"state_id: {state.id}",
        lines=body,
    )


def render_match(state: GameState, renders_dir: Path) -> Path:
    """Match-view renderer — dispatches by payload shape.

    Two payload shapes are supported in A.4.c:
      1. ``schema.Match`` (V2 wire format) — produces an animated frame
         stream + JSON manifest under renders_dir. The animator walks every
         (round, action) × t_ms milestone, calling build_mid_action_frame +
         render_frame_to_png to produce ~12 frames per beat at 50ms intervals.
         Returns the manifest path.
      2. Legacy summary dict (pre-A.4.b dm_match shape) — produces a single
         summary still-frame PNG. Backward compat until A.4.b ships the
         engine→Match adapter and dm_match writes real Match payloads.

    The shape detection uses ``_is_match_payload``; either path keeps the
    dispatcher contract — return Path to a single artifact written under
    renders_dir.
    """
    if _is_match_payload(state.data):
        return _render_match_animated(state, renders_dir)
    return _render_match_summary(state, renders_dir)


def _make_placeholder_handler(view: str) -> Handler:
    """Factory for the seven not-yet-implemented view handlers.

    Each returned handler writes a distinct PNG so tests can verify the
    dispatcher routed to the correct view, without every view having a real
    renderer yet.
    """
    def _handler(state: GameState, renders_dir: Path) -> Path:
        out_path = renders_dir / f"{view}_{state.id}.png"
        return _render_placeholder_png(
            out_path=out_path,
            title=f"DAIMON — {view.upper()} (todo)",
            subtitle=f"state_id: {state.id}",
            lines=[
                f"view:   {view}",
                f"keys:   {sorted(state.data.keys())[:10]}",
                "",
                "The real renderer for this view lands in a later subsystem.",
                "A.2 ships the dispatcher + match renderer only.",
            ],
        )
    return _handler


# Closed handler table. Every KNOWN_VIEWS entry MUST be present; a missing
# handler here would mean the dispatcher silently drops a valid view.
DEFAULT_HANDLERS: dict[str, Handler] = {
    "match":       render_match,
    "pull":        _make_placeholder_handler("pull"),
    "inspect":     _make_placeholder_handler("inspect"),
    "collection":  _make_placeholder_handler("collection"),
    "loadout":     _make_placeholder_handler("loadout"),
    "leaderboard": _make_placeholder_handler("leaderboard"),
    "rank":        _make_placeholder_handler("rank"),
    "idle":        _make_placeholder_handler("idle"),
}

# Structural invariant: handler table must cover every known view.
_missing = KNOWN_VIEWS - set(DEFAULT_HANDLERS.keys())
if _missing:   # pragma: no cover — caught at import time
    raise RuntimeError(
        f"DEFAULT_HANDLERS missing entries for views: {sorted(_missing)}"
    )


# ---------------------------------------------------------------------------
# Sidecar for persistent last-rendered-id
# ---------------------------------------------------------------------------

def _read_last_id(renders_dir: Path) -> Optional[str]:
    """Load the persisted last-rendered id, or None if never rendered."""
    sidecar = renders_dir / LAST_ID_SIDECAR_NAME
    if not sidecar.exists():
        return None
    try:
        val = sidecar.read_text().strip()
    except OSError:
        return None
    return val or None


def _write_last_id(renders_dir: Path, state_id: str) -> None:
    """Persist the last-rendered id atomically (tmp + replace)."""
    import os
    sidecar = renders_dir / LAST_ID_SIDECAR_NAME
    tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
    tmp.write_text(state_id)
    os.replace(tmp, sidecar)


# ---------------------------------------------------------------------------
# Game terminal
# ---------------------------------------------------------------------------

@dataclass
class DispatchResult:
    """What a single dispatch call produced — exposed for tests/telemetry."""
    state: Optional[GameState]
    action: str                          # "rendered" | "skipped_dedupe" | "skipped_no_state" | "error"
    out_path: Optional[Path] = None
    error: Optional[str] = None


class GameTerminal:
    """Long-running watcher over ``state.json``.

    Typical lifecycle:

        term = GameTerminal()
        term.start()     # drains current state once, then watches
        term.wait()      # blocks until stop()

    Or as a context manager:

        with GameTerminal() as term:
            term.wait()
    """

    def __init__(
        self,
        state_path: Optional[Path | str] = None,
        renders_dir: Optional[Path | str] = None,
        handlers: Optional[dict[str, Handler]] = None,
    ):
        self.state_path = resolve_state_path(state_path)
        self.renders_dir = resolve_renders_dir(renders_dir)
        ensure_renders(self.renders_dir)
        self.handlers = dict(handlers) if handlers is not None else dict(DEFAULT_HANDLERS)

        # Runtime state (guarded by _lock).
        self._last_rendered_id: Optional[str] = _read_last_id(self.renders_dir)
        self._lock = threading.Lock()
        self._observer: Optional[Observer] = None
        self._stop_evt = threading.Event()

    # ----- lifecycle -----

    def start(self) -> None:
        """Render current state (if not already dedupe'd), then watch for changes."""
        if self._observer is not None:
            raise RuntimeError("terminal already started")

        # Drain: the file may already exist from a prior write. Render once.
        self.dispatch_once()

        handler = _StateEventHandler(self)
        self._observer = Observer()
        # Watch the PARENT directory (watchdog's file-level watch has flaky
        # behavior under atomic-rename writes — POSIX replace() creates the
        # target inode fresh, so a watch on the old inode never fires again).
        watch_dir = str(self.state_path.parent)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._observer.schedule(handler, watch_dir, recursive=False)
        self._observer.start()
        logger.info(
            "GameTerminal live: state=%s renders=%s",
            self.state_path, self.renders_dir,
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the observer. Idempotent."""
        self._stop_evt.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=timeout)
            self._observer = None

    def wait(self) -> None:
        """Block until stop() is called (or Ctrl-C)."""
        try:
            self._stop_evt.wait()
        except KeyboardInterrupt:     # pragma: no cover — interactive only
            self.stop()

    def __enter__(self) -> "GameTerminal":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ----- dispatch -----

    def dispatch_once(self) -> DispatchResult:
        """Read state.json once and maybe render. Used by start() + tests.

        Never raises — all errors (malformed state, handler exception) become
        a ``DispatchResult`` with ``action='error'``. Callers can inspect the
        result; the terminal itself keeps running either way.
        """
        with self._lock:
            try:
                state = read_state(self.state_path)
            except ValueError as e:
                # Malformed state file — log and keep running. We deliberately
                # do NOT quarantine (unlike the legacy inbox) because the
                # writer will overwrite it atomically on next call.
                logger.warning("state.json malformed: %s", e)
                return DispatchResult(state=None, action="error", error=str(e))

            if state is None:
                return DispatchResult(state=None, action="skipped_no_state")

            if not should_render(state, self._last_rendered_id):
                return DispatchResult(state=state, action="skipped_dedupe")

            handler = self.handlers.get(state.view)
            if handler is None:
                msg = f"no handler for view {state.view!r}"
                logger.warning(msg)
                return DispatchResult(state=state, action="error", error=msg)

            try:
                out = handler(state, self.renders_dir)
            except Exception as e:   # noqa: BLE001 — handler failure is isolated
                logger.exception("handler error for view=%s", state.view)
                return DispatchResult(
                    state=state, action="error", error=f"{type(e).__name__}: {e}",
                )

            # Persist progress AFTER handler success. On crash before this,
            # the next boot re-renders (safe — renders are idempotent).
            self._last_rendered_id = state.id
            try:
                _write_last_id(self.renders_dir, state.id)
            except OSError as e:
                # Losing the sidecar just means a reboot will re-render this
                # state once. Not fatal — log and continue.
                logger.warning("could not persist last-rendered-id: %s", e)

            return DispatchResult(
                state=state, action="rendered", out_path=out,
            )

    @property
    def last_rendered_id(self) -> Optional[str]:
        """In-memory last-rendered id (for tests + telemetry)."""
        return self._last_rendered_id


# ---------------------------------------------------------------------------
# Watchdog glue
# ---------------------------------------------------------------------------

class _StateEventHandler(FileSystemEventHandler):
    """Bridge watchdog events → GameTerminal.dispatch_once.

    We care about events on state.json only; anything else in the watched
    directory is ignored. The atomic-rename path (tmp → state.json) can
    surface as either a modified event on the new inode or a moved event
    where dest_path == state.json — we handle both.
    """

    def __init__(self, terminal: GameTerminal):
        self.terminal = terminal
        self.target = terminal.state_path

    def _is_target(self, path_str: str) -> bool:
        try:
            return Path(path_str) == self.target
        except (ValueError, OSError):
            return False

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        if self._is_target(event.src_path):
            self.terminal.dispatch_once()

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        if self._is_target(event.src_path):
            self.terminal.dispatch_once()

    def on_moved(self, event: FileMovedEvent) -> None:
        if event.is_directory:
            return
        # atomic rename: the tmp file's MOVED event lands here with
        # dest_path == state.json.
        if self._is_target(getattr(event, "dest_path", "")):
            self.terminal.dispatch_once()
