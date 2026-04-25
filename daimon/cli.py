"""daimon CLI — `daimon init`, `daimon match`, `daimon mine`, `daimon pull`.

V1 alpha: only `init`, `version`, `verify` are wired up. The rest are stubs
that print a "not yet implemented" message and exit nonzero, so scripts can
detect feature gaps.

## Auto-update integration

The group callback (``main``) calls ``ensure_art_available()`` for every
subcommand EXCEPT the ones in ``ART_PURE_COMMANDS`` (init, whoami, update,
mine, npcs — none of which touch art binaries). This means:

  * First run of any art-using command auto-pulls the pack synchronously
    (~900MB, one-time, with a progress bar).
  * Subsequent runs spawn a detached background check (rate-limited 24h).
  * ``DAIMON_NO_AUTO_UPDATE=1`` opts out entirely; pinned versions via
    ``DAIMON_PIN_ART`` are honored.

The explicit ``daimon update`` command re-uses the same engine (with
``blocking=True``) for users who want to force a check.
"""

from __future__ import annotations

import sys

import click

from daimon import __version__


# Subcommands that don't need art on disk — skipped by the auto-fetch hook.
# Keep this list short and explicit; defaulting to "fetch" is the safer
# behavior for an end-user CLI.
ART_PURE_COMMANDS = frozenset({
    "init", "whoami", "update", "mine", "npcs",
    # Pure-local browsers — never touch art binaries:
    "collection", "catalog", "loadout",
})


@click.group()
@click.version_option(__version__, prog_name="daimon")
@click.pass_context
def main(ctx: click.Context) -> None:
    """DAIMON — agentic-first autobattler."""
    sub = ctx.invoked_subcommand
    if sub and sub not in ART_PURE_COMMANDS:
        from daimon.update import ArtUpdateError, ensure_art_available
        try:
            ensure_art_available()
        except ArtUpdateError as e:
            click.echo(f"error: failed to fetch art-pack: {e}", err=True)
            click.echo(
                "  hint: ensure network access, or set "
                "DAIMON_NO_AUTO_UPDATE=1 to skip the fetch.",
                err=True,
            )
            sys.exit(2)


@main.command()
@click.option("--force", is_flag=True, help="Overwrite existing identity (DESTRUCTIVE).")
def init(force: bool) -> None:
    """Generate a fresh ed25519 identity + 24-word recovery mnemonic."""
    from daimon.identity import generate_identity

    try:
        identity = generate_identity(force=force)
    except FileExistsError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    from daimon.identity.keys import PRIVATE_KEY_PATH
    click.echo("Identity generated.\n")
    click.echo(f"  pubkey:  {identity.pubkey_hex}")
    click.echo(f"  stored:  {PRIVATE_KEY_PATH} (mode 0600)\n")
    click.echo("RECOVERY MNEMONIC — write this down NOW. We will never show it again:")
    click.echo()
    words = (identity.mnemonic or "").split()
    for i in range(0, len(words), 4):
        chunk = words[i:i + 4]
        click.echo("  " + "  ".join(f"{i+j+1:>2}. {w:<10}" for j, w in enumerate(chunk)))
    click.echo()
    click.echo("If you lose this and your identity.key, your collection is unrecoverable.")


@main.command()
def whoami() -> None:
    """Print this machine's DAIMON public key."""
    from daimon.identity import load_identity
    try:
        identity = load_identity()
    except FileNotFoundError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)
    click.echo(identity.pubkey_hex)


@main.command()
@click.argument("loadout_a")
@click.argument("loadout_b")
@click.option("--seed", default=None, help="Hex-encoded 32-byte seed (default: random).")
def match(loadout_a: str, loadout_b: str, seed: str | None) -> None:
    """Resolve a match between two loadout JSON files. (V1 alpha — basic output.)

    Both files may be in any supported shape: bare list, ``{"cards":[...]}``,
    or showcase format (``{"loadout_id":..., "loadout":["card_id",...]}``).
    See ``daimon.loadouts.load_loadout_file`` for the format matrix.
    """
    import os

    from daimon.engine import resolve_match
    from daimon.loadouts import load_loadout_file
    from daimon.play.publish import publish_match_state

    try:
        a, a_raw = load_loadout_file(loadout_a)
        b, b_raw = load_loadout_file(loadout_b)
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)
    seed_bytes = bytes.fromhex(seed) if seed else os.urandom(32)
    result = resolve_match(a, b, seed_bytes)

    # Publish to state.json so `daimon play` (if running) animates this match.
    # Mirrors the dm_match MCP side-effect — keeps the two surfaces in lockstep.
    state_id = publish_match_state(
        result=result, loadout_a=a, loadout_b=b,
        a_raw=a_raw, b_raw=b_raw,
    )

    click.echo(f"seed:     {seed_bytes.hex()}")
    click.echo(f"winner:   {result.winner if result.winner is not None else 'draw'}")
    click.echo(f"reason:   {result.reason}")
    click.echo(f"hp_a:     {result.side_a_final_hp}")
    click.echo(f"hp_b:     {result.side_b_final_hp}")
    click.echo(f"rounds:   {len(result.rounds)}")
    if state_id:
        click.echo(f"state_id: {state_id}")


@main.command("play")
@click.option("--state", "state_path", default=None,
              help="Override state.json path (default: ~/.config/daimon/state.json or $DAIMON_STATE).")
@click.option("--no-color", is_flag=True, help="Disable ANSI color output.")
@click.option("--no-input", is_flag=True,
              help="Disable keyboard input (CI / pipes).")
@click.option("--paused", is_flag=True,
              help="Start each match paused instead of auto-playing.")
@click.option("--tick-ms", default=50, type=int,
              help="Loop tick interval in ms (default: 50 = 20 Hz).")
def play(state_path: str | None, no_color: bool, no_input: bool,
         paused: bool, tick_ms: int) -> None:
    """Spectator HUD — watch matches play out live in this terminal.

    Opens a long-lived ASCII window that watches state.json for new match
    payloads, then walks through them action-by-action with playback
    controls:

      space    pause / resume
      → / ←    step forward / back
      ↑ / ↓    speed up / down (0.25x .. 4x)
      r        restart current match
      n        skip to end (reveal outcome)
      q / esc  quit

    Idle screen shows a list of recently-seen matches. Quit any time with q.
    """
    from daimon.play.hud import run_play

    rc = run_play(
        state_path=state_path,
        color=not no_color,
        autoplay=not paused,
        no_input=no_input,
        tick_ms=tick_ms,
    )
    sys.exit(rc)


@main.command("play-demo")
@click.option("--no-color", is_flag=True, help="Disable ANSI color output.")
@click.option("--fps", default=20, type=int, help="Frames per second (default: 20).")
@click.option("--max-seconds", default=30, type=int,
              help="Cap on demo duration in seconds (default: 30).")
