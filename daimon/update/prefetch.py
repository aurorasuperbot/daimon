"""Background prefetcher — populate per-card art ahead of demand.

After ``daimon onboard`` lands the manifest + the starter card art (~10
cards, instant first pull), the prefetcher runs in a detached subprocess
to fill in the rest of the cards over the next minute or two. By the
time the user picks their first NPC fight, every card is on disk and
the runtime never blocks on a cache miss.

Design notes
------------

  * **Resumable**. State persists at ``cache/prefetch_state.json``.
    Re-running with the same manifest skips already-fetched cards via
    :func:`is_card_cached` — the state file is for human-facing progress
    reporting, not the source of truth (the cache is).

  * **Concurrent**. Default 4 workers via
    :class:`concurrent.futures.ThreadPoolExecutor`. Each fetch is
    bounded by the per-card tarball size (~50–500 KB) so concurrency
    helps mostly on TCP setup time, not bytes.

  * **Failure-tolerant**. A bad sha256 or transient network error on one
    card doesn't abort the whole run; the failure is recorded and the
    prefetcher moves on. Retries (with backoff) happen the next time
    the prefetcher runs, or on the next render cache-miss via
    :func:`ensure_art_for`.

  * **SIGINT-safe**. Each card lands atomically (the per-card swap
    pattern from :mod:`daimon.update.lazy`), so a Ctrl-C between cards
    leaves the cache consistent. The state file gets one final write
    on a clean exit; an unclean exit is detected on next run by
    ``completed_at is None`` and ``started_at < now - 1h``, which
    triggers a re-walk of the manifest.

  * **Opt-out**. ``DAIMON_NO_AUTO_UPDATE=1`` short-circuits both the
    spawn-from-onboard call and the subprocess entry — no network calls
    at all.

Subprocess entry: ``python -m daimon.update.prefetch [--workers N]``.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from daimon._winspawn import windowless_creationflags, windowless_python
from daimon.update.fetcher import ArtUpdateError
from daimon.update.lazy import (
    cleanup_card_staging,
    fetch_card,
    is_card_cached,
)
from daimon.update.manifest import Manifest, load_manifest
from daimon.update.paths import (
    ART_PACK_NAME,
    auto_update_enabled,
    cache_dir,
    prefetch_state_path,
)


DEFAULT_WORKERS = 4
PREFETCH_LOG_NAME = "prefetch.log"
PREFETCH_LOCK_NAME = "prefetch.lock"


# ---------------------------------------------------------------------------
# Singleton lock — prevents two prefetchers from racing on the same cache
# ---------------------------------------------------------------------------

def _prefetch_lock_path() -> Path:
    return cache_dir() / PREFETCH_LOCK_NAME


def _pid_alive(pid: int) -> bool:
    """Cheap liveness check. Mirrors ``daimon.play.spawn._pid_alive``."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but isn't ours — treat as alive (don't steal the lock).
        return True
    except OSError:
        return False
    return True


def acquire_prefetch_lock() -> Optional[int]:
    """Try to acquire the singleton prefetcher lock. Atomic.

    Returns the file descriptor on success (caller is responsible for
    eventual release via :func:`release_prefetch_lock`), or ``None``
    when another prefetcher already holds it.

    Implementation: ``O_CREAT|O_EXCL`` is the only POSIX-portable way
    to do "create-if-not-exists, fail otherwise" without a TOCTOU race.
    Windows does not support O_EXCL on file create the same way, but
    Python's ``os.open`` translates it via the standard Windows
    semantics (CREATE_NEW), which fails if the file exists — same
    contract.

    Stale lock recovery: if the lockfile already exists, we read the
    embedded ``pid`` and check whether that PID is still alive. A
    crashed prefetcher (SIGKILL'd, OOM, power loss) leaves an
    orphan lockfile with a dead PID — we silently steal it. This
    avoids the "user has to manually rm the lockfile" failure mode
    that classic O_EXCL pidfiles suffer from.

    Two onboards racing each other are the primary case this guards:
    without the lock, both would spawn a prefetcher subprocess, both
    would walk the same manifest, both would download every card —
    bandwidth doubled, per-card temp dir collisions only avoided
    because :func:`daimon.update.lazy._unique_staging` namespaces by
    PID. With the lock, the second onboard's spawn returns immediately
    and the first run completes uncontested.
    """
    p = _prefetch_lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(p), flags, 0o644)
    except FileExistsError:
        # Existing lock — check liveness, steal if dead.
        if _is_lock_stale(p):
            try:
                p.unlink()
            except FileNotFoundError:
                pass  # raced with another stealer — let them have it
            try:
                fd = os.open(str(p), flags, 0o644)
            except FileExistsError:
                return None  # someone else stole it first
        else:
            return None
    # Write our PID so other processes can check liveness on us.
    try:
        os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
        os.fsync(fd)
    except OSError:
        # Couldn't even write our PID — give up cleanly.
        try:
            os.close(fd)
            p.unlink()
        except (OSError, FileNotFoundError):
            pass
        return None
    return fd


