"""Phase 5 difficulty-curve simulator.

Runs N seeded matches between every pair of NPCs in the roster (designed
in tools/phase5/npc_design.py). Reports:

  1. Per-NPC win rate vs the entire field — proxy for raw power.
  2. Tier-vs-tier matrix — for each (tier_x, tier_y), avg winrate of x
     teams against y teams. We require the curve to be MONOTONIC: a
     higher-tier roster should beat any lower-tier roster on average.
  3. Inversions — flagged tier pairs where lower beats higher. These are
     the loadouts to retune.

Determinism: each (a_id, b_id, seed_idx) → fixed 32-byte seed via SHA-256
of the triplet. Same input always produces same numbers. Default seed
count = 11 per pairing (enough to suppress noise but cheap enough to run
in CI).

Usage::

    python tools/phase5/sim_tier_curve.py [--seeds 11] [--quiet]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from daimon.cards import load_card_dict           # noqa: E402
from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog  # noqa: E402
from daimon.engine import Loadout, resolve_match  # noqa: E402

from tools.phase5.npc_design import ROSTER, TIER_ORDER, TIER_RANK  # noqa: E402


def _seed(a: str, b: str, idx: int) -> bytes:
    h = hashlib.sha256(f"{a}|{b}|{idx}".encode("utf-8")).digest()
    return h


def _build_loadout(card_ids: list[str], cat) -> Loadout:
    cards = tuple(load_card_dict(dict(cat.by_id[cid].payload)) for cid in card_ids)
    return Loadout(cards=cards)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, default=11,
                    help="seeds per (a,b) pairing (default 11)")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress per-NPC + tier-matrix tables")
    ap.add_argument("--csv", type=Path, default=None,
                    help="optional CSV dump path (a_id,b_id,winrate_a,avg_hp_diff)")
    args = ap.parse_args()

    cat = load_catalog(DEFAULT_CATALOG_ID)

    # Flatten roster -> [(npc_id, tier, loadout)]
    npcs = []
    for tier in TIER_ORDER:
        for n in ROSTER[tier]:
            ld = _build_loadout(n["loadout"], cat)
            npcs.append((n["npc_id"], tier, ld))
    n_npcs = len(npcs)
    print(f"Loaded {n_npcs} NPCs across {len(TIER_ORDER)} tiers, "
          f"{args.seeds} seeds per pairing.")
    print(f"Total matches: {n_npcs * (n_npcs - 1) * args.seeds}")

    t0 = time.time()
    # winrate[a_id][b_id] = float in [0,1]; ties = 0.5
    winrate: dict[str, dict[str, float]] = defaultdict(dict)
    hp_diff: dict[str, dict[str, float]] = defaultdict(dict)
    for a_id, _, a_ld in npcs:
        for b_id, _, b_ld in npcs:
            if a_id == b_id:
                continue
            wins = 0.0
            hpd = 0.0
            for s in range(args.seeds):
                seed = _seed(a_id, b_id, s)
                r = resolve_match(a_ld, b_ld, seed)
                if r.winner == 0:
                    wins += 1.0
                elif r.winner is None:
                    wins += 0.5
                hpd += r.side_a_final_hp - r.side_b_final_hp
            winrate[a_id][b_id] = wins / args.seeds
            hp_diff[a_id][b_id] = hpd / args.seeds
    dt = time.time() - t0
    print(f"Sim done in {dt:.1f}s")

    # ---------------- Per-NPC field winrate ----------------
    field_wr = {a_id: mean(winrate[a_id].values()) for a_id, _, _ in npcs}
    if not args.quiet:
        print("\n=== PER-NPC FIELD WINRATE (vs all 24 other NPCs) ===")
        for tier in TIER_ORDER:
            ids = [n["npc_id"] for n in ROSTER[tier]]
            ids_sorted = sorted(ids, key=lambda x: -field_wr[x])
            for nid in ids_sorted:
                print(f"  [{tier:8s}] {nid:25s} field_wr={field_wr[nid]*100:5.1f}%")

    # ---------------- Tier-vs-tier matrix ----------------
    tier_matrix: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for a_id, a_t, _ in npcs:
        for b_id, b_t, _ in npcs:
            if a_id == b_id:
                continue
            tier_matrix[a_t][b_t].append(winrate[a_id][b_id])
    tier_avg = {a: {b: mean(v) for b, v in row.items()} for a, row in tier_matrix.items()}

    if not args.quiet:
        print("\n=== TIER-VS-TIER WINRATE MATRIX (row vs col) ===")
        header = "          " + " ".join(f"{t:>10s}" for t in TIER_ORDER)
        print(header)
        for a in TIER_ORDER:
            row = " ".join(f"{tier_avg[a].get(b, float('nan'))*100:9.1f}%"
                           for b in TIER_ORDER)
            print(f"  {a:8s} {row}")

    # ---------------- Difficulty curve check ----------------
    print("\n=== DIFFICULTY CURVE CHECK ===")
    inversions = []
    for a in TIER_ORDER:
        for b in TIER_ORDER:
            if a == b:
                continue
            if TIER_RANK[a] > TIER_RANK[b]:
                # higher tier (a) should beat lower tier (b) on average.
                wr = tier_avg[a].get(b, 0.5)
                if wr < 0.55:
                    inversions.append((a, b, wr))

    if inversions:
        print(f"  {len(inversions)} inversions (higher tier failed to win >=55%):")
        for a, b, wr in inversions:
            print(f"    INVERSION: {a:8s} vs {b:8s}  wr={wr*100:5.1f}%")
        return 1
    print("  PASS — every higher tier beats every lower tier with >=55% winrate.")

    if args.csv:
        import csv
        with args.csv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["a_id", "b_id", "winrate_a", "avg_hp_diff_a"])
            for a_id, _, _ in npcs:
                for b_id, _, _ in npcs:
                    if a_id == b_id:
                        continue
                    w.writerow([a_id, b_id,
                                round(winrate[a_id][b_id], 4),
                                round(hp_diff[a_id][b_id], 2)])
        print(f"  CSV dumped to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
