"""ASCII renderer for the spectator HUD.

Pure render: takes a ``Frame`` (or no frame, for IDLE), returns the full
terminal screen as a single string with ANSI escapes. Owning loop diffs
or just clears + writes — render itself does no I/O.

Layout (80 cols × 24 rows is the design target; gracefully truncates wider):

    ╔══════════════════════════════════════════════════════════════════════════════╗
    ║                  DAIMON — Champion Lyra (Champion)                        ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  OPPONENT                                                                    ║
    ║  [Voltc] HP: ████████░░ 11/14   [Bulw ] HP: █████░░░░░  6/10                ║
    ║  [Storm] HP: ██████████  8/8    [Tide ] HP: ██████████  8/8                 ║
    ║  [Mind ] HP: ██████████ 15/15   [Shell] HP: ██████████ 12/12                ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  → Voltcat Apex hits Blade Foxling for 7  (player/2: 13→6)                   ║
    ║    └ trigger: Blade Foxling counters for 3  (opponent/0: 14→11)              ║
    ║      Iron Boar attacks Bulwarthog for 4                                      ║
    ║      Bulwarthog dies                                                         ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║  PLAYER (santiago)                                                           ║
    ║  [Glimm] HP: ██████░░░░  8/12   [Boar ] HP: ██████████ 14/14                ║
    ║  [Fox L] HP: █████░░░░░  6/13   [Fox R] HP: ██████████  6/6                 ║
    ║  [Anvil] HP: ██████████ 11/11   [Dash ] HP: ██████████ 13/13                ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║ R1 act 3/9 · step 7/53 · 1.0× · ▶ playing  │  [space]pause [→/←]step [q]quit ║
    ╚══════════════════════════════════════════════════════════════════════════════╝

Color: optional ANSI 256-color. Rendering is deterministic — same frame in,
same string out. The app loop renders on every tick at most once.
"""

from __future__ import annotations

from typing import Iterable, Optional

from daimon.play.hud.playback import (
    Frame,
    Phase,
    PlaybackStatus,
    SPEED_LADDER,
    Step,
)
from daimon.play.schema import ActionKind, Match, Side


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

WIDTH = 80
HP_BAR_LEN = 10

# ANSI helpers — kept tiny + escape-string-only so tests can assert on them.
RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
WHITE = "\x1b[37m"
GRAY = "\x1b[90m"

# Element → color. Mapped to readable ANSI 4-bit so it works on any terminal.
ELEMENT_COLOR = {
    "fire":   RED,
    "water":  BLUE,
    "nature": GREEN,
    "volt":   YELLOW,
    "void":   MAGENTA,
}

KIND_PREFIX = {
    ActionKind.DAMAGE:  "⚔",
    ActionKind.HEAL:    "✚",
    ActionKind.BUFF:    "↑",
    ActionKind.DEBUFF:  "↓",
    ActionKind.SHIELD:  "◇",
    ActionKind.STATUS:  "✦",
    ActionKind.DEATH:   "✕",
    ActionKind.PASSIVE: "·",
}


# ---------------------------------------------------------------------------
# Frame builder
# ---------------------------------------------------------------------------

def render_frame(frame: Frame, *, color: bool = True) -> str:
    """Build the full terminal screen for the given playback frame.

    `color=False` strips ANSI escapes — useful for tests + plain logs.

    Layout shape is constant (21 lines × 80 cols) regardless of whether the
    current cursor sits on an action step or a chrome phase step. When the
    cursor lands on a phase step (LINEUP / ROUND_START / OUTCOME), the
    middle "log" band is replaced with a centered chrome banner; the rest
    (title, lineups, status bar) stays intact.
    """
    lines: list[str] = []
    lines.append(_box_top())
    lines.append(_title_line(frame, color=color))
    lines.append(_divider())
    lines.extend(_lineup_section(frame, side=Side.OPPONENT, color=color))
    lines.append(_divider())
    if frame.current_step is not None and frame.current_step.is_phase:
        lines.extend(_chrome_section(frame, color=color))
    else:
        lines.extend(_log_section(frame, color=color))
    lines.append(_divider())
    lines.extend(_lineup_section(frame, side=Side.PLAYER, color=color))
    lines.append(_divider())
    lines.append(_status_line(frame, color=color))
    lines.append(_box_bottom())
    return "\n".join(lines)


