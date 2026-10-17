"""The engineering lead: intake, planning and final review through structured completions.

The lead proposes; the control plane validates. A plan that fails policy checks is sent back once
with the exact problems, then the run pauses rather than accepting it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from saarathi.core.contracts import IntakeResult, ModelDescriptor, Plan, ReviewVerdict, Usage
from saarathi.core.policy import Policy
from saarathi.git.workspace import ALWAYS_DENIED, path_matches
from saarathi.orchestration import prompts
from saarathi.providers.base import Invocation, InvocationMode, ProviderAdapter
from saarathi.verification.verifier import check_commands

M = TypeVar("M", bound=BaseModel)


class LeadError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def plan_policy_problems(plan: Plan, policy: Policy) -> list[str]:
    problems: list[str] = []
    for task in plan.tasks:
        problems += [
            f"{task.id} verification {p}" for p in check_commands(task.verification_commands, policy.permissions)
        ]
        unknown = [c for c in task.required_capabilities if c not in prompts.KNOWN_CAPABILITIES]
        if unknown:
            problems.append(f"{task.id} requires unknown capabilities {unknown}")
        for pattern in task.allowed_paths:
            if any(path_matches(pattern.rstrip("*").rstrip("/") + "/x", d) for d in ALWAYS_DENIED):
                problems.append(f"{task.id} allowed path {pattern!r} covers a protected location")
    problems += [f"integration check {p}" for p in check_commands(plan.integration_checks, policy.permissions)]
    return problems


class LeadAgent:
    def __init__(self, adapter: ProviderAdapter, model: ModelDescriptor, policy: Policy, log_root: Path) -> None:
        self.adapter = adapter
        self.model = model
        self.policy = policy
        self.log_root = log_root
        self.usage: list[dict[str, Any]] = []

    @property
    def identity(self) -> dict[str, str]:
        return {
            "adapter": self.model.adapter,
            "provider": self.model.provider,
            "model": self.model.id,
            "transport": self.model.transport,
        }

    async def _complete(self, purpose: str, prompt: str, schema: dict[str, Any], cwd: Path, out: type[M]) -> M:
        inv = Invocation(
            purpose=purpose,
            mode=InvocationMode.READ_ONLY,
            prompt=prompt,
            schema=schema,
            cwd=cwd,
            model=self.model.id,
            timeout_s=self.policy.execution.lead_timeout_seconds,
            log_dir=self.log_root / purpose.replace(".", "-"),
        )
        result = await self.adapter.invoke(inv)
        self._record_usage(purpose, result.usage)
        if not result.ok or result.structured is None:
            raise LeadError(f"{purpose} failed ({result.error_kind}): {result.error}", retryable=True)
        try:
            return out.model_validate(result.structured)
        except ValidationError as exc:
            raise LeadError(f"{purpose} returned output that fails validation: {exc}", retryable=True) from exc

    def _record_usage(self, purpose: str, usage: Usage) -> None:
        self.usage.append({"purpose": purpose, **usage.model_dump()})

    async def intake(self, brief: str, summary: str, cwd: Path) -> IntakeResult:
        return await self._complete(
            "lead.intake", prompts.intake_prompt(brief, summary), prompts.INTAKE_SCHEMA, cwd, IntakeResult
        )

    async def plan(
        self,
        brief: str,
        summary: str,
        answers: Sequence[tuple[str, str]],
        assumptions: Sequence[str],
        cwd: Path,
    ) -> Plan:
        feedback: str | None = None
        for _ in range(2):
            try:
                plan = await self._complete(
                    "lead.plan",
                    prompts.plan_prompt(brief, summary, answers, assumptions, feedback),
                    prompts.PLAN_SCHEMA,
                    cwd,
                    Plan,
                )
            except LeadError as exc:
                feedback = str(exc)
                continue
            problems = plan_policy_problems(plan, self.policy)
            if not problems:
                return plan
            feedback = "\n".join(f"- {p}" for p in problems)
        raise LeadError(f"lead could not produce a plan that passes policy checks:\n{feedback}")

    async def review(self, plan: Plan, evidence: str, diff: str, cwd: Path) -> ReviewVerdict:
        return await self._complete(
            "lead.review",
            prompts.review_prompt(plan, evidence, diff),
            prompts.REVIEW_SCHEMA,
            cwd,
            ReviewVerdict,
        )
