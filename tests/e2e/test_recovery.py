"""Scenario B: kill the orchestrator mid-run, restart, and prove recovery without duplicate execution."""

import os
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from tests.e2e.helpers import GOOD_T1, GOOD_T2, WIDGET, init_project, make_script, records

SLOW_WIDGET_TEST = (
    "import time\nimport unittest\nfrom ui.widget import render\n\n\nclass T(unittest.TestCase):\n"
    "    def test_render(self):\n        time.sleep(8)\n        self.assertIn('ok', render('ok'))\n"
)


def cli(repo, env, *args, check=False):
    return subprocess.run(
        [sys.executable, "-m", "saarathi", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def task_states(repo):
    conn = sqlite3.connect(repo / ".saarathi" / "state.db")
    try:
        return dict(conn.execute("SELECT id, state FROM tasks").fetchall())
    finally:
        conn.close()


def kill_when(repo, env, predicate, timeout=60):
    proc = subprocess.Popen(
        [sys.executable, "-m", "saarathi", "approve", "--non-interactive"],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"engine exited early: {proc.stderr.read().decode()}")
            try:
                if predicate(task_states(repo)):
                    break
            except sqlite3.OperationalError:
                pass
            time.sleep(0.2)
        else:
            pytest.fail("condition never reached")
        os.kill(proc.pid, signal.SIGKILL)  # simulate a crash: no cleanup code runs
        proc.wait(10)
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.fixture
def env(tmp_path):
    e = dict(os.environ, SAARATHI_ALLOW_FAKE_PROVIDERS="1", SAARATHI_DATA_DIR=str(tmp_path / "data"))
    return e


def start(repo, env, tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("Add a health endpoint and a status widget.")
    out = cli(repo, env, "run", str(brief), "--non-interactive")
    assert out.returncode == 3, out.stderr  # awaiting plan approval


def test_kill_during_worker_execution_recovers(repo, tmp_path, env):
    slow_t2 = {"sleep": 60, **GOOD_T2}
    script = make_script(tmp_path / "s.json", {"T-001": [GOOD_T1], "T-002": [slow_t2, GOOD_T2]})
    init_project(repo, script, tmp_path / "rec.jsonl")
    start(repo, env, tmp_path)
    kill_when(repo, env, lambda s: s.get("T-001") == "SUCCEEDED" and s.get("T-002") == "RUNNING")

    out = cli(repo, env, "resume", "--non-interactive", "--json")
    assert out.returncode == 3, out.stderr  # awaiting final review
    assert '"state": "AWAITING_REVIEW"' in out.stdout
    recs = records(tmp_path / "rec.jsonl")
    assert [r["key"] for r in recs].count("T-001") == 1, "completed work must not be executed again"
    assert [r["key"] for r in recs].count("T-002") == 2, "orphaned attempt is retried once"
    log = cli(repo, env, "logs", "--task", "T-002").stdout
    assert "attempt.abandoned" in log and "orphaned attempt discarded" in log


def test_kill_during_verification_reverifies_without_reexecution(repo, tmp_path, env):
    slow_verify = {"write": {"ui/__init__.py": "", "ui/widget.py": WIDGET, "tests/test_widget.py": SLOW_WIDGET_TEST}}
    script = make_script(tmp_path / "s.json", {"T-001": [GOOD_T1], "T-002": [slow_verify]})
    init_project(repo, script, tmp_path / "rec.jsonl")
    # make the integration check skip the slow test so the run finishes quickly after recovery
    start(repo, env, tmp_path)
    kill_when(repo, env, lambda s: s.get("T-001") == "SUCCEEDED" and s.get("T-002") == "VERIFYING")

    out = cli(repo, env, "status", "--json")
    assert '"T-002"' in out.stdout
    resumed = subprocess.Popen(
        [sys.executable, "-m", "saarathi", "resume", "--non-interactive"],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        resumed.wait(180)
    finally:
        if resumed.poll() is None:
            resumed.kill()
    assert [r["key"] for r in records(tmp_path / "rec.jsonl")].count("T-002") == 1, "worker must not re-run"
    log = cli(repo, env, "logs", "--task", "T-002").stdout
    assert "recovery.reverify" in log
    assert task_states(repo)["T-002"] == "SUCCEEDED"


def test_second_engine_is_locked_out(repo, tmp_path, env):
    slow_t2 = {"sleep": 60, **GOOD_T2}
    script = make_script(tmp_path / "s.json", {"T-001": [GOOD_T1], "T-002": [slow_t2]})
    init_project(repo, script, tmp_path / "rec.jsonl")
    start(repo, env, tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "saarathi", "approve", "--non-interactive"],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline and task_states(repo).get("T-002") != "RUNNING":
            time.sleep(0.2)
        second = cli(repo, env, "resume", "--non-interactive")
        assert second.returncode == 6 and "another Saarathi engine" in second.stderr
    finally:
        proc.kill()
        proc.wait(10)
