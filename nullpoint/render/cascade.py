"""7-tier chafa cascade — adaptive terminal art rendering.

Render tier is chosen at runtime from terminal capability:
  T1: kitty/sixel graphics protocol (true pixel art)
  T2: chafa --format symbols --colors full
  T3: chafa --format symbols --colors 256
  T4: chafa --format symbols --colors 16
  T5: chafa --format symbols --colors 8
  T6: chafa --format ascii --colors 8
  T7: ASCII-art fallback (no chafa)

Hybrid rendering: card frame is drawn as terminal cells (always crisp);
ONLY the central art panel uses the cascade. This is the discipline that
keeps card text legible even at T7.

V1 alpha: stub. Full implementation migrates from agent-tcg/scripts/hybrid_render.py.
"""

from __future__ import annotations

import os
import shutil
from typing import Literal

Tier = Literal[1, 2, 3, 4, 5, 6, 7]


def detect_tier() -> Tier:
    """Best-effort terminal capability detection."""
    if not shutil.which("chafa"):
        return 7

    term = os.environ.get("TERM", "")
    term_program = os.environ.get("TERM_PROGRAM", "")
    colorterm = os.environ.get("COLORTERM", "")

    # Kitty graphics protocol
    if "kitty" in term or term_program == "WezTerm":
        return 1
    # Truecolor terminal
    if colorterm in ("truecolor", "24bit"):
        return 2
    # 256-color
    if "256" in term:
        return 3
    return 4
