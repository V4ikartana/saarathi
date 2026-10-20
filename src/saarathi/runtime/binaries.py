"""Finding provider CLIs at paths that stay valid, and starting the engine itself.

Version managers put CLIs in per-shell directories: fnm creates ``fnm_multishells/<pid>_<time>/bin`` for every shell
and removes it later. A path found there works today and breaks after a reboot, so it is resolved to the real
installation directory. Node-based CLIs (Codex) also need ``node``, which lives beside them, so a CLI's own directory
is added to the PATH it runs with.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

VOLATILE_DIRS = ("fnm_multishells",)

# On Windows, a console program started from the desktop app (which has no console) would open a console window of
# its own. Every short-lived child gets this flag; 0 elsewhere.
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def stable_path(path: str) -> str:
    """The same executable at a path that outlives the shell that found it."""
    p = Path(path)
    if any(part in VOLATILE_DIRS for part in p.parts):
        real = p.parent.resolve() / p.name
        if real.exists():
            return str(real)
    return str(p)


def find_cli(name: str, configured: str | None = None) -> str | None:
    """A configured binary wins; otherwise the CLI on PATH, at a stable location."""
    if configured:
        return configured
    found = shutil.which(name)
    return stable_path(found) if found else None


def cli_env(argv: Sequence[str], env: Mapping[str, str]) -> dict[str, str]:
    """``env`` with the CLI's own directory first on PATH, so a Node-based CLI finds its ``node``."""
    out = dict(env)
    if not argv:
        return out
    exe = Path(argv[0])
    if not exe.is_absolute():
        return out
    key = next((k for k in out if k.upper() == "PATH"), "PATH")
    parts = [p for p in out.get(key, "").split(os.pathsep) if p]
    directory = str(exe.parent)
    if directory not in parts:
        out[key] = os.pathsep.join([directory, *parts])
    return out


def engine_command() -> list[str]:
    """How to start this engine again (for detached run drivers): the frozen binary itself, or ``python -m``."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "saarathi"]
