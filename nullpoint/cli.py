"""nullpoint CLI — `np init`, `np match`, `np mine`, `np pull`.

V1 alpha: only `init`, `version`, `verify` are wired up. The rest are stubs
that print a "not yet implemented" message and exit nonzero, so scripts can
detect feature gaps.
"""

from __future__ import annotations

import sys

import click

from nullpoint import __version__


@click.group()
@click.version_option(__version__, prog_name="nullpoint")
def main() -> None:
    """NULLPOINT — agentic-first autobattler."""


@main.command()
@click.option("--force", is_flag=True, help="Overwrite existing identity (DESTRUCTIVE).")
def init(force: bool) -> None:
    """Generate a fresh ed25519 identity + 24-word recovery mnemonic."""
    from nullpoint.identity import generate_identity

    try:
        identity = generate_identity(force=force)
    except FileExistsError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    click.echo("Identity generated.\n")
    click.echo(f"  pubkey:  {identity.pubkey_hex}")
    click.echo(f"  stored:  ~/.config/nullpoint/identity.key (mode 0600)\n")
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
    """Print this machine's NULLPOINT public key."""
    from nullpoint.identity import load_identity
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
    """Resolve a match between two loadout JSON files. (V1 alpha — basic output.)"""
    import json
    import os

    from nullpoint.cards import load_card_dict
    from nullpoint.engine import Loadout, resolve_match

    def load_lo(path: str) -> Loadout:
        data = json.loads(open(path).read())
        cards = tuple(load_card_dict(d) for d in data["cards"])
        return Loadout(cards=cards)

    a = load_lo(loadout_a)
    b = load_lo(loadout_b)
    seed_bytes = bytes.fromhex(seed) if seed else os.urandom(32)
    result = resolve_match(a, b, seed_bytes)

    click.echo(f"seed:    {seed_bytes.hex()}")
    click.echo(f"winner:  {result.winner if result.winner is not None else 'draw'}")
    click.echo(f"reason:  {result.reason}")
    click.echo(f"hp_a:    {result.side_a_final_hp}")
    click.echo(f"hp_b:    {result.side_b_final_hp}")
    click.echo(f"rounds:  {len(result.rounds)}")


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
        from nullpoint.cards import load_card_dict
        from nullpoint.render import render_hybrid, render_info_from_pack_dict
        card = load_card_dict(pack)
        art_dir = Path(art_root) if art_root else p.parent
        info = render_info_from_pack_dict(pack, art_dir)
        click.echo(render_hybrid(card, info, tier=tier))
        return

    from nullpoint.render import compose_card_from_pack_dict
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

    from nullpoint.mining import get_recent_entries, get_stats, verify_ledger
    from nullpoint.mining.ledger import LEDGER_PATH as _LP

    if not _LP.exists():
        if as_json:
            click.echo(_json.dumps({"balance": 0, "ledger_entries": 0,
                                    "verified": True, "recent": []}))
        else:
            click.echo("balance:        0")
            click.echo("ledger:         (empty — no productive work recorded yet)")
            click.echo("hint:           run `np mine install-hook` to start mining")
        return

    stats = get_stats()
    verification = verify_ledger()
    recent = get_recent_entries(limit=10)

    if as_json:
        click.echo(_json.dumps({
            "balance": stats.balance,
            "total_mined": stats.total_mined,
            "total_pulled": stats.total_pulled,
            "mine_count": stats.mine_count,
            "pull_count": stats.pull_count,
            "ledger_entries": stats.entry_count,
            "verified": verification.get("ok"),
            "errors": verification.get("errors", []),
            "recent": [{k: v for k, v in e.items()
                        if k in ("ts", "kind", "amount", "tool_name",
                                 "card_id", "rarity")}
                       for e in recent],
        }, indent=2))
        return

    click.echo(f"balance:        {stats.balance}")
    click.echo(f"total mined:    {stats.total_mined}  ({stats.mine_count} events)")
    click.echo(f"total pulled:   {stats.total_pulled}  ({stats.pull_count} events)")
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
            label = e.get("tool_name") or e.get("card_id") or ""
            click.echo(f"  {e.get('ts', '')[:19]}  {kind:8} {amount:+5}  {label}")


@mine.command("install-hook")
@click.option("--settings", default=None, help="Override settings.json path.")
@click.option("--dry-run", is_flag=True, help="Show what would change.")
def mine_install_hook(settings: str | None, dry_run: bool) -> None:
    """Register the NULLPOINT PostToolUse hook in Claude Code settings."""
    from pathlib import Path
    from nullpoint.mining.installer import (
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
                   "currency.\nRun `np mine status` to inspect the ledger.")


@mine.command("uninstall-hook")
@click.option("--settings", default=None, help="Override settings.json path.")
@click.option("--dry-run", is_flag=True, help="Show what would change.")
def mine_uninstall_hook(settings: str | None, dry_run: bool) -> None:
    """Remove the NULLPOINT PostToolUse hook from Claude Code settings."""
    from pathlib import Path
    from nullpoint.mining.installer import (
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
    from nullpoint.mining.hook import main as _hook_main

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

    from nullpoint.catalog import DEFAULT_CATALOG_ID
    from nullpoint.mining.ledger import InsufficientBalanceError
    from nullpoint.pulls import perform_pull

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
        click.echo("error: no identity. Run `np init` first.", err=True)
        sys.exit(1)
    except InsufficientBalanceError as e:
        click.echo(f"error: {e}", err=True)
        click.echo("hint: run `np mine status` to see your balance.", err=True)
        sys.exit(2)
    except RuntimeError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(3)

    if as_json:
        click.echo(_json.dumps(receipt.to_dict(), indent=2))
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


if __name__ == "__main__":
    main()
