"""The deterministic orchestration engine (operations role in v0.1).

``drive(run_id)`` advances a run as far as it can and returns when the run is terminal or waiting on
a person (clarification, plan approval, review, pause). It is safe to call repeatedly: every step
is reconstructed from the state store and Git, never from memory of a previous process.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from saarathi.core.contracts import (
    Complexity,
    IntakeResult,
    ModelDescriptor,
    Plan,
    TaskPacket,
    TaskPermissions,
    Transport,
    WorkerReport,
    WorkerResult,
    topological_order,
)
from saarathi.core.errors import SaarathiError
from saarathi.core.states import RUN_TERMINAL, TASK_IN_FLIGHT, RunState, TaskState
from saarathi.git.workspace import scope_violations
from saarathi.orchestration import prompts
from saarathi.orchestration.lead import LeadAgent, LeadError
from saarathi.orchestration.project import Project
from saarathi.providers.base import ErrorKind, Invocation, InvocationMode
from saarathi.reporting.report import write_report
from saarathi.routing.router import RoutingRequest, RuntimeLoad, resolve_lead, route
from saarathi.runtime.process import kill_process_group, process_alive
from saarathi.storage.store import AttemptRecord, TaskRecord, utcnow
from saarathi.verification.verifier import all_passed, run_verification

ACTOR = "engine"
LEASE_TTL = timedelta(minutes=2)
HEARTBEAT_SECONDS = 20.0
RATE_LIMIT_BACKOFF_SECONDS = (30.0, 120.0, 300.0)
WORKER_CAPABILITIES = ("code_generation", "workspace_write", "structured_output")


class UserIO(Protocol):
    def info(self, message: str) -> None: ...
    def ask(self, question: str, default: str) -> str | None: ...
    def confirm(self, message: str) -> bool: ...


class SilentIO:
    """Non-interactive IO: never answers questions and never approves."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)

    def ask(self, question: str, default: str) -> str | None:
        return None

    def confirm(self, message: str) -> bool:
        return False


@dataclass
class _Running:
    task_id: str
    attempt_id: str
    candidate: str
    provider: str