def render_idle(*, recent: Iterable[str] = (), color: bool = True) -> str:
    """Screen shown when no match is loaded. Lists last-rendered match ids.

    Renderer doesn't fetch them — the app passes them in.
    """
    lines: list[str] = []
    lines.append(_box_top())
    lines.append(_centered("DAIMON — spectator HUD", color=color, bold=True))
    lines.append(_divider())
    lines.append(_centered("waiting for match…", color=color, dim=True))
    lines.append(_blank())
    lines.append(_centered("(claude can run a match via dm_match_npc, dm_match)",
                           color=color, dim=True))
    lines.append(_divider())
    recent_l = list(recent)[:5]
    if recent_l:
        lines.append(_left("recent matches:", color=color, bold=True))
        for r in recent_l:
            lines.append(_left(f"  · {r}", color=color, dim=True))
    else:
        lines.append(_left("  (none yet)", color=color, dim=True))
    lines.append(_divider())
    lines.append(_status_line_idle(color=color))
    lines.append(_box_bottom())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Box-drawing primitives
# ---------------------------------------------------------------------------

def _box_top() -> str:
    return "╔" + "═" * (WIDTH - 2) + "╗"


def _box_bottom() -> str:
    return "╚" + "═" * (WIDTH - 2) + "╝"


def _divider() -> str:
    return "╠" + "═" * (WIDTH - 2) + "╣"


def _blank() -> str:
    return "║" + " " * (WIDTH - 2) + "║"


def _frame_line(content: str) -> str:
    """Pad/truncate ``content`` (visible-width) to fit between the box walls."""
    visible = _strip_ansi(content)
    pad = (WIDTH - 2) - len(visible)
    if pad < 0:
        # Truncate visible portion; ANSI inside will be passed through, which
        # is fine — terminals don't care about partial color codes if we
        # never strip an open without its close. We accept that cost and
        # truncate the raw string by visible width.
        content = _truncate_visible(content, WIDTH - 2)
        pad = 0
    return "║" + content + " " * pad + "║"


