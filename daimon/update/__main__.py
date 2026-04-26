"""Entry point for ``python -m daimon.update``.

Used by ``checker.spawn_background_check`` as the detached subprocess
target. Also runnable interactively for debugging.

Flags:
  --check    Rate-limited check + manifest refresh if newer (default).
  --force    Re-fetch the manifest unconditionally, even if up-to-date.
  --version <tag>
             Install this exact tag (e.g. ``--version art-v1.0``). Honors
             ``DAIMON_PIN_ART`` if not given.

Per-card art is fetched lazily by the runtime; this command only
refreshes the pack manifest. To populate the cache aggressively, see
``daimon prefetch`` (the explicit prefetch command).

Output goes to stderr (caller may have redirected to update.log). Exit
codes:
  0  no-op or success
  1  ArtUpdateError (network, sha mismatch, swap failure)
  2  unexpected exception
"""

from __future__ import annotations

import argparse
import sys
import traceback

from daimon.update.checker import (
    is_check_due,
    update_last_check,
)
from daimon.update.fetcher import ArtUpdateError
from daimon.update.manifest import fetch_manifest
from daimon.update.paths import auto_update_enabled, current_version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m daimon.update",
        description="Check for and install daimon manifest updates.",
    )
    parser.add_argument("--check", action="store_true",
                        help="Honor 24h rate-limit (default).")
    parser.add_argument("--force", action="store_true",
                        help="Bypass rate-limit AND cross-major guard.")
    parser.add_argument("--version", default=None,
                        help="Install this exact tag (e.g. art-v1.0).")
    parser.add_argument("--no-progress", action="store_true",
                        help="Suppress the progress bar (logs only).")
    args = parser.parse_args(argv)

    if not args.force and not auto_update_enabled():
        sys.stderr.write("daimon-update: opted out via DAIMON_NO_AUTO_UPDATE.\n")
        return 0

    if args.check and not args.force and not is_check_due():
        sys.stderr.write("daimon-update: not due (rate-limited).\n")
        return 0

    try:
        before = current_version()
        manifest = fetch_manifest(
            target_version=args.version,
            force=args.force,
            show_progress=not args.no_progress,
        )
        if before == manifest.pack_version:
            update_last_check(latest_seen=manifest.pack_version, action="up_to_date")
            sys.stderr.write(
                f"daimon-update: already up to date ({manifest.pack_version}).\n"
            )
        else:
            update_last_check(latest_seen=manifest.pack_version, action="installed")
            sys.stderr.write(
                f"daimon-update: installed manifest for {manifest.pack_version} "
                f"({manifest.card_count} cards, was: {before or 'none'}).\n"
            )
        return 0
    except ArtUpdateError as e:
        update_last_check(error=str(e), action="update_failed")
        sys.stderr.write(f"daimon-update: ERROR: {e}\n")
        return 1
    except Exception as e:
        update_last_check(error=f"crash: {e}", action="crashed")
        sys.stderr.write(f"daimon-update: CRASH: {e}\n")
        traceback.print_exc(file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
