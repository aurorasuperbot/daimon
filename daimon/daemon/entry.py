"""Daemon process entry point — picks a port, starts FastAPI in a daemon
thread, writes the singleton lock, opens the pywebview window, and
tears everything down on window close.

Per refactor.md §7.2.

Wired to the CLI via the hidden ``daimon _daemon_internal`` command;
end-users hit it transitively by running ``daimon menu``, which
double-forks here via :mod:`daimon.daemon.spawn`.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from daimon.bootstrap import ensure_bootstrapped
from daimon.daemon.lock import alive_lock, remove_lock, write_lock
from daimon.web.server import create_app


logger = logging.getLogger(__name__)


def _try_import_webview():
    """Import pywebview lazily so headless callers (Linux servers without
    a webkit2gtk runtime) can still spawn the daemon and fall back to a
    plain browser tab. Returns the module on success, ``None`` if either
    pywebview is missing OR its native runtime is unavailable.
    """
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError as e:
        logger.warning("pywebview unavailable: %s", e)
        return None
    # On Linux, importing succeeds but window creation later fails when
    # webkit2gtk is missing. We can't reliably probe the GTK side from
    # Python without trying — defer the failure to create_window().
    return webview

WINDOW_TITLE = "DAIMON"
WINDOW_WIDTH = 1440
WINDOW_HEIGHT = 900
WINDOW_MIN_WIDTH = 1100
WINDOW_MIN_HEIGHT = 720


def _pick_free_port() -> int:
    """Ask the OS for a free TCP port on 127.0.0.1 and return it.

    The port is released the instant we close the probe socket; uvicorn
    will rebind it microseconds later. There's a tiny TOCTOU window
    where another process could grab it, but on a single-user desktop
    this is acceptable — re-running ``daimon menu`` recovers cleanly.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _DaemonServer:
    """Thin wrapper around uvicorn.Server so we can stop it from the
    pywebview thread when the window closes."""

    def __init__(self, port: int) -> None:
        config = uvicorn.Config(
            app=create_app(),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, name="daimon-fastapi", daemon=True,
        )

    def start(self) -> None:
        self.thread.start()
        self._wait_ready()

    def _wait_ready(self, *, timeout_s: float = 5.0) -> None:
        """Block until uvicorn flips ``server.started`` or we time out."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.server.started:
                return
            time.sleep(0.02)
        raise RuntimeError("uvicorn failed to start within 5s")

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=timeout_s)


def run() -> int:
    """Run the daemon. Blocks until the window is closed."""
    ensure_bootstrapped()

    if alive_lock() is not None:
        # Another daemon already owns the singleton role. Bail silently
        # — the spawning ``daimon menu`` will surface the URL of the
        # existing window to the user.
        return 0

    # CLI group callback in daimon/cli.py skips ensure_art_available for
    # `menu` and `_daemon_internal` (both in ART_PURE_COMMANDS) — so the
    # daemon owns the manifest fetch instead. Without this, a fresh
    # install would serve /art/{card_id} against an empty manifest and
    # every card would 404 to a placeholder. Failure is non-fatal: the
    # lazy art pipeline soft-fails to placeholders and the rest of the
    # daemon (matches, mining, pulls metadata) still works.
    try:
        from daimon.update import ensure_art_available
        ensure_art_available()
    except Exception:  # noqa: BLE001 — never fatal at daemon boot
        logger.exception("art manifest fetch failed at daemon boot (non-fatal)")

    port = _pick_free_port()
    server = _DaemonServer(port)
    server.start()

    info = write_lock(pid=os.getpid(), port=port)
    logger.info("daemon up at http://127.0.0.1:%d (pid %d)", info.port, info.pid)

    url = f"http://127.0.0.1:{port}/"
    webview = _try_import_webview()
    try:
        if webview is None:
            _run_browser_fallback(url, server)
        else:
            try:
                webview.create_window(
                    WINDOW_TITLE,
                    url,
                    width=WINDOW_WIDTH,
                    height=WINDOW_HEIGHT,
                    min_size=(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT),
                    background_color="#0a0c18",
                )
                webview.start()  # blocks until the last window closes
            except Exception as e:  # noqa: BLE001 — pywebview wraps platform errors
                logger.warning(
                    "pywebview window failed (%s); falling back to browser", e,
                )
                _run_browser_fallback(url, server)
    finally:
        server.stop()
        remove_lock()

    return 0


def _run_browser_fallback(url: str, server: "_DaemonServer") -> None:
    """No webview engine available — open the URL in the default browser
    and block until the user kills the daemon. Documented in refactor.md
    R1 as the Linux-without-webkit2gtk escape hatch.
    """
    print(f"daimon: native window unavailable, opening {url} in your browser",
          file=sys.stderr)
    print("daimon: press Ctrl-C to stop the daemon", file=sys.stderr)
    try:
        webbrowser.open(url, new=1)
    except Exception as e:  # noqa: BLE001
        print(f"daimon: failed to launch browser: {e}", file=sys.stderr)
        print(f"daimon: open {url} manually to play", file=sys.stderr)
    try:
        # Wait until the FastAPI server thread dies (Ctrl-C → SIGINT →
        # uvicorn graceful shutdown → thread exits).
        while server.thread.is_alive():
            server.thread.join(timeout=1.0)
    except KeyboardInterrupt:
        pass
