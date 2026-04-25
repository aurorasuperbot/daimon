"""Tests for the Kitty Graphics Protocol encoder.

Validates:
  * Single-chunk transmission round-trips: encode → decode → identical pixels.
  * Multi-chunk transmission round-trips for payloads > CHUNK_BYTES.
  * Control header carries the right keys (a, f, s, v, c, r, i, o, q).
  * Image ID is stable per (card_id, skin_slug) and within the documented range.
  * Cursor positioning + display-existing + delete escapes are well-formed.
"""

from __future__ import annotations

import base64
import zlib

import pytest
from PIL import Image

from daimon.render import kgp


# ---------------------------------------------------------------------------
# image_id_for
# ---------------------------------------------------------------------------


def test_image_id_is_stable():
    a = kgp.image_id_for("aegis_lion")
    b = kgp.image_id_for("aegis_lion")
    assert a == b


def test_image_id_changes_with_skin():
    base = kgp.image_id_for("aegis_lion")
    skinned = kgp.image_id_for("aegis_lion", skin_slug="ukiyoe_scroll")
    assert base != skinned


def test_image_id_within_documented_range():
    # Sample 200 cards — none should escape [ID_BASE, ID_BASE + ID_RANGE).
    for i in range(200):
        cid = f"card_{i:03d}"
        n = kgp.image_id_for(cid)
        assert kgp.ID_BASE <= n < kgp.ID_BASE + kgp.ID_RANGE


def test_image_id_rare_collisions():
    """200-card collision rate over the truncated 24-bit space is negligible.

    Not a strict guarantee (sha256 prefix collisions exist), but verifies our
    keyspace is wide enough that the demo catalog never collides.
    """
    ids = {kgp.image_id_for(f"card_{i:04d}") for i in range(200)}
    assert len(ids) == 200


# ---------------------------------------------------------------------------
# Cursor escapes
# ---------------------------------------------------------------------------


def test_cursor_position():
    assert kgp.cursor_position(5, 12) == "\x1b[5;12H"


def test_cursor_position_normalises_floats():
    # We ints-coerce defensively so "row=2.0, col=3.0" doesn't blow up.
    assert kgp.cursor_position(2.0, 3.0) == "\x1b[2;3H"


def test_save_restore_cursor():
    assert kgp.save_cursor() == "\x1b[s"
    assert kgp.restore_cursor() == "\x1b[u"


# ---------------------------------------------------------------------------
# encode_transmit_and_display — small image (single chunk)
# ---------------------------------------------------------------------------


def _solid_rgba(w: int, h: int, color=(120, 200, 255, 255)) -> Image.Image:
    img = Image.new("RGBA", (w, h), color)
    return img


def test_single_chunk_round_trip():
    img = _solid_rgba(8, 6)
    spec = kgp.KGPDisplaySpec(cells_w=4, cells_h=3, image_id=1234)
    encoded = kgp.encode_transmit_and_display(img, spec)

    # APC framing.
    assert encoded.startswith("\x1b_G")
    assert encoded.endswith("\x1b\\")

    # Round-trip → identical pixels.
    decoded = kgp.decode_transmission(encoded)
    assert decoded.image is not None
    assert decoded.image.size == (8, 6)
    assert list(decoded.image.getdata()) == list(img.getdata())

    # Control header has the expected keys.
    ctrl = decoded.control
    assert ctrl["a"] == "T"
    assert ctrl["f"] == "32"
    assert ctrl["s"] == "8"
    assert ctrl["v"] == "6"
    assert ctrl["c"] == "4"
    assert ctrl["r"] == "3"
    assert ctrl["i"] == "1234"
    assert ctrl["o"] == "z"
    assert ctrl["q"] == "2"
    # m=0 on the last (only) chunk.
    assert ctrl["m"] == "0"


def test_single_chunk_only_one_apc_frame():
    """Small payloads must NOT chunk — verifies the threshold is respected."""
    img = _solid_rgba(4, 4)
    spec = kgp.KGPDisplaySpec(cells_w=2, cells_h=2, image_id=99)
    encoded = kgp.encode_transmit_and_display(img, spec)
    assert encoded.count("\x1b_G") == 1


# ---------------------------------------------------------------------------
# encode_transmit_and_display — large image (multiple chunks)
# ---------------------------------------------------------------------------


def test_multi_chunk_round_trip():
    """A 256x256 RGBA image (256KB raw) chunks into many frames after compression."""
    # Random-ish pattern so zlib doesn't compress to almost nothing.
    img = Image.new("RGBA", (256, 256))
    px = img.load()
    for y in range(256):
        for x in range(256):
            px[x, y] = ((x * 13) % 256, (y * 17) % 256, (x ^ y) % 256, 255)

    spec = kgp.KGPDisplaySpec(cells_w=64, cells_h=32, image_id=4242)
    encoded = kgp.encode_transmit_and_display(img, spec)

    # Multiple APC frames.
    n_frames = encoded.count("\x1b_G")
    assert n_frames >= 2, f"expected chunking, got {n_frames} frame(s)"

    # First chunk has full control + m=1; intermediate chunks have only m=1;
    # last chunk has m=0.
    frames = encoded.split("\x1b_G")[1:]  # drop empty leader
    # Strip trailing \x1b\\
    frames = [f.rstrip("\x1b\\") for f in frames]
    # First frame contains the canonical control header with f=32.
    assert "f=32" in frames[0]
    assert "m=1" in frames[0]
    # Intermediate frames have only m=N + payload (no f=).
    for mid in frames[1:-1]:
        assert "f=32" not in mid
        assert mid.startswith("m=1;")
    # Last frame has m=0.
    assert "m=0" in frames[-1]

    # Decode round-trip.
    decoded = kgp.decode_transmission(encoded)
    assert decoded.image is not None
    assert decoded.image.size == (256, 256)
    assert list(decoded.image.getdata()) == list(img.getdata())