def _centered(text: str, *, color: bool, bold: bool = False, dim: bool = False) -> str:
    visible = text
    pad_each = max(0, ((WIDTH - 2) - len(visible)) // 2)
    body = " " * pad_each + text
    if color:
        if bold:
            body = BOLD + body + RESET
        elif dim:
            body = DIM + body + RESET
    return _frame_line(body)


def _left(text: str, *, color: bool, bold: bool = False, dim: bool = False) -> str:
    if color:
        if bold:
            text = BOLD + text + RESET
        elif dim:
            text = DIM + text + RESET
    return _frame_line(text)


# ---------------------------------------------------------------------------
# Title + status
# ---------------------------------------------------------------------------

def _title_line(frame: Frame, *, color: bool) -> str:
    """Top banner — usually opponent name; swaps to phase label on chrome."""
    cur = frame.current_step
    if cur is not None and cur.is_phase:
        if cur.phase == Phase.LINEUP:
            return _centered("DAIMON — match starting", color=color, bold=True)
        if cur.phase == Phase.ROUND_START:
            return _centered(
                f"DAIMON — round {cur.round_number} begins",
                color=color, bold=True,
            )
        if cur.phase == Phase.OUTCOME:
            outcome = frame.match.outcome
            if outcome is None:
                title = "DAIMON — match concluded"
            elif outcome.winner == Side.DRAW:
                title = "DAIMON — draw"
            else:
                title = f"DAIMON — {outcome.winner.value.upper()} wins"
            return _centered(title, color=color, bold=True)
    match = frame.match
    opp = match.participants.get("opponent")
    name = opp.name if opp else "opponent"
    rank = opp.rank if opp else "?"
    title = f"DAIMON — {name}  ({rank})"
    return _centered(title, color=color, bold=True)


def _status_line(frame: Frame, *, color: bool) -> str:
    cur_step = frame.current_step
    if cur_step is None:
        round_no = "-"
        action_no = "-/-"
    elif cur_step.is_phase:
        # Chrome steps don't have a parent-action notion. Use a label per phase
        # so the status bar stays informative without misreporting "act 1/0".
        if cur_step.phase == Phase.LINEUP:
            round_no = "-"
            action_no = "lineup"
        elif cur_step.phase == Phase.ROUND_START:
            round_no = str(cur_step.round_number)
            action_no = "intro"
        else:   # OUTCOME
            round_no = str(cur_step.round_number)
            action_no = "final"
    else:
        round_no = str(cur_step.round_number)
        per = frame.elapsed_steps_in_round.get(cur_step.round_number, 0)
        # round-action count is hard to compute precisely under nested triggers;
        # we show the step's parent action_index for orientation.
        action_no = f"{cur_step.action_index + 1}/{per}"

    speed_str = _speed_label(frame.speed)
    state_str = _status_label(frame.status, color=color)

    left = (
        f" R{round_no} act {action_no} · "
        f"step {frame.cursor + 1}/{frame.total_steps} · "
        f"{speed_str} · {state_str}"
    )
    right = "[sp]pause [←→]step [↑↓]spd [r]rst [n]end [q]quit "

    # Compose with right-justified controls if there's room.
    visible_left = _strip_ansi(left)
    visible_right = _strip_ansi(right)
    avail = (WIDTH - 2) - len(visible_left) - len(visible_right) - 1
    if avail >= 1:
        return _frame_line(left + " " * avail + " │ " + right)
    return _frame_line(left)


def _status_line_idle(*, color: bool) -> str:
    left = " IDLE — no match loaded"
    right = "[q]quit  [n]demo  "
    avail = (WIDTH - 2) - len(left) - len(right) - 1
    if avail >= 1:
        return _frame_line(left + " " * avail + " │ " + right)
    return _frame_line(left)


def _speed_label(speed: float) -> str:
    # Compact representation: 0.25 → "0.25×", 1.0 → "1.0×".
    if speed >= 1:
        return f"{speed:.1f}×"
    return f"{speed:.2f}×"


def _status_label(status: PlaybackStatus, *, color: bool) -> str:
    icons = {
        PlaybackStatus.IDLE:    "○ idle",
        PlaybackStatus.PLAYING: "▶ playing",
        PlaybackStatus.PAUSED:  "❚❚ paused",
        PlaybackStatus.ENDED:   "■ ended",
    }
    text = icons.get(status, str(status.value))
    if not color:
        return text
    color_for = {
        PlaybackStatus.PLAYING: GREEN,
        PlaybackStatus.PAUSED:  YELLOW,
        PlaybackStatus.ENDED:   CYAN,
        PlaybackStatus.IDLE:    GRAY,
    }
    return color_for.get(status, "") + text + RESET


# ---------------------------------------------------------------------------
# Lineup section
# ---------------------------------------------------------------------------

def _lineup_section(frame: Frame, *, side: Side, color: bool) -> list[str]:
    part = frame.match.participants.get(side.value)
    if part is None:
        return [_left(f"  {side.value.upper()}  (missing)", color=color, dim=True)]

    label = "OPPONENT" if side == Side.OPPONENT else f"PLAYER ({part.name})"
    lines: list[str] = [_left(f"  {label}", color=color, bold=True)]

    # Pair cards in rows of 2 — six cards = three rows.
    cards = sorted(part.loadout, key=lambda c: c.position)
    for i in range(0, len(cards), 2):
        row = cards[i:i + 2]
        cells = [_card_cell(frame, side, c, color=color) for c in row]
        lines.append(_left("  " + "   ".join(cells), color=color))
    return lines


def _card_cell(frame: Frame, side: Side, card, *, color: bool) -> str:
    """Render one [Name] HP: ████░░░░ N/M cell."""
    cur_hp = frame.hp.get(side, card.position, default=card.hp)
    bar = _hp_bar(cur_hp, card.hp_max, color=color)
    name = (card.short_name or card.name or card.species)[:5]
    elem_color = ELEMENT_COLOR.get(card.element.value, "") if color else ""
    name_str = (elem_color + f"[{name:<5}]" + (RESET if elem_color else ""))
    hp_str = f"{cur_hp:>2}/{card.hp_max:<2}"
    if cur_hp <= 0 and color:
        # Strikethrough-ish for dead cards.
        name_str = GRAY + f"[{name:<5}]" + RESET
    return f"{name_str} HP: {bar} {hp_str}"


def _hp_bar(cur: int, mx: int, *, color: bool) -> str:
    if mx <= 0:
        return "░" * HP_BAR_LEN
    pct = max(0.0, min(1.0, cur / mx))
    filled = int(round(pct * HP_BAR_LEN))
    bar_text = "█" * filled + "░" * (HP_BAR_LEN - filled)
    if not color:
        return bar_text
    if pct >= 0.66:
        col = GREEN
    elif pct >= 0.33:
        col = YELLOW
    else:
        col = RED
    return col + bar_text + RESET


# ---------------------------------------------------------------------------
# Log section
# ---------------------------------------------------------------------------

LOG_ROWS = 5    # rows reserved for the action log


def _log_section(frame: Frame, *, color: bool) -> list[str]:
    if not frame.log_tail:
        return [_blank() for _ in range(LOG_ROWS)]
    # Build lines for each step in the tail; current step gets ▶ marker.
    items = list(frame.log_tail)
    # We want most-recent at the bottom, so leave items in chronological order.
    rendered = [_log_line(s, is_current=(s.index == frame.cursor), color=color)
                for s in items]
    # Pad the top with blanks if we have < LOG_ROWS items so the layout is stable.
    while len(rendered) < LOG_ROWS:
        rendered.insert(0, _blank())
    # If we somehow have more (shouldn't, capped at LOG_TAIL_LEN), take last N.
    if len(rendered) > LOG_ROWS:
        rendered = rendered[-LOG_ROWS:]
    return rendered


def _log_line(step: Step, *, is_current: bool, color: bool) -> str:
    if step.is_phase:
        return _phase_log_line(step, is_current=is_current, color=color)
    indent = "  " + ("  " * step.depth)
    arrow = "▶" if is_current else " "
    glyph = KIND_PREFIX.get(step.action.kind, "·")
    text = step.action.log_line.strip() or _fallback_log(step)
    side_marker = _side_marker(step.action.actor.side)
    line = f"{indent}{arrow} {glyph} {side_marker} {text}"
    if not color:
        return _frame_line(line)
    if is_current:
        line = BOLD + line + RESET
    elif step.depth > 0:
        line = DIM + line + RESET
    return _frame_line(line)


def _phase_log_line(step: Step, *, is_current: bool, color: bool) -> str:
    """Compact in-log marker for chrome events (used when scrolled past)."""
    arrow = "▶" if is_current else " "
    if step.phase == Phase.LINEUP:
        body = "─── match begins ───"
    elif step.phase == Phase.ROUND_START:
        body = f"═══ ROUND {step.round_number} ═══"
    else:   # OUTCOME
        body = "═══ match concluded ═══"
    line = f"  {arrow}  {body}"
    if not color:
        return _frame_line(line)
    if is_current:
        line = BOLD + CYAN + line + RESET
    else:
        line = CYAN + line + RESET
    return _frame_line(line)


# ---------------------------------------------------------------------------
# Chrome section — replaces the log band when current step is a phase
# ---------------------------------------------------------------------------

def _chrome_section(frame: Frame, *, color: bool) -> list[str]:
    """Centered banner shown when cursor sits on a phase step.

    Always produces exactly ``LOG_ROWS`` lines so the overall frame stays
    a fixed 21-line height regardless of what the cursor is showing.
    """
    cur = frame.current_step
    assert cur is not None and cur.is_phase
    if cur.phase == Phase.LINEUP:
        rows = _lineup_chrome_rows(frame)
    elif cur.phase == Phase.ROUND_START:
        rows = _round_chrome_rows(frame, cur.round_number)
    else:
        rows = _outcome_chrome_rows(frame)
    # Pad/truncate to exactly LOG_ROWS so layout height stays constant.
    while len(rows) < LOG_ROWS:
        rows.append("")
    rows = rows[:LOG_ROWS]
    return [_centered(r, color=color, bold=(i == 0), dim=(i > 0 and not r.startswith("═")))
            for i, r in enumerate(rows)]


def _lineup_chrome_rows(frame: Frame) -> list[str]:
    p = frame.match.participants.get("player")
    o = frame.match.participants.get("opponent")
    pl = p.name if p else "?"
    op = o.name if o else "?"
    return [
        "═══ MATCH STARTING ═══",
        f"{pl}  vs  {op}",
        "lineups revealed — combat begins",
        "",
        "",
    ]


def _round_chrome_rows(frame: Frame, round_no: int) -> list[str]:
    # Show first-player for the round, if findable in source rounds list.
    fp = ""
    for r in frame.match.rounds:
        if r.round == round_no:
            fp = f"first player: {r.first_player.value}"
            break
    return [
        f"═══ ROUND {round_no} ═══",
        fp,
        "",
        "",
        "",
    ]


def _outcome_chrome_rows(frame: Frame) -> list[str]:
    o = frame.match.outcome
    if o is None:
        return ["═══ MATCH OVER ═══", "", "", "", ""]
    p_name = (frame.match.participants.get("player").name
              if frame.match.participants.get("player") else "player")
    o_name = (frame.match.participants.get("opponent").name
              if frame.match.participants.get("opponent") else "opponent")
    p_hp = o.player_hp_remaining
    o_hp = o.opponent_hp_remaining
    rounds = o.stats.round_count
    cards_killed = o.stats.cards_killed
    p_killed = cards_killed.get("player", 0)
    o_killed = cards_killed.get("opponent", 0)
    if o.winner == Side.DRAW:
        banner = "═══ DRAW ═══"
    else:
        banner = f"═══ {o.winner.value.upper()} WINS ═══"
    return [
        banner,
        f"{p_name}: {p_hp} hp · {o_name}: {o_hp} hp",
        f"rounds: {rounds} · kills — player {p_killed}, opponent {o_killed}",
        "",
        "",
    ]


def _side_marker(side: Side) -> str:
    return "↑" if side == Side.PLAYER else "↓"


def _fallback_log(step: Step) -> str:
    a = step.action
    actor = a.actor.card if a.actor else "?"
    target = a.target.card if a.target else None
    amt = a.amount
    if a.kind == ActionKind.DAMAGE:
        if target and amt is not None:
            return f"{actor} hits {target} for {amt}"
        return f"{actor} attacks"
    if a.kind == ActionKind.HEAL:
        return f"{actor} heals {target or 'self'} for {amt or '?'}"
    if a.kind == ActionKind.DEATH:
        return f"{target or actor} falls"
    if a.kind == ActionKind.SHIELD:
        return f"{actor} gains shield {amt or ''}".strip()
    if a.kind == ActionKind.BUFF:
        return f"{actor} buffs {target or 'self'}"
    if a.kind == ActionKind.DEBUFF:
        return f"{actor} debuffs {target or '?'}"
    return f"{actor} → {a.kind.value}"


# ---------------------------------------------------------------------------
# Tiny ANSI utils
# ---------------------------------------------------------------------------

_ANSI_RE = None


def _strip_ansi(s: str) -> str:
    """Strip ANSI CSI escapes for visible-width math."""
    global _ANSI_RE
    if _ANSI_RE is None:
        import re
        _ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
    return _ANSI_RE.sub("", s)


def _truncate_visible(s: str, n: int) -> str:
    """Truncate string s to visible width n. Naïve — counts code points."""
    visible = _strip_ansi(s)
    if len(visible) <= n:
        return s
    # Walk forward keeping a running visible-char count, drop after n.
    out = []
    count = 0
    i = 0
    while i < len(s) and count < n:
        if s[i] == "\x1b":
            # Pass through CSI to next 'm'
            j = s.find("m", i)
            if j < 0:
                out.append(s[i])
                i += 1
                continue
            out.append(s[i:j + 1])
            i = j + 1
        else:
            out.append(s[i])
            count += 1
            i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Misc helpers (exposed for tests)
# ---------------------------------------------------------------------------

def speed_ladder() -> tuple[float, ...]:
    """Re-export for tests/UI."""
    return SPEED_LADDER


def render_outcome_banner(match: Match, *, color: bool = True) -> Optional[str]:
    """Optional one-line winner banner. Renderer concatenates as needed."""
    outcome = match.outcome
    if outcome is None:
        return None
    if outcome.winner == Side.DRAW:
        verdict = "DRAW"
    else:
        verdict = f"{outcome.winner.value.upper()} WINS"
    label = f" === {verdict} — player {outcome.player_hp_remaining} hp · opponent {outcome.opponent_hp_remaining} hp === "
    if color:
        return BOLD + CYAN + label + RESET
    return label
