"""Animation demo — synthesizes a hand-crafted Match that exercises every
primitive in the V1 vocabulary, then plays it through the spectator HUD.

Wired to ``daimon play-demo`` (see ``cli.py``). Used for:
  - Acceptance criterion #4 from docs/animation_design.md ("daimon play demo
    showcases every primitive in under 30 seconds").
  - Quick visual smoke test after primitive-vocabulary changes.
  - Sales/onboarding screen for new players: "this is what combat looks like".

The demo match is NOT a real engine output — it is a fixture built in code
that maximises primitive coverage:

  Round 1:
    A1  BIG damage  (15 dmg)   opp[0] → player[2]    intent + flash + line +
                                                     overlay + hp_tick + shake +
                                                     hit_pause + glow
        ┗ trigger ON_DAMAGE  player[2] → opp[0]      zap (cascade)
    A2  BUFF        (+2 atk)   player[1] → self      pulse + flash + overlay
    A3  HEAL        (+5 hp)    opp[4]   → opp[0]     pulse + flash + overlay
    A4  SHIELD      (+3 def)   player[0] → self      pulse + flash + overlay
    A5  KO          (lethal)   opp[2]  → player[3]   shake + hit_pause + KO cue
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Optional

from daimon.play.hud.playback import (
    BASE_TICK_MS,
    MatchPlayback,
    PlaybackStatus,
)
from daimon.play.hud.render import render_frame
from daimon.play.schema import (
    Action,
    ActionKind,
    CardRef,
    Match,
    MatchStats,
    Outcome,
    Participant,
    Rewards,
    Round,
    Side,
)


def build_demo_match() -> Match:
    """Construct a hand-crafted Match exercising every V1 primitive."""
    player = Participant(
        name="demo",
        rank="Demo",
        loadout=[
            {"position": 0, "species": "embercat",      "element": "fire",   "name": "Embercat",      "short_name": "Ember", "hp_max": 14, "hp": 14, "rarity": "uncommon"},
            {"position": 1, "species": "iron_boar",     "element": "nature", "name": "Iron Boar",     "short_name": "Boar",  "hp_max": 16, "hp": 16, "rarity": "rare"},
            {"position": 2, "species": "tide_serpent",  "element": "water",  "name": "Tide Serpent",  "short_name": "Tide",  "hp_max": 18, "hp": 18, "rarity": "rare"},
            {"position": 3, "species": "dashmouse",     "element": "volt",   "name": "Dashmouse",     "short_name": "Dash",  "hp_max": 9,  "hp": 9,  "rarity": "common"},
            {"position": 4, "species": "voidling",      "element": "void",   "name": "Voidling",      "short_name": "Void",  "hp_max": 12, "hp": 12, "rarity": "uncommon"},
            {"position": 5, "species": "mossguard",     "element": "nature", "name": "Mossguard",     "short_name": "Moss",  "hp_max": 15, "hp": 15, "rarity": "common"},
        ],
    )
    opponent = Participant(
        name="Demo Champion",
        rank="Champion",
        loadout=[
            {"position": 0, "species": "voltcat_apex", "element": "volt",   "name": "Voltcat Apex", "short_name": "Voltc", "hp_max": 18, "hp": 18, "rarity": "legendary"},
            {"position": 1, "species": "bulwarthog",   "element": "nature", "name": "Bulwarthog",   "short_name": "Bulw",  "hp_max": 14, "hp": 14, "rarity": "uncommon"},
            {"position": 2, "species": "stormhare",    "element": "volt",   "name": "Stormhare",    "short_name": "Storm", "hp_max": 11, "hp": 11, "rarity": "rare"},
            {"position": 3, "species": "tidewyrm",     "element": "water",  "name": "Tidewyrm",     "short_name": "Tide",  "hp_max": 9,  "hp": 9,  "rarity": "rare"},
            {"position": 4, "species": "mindroot",     "element": "void",   "name": "Mindroot",     "short_name": "Mind",  "hp_max": 17, "hp": 17, "rarity": "legendary"},
            {"position": 5, "species": "shellpup",     "element": "water",  "name": "Shellpup",     "short_name": "Shell", "hp_max": 12, "hp": 12, "rarity": "uncommon"},
        ],
    )

    # ----- Round 1: showcase primitives -----
    # A1: BIG damage with cascade trigger
    a1_trigger = Action(
        action_id="demo_r1_a1_t1",
        actor=CardRef(side=Side.PLAYER, position=2, card="Tide Serpent"),
        target=CardRef(side=Side.OPPONENT, position=0, card="Voltcat Apex"),
        kind=ActionKind.DAMAGE,
        amount=4,
        hp_after={"opponent/0": 14},
        reason="ON_DAMAGE",
        log_line="Tide Serpent ON_DAMAGE counter: 4 → Voltcat Apex",
    )
    a1 = Action(
        action_id="demo_r1_a1",
        actor=CardRef(side=Side.OPPONENT, position=0, card="Voltcat Apex"),
        target=CardRef(side=Side.PLAYER, position=2, card="Tide Serpent"),
        kind=ActionKind.DAMAGE,
        amount=15,
        hp_after={"player/2": 3},
        triggers=[a1_trigger],
        log_line="Voltcat Apex strikes Tide Serpent for 15  (HP 18→3)",
    )

    # A2: BUFF on self
    a2 = Action(
        action_id="demo_r1_a2",
        actor=CardRef(side=Side.PLAYER, position=1, card="Iron Boar"),
        target=None,
        kind=ActionKind.BUFF,
        amount=2,
        hp_after={},
        log_line="Iron Boar steels itself (+2 ATK)",
    )

    # A3: HEAL ally
    a3 = Action(
        action_id="demo_r1_a3",
        actor=CardRef(side=Side.OPPONENT, position=4, card="Mindroot"),
        target=CardRef(side=Side.OPPONENT, position=0, card="Voltcat Apex"),
        kind=ActionKind.HEAL,
        amount=5,
        hp_after={"opponent/0": 18},
        log_line="Mindroot heals Voltcat Apex for 5  (HP 14→18)",
    )

    # A4: SHIELD self
    a4 = Action(
        action_id="demo_r1_a4",
        actor=CardRef(side=Side.PLAYER, position=0, card="Embercat"),
        target=None,
        kind=ActionKind.SHIELD,
        amount=3,
        hp_after={},
        log_line="Embercat raises a shield (+3 DEF)",
    )

    # A5: Lethal hit (KO cue)
    a5 = Action(
        action_id="demo_r1_a5",
        actor=CardRef(side=Side.OPPONENT, position=2, card="Stormhare"),
        target=CardRef(side=Side.PLAYER, position=3, card="Dashmouse"),
        kind=ActionKind.DAMAGE,
        amount=12,
        hp_after={"player/3": 0},
        log_line="Stormhare obliterates Dashmouse  (HP 9→0)  ☠",
    )

    # A6: Death event (KO chrome)
    a6 = Action(
        action_id="demo_r1_a6",
        actor=CardRef(side=Side.PLAYER, position=3, card="Dashmouse"),
        target=None,
        kind=ActionKind.DEATH,
        amount=None,
        hp_after={},
        log_line="Dashmouse falls",
    )

    round1 = Round(round=1, first_player=Side.OPPONENT, actions=[a1, a2, a3, a4, a5, a6])

    outcome = Outcome(
        winner=Side.OPPONENT,
        player_hp_remaining=75,
        opponent_hp_remaining=86,
        stats=MatchStats(
            round_count=1,
            cards_killed={"player": 1, "opponent": 0},
        ),
        rewards=Rewards(currency=0, rank_delta="+0"),
    )

    return Match(
        match_id="demo_animation",
        kind="pve",
        timestamp=dt.datetime.now(dt.timezone.utc).isoformat(),
        participants={"player": player, "opponent": opponent},
        seed="0xDEM0",
        rounds=[round1],
        outcome=outcome,
    )


def run_demo(*, color: bool = True, fps: int = 20, max_seconds: int = 30) -> int:
    """Render the demo match in the current terminal at ``fps`` for at most ``max_seconds``.

    Pure stdout writer — no watchdog, no state.json, no keyboard input.
    Ctrl-C exits cleanly. Returns 0 on natural finish, 1 on KeyboardInterrupt.
    """
    match = build_demo_match()
    pb = MatchPlayback(match=match)
    tick_ms = max(1, int(1000 / max(1, fps)))
    deadline = time.monotonic() + max_seconds
    # Hide cursor + clear screen on entry; restore on exit.
    print("\x1b[?25l\x1b[2J\x1b[H", end="", flush=True)
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            pb.step(elapsed_ms=tick_ms)
            frame = pb.snapshot()
            # Repaint from top — same dimensions every frame.
            print("\x1b[H", end="")
            print(render_frame(frame, color=color))
            if pb.status == PlaybackStatus.ENDED and pb._ended_dwell_ms > 1500:
                break
            time.sleep(tick_ms / 1000.0)
    except KeyboardInterrupt:
        return 1
    finally:
        print("\x1b[?25h", end="", flush=True)
    return 0
