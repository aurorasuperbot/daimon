"""Tests for ``daimon.update`` — paths, api, fetcher, checker, CLI integration.

Test isolation pattern (matches ``tests/test_cli.py``): monkeypatch
``DAIMON_ART_DIR`` to a tmp_path so every helper resolves under that
sandbox. We do NOT use ``importlib.reload`` — paths are resolved lazily
on each ``art_root()`` call (by design, see paths.py docstring), so a
single ``monkeypatch.setenv`` suffices.

Network calls are stubbed at the boundary:
  * ``daimon.update.api.urlopen``  — for GH API list/tag lookups
  * ``daimon.update.fetcher._http_open`` — for asset + sha256 downloads

Both stubs return a context-manager object so the ``with urlopen(...)``
pattern in the production code keeps working.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import time
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError

import pytest
from click.testing import CliRunner

from daimon.update import (
    api,
    checker,
    fetcher,
    paths,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def art_dir(monkeypatch, tmp_path: Path) -> Path:
    """Sandbox the art root to a tmp dir for the duration of the test."""
    monkeypatch.setenv("DAIMON_ART_DIR", str(tmp_path))
    monkeypatch.delenv("DAIMON_NO_AUTO_UPDATE", raising=False)
    monkeypatch.delenv("DAIMON_PIN_ART", raising=False)
    monkeypatch.delenv("DAIMON_UPDATE_CHECK_HOURS", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return tmp_path


@pytest.fixture
def fake_pack_tarball(tmp_path: Path) -> tuple[bytes, str]:
    """Build a tiny in-memory tarball matching the art-pack layout.

    Returns (raw_bytes, sha256_hex). The tarball contains:
        art/v1_alpha/<card>/base.png      (1 byte stub)
        art/v1_alpha/<card>/manifest.json (minimal)
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        # Two cards, two files each — enough to test extraction + the
        # "pack is non-empty" check in is_pack_installed.
        for cid in ("alpha_card", "beta_card"):
            png_data = b"\x89PNG\r\n\x1a\n" + b"X" * 8
            info = tarfile.TarInfo(name=f"art/v1_alpha/{cid}/base.png")
            info.size = len(png_data)
            tf.addfile(info, io.BytesIO(png_data))

            manifest = json.dumps({"card_id": cid, "canonical": "v0"}).encode()
            mi = tarfile.TarInfo(name=f"art/v1_alpha/{cid}/manifest.json")
            mi.size = len(manifest)
            tf.addfile(mi, io.BytesIO(manifest))

    raw = buf.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    return raw, digest


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


# ---------------------------------------------------------------------------
# paths.py
# ---------------------------------------------------------------------------