# ---------------------------------------------------------------------------
# z-index + placement_id
# ---------------------------------------------------------------------------


def test_z_index_in_header():
    img = _solid_rgba(2, 2)
    spec = kgp.KGPDisplaySpec(cells_w=1, cells_h=1, image_id=1, z_index=5)
    encoded = kgp.encode_transmit_and_display(img, spec)
    decoded = kgp.decode_transmission(encoded)
    assert decoded.control["z"] == "5"


def test_placement_id_in_header():
    img = _solid_rgba(2, 2)
    spec = kgp.KGPDisplaySpec(cells_w=1, cells_h=1, image_id=1, placement_id=7)
    encoded = kgp.encode_transmit_and_display(img, spec)
    decoded = kgp.decode_transmission(encoded)
    assert decoded.control["p"] == "7"


def test_placement_id_omitted_by_default():
    img = _solid_rgba(2, 2)
    spec = kgp.KGPDisplaySpec(cells_w=1, cells_h=1, image_id=1)
    encoded = kgp.encode_transmit_and_display(img, spec)
    decoded = kgp.decode_transmission(encoded)
    assert "p" not in decoded.control


# ---------------------------------------------------------------------------
# encode_display_existing — reuse uploaded image
# ---------------------------------------------------------------------------


def test_display_existing_no_payload():
    spec = kgp.KGPDisplaySpec(cells_w=10, cells_h=5, image_id=99)
    encoded = kgp.encode_display_existing(spec)
    assert encoded == "\x1b_Ga=p,i=99,c=10,r=5,z=0,q=2\x1b\\"


def test_display_existing_with_placement():
    spec = kgp.KGPDisplaySpec(cells_w=10, cells_h=5, image_id=99, placement_id=3)
    encoded = kgp.encode_display_existing(spec)
    assert "p=3" in encoded
    assert "i=99" in encoded


# ---------------------------------------------------------------------------
# encode_delete / encode_clear_all
# ---------------------------------------------------------------------------


def test_delete_keeps_storage_by_default():
    enc = kgp.encode_delete(42)
    assert "d=i" in enc      # lowercase = keep cached bitmap
    assert "i=42" in enc
    assert enc.startswith("\x1b_G") and enc.endswith("\x1b\\")


def test_delete_with_free_storage():
    enc = kgp.encode_delete(42, free_storage=True)
    assert "d=I" in enc      # uppercase = also free cached bitmap


def test_clear_all_keeps_storage_by_default():
    enc = kgp.encode_clear_all()
    assert "d=a" in enc


def test_clear_all_with_free_storage():
    enc = kgp.encode_clear_all(free_storage=True)
    assert "d=A" in enc


# ---------------------------------------------------------------------------
# render_card_art convenience helper
# ---------------------------------------------------------------------------


def test_render_card_art_helper(tmp_path):
    src = tmp_path / "card.png"
    _solid_rgba(16, 16, (200, 100, 50, 255)).save(src)

    out = kgp.render_card_art(
        src,
        card_id="aegis_lion",
        skin_slug=None,
        cells_w=8, cells_h=8,
        cursor_row=3, cursor_col=10,
    )
    # Cursor positioning before the KGP transmission.
    assert out.startswith("\x1b[3;10H")
    body = out[len("\x1b[3;10H"):]
    decoded = kgp.decode_transmission(body)
    assert decoded.image is not None
    assert decoded.image.size == (16, 16)
    assert decoded.control["c"] == "8"
    assert decoded.control["r"] == "8"
    # ID matches image_id_for(card, skin).
    assert decoded.control["i"] == str(kgp.image_id_for("aegis_lion"))


# ---------------------------------------------------------------------------
# Compression sanity
# ---------------------------------------------------------------------------


def test_compression_actually_compresses():
    """Solid-colour RGBA should compress dramatically vs raw."""
    img = _solid_rgba(64, 64)
    raw = img.tobytes()
    compressed = zlib.compress(raw, kgp.ZLIB_LEVEL)
    # 64x64 RGBA = 16384 bytes raw; uniform colour squeezes very small.
    assert len(compressed) < 200, (
        f"compression unexpectedly weak: {len(compressed)} bytes for solid art")


def test_decoder_handles_uncompressed_payload():
    """Spec allows omitting o=z; decoder should handle it for forward-compat."""
    img = _solid_rgba(4, 4)
    raw = img.tobytes()
    payload = base64.standard_b64encode(raw).decode("ascii")
    encoded = (
        f"\x1b_Ga=T,f=32,s=4,v=4,c=2,r=2,i=1,m=0;{payload}\x1b\\"
    )
    decoded = kgp.decode_transmission(encoded)
    assert decoded.image is not None
    assert decoded.image.size == (4, 4)
    assert "o" not in decoded.control
