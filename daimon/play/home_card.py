"""Home-card HTML renderer — Marvel-Snap-style chat embed.

Consumes the payload from ``daimon.mcp.server.dm_home`` and returns a
self-contained HTML string suitable for posting into the LivingAgent
webapp chat as a ``:::html`` fenced block.

## Why this lives in the daimon repo

DAIMON state lives entirely on the user's machine (``~/.config/daimon/``).
Coda runs on the VPS and has no access to it. The home card therefore
has to be assembled by the user's local Claude Code, which:

  1. Calls ``dm_home_card`` (MCP tool — see ``daimon/mcp/server.py``)
  2. Receives the rendered HTML string
  3. Wraps it in ``:::html\\n…\\n:::`` and posts via
     ``mcp__webapp-channel__reply``

Keeping the renderer in the engine repo means the card always reflects
the canonical payload shape — no second source of truth, no drift.

## Sanitization contract

The frontend MarkdownRenderer runs DOMPurify with this allowlist
(verified ``frontend/src/components/MarkdownRenderer.jsx`` line 13-38):

  ALLOWED_TAGS: div, span, p, button, img, svg, style, header, nav,
                section, plus most semantic HTML
  ALLOWED_ATTR: class, id, style, href, onclick, onchange, oninput,
                onmouseover, onmouseout, plus SVG attrs
  FORBID_TAGS:  script, iframe, form, meta, link
  ALLOW_DATA_ATTR: false

Everything in this renderer respects that allowlist. **No ``data-*``
attributes** — they get stripped silently and break button wiring.

## Button round-trip

Buttons fire ``window.agentAction('send_message', {text: '@daimon …'},
this)`` (Chat.jsx line 345-353). The webapp posts the message AS THE
USER, which then broadcasts via ``pg_notify('agent_events', …)`` to
the SSE stream. The user's local Claude Code mention-watcher (next
phase) sees the ``@daimon`` and reacts by invoking the corresponding
MCP tool (e.g. ``dm_match_npc("Sparring Sam")``).

## Color contract

Terminal-native palette per ``docs/animation_design.md`` (locked
2026-04-22, commit 756b38d). Fallbacks for chat:

  bg          #0f172a   slate-950 (cardstock)
  panel       #1e293b   slate-800 (inset)
  text        #e2e8f0   slate-200 (primary)
  muted       #94a3b8   slate-400 (secondary)
  accent      #818cf8   indigo-400 (CTAs, links)
  win         #34d399   emerald-400
  loss        #f87171   red-400
  draw        #fbbf24   amber-400
  rare        #c084fc   purple-400 (RARE+)
  epic        #f472b6   pink-400 (EPIC)
  legendary   #fbbf24   amber-400 (LEGENDARY)

All inline ``style=""`` so we don't depend on chat CSS shipping any
``.daimon-*`` classes.

## Test surface

See ``tests/test_home_card.py``. Renderer is a pure function — given
identical payload input, returns identical HTML. No globals, no time,
no I/O.
"""

from __future__ import annotations

import html as _html
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Color palette — see module docstring
# ---------------------------------------------------------------------------

_C_BG = "#0f172a"
_C_PANEL = "#1e293b"
_C_PANEL_HI = "#334155"
_C_TEXT = "#e2e8f0"
_C_MUTED = "#94a3b8"
_C_ACCENT = "#818cf8"
_C_ACCENT_HI = "#a5b4fc"
_C_WIN = "#34d399"
_C_LOSS = "#f87171"
_C_DRAW = "#fbbf24"

_RARITY_COLOR = {
    "COMMON": _C_MUTED,
    "UNCOMMON": "#67e8f9",       # cyan-300
    "RARE": "#c084fc",           # purple-400
    "EPIC": "#f472b6",           # pink-400
    "LEGENDARY": _C_DRAW,
}


# ---------------------------------------------------------------------------
# Tiny safe-HTML helpers
# ---------------------------------------------------------------------------

