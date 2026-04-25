"""Kitty Graphics Protocol encoder.

DAIMON ships its own WezTerm and uses KGP for pixel-perfect card-art
rendering. This module is the encoder: PIL.Image → escape-sequence
string the terminal renders as a real bitmap.

KGP basics (https://sw.kovidgoyal.net/kitty/graphics-protocol/):

  * Each transmission is wrapped in ``\\x1b_G<control>;<payload>\\x1b\\\\``
    (the APC sequence). ``control`` is comma-separated key=value pairs;
    ``payload`` is base64-encoded image bytes (optionally zlib-compressed).
  * For payloads larger than ~4 KB we chunk: every chunk except the last
    sets ``m=1`` (more chunks); the last sets ``m=0``. The first chunk
    carries the full control header; subsequent chunks carry only ``m=N``
    plus their slice of the payload.
  * Control keys we use:
      a=T   transmit AND display in one round-trip
      f=32  payload format = 32-bit RGBA (alpha is required for legendary
            gold-frame alpha + selection halos)
      s,v   source pixel width / height
      c,r   display in N cells wide / M cells tall (terminal scales to fit)
      i=N   image id (so we can update the same tile in place; also lets
            the terminal coalesce duplicate images)
      o=z   payload is zlib-compressed (saves ~70% over raw RGBA for art)
      q=2   suppress all responses (we don't read terminal responses, so
            the chatter is just bytes burned)
  * Cursor positioning is OUTSIDE the KGP sequence — emit a CSI
    ``\\x1b[<row>;<col>H`` to put the cursor where you want the image
    to land, THEN emit the KGP transmission.

Why not f=24 (RGB)? Cards have alpha (legendary gold borders, selection
halos), and the bandwidth saving is marginal once zlib compresses the
all-zero alpha channel of an opaque PNG.

Why explicit image IDs instead of letting the terminal allocate? In an
animated TUI we want stable IDs so a redraw of the same tile reuses the
already-uploaded image instead of re-transmitting it. The renderer hands
out IDs by hashing (card_id, skin_slug) so the same card always gets the
same ID across the session.

Why suppress responses (q=2)? A live TUI doesn't have an event loop
waiting for terminal acks; the response packets just clutter our keyboard
input stream. KGP supports q=1 (errors only) but the small bandwidth saving
isn't worth the extra parsing in our render path.
"""

from __future__ import annotations

import base64
import hashlib
import io
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from PIL import Image


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Per-chunk base64 payload size. KGP spec recommends 4096 max; some terminals
# accept larger but 4096 is the universal safe value.
CHUNK_BYTES = 4096

# Compression level: 6 is zlib's default; 9 squeezes ~5% more for ~3x CPU.
# Tile sends are chunky (1 KB-ish per card after compression) so default is fine.
ZLIB_LEVEL = 6

# Reserved image ID range for DAIMON renderers — anything below 1 is
# reserved by some terminals; we start at 1000 to leave headroom for
# user / other-app images. Each card_id+skin gets one ID hashed into [1000, 1<<24).
ID_BASE = 1000
ID_RANGE = (1 << 24) - ID_BASE  # KGP spec: id is u32 but practical max ~2^24


# ---------------------------------------------------------------------------
# ID assignment
# ---------------------------------------------------------------------------


def image_id_for(card_id: str, *, skin_slug: Optional[str] = None) -> int:
    """Stable per-(card_id, skin) image ID in the range [ID_BASE, 2^24).

    Same input → same output, every time. Uses sha256 truncated so
    collisions are negligible (~1 in 16M for 200 cards, irrelevant in
    practice). Caller hands the ID back to KGP as ``i=<N>`` so a redraw
    of the same tile reuses the previously-uploaded image instead of
    re-transmitting it.
    """
    key = f"{card_id}|{skin_slug or ''}"
    h = hashlib.sha256(key.encode("utf-8")).digest()
    n = int.from_bytes(h[:4], "big")
    return ID_BASE + (n % ID_RANGE)


# ---------------------------------------------------------------------------
# Cursor positioning
# ---------------------------------------------------------------------------


def cursor_position(row: int, col: int) -> str:
    """Returns the CSI ``CUP`` escape to move cursor to (row, col).

    Row + col are 1-indexed per ANSI convention (terminal 0,0 → CSI 1;1H).
    Caller should emit this BEFORE the KGP transmission so the image
    lands at the right cell.
    """
    return f"\x1b[{int(row)};{int(col)}H"


