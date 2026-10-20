"""Detached run drivers (ADR-0013).

The bridge never drives a run in its own process. It starts ``saarathi resume --run R --non-interactive`` as a
detached process, so execution survives window close and bridge crashes. The engine's OS lock guarantees at most one
driver per project: a duplicate start simply fails to take the lock and exits.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from saarathi.orchestration.project import Project
from saarathi.runtime.binaries import engine_command
from saarathi.runtime.process import process_alive

DRIVER_EXIT_WAIT_SECONDS = 10.0
# Run control a driver polls for and treats as Ctrl-C (see ``saarathi.cli.main._watch_for_interrupt``).
INTERRUPT_CONTROL = "interrupt_requested"
# Drivers this process started. Polling them reaps finished ones, which would otherwise linger as zombies
# that look alive to a liveness check on POSIX.
_children: dict[int, tuple[subprocess.Popen[bytes], str]] = {}  # pid -> (process, project root)


def _reap() -> None:
    for pid, (child, _root) in list(_children.items()):
        if child.poll() is not None:
            del _children[pid]


def driver_pid(project: Project) -> int | None:
    """Pid of the driver for this project, if one is running.

    A driver this bridge just started counts at once, before it has taken the engine lock; otherwise the pid
    recorded in the lock file is used (a driver started by an earlier bridge or from the CLI).
    """
    _reap()
    root = str(project.root)
    for pid, (_child, child_root) in _children.items():
        if child_root == root:
            return pid
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
    if pid in _children:
        return pid
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
    argv = [*engine_command(), "resume", "--run", run_id, "--non-interactive"]
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
    _children[proc.pid] = (proc, str(project.root))
    return proc.pid


def interrupt_driver(project: Project, run_id: str, pid: int) -> None:
    """Ask a driver to stop the way Ctrl-C would: in-flight attempts are cancelled and the run pauses.

    The request is stored as a run control, which every driver polls. Windows cannot send a console control event
    to a detached process, so that is the only channel there; on POSIX a SIGINT also delivers it at once.
    """
    project.store.set_control(run_id, INTERRUPT_CONTROL, "1", actor="user")
    if sys.platform != "win32":
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGINT)
