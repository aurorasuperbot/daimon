"""HTTP download + verify + atomic-swap primitives for art-pack updates.

This module exposes the shared building blocks used by the lazy art
pipeline (manifest fetching in :mod:`daimon.update.manifest`, per-card
JIT fetching in :mod:`daimon.update.lazy`):

  * :func:`download_with_progress` — streaming HTTP fetch into a
    ``.partial`` file, atomic rename on success.
  * :func:`sha256_file` / :func:`parse_sha256_sidecar` /
    :func:`fetch_expected_sha256` — verification helpers.
  * :func:`safe_extract_tarball` — tarball extraction that rejects path
    traversal, symlinks, hardlinks, and device nodes.
  * :func:`atomic_swap` — two-syscall rename that replaces a live
    directory with a staged one, leaving a ``.trash.<ts>`` orphan if
    the second rename fails (mopped up on next run).
  * :func:`cleanup_trash` — sweep abandoned trash dirs from prior
    crashed swaps.

The legacy monolithic ``do_update`` flow that downloaded one giant
``v1_alpha.tar.gz`` and swapped the entire pack dir was removed when the
lazy-art rewrite landed; per-card downloads against a manifest replaced
it. The primitives here remain the contract used by both the manifest
fetcher and the per-card fetcher.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from daimon.update import api
from daimon.update.paths import (
    ART_PACK_NAME,
    art_root,
)


CHUNK = 1 << 20  # 1 MiB
HTTP_TIMEOUT = 60  # seconds
USER_AGENT = "daimon-update/1.0 (+https://github.com/aurorasuperbot/daimon)"

# Inline regex for sha256 digests embedded in release notes — 64 lowercase hex.
_SHA256_RE = re.compile(r"\b([0-9a-f]{64})\b", re.IGNORECASE)


class ArtUpdateError(RuntimeError):
    """Any failure during art-pack download / verify / install."""


# ---------------------------------------------------------------------------
# HTTP download
# ---------------------------------------------------------------------------

def _run_silent(args: list[str], stdin_text: str = "") -> Optional[str]:
    """Run a subcommand silently; return stdout on success, None on any failure.

    Used to query auth helpers (``gh auth token``, ``git credential fill``)
    without leaking output to the user's terminal. Times out at 5 s.

    Sets a non-interactive environment so neither git nor an askpass
    helper can pop a login dialog. Without these, `git credential fill`
    on Windows happily opens an auth prompt the moment a background art
    fetch hits a release with no cached PAT — that was the "GitHub
    login spam" bug.

      GIT_TERMINAL_PROMPT=0   — git itself never prompts on a TTY
      GIT_ASKPASS=echo        — backstop: if anything still tries to
                                 prompt, the askpass returns an empty
                                 string and the helper exits non-zero

    NOTE: we do NOT set ``GCM_INTERACTIVE=Never``. GCM uses DPAPI/Windows
    Hello to *unlock* its credential vault, and ``Never`` blocks even
    cached-cred reads — GCM falls through to git's host-level fallback,
    which on a multi-account machine is the WRONG account (e.g. the
    daimon-cards repo's path-specific aurorasuperbot PAT gets shadowed
    by the host-level santi-contextually PAT). The empirical test:
    with Never set, ``git credential fill path=aurorasuperbot/...``
    fails with "Cannot prompt because user interactivity has been
    disabled" and silently returns host-level creds. With it unset,
    GCM reads the cached path-specific PAT non-interactively; the UI
    only opens if there's NO cached cred at all, which is the expected
    first-run UX. The TIMEOUT below caps any stuck prompt to 5s.
    """
    import subprocess
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    try:
        proc = subprocess.run(
            args,
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


@functools.lru_cache(maxsize=1)
def _gh_token_via_helper() -> Optional[str]:
    """Best-effort GitHub token from local helpers. Cached per process.

    Tries each source in order — first non-empty wins. Path-specific git
    credential queries come FIRST: if the user has a per-repo PAT cached
    (Windows Credential Manager keys credentials on the path component
    when ``credential.useHttpPath = true``, and many users have separate
    PATs per org), that's almost certainly the right token for this
    repo; ``gh auth token`` returns whatever account the CLI happens to
    be logged into globally and won't have access to repos owned by
    other accounts.

      1. ``git credential fill`` with the daimon-cards path
      2. ``git credential fill`` host-only (github.com)
      3. ``gh auth token``

    None of these prompt: if no helper is configured, they exit non-zero
    and we fall through. Setting ``GITHUB_TOKEN`` in the environment
    bypasses this layer entirely.
    """
    for query in (
        "protocol=https\nhost=github.com\npath=aurorasuperbot/daimon-cards.git\n\n",
        "protocol=https\nhost=github.com\n\n",
    ):
        out = _run_silent(["git", "credential", "fill"], stdin_text=query)
        if not out:
            continue
        for line in out.splitlines():
            if line.startswith("password="):
                tok = line[len("password="):].strip()
                if tok:
                    return tok

    out = _run_silent(["gh", "auth", "token"])
    if out:
        tok = out.strip()
        if tok:
            return tok
    return None


def _gh_token() -> Optional[str]:
    """Resolve a GitHub token: env first, then local auth helpers."""
    return (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or _gh_token_via_helper()
    )


def _http_open(url: str, *, octet_stream: bool = False):
    """Open ``url`` for streaming. Adds GH auth header if a token is set.

    With ``octet_stream=True``, sends ``Accept: application/octet-stream``
    — required when downloading release assets via the GitHub API URL
    (``https://api.github.com/repos/.../releases/assets/<id>``). Without
    that header, the API returns the asset's JSON metadata instead of
    the bytes. The browser_download_url path doesn't need it.
    """
    headers = {"User-Agent": USER_AGENT}
    if octet_stream:
        headers["Accept"] = "application/octet-stream"
    req = Request(url, headers=headers)
    tok = _gh_token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    return urlopen(req, timeout=HTTP_TIMEOUT)


def _pick_asset_url(
    browser_url: str,
    api_url: str,
) -> tuple[str, bool]:
    """Pick the right URL + Accept header based on auth state.

    Returns ``(url, needs_octet_stream)``.

    If a ``GITHUB_TOKEN`` is set we ALWAYS prefer the API URL — it works
    for both private and public repos, with the bearer-auth headers we
    already attach. Without a token, ``browser_download_url`` is the only
    path that works for public repos (it 302-redirects to a signed S3
    URL; the API URL would 404 anonymously on public-not-in-org repos).
    """
    has_token = bool(_gh_token())
    if has_token and api_url:
        return api_url, True
    return browser_url or api_url, False


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} GB"


def _print_progress(prefix: str, done: int, total: int) -> None:
    """Single-line progress to stderr. tqdm-free, no extra deps."""
    if total <= 0:
        sys.stderr.write(f"\r{prefix} {_human_bytes(done)}    ")
    else:
        pct = done * 100 // total
        bar_w = 24
        fill = (pct * bar_w) // 100
        bar = "█" * fill + "░" * (bar_w - fill)
        sys.stderr.write(
            f"\r{prefix} [{bar}] {pct:3}%  "
            f"{_human_bytes(done)} / {_human_bytes(total)}    "
        )
    sys.stderr.flush()


def download_with_progress(
    url: str,
    dest: Path,
    expected_size: int = 0,
    label: str = "fetching",
    show_progress: bool = True,
    octet_stream: bool = False,
) -> Path:
    """Stream ``url`` → ``dest.partial`` → ``dest``. Returns ``dest``.

    Atomic on the destination side: the ``.partial`` rename happens only
    after a clean read, so a half-finished file never appears at ``dest``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()

    try:
        with _http_open(url, octet_stream=octet_stream) as resp:
            total = expected_size or int(resp.headers.get("Content-Length") or 0)
            done = 0
            last_print = 0.0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if show_progress and (now - last_print) > 0.1:
                        _print_progress(label, done, total)
                        last_print = now
            if show_progress:
                _print_progress(label, done, total)
                sys.stderr.write("\n")
                sys.stderr.flush()
    except (HTTPError, URLError, OSError) as e:
        if tmp.exists():
            tmp.unlink()
        raise ArtUpdateError(f"download failed: {e}") from e

    tmp.replace(dest)
    return dest


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    """Stream sha256 over ``path``. Returns lowercase hex."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def parse_sha256_sidecar(text: str) -> Optional[str]:
    """Parse ``sha256sum`` output: ``<hex>  <filename>`` → bare hex."""
    text = text.strip()
    if not text:
        return None
    parts = text.split()
    if not parts:
        return None
    cand = parts[0].lower()
    if len(cand) == 64 and all(c in "0123456789abcdef" for c in cand):
        return cand
    return None


def fetch_expected_sha256(release: api.ReleaseInfo) -> Optional[str]:
    """Resolve the sha256 we should match against.

    Order of preference:
      1. ``<asset>.sha256`` sidecar asset (cleanest).
      2. First 64-hex token in the release body.
      3. None — caller must decide whether to abort or trust-on-first-use.
    """
    if release.sha256_url or release.sha256_api_url:
        url, octet = _pick_asset_url(
            release.sha256_url or "",
            release.sha256_api_url or "",
        )
        try:
            with _http_open(url, octet_stream=octet) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            digest = parse_sha256_sidecar(text)
            if digest:
                return digest
        except (HTTPError, URLError, OSError):
            pass

    if release.body:
        m = _SHA256_RE.search(release.body)
        if m:
            return m.group(1).lower()

    return None


# ---------------------------------------------------------------------------
# Extraction (safe — no path traversal)
# ---------------------------------------------------------------------------

def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_extract_tarball(tar_path: Path, dest_dir: Path) -> None:
    """Extract ``tar_path`` under ``dest_dir`` rejecting absolute paths,
    parent-traversals, symlinks, hardlinks, and device nodes.

    The art tarball is regular-files-only by construction; we treat anything
    else as malicious and abort.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                raise ArtUpdateError(
                    f"refusing to extract link member: {member.name!r}"
                )
            if member.isdev() or member.isfifo():
                raise ArtUpdateError(
                    f"refusing to extract device/fifo member: {member.name!r}"
                )
            target = (dest_dir / member.name)
            if not _is_within(target, dest_dir):
                raise ArtUpdateError(
                    f"refusing path-traversal extract: {member.name!r}"
                )
        # Second pass to actually extract — getmembers is cheap and we want
        # to fail BEFORE writing any bytes. ``filter='data'`` is the modern
        # tarfile safe-extract default (Python 3.12+); it strips ownership /
        # special-mode bits and re-applies the symlink/device guards. We
        # already screened above, but defense-in-depth never hurts.
        try:
            tf.extractall(dest_dir, filter="data")  # noqa: S202 — vetted above
        except TypeError:
            # Older Python without the `filter=` kwarg (3.11 < 3.11.4).
            tf.extractall(dest_dir)  # noqa: S202 — vetted above


# ---------------------------------------------------------------------------
# Atomic swap
# ---------------------------------------------------------------------------

def _trash_path(live: Path) -> Path:
    return live.parent / f"{live.name}.trash.{int(time.time())}"


def atomic_swap(staged_pack_dir: Path, live_pack_dir: Path) -> None:
    """Replace ``live_pack_dir`` with ``staged_pack_dir``.

    Both must live on the same filesystem (same root in our layout).

    Sequence:
      1. If live exists: rename → trash (one syscall).
      2. Rename staged → live (one syscall).
      3. Best-effort delete trash. Failure here is non-fatal — the next
         invocation will mop it up.

    Crash recovery:
      * Crash between (1) and (2): live is missing; next run re-downloads.
      * Crash between (2) and (3): trash is left behind; mopped next run.
    """
    live_pack_dir.parent.mkdir(parents=True, exist_ok=True)
    trash: Optional[Path] = None
    if live_pack_dir.exists():
        trash = _trash_path(live_pack_dir)
        live_pack_dir.rename(trash)
    try:
        staged_pack_dir.rename(live_pack_dir)
    except OSError as e:
        # Roll back: put the old pack back so the user isn't art-less.
        if trash is not None and trash.exists():
            try:
                trash.rename(live_pack_dir)
            except OSError:
                pass
        raise ArtUpdateError(f"swap failed: {e}") from e

    if trash is not None and trash.exists():
        shutil.rmtree(trash, ignore_errors=True)


def cleanup_trash(pack_name: str = ART_PACK_NAME) -> None:
    """Sweep abandoned ``art/<pack>.trash.*`` dirs from prior crashed swaps."""
    art_dir = art_root() / "art"
    if not art_dir.is_dir():
        return
    for child in art_dir.iterdir():
        if child.is_dir() and child.name.startswith(f"{pack_name}.trash."):
            shutil.rmtree(child, ignore_errors=True)


__all__ = [
    "ArtUpdateError",
    "download_with_progress",
    "sha256_file",
    "parse_sha256_sidecar",
    "fetch_expected_sha256",
    "safe_extract_tarball",
    "atomic_swap",
    "cleanup_trash",
]
