"""Live contract test against the real Codex CLI. Opt in: SAARATHI_LIVE_TESTS=1 (uses a little quota).

Set SAARATHI_CODEX_BIN if `codex` is not on PATH.
"""

import asyncio
import os
import shutil
import subprocess

import pytest

from saarathi.core.policy import ProviderConfig
from saarathi.orchestration.prompts import WORKER_SCHEMA
from saarathi.providers.base import Invocation, InvocationMode
from saarathi.providers.codex_cli import CodexCliAdapter
from tests.live.integration_plan import run_lead_plan

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("SAARATHI_LIVE_TESTS") != "1", reason="live tests are opt-in"),
]


@pytest.fixture
def adapter():
    binary = os.environ.get("SAARATHI_CODEX_BIN") or shutil.which("codex")
    if not binary:
        pytest.skip("codex not installed")
    a = CodexCliAdapter("codex-cli", ProviderConfig(binary=binary))
    probe = a.probe()
    if not probe.available:
        pytest.skip(f"codex unavailable: {probe.reason}")
    return a


def test_workspace_write_stays_in_worktree(adapter, repo, tmp_path):
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "t", str(wt)], cwd=repo, check=True)
    inv = Invocation(
        purpose="task.execute",
        task_id="T-001",
        mode=InvocationMode.WORKSPACE_WRITE,
        prompt="Create src/hello.py containing exactly: GREETING = 'hi'\nDo not modify other files. Then report.",
        schema=WORKER_SCHEMA,
        cwd=wt,
        model="default",
        timeout_s=300,
        log_dir=tmp_path / "log",
    )
    result = asyncio.run(adapter.invoke(inv))
    assert result.ok, result.error
    assert result.structured["status"] == "succeeded"
    assert "GREETING" in (wt / "src" / "hello.py").read_text()
    assert not (repo / "src" / "hello.py").exists()
    assert result.usage.input_tokens and result.usage.reported_model is None


def test_read_only_mode_cannot_write(adapter, repo, tmp_path):
    inv = Invocation(
        purpose="lead.intake",
        mode=InvocationMode.READ_ONLY,
        prompt="Try to create a file named should_not_exist.txt, then report what happened.",
        schema=WORKER_SCHEMA,
        cwd=repo,
        model="default",
        timeout_s=300,
        log_dir=tmp_path / "log",
    )
    asyncio.run(adapter.invoke(inv))
    assert not (repo / "should_not_exist.txt").exists()


def test_lead_plan_with_integration_requirement_validates(adapter, repo, tmp_path):
    plan = run_lead_plan(adapter, repo, tmp_path, "default")
    print(plan.model_dump_json(indent=2, include={"tasks"}))
