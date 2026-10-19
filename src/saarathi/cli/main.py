"""``saarathi`` command-line interface."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any

import typer

from saarathi import __version__
from saarathi.cli.output import DoctorOut, ModelOut, PolicyOut, StatusOut, TaskOut, emit
from saarathi.core.errors import ExitCode, SaarathiError
from saarathi.core.policy import load_policy
from saarathi.core.states import RunState
from saarathi.orchestration.engine import Engine, render_plan
from saarathi.orchestration.project import META, Project
from saarathi.providers.base import Invocation, InvocationMode
from saarathi.providers.claude_cli import LIVE_UNVERIFIED_PREFIX
from saarathi.providers.registry import ProviderRegistry
from saarathi.reporting.report import build_report, render_markdown
from saarathi.routing.router import resolve_lead

app = typer.Typer(
    name="saarathi",
    help="Many agents. One direction. Local-first orchestration of AI coding agents into verified changes.",
    no_args_is_help=True,
    add_completion=False,
)
models_app = typer.Typer(help="Model catalogue.", no_args_is_help=True)
policy_app = typer.Typer(help="Policy checks.", no_args_is_help=True)
route_app = typer.Typer(help="Routing decisions.", no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(policy_app, name="policy")
app.add_typer(route_app, name="route")

JsonFlag = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
NonInteractive = Annotated[
    bool, typer.Option("--non-interactive", help="Never prompt; pause where a person must decide.")
]
AcceptDefaults = Annotated[
    bool,
    typer.Option("--accept-defaults", help="Answer clarification questions with the lead's stated defaults."),
]
WAITING = {RunState.CLARIFYING, RunState.AWAITING_PLAN_APPROVAL, RunState.AWAITING_REVIEW, RunState.PAUSED}


class ConsoleIO:
    def __init__(self, interactive: bool) -> None:
        self.interactive = interactive and sys.stdin.isatty()

    def info(self, message: str) -> None:
        typer.echo(message, err=True)

    def ask(self, question: str, default: str) -> str | None:
        if not self.interactive:
            typer.echo(f"Question: {question}\n  default if accepted: {default}", err=True)
            return None
        answer: str = typer.prompt(question + "\n  answer (Enter accepts the default)", default=default)
        return answer

    def confirm(self, message: str) -> bool:
        return self.interactive and typer.confirm(message, default=False)


def _fail(exc: SaarathiError) -> None:
    typer.echo(f"error: {exc.message}", err=True)
    if exc.hint:
        typer.echo(f"hint: {exc.hint}", err=True)
    raise typer.Exit(exc.exit_code)


def _project() -> Project:
    try:
        return Project.load(Path.cwd())
    except SaarathiError as exc:
        _fail(exc)
        raise  # unreachable


def _run_id(project: Project, run_id: str | None) -> str:
    if run_id:
        return run_id
    run = project.store.active_run() or project.store.latest_run()
    if run is None:
        _fail(SaarathiError("no runs yet", hint="start one with `saarathi run brief.md`"))
        raise AssertionError
    return run.id


def _exit_for(state: RunState) -> int:
    if state is RunState.COMPLETED:
        return ExitCode.OK
    if state in WAITING:
        return ExitCode.AWAITING_INPUT
    return ExitCode.FAILED


def _status(project: Project, run_id: str) -> StatusOut:
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


def _print_status(status: StatusOut) -> None:
    typer.echo(f"Run {status.run_id}: {status.state}")
    if status.pause_reason:
        typer.echo(f"  paused: {status.pause_reason}")
    for q in status.open_questions:
        typer.echo(f"  open question {q}")
    for t in status.tasks:
        worker = f" on {t.worker} ({t.provider}/{t.transport})" if t.worker else ""
        typer.echo(f"  {t.id} {t.state:<10} {t.title}{worker}")
        if t.blocked_reason or (t.failure_reason and t.state != "SUCCEEDED"):
            typer.echo(f"      {t.blocked_reason or t.failure_reason}")
    if status.integration_branch:
        typer.echo(f"  integration branch: {status.integration_branch}")


def _drive(project: Project, engine: Engine, run_id: str, json_out: bool) -> None:
    try:
        state = asyncio.run(engine.drive(run_id))
    except KeyboardInterrupt:
        typer.echo("Interrupted. In-flight work was stopped; continue with `saarathi resume`.", err=True)
        raise typer.Exit(130) from None
    except SaarathiError as exc:
        _fail(exc)
        raise
    status = _status(project, run_id)
    emit(status, json_out, _print_status)
    hints = {
        RunState.CLARIFYING: 'Answer with `saarathi answer <id> "..."`, then `saarathi resume`.',
        RunState.AWAITING_PLAN_APPROVAL: "Review with `saarathi status`, then `saarathi approve`.",
        RunState.AWAITING_REVIEW: "Inspect with `saarathi review`, then `saarathi approve` to finish.",
        RunState.PAUSED: "Fix the cause shown above, then `saarathi resume`.",
    }
    if not json_out and state in hints:
        typer.echo(hints[state], err=True)
    raise typer.Exit(_exit_for(state))


# ====================================================================== commands


@app.command()
def version() -> None:
    """Print the Saarathi version."""
    typer.echo(__version__)


@app.command()
def init(name: Annotated[str | None, typer.Option(help="Project display name.")] = None) -> None:
    """Create .saarathi/ with a default policy in the current Git repository."""
    try:
        root = Project.init(Path.cwd(), name=name)
    except SaarathiError as exc:
        _fail(exc)
    typer.echo(f"Initialised {root / META}. Review policies.yaml, then run `saarathi doctor`.")


def _live_probe(registry: ProviderRegistry, adapter_name: str, model: str) -> tuple[bool, str]:
    adapter = registry.get(adapter_name)
    with tempfile.TemporaryDirectory(prefix="saarathi-probe-") as tmp:
        root = Path(tmp)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        inv = Invocation(
            purpose="doctor.live",
            mode=InvocationMode.READ_ONLY,
            prompt='Do not use any tools. Reply with the JSON object {"ok": true}.',
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
            cwd=root,
            model=model,
            timeout_s=180,
            log_dir=root / ".logs",
        )
        result = asyncio.run(adapter.invoke(inv))
    if result.ok and result.structured == {"ok": True}:
        return (
            True,
            f"structured completion ok in {result.duration_s:.1f}s; "
            f"model={result.usage.reported_model or 'unreported'}",
        )
    return False, f"{result.error_kind}: {result.error}"


@app.command()
def doctor(
    live: Annotated[
        bool, typer.Option("--live", help="Run one tiny real completion per adapter (uses quota).")
    ] = False,
    json_out: JsonFlag = False,
) -> None:
    """Inspect installed provider CLIs, authentication and verified capabilities."""
    project = _project()
    out = DoctorOut(project=str(project.root), adapters=[], lead=None, lead_problems=[])
    for name, adapter in project.registry.adapters.items():
        probe = adapter.probe()
        entry: dict[str, Any] = {
            "name": name,
            "type": adapter.type_name,
            "provider": probe.provider,
            "available": probe.available,
            "enabled": adapter.config.enabled,
            "binary": probe.binary,
            "version": probe.version,
            "auth_method": probe.auth_method,
            "metered": probe.metered,
            "reason": probe.reason,
            "test_only": probe.test_only,
            "checks": [c.__dict__ for c in probe.checks],
            "notices": probe.notices,
            "live": None,
        }
        if live and probe.available and adapter.config.models and not probe.test_only:
            if not json_out:
                typer.echo(f"Running a live probe on {name} (uses a small amount of quota)...", err=True)
            ok, detail = _live_probe(project.registry, name, adapter.config.models[0].id)
            entry["live"] = {"ok": ok, "detail": detail}
            if ok:
                entry["notices"] = [n for n in probe.notices if not n.startswith(LIVE_UNVERIFIED_PREFIX)]
        out.adapters.append(entry)
    lead, problems = resolve_lead(project.registry.models(), project.policy)
    out.lead = lead.candidate_id if lead else None
    out.lead_problems = problems

    def show(d: DoctorOut) -> None:
        typer.echo(f"Project: {d.project}")
        for a in d.adapters:
            mark = "ok " if a["available"] else "-- "
            tag = " [test-only]" if a["test_only"] else ""
            typer.echo(f"{mark}{a['name']} ({a['type']}, provider {a['provider']}){tag}")
            typer.echo(
                f"     version={a['version']} auth={a['auth_method']} metered={a['metered']} binary={a['binary']}"
            )
            for c in a["checks"]:
                typer.echo(f"     [{'pass' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
            if a["reason"]:
                typer.echo(f"     unavailable: {a['reason']}")
            if a["live"]:
                typer.echo(f"     live probe: {'pass' if a['live']['ok'] else 'FAIL'} - {a['live']['detail']}")
            for n in a["notices"]:
                typer.echo(f"     note: {n}")
        typer.echo(f"Engineering lead: {d.lead or 'UNAVAILABLE'}")
        for p in d.lead_problems:
            typer.echo(f"     {p}")

    emit(out, json_out, show)
    raise typer.Exit(ExitCode.OK if out.lead else ExitCode.POLICY)


@models_app.command("list")
def models_list(json_out: JsonFlag = False) -> None:
    """Show the model/transport combinations the router may consider."""
    project = _project()
    models = [ModelOut.from_descriptor(m) for m in project.registry.models()]

    def show(items: list[ModelOut]) -> None:
        for m in items:
            status = "available" if m.available else f"unavailable ({m.unavailable_reason})"
            billing = "metered" if m.metered else "subscription/unmetered"
            typer.echo(
                f"{m.candidate:<28} {m.provider:<10} {m.transport:<4} tier={m.reasoning_tier:<9} {billing:<22} {status}"
            )

    emit(models, json_out, show)


@policy_app.command("validate")
def policy_validate(json_out: JsonFlag = False) -> None:
    """Validate .saarathi/policies.yaml and check every referenced model exists."""
    try:
        root = Project.load(Path.cwd()).root
        policy = load_policy(root / META / "policies.yaml")
    except SaarathiError as exc:
        if json_out:
            emit(PolicyOut(valid=False, errors=[exc.message], warnings=[]), True, lambda _: None)
            raise typer.Exit(exc.exit_code) from None
        _fail(exc)
        raise
    warnings = []
    if policy.billing.api_enabled and policy.billing.max_run_usd == 0:
        warnings.append("billing.api_enabled is true but max_run_usd is 0")
    if not policy.permissions.require_approval_for_merge:
        warnings.append("require_approval_for_merge is false; Saarathi still never merges into your branches")
    emit(
        PolicyOut(valid=True, errors=[], warnings=warnings),
        json_out,
        lambda o: typer.echo("Policy is valid." + "".join(f"\nwarning: {w}" for w in o.warnings)),
    )


@app.command()
def run(
    brief: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="Markdown brief.")],
    non_interactive: NonInteractive = False,
    accept_defaults: AcceptDefaults = False,
    json_out: JsonFlag = False,
) -> None:
    """Start a run from a brief: intake, clarify, plan, then execute after approval."""
    project = _project()
    engine = Engine(project, ConsoleIO(not non_interactive and not json_out), accept_defaults=accept_defaults)
    try:
        run_id = asyncio.run(engine.start(brief))
    except SaarathiError as exc:
        _fail(exc)
        raise
    if not json_out:
        typer.echo(f"Started {run_id}", err=True)
    _drive(project, engine, run_id, json_out)


@app.command()
def resume(
    run_id: Annotated[str | None, typer.Option("--run")] = None,
    non_interactive: NonInteractive = False,
    accept_defaults: AcceptDefaults = False,
    json_out: JsonFlag = False,
) -> None:
    """Recover durable state and continue a run (after a pause, crash or answered question)."""
    project = _project()
    rid = _run_id(project, run_id)
    engine = Engine(project, ConsoleIO(not non_interactive and not json_out), accept_defaults=accept_defaults)
    engine.resume(rid)
    _drive(project, engine, rid, json_out)


@app.command()
def answer(question_id: str, text: str, run_id: Annotated[str | None, typer.Option("--run")] = None) -> None:
    """Answer a clarification question."""
    project = _project()
    try:
        project.store.answer_question(_run_id(project, run_id), question_id, text)
    except SaarathiError as exc:
        _fail(exc)
    typer.echo(f"Recorded answer to {question_id}. Continue with `saarathi resume`.")


@app.command()
def approve(
    run_id: Annotated[str | None, typer.Option("--run")] = None,
    non_interactive: NonInteractive = False,
    json_out: JsonFlag = False,
) -> None:
    """Approve the pending plan (then execute) or the final review (then write the report)."""
    project = _project()
    rid = _run_id(project, run_id)
    engine = Engine(project, ConsoleIO(not non_interactive and not json_out))
    state = project.store.get_run(rid).state
    try:
        if state is RunState.AWAITING_PLAN_APPROVAL:
            engine.approve_plan(rid)
            _drive(project, engine, rid, json_out)
        elif state is RunState.AWAITING_REVIEW:
            report_path = engine.approve_review(rid)
            if not json_out:
                typer.echo(f"Completed. Report: {report_path}")
            emit(_status(project, rid), json_out, lambda _: None)
        else:
            raise SaarathiError(f"nothing to approve: run is {state}")
    except SaarathiError as exc:
        _fail(exc)


@app.command()
def status(run_id: Annotated[str | None, typer.Option("--run")] = None, json_out: JsonFlag = False) -> None:
    """Show run and task status."""
    project = _project()
    emit(_status(project, _run_id(project, run_id)), json_out, _print_status)


@app.command()
def tasks(run_id: Annotated[str | None, typer.Option("--run")] = None, json_out: JsonFlag = False) -> None:
    """List the task DAG."""
    project = _project()
    st = _status(project, _run_id(project, run_id))

    def show(items: list[TaskOut]) -> None:
        for t in items:
            deps = f" <- {', '.join(t.dependencies)}" if t.dependencies else ""
            typer.echo(f"{t.id} {t.state:<10} {t.title}{deps}")

    emit(st.tasks, json_out, show)


@app.command()
def logs(
    task: Annotated[str | None, typer.Option("--task")] = None,
    run_id: Annotated[str | None, typer.Option("--run")] = None,
    limit: int = 200,
    json_out: JsonFlag = False,
) -> None:
    """Show the redacted event log."""
    project = _project()
    events = project.store.events(_run_id(project, run_id), task_id=task)[-limit:]
    if json_out:
        typer.echo(json.dumps([e.__dict__ for e in events], indent=2, default=str))
        return
    for e in events:
        scope = f" {e.task_id}" if e.task_id else ""
        typer.echo(f"{e.seq:>5} {e.ts} {e.actor:<7} {e.type}{scope} {json.dumps(e.payload, default=str)[:200]}")


@app.command()
def pause(run_id: Annotated[str | None, typer.Option("--run")] = None) -> None:
    """Ask the running engine to stop dispatching and pause after in-flight attempts finish."""
    project = _project()
    project.store.set_control(_run_id(project, run_id), "pause_requested", "1", actor="user")
    typer.echo("Pause requested.")


@app.command()
def cancel(run_id: Annotated[str | None, typer.Option("--run")] = None) -> None:
    """Cancel a run that is not currently being driven. Branches and worktrees are kept for inspection."""
    project = _project()
    rid = _run_id(project, run_id)
    try:
        with project.engine_lock():
            project.store.transition_run(rid, RunState.CANCELLED, actor="user", reason="cancelled by user")
    except SaarathiError as exc:
        _fail(exc)
    typer.echo(f"Cancelled {rid}.")


@app.command()
def review(run_id: Annotated[str | None, typer.Option("--run")] = None) -> None:
    """Show the plan, integration evidence, lead verdict and diff summary."""
    project = _project()
    rid = _run_id(project, run_id)
    run = project.store.get_run(rid)
    if run.plan:
        from saarathi.core.contracts import Plan

        typer.echo(render_plan(Plan.model_validate(run.plan)))
    integ = run.integration or {}
    if integ:
        typer.echo(f"\nIntegration branch: {integ.get('branch')}")
        for check in integ.get("checks", []):
            typer.echo(f"  check `{check['command']}` -> exit {check['exit_code']}")
        if integ.get("head") and run.base_commit:
            typer.echo("\n" + project.git.diff_stat(run.base_commit, integ["head"]))
    if run.review:
        typer.echo(f"Lead review: {run.review['decision']} - {run.review['summary']}")
        for c in run.review.get("concerns", []):
            typer.echo(f"  concern: {c}")


@app.command()
def report(run_id: Annotated[str | None, typer.Option("--run")] = None, json_out: JsonFlag = False) -> None:
    """Print the evidence-based report (Markdown or JSON)."""
    project = _project()
    data = build_report(project, _run_id(project, run_id))
    typer.echo(json.dumps(data, indent=2, default=str) if json_out else render_markdown(data))


@route_app.command("explain")
def route_explain(
    task_id: str, run_id: Annotated[str | None, typer.Option("--run")] = None, json_out: JsonFlag = False
) -> None:
    """Explain every routing decision made for a task."""
    project = _project()
    decisions = project.store.decisions(_run_id(project, run_id), task_id)
    if json_out:
        typer.echo(json.dumps([d.model_dump(mode="json") for d in decisions], indent=2))
        return
    if not decisions:
        typer.echo(f"No routing decisions recorded for {task_id}.")
    for d in decisions:
        typer.echo(f"{d.decision_id}: chose {d.chosen or 'nothing'} - {d.reason}")
        for alt in d.alternatives:
            typer.echo(f"  alternative: {alt}")
        for b in d.busy:
            typer.echo(f"  busy: {b}")
        for r in d.rejected:
            typer.echo(f"  rejected {r.candidate}: {'; '.join(r.reasons)}")


def main() -> None:  # pragma: no cover
    app()