def _is_lock_stale(path: Path) -> bool:
    """Read the PID from a lockfile and check whether it's still alive."""
    try:
        contents = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        # Can't read it — treat as stale rather than block forever.
        return True
    if not contents:
        return True
    try:
        owner_pid = int(contents.splitlines()[0])
    except (ValueError, IndexError):
        return True
    return not _pid_alive(owner_pid)


def _existing_prefetch_alive() -> bool:
    """Optimistic singleton check for the spawn side.

    Reads the lockfile (if any) and returns ``True`` only when the owner
    PID is still alive. The lockfile-existence check alone is not enough
    — a SIGKILL'd prefetcher leaves an orphan, and we don't want one
    crash to block prefetching forever.

    This is racy by definition (two callers can both pre-check, both see
    "no lock", both spawn, then one child wins the O_EXCL acquire and
    the other exits cleanly). The race is fine: the *child*'s
    :func:`acquire_prefetch_lock` is the definitive guard. The pre-check
    is purely an optimization to skip the fork/exec when we already
    know it's pointless.
    """
    p = _prefetch_lock_path()
    if not p.is_file():
        return False
    return not _is_lock_stale(p)


def release_prefetch_lock(fd: int) -> None:
    """Release the singleton prefetcher lock. Best-effort.

    Closes the fd and unlinks the lockfile. Both operations are
    individually best-effort — a missing lockfile (someone else
    cleaned it up) is fine; a failed close is logged but not
    propagated. The next prefetch run will recover via
    ``_is_lock_stale`` on the orphan lockfile.
    """
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        _prefetch_lock_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------

