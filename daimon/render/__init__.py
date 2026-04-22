"""Render layer — TUI + 7-tier chafa cascade + hybrid frame/art renderer.

Three primary entry points:

  compose_card(card, info, output_path)
      PIL-based PNG card renderer. Produces a 280×392 frame with rarity-driven
      palette + supersample. Used for sharing on the web, Telegram, etc.

  render_hybrid(card, info, tier=...)
      Terminal renderer. Frame as terminal cells (always crisp), art panel via
      chafa cascade. Default tier auto-detected from terminal capability.

  detect_tier()
      Returns the best tier (1–7) the current terminal supports.

The legacy proof scripts that locked the design language are preserved at
scripts/render_legacy/ for reference.
"""

from daimon.render.cascade import Tier, detect_tier
from daimon.render.compose import (
    CardRenderInfo,
    Palette,
    compose_card,
    compose_card_from_pack_dict,
    palette_for,
    render_info_from_pack_dict,
)
from daimon.render.hybrid import render_hybrid

__all__ = [
    "CardRenderInfo",
    "Palette",
    "Tier",
    "compose_card",
    "compose_card_from_pack_dict",
    "detect_tier",
    "palette_for",
    "render_hybrid",
    "render_info_from_pack_dict",
]
