"""Evidence-based completion reports (Markdown for people, JSON for tools)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from saarathi.orchestration.project import Project


def build_report(project: Project, run_id: str) -> dict[str, Any]:
    store = project.store
    run = store.get_run(run_id)
    tasks_out = []
    for task in store.tasks(run_id):
        attempts = store.attempts(run_id, task.id)
        decisions = store.decisions(run_id, task.id)
        result = task.result or {}
        tasks_out.append(
            {
                "id": task.id,
                "title": task.packet.title,
                "state": task.state,
                "attempts": [
                    {
                        "number": a.number,
                        "worker": f"{a.adapter}:{a.model}",
                        "provider": a.provider,
                        "transport": a.transport,
                        "status": a.status,
                        "outcome": a.outcome,
                        "failure_reason": (a.result or {}).get("failure_reason"),
                    }
                    for a in attempts
                ],
                "final_worker": {k: result.get(k) for k in ("adapter", "provider", "model", "transport")}
                if result
                else None,
                "routing": [{"decision_id": d.decision_id, "chosen": d.chosen, "reason": d.reason} for d in decisions],
                "commit": result.get("commit"),
                "changed_files": result.get("changed_files", []),
                "verification": [
                    {
                        "command": e["command"],
                        "exit_code": e["exit_code"],
                        "timed_out": e["timed_out"],
                        "rejected_reason": e.get("rejected_reason"),
                    }
                    for e in result.get("verification", [])
                ],
                "usage": result.get("usage"),
                "worker_assumptions": (result.get("worker_report") or {}).get("assumptions", []),
                "worker_unresolved_risks": (result.get("worker_report") or {}).get("unresolved_risks", []),
                "memory_proposals": (result.get("worker_report") or {}).get("memory_proposals", []),
            }
        )
    questions = store.questions(run_id)
    return {
        "schema_version": 1,
        "run_id": run.id,
        "state": run.state,
        "project": project.name,
        "base_commit": run.base_commit,
        "base_ref": run.base_ref,
        "integration_branch": run.integration_branch,
        "lead": run.lead,
        "clarifications": [{"id": q["id"], "question": q["question"], "answer": q["answer"]} for q in questions],
        "plan_summary": (run.plan or {}).get("summary"),
        "tasks": tasks_out,
        "integration": run.integration,
        "lead_review": run.review,
        "limitations": [
            "Verification commands ran with the user's OS permissions; they are not sandboxed.",
            "Worker self-reports are advisory; success was decided from Git state and verification only.",
            "Costs are shown only where the provider reported them; missing values are unknown, not zero.",
            "Claude Code's cost figure is a client-side, API-equivalent estimate; on a subscription login it is not"
            " a charge.",
        ],
    }


def render_markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# Saarathi report: {data['run_id']}",
        "",
        f"- Project: {data['project']}",
        f"- State: {data['state']}",
        f"- Base: `{data['base_commit']}` ({data['base_ref']})",
        f"- Integration branch: `{data['integration_branch']}`",
    ]
    lead = data.get("lead") or {}
    if lead:
        lines.append(
            f"- Engineering lead: {lead.get('adapter')}:{lead.get('model')} "
            f"({lead.get('provider')}, {lead.get('transport')})"
        )
    lines += ["", "## Plan", "", data.get("plan_summary") or "(none)", ""]
    if data["clarifications"]:
        lines += ["## Clarifications", ""]
        lines += [f"- **{q['id']}** {q['question']}\n  - {q['answer']}" for q in data["clarifications"]]
        lines.append("")
    lines += [
        "## Tasks",
        "",
        "| Task | State | Worker (provider / transport) | Attempts | Changed files |",
        "|---|---|---|---|---|",
    ]
    for t in data["tasks"]:
        w = t["final_worker"] or {}
        worker = f"{w.get('adapter')}:{w.get('model')} ({w.get('provider')} / {w.get('transport')})" if w else "-"
        lines.append(
            f"| {t['id']} {t['title']} | {t['state']} | {worker} | {len(t['attempts'])} | "
            f"{', '.join(t['changed_files']) or '-'} |"
        )
    lines.append("")
    for t in data["tasks"]:
        lines += [f"### {t['id']}: {t['title']}", ""]
        for r in t["routing"]:
            lines.append(f"- Routing `{r['decision_id']}` → {r['chosen']}: {r['reason']}")
        for a in t["attempts"]:
            extra = f" ({a['failure_reason']})" if a.get("failure_reason") else ""
            lines.append(f"- Attempt {a['number']} on {a['worker']}: {a['outcome']}{extra}")
        for v in t["verification"]:
            lines.append(
                f"- Check `{v['command']}` → exit {v['exit_code']}" + (" (timed out)" if v["timed_out"] else "")
            )
        usage = t.get("usage") or {}
        if usage:
            cost = usage.get("cost_usd")
            lines.append(
                f"- Usage: in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
                f"cost={'unknown' if cost is None else f'${cost:.4f} (provider estimate)'}"
            )
        for risk in t["worker_unresolved_risks"]:
            lines.append(f"- Worker-reported risk: {risk}")
        lines.append("")
    integ = data.get("integration") or {}
    lines += ["## Integration", ""]
    for c in integ.get("checks", []):
        lines.append(f"- `{c['command']}` → exit {c['exit_code']}")
    if not integ.get("checks"):
        lines.append("- No integration checks recorded.")
    review = data.get("lead_review") or {}
    lines += [
        "",
        "## Lead review",
        "",
        f"- Decision: **{review.get('decision', 'not reviewed')}**",
        f"- {review.get('summary', '')}",
    ]
    lines += [f"- Concern: {c}" for c in review.get("concerns", [])]
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in data["limitations"]] + [""]
    return "\n".join(lines)


def write_report(project: Project, run_id: str) -> Path:
    data = build_report(project, run_id)
    out_dir = project.meta / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{run_id}.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    md = out_dir / f"{run_id}.md"
    md.write_text(render_markdown(data), encoding="utf-8")
    return md