def _esc(value: Any) -> str:
    """HTML-escape any value coercible to str. Returns '' for None."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def _esc_js(value: Any) -> str:
    """Escape for embedding inside an ``onclick="…"`` JS string literal.

    Combines JS-string escaping with HTML-attribute escaping. The result is
    intended to live between single quotes inside a double-quoted attribute,
    e.g. ``onclick="window.x('THIS')"``.

    Conservative: we only allow a small set of safe characters through; any
    non-printable ASCII or quote-class character gets escaped. This blocks
    the common XSS vectors (``</script>`` is impossible since we forbid <,
    breakout via `'` is impossible since we escape it, etc.)
    """
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\\", "\\\\")
    s = s.replace("'", "\\'")
    s = s.replace('"', "\\&quot;")
    s = s.replace("<", "\\u003c")
    s = s.replace(">", "\\u003e")
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\t", " ")
    # &-escape ampersand so the attribute value remains valid HTML.
    s = s.replace("&", "&amp;")
    # Re-escape the &amp; we just created inside any JS escapes.
    return s


def _bubble_style(*, bg: str = _C_BG, extra: str = "") -> str:
    """Common card-container inline style block."""
    return (
        f"background:{bg};color:{_C_TEXT};"
        "border-radius:12px;padding:16px;"
        "font-family:system-ui,-apple-system,sans-serif;"
        "font-size:14px;line-height:1.4;"
        f"{extra}"
    )


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_no_identity() -> str:
    """The card we show when ``dm_home`` returned ``error=no_identity``.

    Onboarding takeover — single CTA: bootstrap an identity. The
    ``send_message`` button posts `@daimon init` to the chat so the
    user's local Claude Code mention-watcher picks it up. Until that
    watcher exists the user can also just type the same thing manually.
    """
    style = _bubble_style(extra="border:1px solid " + _C_PANEL_HI + ";")
    return (
        f'<div style="{style}">'
        f'<div style="font-size:12px;color:{_C_MUTED};'
        f'letter-spacing:1.5px;">DAIMON</div>'
        f'<div style="font-size:20px;font-weight:600;margin-top:4px;">'
        "No identity yet"
        "</div>"
        f'<div style="margin:12px 0;color:{_C_MUTED};">'
        "Bootstrap your DAIMON identity to start mining currency, "
        "pulling cards, and battling NPCs."
        "</div>"
        f'<button onclick="window.agentAction(\'send_message\','
        f'{{text:\'@daimon init\'}}, this)" '
        f'style="background:{_C_ACCENT};color:{_C_BG};border:none;'
        f'border-radius:8px;padding:12px 18px;font-weight:600;'
        f'font-size:15px;cursor:pointer;width:100%;">'
        "Initialize DAIMON"
        "</button>"
        "</div>"
    )


def _render_header(identity: Dict[str, Any], rank: Dict[str, Any],
                   balance: int, pull: Dict[str, Any]) -> str:
    """Top strip: handle + tier/rank + balance + pull readiness."""
    handle = identity.get("handle") or "unregistered"
    pubkey = identity.get("pubkey_hex") or ""
    pubkey_short = pubkey[:8] + "…" if len(pubkey) > 10 else pubkey

    tier = rank.get("tier", "Rookie")
    rank_n = rank.get("rank")
    total = rank.get("total_players", 0)
    if rank_n and total:
        tier_line = f"{tier} · #{rank_n} of {total}"
    else:
        tier_line = f"{tier} · unranked"

    pulls_avail = pull.get("pulls_available", 0)
    cost = pull.get("cost", 100)
    to_next = pull.get("balance_to_next_pull", cost)

    if pulls_avail > 0:
        pull_chip = (
            f'<span style="color:{_C_ACCENT};font-weight:600;">'
            f"{pulls_avail}× pull ready"
            "</span>"
        )
    else:
        pull_chip = (
            f'<span style="color:{_C_MUTED};">'
            f"next pull in {to_next}¤"
            "</span>"
        )

    return (
        '<div style="display:flex;justify-content:space-between;'
        'align-items:flex-start;margin-bottom:14px;gap:12px;">'
        # Left: handle + tier
        '<div style="min-width:0;flex:1;">'
        f'<div style="font-size:16px;font-weight:600;'
        'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
        f"{_esc(handle)}"
        "</div>"
        f'<div style="font-size:12px;color:{_C_MUTED};margin-top:2px;'
        'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
        f"{_esc(tier_line)}"
        "</div>"
        f'<div style="font-size:11px;color:{_C_MUTED};margin-top:2px;'
        'font-family:monospace;opacity:0.6;">'
        f"{_esc(pubkey_short)}"
        "</div>"
        "</div>"
        # Right: balance + pull readiness
        '<div style="text-align:right;flex-shrink:0;">'
        '<div style="font-size:20px;font-weight:700;'
        f'color:{_C_ACCENT};">'
        f"{int(balance)}¤"
        "</div>"
        '<div style="font-size:11px;margin-top:2px;">'
        f"{pull_chip}"
        "</div>"
        "</div>"
        "</div>"
    )


# Crest emoji per tier — printed in the ceremony banner. Kept as plain
# unicode (no SVG) so DOMPurify never strips it.
_TIER_CREST = {
    "Novice": "✦",        # four-pointed star — the first achievement
    "Veteran": "⚔",       # crossed swords — battle-worn
    "Elite": "✪",         # filled star — eliteness
    "Champion": "♛",      # queen — top of the ladder
}

# Background gradient per tier — celebratory, distinct per tier so the
# eye reads "this is something different" even at-a-glance.
_TIER_GRADIENT = {
    "Novice": ("#22d3ee", "#0891b2"),    # cyan-400 → cyan-600
    "Veteran": ("#a78bfa", "#7c3aed"),   # violet-400 → violet-600
    "Elite": ("#f472b6", "#db2777"),     # pink-400 → pink-600
    "Champion": ("#fbbf24", "#d97706"),  # amber-400 → amber-600
}


def _render_tier_ceremony(ceremony: Optional[Dict[str, Any]]) -> str:
    """Celebratory banner above the play CTA when a tier crossing is unclaimed.

    Renders nothing when ``ceremony`` is None (the common case — most
    callers won't have a pending ceremony at any given moment).

    For multi-tier jumps (``tiers_to_mint`` length > 1) the banner shows
    the highest tier as the headline crest + "+N¤" total, with a small
    sub-line listing the intermediate tiers ("through Novice + Veteran")
    so the player understands they're claiming multiple ceremonies at
    once.

    The CLAIM button posts ``@daimon claim tier-up`` — the local Claude
    Code mention-watcher (or the human directly) responds by calling
    ``dm_tier_up_claim`` which mints the ledger entries.
    """
    if not ceremony:
        return ""
    pending_tier = str(ceremony.get("pending_tier") or "")
    prev_tier = str(ceremony.get("prev_tier") or "")
    reward_total = int(ceremony.get("reward_total", 0))
    tiers_to_mint = ceremony.get("tiers_to_mint") or []
    if not pending_tier or reward_total <= 0:
        # Defensive — a malformed payload shouldn't render a broken
        # banner. Drop silently and let the normal CTA take over.
        return ""

    crest = _TIER_CREST.get(pending_tier, "★")
    grad_from, grad_to = _TIER_GRADIENT.get(
        pending_tier, (_C_ACCENT, _C_ACCENT_HI)
    )

    multi_line = ""
    if len(tiers_to_mint) > 1:
        # "through Novice + Veteran" reads more naturally than a list,
        # but for 3+ skipped tiers we drop to comma-join.
        labels = list(tiers_to_mint)
        if len(labels) == 2:
            joined = " + ".join(labels)
        else:
            joined = ", ".join(labels[:-1]) + " + " + labels[-1]
        multi_line = (
            f'<div style="font-size:11px;margin-top:4px;opacity:0.85;'
            'font-weight:500;">'
            f"includes {_esc(joined)}"
            "</div>"
        )

    sub_line = (
        f"promoted from {_esc(prev_tier)}" if prev_tier else "new tier reached"
    )

    cmd = "@daimon claim tier-up"
    return (
        '<button '
        f'onclick="window.agentAction(\'send_message\','
        f'{{text:\'{_esc_js(cmd)}\'}}, this)" '
        f'style="background:linear-gradient(135deg,{grad_from} 0%,'
        f'{grad_to} 100%);color:{_C_BG};border:none;'
        f'border-radius:10px;padding:14px 16px;font-weight:700;'
        'font-size:14px;cursor:pointer;width:100%;text-align:left;'
        'margin-bottom:12px;display:block;'
        f'box-shadow:0 0 0 2px {grad_from} inset;">'
        '<div style="display:flex;justify-content:space-between;'
        'align-items:center;gap:10px;">'
        # Left: crest + tier label
        '<div style="min-width:0;flex:1;">'
        '<div style="font-size:11px;letter-spacing:1.5px;opacity:0.75;">'
        "TIER UP"
        "</div>"
        '<div style="font-size:18px;margin-top:2px;'
        'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
        f'<span style="margin-right:6px;">{_esc(crest)}</span>'
        f"{_esc(pending_tier)}"
        "</div>"
        f'<div style="font-size:11px;margin-top:2px;opacity:0.75;'
        'font-weight:500;">'
        f"{sub_line}"
        "</div>"
        f"{multi_line}"
        "</div>"
        # Right: reward + claim chip
        '<div style="text-align:right;flex-shrink:0;">'
        '<div style="font-size:20px;font-weight:800;'
        'letter-spacing:-0.5px;">'
        f"+{reward_total}¤"
        "</div>"
        '<div style="font-size:11px;margin-top:2px;opacity:0.85;'
        'letter-spacing:1px;">CLAIM</div>'
        "</div>"
        "</div>"
        "</button>"
    )


def _render_play_cta(recommended: Optional[Dict[str, Any]]) -> str:
    """The Marvel-Snap-style giant primary button."""
    if recommended is None:
        # All champions cleared. PvP isn't shipped yet, so render an
        # informational card instead of a dead button.
        return (
            f'<div style="background:{_C_PANEL};border-radius:10px;'
            'padding:18px;text-align:center;margin-bottom:12px;'
            f'border:1px dashed {_C_PANEL_HI};">'
            f'<div style="font-size:14px;color:{_C_DRAW};'
            'font-weight:600;">'
            "All NPC tiers cleared"
            "</div>"
            f'<div style="font-size:12px;color:{_C_MUTED};margin-top:4px;">'
            "Async PvP arrives in V1 — sit tight."
            "</div>"
            "</div>"
        )

    npc_name = recommended.get("name", "?")
    npc_tier = recommended.get("tier", "")
    npc_rank = recommended.get("rank")
    reason = recommended.get("reason", "")
    flavor = recommended.get("flavor", "")

    sub_bits: List[str] = []
    if npc_tier:
        sub = npc_tier.title() if npc_tier.islower() else npc_tier
        if npc_rank:
            sub_bits.append(f"{sub} · #{npc_rank}")
        else:
            sub_bits.append(sub)
    if reason:
        sub_bits.append(reason)
    sub_line = "  ·  ".join(sub_bits)

    cmd = f"@daimon match-npc {npc_name}"

    return (
        f'<button onclick="window.agentAction(\'send_message\','
        f'{{text:\'{_esc_js(cmd)}\'}}, this)" '
        f'style="background:linear-gradient(135deg,{_C_ACCENT} 0%,'
        f'{_C_ACCENT_HI} 100%);color:{_C_BG};border:none;'
        f'border-radius:10px;padding:18px 16px;font-weight:700;'
        'font-size:15px;cursor:pointer;width:100%;text-align:left;'
        'margin-bottom:12px;display:block;">'
        '<div style="font-size:11px;letter-spacing:1.5px;'
        'opacity:0.7;">PLAY VS</div>'
        f'<div style="font-size:18px;margin-top:2px;">'
        f"{_esc(npc_name)}"
        "</div>"
        + (
            f'<div style="font-size:11px;margin-top:4px;opacity:0.75;'
            'font-weight:500;">'
            f"{_esc(sub_line)}"
            "</div>"
            if sub_line
            else ""
        )
        + (
            f'<div style="font-size:11px;margin-top:6px;opacity:0.6;'
            'font-style:italic;font-weight:400;">'
            f"{_esc(flavor)}"
            "</div>"
            if flavor
            else ""
        )
        + "</button>"
    )


def _render_secondary_actions(pull: Dict[str, Any]) -> str:
    """Pull + Collection buttons in a 2-up row."""
    pulls_avail = pull.get("pulls_available", 0)
    cost = pull.get("cost", 100)
    to_next = pull.get("balance_to_next_pull", cost)

    # Pull button — enabled when ≥1 pull available, otherwise greyed.
    if pulls_avail > 0:
        pull_btn = (
            f'<button onclick="window.agentAction(\'send_message\','
            "{text:'@daimon pull'}, this)\" "
            f'style="background:{_C_PANEL};color:{_C_TEXT};'
            f'border:1px solid {_C_ACCENT};border-radius:8px;'
            'padding:10px;font-size:13px;font-weight:600;'
            'cursor:pointer;width:100%;text-align:center;">'
            f'<div style="font-size:11px;color:{_C_ACCENT};'
            'letter-spacing:1px;">PULL</div>'
            f'<div style="margin-top:2px;">{pulls_avail}× ready</div>'
            "</button>"
        )
    else:
        pull_btn = (
            f'<div style="background:{_C_PANEL};color:{_C_MUTED};'
            f'border:1px dashed {_C_PANEL_HI};border-radius:8px;'
            'padding:10px;font-size:13px;text-align:center;'
            'cursor:not-allowed;opacity:0.65;">'
            f'<div style="font-size:11px;letter-spacing:1px;">PULL</div>'
            f'<div style="margin-top:2px;">in {to_next}¤</div>'
            "</div>"
        )

    coll_btn = (
        f'<button onclick="window.agentAction(\'send_message\','
        "{text:'@daimon show my collection'}, this)\" "
        f'style="background:{_C_PANEL};color:{_C_TEXT};'
        f'border:1px solid {_C_PANEL_HI};border-radius:8px;'
        'padding:10px;font-size:13px;font-weight:600;'
        'cursor:pointer;width:100%;text-align:center;">'
        f'<div style="font-size:11px;color:{_C_MUTED};'
        'letter-spacing:1px;">VIEW</div>'
        '<div style="margin-top:2px;">Collection</div>'
        "</button>"
    )

    return (
        '<div style="display:grid;grid-template-columns:1fr 1fr;'
        'gap:8px;margin-bottom:12px;">'
        + pull_btn + coll_btn +
        "</div>"
    )


def _last_match_chip(recent_matches: List[Dict[str, Any]]) -> str:
    """Single-line summary of the most recent match."""
    if not recent_matches:
        return (
            f'<div style="color:{_C_MUTED};font-style:italic;">'
            "No matches yet — your first opponent awaits."
            "</div>"
        )
    last = recent_matches[0]
    outcome = (last.get("outcome") or "").lower()
    opp = last.get("opponent") or "?"

    if outcome == "win":
        color, label = _C_WIN, "WIN"
    elif outcome == "loss":
        color, label = _C_LOSS, "LOSS"
    elif outcome == "draw":
        color, label = _C_DRAW, "DRAW"
    else:
        color, label = _C_MUTED, outcome.upper() or "—"

    return (
        '<div style="display:flex;align-items:center;gap:8px;">'
        f'<span style="background:{color};color:{_C_BG};'
        'padding:2px 8px;border-radius:4px;font-size:11px;'
        f'font-weight:700;letter-spacing:0.5px;">{_esc(label)}</span>'
        f'<span style="color:{_C_TEXT};">vs {_esc(opp)}</span>'
        "</div>"
    )


def _render_stats_strip(rank: Dict[str, Any],
                        recent_matches: List[Dict[str, Any]]) -> str:
    """Two columns: lifetime W/L/D + last match."""
    wins = rank.get("wins", 0)
    losses = rank.get("losses", 0)
    draws = rank.get("draws", 0)

    return (
        f'<div style="background:{_C_PANEL};border-radius:8px;'
        'padding:10px 12px;margin-bottom:12px;display:grid;'
        'grid-template-columns:auto 1fr;gap:10px;align-items:center;">'
        # Left: WLD chips
        '<div style="display:flex;gap:6px;font-size:12px;'
        'font-weight:600;font-family:monospace;">'
        f'<span style="color:{_C_WIN};">{int(wins)}W</span>'
        f'<span style="color:{_C_LOSS};">{int(losses)}L</span>'
        f'<span style="color:{_C_DRAW};">{int(draws)}D</span>'
        "</div>"
        # Right: last match chip
        f'<div style="font-size:12px;text-align:right;">'
        + _last_match_chip(recent_matches) +
        "</div>"
        "</div>"
    )


def _render_recent_pulls(recent_pulls: List[Dict[str, Any]]) -> str:
    """Last few pulls as colored card-name chips. Hidden when empty."""
    if not recent_pulls:
        return ""
    chips: List[str] = []
    for entry in recent_pulls[:5]:
        card = entry.get("card_id") or entry.get("note") or "?"
        rarity = (entry.get("rarity") or "").upper()
        color = _RARITY_COLOR.get(rarity, _C_MUTED)
        chips.append(
            f'<span style="background:{_C_PANEL};border:1px solid {color};'
            f'color:{color};border-radius:4px;padding:2px 6px;'
            'font-size:11px;font-family:monospace;">'
            f"{_esc(card)}"
            "</span>"
        )
    return (
        f'<div style="margin-bottom:10px;">'
        f'<div style="font-size:10px;color:{_C_MUTED};'
        'letter-spacing:1px;margin-bottom:4px;">RECENT PULLS</div>'
        '<div style="display:flex;flex-wrap:wrap;gap:4px;">'
        + "".join(chips) +
        "</div>"
        "</div>"
    )


_TIER_COLOR = {
    "easy": "#67e8f9",     # cyan-300 — quick, low-effort
    "medium": _C_ACCENT,   # indigo — focused
    "hard": "#f472b6",     # pink-400 — multi-step
}


def _render_quest_row(quest: Dict[str, Any]) -> str:
    """One quest as a single-line row with progress bar + reward chip.

    Renders three states:
      * claimed → check-marked, low-opacity, "+N¤" muted
      * complete (unclaimed) → bright accent CTA chip "claim ready"
      * in-progress → standard row with fractional progress text
    """
    title = quest.get("title", "?")
    tier = quest.get("tier", "")
    reward = int(quest.get("reward", 0))
    progress = int(quest.get("progress", 0))
    target = max(int(quest.get("target", 1)), 1)
    complete = bool(quest.get("complete"))
    claimed = bool(quest.get("claimed"))

    pct = min(100, int(round(progress * 100 / target)))
    bar_color = _TIER_COLOR.get(tier, _C_ACCENT)

    if claimed:
        status_chip = (
            f'<span style="color:{_C_MUTED};font-size:11px;'
            'font-weight:600;letter-spacing:0.5px;">'
            f"+{reward}¤  ✓"
            "</span>"
        )
        title_color = _C_MUTED
        bar_color = _C_MUTED
    elif complete:
        status_chip = (
            f'<span style="background:{_C_WIN};color:{_C_BG};'
            'padding:2px 6px;border-radius:4px;font-size:11px;'
            'font-weight:700;letter-spacing:0.5px;">'
            f"+{reward}¤  CLAIM READY"
            "</span>"
        )
        title_color = _C_TEXT
    else:
        status_chip = (
            f'<span style="color:{_C_MUTED};font-size:11px;'
            'font-family:monospace;">'
            f"{progress}/{target}  ·  +{reward}¤"
            "</span>"
        )
        title_color = _C_TEXT

    return (
        f'<div style="margin-bottom:6px;">'
        '<div style="display:flex;justify-content:space-between;'
        'align-items:center;gap:8px;margin-bottom:3px;">'
        f'<span style="color:{title_color};font-size:12px;'
        'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
        f"{_esc(title)}"
        "</span>"
        f"{status_chip}"
        "</div>"
        # Progress bar — rendered even when claimed (full-width muted) so
        # rows stay vertically aligned and the eye reads the section as
        # a coherent column.
        f'<div style="background:{_C_PANEL_HI};border-radius:3px;'
        'height:4px;overflow:hidden;">'
        f'<div style="background:{bar_color};height:100%;'
        f'width:{pct}%;"></div>'
        "</div>"
        "</div>"
    )


def _render_daily_quests(quests: List[Dict[str, Any]]) -> str:
    """Daily quest panel — 3 rows, one per tier, with progress + claim chips.

    Hidden when ``quests`` is empty (fresh install before the first
    ``dm_home`` / ``dm_quests`` call has rolled the day's set).
    """
    if not quests:
        return ""

    # Compact header line: "DAILY QUESTS  ·  N/3 complete  ·  +X¤ pending".
    completed = sum(1 for q in quests if q.get("complete"))
    pending = sum(int(q.get("reward", 0))
                  for q in quests
                  if q.get("complete") and not q.get("claimed"))

    sub_bits: List[str] = [f"{completed}/{len(quests)} complete"]
    if pending:
        sub_bits.append(f"+{pending}¤ pending")
    sub_line = "  ·  ".join(sub_bits)

    rows = "".join(_render_quest_row(q) for q in quests)

    return (
        f'<div style="background:{_C_PANEL};border-radius:8px;'
        'padding:10px 12px;margin-bottom:12px;">'
        '<div style="display:flex;justify-content:space-between;'
        'align-items:baseline;margin-bottom:8px;">'
        f'<div style="font-size:10px;color:{_C_MUTED};'
        'letter-spacing:1px;">DAILY QUESTS</div>'
        f'<div style="font-size:10px;color:{_C_MUTED};">'
        f"{_esc(sub_line)}"
        "</div>"
        "</div>"
        f"{rows}"
        "</div>"
    )


def _render_loadouts(loadouts: List[Dict[str, Any]]) -> str:
    """Saved-loadouts strip with an inline 'build new' CTA when empty."""
    if not loadouts:
        return (
            f'<div style="font-size:12px;color:{_C_MUTED};'
            'font-style:italic;">'
            "No saved loadouts yet — build one with "
            f'<code style="color:{_C_ACCENT};">daimon loadout-edit</code>'
            "</div>"
        )

    chips: List[str] = []
    for lo in loadouts[:6]:
        name = lo.get("name", "?")
        cnt = lo.get("card_count", 0)
        chips.append(
            f'<span style="background:{_C_PANEL};color:{_C_TEXT};'
            f'border:1px solid {_C_PANEL_HI};border-radius:4px;'
            'padding:2px 8px;font-size:11px;font-family:monospace;">'
            f"{_esc(name)}"
            f'<span style="color:{_C_MUTED};margin-left:4px;">'
            f"({int(cnt)})</span>"
            "</span>"
        )
    return (
        f'<div>'
        f'<div style="font-size:10px;color:{_C_MUTED};'
        'letter-spacing:1px;margin-bottom:4px;">LOADOUTS</div>'
        '<div style="display:flex;flex-wrap:wrap;gap:4px;">'
        + "".join(chips) +
        "</div>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_home_card(payload: Dict[str, Any]) -> str:
    """Turn a ``dm_home`` payload into a chat-ready HTML string.

    The returned string is the *inner* HTML — the caller is responsible
    for wrapping it in ``:::html\\n…\\n:::`` and posting.

    Errors fall back gracefully:
      * ``payload['error'] == 'no_identity'`` → onboarding card
      * Missing optional sections (no recent pulls, no loadouts, etc.)
        render a one-line muted hint instead of a broken layout.
      * Any unexpected exception inside a section helper bubbles up; the
        renderer is a pure function and the caller (MCP tool) wraps it
        with try/except so the agent never sees a stack trace.
    """
    if not isinstance(payload, dict):
        # Defensive: caller violated the contract. Render a safe stub.
        return (
            f'<div style="{_bubble_style()}">'
            f'<div style="color:{_C_LOSS};">'
            "Home card unavailable — invalid payload."
            "</div>"
            "</div>"
        )

    if payload.get("error") == "no_identity":
        return _render_no_identity()

    identity = payload.get("identity") or {}
    rank = payload.get("rank") or {}
    balance = int(payload.get("balance", 0))
    pull = payload.get("pull") or {}
    recent_matches = payload.get("recent_matches") or []
    recent_pulls = payload.get("recent_pulls") or []
    recommended = payload.get("recommended_npc")
    loadouts = payload.get("saved_loadouts") or []
    daily_quests = payload.get("daily_quests") or []
    tier_ceremony = payload.get("tier_ceremony")  # may be None

    # Tier-up banner sits ABOVE the play CTA so a tier crossing is the
    # first thing the player sees on next open. CTAs that ship rewards
    # always lead — the play button is the second-most-important
    # element when a ceremony is pending.
    body = "".join([
        _render_header(identity, rank, balance, pull),
        _render_tier_ceremony(tier_ceremony),
        _render_play_cta(recommended),
        _render_secondary_actions(pull),
        _render_stats_strip(rank, recent_matches),
        _render_daily_quests(daily_quests),
        _render_recent_pulls(recent_pulls),
        _render_loadouts(loadouts),
    ])

    return (
        f'<div style="{_bubble_style(extra="border:1px solid " + _C_PANEL_HI + ";max-width:480px;")}">'
        f"{body}"
        "</div>"
    )


def render_home_card_message(payload: Dict[str, Any]) -> str:
    """Convenience wrapper: returns the full ``:::html`` fenced block.

    Use this when the caller wants to post directly via a chat reply
    tool that takes raw markdown text (the typical path).
    """
    return ":::html\n" + render_home_card(payload) + "\n:::"