def play_demo(no_color: bool, fps: int, max_seconds: int) -> None:
    """Animation showcase — render a synthetic match exercising every primitive.

    Synthesises a hand-crafted Match with damage / heavy-hit / buff / heal /
    shield / KO actions plus a cascade trigger, then renders it through the
    spectator HUD. Hits acceptance criterion #4 from animation_design.md
    ("daimon play demo showcases every primitive in under 30 seconds").

    Ctrl-C exits cleanly.
    """
    from daimon.play.demo import run_demo

    rc = run_demo(color=not no_color, fps=fps, max_seconds=max_seconds)
    sys.exit(rc)


@main.command("npcs")
@click.option("--tier", default=None,
              help="Filter to one tier (rookie / novice / veteran / elite / champion).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def npcs(tier: str | None, as_json: bool) -> None:
    """List the NPC tier roster (Rookie -> Champion).

    With --tier, list only that tier. Without it, list every NPC grouped by tier.
    """
    import json as _json

    from daimon.npcs import get_roster, list_npcs

    roster = get_roster()
    try:
        npcs_l = list_npcs(tier)
    except ValueError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(_json.dumps({
            "tiers": [
                {"tier_id": t.tier_id, "rank": t.rank, "label": t.label,
                 "rule": t.rule, "npc_ids": list(t.npc_ids)}
                for t in sorted(roster.tiers, key=lambda x: x.rank)
            ],
            "npcs": [
                {"npc_id": n.npc_id, "name": n.name, "tier": n.tier,
                 "rank": n.rank, "flavor": n.flavor}
                for n in npcs_l
            ],
            "count": len(npcs_l),
        }, indent=2))
        return

    if tier:
        click.echo(f"NPCs in tier '{tier}':\n")
        for n in npcs_l:
            click.echo(f"  {n.npc_id:24}  {n.name}")
            click.echo(f"  {'':24}  \"{n.flavor}\"")
            click.echo(f"  {'':24}  loadout: {', '.join(n.loadout)}")
            click.echo()
        return

    for t in sorted(roster.tiers, key=lambda x: x.rank):
        tier_npcs = [n for n in npcs_l if n.tier == t.tier_id]
        click.echo(f"\n=== {t.label.upper()} (rank {t.rank}) ===")
        click.echo(f"  {t.rule}")
        click.echo()
        for n in tier_npcs:
            click.echo(f"  {n.npc_id:24}  {n.name:24}  \"{n.flavor}\"")


@main.command("match-npc")
@click.argument("loadout_path")
@click.argument("npc_id")
@click.option("--seed", default=None, help="Hex 32-byte seed (default: random).")
@click.option("--rounds", "show_rounds", is_flag=True,
              help="Print per-round HP totals after the result.")
def match_npc(loadout_path: str, npc_id: str, seed: str | None,
              show_rounds: bool) -> None:
    """Play your loadout JSON file against a named NPC opponent.

    The loadout file may be in any supported shape: bare list,
    ``{"cards":[...]}``, or showcase format
    (``{"loadout_id":..., "loadout":["card_id",...]}``). See
    ``daimon.loadouts.load_loadout_file`` for the format matrix.

    Example:
        daimon match-npc my_team.json sparring_sam --seed 0...
        daimon match-npc daimon/loadouts/showcase/showcase_l1_inferno_burnstack.json sparring_sam
    """
    import os

    from daimon.engine import resolve_match
    from daimon.loadouts import load_loadout_file
    from daimon.npcs import get_npc, npc_loadout
    from daimon.npcs.loader import npc_card_dicts
    from daimon.play.publish import publish_match_state

    try:
        npc = get_npc(npc_id)
    except KeyError as e:
        click.echo(f"error: {e}", err=True)
        click.echo("hint: run `daimon npcs` to see available NPC ids.", err=True)
        sys.exit(1)

    try:
        a, a_raw = load_loadout_file(loadout_path)
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    try:
        b = npc_loadout(npc)
        b_raw = npc_card_dicts(npc)
    except ValueError as e:
        click.echo(f"error: NPC loadout invalid: {e}", err=True)
        sys.exit(2)

    seed_bytes = bytes.fromhex(seed) if seed else os.urandom(32)
    result = resolve_match(a, b, seed_bytes)

    # Publish to state.json — same payload shape as dm_match_npc's MCP
    # side-effect, so a `daimon play` HUD watching state.json picks it up.
    state_id = publish_match_state(
        result=result, loadout_a=a, loadout_b=b,
        a_raw=a_raw, b_raw=list(b_raw),
        opponent_name=npc.name, opponent_rank=npc.tier,
    )

    click.echo(f"opponent: {npc.name}  ({npc.tier}, rank {npc.rank})")
    click.echo(f"          \"{npc.flavor}\"")
    click.echo(f"seed:     {seed_bytes.hex()}")
    click.echo(f"winner:   {'you' if result.winner == 0 else npc.name if result.winner == 1 else 'draw'}")
    click.echo(f"reason:   {result.reason}")
    click.echo(f"hp_a:     {result.side_a_final_hp}  (you)")
    click.echo(f"hp_b:     {result.side_b_final_hp}  ({npc.name})")
    click.echo(f"rounds:   {len(result.rounds)}")
    if state_id:
        click.echo(f"state_id: {state_id}")

    if show_rounds:
        click.echo()
        click.echo("per-round hp:")
        for r in result.rounds:
            click.echo(f"  round {r.round_number}: you={r.side_a_hp_total:4}  "
                       f"opp={r.side_b_hp_total:4}  ({len(r.actions)} actions)")


@main.command()
@click.argument("card_path")
@click.option("--art-root", default=None,
              help="Directory containing art files referenced by _render_only.art (default: card file's dir).")
@click.option("--out", default=None, help="Output PNG path (default: <card>.png next to source).")
@click.option("--terminal", is_flag=True, help="Print hybrid terminal render instead of generating PNG.")
@click.option("--tier", type=int, default=None, help="Force terminal tier 1–7 (default: auto-detect).")
def render(card_path: str, art_root: str | None, out: str | None,
           terminal: bool, tier: int | None) -> None:
    """Render a card from its JSON definition."""
    import json
    from pathlib import Path

    p = Path(card_path)
    if not p.exists():
        click.echo(f"error: {p} not found", err=True)
        sys.exit(1)
    pack = json.loads(p.read_text())

    if terminal:
        from daimon.cards import load_card_dict
        from daimon.render import render_hybrid, render_info_from_pack_dict
        card = load_card_dict(pack)
        art_dir = Path(art_root) if art_root else p.parent
        info = render_info_from_pack_dict(pack, art_dir)
        click.echo(render_hybrid(card, info, tier=tier))
        return

    from daimon.render import compose_card_from_pack_dict
    out_path = Path(out) if out else p.with_suffix(".png")
    art_dir = Path(art_root) if art_root else p.parent
    written = compose_card_from_pack_dict(pack, art_dir, out_path)
    click.echo(f"wrote: {written}")


@main.group()
def mine() -> None:
    """Mining: ledger status, Claude Code hook install, manual receipt entry."""


@mine.command("status")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def mine_status(as_json: bool) -> None:
    """Show current balance + recent ledger activity."""
    import json as _json

    from daimon.mining import get_recent_entries, get_stats, verify_ledger
    from daimon.mining.ledger import LEDGER_PATH as _LP

    if not _LP.exists():
        if as_json:
            click.echo(_json.dumps({
                "balance": 0,
                "total_mined": 0,
                "total_pulled": 0,
                "total_purchased": 0,
                "mine_count": 0,
                "pull_count": 0,
                "purchase_count": 0,
                "ledger_entries": 0,
                "verified": True,
                "recent": [],
            }))
        else:
            click.echo("balance:        0")
            click.echo("ledger:         (empty — no productive work recorded yet)")
            click.echo("hint:           run `daimon mine install-hook` to start mining")
        return

    stats = get_stats()
    verification = verify_ledger()
    recent = get_recent_entries(limit=10)

    if as_json:
        click.echo(_json.dumps({
            "balance": stats.balance,
            "total_mined": stats.total_mined,
            "total_pulled": stats.total_pulled,
            "total_purchased": stats.total_purchased,
            "mine_count": stats.mine_count,
            "pull_count": stats.pull_count,
            "purchase_count": stats.purchase_count,
            "ledger_entries": stats.entry_count,
            "verified": verification.get("ok"),
            "errors": verification.get("errors", []),
            "recent": [{k: v for k, v in e.items()
                        if k in ("ts", "kind", "amount", "tool_name",
                                 "card_id", "rarity", "skin_slug",
                                 "skin_axis")}
                       for e in recent],
        }, indent=2))
        return

    click.echo(f"balance:        {stats.balance}")
    click.echo(f"total mined:    {stats.total_mined}  ({stats.mine_count} events)")
    click.echo(f"total pulled:   {stats.total_pulled}  ({stats.pull_count} events)")
    click.echo(f"total purchased:{stats.total_purchased:>4}  "
               f"({stats.purchase_count} skins)")
    click.echo(f"ledger:         {stats.entry_count} entries — "
               f"{'OK' if verification.get('ok') else 'CORRUPT'}")
    if not verification.get("ok"):
        for err in verification.get("errors", [])[:3]:
            click.echo(f"  ! {err}")
    if recent:
        click.echo("\nrecent:")
        for e in recent[-10:]:
            kind = e.get("kind", "?")
            amount = e.get("amount", 0)
            label = (e.get("tool_name") or e.get("card_id")
                     or e.get("skin_slug") or "")
            click.echo(f"  {e.get('ts', '')[:19]}  {kind:8} {amount:+5}  {label}")


@mine.command("install-hook")
@click.option("--settings", default=None, help="Override settings.json path.")
@click.option("--dry-run", is_flag=True, help="Show what would change.")
def mine_install_hook(settings: str | None, dry_run: bool) -> None:
    """Register the DAIMON PostToolUse hook in Claude Code settings."""
    from pathlib import Path
    from daimon.mining.installer import (
        DEFAULT_SETTINGS_PATH,
        install_hook,
    )

    settings_path = Path(settings) if settings else DEFAULT_SETTINGS_PATH
    try:
        result = install_hook(settings_path=settings_path, dry_run=dry_run)
    except RuntimeError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    click.echo(f"action:         {result['action']}")
    click.echo(f"settings:       {result['settings_path']}")
    if result.get("backup_path"):
        click.echo(f"backup:         {result['backup_path']}")
    if result["action"] == "installed":
        click.echo("\nHook is live. Productive Claude Code tool calls will now mine "
                   "currency.\nRun `daimon mine status` to inspect the ledger.")


@mine.command("uninstall-hook")
@click.option("--settings", default=None, help="Override settings.json path.")
@click.option("--dry-run", is_flag=True, help="Show what would change.")
def mine_uninstall_hook(settings: str | None, dry_run: bool) -> None:
    """Remove the DAIMON PostToolUse hook from Claude Code settings."""
    from pathlib import Path
    from daimon.mining.installer import (
        DEFAULT_SETTINGS_PATH,
        uninstall_hook,
    )

    settings_path = Path(settings) if settings else DEFAULT_SETTINGS_PATH
    try:
        result = uninstall_hook(settings_path=settings_path, dry_run=dry_run)
    except RuntimeError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    click.echo(f"action:    {result['action']}")
    click.echo(f"settings:  {result['settings_path']}")
    if result.get("backup_path"):
        click.echo(f"backup:    {result['backup_path']}")


@mine.command("receipt")
@click.option("--verbose", is_flag=True, help="Print one-line JSON status.")
@click.option("--ledger", default=None, help="Override ledger path.")
def mine_receipt(verbose: bool, ledger: str | None) -> None:
    """Hook entrypoint — reads a Claude Code PostToolUse event from stdin."""
    from daimon.mining.hook import main as _hook_main

    argv: list = []
    if verbose:
        argv.append("--verbose")
    if ledger:
        argv.extend(["--ledger", ledger])
    sys.exit(_hook_main(argv))


@main.command()
@click.option("--seed", default=None, help="Hex 32-byte seed (default: random).")
@click.option("--catalog", default=None, help="Catalog id (default: v1_alpha).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def pull(seed: str | None, catalog: str | None, as_json: bool) -> None:
    """Spend 100 currency on a gacha card pull from the bundled catalog."""
    import json as _json

    from daimon.catalog import DEFAULT_CATALOG_ID
    from daimon.mining.ledger import InsufficientBalanceError
    from daimon.pulls import perform_pull

    seed_bytes = None
    if seed:
        try:
            seed_bytes = bytes.fromhex(seed)
            if len(seed_bytes) != 32:
                click.echo(f"error: seed must be 32 bytes, got {len(seed_bytes)}",
                           err=True)
                sys.exit(1)
        except ValueError as e:
            click.echo(f"error: seed not hex: {e}", err=True)
            sys.exit(1)

    try:
        receipt = perform_pull(
            catalog_name=catalog or DEFAULT_CATALOG_ID,
            seed=seed_bytes,
        )
    except FileNotFoundError:
        click.echo("error: no identity. Run `daimon init` first.", err=True)
        sys.exit(1)
    except InsufficientBalanceError as e:
        click.echo(f"error: {e}", err=True)
        click.echo("hint: run `daimon mine status` to see your balance.", err=True)
        sys.exit(2)
    except RuntimeError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(3)

    # Publish to state.json — same payload shape as dm_pull's MCP side-effect,
    # so a `daimon play` HUD picks up the gacha reveal animation.
    from daimon.play.publish import publish_pull_state
    state_id = publish_pull_state(receipt_dict=receipt.to_dict())

    if as_json:
        out = receipt.to_dict()
        if state_id:
            out["state_id"] = state_id
        click.echo(_json.dumps(out, indent=2))
        return

    payload = receipt.payload
    click.echo(f"PULL  {receipt.rarity.upper():9} — {payload.get('name', receipt.card_id)}")
    click.echo(f"  card_id:       {receipt.card_id}")
    click.echo(f"  serial:        {receipt.serial.serial}")
    click.echo(f"  pack:          {receipt.pack}")
    click.echo(f"  cost:          {receipt.cost}")
    click.echo(f"  balance now:   {receipt.balance_after}")
    click.echo(f"  seed:          {receipt.seed_hex}")
    click.echo(f"  ledger hash:   {receipt.ledger_entry_hash[:16]}…")
    if state_id:
        click.echo(f"  state_id:      {state_id}")


@main.command("play-render")
@click.option("--state", "state_path", default=None,
              help="Override state file path (default: ~/.config/daimon/state.json).")
@click.option("--renders", "renders_dir", default=None,
              help="Override render output dir (default: ~/.config/daimon/renders).")
@click.option("--once", is_flag=True,
              help="Render current state once and exit (for dev loops / smoke tests).")
def play_render(state_path: str | None, renders_dir: str | None, once: bool) -> None:
    """Background PNG renderer — game-terminal frame producer.

    Companion to the spectator HUD (`daimon play`). This command is the
    single reader of state.json that produces PNG frames + manifest under
    the renders dir for downstream image-capable viewers (WezTerm slideshow,
    HTML replay, GIF stitch).

    Watches ``state.json`` and dispatches each new state (by its ``view``
    field) to a renderer that writes a PNG under the renders dir. Dedupes by
    the state's ``id`` so restarts don't re-render a frame that was already
    shown.

    Agent-facing MCP tools (``dm_match``, ``dm_pull``, etc.) write to the
    state file as a side effect; this process is the only reader.

    With ``--once``: render whatever's currently in state.json and exit.
    Without it: block in the watcher loop until Ctrl-C.
    """
    import signal
    from pathlib import Path

    from daimon.play.terminal import GameTerminal

    sp = Path(state_path) if state_path else None
    rd = Path(renders_dir) if renders_dir else None

    term = GameTerminal(state_path=sp, renders_dir=rd)

    if once:
        result = term.dispatch_once()
        click.echo(f"action:   {result.action}")
        if result.state is not None:
            click.echo(f"view:     {result.state.view}")
            click.echo(f"state_id: {result.state.id}")
        if result.out_path is not None:
            click.echo(f"wrote:    {result.out_path}")
        if result.error:
            click.echo(f"error:    {result.error}", err=True)
            sys.exit(1)
        return

    click.echo(f"daimon play-render  —  state: {term.state_path}")
    click.echo(f"                         renders: {term.renders_dir}")
    click.echo("press Ctrl-C to quit.")
    click.echo()

    signal.signal(signal.SIGINT, lambda *_: term.stop())
    signal.signal(signal.SIGTERM, lambda *_: term.stop())

    term.start()
    term.wait()
    click.echo("\nbye.")


@main.command()
@click.option("--check", is_flag=True,
              help="Only check + report — don't install. Honors 24h rate-limit.")
@click.option("--force", is_flag=True,
              help="Bypass rate-limit AND cross-major guard. "
                   "Use after intentionally upgrading the engine.")
@click.option("--version", "version_tag", default=None,
              help="Install this exact tag (e.g. art-v1.0). "
                   "Honors DAIMON_PIN_ART if not given.")
def update(check: bool, force: bool, version_tag: str | None) -> None:
    """Refresh the art-pack from GitHub Releases.

    \b
    Examples:
      daimon update                    # check + install if newer
      daimon update --check            # report status, no install
      daimon update --version art-v1.0 # install a specific version
      daimon update --force            # re-install even if up-to-date

    Path layout:
      \b
      ~/.daimon/art/v1_alpha/             # live pack
      ~/.daimon/cache/staging/            # download scratch
      ~/.daimon/cache/last_check.json     # rate-limit state
      ~/.daimon/cache/update.log          # background-check log

    Override the root via ``DAIMON_ART_DIR``. Pin a version via
    ``DAIMON_PIN_ART``. Disable auto-fetch entirely via
    ``DAIMON_NO_AUTO_UPDATE=1``.
    """
    from daimon.update import (
        ArtUpdateError,
        art_pack_dir,
        current_version,
        do_update,
    )
    from daimon.update.api import gh_latest_release, gh_release_by_tag
    from daimon.update.paths import art_repo, pinned_version

    before = current_version()

    if check:
        # Status-only path — no download.
        repo = art_repo()
        target = version_tag or pinned_version()
        try:
            rel = (gh_release_by_tag(repo, target) if target
                   else gh_latest_release(repo))
        except Exception as e:
            click.echo(f"error: GitHub API failed: {e}", err=True)
            sys.exit(1)
        click.echo(f"installed: {before or '(none)'}")
        click.echo(f"latest:    {rel.tag if rel else '(no release found)'}")
        if rel and before != rel.tag:
            click.echo("status:    update available — run `daimon update` to install")
        elif rel:
            click.echo("status:    up to date")
        return

    try:
        rel = do_update(target_version=version_tag, force=force,
                        show_progress=True)
    except ArtUpdateError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    if before == rel.tag and not force:
        click.echo(f"already up to date: {rel.tag}")
    else:
        click.echo(f"installed {rel.tag} (was: {before or 'none'})")
        click.echo(f"  pack dir: {art_pack_dir()}")


# ---------------------------------------------------------------------------
# Shop subcommand tree — `daimon shop ...` + `daimon skins` + `daimon skin ...`
# ---------------------------------------------------------------------------

def _format_secs(secs: int) -> str:
    """Format a positive seconds int as Hh Mm Ss."""
    h, rem = divmod(max(0, int(secs)), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


@main.group(invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
@click.option("--slot", "slot_idx", default=None, type=int,
              help="Show details for one slot only.")
@click.pass_context
def shop(ctx: click.Context, as_json: bool, slot_idx: int | None) -> None:
    """Browse today's 6-slot skin shop. Refreshes daily at 00:00 UTC.

    \b
    Subcommands:
      daimon shop                  list today's slots
      daimon shop --slot N         detail one slot
      daimon shop buy <slot|key>   purchase by index or 'card_id/skin_slug'
      daimon shop refresh-status   seconds until next rotation
    """
    if ctx.invoked_subcommand is not None:
        return
    import json as _json

    from daimon.shop import get_shop_state

    try:
        state = get_shop_state()
    except FileNotFoundError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    if slot_idx is not None:
        if slot_idx < 0 or slot_idx >= len(state.slots):
            click.echo(
                f"error: slot {slot_idx} out of range (0..{len(state.slots) - 1})",
                err=True,
            )
            sys.exit(1)
        s = state.slots[slot_idx]
        if as_json:
            click.echo(_json.dumps(s.to_dict(), indent=2))
            return
        click.echo(f"slot {s.index}:")
        click.echo(f"  card:      {s.listing.card_id}")
        click.echo(f"  skin:      {s.listing.skin_name}  ({s.listing.skin_slug})")
        click.echo(f"  axis:      {s.listing.skin_axis}")
        click.echo(f"  rarity:    {s.listing.rarity}")
        click.echo(f"  cost:      {s.cost} ¤")
        if s.sold:
            click.echo(f"  status:    [OWNED] (purchased {s.purchased_at})")
        click.echo(f"  art:       {s.listing.art_path}")
        return

    if as_json:
        click.echo(_json.dumps(state.to_dict(), indent=2))
        return

    click.echo(f"balance:    {state.balance} ¤")
    click.echo(f"this week:  {state.weekly_count}/{state.weekly_cap} purchases")
    click.echo(f"refresh in: {_format_secs(state.seconds_until_rotation)}")
    click.echo()
    if not state.slots:
        click.echo("(no slots — you've already bought everything in your pool, or "
                   "the art-pack has no skin variants installed yet.)")
        return
    for s in state.slots:
        # Slots stay in place all day; sold ones show [OWNED] in the cost
        # column instead of the ¤ price so the layout doesn't shift.
        cost_col = "[OWNED]" if s.sold else f"{s.cost:>4} ¤"
        click.echo(
            f"  [{s.index}] {s.listing.card_id:24s}  "
            f"{s.listing.skin_name:28s}  "
            f"{s.listing.rarity:11s}  {cost_col:>7}"
        )
    click.echo()
    click.echo("buy with:  daimon shop buy <slot>     (e.g. daimon shop buy 0)")
    click.echo("        or daimon shop buy <card>/<slug>")
    click.echo("note:      sold slots stay in place until the next 00:00 UTC rotation.")


@shop.command("buy")
@click.argument("selector")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def shop_buy(selector: str, as_json: bool) -> None:
    """Purchase a slot. Selector is either a 0-based slot index or
    'card_id/skin_slug' for unambiguous addressing."""
    import json as _json

    from daimon.mining.ledger import InsufficientBalanceError
    from daimon.shop import (
        AlreadyOwnedError,
        SlotNotInRotationError,
        WeeklyCapExceededError,
        purchase_slot,
    )

    # Normalize: bare digit → int; otherwise pass the string through.
    sel: str | int = int(selector) if selector.isdigit() else selector

    try:
        receipt = purchase_slot(sel)
    except FileNotFoundError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)
    except SlotNotInRotationError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)
    except AlreadyOwnedError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)
    except WeeklyCapExceededError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(3)
    except InsufficientBalanceError as e:
        click.echo(f"error: {e}", err=True)
        click.echo("hint: run `daimon mine status` to see your balance.", err=True)
        sys.exit(4)

    if as_json:
        click.echo(_json.dumps(receipt.to_dict(), indent=2))
        return

    click.echo(f"BOUGHT  {receipt.skin_name}  ({receipt.skin_slug})")
    click.echo(f"  card:          {receipt.card_id}")
    click.echo(f"  axis / rarity: {receipt.skin_axis} / {receipt.rarity}")
    click.echo(f"  cost:          {receipt.cost} ¤")
    click.echo(f"  balance now:   {receipt.balance_after} ¤")
    click.echo(f"  ledger hash:   {receipt.ledger_entry_hash[:16]}…")
    click.echo()
    click.echo(
        f"equip with:  daimon skin equip {receipt.card_id} {receipt.skin_slug}"
    )


@shop.command("refresh-status")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def shop_refresh_status(as_json: bool) -> None:
    """Seconds until the next 00:00 UTC rotation."""
    import json as _json

    from daimon.shop import seconds_until_next_rotation

    secs = seconds_until_next_rotation()
    if as_json:
        click.echo(_json.dumps(
            {"seconds_until_rotation": secs, "human": _format_secs(secs)}
        ))
        return
    click.echo(f"next rotation in: {_format_secs(secs)}  ({secs}s)")


@main.command("skins")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def skins(as_json: bool) -> None:
    """List the skins this identity owns."""
    import json as _json
    from dataclasses import asdict

    from daimon.shop import get_equipped, list_owned

    owned = list_owned()
    if as_json:
        rows = []
        for s in owned:
            d = asdict(s)
            d["equipped"] = get_equipped(s.card_id) == s.skin_slug
            rows.append(d)
        click.echo(_json.dumps({"owned": rows, "count": len(rows)}, indent=2))
        return

    if not owned:
        click.echo("(you don't own any skins yet — run `daimon shop`)")
        return
    click.echo(f"{len(owned)} skin(s) owned:\n")
    for s in owned:
        eq_marker = "  *" if get_equipped(s.card_id) == s.skin_slug else "   "
        click.echo(
            f" {eq_marker}  {s.card_id:24s}  {s.skin_name:28s}  "
            f"{s.rarity:11s}  bought {s.purchased_at[:10]}"
        )
    click.echo("\n(* = currently equipped)")


@main.group()
def skin() -> None:
    """Equip / unequip skins on your cards."""


@skin.command("equip")
@click.argument("card_id")
@click.argument("skin_slug")
def skin_equip_cmd(card_id: str, skin_slug: str) -> None:
    """Equip a skin you own onto a card.

    \b
    Example:
      daimon skin equip aegis_lion heretic_manuscript
    """
    from daimon.shop import NotOwnedError, SkinNotFoundError, equip_skin

    try:
        equip_skin(card_id, skin_slug)
    except (NotOwnedError, SkinNotFoundError) as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)
    click.echo(f"equipped: {card_id} ← {skin_slug}")


