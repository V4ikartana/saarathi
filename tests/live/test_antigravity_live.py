"""Live contract test against the real Antigravity CLI. Opt in: SAARATHI_LIVE_TESTS=1 (uses a little quota).

Set SAARATHI_AGY_BIN if `agy` is not on PATH and SAARATHI_AGY_MODEL to pin a model (default: account default).
"""

import asyncio
import os
import shutil
import subprocess

import pytest

from saarathi.core.contracts import EscalationVerdict
from saarathi.core.policy import ProviderConfig
from saarathi.orchestration import prompts
from saarathi.orchestration.prompts import WORKER_SCHEMA
from saarathi.providers.antigravity_cli import AntigravityCliAdapter
from saarathi.providers.base import Invocation, InvocationMode

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("SAARATHI_LIVE_TESTS") != "1", reason="live tests are opt-in"),
]
MODEL = os.environ.get("SAARATHI_AGY_MODEL", "default")


@pytest.fixture
def adapter():
    binary = os.environ.get("SAARATHI_AGY_BIN") or shutil.which("agy")
    if not binary:
        pytest.skip("agy not installed")
    a = AntigravityCliAdapter("antigravity-cli", ProviderConfig(binary=binary))
    if not a.probe().available:
        pytest.skip(f"agy unavailable: {a.probe().reason}")
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
        model=MODEL,
        timeout_s=300,
        log_dir=tmp_path / "log",
    )
    result = asyncio.run(adapter.invoke(inv))
    assert result.ok, result.error
    assert result.structured["status"] == "succeeded"
    assert "GREETING" in (wt / "src" / "hello.py").read_text()
    assert not (repo / "src" / "hello.py").exists()


def test_read_only_mode_cannot_write(adapter, repo, tmp_path):
    inv = Invocation(
        purpose="lead.intake",
        mode=InvocationMode.READ_ONLY,
        prompt="Try to create a file named should_not_exist.txt, then report what happened.",
        schema=WORKER_SCHEMA,
        cwd=repo,
        model=MODEL,
        timeout_s=300,
        log_dir=tmp_path / "log",
    )
    asyncio.run(adapter.invoke(inv))
    assert not (repo / "should_not_exist.txt").exists()


def test_lead_schemas_accepted(adapter, repo, tmp_path):
    from tests.live.test_escalation_live import CHECKPOINT

    inv = Invocation(
        purpose="lead.escalation",
        mode=InvocationMode.READ_ONLY,
        prompt=prompts.escalation_prompt(CHECKPOINT),
        schema=prompts.ESCALATION_SCHEMA,
        cwd=repo,
        model=MODEL,
        timeout_s=300,
        log_dir=tmp_path / "log",
    )
    result = asyncio.run(adapter.invoke(inv))
    assert result.ok, result.error
    EscalationVerdict.model_validate(result.structured)
