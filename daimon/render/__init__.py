"""Render layer — PIL card composer + Kitty Graphics Protocol encoder.

Two primary entry points for callers outside this package:

  compose_card(card, info, output_path)
      PIL-based PNG card renderer. Produces a 280×392 frame with rarity-driven
      palette + supersample. Used for sharing on the web, Telegram, etc.

  kgp.encode_transmit_and_display(...)  (and the helpers in :mod:`daimon.render.kgp`)
      Terminal renderer. Streams full-fidelity PNG bytes to the bundled
      WezTerm via Kitty Graphics Protocol APC sequences. Replaces the
      legacy chafa cascade / hybrid renderer that was retired in Phase E.

The legacy proof scripts that locked the design language are preserved at
scripts/render_legacy/ for reference.
"""

from daimon.render.art import art_path_for
from daimon.render.compose import (
    FRAMES_PER_RARITY,
    CardRenderInfo,
    Palette,
    compose_card,
    compose_card_frames,
    compose_card_from_pack_dict,
    palette_for,
    render_info_from_pack_dict,
)

__all__ = [
    "CardRenderInfo",
    "FRAMES_PER_RARITY",
    "Palette",
    "art_path_for",
    "compose_card",
    "compose_card_frames",
    "compose_card_from_pack_dict",
    "palette_for",
    "render_info_from_pack_dict",
]
