"""Unit tests for daimon.arena.client — the gh-CLI subprocess wrapper.

The wrapper's job is to turn a ragged set of subprocess failure modes
(missing binary, timeout, non-zero exit, auth error, rate limit) into a
uniform ``{"ok": False, "error": "...", "message": "..."}`` envelope the
caller can compose without try/except gymnastics. These tests pin that
mapping.

We DO NOT test against a real `gh` binary — every test here monkeypatches
the dispatcher boundary (``shutil.which`` or ``subprocess.run``) so the
suite is hermetic and fast.
"""

from __future__ import annotations

import subprocess

from daimon.arena import client as arena_client


# ---------------------------------------------------------------------------
# _run — success + failure envelopes
# ---------------------------------------------------------------------------

def test_run_returns_gh_missing_when_not_on_path(monkeypatch):
    monkeypatch.setattr(arena_client.shutil, "which", lambda name: None)
    res = arena_client._run(["gh", "issue", "list"])
    assert res == {
        "ok": False,
        "error": "gh_missing",
        "message": res["message"],
    }
    assert "gh" in res["message"].lower()


def test_run_success_envelope(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = "hello\n"
        stderr = ""
    monkeypatch.setattr(arena_client.shutil, "which", lambda name: "/u/b/gh")
    monkeypatch.setattr(
        arena_client.subprocess, "run",
        lambda *a, **kw: FakeProc(),
    )
    res = arena_client._run(["gh", "issue", "list"])
    assert res == {"ok": True, "stdout": "hello\n", "stderr": ""}


def test_run_timeout_envelope(monkeypatch):
    monkeypatch.setattr(arena_client.shutil, "which", lambda name: "/u/b/gh")
    def raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=5)
    monkeypatch.setattr(arena_client.subprocess, "run", raise_timeout)
    res = arena_client._run(["gh", "issue", "view", "42"], timeout=5)
    assert res["ok"] is False
    assert res["error"] == "gh_timeout"
    assert "5s" in res["message"]


def test_run_classifies_401_as_auth(monkeypatch):
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "HTTP 401: Bad credentials"
    monkeypatch.setattr(arena_client.shutil, "which", lambda name: "/u/b/gh")
    monkeypatch.setattr(arena_client.subprocess, "run", lambda *a, **kw: FakeProc())
    res = arena_client._run(["gh", "x"])
    assert res["error"] == "gh_auth"
    assert res["exit_code"] == 1


def test_run_classifies_403_as_auth(monkeypatch):
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "HTTP 403: forbidden"
    monkeypatch.setattr(arena_client.shutil, "which", lambda name: "/u/b/gh")
    monkeypatch.setattr(arena_client.subprocess, "run", lambda *a, **kw: FakeProc())
    res = arena_client._run(["gh", "x"])
    assert res["error"] == "gh_auth"


def test_run_generic_failure_keeps_exit_code(monkeypatch):
    class FakeProc:
        returncode = 42
        stdout = ""
        stderr = "something else broke"
    monkeypatch.setattr(arena_client.shutil, "which", lambda name: "/u/b/gh")
    monkeypatch.setattr(arena_client.subprocess, "run", lambda *a, **kw: FakeProc())
    res = arena_client._run(["gh", "x"])
    assert res["error"] == "gh_failed"
    assert res["exit_code"] == 42
    assert "something else broke" in res["message"]


# ---------------------------------------------------------------------------
# create_issue — parses gh's URL output into an issue_number
# ---------------------------------------------------------------------------

def test_create_issue_parses_url(monkeypatch):
    def fake_run(argv, input_text=None, timeout=20):
        return {"ok": True,
                "stdout": "https://github.com/org/repo/issues/123\n",
                "stderr": ""}
    monkeypatch.setattr(arena_client, "_run", fake_run)
    res = arena_client.create_issue("org/repo", "t", "b", labels=["x"])
    assert res["ok"] is True
    assert res["issue_number"] == 123
    assert res["url"] == "https://github.com/org/repo/issues/123"


def test_create_issue_unparseable_url_returns_parse_error(monkeypatch):
    def fake_run(argv, input_text=None, timeout=20):
        return {"ok": True, "stdout": "something weird\n", "stderr": ""}
    monkeypatch.setattr(arena_client, "_run", fake_run)
    res = arena_client.create_issue("org/repo", "t", "b")
    assert res["ok"] is False
    assert res["error"] == "gh_parse"


def test_create_issue_propagates_underlying_failure(monkeypatch):
    def fake_run(argv, input_text=None, timeout=20):
        return {"ok": False, "error": "gh_auth", "message": "401"}
    monkeypatch.setattr(arena_client, "_run", fake_run)
    res = arena_client.create_issue("org/repo", "t", "b")
    assert res == {"ok": False, "error": "gh_auth", "message": "401"}


# ---------------------------------------------------------------------------
# fetch_repo_file — raw.githubusercontent path + 404 handling
# ---------------------------------------------------------------------------

def test_fetch_repo_file_decodes_json(monkeypatch):
    import io
    import urllib.request
    def fake_urlopen(url, timeout=0):
        class R:
            def __enter__(self_inner):
                return io.BytesIO(b'{"x": 1}')
            def __exit__(self_inner, *a):
                return False
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    res = arena_client.fetch_repo_file("org/repo", "leaderboard.json")
    assert res["ok"] is True
    assert res["content"] == {"x": 1}


def test_fetch_repo_file_404_returns_not_found(monkeypatch):
    import urllib.error
    import urllib.request
    def fake_urlopen(url, timeout=0):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    res = arena_client.fetch_repo_file("org/repo", "missing.json")
    assert res["ok"] is False
    assert res["error"] == "not_found"


def test_fetch_repo_file_plain_text_not_parsed_as_json(monkeypatch):
    import io
    import urllib.request
    def fake_urlopen(url, timeout=0):
        class R:
            def __enter__(self_inner):
                return io.BytesIO(b"hello world")
            def __exit__(self_inner, *a):
                return False
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    res = arena_client.fetch_repo_file("org/repo", "README.md")
    assert res["ok"] is True
    assert res["content"] == "hello world"
    assert res["raw"] == "hello world"
