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

import json
import shutil
import subprocess
import urllib.request
from typing import Any, Dict, List, Optional

# Default timeout per gh call. Raised slightly for `gh issue create` which
# can be slow on first auth handshake but kept short enough that an MCP
# tool never blocks the agent's chat indefinitely.
DEFAULT_TIMEOUT_S = 20

# Default raw.githubusercontent base — overridable for test fixtures.
RAW_GITHUB_BASE = "https://raw.githubusercontent.com"


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
    """Fetch a file from a (public OR private) repo.

    For public repos this hits raw.githubusercontent.com directly. For
    private repos that fails, so we fall back to ``gh api`` which carries
    auth. Returns parsed JSON if the path ends in ``.json``, else raw text.

    On success:
      ``{"ok": True, "content": <parsed-or-raw>, "raw": "<bytes-as-text>"}``
    On 404:
      ``{"ok": False, "error": "not_found", "message": "..."}``
    """
    # Try raw.githubusercontent first — fastest, no auth handshake.
    raw_url = f"{RAW_GITHUB_BASE}/{repo}/{ref}/{path}"
    try:
        with urllib.request.urlopen(raw_url, timeout=timeout) as resp:  # noqa: S310
            text = resp.read().decode("utf-8")
        return _decode_file_content(text, path)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "error": "not_found",
                    "message": f"{path} not present at {ref}"}
        # 401/403 likely means private repo — fall through to gh api auth path.
        if e.code not in (401, 403):
            return {"ok": False, "error": "http_error",
                    "message": f"HTTP {e.code} fetching {raw_url}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": "network",
                "message": f"network error fetching {raw_url}: {e.reason}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "fetch_failed",
                "message": f"{type(e).__name__}: {e}"}

    # Fallback: authenticated gh api call.
    argv = ["gh", "api",
            f"repos/{repo}/contents/{path}",
            "--jq", ".content"]
    if ref != "main":
        argv = ["gh", "api",
                f"repos/{repo}/contents/{path}?ref={ref}",
                "--jq", ".content"]
    res = _run(argv, timeout=timeout)
    if not res["ok"]:
        return res
    import base64
    try:
        decoded = base64.b64decode((res["stdout"] or "").strip()).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "decode_failed",
                "message": f"could not decode gh api response: {e}"}
    return _decode_file_content(decoded, path)


def _decode_file_content(text: str, path: str) -> Dict[str, Any]:
    if path.endswith(".json"):
        try:
            return {"ok": True, "content": json.loads(text), "raw": text}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": "json_parse",
                    "message": f"could not parse {path} as JSON: {e}",
                    "raw": text}
    return {"ok": True, "content": text, "raw": text}
