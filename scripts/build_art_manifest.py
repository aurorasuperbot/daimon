#!/usr/bin/env python3
"""Build the art-pack release artifacts: manifest + per-card tarballs.

Walks an input directory of source card art (one subdirectory per
card_id, each containing ``base.png``, ``manifest.json``, and an
optional ``variants/*.png`` set), produces:

  out/
    card_<card_id>.tar.gz          # one per card
    card_<card_id>.tar.gz.sha256   # sidecar
    ...
    manifest.json                  # the pack manifest
    manifest.json.sha256           # sidecar

These are exactly the assets the runtime expects to find on a GitHub
Release tagged ``art-v<X>.<Y>`` (see :mod:`daimon.update.manifest` and
:mod:`daimon.update.lazy`). After this script completes, publish them
with::

    gh release create art-v1.0 out/* --repo aurorasuperbot/daimon-cards \\
        --title "art-v1.0" --notes "Initial release"

Stdlib-only so it runs on any Actions runner without ``pip install``
beyond ``daimon-engine`` (which we import for the Manifest data model
+ schema validation, so what the script writes is what the runtime
reads, by construction).

Input layout::

    <input_dir>/
      voltcat_apex/
        base.png
        manifest.json
        variants/
          v0.png
          v1.png
      firewolf_basic/
        base.png
        manifest.json

Usage::

    python scripts/build_art_manifest.py \\
        --input cards-source/ \\
        --output dist/art-v1.0/ \\
        --pack-version art-v1.0 \\
        --asset-base-url \\
            https://github.com/aurorasuperbot/daimon-cards/releases/download/art-v1.0/ \\
        --starter-card-ids voltcat_apex,firewolf_basic,leafsprite_dawn

The starter card list is comma-separated. Pass ``--starter-card-ids-file
ids.txt`` to read newline-separated IDs from a file instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

# Importing from daimon-engine here is intentional: it guarantees
# the manifest we write conforms to the same schema the runtime reads.
# Mismatch becomes impossible by construction.
from daimon.update.manifest import (
    MANIFEST_ASSET_NAME,
    SCHEMA_VERSION,
    CardEntry,
    Manifest,
)
from daimon.update.paths import ART_PACK_NAME


PER_CARD_TARBALL_PREFIX = "card_"


# ---------------------------------------------------------------------------
# Per-card tarball builder
# ---------------------------------------------------------------------------

def _walk_card_files(card_dir: Path) -> list[Path]:
    """Files we ship for one card: base.png, manifest.json, variants/*.png.

    Other files (notes, scratch dirs, .DS_Store) are ignored so the
    tarball has a known, minimal shape. Order is deterministic so
    sha256 is stable across runs.
    """
    out: list[Path] = []
    base = card_dir / "base.png"
    if base.is_file():
        out.append(base)
    cardman = card_dir / "manifest.json"
    if cardman.is_file():
        out.append(cardman)
    variants = card_dir / "variants"
    if variants.is_dir():
        for png in sorted(variants.iterdir()):
            if png.is_file() and png.suffix.lower() == ".png":
                out.append(png)
    return out


def build_card_tarball(card_dir: Path, out_path: Path) -> tuple[int, str]:
    """Produce a flat ``.tar.gz`` for one card.

    Layout inside the tarball mirrors what the runtime extracts to
    ``art/<pack>/<card_id>/`` — flat at the top, no wrapping directory::

        base.png
        manifest.json
        variants/v0.png
        variants/v1.png ...

    Returns ``(size_bytes, sha256_hex)`` of the produced archive. Uses
    deterministic mtime and a fixed user/group so two runs over the
    same source files produce byte-identical tarballs (important for
    incremental updates that compare per-card sha256 across releases).
    """
    files = _walk_card_files(card_dir)
    if not files:
        raise SystemExit(
            f"build_art_manifest: card dir {card_dir} has no shippable files "
            f"(expected base.png and/or manifest.json)"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_path, "w:gz", compresslevel=9) as tf:
        for src in files:
            arc = src.relative_to(card_dir).as_posix()
            info = tf.gettarinfo(name=str(src), arcname=arc)
            # Determinism: zero out the bits that vary across runs.
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with src.open("rb") as fh:
                tf.addfile(info, fh)

    size = out_path.stat().st_size
    digest = _sha256_file(out_path)
    return size, digest


# ---------------------------------------------------------------------------
# sha256 helpers + sidecar writer
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_sha256_sidecar(asset_path: Path) -> Path:
    """Write ``<asset>.sha256`` next to ``asset_path`` in sha256sum format.

    The format ``<hex>  <filename>`` matches what ``sha256sum`` produces
    and what :func:`daimon.update.fetcher.parse_sha256_sidecar` expects.
    """
    digest = _sha256_file(asset_path)
    sidecar = asset_path.with_name(asset_path.name + ".sha256")
    sidecar.write_text(
        f"{digest}  {asset_path.name}\n", encoding="utf-8"
    )
    return sidecar


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def discover_cards(input_dir: Path) -> list[str]:
    """Return sorted list of ``card_id`` directory names under ``input_dir``."""
    if not input_dir.is_dir():
        raise SystemExit(f"build_art_manifest: input dir not found: {input_dir}")
    out = []
    for child in sorted(input_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        out.append(child.name)
    if not out:
        raise SystemExit(
            f"build_art_manifest: input dir {input_dir} has no card subdirectories"
        )
    return out


def parse_starter_ids(
    cli_csv: str | None,
    file_path: Path | None,
    known_ids: set[str],
) -> tuple[str, ...]:
    """Resolve the starter list from --starter-card-ids or --starter-card-ids-file.

    Validates every starter id against the discovered cards so a typo is
    caught at build time, not at runtime when an onboard hits a manifest
    pointing at a card that doesn't exist.
    """
    if cli_csv and file_path:
        raise SystemExit(
            "build_art_manifest: pass at most one of --starter-card-ids / "
            "--starter-card-ids-file (got both)"
        )
    if file_path:
        if not file_path.is_file():
            raise SystemExit(
                f"build_art_manifest: --starter-card-ids-file not found: {file_path}"
            )
        raw = file_path.read_text(encoding="utf-8")
        ids = tuple(line.strip() for line in raw.splitlines() if line.strip())
    elif cli_csv:
        ids = tuple(s.strip() for s in cli_csv.split(",") if s.strip())
    else:
        # Sensible default: first ten cards alphabetically. Operator can
        # override per release with --starter-card-ids[-file].
        ids = tuple(sorted(known_ids))[:10]

    bad = [cid for cid in ids if cid not in known_ids]
    if bad:
        raise SystemExit(
            f"build_art_manifest: starter ids reference unknown cards: "
            f"{', '.join(bad)}"
        )
    return ids


def build_manifest(
    *,
    input_dir: Path,
    output_dir: Path,
    pack_version: str,
    pack_name: str,
    asset_base_url: str,
    starter_card_ids: Iterable[str],
) -> Manifest:
    """Build all per-card tarballs + the manifest. Returns the parsed manifest.

    Side effects:
      * Writes ``card_<card_id>.tar.gz`` + ``.sha256`` sidecar per card
        under ``output_dir``.
      * Writes ``manifest.json`` + ``manifest.json.sha256`` under
        ``output_dir``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    card_ids = discover_cards(input_dir)
    starter = tuple(starter_card_ids)

    cards: dict[str, CardEntry] = {}
    for cid in card_ids:
        asset_name = f"{PER_CARD_TARBALL_PREFIX}{cid}.tar.gz"
        out_tar = output_dir / asset_name
        size, digest = build_card_tarball(input_dir / cid, out_tar)
        write_sha256_sidecar(out_tar)
        cards[cid] = CardEntry(
            asset_name=asset_name,
            sha256=digest,
            size_bytes=size,
        )
        sys.stderr.write(f"  {cid:<32}  {size:>10,} B  {digest[:12]}…\n")

    manifest = Manifest(
        schema_version=SCHEMA_VERSION,
        pack_version=pack_version,
        pack_name=pack_name,
        asset_base_url=asset_base_url,
        starter_card_ids=starter,
        cards=cards,
    )

    manifest_path = output_dir / MANIFEST_ASSET_NAME
    manifest_path.write_text(manifest.to_json() + "\n", encoding="utf-8")
    write_sha256_sidecar(manifest_path)

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/build_art_manifest.py",
        description="Build art-pack release artifacts (manifest + per-card tarballs).",
    )
    parser.add_argument("--input", required=True, type=Path,
                        help="Source directory; one subdir per card_id.")
    parser.add_argument("--output", required=True, type=Path,
                        help="Destination dir for the release assets.")
    parser.add_argument("--pack-version", required=True,
                        help="Release tag, e.g. 'art-v1.0'.")
    parser.add_argument("--pack-name", default=ART_PACK_NAME,
                        help=f"Pack identifier (default: {ART_PACK_NAME}).")
    parser.add_argument("--asset-base-url", required=True,
                        help="Where the runtime will fetch per-card tarballs "
                             "from (e.g. the GitHub Release download URL).")
    parser.add_argument("--starter-card-ids", default=None,
                        help="Comma-separated card_ids to mark as starter "
                             "pack (prefetched at onboard).")
    parser.add_argument("--starter-card-ids-file", default=None, type=Path,
                        help="Newline-separated alternative to "
                             "--starter-card-ids.")
    args = parser.parse_args(argv)

    if not args.pack_version.startswith("art-v"):
        raise SystemExit(
            f"build_art_manifest: --pack-version must look like 'art-vX.Y', "
            f"got {args.pack_version!r}"
        )

    if not args.asset_base_url.endswith("/"):
        # Normalize: the runtime appends asset_name without a separator.
        args.asset_base_url = args.asset_base_url + "/"

    sys.stderr.write(
        f"build_art_manifest: scanning {args.input}\n"
    )
    card_ids = set(discover_cards(args.input))
    starter = parse_starter_ids(
        cli_csv=args.starter_card_ids,
        file_path=args.starter_card_ids_file,
        known_ids=card_ids,
    )
    sys.stderr.write(
        f"build_art_manifest: {len(card_ids)} cards, "
        f"{len(starter)} starters\n"
        f"build_art_manifest: building tarballs into {args.output}\n"
    )

    manifest = build_manifest(
        input_dir=args.input,
        output_dir=args.output,
        pack_version=args.pack_version,
        pack_name=args.pack_name,
        asset_base_url=args.asset_base_url,
        starter_card_ids=starter,
    )

    sys.stderr.write(
        f"build_art_manifest: wrote {manifest.card_count}-card manifest "
        f"for {manifest.pack_version} → {args.output / MANIFEST_ASSET_NAME}\n"
        f"build_art_manifest: ready to publish — "
        f"`gh release create {manifest.pack_version} {args.output}/* "
        f"--repo aurorasuperbot/daimon-cards`\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