class TestPaths:
    def test_art_root_uses_daimon_art_dir(self, art_dir: Path):
        assert paths.art_root() == art_dir

    def test_art_root_falls_back_to_xdg(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("DAIMON_ART_DIR", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        assert paths.art_root() == tmp_path / "xdg" / "daimon"

    def test_art_root_falls_back_to_home(self, monkeypatch):
        monkeypatch.delenv("DAIMON_ART_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert paths.art_root() == Path.home() / ".daimon"

    def test_pack_dirs_layout(self, art_dir: Path):
        assert paths.art_pack_dir() == art_dir / "art" / "v1_alpha"
        assert paths.cache_dir() == art_dir / "cache"
        assert paths.staging_dir() == art_dir / "cache" / "staging"
        assert paths.last_check_path() == art_dir / "cache" / "last_check.json"

    def test_parse_art_version(self):
        assert paths.parse_art_version("art-v1.0") == (1, 0)
        assert paths.parse_art_version("art-v2.7") == (2, 7)
        assert paths.parse_art_version("art-v3") == (3, 0)
        assert paths.parse_art_version("v1.0") is None
        assert paths.parse_art_version("art-vfoo") is None
        assert paths.parse_art_version("art-v1.x") is None

    def test_current_version_missing(self, art_dir: Path):
        assert paths.current_version() is None

    def test_current_version_present(self, art_dir: Path):
        pack = paths.art_pack_dir()
        pack.mkdir(parents=True)
        (pack / ".version").write_text("art-v1.0\n")
        assert paths.current_version() == "art-v1.0"

    def test_expected_checksum_sha256sum_format(self, art_dir: Path):
        pack = paths.art_pack_dir()
        pack.mkdir(parents=True)
        digest = "a" * 64
        (pack / ".checksum").write_text(f"{digest}  v1_alpha.tar.gz\n")
        assert paths.expected_checksum() == digest

    def test_expected_checksum_bare_hex(self, art_dir: Path):
        pack = paths.art_pack_dir()
        pack.mkdir(parents=True)
        digest = "b" * 64
        (pack / ".checksum").write_text(digest + "\n")
        assert paths.expected_checksum() == digest

    def test_expected_checksum_malformed(self, art_dir: Path):
        pack = paths.art_pack_dir()
        pack.mkdir(parents=True)
        (pack / ".checksum").write_text("not a hash\n")
        assert paths.expected_checksum() is None

    def test_auto_update_opt_out(self, monkeypatch):
        monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "1")
        assert not paths.auto_update_enabled()
        monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "true")
        assert not paths.auto_update_enabled()
        monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "0")
        assert paths.auto_update_enabled()

    def test_pinned_version(self, monkeypatch):
        monkeypatch.delenv("DAIMON_PIN_ART", raising=False)
        assert paths.pinned_version() is None
        monkeypatch.setenv("DAIMON_PIN_ART", "art-v1.0")
        assert paths.pinned_version() == "art-v1.0"

    def test_check_interval_override(self, monkeypatch):
        monkeypatch.setenv("DAIMON_UPDATE_CHECK_HOURS", "0")
        assert paths.update_check_interval_hours() == 0.0
        monkeypatch.setenv("DAIMON_UPDATE_CHECK_HOURS", "0.5")
        assert paths.update_check_interval_hours() == 0.5
        monkeypatch.setenv("DAIMON_UPDATE_CHECK_HOURS", "garbage")
        assert paths.update_check_interval_hours() == 24.0


# ---------------------------------------------------------------------------
# api.py — release listing / asset matching
# ---------------------------------------------------------------------------

def _release_json(tag: str, asset_name: str = "v1_alpha.tar.gz",
                  has_sidecar: bool = True, body: str = "",
                  draft: bool = False, asset_size: int = 100) -> dict:
    assets = [{
        "name": asset_name,
        "browser_download_url": f"https://example.invalid/{tag}/{asset_name}",
        "url": f"https://api.example.invalid/assets/{tag}/{asset_name}",
        "size": asset_size,
    }]
    if has_sidecar:
        assets.append({
            "name": f"{asset_name}.sha256",
            "browser_download_url": f"https://example.invalid/{tag}/{asset_name}.sha256",
            "url": f"https://api.example.invalid/assets/{tag}/{asset_name}.sha256",
            "size": 82,
        })
    return {
        "tag_name": tag,
        "draft": draft,
        "published_at": "2026-04-24T00:00:00Z",
        "body": body,
        "assets": assets,
    }


