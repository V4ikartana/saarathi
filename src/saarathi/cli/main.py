"""``saarathi`` command-line interface."""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from saarathi import __version__
from saarathi.cli.output import (
    AccountOut,
    AccountsOut,
    CapabilityOut,
    ConnectionOut,
    DoctorOut,
    IntegrationsOut,
    ModelOut,
    PolicyOut,
    ScanEntryOut,
    ScanOut,
    StatusOut,
    TaskOut,
    VerifyOut,
    emit,
)
from saarathi.cli.status import build_status
from saarathi.core.contracts import RoutingDecision
from saarathi.core.errors import ExitCode, SaarathiError
from saarathi.core.policy import load_policy
from saarathi.core.states import RunState
from saarathi.integrations.discovery import discovery_for
from saarathi.integrations.model import ConnectionStatus, Evidence, IntegrationSnapshot
from saarathi.memory.lessons import approve_lesson
from saarathi.orchestration.engine import GRANT_PREFIX, SELECT_PREFIX, Engine, render_plan
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
providers_app = typer.Typer(help="Provider limits and cooldowns.", no_args_is_help=True)
lessons_app = typer.Typer(
    help="Lessons proposed by workers; nothing is kept without your approval.", invoke_without_command=True
)
app.add_typer(lessons_app, name="lessons")
escalations_app = typer.Typer(
    help="Escalations raised by workers and the lead's verdicts.", invoke_without_command=True
)
app.add_typer(escalations_app, name="escalations")
app.add_typer(providers_app, name="providers")
integrations_app = typer.Typer(
    help="Integration and account registry: which worker may do what, on which account.", no_args_is_help=True
)
app.add_typer(integrations_app, name="integrations")

JsonFlag = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
WorkspaceOpt = Annotated[
    str | None, typer.Option("--workspace", help="Workspace scope (default: integrations.workspace in the policy).")
]
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
    return build_status(project, run_id)


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


def _interrupt_on_termination() -> None:
    """Make SIGTERM (and Windows CTRL_BREAK) stop a run the way Ctrl-C does: attempts cancelled, run paused."""
    for name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with contextlib.suppress(ValueError, OSError):  # only possible on the main thread
                signal.signal(sig, signal.default_int_handler)


def _drive(project: Project, engine: Engine, run_id: str, json_out: bool) -> None:
    _interrupt_on_termination()
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
            "provider_limit": _active_limits(project).get(name),
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
    if (
        lead
        and lead.adapter in project.registry.adapters
        and project.registry.get(lead.adapter).type_name == "antigravity-cli"
    ):
        problems = [
            *problems,
            "note: Antigravity as lead is less reliable than as a worker: a read-only call that "
            "reaches for a disallowed tool ends without a result (one retry, then the run pauses).",
        ]
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
            if a["provider_limit"]:
                typer.echo(
                    f"     provider limit: {a['provider_limit']} (clear early: saarathi providers reset {a['name']})"
                )
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
    limits = _active_limits(project)
    models = [ModelOut.from_descriptor(m, limits.get(m.adapter)) for m in project.registry.models()]

    def show(items: list[ModelOut]) -> None:
        for m in items:
            status = "available" if m.available else f"unavailable ({m.unavailable_reason})"
            if m.available and m.provider_limit:
                status = f"available but {m.provider_limit}"
            billing = "metered" if m.metered else "subscription/unmetered"
            typer.echo(
                f"{m.candidate:<28} {m.provider:<10} {m.transport:<4} tier={m.reasoning_tier:<9} {billing:<22} {status}"
            )

    emit(models, json_out, show)


def _active_limits(project: Project) -> dict[str, str]:
    """adapter -> human description of a provider cooldown still in force."""
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    return {
        h.adapter: f"{h.state.replace('_', ' ')} until {h.until}: {h.reason}"
        for h in project.store.provider_health().values()
        if h.until > now
    }


