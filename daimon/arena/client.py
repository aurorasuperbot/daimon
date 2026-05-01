"""``gh`` CLI subprocess wrapper for arena operations.

All arena-bound side effects route through here. The wrapper:

  - Captures structured ``{"ok": True, ...}`` / ``{"ok": False, "error": ...}``
    envelopes that the ops layer can compose without try/except gymnastics.
  - Times out hard (default 20s per call) so a hung arena issue can't lock
    up the MCP server.
  - Surfaces the ``gh`` exit code + stderr for genuinely-broken cases
    (auth gone, repo deleted, rate limit) without dumping a full Python
    traceback into the agent's chat history.
  - Has zero coupling to engine internals — easy to mock in tests by
    monkey-patching the ``_run`` dispatcher.

The wrapper does **NOT** retry. Network blips are exposed as-is so the
agent (or its caller) can decide policy. The PvP flow is async-friendly
anyway — losing one ``gh`` call only means the agent re-runs the same
``dm_pvp_*`` tool, which is idempotent at the protocol level (the commit
is already on the wire).
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from daimon._winspawn import windowless_creationflags

# Default timeout per gh call. Raised slightly for `gh issue create` which
# can be slow on first auth handshake but kept short enough that an MCP
# tool never blocks the agent's chat indefinitely.
DEFAULT_TIMEOUT_S = 20


# ---------------------------------------------------------------------------
# Low-level subprocess dispatcher
# ---------------------------------------------------------------------------

def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _run(argv: List[str],
         input_text: Optional[str] = None,
         timeout: int = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    """Run a gh subprocess with a uniform success/failure envelope.

    Returns:
      Success: ``{"ok": True, "stdout": "<text>", "stderr": "<text>"}``
      Failure: ``{"ok": False, "error": "<category>", "message": "<detail>",
                  "exit_code": int, "stderr": "<text>"}``

    Failure categories:
      - ``gh_missing``  : gh CLI not on PATH
      - ``gh_timeout``  : process exceeded timeout
      - ``gh_auth``     : exit code suggests auth problem (401/403 in stderr)
      - ``gh_failed``   : non-zero exit, generic
    """
    if not _gh_available():
        return {
            "ok": False,
            "error": "gh_missing",
            "message": (
                "The 'gh' CLI is not installed or not on PATH. Install it "
                "from https://cli.github.com and run `gh auth login`."
            ),
        }
    try:
        proc = subprocess.run(
            argv,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=windowless_creationflags(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "gh_timeout",
            "message": f"gh call timed out after {timeout}s: {' '.join(argv[:3])}…",
        }
    if proc.returncode == 0:
        return {"ok": True, "stdout": proc.stdout, "stderr": proc.stderr}
    err = (proc.stderr or "").strip()
    err_low = err.lower()
    if "401" in err_low or "403" in err_low or "authentication" in err_low:
        category = "gh_auth"
    else:
        category = "gh_failed"
    return {
        "ok": False,
        "error": category,
        "message": err or f"gh exit code {proc.returncode}",
        "exit_code": proc.returncode,
        "stderr": err,
    }


# ---------------------------------------------------------------------------
# Issue + comment operations
# ---------------------------------------------------------------------------

def create_issue(repo: str,
                 title: str,
                 body: str,
                 labels: Optional[List[str]] = None,
                 timeout: int = 30) -> Dict[str, Any]:
    """Open an Issue on ``repo`` and return the parsed result.

    On success::
        {"ok": True, "issue_number": 42, "url": "https://github.com/.../42"}

    Body is passed via ``--body-file -`` on stdin so quotes / newlines /
    backticks don't have to be re-escaped at the shell layer.
    """
    argv = ["gh", "issue", "create",
            "--repo", repo,
            "--title", title,
            "--body-file", "-"]
    for lbl in (labels or []):
        argv.extend(["--label", lbl])
    res = _run(argv, input_text=body, timeout=timeout)
    if not res["ok"]:
        return res
    # gh prints the issue URL on stdout. Issue number is the trailing path segment.
    url = (res["stdout"] or "").strip().splitlines()[-1].strip()
    issue_number: Optional[int] = None
    if url and "/" in url:
        tail = url.rsplit("/", 1)[-1]
        if tail.isdigit():
            issue_number = int(tail)
    if issue_number is None:
        return {
            "ok": False,
            "error": "gh_parse",
            "message": f"could not parse issue number from gh output: {url!r}",
            "stdout": res["stdout"],
        }
    return {"ok": True, "issue_number": issue_number, "url": url}


def comment_issue(repo: str,
                  issue_number: int,
                  body: str,
                  timeout: int = 20) -> Dict[str, Any]:
    """Post a comment on an existing Issue.

    On success::
        {"ok": True, "url": "https://github.com/.../#issuecomment-123"}
    """
    argv = ["gh", "issue", "comment", str(issue_number),
            "--repo", repo,
            "--body-file", "-"]
    res = _run(argv, input_text=body, timeout=timeout)
    if not res["ok"]:
        return res
    url = (res["stdout"] or "").strip().splitlines()[-1].strip()
    return {"ok": True, "url": url}


def view_issue(repo: str,
               issue_number: int,
               timeout: int = 20) -> Dict[str, Any]:
    """Fetch an Issue's metadata + body + comments.

    On success::
        {
          "ok": True,
          "issue": {
            "number": 42,
            "title": "...",
            "body": "...",
            "state": "OPEN" | "CLOSED",
            "labels": [{"name": "..."}, ...],
            "comments": [{"id": "...", "author": {"login": "..."}, "body": "..."}, ...],
            "url": "..."
          }
        }
    """
    fields = "number,title,body,state,labels,comments,url"
    argv = ["gh", "issue", "view", str(issue_number),
            "--repo", repo,
            "--json", fields]
    res = _run(argv, timeout=timeout)
    if not res["ok"]:
        return res
    try:
        issue = json.loads(res["stdout"])
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "error": "gh_parse",
            "message": f"could not parse gh issue view output: {e}",
            "stdout": res["stdout"],
        }
    return {"ok": True, "issue": issue}


def list_issues(repo: str,
                labels: Optional[List[str]] = None,
                state: str = "all",
                limit: int = 100,
                timeout: int = 30) -> Dict[str, Any]:
    """List Issues matching the given labels + state.

    ``state`` is one of ``"open"``, ``"closed"``, ``"all"``.
    Returns ``{"ok": True, "issues": [{number, title, body, labels, state, url, ...}, ...]}``.

    Used by ``dm_pvp_my_matches`` — body is included so the caller can
    filter by pubkey embedded in the body kv pairs.
    """
    if state not in ("open", "closed", "all"):
        return {"ok": False, "error": "invalid_input",
                "message": "state must be one of open/closed/all"}
    fields = "number,title,body,state,labels,url,createdAt,updatedAt"
    argv = ["gh", "issue", "list",
            "--repo", repo,
            "--state", state,
            "--limit", str(limit),
            "--json", fields]
    for lbl in (labels or []):
        argv.extend(["--label", lbl])
    res = _run(argv, timeout=timeout)
    if not res["ok"]:
        return res
    try:
        issues = json.loads(res["stdout"])
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "error": "gh_parse",
            "message": f"could not parse gh issue list output: {e}",
            "stdout": res["stdout"],
        }
    return {"ok": True, "issues": issues}


# ---------------------------------------------------------------------------
# Raw file fetch (leaderboard.json, matches/<id>.json, etc.)
# ---------------------------------------------------------------------------

def fetch_repo_file(repo: str,
                    path: str,
                    ref: str = "main",
                    timeout: int = 15) -> Dict[str, Any]:
    """Fetch a file from a (public OR private) repo via the GitHub API.

    Uses ``gh api repos/<repo>/contents/<path>`` exclusively. We deliberately
    do NOT route through ``raw.githubusercontent.com``:

      - raw.githubusercontent returns 404 for private repos without auth,
        which is indistinguishable from "the file truly doesn't exist".
        Pre-V1-public-launch the arena is private; that path was fundamentally
        broken (and only worked in tests because urlopen was mocked).
      - For freshly-pushed files (e.g. ``matches/N.json`` posted by the
        arbiter seconds before a ``dm_pvp_status`` poll), raw.githubusercontent
        has a CDN propagation lag of ~minutes. The API endpoint reads from
        the canonical store directly, so a poll right after settlement
        sees the file immediately.

    Returns parsed JSON if the path ends in ``.json``, else raw text.

    On success:
      ``{"ok": True, "content": <parsed-or-raw>, "raw": "<bytes-as-text>"}``
    On 404 (file truly absent at ref):
      ``{"ok": False, "error": "not_found", "message": "..."}``
    On other gh failure:
      ``{"ok": False, "error": "<gh_*>", "message": "..."}``
    """
    if ".." in path or path.startswith("/"):
        return {"ok": False, "error": "invalid_input",
                "message": "path must not contain '..' or start with '/'"}
    if ref == "main":
        endpoint = f"repos/{repo}/contents/{path}"
    else:
        endpoint = f"repos/{repo}/contents/{path}?ref={quote(ref, safe='')}"
    res = _run(["gh", "api", endpoint, "--jq", ".content"], timeout=timeout)
    if not res["ok"]:
        # The contents endpoint returns 404 for missing files; gh surfaces
        # that as a non-zero exit with "Not Found" in stderr. Re-categorize
        # so callers can distinguish "missing" from "auth gone".
        msg = (res.get("message") or "").lower()
        stderr = (res.get("stderr") or "").lower()
        if "not found" in msg or "not found" in stderr or "404" in stderr:
            return {"ok": False, "error": "not_found",
                    "message": f"{path} not present at {ref}"}
        return res
    encoded = (res["stdout"] or "").strip()
    if not encoded:
        # Defensive: gh returned 0 but no payload — treat as not_found rather
        # than crash on b64 decode of empty string.
        return {"ok": False, "error": "not_found",
                "message": f"{path} returned empty payload at {ref}"}
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "decode_failed",
                "message": f"could not decode gh api response: {e}"}
    return _decode_file_content(decoded, path)


# ---------------------------------------------------------------------------
# Player state reads (state/{username}/*.json)
# ---------------------------------------------------------------------------

def fetch_arbiter_pubkey(repo: str,
                        timeout: int = 15) -> Dict[str, Any]:
    """Fetch ``arbiter_pubkey.json`` from the arena repo root.

    On success::
        {"ok": True, "content": {"pubkey_hex": "..."}, "raw": "..."}
    """
    return fetch_repo_file(repo, "arbiter_pubkey.json", timeout=timeout)


def fetch_player_balance(repo: str, username: str,
                         timeout: int = 15) -> Dict[str, Any]:
    """Fetch ``state/{username}/balance.json`` from the arena repo."""
    return fetch_repo_file(repo, f"state/{username}/balance.json",
                           timeout=timeout)


def fetch_player_collection(repo: str, username: str,
                            timeout: int = 15) -> Dict[str, Any]:
    """Fetch ``state/{username}/collection.json`` from the arena repo."""
    return fetch_repo_file(repo, f"state/{username}/collection.json",
                           timeout=timeout)


def fetch_player_tickets(repo: str, username: str,
                         timeout: int = 15) -> Dict[str, Any]:
    """Fetch ``state/{username}/tickets/pending.json`` from the arena repo."""
    return fetch_repo_file(repo, f"state/{username}/tickets/pending.json",
                           timeout=timeout)


# ---------------------------------------------------------------------------
# GitHub user identity
# ---------------------------------------------------------------------------

def get_github_user(timeout: int = 10) -> Dict[str, Any]:
    """Fetch the authenticated GitHub user via ``gh api user``.

    On success::
        {"ok": True, "login": "alice", "id": 12345,
         "avatar_url": "https://avatars.githubusercontent.com/..."}

    Requires ``gh auth login`` to have been run at least once.
    """
    res = _run(["gh", "api", "user"], timeout=timeout)
    if not res["ok"]:
        return res
    try:
        user = json.loads(res["stdout"])
    except json.JSONDecodeError as e:
        return {"ok": False, "error": "gh_parse",
                "message": f"could not parse gh api user output: {e}",
                "stdout": res["stdout"]}
    return {
        "ok": True,
        "login": user.get("login"),
        "id": user.get("id"),
        "avatar_url": user.get("avatar_url"),
        "name": user.get("name"),
    }


def _decode_file_content(text: str, path: str) -> Dict[str, Any]:
    if path.endswith(".json"):
        try:
            return {"ok": True, "content": json.loads(text), "raw": text}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": "json_parse",
                    "message": f"could not parse {path} as JSON: {e}",
                    "raw": text}
    return {"ok": True, "content": text, "raw": text}