@skin.command("unequip")
@click.argument("card_id")
def skin_unequip_cmd(card_id: str) -> None:
    """Revert a card to its canonical base art."""
    from daimon.shop import unequip_skin

    unequip_skin(card_id)
    click.echo(f"unequipped: {card_id} → canonical base art")


# ---------------------------------------------------------------------------
# `daimon collection` — owned cards (mirrors dm_collection MCP tool)
# ---------------------------------------------------------------------------

# Rarity ordering used everywhere in the collection / catalog views. Matches
# the catalog manifest weights — keeps display consistent with `daimon
# catalog list` output.
_RARITY_ORDER = ("common", "uncommon", "rare", "epic", "legendary")


def _rarity_sort_key(r: str) -> int:
    try:
        return _RARITY_ORDER.index(r)
    except ValueError:
        return len(_RARITY_ORDER)  # unknown rarities sort last


@main.command("collection")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
@click.option("--rarity", default=None,
              help="Filter to one rarity (common/uncommon/rare/epic/legendary).")
@click.option("--card", default=None,
              help="Filter to one card_id (shows every serial of that card).")
def collection(as_json: bool, rarity: str | None, card: str | None) -> None:
    """List cards owned by this identity.

    Reads ``~/.config/daimon/collection.json``. Cards are grouped by
    card_id and shown with rarity + serial count. ``--rarity`` and
    ``--card`` are independent filters; combining them is fine.

    Example:
      daimon collection
      daimon collection --rarity legendary
      daimon collection --card magma_tyrant
      daimon collection --json
    """
    import json as _json

    from daimon.collection import list_serials

    serials = list_serials()
    if rarity:
        serials = [s for s in serials if s.get("rarity") == rarity]
    if card:
        serials = [s for s in serials if s.get("card_id") == card]

    # Group by card_id for the human-readable view; rarity rollup lives at
    # the top so a quick glance shows pull-luck distribution.
    by_card: dict[str, list[dict]] = {}
    rarity_counts: dict[str, int] = {}
    for s in serials:
        by_card.setdefault(s.get("card_id", "?"), []).append(s)
        r = s.get("rarity", "?")
        rarity_counts[r] = rarity_counts.get(r, 0) + 1

    if as_json:
        click.echo(_json.dumps({
            "count": len(serials),
            "unique_cards": len(by_card),
            "rarity_counts": rarity_counts,
            "serials": serials,
        }, indent=2))
        return

    if not serials:
        if rarity or card:
            click.echo("(no matching cards in your collection)")
        else:
            click.echo("(empty collection — run `daimon pull` to mint your first card)")
        return

    click.echo(f"total:  {len(serials)} serial(s) across {len(by_card)} unique card(s)")
    if rarity_counts:
        parts = [
            f"{r}={n}"
            for r, n in sorted(rarity_counts.items(), key=lambda kv: _rarity_sort_key(kv[0]))
        ]
        click.echo(f"rarity: {'  '.join(parts)}")
    click.echo()
    for cid in sorted(by_card.keys()):
        rows = by_card[cid]
        # Rarity is identical across serials of the same card_id, so just
        # take the first row's value.
        r = rows[0].get("rarity", "?")
        click.echo(f"  {cid:28s}  x{len(rows):<3}  {r}")