@lessons_app.callback()
def lessons_list(
    ctx: typer.Context,
    all_: Annotated[bool, typer.Option("--all", help="Include approved and rejected proposals.")] = False,
    json_out: JsonFlag = False,
) -> None:
    """List lesson proposals from verified work."""
    if ctx.invoked_subcommand:
        return
    project = _project()
    items = project.store.lessons(status=None if all_ else "proposed")
    if json_out:
        typer.echo(json.dumps([i.__dict__ for i in items], indent=2))
        return
    if not items:
        typer.echo("No lesson proposals." if not all_ else "No lessons recorded.")
    for i in items:
        typer.echo(f"{i.id} [{i.status}] {i.task_id} via {i.source}: {i.text}")
    if any(i.status == "proposed" for i in items):
        typer.echo(
            "Keep one with `saarathi lessons approve <id> [--text ...]`, drop it with `saarathi lessons reject <id>`."
        )


@lessons_app.command("approve")
def lessons_approve(
    lesson_id: str,
    text: Annotated[str | None, typer.Option("--text", help="Edited wording to keep instead.")] = None,
) -> None:
    """Add a proposal to .saarathi/lessons.md, optionally reworded. Later tasks see it in their context."""
    project = _project()
    try:
        lesson = approve_lesson(project.store, project.memory, lesson_id, text=text)
    except (SaarathiError, ValueError) as exc:
        _fail(exc if isinstance(exc, SaarathiError) else SaarathiError(str(exc)))
        raise
    typer.echo(f"Added to .saarathi/lessons.md: {lesson.text}")


@lessons_app.command("reject")
def lessons_reject(lesson_id: str) -> None:
    """Drop a proposal. It is kept in the state database for the audit trail only."""
    project = _project()
    try:
        project.store.decide_lesson(lesson_id, status="rejected", actor="user")
    except (SaarathiError, ValueError) as exc:
        _fail(exc if isinstance(exc, SaarathiError) else SaarathiError(str(exc)))
    typer.echo(f"Rejected {lesson_id}.")


@escalations_app.callback()
def escalations_list(
    ctx: typer.Context,
    run_id: Annotated[str | None, typer.Option("--run")] = None,
    json_out: JsonFlag = False,
) -> None:
    """List escalations with the lead's verdict and the checkpoint sent to it."""
    if ctx.invoked_subcommand:
        return
    project = _project()
    items = project.store.escalations(_run_id(project, run_id))
    if json_out:
        typer.echo(json.dumps([{k: v for k, v in e.__dict__.items() if k != "report"} for e in items], indent=2))
        return
    if not items:
        typer.echo("No escalations.")
    for e in items:
        typer.echo(f"{e.id} {e.task_id} {e.kind} [{e.status}] {e.summary}")
        verdict = e.verdict or {}
        if verdict:
            typer.echo(f"   lead: {verdict['decision']} - {verdict['rationale']}")
            adr = verdict.get("adr") or {}
            if verdict["decision"] == "supersede_adr":
                typer.echo(f"   proposed: ADR replacing ADR-{adr['supersedes']:04d}: {adr['title']}")
                typer.echo(f"   decision: {adr['decision']}")
            if verdict.get("guidance"):
                typer.echo(f"   guidance: {verdict['guidance']}")
        if e.resolution:
            typer.echo(f"   resolution: {e.resolution}")
    if any(e.status == "awaiting_approval" for e in items):
        typer.echo("Approve with `saarathi approve`, or reject with `saarathi escalations reject <id>`.")


@escalations_app.command("reject")
def escalations_reject(escalation_id: str, run_id: Annotated[str | None, typer.Option("--run")] = None) -> None:
    """Reject the lead's proposed change. The task stays blocked; the accepted ADR is unchanged."""
    project = _project()
    try:
        Engine(project).reject_escalation(_run_id(project, run_id), escalation_id)
    except SaarathiError as exc:
        _fail(exc)
    typer.echo(f"Rejected {escalation_id}. The task stays blocked; `resume` retries it, `cancel` ends the run.")


@providers_app.command("status")
def providers_status(json_out: JsonFlag = False) -> None:
    """Show provider cooldowns recorded after rate-limit or quota errors."""
    project = _project()
    health = sorted(project.store.provider_health().values(), key=lambda h: h.adapter)
    if json_out:
        typer.echo(json.dumps([h.__dict__ for h in health], indent=2))
        return
    if not health:
        typer.echo("No provider limits recorded.")
    for h in health:
        typer.echo(f"{h.adapter}: {h.state} until {h.until} (hit {h.consecutive}x) - {h.reason}")


