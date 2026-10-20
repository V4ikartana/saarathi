"""Turn real run state and events into conversation entries. The only place the desktop's wording is decided.

Every entry comes from a stored event or record. Nothing here invents progress, reasoning or results.
"""

from __future__ import annotations

from typing import Any

from saarathi.storage.store import EventRecord, RunRecord

PHASES = {
    "INTAKE": "Engineering lead is reading the request",
    "CLARIFYING": "The lead has questions before planning",
    "PLANNING": "Planning",
    "AWAITING_PLAN_APPROVAL": "Plan ready for approval",
    "EXECUTING": "Executing tasks",
    "VERIFYING": "Integrating and verifying",
    "AWAITING_REVIEW": "Ready for your final review",
    "READY_TO_SHIP": "Approved",
    "COMPLETED": "Completed",
    "PAUSED": "Paused",
    "FAILED": "Failed",
    "CANCELLED": "Cancelled",
}


def entry(
    event: EventRecord,
    kind: str,
    title: str,
    *,
    level: str = "info",
    detail: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"{event.run_id}:{event.seq}",
        "seq": event.seq,
        "ts": event.ts,
        "run_id": event.run_id,
        "task_id": event.task_id,
        "kind": kind,
        "level": level,
        "title": title,
        "detail": detail,
        "data": data or {},
        "event_type": event.type,
    }


def render_event(event: EventRecord, run: RunRecord) -> dict[str, Any] | None:
    """One conversation entry for an event, or None for bookkeeping events shown only in technical logs."""
    p = event.payload
    t = event.type
    if t == "lead.intake_recorded":
        lead = run.lead or {}
        who = f"{lead.get('adapter')}:{lead.get('model')}" if lead else "the lead"
        return entry(event, "lead", f"Request reviewed by {who}", data={"lead": lead})
    if t == "questions.added":
        return entry(event, "questions", f"{len(p.get('ids', []))} clarification question(s)", data=p)
    if t == "plan.proposed" and run.plan:
        tasks = run.plan.get("tasks", [])
        return entry(
            event,
            "plan",
            run.plan.get("summary", "Plan proposed"),
            detail=run.plan.get("architecture_notes"),
            data={
                "tasks": tasks,
                "decisions": run.plan.get("decisions", []),
                "integration_checks": run.plan.get("integration_checks", []),
            },
        )
    if t == "run.transition":
        to = str(p.get("to"))
        level = {"FAILED": "error", "CANCELLED": "warn", "PAUSED": "warn", "COMPLETED": "success"}.get(to, "info")
        reason = p.get("reason")
        return entry(
            event,
            "phase",
            PHASES.get(to, to),
            level=level,
            detail=str(reason) if reason else None,
            data={"from": p.get("from"), "to": to},
        )
    if t == "routing.decided":
        chosen = p.get("chosen")
        if chosen is None:
            return entry(event, "routing", f"{event.task_id}: no eligible worker", level="warn", detail=p.get("reason"))
        return entry(
            event,
            "delegation",
            f"{event.task_id} assigned to {chosen}",
            detail=p.get("reason"),
            data={"decision_id": p.get("decision_id"), "candidate": chosen},
        )
    if t == "attempt.started":
        return entry(
            event, "attempt", f"{event.task_id} attempt {p.get('number')} started on {p.get('candidate')}", data=p
        )
    if t == "task.transition":
        to = str(p.get("to"))
        if to in ("SUCCEEDED", "FAILED", "BLOCKED", "VERIFYING"):
            level = {"SUCCEEDED": "success", "FAILED": "error", "BLOCKED": "warn"}.get(to, "info")
            label = {
                "VERIFYING": "is being verified",
                "SUCCEEDED": "passed verification",
                "FAILED": "failed",
                "BLOCKED": "is blocked",
            }[to]
            reason = p.get("reason")
            return entry(
                event,
                "task",
                f"{event.task_id} {label}",
                level=level,
                detail=str(reason) if reason else None,
                data={"from": p.get("from"), "to": to},
            )
        return None
    if t == "escalation.raised":
        return entry(
            event,
            "escalation",
            f"{event.task_id} escalated: {p.get('kind')}",
            level="warn",
            detail=p.get("summary"),
            data=p,
        )
    if t == "lead.escalation_reviewed":
        return entry(event, "escalation", f"Lead ruled on {p.get('escalation_id')}: {p.get('decision')}", data=p)
    if t == "integration.verified":
        return entry(event, "integration", "Task branches merged; integration checks ran")
    if t == "integration.conflict":
        return entry(event, "integration", "Task branches conflict", level="error")
    if t == "lead.reviewed" and run.review:
        decision = run.review.get("decision")
        return entry(
            event,
            "review",
            f"Lead review: {decision}",
            level="success" if decision == "approve" else "warn",
            detail=run.review.get("summary"),
            data={"concerns": run.review.get("concerns", [])},
        )
    if t == "provider.limited":
        return entry(
            event,
            "provider",
            f"{p.get('adapter')} {str(p.get('state', '')).replace('_', ' ')}",
            level="warn",
            detail=f"until {p.get('until')}: {p.get('reason')}",
        )
    if t.startswith("security."):
        return entry(event, "security", t.split(".", 1)[1].replace("_", " "), level="error", data=p)
    if t == "approval.automatic":
        what = "Plan" if p.get("kind") == "plan" else "Final review"
        return entry(
            event,
            "approval",
            f"{what} approved by the session's preset ({p.get('preset')})",
            detail=p.get("reason"),
            data=p,
        )
    if t == "approval.required":
        what = "The plan" if p.get("kind") == "plan" else "The final review"
        return entry(event, "approval", f"{what} needs you: {p.get('reason')}", level="warn", data=p)
    if t == "checkpoint.created":
        return entry(
            event, "checkpoint", f"Checkpoint: {p.get('kind')}", level="warn", detail="\n".join(p.get("items", []))
        )
    return None