# ---------------------------------------------------------------------------
# `daimon catalog` — read-only catalog browser
# ---------------------------------------------------------------------------

@main.group("catalog")
def catalog_group() -> None:
    """Browse the bundled card catalog (cards, expansions, comparisons).

    \b
    Subcommands:
      daimon catalog expansions          list installed catalogs
      daimon catalog list                list every card in a catalog
      daimon catalog card <card_id>      print one card's full definition
      daimon catalog compare <a> <b>     side-by-side stat + trigger diff
    """


@catalog_group.command("expansions")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def catalog_expansions(as_json: bool) -> None:
    """List all installed card catalogs."""
    import json as _json

    from daimon.catalog import list_catalogs, load_catalog

    try:
        ids = list_catalogs()
    except Exception as e:  # noqa: BLE001
        click.echo(f"error: catalog load failed: {e}", err=True)
        sys.exit(1)

    rows = []
    for cid in ids:
        try:
            cat = load_catalog(cid)
            rarity_counts: dict[str, int] = {}
            for c in cat.cards:
                rarity_counts[c.rarity] = rarity_counts.get(c.rarity, 0) + 1
            rows.append({
                "pack_id": cat.pack_id,
                "version": cat.version,
                "description": cat.description,
                "card_count": len(cat.cards),
                "rarity_counts": rarity_counts,
            })
        except Exception as e:  # noqa: BLE001
            rows.append({"pack_id": cid, "error": str(e)})

    if as_json:
        click.echo(_json.dumps({"expansions": rows, "count": len(rows)}, indent=2))
        return

    if not rows:
        click.echo("(no catalogs installed)")
        return
    for r in rows:
        if "error" in r:
            click.echo(f"  {r['pack_id']:14s}  ERROR: {r['error']}")
            continue
        click.echo(f"  {r['pack_id']:14s}  v{r['version']:8s}  "
                   f"{r['card_count']} cards  {r['description']}")
        rc = r.get("rarity_counts", {})
        if rc:
            parts = [
                f"{rar}={n}"
                for rar, n in sorted(rc.items(), key=lambda kv: _rarity_sort_key(kv[0]))
            ]
            click.echo(f"  {'':14s}  {'  '.join(parts)}")