@providers_app.command("reset")
def providers_reset(adapter: str) -> None:
    """Clear a recorded provider limit, for example after your plan's usage window resets."""
    project = _project()
    project.registry.get(adapter)  # unknown adapters are a usage error
    cleared = project.store.clear_provider_health(adapter, actor="user", reason="cleared by user")
    typer.echo(f"Cleared the limit on {adapter}." if cleared else f"No limit recorded for {adapter}.")


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
    """Approve the pending plan, an escalation's ADR change, or the final review."""
    project = _project()
    rid = _run_id(project, run_id)
    engine = Engine(project, ConsoleIO(not non_interactive and not json_out))
    state = project.store.get_run(rid).state
    try:
        if state is RunState.AWAITING_PLAN_APPROVAL:
            engine.approve_plan(rid)
            _drive(project, engine, rid, json_out)
        elif state is RunState.PAUSED and engine.pending_approval(rid):
            new_adr = engine.approve_escalation(rid)
            if not json_out:
                typer.echo(f"Recorded {new_adr}; the escalated task continues with the lead's guidance.", err=True)
            if project.store.get_run(rid).state is RunState.PAUSED:
                emit(_status(project, rid), json_out, _print_status)  # more escalations wait
                raise typer.Exit(ExitCode.AWAITING_INPUT)
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


def _print_integration(d: RoutingDecision) -> None:
    trace = d.integration
    if trace is None:
        return
    typer.echo(f"  integration (workspace {trace.workspace}):")
    for r in trace.requirements:
        account = (
            f"account '{r.account_label}' ({r.account}) via {r.resolved_by}" if r.account else "account unresolved"
        )
        identity = f", identity {r.identity_status}" if r.identity_status else ""
        typer.echo(f"    [{r.index}] {r.service}/{r.operation} ({r.effect}) {account}{identity}")
        for c in r.connections:
            state = "eligible" if c.eligible else "rejected: " + "; ".join(c.reasons)
            evidence = f" [{c.evidence}{' ' + c.checked_at if c.checked_at else ''}]" if c.evidence else ""
            typer.echo(f"        {c.connection} on worker {c.worker} ({c.adapter}){evidence}: {state}")
        if r.problem:
            typer.echo(f"        problem: {r.problem}")
    for choice in trace.account_choices:
        typer.echo(
            f"    choose for [{choice.requirement}]: {choice.account} ('{choice.label}', "
            f"identity {choice.identity_status}) -> "
            f"`saarathi integrations select {d.task_id} {choice.account} --requirement {choice.requirement}`"
        )
    for t in trace.transfers:
        typer.echo(
            f"    transfer {t.source_task} {t.service} {t.from_provider} -> {t.to_candidate} ({t.to_provider}): "
            f"{t.mode}, fields {t.fields}, approved={'yes' if t.approved else 'no'}"
        )
    for a in trace.approvals_required:
        typer.echo(f"    approval required: {a.key} - {a.detail}")
        typer.echo(f"        grant with `saarathi integrations approve {d.task_id} '{a.key}'`")
    for key in trace.approvals_granted:
        typer.echo(f"    approval granted: {key}")
    if trace.selected_connections:
        typer.echo(f"    selected connection(s): {', '.join(trace.selected_connections)}")


@route_app.command("explain")
def route_explain(
    task_id: str, run_id: Annotated[str | None, typer.Option("--run")] = None, json_out: JsonFlag = False
) -> None:
    """Explain every routing decision made for a task, including integration eligibility."""
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
        _print_integration(d)


# ====================================================================== integrations


def _ws(project: Project, workspace: str | None) -> str:
    return workspace or project.workspace


def _connections_out(snap: IntegrationSnapshot) -> list[ConnectionOut]:
    out = []
    for c in snap.connections:
        account = snap.accounts[c.account_id]
        worker = snap.workers.get(c.worker_id)
        out.append(
            ConnectionOut(
                id=c.id,
                service=account.service,
                account=account.id,
                account_label=snap.label(account),
                worker=c.worker_id,
                adapter=worker.adapter if worker else None,
                status=c.status,
                discovery_method=c.discovery_method,
                verified_at=c.verified_at,
                expires_at=c.expires_at,
                capabilities=[
                    CapabilityOut(
                        operation=cap.operation,
                        effect=cap.permission_level,
                        resource_scope=cap.resource_scope,
                        evidence=cap.evidence,
                        checked_at=cap.checked_at,
                    )
                    for cap in c.capabilities
                ],
            )
        )
    return out


