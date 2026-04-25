"""Tests for the KGP overlay painter (daimon.play.art_render.paint_overlays_as_kgp).

The painter is the second-pass complement to ``RenderMode.OVERLAY_ONLY``:
the tile composer leaves blank cells where the art goes, this paints
real bitmaps into those cells via Kitty Graphics Protocol escapes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from daimon.play.art_render import paint_overlays_as_kgp
from daimon.play.screenshot import ImageOverlay
from daimon.render import kgp


@pytest.fixture
def tiny_png(tmp_path) -> Path:
    """A 16×16 RGBA solid-blue PNG — small enough for fast tests, real PIL bytes."""
    img = Image.new("RGBA", (16, 16), (40, 80, 200, 255))
    out = tmp_path / "tile.png"
    img.save(out)
    return out


@pytest.fixture
def second_png(tmp_path) -> Path:
    img = Image.new("RGBA", (16, 16), (200, 80, 40, 255))
    out = tmp_path / "tile2.png"
    img.save(out)
    return out


# ---------------------------------------------------------------------------
# Empty / degenerate input
# ---------------------------------------------------------------------------


def test_paint_overlays_empty_list_returns_empty_string():
    assert paint_overlays_as_kgp([]) == ""


def test_paint_overlays_skips_overlays_with_no_image_path(tmp_path):
    """Empty-slot tiles emit overlays with image_path=None — they must be no-ops."""
    # Using a duck-typed object since ImageOverlay requires image_path: Path.
    class _NoImg:
        image_path = None
        row = 1
        col = 1
        rows = 4
        cols = 8
    out = paint_overlays_as_kgp([_NoImg()])
    assert out == ""


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_paint_overlays_wraps_in_save_restore_cursor(tiny_png):
    overlays = [ImageOverlay(row=2, col=3, rows=4, cols=6, image_path=tiny_png)]
    out = paint_overlays_as_kgp(overlays)
    assert out.startswith(kgp.save_cursor())
    assert out.endswith(kgp.restore_cursor())


def test_paint_overlays_emits_one_kgp_block_per_overlay(tiny_png, second_png):
    overlays = [
        ImageOverlay(row=2, col=3, rows=4, cols=6, image_path=tiny_png),
        ImageOverlay(row=10, col=20, rows=4, cols=6, image_path=second_png),
    ]
    out = paint_overlays_as_kgp(overlays)
    # Each overlay produces at least one APC frame ("\x1b_G ... \x1b\\").
    n_frames = out.count("\x1b_G")
    assert n_frames >= 2


def test_paint_overlays_inserts_cursor_position_per_overlay(tiny_png):
    overlays = [
        ImageOverlay(row=4, col=8, rows=2, cols=2, image_path=tiny_png),
        ImageOverlay(row=12, col=16, rows=2, cols=2, image_path=tiny_png),
    ]
    out = paint_overlays_as_kgp(overlays)
    # CSI is 1-based, so row=4/col=8 → "\x1b[5;9H".
    assert "\x1b[5;9H" in out
    assert "\x1b[13;17H" in out


# ---------------------------------------------------------------------------
# Stable image IDs across re-renders
# ---------------------------------------------------------------------------


def test_paint_overlays_uses_stable_image_id_for_same_path(tiny_png):
    """Re-painting the same overlay should reuse the same KGP image ID,
    so the terminal can short-circuit the upload."""
    overlay = ImageOverlay(row=2, col=3, rows=4, cols=6, image_path=tiny_png)

    a = paint_overlays_as_kgp([overlay])
    b = paint_overlays_as_kgp([overlay])

    # Decode each round-trip — control header `i=` should match.
    # The painter wraps in save_cursor; strip it so decode finds the APC.
    body_a = a[len(kgp.save_cursor()):-len(kgp.restore_cursor())]
    body_b = b[len(kgp.save_cursor()):-len(kgp.restore_cursor())]
    # The cursor-position prefix from kgp.render_card_art is "\x1b[r;cH" —
    # strip it before decoding the APC payload.
    cursor_csi = "\x1b[3;4H"
    assert body_a.startswith(cursor_csi)
    assert body_b.startswith(cursor_csi)
    decoded_a = kgp.decode_transmission(body_a[len(cursor_csi):])
    decoded_b = kgp.decode_transmission(body_b[len(cursor_csi):])
    assert decoded_a.control["i"] == decoded_b.control["i"]


def test_paint_overlays_different_paths_get_different_ids(tiny_png, second_png):
    o1 = ImageOverlay(row=2, col=3, rows=4, cols=6, image_path=tiny_png)
    o2 = ImageOverlay(row=2, col=3, rows=4, cols=6, image_path=second_png)
    out = paint_overlays_as_kgp([o1, o2])
    # Decode both APC headers; they must carry different image IDs.
    cursor_csi = "\x1b[3;4H"
    body = out[len(kgp.save_cursor()):-len(kgp.restore_cursor())]
    # Two overlays at the same position → two cursor escapes.
    pieces = body.split(cursor_csi)
    # First piece is empty (string starts with cursor_csi), then 2 payloads.
    assert len(pieces) == 3 and pieces[0] == ""
    decoded_1 = kgp.decode_transmission(pieces[1])
    decoded_2 = kgp.decode_transmission(pieces[2])
    assert decoded_1.control["i"] != decoded_2.control["i"]


# ---------------------------------------------------------------------------
# Round-trip: pasted image bytes match the source
# ---------------------------------------------------------------------------


def test_paint_overlays_round_trip_preserves_pixels(tiny_png):
    overlay = ImageOverlay(row=0, col=0, rows=8, cols=8, image_path=tiny_png)
    out = paint_overlays_as_kgp([overlay])
    # Strip the save_cursor wrapper + the cursor position prefix.
    body = out[len(kgp.save_cursor()):-len(kgp.restore_cursor())]
    cursor_csi = "\x1b[1;1H"
    assert body.startswith(cursor_csi)
    apc_payload = body[len(cursor_csi):]
    decoded = kgp.decode_transmission(apc_payload)
    assert decoded.image is not None
    # The painter doesn't resize; image is transmitted at its source dims.
    assert decoded.image.size == (16, 16)


# ---------------------------------------------------------------------------
# clear_first option
# ---------------------------------------------------------------------------


def test_paint_overlays_clear_first_prepends_clear_all(tiny_png):
    overlay = ImageOverlay(row=2, col=3, rows=4, cols=6, image_path=tiny_png)
    out = paint_overlays_as_kgp([overlay], clear_first=True)
    # Clear-all escape ("d=a") must come before the save_cursor + paint.
    clear = kgp.encode_clear_all()
    assert out.startswith(clear)


def test_paint_overlays_clear_first_no_op_when_no_overlays():
    """clear_first must NOT emit anything when there are no overlays —
    we don't want to wipe the screen for nothing."""
    assert paint_overlays_as_kgp([], clear_first=True) == ""