@dataclass
class PrefetchState:
    """Mutable progress record for the background prefetcher.

    ``failed`` is a list of ``[card_id, message]`` pairs. ``failed_count``
    counts unique card_ids; a card that fails twice across two prefetch
    runs (e.g. flaky network) appears once in the list with the latest
    message.
    """
    manifest_version: str
    pack_name: str
    started_at: int
    completed_at: Optional[int] = None
    total: int = 0
    fetched_count: int = 0
    skipped_count: int = 0
    failed: list[list[str]] = field(default_factory=list)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    def to_dict(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "pack_name": self.pack_name,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total": self.total,
            "fetched_count": self.fetched_count,
            "skipped_count": self.skipped_count,
            "failed": [list(p) for p in self.failed],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PrefetchState":
        failed_raw = d.get("failed", [])
        failed: list[list[str]] = []
        if isinstance(failed_raw, list):
            for entry in failed_raw:
                if isinstance(entry, (list, tuple)) and len(entry) == 2:
                    failed.append([str(entry[0]), str(entry[1])])
        return cls(
            manifest_version=str(d["manifest_version"]),
            pack_name=str(d.get("pack_name") or ART_PACK_NAME),
            started_at=int(d["started_at"]),
            completed_at=(int(d["completed_at"])
                          if d.get("completed_at") is not None else None),
            total=int(d.get("total", 0)),
            fetched_count=int(d.get("fetched_count", 0)),
            skipped_count=int(d.get("skipped_count", 0)),
            failed=failed,
        )


def read_state() -> Optional[PrefetchState]:
    """Load the persisted prefetch state, or ``None`` if absent / corrupt."""
    p = prefetch_state_path()
    if not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return PrefetchState.from_dict(data)
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def write_state(state: PrefetchState) -> Path:
    """Atomic write — tempfile + rename so a crash mid-write is safe."""
    p = prefetch_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


# ---------------------------------------------------------------------------
# The prefetch loop
# ---------------------------------------------------------------------------

def run_prefetch(
    *,
    manifest: Optional[Manifest] = None,
    pack_name: str = ART_PACK_NAME,
    workers: int = DEFAULT_WORKERS,
    log_stream=None,
    skip_card_ids: Sequence[str] = (),
) -> PrefetchState:
    """Materialize every card in the manifest. Idempotent.

    Cards already on disk (per :func:`is_card_cached`) are counted as
    ``skipped`` and not re-fetched. Cards that fail are recorded in
    ``failed`` and the loop continues. The state file is written once
    at start and once at end (success or failure); intra-run progress
    is approximate by design — card-level updates would balloon write
    amplification on a 200-card pack.

    Args:
        manifest: a pre-loaded manifest. Loaded from disk if ``None``.
        pack_name: which pack to prefetch into.
        workers: parallel worker count. Default 4.
        log_stream: optional file-like for per-card status lines.
            Defaults to ``sys.stderr`` when called from CLI; the
            subprocess entry routes this to ``cache/prefetch.log``.
        skip_card_ids: extra ids to skip beyond what's already cached
            (useful for the onboard flow, which skips the starter pack
            it already fetched synchronously).

    Returns the final :class:`PrefetchState`. When another prefetcher
    already holds the singleton lock, returns a no-op state with
    ``manifest_version=""`` and ``total=0`` (and writes nothing to disk
    — clobbering the active prefetcher's state file would corrupt its
    progress reporting).
    """
    log = log_stream if log_stream is not None else sys.stderr

    # Singleton acquire. The lock guarantees at most one prefetcher per
    # cache directory at a time. If we lose the race, exit cleanly so
    # the parent process / CLI sees a successful no-op rather than an
    # error — onboard spawns are explicitly fire-and-forget and a
    # contended lock is the *expected* outcome of the second of two
    # racing onboards.
    lock_fd = acquire_prefetch_lock()
    if lock_fd is None:
        log.write(
            "prefetch: another prefetcher already holds the singleton "
            "lock for this cache; exiting cleanly.\n"
        )
        log.flush()
        now = int(time.time())
        return PrefetchState(
            manifest_version="",
            pack_name=pack_name,
            started_at=now,
            completed_at=now,
            total=0,
        )

    try:
        m = manifest if manifest is not None else load_manifest(pack_name)
        if m is None:
            raise ArtUpdateError(
                f"prefetch: no manifest installed for pack {pack_name!r}; "
                "run `daimon update` first"
            )

        skip_set = set(skip_card_ids)
        todo = [
            cid for cid in sorted(m.cards.keys())
            if cid not in skip_set and not is_card_cached(cid, pack_name=pack_name)
        ]
        skipped_count = len(m.cards) - len(todo)

        state = PrefetchState(
            manifest_version=m.pack_version,
            pack_name=pack_name,
            started_at=int(time.time()),
            total=len(m.cards),
            skipped_count=skipped_count,
        )
        write_state(state)

        if not todo:
            log.write(
                f"prefetch: nothing to do — all {len(m.cards)} cards "
                f"already cached for {m.pack_version}.\n"
            )
            log.flush()
            state.completed_at = int(time.time())
            write_state(state)
            return state

        log.write(
            f"prefetch: starting — {len(todo)} of {len(m.cards)} cards "
            f"to fetch for {m.pack_version}, {workers} workers.\n"
        )
        log.flush()

        # ``cleanup_card_staging`` mops up any per-card scratch dirs left
        # behind by a previous crashed run. Cheap, runs once.
        cleanup_card_staging()

        def _fetch_one(card_id: str) -> tuple[str, Optional[str]]:
            try:
                fetch_card(card_id, manifest=m, pack_name=pack_name,
                           show_progress=False)
                return card_id, None
            except ArtUpdateError as e:
                return card_id, str(e)
            except Exception as e:  # noqa: BLE001 — we record + continue
                return card_id, f"{type(e).__name__}: {e}"

        with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_fetch_one, cid): cid for cid in todo}
            for fut in cf.as_completed(futures):
                cid, err = fut.result()
                if err is None:
                    state.fetched_count += 1
                    log.write(f"  ok   {cid}\n")
                else:
                    state.failed.append([cid, err])
                    log.write(f"  FAIL {cid}: {err}\n")
                log.flush()

        state.completed_at = int(time.time())
        write_state(state)

        duration = state.completed_at - state.started_at
        log.write(
            f"prefetch: done — {state.fetched_count} fetched, "
            f"{state.skipped_count} skipped, {state.failed_count} failed "
            f"in {duration}s.\n"
        )
        log.flush()
        return state
    finally:
        release_prefetch_lock(lock_fd)


