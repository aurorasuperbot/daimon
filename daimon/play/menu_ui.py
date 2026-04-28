"""Main-screen TUI for ``daimon menu`` — built on the unified daimon.ui framework.

This is the player's home dashboard: identity strip, hero CTA panel, five
focusable action cards (Pull / Match / Loadouts / Collection / Shop), then
a quests + activity bottom row. Arrow keys + Enter or mouse click are the
primary navigation; letter hotkeys (P/M/L/C/S) are accelerators.

Auto-refreshes every few seconds on a worker thread so dm_home()'s ledger
reads (~1.6s) never freeze the UI. Hotkeys/cards suspend the GameApp and
run the matching ``daimon <cmd>`` subprocess in-place, then redraw.

The framework lives in :mod:`daimon.ui` — Frame/Widget/Layout/Screen/GameApp.
This file only contains the *home screen's* state, layout, and actions.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from daimon.ui import (
    BindingTable,
    Button,
    Frame,
    GameApp,
    HBox,
    HitRegion,
    Pad,
    Panel,
    ProgressBar,
    Screen,
    Static,
    VBox,
    Widget,
    button_row,
)


REFRESH_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Data acquisition — wraps dm_home() with a defensive fallback so the screen
# can still mount when the catalog/manifest isn't loaded yet (fresh onboard).
# ---------------------------------------------------------------------------


def _safe_load_home() -> Dict[str, Any]:
    """Call dm_home(); on any error, return a degraded payload the UI can render."""
    try:
        from daimon.mcp.server import dm_home
        return dm_home()
    except Exception as e:  # noqa: BLE001 — keep the screen alive
        return {
            "status": "degraded",
            "error": f"dm_home() failed: {e}",
            "identity": {"pubkey_hex": "(unavailable)", "handle": None,
                         "registered": False, "version": "?"},
            "balance": 0,
            "pull": {"cost": 100, "pulls_available": 0,
                     "balance_to_next_pull": 100},
            "stats": {},
            "rank": {"tier": "?", "wins": 0, "losses": 0,
                     "note": "rank unavailable"},
            "recent_matches": [],
            "recent_pulls": [],
            "recommended_npc": None,
            "saved_loadouts": [],
            "daily_quests": [],
            "onboarding": None,
        }


def _resolve_daimon_argv(args: List[str]) -> List[str]:
    """Build an argv that invokes the daimon CLI without relying on PATH."""
    from daimon.render.wezterm_bundle import daimon_invocation
    return daimon_invocation() + args


# ---------------------------------------------------------------------------
# Small formatters
# ---------------------------------------------------------------------------


def _short_pubkey(pubkey: str) -> str:
    if not pubkey or len(pubkey) < 12:
        return pubkey or "(none)"
    return f"{pubkey[:8]}…{pubkey[-4:]}"


def _format_relative(ts: Optional[str]) -> str:
    if not ts:
        return ""
    try:
        cleaned = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
    except (ValueError, TypeError):
        return ts
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _quest_status(quest: Dict[str, Any]) -> Tuple[str, str]:
    if quest.get("claimed"):
        return ("✓", "green")
    if quest.get("complete"):
        return ("★", "yellow")
    return ("·", "grey50")


def _outcome_label(outcome: Optional[str]) -> Tuple[str, str]:
    o = (outcome or "").lower()
    if o == "win":
        return ("WON ", "green")
    if o == "loss":
        return ("LOST", "red")
    if o == "draw":
        return ("DREW", "yellow")
    return ("PLAYED", "grey50")


def _card_count() -> int:
    try:
        from daimon.collection import count as _count
        return _count()
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Card model — ordered tuple drives both layout AND focus navigation.
# ---------------------------------------------------------------------------


_CARDS: Tuple[Tuple[str, str, str, str], ...] = (
    # (action, icon, label, hotkey)
    ("pull",       "◆", "PULL",       "P"),
    ("match",      "⚔", "MATCH",      "M"),
    ("loadouts",   "▤", "LOADOUTS",   "L"),
    ("collection", "□", "COLLECTION", "C"),
    ("shop",       "⛀", "SHOP",       "S"),
)


_HOTKEY_TO_INDEX = {hk.lower(): i for i, (_, _, _, hk) in enumerate(_CARDS)}
_ACTION_TO_INDEX = {a: i for i, (a, _, _, _) in enumerate(_CARDS)}


# ---------------------------------------------------------------------------
# HomeMenu — the Screen subclass
# ---------------------------------------------------------------------------


class HomeMenu(Screen):
    """Player home — identity · CTA · 5 cards · currency / quests / activity."""

    bindings = BindingTable({
        # Letter accelerators (visible in footer).
        "p": ("act_pull",       "Pull"),
        "m": ("act_match",      "Match"),
        "l": ("act_loadouts",   "Loadouts"),
        "c": ("act_collection", "Collection"),
        "s": ("act_shop",       "Shop"),
        "q,esc": ("quit",       "Quit"),
        # Game-style nav (hidden — players see the focused card).
        "right,tab,down":     ("focus_next", "", False),
        "left,shift+tab,up":  ("focus_prev", "", False),
        "enter,space":        ("activate",   "", False),
        # Utilities (hidden footer chips).
        "r": ("refresh",    "", False),
        "d": ("act_doctor", "", False),
    })

    def __init__(self,
                 *,
                 home_loader: Callable[[], Dict[str, Any]] = _safe_load_home,
                 width: int = 150,
                 height: int = 42,
                 sink: Optional[object] = None,
                 ) -> None:
        super().__init__(width=width, height=height, sink=sink)
        self._home_loader = home_loader
        self._home_data: Dict[str, Any] = self._initial_home_payload()
        self._home_lock = threading.Lock()
        self._loader_thread: Optional[threading.Thread] = None
        self._next_refresh_at = 0.0
        self._focus_index = 0
        self._notice: Optional[str] = None
        self._notice_until = 0.0
        # Cache the count once at mount; refreshed on each subprocess return.
        self._cached_card_count = 0

    @staticmethod
    def _initial_home_payload() -> Dict[str, Any]:
        return {
            "status": "loading",
            "identity": {"pubkey_hex": "", "handle": None, "registered": False},
            "balance": 0,
            "pull": {"cost": 100, "pulls_available": 0,
                     "balance_to_next_pull": 100},
            "stats": {},
            "rank": {"tier": "—", "wins": 0, "losses": 0},
            "recent_matches": [],
            "recent_pulls": [],
            "recommended_npc": None,
            "saved_loadouts": [],
            "daily_quests": [],
            "onboarding": None,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._cached_card_count = _card_count()
        self._kick_refresh()

    def on_tick(self) -> None:
        # Expire the transient notice.
        if self._notice and time.monotonic() >= self._notice_until:
            self._notice = None
            self.refresh()
        # Auto-refresh on cadence (only if no loader is in-flight).
        now = time.monotonic()
        if now >= self._next_refresh_at and not self._loader_in_flight():
            self._kick_refresh()

    def _loader_in_flight(self) -> bool:
        return self._loader_thread is not None and self._loader_thread.is_alive()

    def _kick_refresh(self) -> None:
        self._next_refresh_at = time.monotonic() + REFRESH_SECONDS
        thread = threading.Thread(
            target=self._refresh_worker, name="daimon-menu-loader", daemon=True
        )
        self._loader_thread = thread
        thread.start()

    def _refresh_worker(self) -> None:
        try:
            data = self._home_loader()
        except Exception:  # noqa: BLE001
            data = self._initial_home_payload()
            data["status"] = "degraded"
        with self._home_lock:
            self._home_data = data
        # Trigger a redraw on the main thread by invalidating the signature.
        self.refresh()

    def notify(self, message: str) -> None:
        self._notice = message
        self._notice_until = time.monotonic() + 1.5
        self.refresh()

    # ------------------------------------------------------------------
    # Signature — drives render dedupe
    # ------------------------------------------------------------------

    def signature(self) -> Any:
        with self._home_lock:
            data = self._home_data
        ident = data.get("identity") or {}
        rank = data.get("rank") or {}
        pull = data.get("pull") or {}
        rec = data.get("recommended_npc") or {}
        ob = data.get("onboarding") or {}
        quests = tuple(
            (q.get("quest_id"), q.get("progress"),
             q.get("complete"), q.get("claimed"))
            for q in (data.get("daily_quests") or [])
        )
        matches = tuple(
            (m.get("ts"), m.get("outcome"), m.get("opponent"))
            for m in (data.get("recent_matches") or [])[:5]
        )
        pulls = tuple(
            (p.get("ts"), p.get("rarity"), p.get("card_id"))
            for p in (data.get("recent_pulls") or [])[:5]
        )
        return (
            self.width, self.height, self._focus_index,
            data.get("status"),
            ident.get("pubkey_hex"), ident.get("handle"),
            rank.get("tier"), rank.get("wins"), rank.get("losses"),
            data.get("balance"),
            pull.get("cost"), pull.get("pulls_available"),
            pull.get("balance_to_next_pull"),
            rec.get("name"), rec.get("tier"),
            ob.get("title"), ob.get("step"), ob.get("cta_message"),
            len(data.get("saved_loadouts") or []),
            self._cached_card_count,
            quests, matches, pulls,
            self._notice,
        )

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> Widget:
        with self._home_lock:
            data = self._home_data

        outer_pad_h = 4
        inner_w = max(0, self.width - 2 * outer_pad_h)

        # Reserve fixed heights for everything except the bottom row,
        # which gets the remaining space.
        title_h = 3
        hero_h = 9
        cards_h = 9
        footer_h = 2
        gap = 1
        fixed = title_h + hero_h + cards_h + footer_h + gap * 4
        bottom_h = max(8, self.height - fixed)

        title = self._build_title_bar(data, inner_w)
        hero = self._build_hero(data, inner_w)
        cards = self._build_action_row(data, inner_w)
        bottom = self._build_bottom_row(data, inner_w, bottom_h)
        footer = self._build_footer(inner_w)

        column = VBox(
            [
                (title, 0),     # weights ignored in fixed layout below
                (hero, 0),
                (cards, 0),
                (bottom, 1),
                (footer, 0),
            ],
            gap=gap,
        )

        # We need exact heights for the fixed sections — wrap each in a
        # FixedHeight wrapper instead of relying on weight math which would
        # share remaining space proportionally. Cleanest: compose a custom
        # layout via FixedRows.
        return Pad(
            _FixedRows(
                rows=[
                    (title, title_h),
                    (hero, hero_h),
                    (cards, cards_h),
                    (bottom, bottom_h),
                    (footer, footer_h),
                ],
                gap=gap,
            ),
            top=0, right=outer_pad_h, bottom=0, left=outer_pad_h,
        )

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_title_bar(self, data: Dict[str, Any], width: int) -> Widget:
        ident = data.get("identity") or {}
        rank = data.get("rank") or {}
        balance = data.get("balance", 0)
        pubkey_short = _short_pubkey(ident.get("pubkey_hex") or "")
        handle = ident.get("handle")
        tier = rank.get("tier") or "—"
        wins = rank.get("wins", 0)
        losses = rank.get("losses", 0)

        if handle:
            left = (f"[bold]DAIMON[/bold]   [white]{handle}[/white]   "
                    f"[grey50]{pubkey_short}[/grey50]")
        else:
            left = (f"[bold]DAIMON[/bold]   [grey50]{pubkey_short}[/grey50]   "
                    f"[grey50](unregistered)[/grey50]")

        right = (
            f"[bold yellow]{tier}[/bold yellow] "
            f"[grey50]({wins}W · {losses}L)[/grey50]   "
            f"[bold yellow]¤ {balance}[/bold yellow]"
        )

        bar = HBox(
            [
                (Static(left, align="left", valign="middle"), 2),
                (Static(right, align="right", valign="middle"), 1),
            ],
            gap=2,
        )
        return Panel(
            Pad(bar, left=1, right=1),
            border_style="heavy",
            border_color="blue",
            padding_h=0,
            padding_v=0,
        )

    def _build_hero(self, data: Dict[str, Any], width: int) -> Widget:
        ob = data.get("onboarding") or {}
        if ob.get("title"):
            title = ob.get("title", "")
            blurb = ob.get("blurb", "")
            step = ob.get("step")
            total = ob.get("total")
            chip = (
                f"[grey50]STEP {step} OF {total}[/grey50]"
                if step is not None and total is not None else ""
            )
            hint = self._cta_to_hint(ob.get("cta_message") or "")
            body = (
                (chip + "\n\n" if chip else "")
                + f"[bold yellow]▶  {title.upper()}[/bold yellow]\n"
                + blurb
                + (f"\n\n[grey50]{hint}[/grey50]" if hint else "")
            )
        else:
            rec = data.get("recommended_npc")
            if rec:
                body = (
                    "[grey50]NEXT MATCH[/grey50]\n\n"
                    f"[bold yellow]▶  CHALLENGE "
                    f"{rec.get('name', '?').upper()}[/bold yellow]\n"
                    f"{rec.get('flavor', '')}  "
                    f"[grey50]({rec.get('tier', '?')})[/grey50]\n\n"
                    "[grey50]click MATCH or press M[/grey50]"
                )
            else:
                body = (
                    "[bold yellow]▶  YOU'RE ALL CAUGHT UP[/bold yellow]\n\n"
                    "Mine more currency, pull more cards, "
                    "or challenge another agent."
                )

        return Panel(
            Static(body, align="center", valign="middle"),
            border_style="heavy",
            border_color="yellow",
            padding_h=2,
            padding_v=0,
        )

    def _cta_to_hint(self, cta_message: str) -> str:
        msg = cta_message.lower()
        if "pull" in msg:
            return "click PULL or press P"
        if "match" in msg or "fight" in msg or "challenge" in msg:
            return "click MATCH or press M"
        if "loadout" in msg:
            return "click LOADOUTS or press L"
        if "shop" in msg:
            return "click SHOP or press S"
        if "collection" in msg:
            return "click COLLECTION or press C"
        return ""

    def _build_action_row(self, data: Dict[str, Any], width: int) -> Widget:
        balance = data.get("balance", 0)
        pull = data.get("pull") or {}
        cost = pull.get("cost", 100)
        pulls_avail = pull.get("pulls_available", 0)
        rec = data.get("recommended_npc")
        loadouts = data.get("saved_loadouts") or []

        # Highlight whichever card the hero panel is pointing at.
        ob = data.get("onboarding") or {}
        cta_msg = (ob.get("cta_message") or "").lower()
        suggested = None
        if "pull" in cta_msg:
            suggested = "pull"
        elif "match" in cta_msg or "fight" in cta_msg:
            suggested = "match"

        details = {
            "pull": (
                f"[green]{pulls_avail} ready[/green]"
                if pulls_avail > 0 else
                f"[grey50]{cost}¤ each[/grey50]"
            ),
            "match": (
                f"vs {rec.get('name', '?')}" if rec
                else "[grey50]find one[/grey50]"
            ),
            "loadouts": (
                f"{len(loadouts)} saved" if loadouts
                else "[grey50]none yet[/grey50]"
            ),
            "collection": (
                f"{self._cached_card_count} card"
                f"{'s' if self._cached_card_count != 1 else ''}"
                if self._cached_card_count else "[grey50]empty[/grey50]"
            ),
            "shop": "daily skins",
        }

        buttons: List[Button] = []
        for i, (action, icon, label, hotkey) in enumerate(_CARDS):
            buttons.append(Button(
                action=f"card_{action}",
                icon=icon,
                label=label,
                hotkey=hotkey,
                detail=details.get(action, ""),
                focused=(i == self._focus_index),
                highlighted=(action == suggested and i != self._focus_index),
                accent_color="yellow",
                dim_color="grey50",
                id=f"card-{action}",
            ))
        return button_row(buttons, gap=1)

    def _build_bottom_row(self,
                          data: Dict[str, Any],
                          width: int,
                          height: int) -> Widget:
        currency_panel = self._build_currency_panel(data)
        quests_panel = self._build_quests_panel(data)
        activity_panel = self._build_activity_panel(data)
        return HBox(
            [
                (currency_panel, 1),
                (quests_panel, 2),
                (activity_panel, 2),
            ],
            gap=1,
        )

    def _build_currency_panel(self, data: Dict[str, Any]) -> Widget:
        balance = data.get("balance", 0)
        pull = data.get("pull") or {}
        stats = data.get("stats") or {}
        cost = pull.get("cost", 100)
        pulls_avail = pull.get("pulls_available", 0)
        to_next = pull.get("balance_to_next_pull", cost)

        if pulls_avail > 0:
            ready_line = (
                f"[bold green]{pulls_avail} pull"
                f"{'s' if pulls_avail != 1 else ''} ready[/bold green]"
            )
        else:
            ready_line = f"[grey50]{to_next}¤ to next pull[/grey50]"

        earned = stats.get("total_mined", 0)
        spent = stats.get("total_pulled", 0) + stats.get("total_purchased", 0)
        progress = balance % cost if balance > 0 else 0

        body_text = (
            f"[bold yellow]¤ {balance:>5}[/bold yellow]\n"
            f"{ready_line}\n\n"
            f"[grey50]earned[/grey50]  [white]{earned:>6}[/white]\n"
            f"[grey50]spent [/grey50]  [white]{spent:>6}[/white]"
        )
        body = VBox(
            [
                (Static(body_text, align="left", valign="top"), 1),
                (ProgressBar(progress=progress, total=cost,
                             fill_color="yellow", dim_color="grey39"), 0),
            ],
            gap=1,
        )
        return Panel(
            body,
            title="CURRENCY",
            border_style="round",
            border_color="yellow",
            padding_h=1,
            padding_v=0,
        )

    def _build_quests_panel(self, data: Dict[str, Any]) -> Widget:
        quests = data.get("daily_quests") or []
        if not quests:
            inner = Static(
                "[grey50](no quests today)[/grey50]",
                align="left", valign="top",
            )
        else:
            lines: List[str] = []
            for q in quests:
                glyph, color = _quest_status(q)
                tier = (q.get("tier") or "").upper()
                title = q.get("title", "")
                progress = q.get("progress", 0)
                target = q.get("target", 0)
                reward = q.get("reward", 0)
                progress_str = (
                    f"[green]done[/green]" if q.get("claimed")
                    else f"[grey50]{progress:>3}/{target:<3}[/grey50]"
                )
                tier_str = f"[grey50]{tier:<6}[/grey50] " if tier else ""
                lines.append(
                    f"[{color}]{glyph}[/{color}]  {tier_str}{title}"
                    f"   {progress_str}   [yellow]{reward}¤[/yellow]"
                )
            inner = Static("\n".join(lines), align="left", valign="top")
        return Panel(
            inner,
            title="DAILY QUESTS",
            border_style="round",
            border_color="green",
            padding_h=1,
            padding_v=0,
        )

    def _build_activity_panel(self, data: Dict[str, Any]) -> Widget:
        matches = data.get("recent_matches") or []
        pulls = data.get("recent_pulls") or []

        lines: List[str] = []
        for m in matches[:4]:
            verb, color = _outcome_label(m.get("outcome"))
            opp = m.get("opponent") or m.get("note") or "?"
            ago = _format_relative(m.get("ts"))
            lines.append(
                f"[{color}]{verb}[/{color}]  vs {opp}"
                f"   [grey50]{ago}[/grey50]"
            )
        if pulls:
            if matches:
                lines.append("")
            for p in pulls[:4]:
                rarity = (p.get("rarity") or "").upper()
                card = p.get("card_id") or p.get("card") or "?"
                ago = _format_relative(p.get("ts"))
                rarity_color = {
                    "LEGENDARY": "magenta", "EPIC": "cyan", "RARE": "blue",
                    "UNCOMMON": "green", "COMMON": "white",
                }.get(rarity, "white")
                lines.append(
                    f"[{rarity_color}]PULLED {rarity}[/{rarity_color}]  "
                    f"{card}   [grey50]{ago}[/grey50]"
                )

        if not lines:
            lines = [
                "[grey50]nothing yet — your first match[/grey50]",
                "[grey50]or pull lands here.[/grey50]",
                "",
                "[grey50]hint: every Claude Code tool[/grey50]",
                "[grey50]call earns a few ¤. The game[/grey50]",
                "[grey50]ticks live in the background[/grey50]",
                "[grey50]while you work.[/grey50]",
            ]

        return Panel(
            Static("\n".join(lines), align="left", valign="top"),
            title="RECENT ACTIVITY",
            border_style="round",
            border_color="cyan",
            padding_h=1,
            padding_v=0,
        )

    def _build_footer(self, width: int) -> Widget:
        chips = []
        for binding in self.bindings.visible():
            label = binding.description or binding.action
            key = binding.keys[0].upper()
            chips.append(f"[bold yellow]{key}[/bold yellow] [grey50]{label}[/grey50]")
        bar_left = "  ".join(chips)
        bar_right = ""
        if self._notice:
            bar_right = f"[bold yellow]{self._notice}[/bold yellow]"
        elif self._loader_in_flight():
            bar_right = "[grey50]refreshing…[/grey50]"
        else:
            bar_right = (
                "[grey50]← →[/grey50] move  "
                "[grey50]ENTER[/grey50] activate  "
                "[grey50]click[/grey50] any card"
            )
        return HBox(
            [
                (Static(bar_left, align="left", valign="middle"), 2),
                (Static(bar_right, align="right", valign="middle"), 1),
            ],
            gap=2,
        )

    # ------------------------------------------------------------------
    # Action routing
    # ------------------------------------------------------------------

    def on_action(self,
                  name: str,
                  *,
                  source: Optional[HitRegion] = None) -> None:
        # Card click — translate to act_<name> and update focus.
        if name.startswith("card_"):
            action = name[len("card_"):]
            idx = _ACTION_TO_INDEX.get(action)
            if idx is not None:
                self._focus_index = idx
            self._dispatch_action(action)
            return

        if name == "focus_next":
            self._focus_index = (self._focus_index + 1) % len(_CARDS)
            self.refresh()
            return
        if name == "focus_prev":
            self._focus_index = (self._focus_index - 1) % len(_CARDS)
            self.refresh()
            return
        if name == "activate":
            action = _CARDS[self._focus_index][0]
            self._dispatch_action(action)
            return
        if name == "refresh":
            self._kick_refresh()
            self.notify("refreshing…")
            return
        if name == "act_doctor":
            self._run_subcommand(["doctor"])
            return
        if name.startswith("act_"):
            action = name[len("act_"):]
            idx = _ACTION_TO_INDEX.get(action)
            if idx is not None:
                self._focus_index = idx
            self._dispatch_action(action)
            return

        # Fallback — delegate to base (handles "quit").
        super().on_action(name, source=source)

    def _dispatch_action(self, action: str) -> None:
        argv = {
            "pull":       ["pull"],
            "match":      ["npcs"],
            "loadouts":   ["loadout", "list"],
            "collection": ["collection"],
            "shop":       ["shop"],
        }.get(action)
        if argv is None:
            return
        self._run_subcommand(argv)

    def _run_subcommand(self, args: List[str]) -> None:
        argv = _resolve_daimon_argv(args)
        if self._app is None:
            # No GameApp wired (degenerate / test) — just attempt to run.
            try:
                subprocess.run(argv, check=False)
            except OSError as e:
                print(f"[daimon menu] failed to run {' '.join(argv)}: {e}",
                      file=sys.stderr)
            return
        with self._app.suspend():
            try:
                subprocess.run(argv, check=False)
            except OSError as e:
                print(f"\n[daimon menu] failed to run {' '.join(argv)}: {e}",
                      file=sys.stderr)
            try:
                input("\n[press Enter to return to menu]")
            except (EOFError, KeyboardInterrupt):
                pass
        # Card count and ledger may have changed — refresh both.
        self._cached_card_count = _card_count()
        self._kick_refresh()


# ---------------------------------------------------------------------------
# _FixedRows — VBox-style stack with explicit per-child heights.
#
# VBox does flex weights; we want exact heights for the title/hero/cards/
# footer with all the leftover going to the bottom row. Building a small
# dedicated layout keeps the menu's compose() readable.
# ---------------------------------------------------------------------------


class _FixedRows(Widget):

    def __init__(self,
                 rows: List[Tuple[Widget, int]],
                 *,
                 gap: int = 0,
                 id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self._rows = rows
        self._gap = max(0, gap)

    def render(self, width: int, height: int) -> Frame:
        from daimon.ui.layout import _coerce_size

        composed_rows: List[str] = []
        overlays: List[Any] = []
        hits: List[HitRegion] = []
        row_offset = 0
        blank_row = " " * width

        for i, (widget, requested_h) in enumerate(self._rows):
            remaining = max(0, height - row_offset)
            if remaining <= 0:
                break
            allocated_h = min(requested_h, remaining)
            child_frame = widget.render(width, allocated_h)
            child_frame = _coerce_size(child_frame, width, allocated_h)
            shifted = child_frame.translated(row_offset, 0)
            overlays.extend(shifted.overlays)
            hits.extend(shifted.hit_regions)
            composed_rows.extend(child_frame.rows)
            row_offset += allocated_h
            if i < len(self._rows) - 1 and self._gap:
                gap_h = min(self._gap, max(0, height - row_offset))
                for _ in range(gap_h):
                    composed_rows.append(blank_row)
                row_offset += gap_h

        # Pad with blanks to exact height.
        while len(composed_rows) < height:
            composed_rows.append(blank_row)
        if len(composed_rows) > height:
            composed_rows = composed_rows[:height]

        return Frame(
            rows=tuple(composed_rows),
            width=width,
            height=height,
            overlays=tuple(overlays),
            hit_regions=tuple(hits),
        )


# ---------------------------------------------------------------------------
# Entry point used by ``daimon menu``
# ---------------------------------------------------------------------------


def run_menu() -> None:
    screen = HomeMenu()
    app = GameApp(screen, tick_ms=50, enable_mouse=True, enable_alt_buffer=True)
    app.run()
