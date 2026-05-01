"""Per-serial battle stats — the Imprint system.

Each card serial accumulates a biography through play: wins, losses, kills,
damage, streaks, and trophy marks. Stats are recorded incrementally after
each match (PvE or PvP) and stored in a local JSON file keyed by serial UUID.

Storage: ~/.config/daimon/imprint_stats.json
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from daimon.identity.keys import CONFIG_DIR

if TYPE_CHECKING:
    from daimon.engine.types import MatchResult
    from daimon.engine.loadout import Loadout

IMPRINT_STATS_PATH = CONFIG_DIR / "imprint_stats.json"

TEAM_SIZE = 6


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_imprint_stats(path: Optional[Path] = None) -> Dict[str, Any]:
    if path is None:
        path = IMPRINT_STATS_PATH
    if not path.exists():
        return {"version": 1, "serials": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"corrupt imprint stats at {path}: {e}") from e
    data.setdefault("version", 1)
    data.setdefault("serials", {})
    return data


def save_imprint_stats(data: Dict[str, Any],
                       path: Optional[Path] = None) -> None:
    if path is None:
        path = IMPRINT_STATS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_serial_stats(serial: str,
                     path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    return load_imprint_stats(path)["serials"].get(serial)


# ---------------------------------------------------------------------------
# Stat recording
# ---------------------------------------------------------------------------

def _blank_stats(card_id: str) -> Dict[str, Any]:
    return {
        "card_id": card_id,
        "wins": 0,
        "losses": 0,
        "kills": 0,
        "damage_dealt": 0,
        "damage_taken": 0,
        "matches_played": 0,
        "last_match_at": None,
        "streak": 0,
        "best_streak": 0,
        "trophies": [],
    }


def record_match(
    serial: str,
    card_id: str,
    won: bool,
    kills: int = 0,
    damage_dealt: int = 0,
    damage_taken: int = 0,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Incrementally update one serial's stats after a match.

    Returns the updated stats dict for that serial.
    """
    data = load_imprint_stats(path)
    entry = data["serials"].get(serial)
    if entry is None:
        entry = _blank_stats(card_id)
        data["serials"][serial] = entry

    entry["matches_played"] += 1
    entry["kills"] += kills
    entry["damage_dealt"] += damage_dealt
    entry["damage_taken"] += damage_taken
    entry["last_match_at"] = _now_iso()

    if won:
        entry["wins"] += 1
        entry["streak"] += 1
        if entry["streak"] > entry["best_streak"]:
            entry["best_streak"] = entry["streak"]
    else:
        entry["losses"] += 1
        entry["streak"] = 0

    entry["trophies"] = compute_trophies(entry)
    save_imprint_stats(data, path)
    return entry


# ---------------------------------------------------------------------------
# Trophy computation
# ---------------------------------------------------------------------------

def compute_trophies(stats: Dict[str, Any]) -> List[str]:
    """Derive trophy marks from a serial's stats. Pure function."""
    trophies: List[str] = []

    wins = stats.get("wins", 0)
    kills = stats.get("kills", 0)
    best_streak = stats.get("best_streak", 0)

    if wins >= 100:
        trophies.append("centurion")
    if wins >= 10:
        trophies.append("veteran")
    if kills >= 100:
        trophies.append("slayer")
    if best_streak >= 25:
        trophies.append("undefeated_25")
    elif best_streak >= 10:
        trophies.append("undefeated_10")
    elif best_streak >= 5:
        trophies.append("undefeated_5")

    return trophies


# ---------------------------------------------------------------------------
# Serial resolution for loadouts
# ---------------------------------------------------------------------------

def resolve_serials_for_loadout(
    card_ids: List[str],
    serials: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Map each card_id in a loadout to the oldest owned serial UUID.

    Args:
        card_ids: The card_ids in the loadout (up to 6).
        serials: The full serial list from collection.json.

    Returns:
        Dict mapping card_id → serial UUID. Cards with no matching serial
        are omitted.
    """
    by_card: Dict[str, List[Dict[str, Any]]] = {}
    for s in serials:
        cid = s.get("card_id", "")
        by_card.setdefault(cid, []).append(s)

    for group in by_card.values():
        group.sort(key=lambda s: s.get("minted_at", ""))

    result: Dict[str, str] = {}
    used: set[str] = set()
    for cid in card_ids:
        for s in by_card.get(cid, []):
            if s["serial"] not in used:
                result[cid] = s["serial"]
                used.add(s["serial"])
                break
    return result


# ---------------------------------------------------------------------------
# Per-card stat extraction from engine result
# ---------------------------------------------------------------------------

def extract_per_card_stats(
    result: "MatchResult",
    loadout: "Loadout",
    side: int = 0,
) -> List[Dict[str, Any]]:
    """Walk engine round events and tally per-position stats for one side.

    Returns a list of dicts (one per loadout position 0..5):
        {"position": int, "card_id": str, "kills": int,
         "damage_dealt": int, "damage_taken": int, "survived": bool}
    """
    stats = []
    for pos in range(min(TEAM_SIZE, len(loadout.cards))):
        stats.append({
            "position": pos,
            "card_id": loadout.cards[pos].card_id,
            "kills": 0,
            "damage_dealt": 0,
            "damage_taken": 0,
            "survived": True,
        })

    def _walk_events(events: list) -> None:
        for ev in events:
            kind = ev.kind

            if kind == "damage" and ev.amount is not None:
                if ev.actor_side == side:
                    idx = ev.actor_position
                    if 0 <= idx < len(stats):
                        stats[idx]["damage_dealt"] += ev.amount
                if ev.target_side == side and ev.target_position is not None:
                    idx = ev.target_position
                    if 0 <= idx < len(stats):
                        stats[idx]["damage_taken"] += ev.amount

            elif kind == "death":
                dead_side = ev.actor_side
                dead_pos = ev.actor_position
                if dead_side == side and 0 <= dead_pos < len(stats):
                    stats[dead_pos]["survived"] = False
                elif dead_side != side:
                    pass

            if ev.triggers:
                _walk_events(ev.triggers)

    def _walk_for_kills(events: list) -> None:
        """Attribute kills: a death in triggers means the parent actor
        scored the kill."""
        for ev in events:
            if ev.kind == "damage" and ev.actor_side == side:
                for sub in ev.triggers:
                    if sub.kind == "death" and sub.actor_side != side:
                        idx = ev.actor_position
                        if 0 <= idx < len(stats):
                            stats[idx]["kills"] += 1
            if ev.triggers:
                _walk_for_kills(ev.triggers)

    for rnd in result.rounds:
        _walk_events(rnd.events)
        _walk_for_kills(rnd.events)

    return stats
