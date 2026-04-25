"""Per-card art path resolution — equipped-skin aware.

Single source of truth for "given a card_id, what PNG do I render?". Used
by every renderer (compose_card, render_hybrid, the play HUD's art panel)
so a player who equips a skin sees it everywhere — battles, replays, the
collection view, the shop preview.

Resolution order:
  1. ``equipped_skins.json[card_id]`` → if set AND the variant PNG exists
     in the art-pack manifest, return it.
  2. ``manifest.canonical`` → if set AND the variant PNG exists, return it.
  3. ``base.png`` in the card directory — the legacy mirror that older
     compose paths still expect.
  4. ``None`` if nothing is on disk (caller must handle a card with no art,
     e.g. by rendering a placeholder).

The resolver never raises — missing art is a soft fail. The CLI surfaces a
warning when art is absent; the engine doesn't care (it never reads art).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from daimon.shop.equipped import load_equipped
from daimon.update.paths import art_pack_dir


def _read_manifest(card_dir: Path) -> Optional[dict]:
    p = card_dir / "manifest.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _variant_png(card_dir: Path, vid: str) -> Optional[Path]:
    """Return the PNG path for a variant id if it exists, else None."""
    p = card_dir / "variants" / f"{vid}.png"
    return p if p.is_file() else None


def _variant_id_for_slug(manifest: dict, slug: str) -> Optional[str]:
    """Find the variant id whose skin_slug matches `slug` (active only)."""
    for v in manifest.get("variants", []):
        if not isinstance(v, dict):
            continue
        if v.get("skin_slug") == slug and v.get("status") == "active":
            return v.get("id")
    return None


def art_path_for(card_id: str,
                 *,
                 art_root: Optional[Path] = None,
                 equipped_path: Optional[Path] = None) -> Optional[Path]:
    """Resolve the on-disk PNG for a card, honoring the equipped skin.

    Args:
      card_id: card identifier.
      art_root: art-pack dir override (defaults to ``art_pack_dir()``).
      equipped_path: equipped_skins.json override (for tests).

    Returns the absolute path to a PNG, or None if nothing's on disk.
    """
    if art_root is None:
        art_root = art_pack_dir()
    card_dir = art_root / card_id

    # Step 1: equipped skin (if any).
    equipped = load_equipped(equipped_path)
    slug = equipped.get(card_id)
    manifest = _read_manifest(card_dir)
    if slug and manifest:
        vid = _variant_id_for_slug(manifest, slug)
        if vid:
            png = _variant_png(card_dir, vid)
            if png:
                return png
        # Skin equipped but its art is gone — fall through silently. The
        # equipped pointer stays (player may still own it; the art-pack may
        # have just been mid-update).

    # Step 2: manifest canonical.
    if manifest:
        canonical = manifest.get("canonical")
        if canonical:
            png = _variant_png(card_dir, canonical)
            if png:
                return png

    # Step 3: legacy base.png mirror.
    base = card_dir / "base.png"
    if base.is_file():
        return base

    return None
