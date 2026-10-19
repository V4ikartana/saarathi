"""Cross-task data handoff (backlog P2b): which dependency results a task may see.

Routing already refuses a worker that may not receive a dependency's integration data (ADR-0008).
This module applies the same decision to the content itself, field by field:

- A dependency that used a connected account hands on only its requirements' ``share_fields``. If the
  receiving worker is on another provider, those are further narrowed to the fields the routing
  decision's transfer check permitted, and only when that check was approved.
- A dependency without integrations hands on all its outputs, unless integration data reached it
  through its own dependencies. Field names then say nothing about where the data came from, so those
  outputs cross only when every upstream integration source stays with the same provider.
- Anything without a matching, approved transfer check is dropped: the rule fails closed.

Values are redacted and size-capped. Callers log field names, never values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from saarathi.core.contracts import IntegrationRequirement, NamedOutput, RoutingDecision, TransferCheck

MAX_VALUE_CHARS = 4_000
MAX_TOTAL_CHARS = 20_000


@dataclass(frozen=True)
class DependencyResult:
    task_id: str
    dependencies: tuple[str, ...]
    integrations: tuple[IntegrationRequirement, ...]
    outputs: tuple[NamedOutput, ...]


@dataclass
class Handoff:
    inputs: list[tuple[str, str, str]] = field(default_factory=list)  # (source task, field, value)
    passed: list[str] = field(default_factory=list)  # "T-001.subject"
    dropped: list[str] = field(default_factory=list)  # "T-001.body: reason"


def _transitive(task_id: str, results: Mapping[str, DependencyResult]) -> list[str]:
    seen: set[str] = set()
    stack = list(results[task_id].dependencies) if task_id in results else []
    while stack:
        dep = stack.pop()
        if dep in seen or dep not in results:
            continue
        seen.add(dep)
        stack.extend(results[dep].dependencies)
    return sorted(seen)


def _checks_for(decision: RoutingDecision | None, source: str) -> list[TransferCheck]:
    if decision is None or decision.integration is None:
        return []
    return [t for t in decision.integration.transfers if t.source_task == source and t.to_candidate == decision.chosen]


def _integration_fields(dep: DependencyResult, decision: RoutingDecision | None) -> tuple[set[str], str]:
    """Fields of an integration dependency the receiver may see, and why others are withheld."""
    declared = {f for req in dep.integrations for f in req.share_fields}
    if not declared:
        return set(), "not listed in the requirement's share_fields"
    checks = _checks_for(decision, dep.task_id)
    if not checks:
        return set(), "no transfer check recorded for this worker"
    allowed = set(declared)
    for check in checks:
        if check.mode == "same_provider":
            continue
        if not check.approved:
            return set(), f"cross-provider transfer not permitted ({check.mode})"
        allowed &= set(check.fields)
    return allowed, "not permitted to cross providers (integrations.shareable_fields)"


def plan_handoff(
    task_id: str,
    results: Mapping[str, DependencyResult],
    decision: RoutingDecision | None,
    redact: Callable[[str], str],
) -> Handoff:
    """Decide what the attempt for ``task_id`` receives. ``results`` holds every task of the run."""
    out = Handoff()
    total = 0
    for dep_id in _transitive(task_id, results):
        dep = results[dep_id]
        if not dep.outputs:
            continue
        if dep.integrations:
            allowed, why = _integration_fields(dep, decision)
        else:
            upstream = [u for u in _transitive(dep_id, results) if results[u].integrations]
            checks = [c for u in upstream for c in _checks_for(decision, u)]
            local = all(c.mode == "same_provider" for c in checks) and len(checks) >= len(upstream)
            allowed = {o.name for o in dep.outputs} if not upstream or local else set()
            why = "derived from integration data that may not leave its provider"
        for output in dep.outputs:
            ref = f"{dep_id}.{output.name}"
            if output.name not in allowed:
                out.dropped.append(f"{ref}: {why}")
                continue
            value = redact(output.value)
            if len(value) > MAX_VALUE_CHARS:
                value = value[:MAX_VALUE_CHARS] + "\n[truncated]"
            if total + len(value) > MAX_TOTAL_CHARS:
                out.dropped.append(f"{ref}: handoff size limit reached")
                continue
            total += len(value)
            out.inputs.append((dep_id, output.name, value))
            out.passed.append(ref)
    return out


def dependency_results(
    tasks: Sequence[tuple[str, tuple[str, ...], tuple[IntegrationRequirement, ...], dict[str, Any] | None]],
) -> dict[str, DependencyResult]:
    """Build ``results`` from (task id, dependencies, integrations, stored WorkerResult JSON) rows."""
    built: dict[str, DependencyResult] = {}
    for task_id, deps, integrations, result in tasks:
        report = (result or {}).get("worker_report") or {}
        succeeded = (result or {}).get("status") == "succeeded"
        outputs = tuple(NamedOutput.model_validate(o) for o in report.get("outputs", [])) if succeeded else ()
        built[task_id] = DependencyResult(task_id, deps, integrations, outputs)
    return built
