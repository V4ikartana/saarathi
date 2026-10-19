"""Bridge methods (protocol 1). Each one calls the engine as a library; none decides routing or transitions itself."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import platform
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from saarathi import __version__
from saarathi.bridge.drivers import driver_log, driver_pid, interrupt_driver, start_driver, wait_for_exit
from saarathi.bridge.sessions import SessionStore
from saarathi.bridge.timeline import render_event
from saarathi.cli.output import ModelOut
from saarathi.cli.status import build_status
from saarathi.core.errors import (
    ConfigError,
    EngineLocked,
    InvalidTransition,
    NotFound,
    PolicyViolation,
    SaarathiError,
)
from saarathi.core.states import RUN_TERMINAL, RunState
from saarathi.git.workspace import GitRepo, data_dir
from saarathi.orchestration.engine import Engine
from saarathi.orchestration.project import META, Project
from saarathi.routing.router import resolve_lead

PROTOCOL = 1
CANCEL_WAIT_SECONDS = 30.0


class BridgeError(Exception):
    """An expected failure with a stable ``kind`` the UI can act on."""

    def __init__(self, kind: str, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hint = hint


ERROR_KINDS: tuple[tuple[type[Exception], str], ...] = (
    (NotFound, "not_found"),
    (InvalidTransition, "invalid_state"),
    (PolicyViolation, "policy"),
    (EngineLocked, "locked"),
    (ConfigError, "invalid_params"),
    (SaarathiError, "invalid_state"),
    (ValueError, "invalid_params"),
)


def jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


class BridgeService:
    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._idempotent: dict[str, Any] = {}

    # ------------------------------------------------------------------ plumbing

    def methods(self) -> dict[str, Callable[..., Any]]:
        return {
            "system.initialize": self.initialize,
            "system.doctor": self.doctor,
            "projects.open": self.project_open,
            "projects.init": self.project_init,
            "sessions.list": self.sessions_list,
            "sessions.create": self.sessions_create,
            "sessions.rename": self.sessions_rename,
            "sessions.archive": self.sessions_archive,
            "sessions.restore": self.sessions_restore,
            "sessions.get": self.sessions_get,
            "sessions.send": self.sessions_send,
            "runs.status": self.runs_status,
            "runs.events": self.runs_events,
            "runs.approve": self.runs_approve,
            "runs.answer": self.runs_answer,
            "runs.stop": self.runs_stop,
            "runs.cancel": self.runs_cancel,
            "runs.resume": self.runs_resume,
            "tasks.diff": self.tasks_diff,
            "tasks.logs": self.tasks_logs,
            "models.list": self.models_list,
            "providers.status": self.providers_status,
        }

    def project(self, path: str) -> Project:
        root = str(Path(path).expanduser().resolve())
        if root not in self._projects:
            try:
                self._projects[root] = Project.load(Path(root))
            except ConfigError as exc:
                raise BridgeError(
                    "not_initialized" if "not a Saarathi project" in exc.message else "invalid_params",
                    exc.message,
                    hint=exc.hint,
                ) from exc
        return self._projects[root]

    def _remember(self, key: str | None, result: dict[str, Any]) -> dict[str, Any]:
        if key:
            self._idempotent[key] = result
        return result

    # ------------------------------------------------------------------ system

    def initialize(self, client: str = "unknown", protocol: int = PROTOCOL) -> dict[str, Any]:
        if protocol != PROTOCOL:
            raise BridgeError(
                "incompatible",
                f"client speaks protocol {protocol}; this bridge speaks {PROTOCOL}",
                hint="update Saarathi Desktop and the saarathi package together",
            )
        return {
            "protocol": PROTOCOL,
            "saarathi_version": __version__,
            "python": platform.python_version(),
            "platform": sys.platform,
            "pid": os.getpid(),
            "data_dir": str(data_dir()),
            "client": client,
        }

    def doctor(self, project: str) -> dict[str, Any]:
        p = self.project(project)
        adapters = []
        for name, adapter in p.registry.adapters.items():
            probe = adapter.probe(refresh=True)
            adapters.append(
                {
                    "name": name,
                    "type": adapter.type_name,
                    "provider": probe.provider,
                    "available": probe.available,
                    "version": probe.version,
                    "auth_method": probe.auth_method,
                    "metered": probe.metered,
                    "reason": probe.reason,
                    "test_only": probe.test_only,
                    "checks": [dataclasses.asdict(c) for c in probe.checks],
                    "notices": probe.notices,
                }
            )
        lead, problems = resolve_lead(p.registry.models(), p.policy)
        return {"adapters": adapters, "lead": lead.candidate_id if lead else None, "lead_problems": problems}

    # ------------------------------------------------------------------ projects

    def project_open(self, path: str) -> dict[str, Any]:
        repo = GitRepo(Path(path).expanduser())
        if not repo.is_repo():
            raise BridgeError("not_a_repo", f"{path} is not inside a Git repository", hint="choose a Git repository")
        root = repo.toplevel()
        initialized = (root / META / "project.yaml").exists()
        info: dict[str, Any] = {"root": str(root), "name": root.name, "initialized": initialized}
        if initialized:
            p = self.project(str(root))
            run = p.store.active_run()
            info.update(
                {
                    "project_id": p.project_id,
                    "name": p.name,
                    "mode": p.policy.mode,
                    "lead": f"{p.policy.orchestration.lead.adapter}:{p.policy.orchestration.lead.model}",
                    "active_run": run.id if run else None,
                    "driver_pid": driver_pid(p),
                }
            )
        return info

    def project_init(self, path: str) -> dict[str, Any]:
        root = Project.init(Path(path).expanduser())
        return self.project_open(str(root))

    # ------------------------------------------------------------------ sessions

    def _sessions(self, project: str) -> tuple[Project, SessionStore]:
        p = self.project(project)
        return p, SessionStore(p.store)

    def sessions_list(self, project: str, include_archived: bool = False, query: str | None = None) -> list[Any]:
        p, sessions = self._sessions(project)
        out = []
        for s in sessions.list_for(p.project_id, include_archived=include_archived, query=query):
            runs = sessions.runs(s.id)
            last = p.store.get_run(runs[-1]) if runs else None
            out.append(
                {
                    **jsonable(s),
                    "run_count": len(runs),
                    "last_run": last.id if last else None,
                    "last_state": last.state if last else None,
                }
            )
        return out

    def sessions_create(self, project: str, title: str = "New session") -> Any:
        p, sessions = self._sessions(project)
        return jsonable(sessions.create(p.project_id, title))

    def sessions_rename(self, project: str, session: str, title: str) -> Any:
        return jsonable(self._sessions(project)[1].rename(session, title))

    def sessions_archive(self, project: str, session: str) -> Any:
        return jsonable(self._sessions(project)[1].set_status(session, "archived"))

    def sessions_restore(self, project: str, session: str) -> Any:
        return jsonable(self._sessions(project)[1].set_status(session, "active"))

    def sessions_get(self, project: str, session: str) -> dict[str, Any]:
        p, sessions = self._sessions(project)
        meta = sessions.get(session)
        conversation: list[dict[str, Any]] = []
        for item in sessions.items(session):
            conversation.append(
                {
                    "id": item.id,
                    "kind": item.kind,
                    "role": item.role,
                    "ts": item.created_at,
                    "run_id": item.run_id,
                    "title": item.body.get("text", ""),
                    "data": item.body,
                }
            )
        runs = sessions.runs(session)
        snapshots = {}
        for run_id in runs:
            run = p.store.get_run(run_id)
            for event in p.store.events(run_id):
                rendered = render_event(event, run)
                if rendered:
                    conversation.append({**rendered, "role": "orchestrator"})
            snapshots[run_id] = self._snapshot(p, run_id)
        conversation.sort(key=lambda e: (e["ts"], e.get("seq", 0)))
        return {"session": jsonable(meta), "conversation": conversation, "runs": snapshots}

    def sessions_send(
        self, project: str, session: str, text: str, idempotency_key: str | None = None, accept_defaults: bool = False
    ) -> dict[str, Any]:
        """Start a run in this session with ``text`` as its brief."""
        if idempotency_key and idempotency_key in self._idempotent:
            return dict(self._idempotent[idempotency_key])
        if not text.strip():
            raise BridgeError("invalid_params", "the request is empty")
        p, sessions = self._sessions(project)
        sessions.get(session)
        active = p.store.active_run()
        if active is not None:
            owner = sessions.session_of_run(active.id)
            raise BridgeError(
                "project_busy",
                f"run {active.id} is still {active.state} in this project",
                hint=f"finish, stop or cancel it first (session {owner or 'outside the desktop'})",
            )
        item = sessions.add_item(
            session, kind="message", role="user", body={"text": text}, idempotency_key=idempotency_key
        )
        if item.run_id:  # a replay of a request that already started a run
            return self._remember(idempotency_key, {"item": item.id, "run_id": item.run_id})
        brief_dir = data_dir() / "briefs" / p.project_id
        brief_dir.mkdir(parents=True, exist_ok=True)
        brief = brief_dir / f"{item.id}.md"
        brief.write_text(text, encoding="utf-8")
        run_id = asyncio.run(Engine(p).start(brief))
        sessions.link_run(item.id, run_id)
        pid = start_driver(p, run_id, accept_defaults=accept_defaults)
        return self._remember(idempotency_key, {"item": item.id, "run_id": run_id, "driver_pid": pid})

    # ------------------------------------------------------------------ runs

    def _snapshot(self, p: Project, run_id: str) -> dict[str, Any]:
        run = p.store.get_run(run_id)
        status = jsonable(build_status(p, run_id))
        engine = Engine(p)
        pending = None
        if run.state is RunState.AWAITING_PLAN_APPROVAL:
            pending = "plan"
        elif run.state is RunState.AWAITING_REVIEW:
            pending = "review"
        elif run.state is RunState.PAUSED and engine.pending_approval(run_id):
            pending = "escalation"
        pid = driver_pid(p)
        driving = pid is not None and p.store.active_run() is not None and p.store.active_run().id == run_id  # type: ignore[union-attr]
        return {
            **status,
            "pending_approval": pending,
            "questions": p.store.questions(run_id),
            "plan": run.plan,
            "review": run.review,
            "integration": run.integration,
            "lead": run.lead,
            "driver": {"running": driving, "pid": pid if driving else None},
            "terminal": run.state in RUN_TERMINAL,
        }

    def runs_status(self, project: str, run: str) -> dict[str, Any]:
        return self._snapshot(self.project(project), run)

    def runs_events(self, project: str, run: str, since_seq: int = 0, limit: int = 500) -> list[Any]:
        p = self.project(project)
        return [jsonable(e) for e in p.store.events(run, since_seq=since_seq)[:limit]]

    def _ensure_not_driving(self, p: Project, run_id: str) -> None:
        if wait_for_exit(p) is not None:
            raise BridgeError("locked", "an engine is currently driving this project", hint="wait, or stop the run")

    def runs_approve(self, project: str, run: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Approve whatever the engine is waiting on: the plan, an escalation's ADR change or the final review."""
        if idempotency_key and idempotency_key in self._idempotent:
            return dict(self._idempotent[idempotency_key])
        p = self.project(project)
        self._ensure_not_driving(p, run)
        engine = Engine(p)
        state = p.store.get_run(run).state
        pid: int | None
        if state is RunState.AWAITING_PLAN_APPROVAL:
            engine.approve_plan(run)
            approved, pid = "plan", start_driver(p, run)
        elif state is RunState.PAUSED and engine.pending_approval(run):
            engine.approve_escalation(run)
            approved = "escalation"
            pid = start_driver(p, run) if p.store.get_run(run).state is not RunState.PAUSED else None
        elif state is RunState.AWAITING_REVIEW:
            report = engine.approve_review(run)
            return self._remember(
                idempotency_key, {"approved": "review", "report": str(report), "state": p.store.get_run(run).state}
            )
        else:
            raise BridgeError("invalid_state", f"nothing to approve: run is {state}")
        return self._remember(
            idempotency_key, {"approved": approved, "driver_pid": pid, "state": p.store.get_run(run).state}
        )

    def runs_answer(
        self, project: str, run: str, question: str, text: str, accept_defaults: bool = False
    ) -> dict[str, Any]:
        p = self.project(project)
        p.store.answer_question(run, question, text)
        sessions = SessionStore(p.store)
        owner = sessions.session_of_run(run)
        if owner:
            sessions.add_item(owner, kind="message", role="user", body={"text": text, "answers": question}, run_id=run)
        open_questions = [q for q in p.store.questions(run) if q["answer"] is None]
        pid = None
        if not open_questions and p.store.get_run(run).state is RunState.CLARIFYING:
            pid = start_driver(p, run, accept_defaults=accept_defaults)
        return {"remaining": len(open_questions), "driver_pid": pid}

    def runs_stop(self, project: str, run: str) -> dict[str, Any]:
        """Graceful: no new attempts are dispatched; in-flight ones finish; the run then pauses."""
        p = self.project(project)
        p.store.set_control(run, "pause_requested", "1", actor="user")
        return {"requested": True, "driving": driver_pid(p) is not None}

    def runs_cancel(self, project: str, run: str) -> dict[str, Any]:
        """Interrupt the driver (attempts are cancelled and the run pauses), then mark the run cancelled."""
        p = self.project(project)
        if p.store.get_run(run).state in RUN_TERMINAL:
            raise BridgeError("invalid_state", f"run is already {p.store.get_run(run).state}")
        pid = driver_pid(p)
        if pid is not None:
            interrupt_driver(pid)
            deadline = time.monotonic() + CANCEL_WAIT_SECONDS
            while driver_pid(p) is not None and time.monotonic() < deadline:
                time.sleep(0.2)
            if driver_pid(p) is not None:
                raise BridgeError("locked", "the driver did not stop in time", hint="try again in a moment")
        with p.engine_lock():
            p.store.transition_run(run, RunState.CANCELLED, actor="user", reason="cancelled from the desktop app")
        return {"state": p.store.get_run(run).state}

    def runs_resume(self, project: str, run: str, accept_defaults: bool = False) -> dict[str, Any]:
        p = self.project(project)
        if p.store.get_run(run).state in RUN_TERMINAL:
            raise BridgeError("invalid_state", f"run is {p.store.get_run(run).state}")
        return {"driver_pid": start_driver(p, run, accept_defaults=accept_defaults)}

    # ------------------------------------------------------------------ tasks, models, providers

    def tasks_diff(self, project: str, run: str, task: str, max_chars: int = 200_000) -> dict[str, Any]:
        p = self.project(project)
        record = p.store.get_task(run, task)
        commit = (record.result or {}).get("commit")
        attempts = p.store.attempts(run, task)
        if not commit or not attempts:
            return {"commit": None, "base": None, "diff": "", "files": []}
        base = attempts[-1].base_commit
        files = p.git.git("diff", "--numstat", base, commit).splitlines()
        stats = [{"added": a, "deleted": d, "path": f} for a, d, f in (line.split("\t", 2) for line in files if line)]
        return {"commit": commit, "base": base, "diff": p.git.diff(base, commit, max_chars), "files": stats}

    def tasks_logs(self, project: str, run: str, task: str, max_chars: int = 100_000) -> dict[str, Any]:
        p = self.project(project)
        root = p.logs / run / task
        logs = []
        if root.exists():
            for path in sorted(root.rglob("*.log")):
                text = path.read_text(encoding="utf-8", errors="replace")
                logs.append(
                    {"name": str(path.relative_to(root)), "text": text[-max_chars:], "truncated": len(text) > max_chars}
                )
        return {"logs": logs, "events": [jsonable(e) for e in p.store.events(run, task_id=task)]}

    def driver_log(self, project: str, run: str, max_chars: int = 50_000) -> str:
        path = driver_log(self.project(project), run)
        return path.read_text(encoding="utf-8", errors="replace")[-max_chars:] if path.exists() else ""

    def models_list(self, project: str) -> list[Any]:
        p = self.project(project)
        return [jsonable(ModelOut.from_descriptor(m)) for m in p.registry.models()]

    def providers_status(self, project: str) -> dict[str, Any]:
        p = self.project(project)
        return {
            "health": [jsonable(h) for h in p.store.provider_health().values()],
            "models": self.models_list(project),
        }


def error_payload(exc: Exception) -> tuple[str, str, str | None]:
    if isinstance(exc, BridgeError):
        return exc.kind, exc.message, exc.hint
    for cls, kind in ERROR_KINDS:
        if isinstance(exc, cls):
            message = exc.message if isinstance(exc, SaarathiError) else str(exc)
            hint = exc.hint if isinstance(exc, SaarathiError) else None
            return kind, message, hint
    return "internal", f"{type(exc).__name__}: {exc}", None
