"""Operations role (ADR-0001): turns execution state into concise, evidence-backed checkpoints.

v0.1 implements it deterministically. A model-backed variant (backlog P5) would implement the same
interface; the lead, not operations, decides anything that changes architecture or scope.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from saarathi.core.contracts import Escalation, TaskPacket

MAX_EVIDENCE_CHARS = 6_000


@dataclass(frozen=True)
class EscalationContext:
    escalation_id: str
    packet: TaskPacket
    worker: str
    escalation: Escalation
    worker_summary: str
    attempt_diff_stat: str
    adrs: Sequence[tuple[int, str, str, str]]  # (number, title, status, decision text)
    affected: Sequence[tuple[str, str, str]]  # (task id, title, state) of transitive dependents
    previous: Sequence[str]  # earlier escalations of this task, one line each


class OperationsAgent(Protocol):
    def escalation_checkpoint(self, ctx: EscalationContext) -> str: ...


class DeterministicOperations:
    def escalation_checkpoint(self, ctx: EscalationContext) -> str:
        p, e = ctx.packet, ctx.escalation
        adrs = (
            "\n".join(
                f"- ADR-{n:04d} [{status}] {title}: {decision.strip()[:600]}" for n, title, status, decision in ctx.adrs
            )
            or "- (none)"
        )
        affected = "\n".join(f"- {tid} {title} ({state})" for tid, title, state in ctx.affected) or "- (none)"
        previous = "\n".join(f"- {line}" for line in ctx.previous) or "- (none)"
        evidence = e.evidence[:MAX_EVIDENCE_CHARS] + ("\n[truncated]" if len(e.evidence) > MAX_EVIDENCE_CHARS else "")
        return f"""# Escalation {ctx.escalation_id}: {e.kind}

Task {p.task_id} "{p.title}" was stopped by its worker ({ctx.worker}). It is paused, along with the tasks that
depend on it. Independent tasks continue.

## Worker's claim
{e.summary}

## Evidence reported by the worker
{evidence or "(none given)"}

## Change the worker proposes
{e.proposed_change or "(none given)"}

## Worker's summary
{ctx.worker_summary}

## What the attempt had changed before it stopped (discarded)
{ctx.attempt_diff_stat.strip() or "(no changes)"}

## Task as approved
Objective: {p.objective}
Acceptance criteria:
{chr(10).join(f"- {c}" for c in p.acceptance_criteria)}
Allowed paths: {", ".join(p.allowed_paths)}
Verification: {"; ".join(p.verification_commands)}

## Accepted decisions
{adrs}

## Tasks waiting on this one
{affected}

## Earlier escalations of this task
{previous}
"""
