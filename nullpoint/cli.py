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
def mine() -> None:
    """Start the mining daemon. (NOT YET IMPLEMENTED — V1.1.)"""
    click.echo("not yet implemented", err=True)
    sys.exit(2)


@main.command()
def pull() -> None:
    """Spend currency on a gacha pull. (NOT YET IMPLEMENTED — V1.1.)"""
    click.echo("not yet implemented", err=True)
    sys.exit(2)


if __name__ == "__main__":
    main()
