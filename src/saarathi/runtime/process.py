"""Bounded subprocess execution: own process tree, timeout, cancellation, capped output.

POSIX: each child starts a new session, so its process group (pgid == pid) is signalled as a unit.
Windows: each child starts a new process group, and its tree is ended with ``taskkill /T``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

TERM_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


async def _read_capped(stream: asyncio.StreamReader | None, limit: int) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    chunks: list[bytes] = []
    size = 0
    truncated = False
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        if size < limit:
            keep = chunk[: limit - size]
            chunks.append(keep)
            size += len(keep)
            truncated = truncated or len(keep) < len(chunk)
        else:
            truncated = True  # keep draining so the child never blocks on a full pipe
    return b"".join(chunks), truncated


def terminate_tree(pid: int, *, force: bool = False) -> bool:
    """Ask a child and everything it started to stop (``force``: kill outright). True if a signal was sent."""
    if sys.platform == "win32":
        argv = ["taskkill", "/PID", str(pid), "/T", *(["/F"] if force else [])]
        done = subprocess.run(argv, capture_output=True, check=False, stdin=subprocess.DEVNULL)
        return done.returncode == 0
    try:
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _spawn_options() -> dict[str, object]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    terminate_tree(proc.pid)
    try:
        await asyncio.wait_for(proc.wait(), TERM_GRACE_SECONDS)
    except TimeoutError:
        terminate_tree(proc.pid, force=True)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), TERM_GRACE_SECONDS)


async def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    stdin_data: bytes | None = None,
    max_output_bytes: int = 2_000_000,
    on_start: Callable[[int], None] | None = None,
) -> ProcessResult:
    """Run ``argv`` without a shell as the root of its own process tree.

    On timeout or task cancellation the whole tree is terminated, then killed.
    Cancellation is re-raised after cleanup.
    """
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=dict(env),
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **_spawn_options(),  # type: ignore[arg-type]
    )
    if on_start is not None:
        on_start(proc.pid)

    async def feed() -> None:
        if stdin_data is not None and proc.stdin is not None:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.write(stdin_data)
                await proc.stdin.drain()
            proc.stdin.close()

    async def communicate() -> tuple[tuple[bytes, bool], tuple[bytes, bool]]:
        _, out, err = await asyncio.gather(
            feed(),
            _read_capped(proc.stdout, max_output_bytes),
            _read_capped(proc.stderr, max_output_bytes),
        )
        await proc.wait()
        return out, err

    timed_out = False
    io_task = asyncio.ensure_future(communicate())
    try:
        (out, out_trunc), (err, err_trunc) = await asyncio.wait_for(asyncio.shield(io_task), timeout_s)
    except TimeoutError:
        timed_out = True
        await _terminate(proc)
        try:
            (out, out_trunc), (err, err_trunc) = await asyncio.wait_for(io_task, TERM_GRACE_SECONDS)
        except TimeoutError:
            io_task.cancel()
            out, out_trunc, err, err_trunc = b"", False, b"", False
    except asyncio.CancelledError:
        await _terminate(proc)
        io_task.cancel()
        raise
    return ProcessResult(
        argv=tuple(argv),
        exit_code=proc.returncode,
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
        duration_s=round(time.monotonic() - started, 3),
        timed_out=timed_out,
        stdout_truncated=out_trunc,
        stderr_truncated=err_trunc,
    )


def write_log(path: Path, result: ProcessResult, redactor: Callable[[str], str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"$ {' '.join(result.argv[:1])} ... ({len(result.argv) - 1} args)\n"
        f"exit_code={result.exit_code} timed_out={result.timed_out} duration_s={result.duration_s}\n"
        f"--- stdout{' (truncated)' if result.stdout_truncated else ''} ---\n{result.stdout}\n"
        f"--- stderr{' (truncated)' if result.stderr_truncated else ''} ---\n{result.stderr}\n"
    )
    path.write_text(redactor(body), encoding="utf-8")


def process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        # Never os.kill(pid, 0) here: on Windows that terminates the process.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: exists, not ours to inspect
        try:
            code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