class TestApi:
    def test_latest_picks_highest_version(self, monkeypatch):
        releases = [
            _release_json("art-v1.0"),
            _release_json("art-v1.2"),
            _release_json("art-v1.1"),
            _release_json("v1-other-tag"),  # unrelated tag
        ]
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return FakeHTTPResponse(json.dumps(releases).encode())

        monkeypatch.setattr(api, "urlopen", fake_urlopen)
        rel = api.gh_latest_release("acme/cards")
        assert rel is not None
        assert rel.tag == "art-v1.2"
        assert rel.version == (1, 2)
        assert rel.sha256_url and rel.sha256_url.endswith(".sha256")
        assert "/repos/acme/cards/releases" in captured["url"]

    def test_latest_skips_drafts(self, monkeypatch):
        releases = [
            _release_json("art-v1.5", draft=True),
            _release_json("art-v1.0"),
        ]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(releases).encode()),
        )
        rel = api.gh_latest_release("acme/cards")
        assert rel is not None and rel.tag == "art-v1.0"

    def test_latest_skips_releases_missing_asset(self, monkeypatch):
        releases = [
            _release_json("art-v1.5", asset_name="other.tar.gz"),
            _release_json("art-v1.0"),
        ]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(releases).encode()),
        )
        rel = api.gh_latest_release("acme/cards")
        assert rel is not None and rel.tag == "art-v1.0"

    def test_latest_returns_none_when_empty(self, monkeypatch):
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(b"[]"),
        )
        assert api.gh_latest_release("acme/cards") is None

    def test_release_by_tag_404(self, monkeypatch):
        def raise_404(req, timeout=None):
            raise HTTPError(req.full_url, 404, "Not Found", {}, None)
        monkeypatch.setattr(api, "urlopen", raise_404)
        assert api.gh_release_by_tag("acme/cards", "art-v9.9") is None

    def test_release_by_tag_other_http_error_propagates(self, monkeypatch):
        def raise_500(req, timeout=None):
            raise HTTPError(req.full_url, 500, "Server Error", {}, None)
        monkeypatch.setattr(api, "urlopen", raise_500)
        with pytest.raises(HTTPError):
            api.gh_release_by_tag("acme/cards", "art-v1.0")

    def test_auth_header_added_when_token_present(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["auth"] = req.headers.get("Authorization")
            return FakeHTTPResponse(b"[]")

        monkeypatch.setattr(api, "urlopen", fake_urlopen)
        api.gh_latest_release("acme/cards")
        assert captured["auth"] == "Bearer ghp_secret"


# ---------------------------------------------------------------------------
# fetcher.py — sha256, extraction, atomic swap
# ---------------------------------------------------------------------------

class TestFetcher:
    def test_sha256_file_matches_hashlib(self, tmp_path: Path):
        p = tmp_path / "blob"
        data = b"daimon-test-bytes" * 1024
        p.write_bytes(data)
        assert fetcher.sha256_file(p) == hashlib.sha256(data).hexdigest()

    def test_parse_sha256_sidecar_formats(self):
        d = "c" * 64
        assert fetcher.parse_sha256_sidecar(f"{d}  v1_alpha.tar.gz") == d
        assert fetcher.parse_sha256_sidecar(d) == d
        assert fetcher.parse_sha256_sidecar("garbage line") is None
        assert fetcher.parse_sha256_sidecar("") is None

    def test_safe_extract_rejects_path_traversal(self, tmp_path: Path):
        bad_tar = tmp_path / "bad.tar.gz"
        with tarfile.open(bad_tar, "w:gz") as tf:
            data = b"x"
            info = tarfile.TarInfo(name="../../etc/evil")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        with pytest.raises(fetcher.ArtUpdateError, match="path-traversal"):
            fetcher.safe_extract_tarball(bad_tar, tmp_path / "out")

    def test_safe_extract_rejects_symlink(self, tmp_path: Path):
        bad_tar = tmp_path / "sym.tar.gz"
        with tarfile.open(bad_tar, "w:gz") as tf:
            info = tarfile.TarInfo(name="link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tf.addfile(info)
        with pytest.raises(fetcher.ArtUpdateError, match="link member"):
            fetcher.safe_extract_tarball(bad_tar, tmp_path / "out")

    def test_safe_extract_clean_tarball_works(
        self, tmp_path: Path, fake_pack_tarball
    ):
        raw, _ = fake_pack_tarball
        tar_path = tmp_path / "good.tar.gz"
        tar_path.write_bytes(raw)
        out = tmp_path / "out"
        fetcher.safe_extract_tarball(tar_path, out)
        assert (out / "art" / "v1_alpha" / "alpha_card" / "base.png").is_file()
        assert (out / "art" / "v1_alpha" / "beta_card" / "manifest.json").is_file()

    def test_atomic_swap_replaces_existing(self, tmp_path: Path):
        live = tmp_path / "live"
        live.mkdir()
        (live / "old_marker").write_text("old")

        staged = tmp_path / "staged"
        staged.mkdir()
        (staged / "new_marker").write_text("new")

        fetcher.atomic_swap(staged, live)
        assert (live / "new_marker").is_file()
        assert not (live / "old_marker").exists()
        assert not staged.exists()

    def test_atomic_swap_fresh_install(self, tmp_path: Path):
        live = tmp_path / "live"
        # No prior install — live doesn't exist.
        staged = tmp_path / "staged"
        staged.mkdir()
        (staged / "new_marker").write_text("new")

        fetcher.atomic_swap(staged, live)
        assert (live / "new_marker").is_file()

    def test_cleanup_trash_removes_orphans(self, art_dir: Path):
        art = art_dir / "art"
        art.mkdir(parents=True)
        (art / "v1_alpha.trash.123").mkdir()
        (art / "v1_alpha.trash.456").mkdir()
        (art / "v1_alpha").mkdir()  # the live one — must NOT be removed

        fetcher.cleanup_trash()
        assert not (art / "v1_alpha.trash.123").exists()
        assert not (art / "v1_alpha.trash.456").exists()
        assert (art / "v1_alpha").exists()

    def test_do_update_full_flow(
        self, art_dir: Path, monkeypatch, fake_pack_tarball
    ):
        raw, digest = fake_pack_tarball

        # Mock the GH API: one release, with sidecar + tarball.
        rel_json = [_release_json("art-v1.0", asset_size=len(raw))]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(rel_json).encode()),
        )

        # Mock the asset/sidecar downloads (used by fetcher._http_open).
        def fake_http_open(url: str, *, octet_stream: bool = False):
            if url.endswith(".sha256"):
                return FakeHTTPResponse(
                    f"{digest}  v1_alpha.tar.gz\n".encode(),
                    headers={"Content-Length": str(82)},
                )
            return FakeHTTPResponse(
                raw, headers={"Content-Length": str(len(raw))}
            )

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)

        rel = fetcher.do_update(show_progress=False)
        assert rel.tag == "art-v1.0"

        # Live pack present + populated.
        live = paths.art_pack_dir()
        assert (live / "alpha_card" / "base.png").is_file()
        assert (live / "beta_card" / "manifest.json").is_file()

        # Version + checksum sidecars written.
        assert paths.current_version() == "art-v1.0"
        assert paths.expected_checksum() == digest

        # Staging cleaned up.
        staging = paths.staging_dir()
        leftovers = list(staging.iterdir()) if staging.exists() else []
        assert leftovers == [], f"unexpected leftover: {leftovers}"

    def test_do_update_rejects_sha_mismatch(
        self, art_dir: Path, monkeypatch, fake_pack_tarball
    ):
        raw, _real_digest = fake_pack_tarball
        wrong_digest = "f" * 64

        rel_json = [_release_json("art-v1.0", asset_size=len(raw))]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(rel_json).encode()),
        )

        def fake_http_open(url: str, *, octet_stream: bool = False):
            if url.endswith(".sha256"):
                return FakeHTTPResponse(f"{wrong_digest}  v1_alpha.tar.gz\n".encode())
            return FakeHTTPResponse(raw, headers={"Content-Length": str(len(raw))})

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)

        with pytest.raises(fetcher.ArtUpdateError, match="sha256 mismatch"):
            fetcher.do_update(show_progress=False)

        # No live pack should have been installed.
        assert paths.current_version() is None

    def test_do_update_idempotent_when_up_to_date(
        self, art_dir: Path, monkeypatch, fake_pack_tarball
    ):
        # Pre-install a v1.0 pack.
        live = paths.art_pack_dir()
        live.mkdir(parents=True)
        (live / "stub_card").mkdir()
        paths.version_file().write_text("art-v1.0\n")

        raw, digest = fake_pack_tarball
        rel_json = [_release_json("art-v1.0", asset_size=len(raw))]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(rel_json).encode()),
        )
        download_called = {"n": 0}

        def fake_http_open(url: str, *, octet_stream: bool = False):
            download_called["n"] += 1
            return FakeHTTPResponse(raw)

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)
        rel = fetcher.do_update(show_progress=False)
        assert rel.tag == "art-v1.0"
        # Already up to date → NO download.
        assert download_called["n"] == 0

    def test_do_update_refuses_cross_major(self, art_dir: Path, monkeypatch):
        # Simulate engine on v1, release on v2.
        rel_json = [_release_json("art-v2.0")]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(rel_json).encode()),
        )
        with pytest.raises(fetcher.ArtUpdateError, match="cross-major|major"):
            fetcher.do_update(show_progress=False)