@catalog_group.command("list")
@click.option("--expansion", default=None, help="Catalog id (default: v1_alpha).")
@click.option("--rarity", default=None,
              help="Filter to one rarity (common/uncommon/rare/epic/legendary).")
@click.option("--element", default=None,
              help="Filter to one element (FIRE/WATER/NATURE/...).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def catalog_list(expansion: str | None, rarity: str | None,
                 element: str | None, as_json: bool) -> None:
    """List every card in a catalog with quick-look stats."""
    import json as _json

    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog

    cid = expansion or DEFAULT_CATALOG_ID
    try:
        cat = load_catalog(cid)
    except FileNotFoundError:
        click.echo(f"error: unknown catalog {cid!r}", err=True)
        click.echo("hint: run `daimon catalog expansions` to list installed catalogs.",
                   err=True)
        sys.exit(1)

    rows = []
    for cc in cat.cards:
        p = cc.payload
        if rarity and cc.rarity != rarity:
            continue
        if element and p.get("element") != element:
            continue
        rows.append({
            "card_id": cc.card_id,
            "species": p.get("species", cc.card_id),
            "element": p.get("element", "UNKNOWN"),
            "rarity": cc.rarity,
            "atk": p.get("atk", 0),
            "def": p.get("def", 0),
            "hp": p.get("hp", 0),
            "spd": p.get("spd", 0),
            "trigger_count": len(p.get("triggers", []) or []),
        })

    if as_json:
        click.echo(_json.dumps({
            "pack_id": cat.pack_id, "version": cat.version,
            "count": len(rows), "cards": rows,
        }, indent=2))
        return

    click.echo(f"catalog:  {cat.pack_id}  v{cat.version}  ({len(rows)} cards)")
    if not rows:
        click.echo("(no cards match the filter)")
        return
    click.echo()
    click.echo(f"  {'card_id':28s}  {'element':9s}  {'rarity':10s}  "
               f"{'atk':>3}/{'def':>3}/{'hp':>3}/{'spd':>3}  triggers")
    rows.sort(key=lambda r: (_rarity_sort_key(r["rarity"]), r["card_id"]))
    for r in rows:
        click.echo(
            f"  {r['card_id']:28s}  {r['element']:9s}  {r['rarity']:10s}  "
            f"{r['atk']:>3}/{r['def']:>3}/{r['hp']:>3}/{r['spd']:>3}  "
            f"{r['trigger_count']}"
        )