# ---------------------------------------------------------------------------
# terminal_supports_kgp detector
# ---------------------------------------------------------------------------


def test_terminal_supports_kgp_inside_env(monkeypatch):
    from daimon.render.wezterm_bundle import (
        INSIDE_TERMINAL_ENV,
        terminal_supports_kgp,
    )
    monkeypatch.setenv(INSIDE_TERMINAL_ENV, "1")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert terminal_supports_kgp() is True


def test_terminal_supports_kgp_term_program_wezterm(monkeypatch):
    from daimon.render.wezterm_bundle import (
        INSIDE_TERMINAL_ENV,
        terminal_supports_kgp,
    )
    monkeypatch.delenv(INSIDE_TERMINAL_ENV, raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "WezTerm")
    assert terminal_supports_kgp() is True


def test_terminal_supports_kgp_default_false(monkeypatch):
    from daimon.render.wezterm_bundle import (
        INSIDE_TERMINAL_ENV,
        terminal_supports_kgp,
    )
    monkeypatch.delenv(INSIDE_TERMINAL_ENV, raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert terminal_supports_kgp() is False


def test_terminal_supports_kgp_other_term_programs_false(monkeypatch):
    """iTerm2, Apple_Terminal, vscode, etc. don't (reliably) speak KGP."""
    from daimon.render.wezterm_bundle import (
        INSIDE_TERMINAL_ENV,
        terminal_supports_kgp,
    )
    monkeypatch.delenv(INSIDE_TERMINAL_ENV, raising=False)
    for term in ("iTerm.app", "Apple_Terminal", "vscode", "tmux"):
        monkeypatch.setenv("TERM_PROGRAM", term)
        assert terminal_supports_kgp() is False, f"{term} should NOT report KGP"