@integrations_app.command("list")
def integrations_list(workspace: WorkspaceOpt = None, json_out: JsonFlag = False) -> None:
    """Show known connections, their owning worker, operations and verification status. No secrets."""
    project = _project()
    ws = _ws(project, workspace)
    out = IntegrationsOut(workspace=ws, connections=_connections_out(project.integrations.snapshot(ws)))

    def show(o: IntegrationsOut) -> None:
        if not o.connections:
            typer.echo(f"No integrations registered in workspace '{o.workspace}'.")
            typer.echo("Saarathi does not detect connections it cannot see; use `saarathi integrations register`.")
        for c in o.connections:
            typer.echo(
                f"{c.id}  {c.service} account '{c.account_label}' ({c.account})  worker {c.worker} ({c.adapter})"
            )
            typer.echo(f"     status={c.status} discovery={c.discovery_method} verified_at={c.verified_at or 'never'}")
            for cap in c.capabilities:
                typer.echo(f"     {cap.operation:<20} {cap.effect:<8} {cap.evidence}")

    emit(out, json_out, show)


@integrations_app.command("accounts")
def integrations_accounts(workspace: WorkspaceOpt = None, json_out: JsonFlag = False) -> None:
    """List accounts with scoped aliases, masked identities, connection owners and identity status."""
    project = _project()
    ws = _ws(project, workspace)
    snap = project.integrations.snapshot(ws)
    accounts = [
        AccountOut(
            id=a.id,
            service=a.service,
            aliases=snap.aliases_of(a.id),
            identity=a.identity_hint,
            identity_status=a.identity_status,
            tenant=a.tenant,
            is_default=snap.defaults.get(a.service) == a.id,
            connections=[f"{c.id}@{c.worker_id}" for c in snap.connections if c.account_id == a.id],
        )
        for a in sorted(snap.accounts.values(), key=lambda a: (a.service, a.id))
    ]

    def show(o: AccountsOut) -> None:
        if not o.accounts:
            typer.echo(f"No accounts registered in workspace '{o.workspace}'.")
        for a in o.accounts:
            default = " [default]" if a.is_default else ""
            aliases = ",".join(a.aliases) or "-"
            typer.echo(
                f"{a.id}  {a.service}{default}  aliases={aliases}  identity={a.identity or '-'} ({a.identity_status})"
            )
            typer.echo(f"     tenant={a.tenant or '-'} connections={', '.join(a.connections) or '-'}")

    emit(AccountsOut(workspace=ws, accounts=accounts), json_out, show)


@integrations_app.command("scan")
def integrations_scan(workspace: WorkspaceOpt = None, json_out: JsonFlag = False) -> None:
    """Refresh integration inventory through documented provider interfaces only."""
    project = _project()
    ws = _ws(project, workspace)
    snap = project.integrations.snapshot(ws)
    entries = []
    for name, adapter in project.registry.adapters.items():
        report = discovery_for(adapter.type_name).scan(name)
        workers = {w.id for w in snap.workers.values() if w.adapter == name}
        entries.append(
            ScanEntryOut(
                adapter=name,
                status=report.status,
                detail=report.detail,
                notes=list(report.notes),
                registered_connections=[c.id for c in snap.connections if c.worker_id in workers],
            )
        )

    def show(o: ScanOut) -> None:
        for e in o.adapters:
            typer.echo(f"{e.adapter}: {e.status} - {e.detail}")
            for n in e.notes:
                typer.echo(f"     note: {n}")
            registered = ", ".join(e.registered_connections) or "none"
            typer.echo(f"     registered connections (not detected): {registered}")

    emit(ScanOut(workspace=ws, adapters=entries), json_out, show)