@catalog_group.command("card")
@click.argument("card_id")
@click.option("--expansion", default=None, help="Catalog id (default: v1_alpha).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def catalog_card(card_id: str, expansion: str | None, as_json: bool) -> None:
    """Print the full card definition for a catalog card."""
    import json as _json

    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog

    cid = expansion or DEFAULT_CATALOG_ID
    try:
        cat = load_catalog(cid)
    except FileNotFoundError:
        click.echo(f"error: unknown catalog {cid!r}", err=True)
        sys.exit(1)

    cc = cat.by_id.get(card_id)
    if cc is None:
        click.echo(f"error: unknown card {card_id!r} in catalog {cid!r}", err=True)
        click.echo("hint: run `daimon catalog list` to see all cards.", err=True)
        sys.exit(1)

    p = cc.payload
    if as_json:
        click.echo(_json.dumps({
            "card_id": cc.card_id, "pack": cc.pack, "rarity": cc.rarity,
            "payload": p,
        }, indent=2))
        return

    click.echo(f"card_id:   {cc.card_id}")
    click.echo(f"pack:      {cc.pack}")
    click.echo(f"rarity:    {cc.rarity}")
    if p.get("name"):
        click.echo(f"name:      {p['name']}")
    if p.get("species"):
        click.echo(f"species:   {p['species']}")
    click.echo(f"element:   {p.get('element', 'UNKNOWN')}")
    click.echo(f"stats:     atk={p.get('atk', 0)}  def={p.get('def', 0)}  "
               f"hp={p.get('hp', 0)}  spd={p.get('spd', 0)}")
    triggers = p.get("triggers", []) or []
    if triggers:
        click.echo(f"triggers:  ({len(triggers)})")
        for t in triggers:
            if isinstance(t, dict):
                click.echo(
                    f"  - when={t.get('when'):16s} "
                    f"op={t.get('op'):20s} "
                    f"target={t.get('target'):8s} "
                    f"value={t.get('value')}"
                )
    if p.get("flavor"):
        click.echo(f"flavor:    \"{p['flavor']}\"")
    if p.get("rule_change"):
        click.echo(f"rule:      {p['rule_change']}  (legendary mutation)")


@catalog_group.command("compare")
@click.argument("card_a")
@click.argument("card_b")
@click.option("--expansion", default=None, help="Catalog id (default: v1_alpha).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def catalog_compare(card_a: str, card_b: str, expansion: str | None,
                    as_json: bool) -> None:
    """Side-by-side stat + trigger diff between two catalog cards."""
    import json as _json

    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog

    cid = expansion or DEFAULT_CATALOG_ID
    try:
        cat = load_catalog(cid)
    except FileNotFoundError:
        click.echo(f"error: unknown catalog {cid!r}", err=True)
        sys.exit(1)

    cc_a = cat.by_id.get(card_a)
    cc_b = cat.by_id.get(card_b)
    missing = [x for x, cc in ((card_a, cc_a), (card_b, cc_b)) if cc is None]
    if missing:
        click.echo(f"error: unknown card(s) in catalog {cid!r}: {', '.join(missing)}",
                   err=True)
        sys.exit(1)
    pa, pb = cc_a.payload, cc_b.payload

    def _tsig(p):
        return [
            (t.get("when"), t.get("op"), t.get("target"), t.get("value"))
            for t in (p.get("triggers", []) or [])
            if isinstance(t, dict)
        ]

    sa, sb = _tsig(pa), _tsig(pb)
    shared = [t for t in sa if t in sb]
    a_only = [t for t in sa if t not in sb]
    b_only = [t for t in sb if t not in sa]

    if as_json:
        out = {
            "a": {"card_id": cc_a.card_id, "rarity": cc_a.rarity,
                  "element": pa.get("element"),
                  "atk": pa.get("atk", 0), "def": pa.get("def", 0),
                  "hp": pa.get("hp", 0), "spd": pa.get("spd", 0)},
            "b": {"card_id": cc_b.card_id, "rarity": cc_b.rarity,
                  "element": pb.get("element"),
                  "atk": pb.get("atk", 0), "def": pb.get("def", 0),
                  "hp": pb.get("hp", 0), "spd": pb.get("spd", 0)},
            "diff": {k: {"a": pa.get(k, 0), "b": pb.get(k, 0),
                         "delta": pb.get(k, 0) - pa.get(k, 0)}
                     for k in ("atk", "def", "hp", "spd")},
            "trigger_diff": {
                "a_only": [{"when": w, "op": o, "target": t, "value": v}
                           for w, o, t, v in a_only],
                "b_only": [{"when": w, "op": o, "target": t, "value": v}
                           for w, o, t, v in b_only],
                "shared": [{"when": w, "op": o, "target": t, "value": v}
                           for w, o, t, v in shared],
            },
        }
        click.echo(_json.dumps(out, indent=2))
        return

    def _stat_line(label, ka, kb):
        delta = kb - ka
        sign = "+" if delta > 0 else ""
        return f"  {label:7s}  {ka:>4}  vs  {kb:>4}    ({sign}{delta})"

    click.echo(f"{cc_a.card_id}  vs  {cc_b.card_id}    "
               f"({cc_a.rarity} vs {cc_b.rarity})")
    click.echo()
    click.echo(_stat_line("atk", pa.get("atk", 0), pb.get("atk", 0)))
    click.echo(_stat_line("def", pa.get("def", 0), pb.get("def", 0)))
    click.echo(_stat_line("hp",  pa.get("hp", 0),  pb.get("hp", 0)))
    click.echo(_stat_line("spd", pa.get("spd", 0), pb.get("spd", 0)))
    click.echo(f"  element  {pa.get('element', '?'):>4}  vs  {pb.get('element', '?'):>4}")
    click.echo()
    if shared:
        click.echo(f"shared triggers ({len(shared)}):")
        for w, o, t, v in shared:
            click.echo(f"  - {w} {o} {t} {v}")
    if a_only:
        click.echo(f"\n{cc_a.card_id} only ({len(a_only)}):")
        for w, o, t, v in a_only:
            click.echo(f"  - {w} {o} {t} {v}")
    if b_only:
        click.echo(f"\n{cc_b.card_id} only ({len(b_only)}):")
        for w, o, t, v in b_only:
            click.echo(f"  - {w} {o} {t} {v}")


# ---------------------------------------------------------------------------
# `daimon loadout` — saved-team CRUD (mirrors dm_loadout_* MCP tools)
# ---------------------------------------------------------------------------

# Same path the MCP tools use — single source of truth lives in mcp.server.
# Re-import here at use time (lazy) to avoid pulling in the whole MCP module
# at CLI startup.

@main.group("loadout")
def loadout_group() -> None:
    """Save / load / list / validate / scaffold loadouts.

    \b
    Subcommands:
      daimon loadout list                  list saved loadouts
      daimon loadout save <path> <name>    save a file under a name
      daimon loadout load <name>           print a saved loadout JSON
      daimon loadout validate <path>       check a file's shape + cards
      daimon loadout new                   print a starter showcase template

    Saved loadouts live at ``~/.config/daimon/loadouts/<name>.json`` and are
    addressable by name in ``daimon loadout load`` / future arena flows.
    Names are restricted to ``[A-Za-z0-9_-]`` (1-48 chars).
    """


def _loadouts_dir() -> "Path":
    """Return the canonical loadouts directory (mirrors mcp.server)."""
    from pathlib import Path

    from daimon.identity.keys import CONFIG_DIR
    return Path(CONFIG_DIR) / "loadouts"


@loadout_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def loadout_list(as_json: bool) -> None:
    """List saved loadouts on this machine."""
    import json as _json

    d = _loadouts_dir()
    if not d.exists():
        if as_json:
            click.echo(_json.dumps({"loadouts": [], "count": 0}))
        else:
            click.echo("(no saved loadouts — try `daimon loadout save <file> <name>`)")
        return

    rows = []
    for entry in sorted(d.iterdir()):
        if not entry.is_file() or entry.suffix != ".json":
            continue
        name = entry.stem
        try:
            doc = _json.loads(entry.read_text(encoding="utf-8"))
            cards = doc.get("cards", [])
            if not isinstance(cards, list):
                raise ValueError("cards not a list")
            rows.append({"name": name, "card_count": len(cards),
                         "path": str(entry), "mtime": entry.stat().st_mtime})
        except Exception as e:  # noqa: BLE001
            rows.append({"name": name, "corrupt": True, "message": str(e),
                         "path": str(entry)})

    if as_json:
        click.echo(_json.dumps({"loadouts": rows, "count": len(rows)}, indent=2))
        return

    if not rows:
        click.echo("(no saved loadouts — try `daimon loadout save <file> <name>`)")
        return
    click.echo(f"{len(rows)} saved loadout(s):\n")
    for r in rows:
        if r.get("corrupt"):
            click.echo(f"  {r['name']:24s}  CORRUPT  {r['message']}")
            continue
        click.echo(f"  {r['name']:24s}  {r['card_count']} cards  {r['path']}")


@loadout_group.command("save")
@click.argument("loadout_path")
@click.argument("name")
def loadout_save(loadout_path: str, name: str) -> None:
    """Save a loadout file under ``name`` for future use.

    The file is validated through the unified loader (accepts bare list,
    {"cards":[...]}, or showcase format) before being written. Showcase
    files are resolved through the catalog and saved as the full
    stat-block form so subsequent ``daimon match`` calls don't need the
    catalog at runtime.

    Names are restricted to ``[A-Za-z0-9_-]`` (1-48 chars).
    """
    import json as _json

    from daimon.loadouts import load_loadout_file
    from daimon.mcp.server import _validate_loadout_name

    try:
        safe_name = _validate_loadout_name(name)
    except ValueError as e:
        click.echo(f"error: invalid name: {e}", err=True)
        sys.exit(1)

    try:
        _lo, raw = load_loadout_file(loadout_path)
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    d = _loadouts_dir()
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{safe_name}.json"
    overwrote = target.exists()
    doc = {"name": safe_name, "cards": raw}
    target.write_text(_json.dumps(doc, indent=2), encoding="utf-8")

    click.echo(f"saved:    {safe_name}  ({len(raw)} cards) → {target}")
    if overwrote:
        click.echo("note:     overwrote an existing loadout with the same name.")


@loadout_group.command("load")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def loadout_load(name: str, as_json: bool) -> None:
    """Print a saved loadout's contents (full JSON in --json mode)."""
    import json as _json

    from daimon.mcp.server import _validate_loadout_name

    try:
        safe_name = _validate_loadout_name(name)
    except ValueError as e:
        click.echo(f"error: invalid name: {e}", err=True)
        sys.exit(1)

    target = _loadouts_dir() / f"{safe_name}.json"
    if not target.is_file():
        click.echo(f"error: unknown loadout {safe_name!r}", err=True)
        click.echo("hint: run `daimon loadout list` to see saved loadouts.", err=True)
        sys.exit(1)

    try:
        doc = _json.loads(target.read_text(encoding="utf-8"))
    except _json.JSONDecodeError as e:
        click.echo(f"error: corrupt loadout file: {e}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(_json.dumps(doc, indent=2))
        return

    cards = doc.get("cards", [])
    click.echo(f"name:        {safe_name}")
    click.echo(f"path:        {target}")
    click.echo(f"card count:  {len(cards)}")
    click.echo()
    for i, c in enumerate(cards):
        if isinstance(c, dict):
            cid = c.get("card_id", "?")
            sp = c.get("species", "?")
            click.echo(f"  [{i}] {cid:28s}  ({sp})")
        else:
            click.echo(f"  [{i}] {c!r}")


@loadout_group.command("validate")
@click.argument("loadout_path")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def loadout_validate(loadout_path: str, as_json: bool) -> None:
    """Check that a loadout file parses + resolves without errors."""
    import json as _json

    from daimon.loadouts import load_loadout_file

    try:
        lo, raw = load_loadout_file(loadout_path)
    except (FileNotFoundError, ValueError) as e:
        if as_json:
            click.echo(_json.dumps({"valid": False, "error": str(e)}))
        else:
            click.echo(f"INVALID: {e}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(_json.dumps({
            "valid": True,
            "card_count": len(lo.cards),
            "cards": [{"card_id": c.card_id, "species": c.species,
                       "element": c.element.name}
                      for c in lo.cards],
        }, indent=2))
        return

    click.echo(f"VALID: {len(lo.cards)} cards loaded from {loadout_path}")
    for i, c in enumerate(lo.cards):
        click.echo(f"  [{i}] {c.card_id:28s}  ({c.species}, {c.element.name})")


