"""Process runner: whole-tree termination, cancellation, capped output, environment allowlist. Portable."""

import asyncio
import os
import sys
import time

from saarathi.runtime.process import process_alive, run_process
from saarathi.security.env import filtered_env

PY = sys.executable
SLEEPER = "import time; time.sleep(60)"


def wait_dead(pid, seconds=10):
    deadline = time.time() + seconds
    while process_alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    return not process_alive(pid)


def test_timeout_kills_the_whole_tree(tmp_path):
    pidfile = tmp_path / "child.pid"
    parent = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {SLEEPER!r}])\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    res = asyncio.run(run_process([PY, "-c", parent], cwd=tmp_path, env=filtered_env(), timeout_s=3))
    assert res.timed_out and not res.ok
    child = int(pidfile.read_text())
    assert wait_dead(child), "grandchild survived the timeout"


def test_cancellation_terminates_child(tmp_path):
    started = []

    async def main():
        task = asyncio.create_task(
            run_process([PY, "-c", SLEEPER], cwd=tmp_path, env=filtered_env(), timeout_s=100, on_start=started.append)
        )
        await asyncio.sleep(1.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(main())
    assert started and wait_dead(started[0])


def test_output_is_capped(tmp_path):
    script = "import sys; sys.stdout.write('y' * 500000)"
    res = asyncio.run(
        run_process([PY, "-c", script], cwd=tmp_path, env=filtered_env(), timeout_s=30, max_output_bytes=1000)
    )
    assert res.ok and len(res.stdout) == 1000 and res.stdout_truncated


def test_process_alive_never_harms_a_live_process(tmp_path):
    async def main():
        proc = await asyncio.create_subprocess_exec(PY, "-c", SLEEPER)
        try:
            assert process_alive(proc.pid)
            assert process_alive(proc.pid), "checking twice must not have terminated it (os.kill(pid, 0) on Windows)"
            assert proc.returncode is None
        finally:
            proc.kill()
            await proc.wait()
        return proc.pid

    pid = asyncio.run(main())
    assert wait_dead(pid)


def test_env_allowlist_drops_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x" * 20)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy")
    env = filtered_env(["CODEX_HOME"])
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_BASE_URL" not in env
    assert env["PATH"] == os.environ["PATH"]
    assert "ANTHROPIC_API_KEY" in filtered_env(["ANTHROPIC_API_KEY"])
    if sys.platform == "win32":
        assert "SYSTEMROOT" in {k.upper() for k in env}, "Windows programs need SYSTEMROOT to start"