# ---------------------------------------------------------------------------
# Subprocess entry / spawn
# ---------------------------------------------------------------------------

def _open_log() -> tuple[Path, "Optional[int]"]:
    """Open prefetch.log for append. Caller closes the fd."""
    p = cache_dir() / PREFETCH_LOG_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    return p, fd


def spawn_prefetch_subprocess(
    *,
    workers: int = DEFAULT_WORKERS,
    extra_env: Optional[dict] = None,
) -> Optional[int]:
    """Fire-and-forget ``python -m daimon.update.prefetch``.

    Returns the child PID or ``None`` if the spawn failed silently —
    same contract as :func:`daimon.update.checker.spawn_background_check`.
    Onboard / CLI invocations call this and return immediately; the
    user's terminal is unblocked while cards quietly land on disk.

    Honors ``DAIMON_NO_AUTO_UPDATE=1``: returns ``None`` without
    spawning when opted out.

    Singleton optimization: if a prefetcher lockfile is present and its
    owner PID is alive, returns ``None`` without forking. The child
    would have done the same check via :func:`acquire_prefetch_lock`
    and exited cleanly, but skipping the fork saves a measurable
    amount of work on systems where ``daimon onboard`` is invoked
    twice in quick succession.
    """
    if not auto_update_enabled():
        return None

    if _existing_prefetch_alive():
        return None

    try:
        log_path, log_fd = _open_log()
    except OSError:
        return None

    try:
        devnull = os.open(os.devnull, os.O_RDONLY)
    except OSError:
        os.close(log_fd)
        return None

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    try:
        proc = subprocess.Popen(
            [windowless_python(), "-m", "daimon.update.prefetch",
             "--workers", str(workers)],
            stdin=devnull,
            stdout=log_fd,
            stderr=log_fd,
            close_fds=True,
            start_new_session=True,
            creationflags=windowless_creationflags(),
            env=env,
        )
        return proc.pid
    except (OSError, ValueError):
        return None
    finally:
        os.close(devnull)
        os.close(log_fd)


def _cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m daimon.update.prefetch",
        description="Background prefetcher — populate per-card art ahead of demand.",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Parallel worker count (default: {DEFAULT_WORKERS}).")
    parser.add_argument("--pack", default=ART_PACK_NAME,
                        help=f"Pack to prefetch (default: {ART_PACK_NAME}).")
    args = parser.parse_args(argv)

    if not auto_update_enabled():
        sys.stderr.write("daimon-prefetch: opted out via DAIMON_NO_AUTO_UPDATE.\n")
        return 0

    try:
        run_prefetch(workers=args.workers, pack_name=args.pack)
        return 0
    except ArtUpdateError as e:
        sys.stderr.write(f"daimon-prefetch: ERROR: {e}\n")
        return 1
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"daimon-prefetch: CRASH: {e}\n")
        traceback.print_exc(file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_cli_main())


__all__ = [
    "DEFAULT_WORKERS",
    "PrefetchState",
    "read_state",
    "write_state",
    "run_prefetch",
    "spawn_prefetch_subprocess",
]