# ---------------------------------------------------------------------------
# checker.py — rate-limit + ensure_art_available
# ---------------------------------------------------------------------------

class TestChecker:
    def test_is_check_due_no_state(self, art_dir: Path):
        assert checker.is_check_due()

    def test_is_check_due_recent_blocks(self, art_dir: Path):
        checker.write_last_check({"ts": int(time.time())})
        assert not checker.is_check_due()

    def test_is_check_due_stale_passes(self, art_dir: Path):
        checker.write_last_check({"ts": int(time.time()) - 25 * 3600})
        assert checker.is_check_due()

    def test_is_check_due_zero_interval_always_true(
        self, art_dir: Path, monkeypatch
    ):
        monkeypatch.setenv("DAIMON_UPDATE_CHECK_HOURS", "0")
        checker.write_last_check({"ts": int(time.time())})
        assert checker.is_check_due()

    def test_update_last_check_clears_error_on_success(self, art_dir: Path):
        checker.update_last_check(error="boom", action="failed")
        state = checker.read_last_check()
        assert state.get("last_error") == "boom"
        checker.update_last_check(latest_seen="art-v1.0", action="installed")
        state = checker.read_last_check()
        assert "last_error" not in state
        assert state["latest_seen"] == "art-v1.0"

    def test_is_pack_installed_false_when_missing(self, art_dir: Path):
        assert not checker.is_pack_installed()

    def test_is_pack_installed_false_when_version_only(self, art_dir: Path):
        # Half-installed: version file exists but no card subdirs.
        pack = paths.art_pack_dir()
        pack.mkdir(parents=True)
        (pack / ".version").write_text("art-v1.0\n")
        assert not checker.is_pack_installed()

    def test_is_pack_installed_true_when_populated(self, art_dir: Path):
        pack = paths.art_pack_dir()
        pack.mkdir(parents=True)
        (pack / ".version").write_text("art-v1.0\n")
        (pack / "alpha_card").mkdir()
        assert checker.is_pack_installed()

    def test_ensure_art_available_first_run_blocking(
        self, art_dir: Path, monkeypatch, fake_pack_tarball
    ):
        raw, digest = fake_pack_tarball
        rel_json = [_release_json("art-v1.0", asset_size=len(raw))]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(rel_json).encode()),
        )

        def fake_http_open(url: str, *, octet_stream: bool = False):
            if url.endswith(".sha256"):
                return FakeHTTPResponse(f"{digest}  v1_alpha.tar.gz\n".encode())
            return FakeHTTPResponse(raw)

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)
        # Block the spawn path — first-run must NOT spawn.
        monkeypatch.setattr(
            checker, "spawn_background_check",
            lambda: pytest.fail("spawn called on first-run"),
        )

        checker.ensure_art_available()
        assert checker.is_pack_installed()

    def test_ensure_art_available_skips_when_opted_out(
        self, art_dir: Path, monkeypatch
    ):
        # Pretend a pack is installed.
        pack = paths.art_pack_dir()
        pack.mkdir(parents=True)
        (pack / ".version").write_text("art-v1.0\n")
        (pack / "alpha_card").mkdir()

        monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "1")
        monkeypatch.setattr(
            checker, "spawn_background_check",
            lambda: pytest.fail("spawn called when opted out"),
        )
        checker.ensure_art_available()  # must not raise

    def test_ensure_art_available_skips_when_opted_out_and_no_pack(
        self, art_dir: Path, monkeypatch, capsys
    ):
        """DAIMON_NO_AUTO_UPDATE=1 is an UNCONDITIONAL opt-out.

        Even with no pack installed, the env var must suppress the
        synchronous first-run fetch. The function emits a one-line
        stderr warning and returns — it must NOT raise, and downstream
        ``art_path_for`` will soft-fail to None.
        """
        # No pack installed — confirm starting state.
        assert not checker.is_pack_installed()

        monkeypatch.setenv("DAIMON_NO_AUTO_UPDATE", "1")
        # Both network paths must NOT be called.
        monkeypatch.setattr(
            checker, "spawn_background_check",
            lambda: pytest.fail("spawn called when opted out"),
        )

        def _no_network(*_a, **_kw):
            pytest.fail("network/fetch called when opted out")

        monkeypatch.setattr(api, "urlopen", _no_network)
        monkeypatch.setattr(fetcher, "_http_open", _no_network)

        checker.ensure_art_available()  # must not raise

        # Pack should still be missing — opt-out means no install.
        assert not checker.is_pack_installed()

        # Warning should mention the env var + the missing-pack situation.
        captured = capsys.readouterr()
        assert "DAIMON_NO_AUTO_UPDATE" in captured.err
        assert "no art pack" in captured.err.lower()

    def test_ensure_art_available_spawns_when_due(
        self, art_dir: Path, monkeypatch
    ):
        # Installed pack + check is due.
        pack = paths.art_pack_dir()
        pack.mkdir(parents=True)
        (pack / ".version").write_text("art-v1.0\n")
        (pack / "alpha_card").mkdir()

        spawned = {"n": 0}
        monkeypatch.setattr(
            checker, "spawn_background_check",
            lambda: spawned.__setitem__("n", spawned["n"] + 1) or 4242,
        )
        checker.ensure_art_available()
        assert spawned["n"] == 1

    def test_ensure_art_available_no_spawn_when_not_due(
        self, art_dir: Path, monkeypatch
    ):
        pack = paths.art_pack_dir()
        pack.mkdir(parents=True)
        (pack / ".version").write_text("art-v1.0\n")
        (pack / "alpha_card").mkdir()

        # Mark a recent check.
        checker.write_last_check({"ts": int(time.time())})
        monkeypatch.setattr(
            checker, "spawn_background_check",
            lambda: pytest.fail("spawn called inside rate-limit window"),
        )
        checker.ensure_art_available()


