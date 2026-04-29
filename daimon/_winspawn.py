"""Windowless-spawn helpers for Windows console-subsystem avoidance.

Detached children spawned via :func:`subprocess.Popen` without
``CREATE_NO_WINDOW`` will allocate a new console on Windows whenever
the child binary is console-subsystem (``python.exe``, ``gh.exe``, …).
That is why ``daimon menu`` and friends were popping ghost terminals
during screen navigation: every fire-and-forget Popen flashed one.

Two primitives:

  * :func:`windowless_python` — return ``pythonw.exe`` (GUI-subsystem
    Python launcher; no console at all) on Windows when present, else
    ``sys.executable``. Use as ``argv[0]`` when the child is a Python
    process invoked via ``-m``.

  * :func:`windowless_creationflags` — return
    ``CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`` on Windows, ``0``
    elsewhere. Pass to ``Popen(creationflags=...)``. The constant is
    safe to OR with whatever flags a caller already wants.

Use both for Python children; just the flags for non-Python children
(e.g. ``gh.exe``). The two are belt-and-suspenders: pythonw alone
removes the console subsystem from the binary itself, the flag tells
the loader not to allocate one even if the binary still asks for it.
"""

from __future__ import annotations

import sys
from pathlib import Path

# CREATE_NO_WINDOW=0x08000000, CREATE_NEW_PROCESS_GROUP=0x00000200.
_WIN_DETACH_FLAGS = 0x08000000 | 0x00000200


def windowless_python() -> str:
    """Return pythonw.exe on Windows when available; sys.executable elsewhere."""
    if sys.platform != "win32":
        return sys.executable
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else sys.executable


def windowless_creationflags() -> int:
    """Return Windows detach flags, or 0 on POSIX (silently accepted by Popen)."""
    return _WIN_DETACH_FLAGS if sys.platform == "win32" else 0
