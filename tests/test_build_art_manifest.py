"""Tests for ``scripts/build_art_manifest.py``.

Validates the build output round-trips through the runtime's
:class:`Manifest` and :func:`fetch_card`/:func:`ensure_art_for` paths
without modification — what the script writes is exactly what the
engine expects to read.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tarfile
from pathlib import Path

import pytest

# Import the build script directly from its file path. It lives under
# scripts/ which isn't on the package path; importlib lets us load it
# as a module without polluting sys.path.
_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "build_art_manifest.py"
_spec = importlib.util.spec_from_file_location("build_art_manifest", _SCRIPT_PATH)
build_art_manifest = importlib.util.module_from_spec(_spec)
sys.modules["build_art_manifest"] = build_art_manifest
_spec.loader.exec_module(build_art_manifest)

from daimon.update.manifest import (
    MANIFEST_ASSET_NAME,
    SCHEMA_VERSION,
    Manifest,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_card_source(parent: Path, card_id: str, *, with_variants: bool = False) -> Path:
    """Build a minimal card source tree under ``parent / card_id /``."""
    d = parent / card_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "base.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(64)))
    (d / "manifest.json").write_text(
        json.dumps({"card_id": card_id, "canonical": "v0"}),
        encoding="utf-8",
    )
    if with_variants:
        (d / "variants").mkdir()
        (d / "variants" / "v0.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        )
        (d / "variants" / "v1.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\xff" * 32
        )
    return d


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    """A sandbox with three cards — two simple, one with variants."""
    src = tmp_path / "cards-source"
    src.mkdir()
    _write_card_source(src, "alpha_card")
    _write_card_source(src, "beta_card")
    _write_card_source(src, "gamma_card", with_variants=True)
    # Hidden + non-card files that must be ignored.
    (src / ".gitignore").write_text("*.tmp")
    (src / ".DS_Store").write_bytes(b"\x00")
    (src / "README.md").write_text("scratch", encoding="utf-8")
    return src


# ---------------------------------------------------------------------------
# discover_cards
# ---------------------------------------------------------------------------

class TestDiscoverCards:
    def test_returns_sorted_card_dirs(self, source_dir: Path):
        ids = build_art_manifest.discover_cards(source_dir)
        assert ids == ["alpha_card", "beta_card", "gamma_card"]

    def test_skips_dotfiles_and_files(self, source_dir: Path):
        ids = build_art_manifest.discover_cards(source_dir)
        assert ".gitignore" not in ids
        assert "README.md" not in ids
        assert ".DS_Store" not in ids

    def test_raises_on_empty_dir(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(SystemExit, match="no card subdirectories"):
            build_art_manifest.discover_cards(empty)

    def test_raises_on_missing_dir(self, tmp_path: Path):
        with pytest.raises(SystemExit, match="input dir not found"):
            build_art_manifest.discover_cards(tmp_path / "nope")


# ---------------------------------------------------------------------------
# build_card_tarball
# ---------------------------------------------------------------------------

class TestBuildCardTarball:
    def test_flat_layout(self, source_dir: Path, tmp_path: Path):
        out = tmp_path / "out" / "card_alpha_card.tar.gz"
        size, digest = build_art_manifest.build_card_tarball(
            source_dir / "alpha_card", out
        )
        assert out.is_file()
        assert size == out.stat().st_size
        assert size > 0
        assert len(digest) == 64

        # Tarball contents are flat (no top-level dir wrapping).
        with tarfile.open(out, "r:gz") as tf:
            names = sorted(m.name for m in tf.getmembers())
        assert names == ["base.png", "manifest.json"]

    def test_includes_variants(self, source_dir: Path, tmp_path: Path):
        out = tmp_path / "out" / "card_gamma_card.tar.gz"
        build_art_manifest.build_card_tarball(source_dir / "gamma_card", out)
        with tarfile.open(out, "r:gz") as tf:
            names = sorted(m.name for m in tf.getmembers())
        assert names == [
            "base.png",
            "manifest.json",
            "variants/v0.png",
            "variants/v1.png",
        ]

    def test_deterministic_sha(self, source_dir: Path, tmp_path: Path):
        """Two builds over the same source produce byte-identical tarballs.

        Important for incremental updates: the manifest diff between
        releases compares per-card sha256, and a flapping mtime would
        force every card to re-fetch on every release.
        """
        out_a = tmp_path / "a" / "card.tar.gz"
        out_b = tmp_path / "b" / "card.tar.gz"
        _, sha_a = build_art_manifest.build_card_tarball(
            source_dir / "alpha_card", out_a
        )
        _, sha_b = build_art_manifest.build_card_tarball(
            source_dir / "alpha_card", out_b
        )
        assert sha_a == sha_b

    def test_gzip_header_mtime_is_zero(self, source_dir: Path, tmp_path: Path):
        """Regression: the gzip stream header MUST have mtime=0.

        ``tarfile.open(..., "w:gz")`` uses ``time.time()`` for the gzip
        header's mtime field. The previous deterministic_sha test only
        compared two builds in the same second so it never caught this
        — across release boundaries (or across CI runners with skewed
        clocks) the per-card sha256 would flap, defeating the
        manifest-diff incremental-update story.

        We assert the literal mtime bytes (offset 4..7 of the gzip
        envelope, little-endian uint32) are zero. This is the
        belt-and-braces complement to the determinism test: even if
        someone reverts the GzipFile(mtime=0) call, this fails.
        """
        import struct

        out = tmp_path / "card.tar.gz"
        build_art_manifest.build_card_tarball(source_dir / "alpha_card", out)
        raw = out.read_bytes()
        # gzip header layout (RFC 1952): magic(2) + cm(1) + flg(1) +
        # mtime(4 LE) + xfl(1) + os(1).
        assert raw[:2] == b"\x1f\x8b", "not a gzip stream"
        mtime = struct.unpack("<I", raw[4:8])[0]
        assert mtime == 0, (
            f"gzip header mtime should be 0 for deterministic builds, "
            f"got {mtime}. If this fails, build_card_tarball reverted "
            f'to tarfile.open(..., "w:gz") which writes time.time().'
        )

    def test_raises_on_empty_card_dir(self, tmp_path: Path):
        empty = tmp_path / "empty_card"
        empty.mkdir()
        with pytest.raises(SystemExit, match="no shippable files"):
            build_art_manifest.build_card_tarball(
                empty, tmp_path / "out.tar.gz"
            )


# ---------------------------------------------------------------------------
# write_sha256_sidecar
# ---------------------------------------------------------------------------

class TestWriteSha256Sidecar:
    def test_writes_sha256sum_format(self, tmp_path: Path):
        asset = tmp_path / "card_alpha.tar.gz"
        asset.write_bytes(b"hello world")
        sidecar = build_art_manifest.write_sha256_sidecar(asset)
        assert sidecar.name == "card_alpha.tar.gz.sha256"
        text = sidecar.read_text(encoding="utf-8")
        digest = hashlib.sha256(b"hello world").hexdigest()
        assert text == f"{digest}  card_alpha.tar.gz\n"


# ---------------------------------------------------------------------------
# parse_starter_ids
# ---------------------------------------------------------------------------

class TestParseStarterIds:
    def test_csv_form(self):
        ids = build_art_manifest.parse_starter_ids(
            cli_csv="alpha_card,beta_card",
            file_path=None,
            known_ids={"alpha_card", "beta_card", "gamma_card"},
        )
        assert ids == ("alpha_card", "beta_card")

    def test_file_form(self, tmp_path: Path):
        f = tmp_path / "starters.txt"
        f.write_text("alpha_card\nbeta_card\n\n  \n", encoding="utf-8")
        ids = build_art_manifest.parse_starter_ids(
            cli_csv=None,
            file_path=f,
            known_ids={"alpha_card", "beta_card"},
        )
        assert ids == ("alpha_card", "beta_card")

    def test_default_picks_first_ten(self):
        ids = build_art_manifest.parse_starter_ids(
            cli_csv=None,
            file_path=None,
            known_ids={f"card_{i:02}" for i in range(20)},
        )
        assert len(ids) == 10
        assert ids == tuple(sorted(f"card_{i:02}" for i in range(10)))

    def test_rejects_unknown_id(self):
        with pytest.raises(SystemExit, match="unknown cards: ghost_card"):
            build_art_manifest.parse_starter_ids(
                cli_csv="alpha_card,ghost_card",
                file_path=None,
                known_ids={"alpha_card"},
            )

    def test_rejects_both_sources(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("alpha_card", encoding="utf-8")
        with pytest.raises(SystemExit, match="at most one"):
            build_art_manifest.parse_starter_ids(
                cli_csv="alpha_card",
                file_path=f,
                known_ids={"alpha_card"},
            )

    def test_rejects_missing_file(self, tmp_path: Path):
        with pytest.raises(SystemExit, match="not found"):
            build_art_manifest.parse_starter_ids(
                cli_csv=None,
                file_path=tmp_path / "nope",
                known_ids={"alpha_card"},
            )


# ---------------------------------------------------------------------------
# build_manifest — end-to-end
# ---------------------------------------------------------------------------

class TestBuildManifestEndToEnd:
    def test_produces_valid_manifest_and_assets(
        self, source_dir: Path, tmp_path: Path
    ):
        out = tmp_path / "release"
        manifest = build_art_manifest.build_manifest(
            input_dir=source_dir,
            output_dir=out,
            pack_version="art-v1.0",
            pack_name="v1_alpha",
            asset_base_url="https://example.invalid/dl/",
            starter_card_ids=("alpha_card",),
        )

        # Manifest fields.
        assert manifest.schema_version == SCHEMA_VERSION
        assert manifest.pack_version == "art-v1.0"
        assert manifest.pack_name == "v1_alpha"
        assert manifest.asset_base_url == "https://example.invalid/dl/"
        assert manifest.starter_card_ids == ("alpha_card",)
        assert set(manifest.cards.keys()) == {
            "alpha_card", "beta_card", "gamma_card",
        }

        # Per-card assets land on disk with sidecars.
        for cid in manifest.cards:
            tarball = out / f"card_{cid}.tar.gz"
            sidecar = out / f"card_{cid}.tar.gz.sha256"
            assert tarball.is_file()
            assert sidecar.is_file()
            # The on-disk sha matches what the manifest claims.
            actual_sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
            assert manifest.cards[cid].sha256 == actual_sha
            assert manifest.cards[cid].size_bytes == tarball.stat().st_size

        # Manifest itself + sidecar.
        manifest_file = out / MANIFEST_ASSET_NAME
        assert manifest_file.is_file()
        sidecar = out / f"{MANIFEST_ASSET_NAME}.sha256"
        assert sidecar.is_file()

        # Re-parse from disk through the runtime's loader → identical.
        roundtrip = Manifest.from_json(manifest_file.read_text(encoding="utf-8"))
        assert roundtrip == manifest

    def test_round_trips_through_fetch_card(
        self, source_dir: Path, tmp_path: Path, monkeypatch
    ):
        """Built tarballs extract correctly via the runtime's fetch_card.

        This is the strongest contract test: build script writes →
        runtime reads → the lazy fetcher produces the exact files we
        started from. If anything in the build script's tarball layout
        drifted from what fetch_card expects, this assertion fails.
        """
        from daimon.update import fetcher
        from daimon.update.lazy import fetch_card
        from daimon.update.manifest import write_manifest
        from daimon.update.paths import art_pack_dir

        out = tmp_path / "release"
        manifest = build_art_manifest.build_manifest(
            input_dir=source_dir,
            output_dir=out,
            pack_version="art-v1.0",
            pack_name="v1_alpha",
            asset_base_url="https://example.invalid/dl/",
            starter_card_ids=("alpha_card",),
        )

        # Sandbox the runtime to a fresh art root and install the manifest.
        runtime_root = tmp_path / "runtime"
        monkeypatch.setenv("DAIMON_ART_DIR", str(runtime_root))
        write_manifest(manifest)

        # Stub _http_open to serve from disk, since the build output IS
        # what would land at the GitHub Release.
        def fake_http_open(url: str, *, octet_stream: bool = False):
            from io import BytesIO
            asset_name = url.rsplit("/", 1)[-1]
            return _FakeResp((out / asset_name).read_bytes())

        class _FakeResp:
            def __init__(self, payload: bytes):
                from io import BytesIO
                self._buf = BytesIO(payload)
                self.headers = {}

            def read(self, n: int = -1) -> bytes:
                return self._buf.read(n) if n > 0 else self._buf.read()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self._buf.close()
                return False

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)

        # Fetch one card and confirm the extracted files match the source.
        live = fetch_card("gamma_card", manifest=manifest, show_progress=False)
        assert live == art_pack_dir() / "gamma_card"
        assert (live / "base.png").is_file()
        assert (live / "manifest.json").is_file()
        assert (live / "variants" / "v0.png").is_file()
        assert (live / "variants" / "v1.png").is_file()

        # Per-byte equality with the source — proves the tarball
        # format is preserved end-to-end.
        for f in ("base.png", "manifest.json", "variants/v0.png"):
            src = source_dir / "gamma_card" / f
            dst = live / f
            assert src.read_bytes() == dst.read_bytes(), f"mismatch on {f}"