def save_cursor() -> str:
    """CSI ``DECSC`` — save cursor position + attributes."""
    return "\x1b[s"


def restore_cursor() -> str:
    """CSI ``DECRC`` — restore cursor position + attributes."""
    return "\x1b[u"


# ---------------------------------------------------------------------------
# Transmission builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KGPDisplaySpec:
    """How a transmission should be rendered.

    cells_w / cells_h: target display size in terminal cells. KGP scales
        the image to fit. If 0, the terminal sizes from the source pixels
        (1 cell ≈ font_size px which is unreliable cross-terminal — always
        set explicit cells for layout-stable rendering).
    image_id: stable ID for this image. See :func:`image_id_for`.
    z_index: optional Z-order for overlapping images (-N = behind, +N =
        above). Defaults to 0; set positive to layer overlays on top of
        previously-sent images.
    placement_id: when displaying an already-uploaded image multiple times
        (e.g. the same card art shown in shop AND collection in the same
        session), distinct placement_ids let the terminal track each
        instance separately. None = single placement.
    """
    cells_w: int
    cells_h: int
    image_id: int
    z_index: int = 0
    placement_id: Optional[int] = None


def encode_transmit_and_display(img: Image.Image, spec: KGPDisplaySpec) -> str:
    """Encode a PIL image as a KGP transmit-and-display escape sequence.

    Result is ready to write to a TTY (with cursor already positioned).
    Handles chunking transparently — large images produce multiple APC
    sequences in one returned string.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    raw = img.tobytes()  # row-major RGBA
    compressed = zlib.compress(raw, ZLIB_LEVEL)
    payload_b64 = base64.standard_b64encode(compressed).decode("ascii")

    base_ctrl = (
        f"a=T,"
        f"f=32,"
        f"s={img.width},"
        f"v={img.height},"
        f"c={spec.cells_w},"
        f"r={spec.cells_h},"
        f"i={spec.image_id},"
        f"o=z,"
        f"q=2,"
        f"z={spec.z_index}"
    )
    if spec.placement_id is not None:
        base_ctrl += f",p={spec.placement_id}"

    return _frame_chunks(base_ctrl, payload_b64)


def encode_display_existing(spec: KGPDisplaySpec) -> str:
    """Display an already-uploaded image (action=p, no payload).

    Used after an initial ``encode_transmit_and_display`` to redraw the
    same image at a new cursor position WITHOUT re-uploading the bitmap.
    Saves ~1 KB per redraw — important for animation smoothness.
    """
    parts = [
        "a=p",
        f"i={spec.image_id}",
        f"c={spec.cells_w}",
        f"r={spec.cells_h}",
        f"z={spec.z_index}",
        "q=2",
    ]
    if spec.placement_id is not None:
        parts.append(f"p={spec.placement_id}")
    ctrl = ",".join(parts)
    return f"\x1b_G{ctrl}\x1b\\"


def encode_delete(image_id: int, *, free_storage: bool = False) -> str:
    """Erase a previously-displayed image from the screen.

    With ``free_storage=False`` (default), the bitmap stays cached in the
    terminal so a future display call can reuse it. With ``True`` (action
    code ``I``), the terminal also frees its copy of the bitmap.
    """
    code = "I" if free_storage else "i"
    return f"\x1b_Ga=d,d={code},i={image_id},q=2\x1b\\"


def encode_clear_all(*, free_storage: bool = False) -> str:
    """Erase ALL images currently visible (helpful between TUI screens)."""
    code = "A" if free_storage else "a"
    return f"\x1b_Ga=d,d={code},q=2\x1b\\"


# ---------------------------------------------------------------------------
# High-level convenience
# ---------------------------------------------------------------------------


def render_card_art(image_path: Union[str, Path], *,
                    card_id: str,
                    skin_slug: Optional[str],
                    cells_w: int,
                    cells_h: int,
                    cursor_row: int,
                    cursor_col: int,
                    z_index: int = 0) -> str:
    """One-call helper: load PIL image + position cursor + emit KGP transmit.

    Returns the full escape string ready to write to stdout.
    """
    img = Image.open(image_path)
    spec = KGPDisplaySpec(
        cells_w=cells_w,
        cells_h=cells_h,
        image_id=image_id_for(card_id, skin_slug=skin_slug),
        z_index=z_index,
    )
    return cursor_position(cursor_row, cursor_col) + encode_transmit_and_display(img, spec)


# ---------------------------------------------------------------------------
# Internal: framing + chunking
# ---------------------------------------------------------------------------


def _frame_chunks(base_ctrl: str, payload_b64: str) -> str:
    """Wrap a base64 payload into one-or-more KGP APC sequences.

    Single-chunk payloads emit one APC with full control + ``m=0``.
    Multi-chunk payloads emit:
       chunk 0: full control + ``m=1`` + first slice
       chunks 1..N-1: ``m=1`` only + slice
       chunk N: ``m=0`` only + last slice

    The control header on continuation chunks is intentionally minimal
    per spec (only ``m`` is required); this keeps the wire chatter small
    on chunky images.
    """
    if len(payload_b64) <= CHUNK_BYTES:
        return f"\x1b_G{base_ctrl},m=0;{payload_b64}\x1b\\"

    parts: list[str] = []
    chunks = [payload_b64[i:i + CHUNK_BYTES]
              for i in range(0, len(payload_b64), CHUNK_BYTES)]

    # First chunk carries the full control header.
    parts.append(f"\x1b_G{base_ctrl},m=1;{chunks[0]}\x1b\\")
    # Middle chunks carry only m=1.
    for c in chunks[1:-1]:
        parts.append(f"\x1b_Gm=1;{c}\x1b\\")
    # Last chunk carries m=0 to flush.
    parts.append(f"\x1b_Gm=0;{chunks[-1]}\x1b\\")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Decoder (round-trip used by tests; not part of the live render path)
# ---------------------------------------------------------------------------


@dataclass
class DecodedTransmission:
    """Result of round-tripping an encoded KGP stream — for tests."""
    control: dict
    payload_raw: bytes
    image: Optional[Image.Image]


def decode_transmission(encoded: str) -> DecodedTransmission:
    """Parse one KGP transmission back into control dict + raw payload.

    Used by ``test_kgp.py`` to verify the encoder round-trips. Reassembles
    chunks (``m=1`` ... ``m=0``), zlib-decompresses, then rebuilds the PIL
    image from the control's stated size + format.
    """
    # Split into APC frames.
    frames: list[str] = []
    i = 0
    while True:
        start = encoded.find("\x1b_G", i)
        if start < 0:
            break
        end = encoded.find("\x1b\\", start)
        if end < 0:
            raise ValueError("unterminated KGP frame")
        frames.append(encoded[start + 3:end])  # strip \x1b_G prefix and \x1b\\ suffix
        i = end + 2

    if not frames:
        raise ValueError("no KGP frames found")

    control: dict = {}
    payload_chunks: list[str] = []

    for frame in frames:
        # Each frame is "<ctrl>;<payload>" or just "<ctrl>" if no payload.
        sep = frame.find(";")
        if sep < 0:
            ctrl_str, payload = frame, ""
        else:
            ctrl_str, payload = frame[:sep], frame[sep + 1:]

        ctrl = _parse_control(ctrl_str)
        if not control:
            # First frame carries the canonical control header.
            control = dict(ctrl)
        else:
            # Continuation frames merge only m= (other keys are absent per spec).
            if "m" in ctrl:
                control["m"] = ctrl["m"]
        payload_chunks.append(payload)

    payload_b64 = "".join(payload_chunks)
    raw = base64.standard_b64decode(payload_b64) if payload_b64 else b""

    # Decompress if needed.
    if control.get("o") == "z" and raw:
        raw = zlib.decompress(raw)

    img: Optional[Image.Image] = None
    fmt = control.get("f")
    s = int(control.get("s", 0) or 0)
    v = int(control.get("v", 0) or 0)
    if fmt == "32" and s and v and len(raw) == s * v * 4:
        img = Image.frombytes("RGBA", (s, v), raw)
    elif fmt == "24" and s and v and len(raw) == s * v * 3:
        img = Image.frombytes("RGB", (s, v), raw)

    return DecodedTransmission(control=control, payload_raw=raw, image=img)


def _parse_control(ctrl_str: str) -> dict:
    """Parse ``a=T,f=32,...`` into a dict (values stay as strings)."""
    out: dict = {}
    for kv in ctrl_str.split(","):
        kv = kv.strip()
        if not kv or "=" not in kv:
            continue
        k, _, v = kv.partition("=")
        out[k] = v
    return out