@integrations_app.command("register")
def integrations_register(
    adapter: Annotated[str, typer.Option("--adapter", help="Provider adapter that owns the connection.")],
    service: Annotated[str, typer.Option("--service", help="Canonical service id, e.g. gmail.")],
    operation: Annotated[list[str], typer.Option("--operation", "-o", help="Permitted operation (repeatable).")],
    account: Annotated[
        str | None, typer.Option("--account", help="Existing account id, or the id for a new one.")
    ] = None,
    identity: Annotated[
        str | None, typer.Option("--identity", help="Account identity you declare (stored masked and hashed).")
    ] = None,
    alias: Annotated[str | None, typer.Option("--alias", help="Workspace-scoped alias for the account.")] = None,
    tenant: Annotated[str | None, typer.Option("--tenant", help="Tenant boundary of the account.")] = None,
    worker: Annotated[str | None, typer.Option("--worker", help="Worker id (default: the adapter name).")] = None,
    connection_id: Annotated[str | None, typer.Option("--connection-id")] = None,
    workspace: WorkspaceOpt = None,
    json_out: JsonFlag = False,
) -> None:
    """Declare a connection by hand. It stays unverified until `integrations verify` confirms it."""
    project = _project()
    ws = _ws(project, workspace)
    try:
        if adapter not in project.registry.adapters:
            raise SaarathiError(f"adapter '{adapter}' is not configured", hint="see providers in policies.yaml")
        reg = project.integrations
        runtime = project.registry.adapters[adapter].type_name
        w = reg.register_worker(ws, worker_id=worker or adapter, adapter=adapter, runtime=runtime)
        acct = reg.register_account(ws, service=service, account_id=account, identity=identity, tenant=tenant)
        if alias:
            reg.set_alias(ws, acct.id, alias)
        conn = reg.register_connection(
            ws, account_id=acct.id, worker_id=w.id, operations=operation, connection_id=connection_id
        )
    except SaarathiError as exc:
        _fail(exc)
        raise
    snap = reg.snapshot(ws)
    out = next(c for c in _connections_out(snap) if c.id == conn.id)
    emit(
        out,
        json_out,
        lambda c: typer.echo(
            f"Registered {c.id}: {c.service} account '{c.account_label}' ({c.account}) on worker {c.worker}.\n"
            f"It is declared, not verified. Confirm it with `saarathi integrations verify {c.id} --confirm`."
        ),
    )


@integrations_app.command("verify")
def integrations_verify(
    connection_id: str,
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Record that you confirmed this connection works (not provider verification)."),
    ] = False,
    workspace: WorkspaceOpt = None,
    json_out: JsonFlag = False,
) -> None:
    """Run a supported safe probe, or record explicit user confirmation, or report that neither is available."""
    project = _project()
    ws = _ws(project, workspace)
    reg = project.integrations
    snap = reg.snapshot(ws)
    conn = next((c for c in snap.connections if c.id == connection_id), None)
    if conn is None:
        _fail(SaarathiError(f"connection '{connection_id}' not found in workspace '{ws}'"))
        raise AssertionError
    worker = snap.workers.get(conn.worker_id)
    adapter = project.registry.adapters.get(worker.adapter) if worker else None
    if confirm:
        reg.record_verification(ws, connection_id, evidence=Evidence.USER_CONFIRMED, method="user_confirmation")
        out = VerifyOut(
            connection=connection_id,
            status="user_confirmed",
            evidence=Evidence.USER_CONFIRMED,
            detail="recorded your confirmation; this is not provider verification",
        )
    else:
        discovery = discovery_for(adapter.type_name if adapter else "unknown")
        outcomes = [discovery.probe(conn, cap.operation) for cap in conn.capabilities]
        if outcomes and all(o.status == "verified" for o in outcomes):
            reg.record_verification(ws, connection_id, evidence=Evidence.PROBED, method="probe")
            out = VerifyOut(
                connection=connection_id, status="verified", evidence=Evidence.PROBED, detail="probe passed"
            )
        else:
            status = "failed" if any(o.status == "failed" for o in outcomes) else "unavailable"
            evidence = conn.capabilities[0].evidence if conn.capabilities else Evidence.DECLARED
            detail = "; ".join(o.detail for o in outcomes) or "no operations to probe"
            out = VerifyOut(connection=connection_id, status=status, evidence=evidence, detail=detail)
    emit(out, json_out, lambda o: typer.echo(f"{o.connection}: {o.status} ({o.evidence}) - {o.detail}"))
    if out.status in ("failed", "unavailable"):
        raise typer.Exit(ExitCode.AWAITING_INPUT)


