"""Live: both CLIs accept the lead's escalation schema in strict mode and return a valid verdict.

Opt in: SAARATHI_LIVE_TESTS=1 (uses a little quota). SAARATHI_CODEX_BIN if codex is not on PATH.
"""

import asyncio
import os
import shutil

import pytest

from saarathi.core.contracts import EscalationVerdict
from saarathi.core.policy import ProviderConfig
from saarathi.orchestration import prompts
from saarathi.providers.base import Invocation, InvocationMode
from saarathi.providers.claude_cli import ClaudeCodeAdapter
from saarathi.providers.codex_cli import CodexCliAdapter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("SAARATHI_LIVE_TESTS") != "1", reason="live tests are opt-in"),
]

CHECKPOINT = """# Escalation esc-1: contract_change
Task T-001 "Add GET /health" was stopped by its worker.
## Worker's claim
Monitoring requires JSON from /health, but ADR-0001 fixes a plain-text body 'ok'.
## Evidence reported by the worker
tests/test_health.py expects {'status': 'ok'}; ADR-0001 says plain text.
## Change the worker proposes
Return JSON {"status": "ok"}.
## Accepted decisions
- ADR-0001 [Accepted] Health returns plain text: GET /health returns the plain-text body 'ok'.
## Tasks waiting on this one
- T-002 Status widget (BLOCKED)
"""

ADAPTERS = {
    "claude": (ClaudeCodeAdapter, "SAARATHI_CLAUDE_BIN", "claude", "sonnet"),
    "codex": (CodexCliAdapter, "SAARATHI_CODEX_BIN", "codex", "default"),
}


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_lead_escalation_verdict_validates(name, repo, tmp_path):
    cls, env, exe, model = ADAPTERS[name]
    binary = os.environ.get(env) or shutil.which(exe)
    if not binary:
        pytest.skip(f"{exe} not installed")
    adapter = cls(f"{name}-live", ProviderConfig(binary=binary))
    if not adapter.probe().available:
        pytest.skip(f"{exe} unavailable: {adapter.probe().reason}")
    inv = Invocation(
        purpose="lead.escalation",
        mode=InvocationMode.READ_ONLY,
        prompt=prompts.escalation_prompt(CHECKPOINT),
        schema=prompts.ESCALATION_SCHEMA,
        cwd=repo,
        model=model,
        timeout_s=300,
        log_dir=tmp_path / "log",
    )
    result = asyncio.run(adapter.invoke(inv))
    assert result.ok, result.error
    verdict = EscalationVerdict.model_validate(result.structured)
    print(name, verdict.model_dump_json(indent=2))
    if verdict.decision == "supersede_adr":
        assert verdict.adr.supersedes == 1