# ---------------------------------------------------------------------------
# CLI integration — `daimon update`
# ---------------------------------------------------------------------------

class TestCliUpdate:
    def test_update_check_only_prints_status(
        self, art_dir: Path, monkeypatch
    ):
        from daimon.cli import main as cli_main

        rel_json = [_release_json("art-v1.0")]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(rel_json).encode()),
        )

        runner = CliRunner()
        result = runner.invoke(cli_main, ["update", "--check"])
        assert result.exit_code == 0, result.output
        assert "art-v1.0" in result.output
        assert "installed:" in result.output

    def test_update_install_runs_full_flow(
        self, art_dir: Path, monkeypatch, fake_pack_tarball
    ):
        from daimon.cli import main as cli_main

        raw, digest = fake_pack_tarball
        rel_json = [_release_json("art-v1.0", asset_size=len(raw))]
        monkeypatch.setattr(
            api, "urlopen",
            lambda req, timeout=None: FakeHTTPResponse(json.dumps(rel_json).encode()),
        )

        def fake_http_open(url: str, *, octet_stream: bool = False):
            if url.endswith(".sha256"):
                return FakeHTTPResponse(f"{digest}  v1_alpha.tar.gz\n".encode())
            return FakeHTTPResponse(raw)

        monkeypatch.setattr(fetcher, "_http_open", fake_http_open)

        runner = CliRunner()
        result = runner.invoke(cli_main, ["update"])
        assert result.exit_code == 0, result.output
        assert "installed art-v1.0" in result.output
        assert paths.current_version() == "art-v1.0"

    def test_update_handles_network_failure(self, art_dir: Path, monkeypatch):
        from daimon.cli import main as cli_main

        def boom(req, timeout=None):
            raise HTTPError(req.full_url, 500, "boom", {}, None)
        monkeypatch.setattr(api, "urlopen", boom)

        runner = CliRunner()
        result = runner.invoke(cli_main, ["update"])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_pure_command_does_not_trigger_fetch(
        self, art_dir: Path, monkeypatch
    ):
        """`daimon npcs` must NOT call ensure_art_available."""
        from daimon.cli import main as cli_main
        from daimon.update import checker as ck

        called = {"n": 0}

        def trip(*_a, **_kw):
            called["n"] += 1

        monkeypatch.setattr(ck, "ensure_art_available", trip)

        runner = CliRunner()
        result = runner.invoke(cli_main, ["npcs", "--help"])
        assert result.exit_code == 0
        assert called["n"] == 0