@loadout_group.command("new")
@click.option("--out", default=None,
              help="Write the template to this path instead of stdout.")
@click.option("--catalog", default=None,
              help="Catalog id to draw card_ids from (default: v1_alpha).")
def loadout_new(out: str | None, catalog: str | None) -> None:
    """Print a starter loadout template (showcase format).

    Picks the first 6 catalog cards as a placeholder skeleton — edit the
    ``loadout`` array with your chosen ``card_id``\\s, then save with::

        daimon loadout save <file> <name>

    or play directly::

        daimon match-npc <file> sparring_sam
    """
    import json as _json
    from pathlib import Path

    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog

    cid = catalog or DEFAULT_CATALOG_ID
    try:
        cat = load_catalog(cid)
    except FileNotFoundError:
        click.echo(f"error: unknown catalog {cid!r}", err=True)
        sys.exit(1)

    if len(cat.cards) < 6:
        click.echo(f"error: catalog {cid!r} has fewer than 6 cards "
                   f"({len(cat.cards)}); cannot scaffold a loadout.", err=True)
        sys.exit(1)

    # Take the first 6 cards in catalog order — a placeholder, not balanced.
    template = {
        "loadout_id": "my_loadout",
        "name": "My Loadout",
        "demonstrates": "",
        "flavor": "edit me",
        "description": "Replace card_ids with your chosen team. "
                       "Run `daimon catalog list` to browse options.",
        "loadout": [cc.card_id for cc in cat.cards[:6]],
    }
    text = _json.dumps(template, indent=2) + "\n"

    if out:
        target = Path(out)
        if target.exists():
            click.echo(f"error: {target} already exists; refusing to overwrite",
                       err=True)
            sys.exit(1)
        target.write_text(text, encoding="utf-8")
        click.echo(f"wrote starter template to {target}")
        click.echo("next: edit the `loadout` array, then run "
                   "`daimon loadout validate` and `daimon match-npc`.")
        return

    click.echo(text, nl=False)


if __name__ == "__main__":
    main()