@integrations_app.command("revoke")
def integrations_revoke(
    connection_id: str,
    stale: Annotated[
        bool, typer.Option("--stale", help="Mark stale (needs re-verification) instead of revoked.")
    ] = False,
    workspace: WorkspaceOpt = None,
) -> None:
    """Invalidate a connection. Tasks that need it pause; Saarathi never reroutes to another account."""
    project = _project()
    status = ConnectionStatus.STALE if stale else ConnectionStatus.REVOKED
    try:
        project.integrations.set_connection_status(_ws(project, workspace), connection_id, status, reason="user")
    except SaarathiError as exc:
        _fail(exc)
    typer.echo(f"{connection_id} is now {status}.")


@integrations_app.command("alias")
def integrations_alias(account_id: str, alias: str, workspace: WorkspaceOpt = None) -> None:
    """Set a workspace-scoped alias (e.g. personal, business) for an account."""
    project = _project()
    try:
        project.integrations.set_alias(_ws(project, workspace), account_id, alias)
    except SaarathiError as exc:
        _fail(exc)
    typer.echo(f"Alias '{alias}' -> {account_id} in workspace '{_ws(project, workspace)}'.")


@integrations_app.command("default")
def integrations_default(service: str, account_id: str, workspace: WorkspaceOpt = None) -> None:
    """Set the scoped default account for a service, or clear it with `none`."""
    project = _project()
    target = None if account_id.lower() == "none" else account_id
    try:
        project.integrations.set_default(_ws(project, workspace), service, target)
    except SaarathiError as exc:
        _fail(exc)
    typer.echo(f"Default {service} account in workspace '{_ws(project, workspace)}': {target or 'none'}.")


def _latest_decision(project: Project, run_id: str, task_id: str) -> RoutingDecision:
    decisions = project.store.decisions(run_id, task_id)
    if not decisions:
        raise SaarathiError(f"no routing decisions recorded for {task_id}")
    return decisions[-1]


@integrations_app.command("approve")
def integrations_approve(task_id: str, key: str, run_id: Annotated[str | None, typer.Option("--run")] = None) -> None:
    """Grant one approval the router asked for (see `saarathi route explain <task>`), for this run only."""
    project = _project()
    try:
        rid = _run_id(project, run_id)
        trace = _latest_decision(project, rid, task_id).integration
        pending = {a.key for a in trace.approvals_required} if trace else set()
        if key not in pending:
            raise SaarathiError(
                f"'{key}' is not an approval pending for {task_id}",
                hint=f"pending: {', '.join(sorted(pending)) or 'none'}",
            )
        project.store.set_control(rid, f"{GRANT_PREFIX}{key}", "user", actor="user")
    except SaarathiError as exc:
        _fail(exc)
    typer.echo(f"Approved {key}. Continue with `saarathi resume`.")


@integrations_app.command("select")
def integrations_select(
    task_id: str,
    account_id: str,
    requirement: Annotated[int, typer.Option("--requirement", help="Requirement index in the task.")] = 0,
    run_id: Annotated[str | None, typer.Option("--run")] = None,
) -> None:
    """Choose the account for an ambiguous requirement of one task, in this run only."""
    project = _project()
    try:
        rid = _run_id(project, run_id)
        task = project.store.get_task(rid, task_id)
        if not 0 <= requirement < len(task.packet.integrations):
            raise SaarathiError(f"{task_id} has no integration requirement {requirement}")
        req = task.packet.integrations[requirement]
        if req.account is not None:
            raise SaarathiError(f"requirement {requirement} names its account explicitly; it cannot be changed")
        account = project.integrations.snapshot(project.workspace).accounts.get(account_id)
        if account is None or account.service != req.service:
            raise SaarathiError(f"'{account_id}' is not a {req.service} account in workspace '{project.workspace}'")
        project.store.set_control(rid, f"{SELECT_PREFIX}{task_id}:{requirement}", account_id, actor="user")
    except SaarathiError as exc:
        _fail(exc)
    typer.echo(f"{task_id} requirement {requirement} will use {account_id}. Continue with `saarathi resume`.")


@app.command()
def bridge(
    stdio: Annotated[bool, typer.Option("--stdio", help="Speak JSON-RPC 2.0 on stdin/stdout (required).")] = False,
) -> None:
    """Run the desktop bridge (protocol 1). Used by Saarathi Desktop; not meant for interactive use."""
    if not stdio:
        _fail(SaarathiError("the bridge only supports --stdio", hint="saarathi bridge --stdio"))
    from saarathi.bridge.server import serve_stdio

    raise typer.Exit(serve_stdio())


def main() -> None:  # pragma: no cover
    app()
