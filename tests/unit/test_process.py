import asyncio
import os
import time

from saarathi.runtime.process import process_alive, run_process
from saarathi.security.env import filtered_env


def test_timeout_kills_process_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = f"sleep 60 & echo $! > {pidfile}; wait"
    res = asyncio.run(run_process(["sh", "-c", script], cwd=tmp_path, env=filtered_env(), timeout_s=1))
    assert res.timed_out and not res.ok
    child = int(pidfile.read_text())
    deadline = time.time() + 5
    while process_alive(child) and time.time() < deadline:
        time.sleep(0.1)
    assert not process_alive(child), "grandchild survived the timeout"


def test_cancellation_terminates_child(tmp_path):
    started = []

    async def main():
        task = asyncio.create_task(
            run_process(["sleep", "60"], cwd=tmp_path, env=filtered_env(), timeout_s=100, on_start=started.append)
        )
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(main())
    time.sleep(0.2)
    assert started and not process_alive(started[0])


def test_output_is_capped(tmp_path):
    res = asyncio.run(
        run_process(
            ["sh", "-c", "yes | head -c 500000"],
            cwd=tmp_path,
            env=filtered_env(),
            timeout_s=10,
            max_output_bytes=1000,
        )
    )
    assert res.ok and len(res.stdout) == 1000 and res.stdout_truncated


def test_env_allowlist_drops_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x" * 20)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy")
    env = filtered_env(["CODEX_HOME"])
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_BASE_URL" not in env and env["PATH"] == os.environ["PATH"]
    assert "ANTHROPIC_API_KEY" in filtered_env(["ANTHROPIC_API_KEY"])
