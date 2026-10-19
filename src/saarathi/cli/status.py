"""Run status snapshot shared by the CLI (`--json`) and the desktop bridge."""

from __future__ import annotations

from saarathi.cli.output import StatusOut, TaskOut
from saarathi.orchestration.project import Project


def build_status(project: Project, run_id: str) -> StatusOut:
    run = project.store.get_run(run_id)
    tasks = []
    for t in project.store.tasks(run_id):
        attempts = project.store.attempts(run_id, t.id)
        last = attempts[-1] if attempts else None
        tasks.append(
            TaskOut(
                id=t.id,
                title=t.packet.title,
                state=t.state,
                attempts=len(attempts),
                failures=t.failures,
                dependencies=t.packet.dependencies,
                worker=f"{last.adapter}:{last.model}" if last else None,
                provider=last.provider if last else None,
                transport=last.transport if last else None,
                blocked_reason=t.blocked_reason,
                failure_reason=(t.result or {}).get("failure_reason"),
            )
        )
    questions = [q for q in project.store.questions(run_id) if q["answer"] is None]
    return StatusOut(
        run_id=run.id,
        state=run.state,
        pause_reason=run.pause_reason,
        paused_from=run.paused_from,
        base_commit=run.base_commit,
        integration_branch=run.integration_branch,
        open_questions=[f"{q['id']}: {q['question']}" for q in questions],
        tasks=tasks,
    )
