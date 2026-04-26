"""Tests for the lazy art-pack architecture.

Covers ``daimon.update.manifest`` (data model + fetcher) and
``daimon.update.lazy`` (per-card JIT fetch + ensure_art_for).

Network calls are stubbed at the same boundaries the legacy tests use:
  * ``daimon.update.api.urlopen``    — release listing / by-tag lookup
  * ``daimon.update.fetcher._http_open`` — asset + sha256 downloads

Test isolation pattern matches ``tests/test_update.py``: monkeypatch
``DAIMON_ART_DIR`` to a tmp dir so every helper resolves under that
sandbox.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError

import pytest

from daimon.update import api, fetcher
from daimon.update.fetcher import ArtUpdateError
from daimon.update.lazy import (
    cleanup_card_staging,
    ensure_art_for,
    fetch_card,
    is_card_cached,
)
from daimon.update.manifest import (
    MANIFEST_ASSET_NAME,
    SCHEMA_VERSION,
    CardEntry,
    Manifest,
    ManifestDiff,
    diff_manifests,
    fetch_manifest,
    load_manifest,
    write_manifest,
)
from daimon.update.paths import (
    art_pack_dir,
    checksum_file,
    manifest_path,
    version_file,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def art_dir(monkeypatch, tmp_path: Path) -> Path:
    """Sandbox the art root to a tmp dir for the test."""
    monkeypatch.setenv("DAIMON_ART_DIR", str(tmp_path))
    monkeypatch.delenv("DAIMON_NO_AUTO_UPDATE", raising=False)
    monkeypatch.delenv("DAIMON_PIN_ART", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("DAIMON_ART_REPO", raising=False)
    return tmp_path


class FakeHTTPResponse:
    """Minimal urlopen() stand-in: context manager + .read() + .headers."""

    def __init__(self, payload: bytes, headers: Optional[dict] = None,
                 status: int = 200):
        self._buf = io.BytesIO(payload)
        self.headers = headers or {}
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n) if n > 0 else self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._buf.close()
        return False


def _build_card_tarball(card_id: str, *, with_top_dir: bool = False) -> tuple[bytes, str]:
    """Build an in-memory per-card tarball + return (raw_bytes, sha256_hex).

    Default layout is flat (base.png, manifest.json) — the format
    ``scripts/build_art_manifest.py`` produces. Set ``with_top_dir=True``
    to test the defensive fallback where the tarball wraps content in
    ``<card_id>/``.
    """
    buf = io.BytesIO()
    prefix = f"{card_id}/" if with_top_dir else ""
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        png = b"\x89PNG\r\n\x1a\n" + b"X" * 16
        info = tarfile.TarInfo(name=f"{prefix}base.png")
        info.size = len(png)
        tf.addfile(info, io.BytesIO(png))

        manifest_blob = json.dumps({"card_id": card_id, "canonical": "v0"}).encode()
        mi = tarfile.TarInfo(name=f"{prefix}manifest.json")
        mi.size = len(manifest_blob)
        tf.addfile(mi, io.BytesIO(manifest_blob))

        variant_png = b"\x89PNG\r\n\x1a\n" + b"V" * 16
        vi = tarfile.TarInfo(name=f"{prefix}variants/v0.png")
        vi.size = len(variant_png)
        tf.addfile(vi, io.BytesIO(variant_png))
    raw = buf.getvalue()
    return raw, hashlib.sha256(raw).hexdigest()


def _sample_manifest(
    *,
    pack_version: str = "art-v1.0",
    cards: Optional[dict] = None,
    starter: Optional[tuple[str, ...]] = None,
) -> Manifest:
    """Build an in-memory Manifest with sane defaults."""
    if cards is None:
        cards = {
            "alpha_card": CardEntry("card_alpha_card.tar.gz", "a" * 64, 1024),
            "beta_card": CardEntry("card_beta_card.tar.gz", "b" * 64, 2048),
        }
    if starter is None:
        starter = tuple(sorted(cards.keys()))[:1]
    return Manifest(
        schema_version=SCHEMA_VERSION,
        pack_version=pack_version,
        pack_name="v1_alpha",
        asset_base_url="https://example.invalid/dl/",
        starter_card_ids=starter,
        cards=cards,
    )


# ---------------------------------------------------------------------------
# CardEntry — validation
# ---------------------------------------------------------------------------

class TestCardEntry:
    def test_round_trip(self):
        entry = CardEntry("card_x.tar.gz", "f" * 64, 4096)
        d = entry.to_dict()
        assert d == {"asset_name": "card_x.tar.gz", "sha256": "f" * 64, "size_bytes": 4096}
        roundtrip = CardEntry.from_dict(d, card_id="x")
        assert roundtrip == entry

    def test_rejects_short_sha(self):
        with pytest.raises(ArtUpdateError, match="64-hex"):
            CardEntry.from_dict(
                {"asset_name": "x.tar.gz", "sha256": "abcd", "size_bytes": 1},
                card_id="x",
            )

    def test_rejects_non_hex_sha(self):
        with pytest.raises(ArtUpdateError, match="64-hex"):
            CardEntry.from_dict(
                {"asset_name": "x.tar.gz", "sha256": "g" * 64, "size_bytes": 1},
                card_id="x",
            )

    def test_rejects_negative_size(self):
        with pytest.raises(ArtUpdateError, match="negative"):
            CardEntry.from_dict(
                {"asset_name": "x.tar.gz", "sha256": "0" * 64, "size_bytes": -1},
                card_id="x",
            )

    def test_rejects_path_like_asset_name(self):
        with pytest.raises(ArtUpdateError, match="unsafe"):
            CardEntry.from_dict(
                {"asset_name": "../etc/passwd", "sha256": "0" * 64, "size_bytes": 1},
                card_id="x",
            )

    def test_normalizes_sha_case(self):
        entry = CardEntry.from_dict(
            {"asset_name": "x.tar.gz", "sha256": "A" * 64, "size_bytes": 1},
            card_id="x",
        )
        assert entry.sha256 == "a" * 64


# ---------------------------------------------------------------------------
# Manifest — serialization, validation, card_url
# ---------------------------------------------------------------------------

class TestManifest:
    def test_to_from_json_round_trip(self):
        m = _sample_manifest()
        roundtrip = Manifest.from_json(m.to_json())
        assert roundtrip == m

    def test_card_url_appends_slash(self):
        m = _sample_manifest()
        # asset_base_url has trailing slash already
        assert m.card_url("alpha_card") == "https://example.invalid/dl/card_alpha_card.tar.gz"

    def test_card_url_adds_slash_if_missing(self):
        m = _sample_manifest()
        m_no_slash = Manifest(
            schema_version=m.schema_version,
            pack_version=m.pack_version,
            pack_name=m.pack_name,
            asset_base_url="https://example.invalid/dl",
            starter_card_ids=m.starter_card_ids,
            cards=m.cards,
        )
        assert m_no_slash.card_url("alpha_card") == "https://example.invalid/dl/card_alpha_card.tar.gz"

    def test_card_url_unknown_raises(self):
        m = _sample_manifest()
        with pytest.raises(ArtUpdateError, match="unknown card_id"):
            m.card_url("nonexistent")

    def test_rejects_schema_version_mismatch(self):
        m = _sample_manifest()
        d = m.to_dict()
        d["schema_version"] = SCHEMA_VERSION + 99
        with pytest.raises(ArtUpdateError, match="unsupported"):
            Manifest.from_dict(d)

    def test_rejects_missing_field(self):
        m = _sample_manifest()
        d = m.to_dict()
        d.pop("pack_version")
        with pytest.raises(ArtUpdateError, match="missing field"):
            Manifest.from_dict(d)

    def test_rejects_starter_pointing_at_unknown_card(self):
        m = _sample_manifest()
        d = m.to_dict()
        d["starter_card_ids"] = ["alpha_card", "ghost_card"]
        with pytest.raises(ArtUpdateError, match="unknown card"):
            Manifest.from_dict(d)

    def test_rejects_empty_asset_base_url(self):
        m = _sample_manifest()
        d = m.to_dict()
        d["asset_base_url"] = ""
        with pytest.raises(ArtUpdateError, match="asset_base_url is empty"):
            Manifest.from_dict(d)

    def test_card_count_matches_cards_dict(self):
        m = _sample_manifest()
        assert m.card_count == len(m.cards) == 2

    def test_from_json_rejects_non_object(self):
        with pytest.raises(ArtUpdateError, match="top-level must be an object"):
            Manifest.from_json("[]")


# ---------------------------------------------------------------------------
# ManifestDiff — incremental update planning
# ---------------------------------------------------------------------------

class TestManifestDiff:
    def test_fresh_install_all_added(self):
        new = _sample_manifest()
        diff = diff_manifests(None, new)
        assert set(diff.added) == set(new.cards.keys())
        assert diff.removed == ()
        assert diff.changed == ()
        assert diff.unchanged == ()
        assert set(diff.needs_fetch) == set(new.cards.keys())

    def test_no_op_when_identical(self):
        m = _sample_manifest()
        diff = diff_manifests(m, m)
        assert diff.added == ()
        assert diff.removed == ()
        assert diff.changed == ()
        assert set(diff.unchanged) == set(m.cards.keys())
        assert diff.needs_fetch == ()

    def test_version_bump_mix(self):
        old = _sample_manifest(
            cards={
                "alpha_card": CardEntry("card_alpha_card.tar.gz", "a" * 64, 1024),
                "beta_card": CardEntry("card_beta_card.tar.gz", "b" * 64, 2048),
                "gamma_card": CardEntry("card_gamma_card.tar.gz", "c" * 64, 4096),
            },
            starter=("alpha_card",),
        )
        new = _sample_manifest(
            pack_version="art-v1.1",
            cards={
                # alpha_card: unchanged
                "alpha_card": CardEntry("card_alpha_card.tar.gz", "a" * 64, 1024),
                # beta_card: sha changed (re-fetch)
                "beta_card": CardEntry("card_beta_card.tar.gz", "B" * 64, 2048),
                # gamma_card: removed
                # delta_card: added
                "delta_card": CardEntry("card_delta_card.tar.gz", "d" * 64, 8192),
            },
            starter=("alpha_card",),
        )
        diff = diff_manifests(old, new)
        assert diff.added == ("delta_card",)
        assert diff.removed == ("gamma_card",)
        assert diff.changed == ("beta_card",)
        assert diff.unchanged == ("alpha_card",)
        assert set(diff.needs_fetch) == {"delta_card", "beta_card"}


# ---------------------------------------------------------------------------
# load_manifest / write_manifest — atomic IO
# ---------------------------------------------------------------------------

class TestManifestIO:
    def test_load_returns_none_when_absent(self, art_dir: Path):
        assert load_manifest() is None

    def test_write_then_load_round_trip(self, art_dir: Path):
        m = _sample_manifest()
        write_manifest(m)
        assert manifest_path().is_file()
        loaded = load_manifest()
        assert loaded == m

    def test_load_corrupt_raises(self, art_dir: Path):
        manifest_path().parent.mkdir(parents=True, exist_ok=True)
        manifest_path().write_text("not json", encoding="utf-8")
        with pytest.raises(ArtUpdateError, match="not valid JSON"):
            load_manifest()

    def test_write_is_atomic_via_tempfile(self, art_dir: Path, monkeypatch):
        """tempfile + rename means a crash mid-write never produces a half-baked file at the live path."""
        m = _sample_manifest()
        write_manifest(m)
        # Simulate a crash mid-write: monkeypatch Path.replace to no-op AFTER
        # the tempfile is created but BEFORE the rename. The original file
        # must still be intact.
        original = manifest_path().read_text()

        captured = {}
        real_replace = Path.replace

        def crashing_replace(self, target):
            captured["tmp"] = self
            raise OSError("simulated crash")

        monkeypatch.setattr(Path, "replace", crashing_replace)
        m2 = _sample_manifest(pack_version="art-v1.1")
        with pytest.raises(OSError, match="simulated crash"):
            write_manifest(m2)
        # Live file untouched.
        assert manifest_path().read_text() == original
        # Temp file landed but not at live path.
        assert captured["tmp"].exists()


# ---------------------------------------------------------------------------
# fetch_manifest — network round-trip
# ---------------------------------------------------------------------------

def _release_json(
    tag: str,
    asset_name: str = MANIFEST_ASSET_NAME,
    sha256_hex: Optional[str] = None,
    body: str = "",
) -> dict:
    """Build a GH Release JSON featuring the manifest as the primary asset."""
    assets = [{
        "name": asset_name,
        "browser_download_url": f"https://example.invalid/{tag}/{asset_name}",
        "url": f"https://api.example.invalid/assets/{tag}/{asset_name}",
        "size": 1024,
    }]
    if sha256_hex:
        assets.append({
            "name": f"{asset_name}.sha256",
            "browser_download_url": f"https://example.invalid/{tag}/{asset_name}.sha256",
            "url": f"https://api.example.invalid/assets/{tag}/{asset_name}.sha256",
            "size": 82,
        })
    return {
        "tag_name": tag,
        "draft": False,
        "published_at": "2026-04-26T00:00:00Z",
        "body": body,
        "assets": assets,
    }


class TestFetchManifest:
    def _stub_release_listing(self, monkeypatch, tag: str, manifest_bytes: bytes):
        """Pin gh API to return one release tagged ``tag`` with the manifest asset."""
        digest = hashlib.sha256(manifest_bytes).hexdigest()
        releases = [_release_json(tag, sha256_hex=digest)]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(releases).encode()),
        )
        return digest

    def _stub_http_open(self, monkeypatch, *, manifest_bytes: bytes, sha256_hex: str):
        """Route ``_http_open`` to either the manifest payload or the sidecar.

        Tests that download a manifest call ``_http_open`` twice — once for
        the manifest itself, once for the sidecar. We dispatch by inspecting
        the URL.
        """

        def fake_http_open(url: str, *, octet_stream: bool = False):
            if url.endswith(".sha256"):
                return FakeHTTPResponse(f"{sha256_hex}  {MANIFEST_ASSET_NAME}\n".encode())
            return FakeHTTPResponse(manifest_bytes)

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)

    def test_happy_path_writes_manifest_and_sidecars(self, art_dir: Path, monkeypatch):
        manifest = _sample_manifest()
        manifest_bytes = manifest.to_json().encode()
        digest = self._stub_release_listing(monkeypatch, "art-v1.0", manifest_bytes)
        self._stub_http_open(monkeypatch, manifest_bytes=manifest_bytes, sha256_hex=digest)

        result = fetch_manifest(show_progress=False)

        # Returned manifest equals what we served (round-trip semantically equal).
        assert result.pack_version == "art-v1.0"
        assert set(result.cards.keys()) == set(manifest.cards.keys())

        # On-disk artifacts.
        assert manifest_path().is_file()
        on_disk = load_manifest()
        assert on_disk is not None and on_disk.pack_version == "art-v1.0"
        assert version_file().read_text().strip() == "art-v1.0"
        cs_text = checksum_file().read_text().strip()
        assert cs_text.startswith(digest)
        assert MANIFEST_ASSET_NAME in cs_text

    def test_sha_mismatch_aborts(self, art_dir: Path, monkeypatch):
        manifest = _sample_manifest()
        manifest_bytes = manifest.to_json().encode()
        # Real digest of the bytes — but advertise a different one.
        wrong_digest = "0" * 64
        releases = [_release_json("art-v1.0", sha256_hex=wrong_digest)]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(releases).encode()),
        )
        self._stub_http_open(
            monkeypatch, manifest_bytes=manifest_bytes, sha256_hex=wrong_digest
        )

        with pytest.raises(ArtUpdateError, match="sha256 mismatch"):
            fetch_manifest(show_progress=False)
        # Nothing persisted on failure.
        assert not manifest_path().exists()

    def test_pack_version_mismatch_aborts(self, art_dir: Path, monkeypatch):
        # The manifest declares art-v1.0 but the release tag is art-v1.1 —
        # build script bug. Refuse to install.
        manifest = _sample_manifest(pack_version="art-v1.0")
        manifest_bytes = manifest.to_json().encode()
        digest = self._stub_release_listing(monkeypatch, "art-v1.1", manifest_bytes)
        self._stub_http_open(monkeypatch, manifest_bytes=manifest_bytes, sha256_hex=digest)

        with pytest.raises(ArtUpdateError, match="declares pack_version"):
            fetch_manifest(show_progress=False)
        assert not manifest_path().exists()

    def test_idempotent_on_same_version(self, art_dir: Path, monkeypatch):
        # Pre-install a manifest for art-v1.0; fetch_manifest() should
        # short-circuit without calling _http_open at all.
        manifest = _sample_manifest()
        write_manifest(manifest)
        manifest_bytes = manifest.to_json().encode()
        self._stub_release_listing(monkeypatch, "art-v1.0", manifest_bytes)
        # Make _http_open blow up if called.
        monkeypatch.setattr(
            fetcher, "_http_open",
            lambda *a, **k: pytest.fail("_http_open should not be called"),
        )
        result = fetch_manifest(show_progress=False)
        assert result.pack_version == "art-v1.0"


# ---------------------------------------------------------------------------
# is_card_cached — directory predicate
# ---------------------------------------------------------------------------

class TestIsCardCached:
    def test_missing_dir_is_false(self, art_dir: Path):
        assert is_card_cached("nope") is False

    def test_empty_dir_is_false(self, art_dir: Path):
        d = art_pack_dir() / "alpha"
        d.mkdir(parents=True)
        assert is_card_cached("alpha") is False

    def test_base_png_present_is_true(self, art_dir: Path):
        d = art_pack_dir() / "alpha"
        d.mkdir(parents=True)
        (d / "base.png").write_bytes(b"\x89PNG")
        assert is_card_cached("alpha") is True

    def test_only_variants_present_is_true(self, art_dir: Path):
        d = art_pack_dir() / "alpha"
        (d / "variants").mkdir(parents=True)
        (d / "variants" / "v0.png").write_bytes(b"\x89PNG")
        assert is_card_cached("alpha") is True


# ---------------------------------------------------------------------------
# fetch_card / ensure_art_for — per-card flow
# ---------------------------------------------------------------------------

class TestFetchCard:
    def _install_manifest_with_card(self, *, card_id: str, sha: str, size: int) -> Manifest:
        m = _sample_manifest(
            cards={card_id: CardEntry(f"card_{card_id}.tar.gz", sha, size)},
            starter=(card_id,),
        )
        write_manifest(m)
        return m

    def test_no_manifest_raises(self, art_dir: Path):
        with pytest.raises(ArtUpdateError, match="no manifest"):
            fetch_card("alpha")

    def test_unknown_card_raises(self, art_dir: Path):
        self._install_manifest_with_card(card_id="alpha", sha="0" * 64, size=1)
        with pytest.raises(ArtUpdateError, match="not in manifest"):
            fetch_card("ghost")

    def test_happy_path_extracts_and_swaps(self, art_dir: Path, monkeypatch):
        raw, digest = _build_card_tarball("alpha")
        m = self._install_manifest_with_card(
            card_id="alpha", sha=digest, size=len(raw)
        )

        def fake_http_open(url: str, *, octet_stream: bool = False):
            assert url == m.card_url("alpha")
            return FakeHTTPResponse(raw)

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)
        result = fetch_card("alpha", show_progress=False)
        assert result == art_pack_dir() / "alpha"
        assert (result / "base.png").is_file()
        assert (result / "manifest.json").is_file()
        assert (result / "variants" / "v0.png").is_file()

    def test_sha_mismatch_aborts(self, art_dir: Path, monkeypatch):
        raw, _digest = _build_card_tarball("alpha")
        # Manifest says some other digest.
        wrong_digest = "0" * 64
        self._install_manifest_with_card(
            card_id="alpha", sha=wrong_digest, size=len(raw)
        )

        def fake_http_open(url: str, *, octet_stream: bool = False):
            return FakeHTTPResponse(raw)

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)
        with pytest.raises(ArtUpdateError, match="sha256 mismatch"):
            fetch_card("alpha", show_progress=False)
        # Nothing was swapped in.
        assert not (art_pack_dir() / "alpha").exists()

    def test_idempotent_when_already_cached(self, art_dir: Path, monkeypatch):
        d = art_pack_dir() / "alpha"
        d.mkdir(parents=True)
        (d / "base.png").write_bytes(b"\x89PNG")
        self._install_manifest_with_card(card_id="alpha", sha="0" * 64, size=1)
        # Make _http_open blow up if called.
        monkeypatch.setattr(
            fetcher, "_http_open",
            lambda *a, **k: pytest.fail("network called for cached card"),
        )
        result = fetch_card("alpha")
        assert result == d

    def test_accepts_tarball_with_top_level_dir(self, art_dir: Path, monkeypatch):
        """Defensive: tarballs that wrap content in <card_id>/ also extract."""
        raw, digest = _build_card_tarball("alpha", with_top_dir=True)
        self._install_manifest_with_card(card_id="alpha", sha=digest, size=len(raw))
        monkeypatch.setattr(
            fetcher, "_http_open",
            lambda url, *, octet_stream=False: FakeHTTPResponse(raw),
        )
        result = fetch_card("alpha", show_progress=False)
        assert (result / "base.png").is_file()


class TestEnsureArtFor:
    def test_no_manifest_returns_none(self, art_dir: Path, capsys):
        assert ensure_art_for("alpha") is None

    def test_returns_path_when_cached(self, art_dir: Path):
        d = art_pack_dir() / "alpha"
        d.mkdir(parents=True)
        (d / "base.png").write_bytes(b"\x89PNG")
        # No manifest, no network — but cache hit short-circuits.
        m = _sample_manifest(
            cards={"alpha": CardEntry("card_alpha.tar.gz", "0" * 64, 1)},
            starter=("alpha",),
        )
        write_manifest(m)
        assert ensure_art_for("alpha") == d

    def test_soft_fails_on_fetch_error(self, art_dir: Path, monkeypatch, capsys):
        m = _sample_manifest(
            cards={"alpha": CardEntry("card_alpha.tar.gz", "f" * 64, 1)},
            starter=("alpha",),
        )
        write_manifest(m)
        # _http_open raises — fetch_card raises ArtUpdateError — ensure_art_for swallows.

        def boom(url: str, *, octet_stream: bool = False):
            raise OSError("network unreachable")

        monkeypatch.setattr(fetcher, "_http_open", boom)
        result = ensure_art_for("alpha")
        assert result is None
        captured = capsys.readouterr()
        assert "alpha" in captured.err
        assert "failed" in captured.err

    def test_fetches_on_miss(self, art_dir: Path, monkeypatch):
        raw, digest = _build_card_tarball("alpha")
        m = _sample_manifest(
            cards={"alpha": CardEntry("card_alpha.tar.gz", digest, len(raw))},
            starter=("alpha",),
        )
        write_manifest(m)
        monkeypatch.setattr(
            fetcher, "_http_open",
            lambda url, *, octet_stream=False: FakeHTTPResponse(raw),
        )
        result = ensure_art_for("alpha", show_progress=False)
        assert result == art_pack_dir() / "alpha"
        assert (result / "base.png").is_file()


# ---------------------------------------------------------------------------
# cleanup_card_staging — abandoned-fetch sweep
# ---------------------------------------------------------------------------

class TestCleanupCardStaging:
    def test_removes_abandoned_subdirs(self, art_dir: Path):
        from daimon.update.paths import staging_dir
        cards_staging = staging_dir() / "cards"
        cards_staging.mkdir(parents=True)
        (cards_staging / "alpha.999.0").mkdir()
        (cards_staging / "alpha.999.0" / "stuff.txt").write_text("x")
        (cards_staging / "stale-tarball.tar.gz").write_bytes(b"data")

        cleanup_card_staging()

        assert not (cards_staging / "alpha.999.0").exists()
        assert not (cards_staging / "stale-tarball.tar.gz").exists()

    def test_handles_missing_dir_gracefully(self, art_dir: Path):
        # No staging dir at all — must not raise.
        cleanup_card_staging()
