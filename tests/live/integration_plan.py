"""Shared live check: the lead's strict PLAN_SCHEMA, with integration requirements, round-trips through a real CLI."""

import asyncio

import yaml

from saarathi.core.contracts import Plan
from saarathi.core.policy import DEFAULT_POLICY_YAML, parse_policy
from saarathi.orchestration import prompts
from saarathi.orchestration.lead import plan_policy_problems
from saarathi.providers.base import Invocation, InvocationMode

BRIEF = (
    "Add scripts/inbox_digest.py, which writes reports/digest.md: one line per unread message (sender and subject)"
    " from the Gmail account with alias 'work', read at run time through the worker's connected mail integration."
    " Do it separately for the Gmail account with alias 'personal' into reports/personal-digest.md."
    " Add tests/test_inbox_digest.py that checks the formatting with fixture data, no network."
)


def run_lead_plan(adapter, repo, tmp_path, model: str) -> Plan:
    summary = prompts.repo_summary(["README.md", "src/app.py"], "# demo\n")
    inv = Invocation(
        purpose="lead.plan",
        mode=InvocationMode.READ_ONLY,
        prompt=prompts.plan_prompt(BRIEF, summary, [], []),
        schema=prompts.PLAN_SCHEMA,
        cwd=repo,
        model=model,
        timeout_s=600,
        log_dir=tmp_path / "log",
    )
    result = asyncio.run(adapter.invoke(inv))
    assert result.ok, result.error
    plan = Plan.model_validate(result.structured)  # pydantic stays the trust boundary
    reqs = [(t.id, r) for t in plan.tasks for r in t.integrations]
    assert reqs, "the plan carries at least one integration requirement"
    assert all(r.service and r.operation for _, r in reqs)
    problems = plan_policy_problems(plan, parse_policy(yaml.safe_load(DEFAULT_POLICY_YAML)))
    assert not [p for p in problems if "accounts; split" in p], problems
    return plan
