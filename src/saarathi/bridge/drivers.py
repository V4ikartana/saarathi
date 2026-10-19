"""Detached run drivers (ADR-0013).

The bridge never drives a run in its own process. It starts ``saarathi resume --run R --non-interactive`` as a
detached process, so execution survives window close and bridge crashes. The engine's OS lock guarantees at most one
driver per project: a duplicate start simply fails to take the lock and exits.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from saarathi.orchestration.project import Project
from saarathi.runtime.process import process_alive

DRIVER_EXIT_WAIT_SECONDS = 10.0
# Drivers this process started. Polling them reaps finished ones, which would otherwise linger as zombies
# that look alive to a liveness check on POSIX.
_children: dict[int, subprocess.Popen[bytes]] = {}


def _reap() -> None:
    for pid, child in list(_children.items()):
        if child.poll() is not None:
            del _children[pid]


def driver_pid(project: Project) -> int | None:
    """Pid recorded by the engine that last held the project lock, if it is still running."""
    lock = project.meta / "engine.lock"
    try:
        raw = lock.read_bytes()
    except OSError:
        return None
    text = raw.replace(b"\x00", b" ").decode("ascii", errors="ignore").strip()
    digits = "".join(ch for ch in text.split()[0] if ch.isdigit()) if text else ""
    if not digits:
        return None
    pid = int(digits)
    _reap()
    child = _children.get(pid)
    if child is not None:
        return pid if child.poll() is None else None
    if pid == os.getpid() or not process_alive(pid):
        return None
    return pid


def driver_log(project: Project, run_id: str) -> Path:
    return project.logs / run_id / "driver.log"


def wait_for_exit(project: Project, timeout: float = DRIVER_EXIT_WAIT_SECONDS) -> int | None:
    """Give a driver that has just reached a waiting state time to exit. Returns the pid if it is still running."""
    deadline = time.monotonic() + timeout
    pid = driver_pid(project)
    while pid is not None and time.monotonic() < deadline:
        time.sleep(0.1)
        pid = driver_pid(project)
    return pid


def start_driver(project: Project, run_id: str, *, accept_defaults: bool = False) -> int:
    """Start a detached driver for ``run_id`` unless one is still driving this project. Returns the pid.

    Callers only start drivers when the run is waiting or paused, so a driver that is still alive is normally
    finishing its last step; it gets a moment to exit before a new one starts.
    """
    existing = wait_for_exit(project)
    if existing is not None:
        return existing
    argv = [sys.executable, "-m", "saarathi", "resume", "--run", run_id, "--non-interactive"]
    if accept_defaults:
        argv.append("--accept-defaults")
    log = driver_log(project, run_id)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            proc = subprocess.Popen(
                argv,
                cwd=project.root,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                close_fds=True,
            )
        else:
            proc = subprocess.Popen(
                argv,
                cwd=project.root,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    _children[proc.pid] = proc
    return proc.pid


def interrupt_driver(pid: int) -> bool:
    """Ask a driver to stop the way Ctrl-C would: in-flight attempts are cancelled and the run pauses."""
    try:
        if sys.platform == "win32":
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        else:
            os.kill(pid, signal.SIGINT)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False
