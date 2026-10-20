"""The engineering lead: intake, planning and final review through structured completions.

The lead proposes; the control plane validates. A plan that fails policy checks is sent back once
with the exact problems, then the run pauses rather than accepting it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from saarathi.core.contracts import EscalationVerdict, IntakeResult, ModelDescriptor, Plan, ReviewVerdict, Usage
from saarathi.core.policy import Policy
from saarathi.git.workspace import ALWAYS_DENIED, path_matches
from saarathi.orchestration import prompts
from saarathi.providers.base import Invocation, InvocationMode, ProviderAdapter
from saarathi.verification.verifier import check_commands

M = TypeVar("M", bound=BaseModel)


class LeadError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, kind: str | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind  # the provider error kind (e.g. "quota_exhausted") when the CLI call itself failed


def plan_policy_problems(plan: Plan, policy: Policy) -> list[str]:
    problems: list[str] = []
    for task in plan.tasks:
        problems += [
            f"{task.id} verification {p}" for p in check_commands(task.verification_commands, policy.permissions)
        ]
        unknown = [c for c in task.required_capabilities if c not in prompts.KNOWN_CAPABILITIES]
        if unknown:
            problems.append(f"{task.id} requires unknown capabilities {unknown}")
        accounts: dict[str, set[str | None]] = {}
        for req in task.integrations:
            accounts.setdefault(req.service, set()).add(req.account)
        for service, selectors in sorted(accounts.items()):
            if len(selectors) > 1:
                problems.append(f"{task.id} uses several {service} accounts; split it into one task per account")
        for pattern in task.allowed_paths:
            if any(path_matches(pattern.rstrip("*").rstrip("/") + "/x", d) for d in ALWAYS_DENIED):
                problems.append(f"{task.id} allowed path {pattern!r} covers a protected location")
    problems += [f"integration check {p}" for p in check_commands(plan.integration_checks, policy.permissions)]
    return problems


class LeadAgent:
    def __init__(
        self,
        adapter: ProviderAdapter,
        model: ModelDescriptor,
        policy: Policy,
        log_root: Path,
        on_usage: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.adapter = adapter
        self.model = model
        self.policy = policy
        self.log_root = log_root
        self.usage: list[dict[str, Any]] = []
        self.on_usage = on_usage  # persists each lead call's provider-reported usage

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
        self._record_usage(purpose, result.usage, ok=result.ok and result.structured is not None)
        if not result.ok or result.structured is None:
            raise LeadError(
                f"{purpose} failed ({result.error_kind}): {result.error}", retryable=True, kind=str(result.error_kind)
            )
        try:
            return out.model_validate(result.structured)
        except ValidationError as exc:
            raise LeadError(f"{purpose} returned output that fails validation: {exc}", retryable=True) from exc

    def _record_usage(self, purpose: str, usage: Usage, *, ok: bool = True) -> None:
        entry = {"purpose": purpose, "ok": ok, **self.identity, **usage.model_dump()}
        self.usage.append(entry)
        if self.on_usage is not None:
            self.on_usage(entry)

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

    async def escalation(self, checkpoint: str, cwd: Path) -> EscalationVerdict:
        return await self._complete(
            "lead.escalation",
            prompts.escalation_prompt(checkpoint),
            prompts.ESCALATION_SCHEMA,
            cwd,
            EscalationVerdict,
        )