def _later(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat(timespec="milliseconds")


class Engine:
    def __init__(self, project: Project, io: UserIO | None = None, *, accept_defaults: bool = False) -> None:
        self.p = project
        self.io = io or SilentIO()
        self.accept_defaults = accept_defaults
        self.owner = f"engine-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._running: dict[asyncio.Task[None], _Running] = {}

    # ================================================================== entry points

    async def start(self, brief_path: Path) -> str:
        brief = brief_path.read_text(encoding="utf-8")
        if self.p.store.active_run() is not None:
            raise SaarathiError(
                "a run is already active in this project",
                hint="use `saarathi status`, `saarathi resume` or `saarathi cancel`",
            )
        run = self.p.store.create_run(
            project_id=self.p.project_id,
            brief_path=str(brief_path.resolve()),
            brief_sha256=hashlib.sha256(brief.encode()).hexdigest(),
        )
        self.p.memory.write_new_or_same(f"tasks/{run.id}/brief.md", brief)
        return run.id

    async def drive(self, run_id: str) -> RunState:
        with self.p.engine_lock():
            while True:
                run = self.p.store.get_run(run_id)
                if run.state in RUN_TERMINAL:
                    return run.state
                step = getattr(self, f"_step_{run.state.value.lower()}")
                progressed: bool = await step(run_id)
                if not progressed:
                    return self.p.store.get_run(run_id).state

    # ================================================================== steps
    # Each step returns True if the run moved and the loop should continue.

    async def _step_created(self, run_id: str) -> bool:
        self.p.store.transition_run(run_id, RunState.INTAKE, actor=ACTOR)
        return True

    async def _step_paused(self, run_id: str) -> bool:
        return False

    async def _step_awaiting_review(self, run_id: str) -> bool:
        return False

    def _lead(self, run_id: str) -> LeadAgent | None:
        models = self.p.registry.models()
        lead_model, problems = resolve_lead(models, self.p.policy)
        if lead_model is None:
            self._pause(run_id, "engineering lead unavailable: " + "; ".join(problems))
            return None
        adapter = self.p.registry.get(lead_model.adapter)
        return LeadAgent(adapter, lead_model, self.p.policy, self.p.logs / run_id)

    def _lead_workspace(self, run_id: str, commit: str) -> Path:
        path = self.p.worktree_root(run_id) / "lead"
        if not (path / ".git").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            self.p.git.git("worktree", "add", "--detach", str(path), commit)
        return path

    def _brief(self, run_id: str) -> str:
        doc = self.p.memory.read(f"tasks/{run_id}/brief.md")
        assert doc is not None
        return doc.content

    def _summary(self, workspace: Path) -> str:
        readme = next((workspace / n for n in ("README.md", "README.rst", "README") if (workspace / n).exists()), None)
        files = self.p.git.git("ls-files", cwd=workspace).splitlines()[:400]
        return prompts.repo_summary(files, readme.read_text(errors="replace") if readme else None)

    async def _step_intake(self, run_id: str) -> bool:
        lead = self._lead(run_id)
        if lead is None:
            return False
        run = self.p.store.get_run(run_id)
        base = run.base_commit or self.p.git.head()
        if not run.base_commit:
            if self.p.git.has_uncommitted_changes():
                self.io.info("Note: uncommitted changes in your checkout are not part of the base commit.")
            self.p.store.update_run(
                run_id,
                actor=ACTOR,
                event="run.base_recorded",
                base_commit=base,
                base_ref=self.p.git.current_ref(),
            )
        workspace = self._lead_workspace(run_id, base)
        try:
            intake = await lead.intake(self._brief(run_id), self._summary(workspace), workspace)
        except LeadError as exc:
            self._pause(run_id, f"lead intake failed: {exc}")
            return False
        self.p.store.update_run(
            run_id, actor=ACTOR, event="lead.intake_recorded", intake=intake.model_dump(), lead=lead.identity
        )
        if intake.questions:
            self.p.store.add_questions(run_id, intake.questions, actor="lead")
            self.p.store.transition_run(run_id, RunState.CLARIFYING, actor=ACTOR)
        else:
            self.p.store.transition_run(run_id, RunState.PLANNING, actor=ACTOR)
        return True

    async def _step_clarifying(self, run_id: str) -> bool:
        for q in self.p.store.questions(run_id):
            if q["answer"] is not None:
                continue
            answer = self.io.ask(
                f"{q['id']}: {q['question']}\n   (why: {q['why_it_matters']})", q["default_assumption"]
            )
            if answer is None and self.accept_defaults:
                answer = f"[accepted default] {q['default_assumption']}"
            if answer is None:
                self.io.info(f'Waiting for an answer to {q["id"]}. Use `saarathi answer {q["id"]} "..."`.')
                return False
            self.p.store.answer_question(
                run_id,
                q["id"],
                answer,
                actor="user" if not answer.startswith("[accepted default]") else ACTOR,
            )
        self.p.store.transition_run(run_id, RunState.PLANNING, actor=ACTOR)
        return True

    async def _step_planning(self, run_id: str) -> bool:
        lead = self._lead(run_id)
        if lead is None:
            return False
        run = self.p.store.get_run(run_id)
        assert run.base_commit
        workspace = self._lead_workspace(run_id, run.base_commit)
        answers = [(q["question"], q["answer"]) for q in self.p.store.questions(run_id)]
        intake = IntakeResult.model_validate(run.intake or {"questions": [], "assumptions": []})
        try:
            plan = await lead.plan(
                self._brief(run_id), self._summary(workspace), answers, intake.assumptions, workspace
            )
        except LeadError as exc:
            self._pause(run_id, f"planning failed: {exc}")
            return False
        self.p.store.update_run(run_id, actor=ACTOR, event="plan.proposed", plan=plan.model_dump())
        self.p.store.transition_run(run_id, RunState.AWAITING_PLAN_APPROVAL, actor=ACTOR)
        return True

    async def _step_awaiting_plan_approval(self, run_id: str) -> bool:
        plan = Plan.model_validate(self.p.store.get_run(run_id).plan)
        self.io.info(render_plan(plan))
        if not self.io.confirm("Approve this plan and start execution?"):
            self.io.info("Plan awaiting approval. Run `saarathi approve` when ready.")
            return False
        self.approve_plan(run_id, actor="user")
        return True

    def approve_plan(self, run_id: str, *, actor: str = "user") -> None:
        run = self.p.store.get_run(run_id)
        if run.state is not RunState.AWAITING_PLAN_APPROVAL:
            raise SaarathiError(f"run is {run.state}, not awaiting plan approval")
        plan = Plan.model_validate(run.plan)
        assert run.base_commit
        accepted = self._accept_adrs(run_id, plan)
        architecture = f"# Architecture (run {run_id})\n\n{plan.summary}\n\n{plan.architecture_notes}\n"
        current = self.p.memory.read("architecture.md")
        self.p.memory.write("architecture.md", architecture, expected_version=current.version if current else None)
        packets = []
        for task in plan.tasks:
            snapshot, context = prompts.compile_task_context(plan, task, accepted)
            self.p.memory.write_new_or_same(f"tasks/{run_id}/{task.id}.context.md", context)
            packet = TaskPacket(
                task_id=task.id,
                run_id=run_id,
                title=task.title,
                objective=task.objective,
                acceptance_criteria=task.acceptance_criteria,
                dependencies=task.dependencies,
                allowed_paths=task.allowed_paths,
                context_snapshot_id=snapshot,
                required_capabilities=sorted({*task.required_capabilities, *WORKER_CAPABILITIES}),
                complexity=task.complexity,
                risk=task.risk,
                max_attempts=self.p.policy.execution.max_attempts,
                verification_commands=task.verification_commands,
                permissions=TaskPermissions(network=self.p.policy.permissions.allow_network, secrets=False),
            )
            self.p.memory.write_new_or_same(f"tasks/{run_id}/{task.id}.json", packet.model_dump_json(indent=2))
            packets.append(packet)
        with self.p.store.transaction():
            self.p.store.insert_tasks(run_id, packets, actor=actor)
            self.p.store.transition_run(
                run_id,
                RunState.EXECUTING,
                actor=actor,
                reason="plan approved",
                payload={"base_commit": run.base_commit, "adrs": [a[0] for a in accepted]},
            )

    # ------------------------------------------------------------------ execution

    def _accept_adrs(self, run_id: str, plan: Plan) -> list[tuple[str, str, str]]:
        """Record the plan's decisions as accepted ADRs exactly once per run."""
        marker = f"tasks/{run_id}/adrs.json"
        existing = self.p.memory.read(marker)
        if existing is not None:
            paths: list[str] = json.loads(existing.content)
        else:
            today = datetime.now(UTC).date().isoformat()
            paths = []
            for decision in plan.decisions:
                adr = self.p.memory.add_adr(decision.title, decision.context, decision.decision, date=today)
                self.p.memory.set_adr_status(adr.number, "Accepted", expected_version=adr.version)
                paths.append(adr.path)
            self.p.memory.write_new_or_same(marker, json.dumps(paths))
        accepted = []
        for path in paths:
            doc = self.p.memory.read(path)
            if doc is not None:
                accepted.append((path, doc.version, doc.content))
        return accepted

    async def _step_executing(self, run_id: str) -> bool:
        self._reconcile(run_id)
        self._promote(run_id)
        verify_only = [t for t in self.p.store.tasks(run_id) if t.state is TaskState.VERIFYING]
        for t in verify_only:  # recovered work whose commit already exists: verify, never re-execute
            self._spawn(run_id, t, None)
        pausing = False
        try:
            while True:
                if not pausing and self.p.store.pop_control(run_id, "pause_requested"):
                    pausing = True
                    self.io.info("Pause requested: waiting for running attempts to finish.")
                if not pausing:
                    self._promote(run_id)
                    self._dispatch(run_id)
                if not self._running:
                    break
                done, _ = await asyncio.wait(list(self._running), timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    self._running.pop(finished)
                    exc = finished.exception()
                    if exc is not None:
                        raise exc
        except BaseException:
            await self._cancel_all(run_id)
            raise
        if pausing:
            self._pause(run_id, "paused by user")
            return False
        tasks = self.p.store.tasks(run_id)
        if all(t.state is TaskState.SUCCEEDED for t in tasks):
            self.p.store.transition_run(run_id, RunState.VERIFYING, actor=ACTOR)
            return True
        waiting = [t for t in tasks if t.state is TaskState.RETRY_WAIT]
        if waiting:
            delay = min(self._retry_delay(run_id, t.id) for t in waiting)
            await asyncio.sleep(max(0.0, delay))
            return True
        stuck = [
            f"{t.id} {t.state}: {t.blocked_reason or (t.result or {}).get('failure_reason', '')}"
            for t in tasks
            if t.state is not TaskState.SUCCEEDED
        ]
        self._checkpoint(run_id, "execution-stopped", "Execution cannot continue without a decision.", stuck)
        self._pause(run_id, "tasks failed or blocked: " + "; ".join(stuck))
        return False

    def _retry_delay(self, run_id: str, task_id: str) -> float:
        raw = self.p.store.get_control(run_id, f"retry_at:{task_id}")
        if raw is None:
            return 0.0
        return (datetime.fromisoformat(raw) - datetime.now(UTC)).total_seconds()

    def _promote(self, run_id: str) -> None:
        tasks = {t.id: t for t in self.p.store.tasks(run_id)}
        for t in tasks.values():
            if t.state is TaskState.PENDING:
                deps = [tasks[d].state for d in t.packet.dependencies]
                if any(s in (TaskState.FAILED, TaskState.CANCELLED, TaskState.BLOCKED) for s in deps):
                    self.p.store.transition_task(
                        run_id,
                        t.id,
                        TaskState.BLOCKED,
                        actor=ACTOR,
                        blocked_reason="a dependency did not succeed",
                    )
                elif all(s is TaskState.SUCCEEDED for s in deps):
                    self.p.store.transition_task(run_id, t.id, TaskState.READY, actor=ACTOR)
            elif t.state is TaskState.RETRY_WAIT and self._retry_delay(run_id, t.id) <= 0:
                self.p.store.pop_control(run_id, f"retry_at:{t.id}")
                self.p.store.transition_task(run_id, t.id, TaskState.READY, actor=ACTOR, reason="retry")

    def _load(self) -> RuntimeLoad:
        load = RuntimeLoad(history=self.p.store.model_history())
        for meta in self._running.values():
            load.in_flight[meta.candidate] = load.in_flight.get(meta.candidate, 0) + 1
            load.providers_in_flight[meta.provider] = load.providers_in_flight.get(meta.provider, 0) + 1
        return load

    def _dispatch(self, run_id: str) -> None:
        models = self.p.registry.models()
        for task in self.p.store.tasks(run_id):
            if len(self._running) >= self.p.policy.routing.max_parallel_workers:
                return
            if task.state is not TaskState.READY:
                continue
            failed_before = frozenset(
                f"{a.adapter}:{a.model}" for a in self.p.store.attempts(run_id, task.id) if a.outcome == "failed"
            )
            req = RoutingRequest(
                task_id=task.id,
                required_capabilities=tuple(task.packet.required_capabilities),
                complexity=Complexity(task.packet.complexity),
                previously_failed=failed_before,
            )
            decision = route(req, models, self.p.policy, self._load())
            if decision.chosen is None and decision.busy:
                continue  # wait for capacity; not recorded to keep the log readable
            self.p.store.save_decision(run_id, decision, actor="router")
            if decision.chosen is None:
                self.p.store.transition_task(
                    run_id, task.id, TaskState.BLOCKED, actor="router", blocked_reason=decision.reason
                )
                continue
            self.p.store.transition_task(
                run_id,
                task.id,
                TaskState.LEASED,
                actor=ACTOR,
                lease_owner=self.owner,
                lease_expires_at=_later(LEASE_TTL),
                payload={"decision_id": decision.decision_id},
            )
            model = next(m for m in models if m.candidate_id == decision.chosen)
            self._spawn(run_id, self.p.store.get_task(run_id, task.id), model, decision_id=decision.decision_id)

    def _spawn(self, run_id: str, task: TaskRecord, model: ModelDescriptor | None, decision_id: str = "") -> None:
        if model is None:  # verification-only recovery of an existing attempt
            attempt = self._latest_attempt(run_id, task.id)
            assert attempt is not None
            coro = self._verify_existing(run_id, task, attempt)
            meta = _Running(task.id, attempt.id, f"{attempt.adapter}:{attempt.model}", attempt.provider)
        else:
            coro = self._attempt(run_id, task, model, decision_id)
            meta = _Running(task.id, "", model.candidate_id, model.provider)
        self._running[asyncio.ensure_future(coro)] = meta

    async def _cancel_all(self, run_id: str) -> None:
        for fut in self._running:
            fut.cancel()
        await asyncio.gather(*self._running, return_exceptions=True)
        self._running.clear()
        with contextlib.suppress(Exception):
            self._pause(run_id, "interrupted; in-flight attempts were stopped and will be retried on resume")

    def _latest_attempt(self, run_id: str, task_id: str) -> AttemptRecord | None:
        attempts = self.p.store.attempts(run_id, task_id)
        return attempts[-1] if attempts else None

    def _worktree(self, run_id: str, task_id: str) -> tuple[Path, str]:
        return self.p.worktree_root(run_id) / task_id, f"saarathi/{run_id}/{task_id}"

    async def _attempt(self, run_id: str, task: TaskRecord, model: ModelDescriptor, decision_id: str) -> None:
        store, git = self.p.store, self.p.git
        run = store.get_run(run_id)
        assert run.base_commit
        base = run.base_commit
        path, branch = self._worktree(run_id, task.id)
        git.ensure_worktree(path, branch, base)
        store.set_task_workspace(run_id, task.id, branch=branch, worktree=str(path))
        git.reset_worktree(path, base)
        previous = self._latest_attempt(run_id, task.id)
        feedback = (previous.result or {}).get("failure_reason") if previous and previous.outcome == "failed" else None
        if previous and previous.outcome == "failed" and previous.result:
            tails = [e["output_tail"][-1500:] for e in previous.result.get("verification", []) if e.get("exit_code")]
            feedback = "\n".join(filter(None, [feedback, *tails]))
        decision = store.decisions(run_id, task.id)[-1]
        attempt = store.start_attempt(
            run_id, task.id, decision=decision, base_commit=base, feedback=feedback, actor=ACTOR
        )
        store.transition_task(
            run_id,
            task.id,
            TaskState.RUNNING,
            actor=ACTOR,
            payload={"attempt_id": attempt.id, "candidate": model.candidate_id},
        )
        context_doc = self.p.memory.read(f"tasks/{run_id}/{task.id}.context.md")
        prompt = prompts.worker_prompt(task.packet, context_doc.content if context_doc else "", feedback)
        before = git.checkout_snapshot()
        adapter = self.p.registry.get(model.adapter)
        heartbeat = asyncio.ensure_future(self._heartbeat(run_id, task.id))
        try:
            result = await adapter.invoke(
                Invocation(
                    purpose="task.execute",
                    task_id=task.id,
                    mode=InvocationMode.WORKSPACE_WRITE,
                    prompt=prompt,
                    schema=prompts.WORKER_SCHEMA,
                    cwd=path,
                    model=model.id,
                    timeout_s=self.p.policy.execution.task_timeout_seconds,
                    log_dir=self.p.logs / run_id / task.id / f"attempt-{attempt.number}",
                    allowed_commands=task.packet.verification_commands,
                    allow_network=self.p.policy.permissions.allow_network,
                    env_passthrough=self.p.policy.execution.env_passthrough,
                    on_start=lambda pid: store.set_attempt_pid(attempt.id, pid),
                )
            )
        except asyncio.CancelledError:
            git.reset_worktree(path, base)
            store.finish_attempt(attempt.id, status="abandoned", outcome="cancelled", result=None, actor=ACTOR)
            store.transition_task(run_id, task.id, TaskState.RETRY_WAIT, actor=ACTOR, reason="cancelled")
            raise
        finally:
            heartbeat.cancel()

        assessment = WorkerResult(
            task_id=task.id,
            attempt_id=attempt.id,
            status="failed",
            adapter=model.adapter,
            provider=model.provider,
            model=result.usage.reported_model or model.id,
            transport=model.transport,
            usage=result.usage,
        )
        if result.structured is not None:
            with contextlib.suppress(Exception):
                assessment.worker_report = WorkerReport.model_validate(result.structured)
        assessment.checkout_modified = git.checkout_snapshot() != before
        changed = git.stage_and_list_changes(path, base)
        assessment.changed_files = changed
        assessment.scope_violations = scope_violations(changed, task.packet.allowed_paths)

        problem = None
        if not result.ok:
            problem = f"worker invocation failed ({result.error_kind}): {result.error}"
        elif assessment.worker_report is None:
            problem = "worker returned no valid report"
        elif assessment.worker_report.status != "succeeded":
            problem = f"worker reported {assessment.worker_report.status}: {assessment.worker_report.summary}"
        elif assessment.checkout_modified:
            problem = "the user's checkout changed during the attempt (possible write outside the worktree)"
            store.append_event(
                "security.checkout_modified",
                {"attempt_id": attempt.id},
                actor=ACTOR,
                run_id=run_id,
                task_id=task.id,
            )
        elif assessment.scope_violations:
            problem = f"changes outside allowed paths: {assessment.scope_violations}"
            store.append_event(
                "security.scope_violation",
                {"paths": assessment.scope_violations},
                actor=ACTOR,
                run_id=run_id,
                task_id=task.id,
            )
        elif not changed:
            problem = "worker reported success but changed no files"

        if problem:
            assessment.failure_reason = problem
            await self._fail(run_id, task.id, attempt, assessment, result.error_kind)
            return
        message = (
            f"{task.id}: {task.packet.title}\n\nSaarathi-Task: {task.id}\nSaarathi-Attempt: {attempt.id}\n"
            f"Saarathi-Worker: {model.candidate_id}"
        )
        assessment.commit = git.commit_staged(path, message)
        store.transition_task(
            run_id,
            task.id,
            TaskState.VERIFYING,
            actor=ACTOR,
            payload={"commit": assessment.commit},
            result=assessment.model_dump(mode="json"),
        )
        await self._verify(run_id, task.id, attempt, assessment, path)

    async def _verify_existing(self, run_id: str, task: TaskRecord, attempt: AttemptRecord) -> None:
        path, _ = self._worktree(run_id, task.id)
        prior = WorkerResult.model_validate(task.result) if task.result else None
        if prior is None or prior.attempt_id != attempt.id:
            prior = WorkerResult(
                task_id=task.id,
                attempt_id=attempt.id,
                status="failed",
                adapter=attempt.adapter,
                provider=attempt.provider,
                model=attempt.model,
                transport=Transport(attempt.transport),
                commit=self.p.git.head(cwd=path),
            )
        await self._verify(run_id, task.id, attempt, prior, path)

    async def _verify(
        self, run_id: str, task_id: str, attempt: AttemptRecord, assessment: WorkerResult, path: Path
    ) -> None:
        task = self.p.store.get_task(run_id, task_id)
        commands = task.packet.verification_commands
        assessment.verification = await run_verification(
            commands,
            cwd=path,
            permissions=self.p.policy.permissions,
            timeout_s=self.p.policy.execution.verification_timeout_seconds,
            log_dir=self.p.logs / run_id / task_id / f"attempt-{attempt.number}",
            env_passthrough=self.p.policy.execution.env_passthrough,
        )
        if all_passed(assessment.verification, len(commands)):
            assessment.status = "succeeded"
            self.p.store.finish_attempt(
                attempt.id,
                status="finished",
                outcome="succeeded",
                result=assessment.model_dump(mode="json"),
                actor=ACTOR,
            )
            self.p.store.transition_task(
                run_id, task_id, TaskState.SUCCEEDED, actor=ACTOR, result=assessment.model_dump(mode="json")
            )
            return
        failed = next(e for e in assessment.verification if not e.passed)
        assessment.failure_reason = (
            f"verification failed: `{failed.command}` {failed.rejected_reason or f'exited {failed.exit_code}'}"
        )
        await self._fail(run_id, task_id, attempt, assessment, ErrorKind.NONE)

    async def _fail(
        self, run_id: str, task_id: str, attempt: AttemptRecord, assessment: WorkerResult, kind: ErrorKind
    ) -> None:
        store = self.p.store
        run = store.get_run(run_id)
        assert run.base_commit
        path, _ = self._worktree(run_id, task_id)
        self.p.git.reset_worktree(path, run.base_commit)
        store.finish_attempt(
            attempt.id,
            status="finished",
            outcome="failed",
            result=assessment.model_dump(mode="json"),
            actor=ACTOR,
        )
        task = store.get_task(run_id, task_id)
        if kind in (ErrorKind.AUTH, ErrorKind.UNAVAILABLE):
            store.transition_task(
                run_id,
                task_id,
                TaskState.BLOCKED,
                actor=ACTOR,
                blocked_reason=assessment.failure_reason,
                result=assessment.model_dump(mode="json"),
            )
            return
        failures = task.failures + 1
        limit = min(task.packet.max_attempts, self.p.policy.routing.escalate_after_failures)
        if failures >= limit:
            store.transition_task(
                run_id,
                task_id,
                TaskState.FAILED,
                actor=ACTOR,
                increment_failures=True,
                reason=assessment.failure_reason,
                result=assessment.model_dump(mode="json"),
            )
            return
        store.transition_task(
            run_id,
            task_id,
            TaskState.RETRY_WAIT,
            actor=ACTOR,
            increment_failures=True,
            reason=assessment.failure_reason,
            result=assessment.model_dump(mode="json"),
        )
        if kind is ErrorKind.RATE_LIMIT:
            delay = RATE_LIMIT_BACKOFF_SECONDS[min(failures - 1, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)]
            store.set_control(run_id, f"retry_at:{task_id}", _later(timedelta(seconds=delay)), actor=ACTOR)

    async def _heartbeat(self, run_id: str, task_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            self.p.store.heartbeat(run_id, task_id, owner=self.owner, expires_at=_later(LEASE_TTL))

    def _reconcile(self, run_id: str) -> None:
        """Repair tasks left in flight by a previous engine (we hold the lock, so it is gone)."""
        store, git = self.p.store, self.p.git
        run = store.get_run(run_id)
        for task in store.tasks(run_id):
            if task.state not in TASK_IN_FLIGHT:
                continue
            attempt = self._latest_attempt(run_id, task.id)
            if task.state is TaskState.LEASED and (attempt is None or attempt.status != "running"):
                store.transition_task(run_id, task.id, TaskState.READY, actor=ACTOR, reason="orphaned lease released")
                continue
            assert attempt is not None and run.base_commit
            if attempt.pid and attempt.pid != os.getpid() and process_alive(attempt.pid):
                kill_process_group(attempt.pid, signal.SIGTERM)
                store.append_event(
                    "recovery.killed_orphan",
                    {"pid": attempt.pid},
                    actor=ACTOR,
                    run_id=run_id,
                    task_id=task.id,
                )
            path, _ = self._worktree(run_id, task.id)
            committed = (path / ".git").exists() and git.find_commit_with_trailer(
                path, run.base_commit, f"Saarathi-Attempt: {attempt.id}"
            )
            if committed:
                if task.state is TaskState.RUNNING:
                    store.transition_task(
                        run_id,
                        task.id,
                        TaskState.VERIFYING,
                        actor=ACTOR,
                        reason="recovered committed attempt; re-verifying",
                    )
                store.append_event(
                    "recovery.reverify",
                    {"attempt_id": attempt.id},
                    actor=ACTOR,
                    run_id=run_id,
                    task_id=task.id,
                )
                continue
            if (path / ".git").exists():
                git.reset_worktree(path, run.base_commit)
            store.finish_attempt(attempt.id, status="abandoned", outcome="orphaned", result=None, actor=ACTOR)
            if task.state is TaskState.VERIFYING:
                # verifying without a commit should not happen; treat as a failed attempt to stay safe
                store.transition_task(
                    run_id,
                    task.id,
                    TaskState.RETRY_WAIT,
                    actor=ACTOR,
                    increment_failures=True,
                    reason="verification state without commit",
                )
                continue
            if task.state is TaskState.LEASED:
                store.transition_task(run_id, task.id, TaskState.RUNNING, actor=ACTOR, reason="recovery bookkeeping")
            store.transition_task(
                run_id, task.id, TaskState.RETRY_WAIT, actor=ACTOR, reason="orphaned attempt discarded"
            )

    # ------------------------------------------------------------------ integration and review

    async def _step_verifying(self, run_id: str) -> bool:
        store, git = self.p.store, self.p.git
        run = store.get_run(run_id)
        assert run.base_commit
        plan = Plan.model_validate(run.plan)
        path = self.p.worktree_root(run_id) / "integration"
        branch = f"saarathi/{run_id}/integration"
        git.ensure_worktree(path, branch, run.base_commit)
        git.reset_worktree(path, run.base_commit)
        order = topological_order(plan.tasks)
        titles = {t.id: t.title for t in plan.tasks}
        merge = git.merge_branches(
            path, [(f"saarathi/{run_id}/{tid}", f"Integrate {tid}: {titles[tid]}") for tid in order]
        )
        integration: dict[str, Any] = {
            "branch": branch,
            "worktree": str(path),
            "merged": merge.merged,
            "conflict_branch": merge.conflict_branch,
            "conflict_files": list(merge.conflict_files),
        }
        if not merge.ok:
            store.update_run(run_id, actor=ACTOR, event="integration.conflict", integration=integration)
            self._checkpoint(
                run_id,
                "integration-conflict",
                "Task branches conflict.",
                [f"{merge.conflict_branch}: {list(merge.conflict_files)}"],
            )
            self._pause(run_id, f"merge conflict integrating {merge.conflict_branch}")
            return False
        evidence = await run_verification(
            plan.integration_checks,
            cwd=path,
            permissions=self.p.policy.permissions,
            timeout_s=self.p.policy.execution.verification_timeout_seconds,
            log_dir=self.p.logs / run_id / "integration",
            env_passthrough=self.p.policy.execution.env_passthrough,
        )
        integration["head"] = git.head(cwd=path)
        integration["checks"] = [e.model_dump(mode="json") for e in evidence]
        integration["passed"] = all_passed(evidence, len(plan.integration_checks))
        store.update_run(
            run_id,
            actor=ACTOR,
            event="integration.verified",
            integration_branch=branch,
            integration=integration,
        )
        if not integration["passed"]:
            self._checkpoint(
                run_id,
                "integration-checks",
                "Integration checks failed.",
                [f"{e.command}: exit {e.exit_code} {e.rejected_reason or ''}" for e in evidence if not e.passed],
            )
            self._pause(run_id, "integration checks failed")
            return False

        lead = self._lead(run_id)
        if lead is None:
            return False
        evidence_text = (
            "\n".join(f"- integration `{e.command}` → exit {e.exit_code}" for e in evidence)
            + "\n"
            + "\n".join(
                f"- {t.id} verified by {', '.join(t.packet.verification_commands)}" for t in store.tasks(run_id)
            )
        )
        diff = git.diff(run.base_commit, integration["head"])
        try:
            verdict = await lead.review(plan, evidence_text, diff, path)
        except LeadError as exc:
            self._pause(run_id, f"lead review failed: {exc}")
            return False
        store.update_run(run_id, actor="lead", event="lead.reviewed", review=verdict.model_dump())
        store.transition_run(run_id, RunState.AWAITING_REVIEW, actor=ACTOR, payload={"lead_decision": verdict.decision})
        return True

    def approve_review(self, run_id: str, *, actor: str = "user") -> Path:
        run = self.p.store.get_run(run_id)
        if run.state is not RunState.AWAITING_REVIEW:
            raise SaarathiError(f"run is {run.state}, not awaiting review")
        self.p.store.transition_run(run_id, RunState.READY_TO_SHIP, actor=actor, reason="human approved")
        self.p.store.transition_run(run_id, RunState.COMPLETED, actor=ACTOR, reason="branch and report delivered")
        return write_report(self.p, run_id)  # regenerate any time with `saarathi report`

    # ------------------------------------------------------------------ helpers

    def _pause(self, run_id: str, reason: str) -> None:
        run = self.p.store.get_run(run_id)
        if run.state is RunState.PAUSED or run.state in RUN_TERMINAL:
            return
        if run.state.value in ("CLARIFYING", "AWAITING_PLAN_APPROVAL", "AWAITING_REVIEW"):
            self.io.info(reason)
            return
        self.p.store.transition_run(run_id, RunState.PAUSED, actor=ACTOR, reason=reason)
        self.io.info(f"Run paused: {reason}")

    def _checkpoint(self, run_id: str, kind: str, headline: str, items: list[str]) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        body = (
            f"# Checkpoint: {kind}\n\nRun: {run_id}\nTime: {utcnow()}\n\n{headline}\n\n"
            + "\n".join(f"- {i}" for i in items)
            + "\n\nResolve the issue, then run `saarathi resume`.\n"
        )
        self.p.memory.write_new_or_same(f"checkpoints/{run_id}-{stamp}-{kind}.md", body)
        self.p.store.append_event("checkpoint.created", {"kind": kind, "items": items}, actor=ACTOR, run_id=run_id)

    def resume(self, run_id: str) -> None:
        """Move a paused run back to the state it was paused from. Blocked tasks become ready again."""
        run = self.p.store.get_run(run_id)
        if run.state is RunState.PAUSED and run.paused_from is not None:
            self.p.store.transition_run(run_id, run.paused_from, actor="user", reason="resume")
            if run.paused_from is RunState.EXECUTING:
                self.retry_blocked(run_id)

    def retry_blocked(self, run_id: str) -> list[str]:
        """Return blocked tasks to READY so routing is re-evaluated (explicit user decision)."""
        revived = []
        for t in self.p.store.tasks(run_id):
            if t.state is TaskState.BLOCKED:
                self.p.store.transition_task(run_id, t.id, TaskState.READY, actor="user", reason="user retry")
                revived.append(t.id)
        return revived


def render_plan(plan: Plan) -> str:
    lines = [f"Plan: {plan.summary}", ""]
    for task in plan.tasks:
        deps = f" (after {', '.join(task.dependencies)})" if task.dependencies else ""
        lines.append(f"  {task.id} [{task.complexity}/{task.risk}] {task.title}{deps}")
        lines.append(f"      paths: {', '.join(task.allowed_paths)}")
        lines.append(f"      checks: {' ; '.join(task.verification_commands)}")
    lines.append(f"  integration checks: {' ; '.join(plan.integration_checks)}")
    for d in plan.decisions:
        lines.append(f"  decision: {d.title}")
    return "\n".join(lines)
