"""Bridge methods (protocol 1). Each one calls the engine as a library; none decides routing or transitions itself."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import platform
import shlex
import shutil
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from saarathi import __version__, user_settings
from saarathi.bridge.drivers import (
    INTERRUPT_CONTROL,
    driver_log,
    driver_pid,
    interrupt_driver,
    start_driver,
    wait_for_exit,
)
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
from saarathi.core.policy import ApprovalPreset
from saarathi.core.states import RUN_TERMINAL, RunState
from saarathi.git.workspace import GitError, GitRepo, data_dir
from saarathi.orchestration.engine import Engine
from saarathi.orchestration.preferences import PRESET_LABELS, RunPreferences
from saarathi.orchestration.project import META, Project
from saarathi.reporting.usage import usage_summary
from saarathi.routing.router import resolve_lead
from saarathi.security.redaction import Redactor
from saarathi.storage.store import utcnow

PROTOCOL = 1
CANCEL_WAIT_SECONDS = 30.0
LIMITS_TTL_SECONDS = 60.0
# Provider CLIs Saarathi has adapters for: adapter type -> (binary, provider).
KNOWN_CLIS = {
    "claude-code": ("claude", "anthropic"),
    "codex-cli": ("codex", "openai"),
    "antigravity-cli": ("agy", "google"),
}


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
        self._policy_stamp: dict[str, float] = {}  # policies.yaml mtime each cached project was loaded with
        self._limits: dict[str, tuple[float, list[dict[str, Any]]]] = {}  # project root -> (read at, limits)
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
            "sessions.set_preferences": self.sessions_set_preferences,
            "runs.status": self.runs_status,
            "runs.events": self.runs_events,
            "runs.approve": self.runs_approve,
            "runs.answer": self.runs_answer,
            "runs.stop": self.runs_stop,
            "runs.cancel": self.runs_cancel,
            "runs.resume": self.runs_resume,
            "tasks.diff": self.tasks_diff,
            "tasks.logs": self.tasks_logs,
            "tasks.inspect": self.tasks_inspect,
            "tasks.file": self.tasks_file,
            "runs.timeline": self.runs_timeline,
            "models.list": self.models_list,
            "models.set_enabled": self.models_set_enabled,
            "providers.status": self.providers_status,
            "policy.get": self.policy_get,
            "usage.summary": self.usage_summary,
            "usage.limits": self.usage_limits,
        }

    def project(self, path: str) -> Project:
        root = str(Path(path).expanduser().resolve())
        try:
            stamp = (Path(root) / META / "policies.yaml").stat().st_mtime
        except OSError:
            stamp = 0.0
        if root in self._projects and self._policy_stamp.get(root) != stamp:
            self._projects.pop(root).store.close()  # the policy changed on disk: reload it
        if root not in self._projects:
            try:
                self._projects[root] = Project.load(Path(root))
                self._policy_stamp[root] = stamp
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

    def sessions_set_preferences(
        self,
        project: str,
        session: str,
        approvals: str = "ask",
        model: str | None = None,
        model_mode: str = "prefer",
    ) -> Any:
        """Store the session's approval preset and worker model for its next runs, after the policy accepts them."""
        p, sessions = self._sessions(project)
        prefs = RunPreferences.parse(approvals, model, model_mode)
        prefs.validate(p.policy, p.registry.models())
        stored = {"approvals": prefs.approvals.value, "model": prefs.model, "model_mode": prefs.model_mode.value}
        return jsonable(sessions.set_preferences(session, stored))

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
        # JSON objects carry no reliable key order across the Rust shell, so the run order is explicit.
        return {"session": jsonable(meta), "conversation": conversation, "runs": snapshots, "run_order": runs}

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
        stored = sessions.get(session).preferences
        prefs = RunPreferences.parse(stored.get("approvals"), stored.get("model"), stored.get("model_mode"))
        run_id = asyncio.run(Engine(p).start(brief, prefs))
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
        active = p.store.active_run()
        driving = pid is not None and active is not None and active.id == run_id
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
            interrupt_driver(p, run, pid)
            deadline = time.monotonic() + CANCEL_WAIT_SECONDS
            while driver_pid(p) is not None and time.monotonic() < deadline:
                time.sleep(0.2)
            if driver_pid(p) is not None:
                raise BridgeError("locked", "the driver did not stop in time", hint="try again in a moment")
        with p.engine_lock():
            p.store.pop_control(run, INTERRUPT_CONTROL)
            p.store.transition_run(run, RunState.CANCELLED, actor="user", reason="cancelled from the desktop app")
        return {"state": p.store.get_run(run).state}

    def runs_resume(self, project: str, run: str, accept_defaults: bool = False) -> dict[str, Any]:
        p = self.project(project)
        if p.store.get_run(run).state in RUN_TERMINAL:
            raise BridgeError("invalid_state", f"run is {p.store.get_run(run).state}")
        return {"driver_pid": start_driver(p, run, accept_defaults=accept_defaults)}

    # ------------------------------------------------------------------ tasks, models, providers

    def _task_change(self, p: Project, run: str, task: str) -> tuple[str, str] | None:
        """(base, commit) of the task's accepted change, or None while it has no commit."""
        commit = (p.store.get_task(run, task).result or {}).get("commit")
        attempts = p.store.attempts(run, task)
        if not commit or not attempts:
            return None
        return attempts[-1].base_commit, str(commit)

    def tasks_diff(self, project: str, run: str, task: str, max_chars: int = 200_000) -> dict[str, Any]:
        p = self.project(project)
        change = self._task_change(p, run, task)
        if change is None:
            return {"commit": None, "base": None, "diff": "", "files": []}
        base, commit = change
        diff = p.git.diff(base, commit, max_chars)
        return {"commit": commit, "base": base, "diff": diff, "files": _numstat(p, base, commit)}

    def tasks_file(self, project: str, run: str, task: str, path: str, max_chars: int = 500_000) -> dict[str, Any]:
        """Both versions of one file the task changed, for a side-by-side diff. Only paths in the task's diff."""
        p = self.project(project)
        change = self._task_change(p, run, task)
        if change is None:
            raise BridgeError("not_found", f"{task} has no committed change yet")
        base, commit = change
        entry = next((f for f in _numstat(p, base, commit) if f["path"] == path), None)
        if entry is None:
            raise BridgeError("invalid_params", f"{path} is not part of {task}'s change")
        if entry["binary"]:
            return {"path": path, "binary": True, "original": "", "modified": "", "truncated": False}

        def show(rev: str) -> str:
            try:
                return p.git.git("show", f"{rev}:{path}")
            except GitError:  # added or deleted in this change
                return ""

        original, modified = show(base), show(commit)
        truncated = len(original) > max_chars or len(modified) > max_chars
        return {
            "path": path,
            "binary": False,
            "original": original[:max_chars],
            "modified": modified[:max_chars],
            "truncated": truncated,
        }

    def tasks_inspect(self, project: str, run: str, task: str) -> dict[str, Any]:
        """Everything recorded about one task: its packet, each attempt with its assessment, each routing decision."""
        p = self.project(project)
        record = p.store.get_task(run, task)
        return {
            "task": {
                **jsonable(record.packet),
                "state": record.state,
                "attempts": record.attempts,
                "failures": record.failures,
                "branch": record.branch,
                "worktree": record.worktree,
                "blocked_reason": record.blocked_reason,
                "result": record.result,
                "updated_at": record.updated_at,
            },
            "attempts": [jsonable(a) for a in p.store.attempts(run, task)],
            "decisions": [jsonable(d) for d in p.store.decisions(run, task)],
        }

    def runs_timeline(self, project: str, run: str) -> dict[str, Any]:
        """Tasks with their dependency depth and every attempt's span: the data behind the DAG timeline."""
        p = self.project(project)
        record = p.store.get_run(run)
        tasks = p.store.tasks(run)
        deps = {t.id: list(t.packet.dependencies) for t in tasks}
        depth: dict[str, int] = {}

        def level(tid: str, seen: frozenset[str] = frozenset()) -> int:
            if tid not in depth:
                parents = [d for d in deps.get(tid, []) if d in deps and d not in seen]
                depth[tid] = 1 + max((level(d, seen | {tid}) for d in parents), default=-1)
            return depth[tid]

        return {
            "run_id": run,
            "state": record.state,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "now": utcnow(),
            "tasks": [
                {
                    "id": t.id,
                    "title": t.packet.title,
                    "state": t.state,
                    "dependencies": deps[t.id],
                    "depth": level(t.id),
                }
                for t in tasks
            ],
            "attempts": [
                {
                    "id": a.id,
                    "task_id": a.task_id,
                    "number": a.number,
                    "status": a.status,
                    "outcome": a.outcome,
                    "adapter": a.adapter,
                    "model": a.model,
                    "started_at": a.started_at,
                    "finished_at": a.finished_at,
                }
                for a in p.store.attempts(run)
            ],
        }

    def tasks_logs(self, project: str, run: str, task: str, max_chars: int = 100_000) -> dict[str, Any]:
        p = self.project(project)
        root = p.logs / run / task
        redact = Redactor()  # logs are redacted when written; this catches anything a provider wrote itself
        logs = []
        if root.exists():
            for path in sorted(root.rglob("*.log")):
                text = redact(path.read_text(encoding="utf-8", errors="replace"))
                name = path.relative_to(root).as_posix()
                logs.append({"name": name, "text": text[-max_chars:], "truncated": len(text) > max_chars})
        return {"logs": logs, "events": [jsonable(e) for e in p.store.events(run, task_id=task)]}

    def driver_log(self, project: str, run: str, max_chars: int = 50_000) -> str:
        path = driver_log(self.project(project), run)
        return path.read_text(encoding="utf-8", errors="replace")[-max_chars:] if path.exists() else ""

    def models_list(self, project: str) -> list[Any]:
        p = self.project(project)
        off = user_settings.disabled_models()
        return [
            {**jsonable(ModelOut.from_descriptor(m)), "disabled": m.candidate_id in off} for m in p.registry.models()
        ]

    def models_set_enabled(self, candidate: str, enabled: bool) -> dict[str, Any]:
        """Turn a model on or off for this person, in every project (``<data dir>/settings.json``)."""
        return {"disabled": sorted(user_settings.set_model_enabled(candidate, enabled))}

    def policy_get(self, project: str) -> dict[str, Any]:
        """The effective policy and how each worker is contained, for the permission review screen. Read-only:
        the policy is changed by editing .saarathi/policies.yaml."""
        p = self.project(project)
        pol = p.policy
        cap = pol.approvals.max_preset
        workers = []
        for name, adapter in p.registry.adapters.items():
            cfg = pol.providers[name]
            command = adapter.worker_command(
                allow_network=pol.permissions.allow_network, allowed_commands=["<a verification command>"]
            )
            workers.append(
                {
                    "adapter": name,
                    "provider": adapter.provider,
                    "enabled": cfg.enabled,
                    "models": [m.id for m in cfg.models],
                    "isolation": adapter.isolation,
                    "worker_command": shlex.join(command) if command else None,
                }
            )
        configured = {cfg.type or name for name, cfg in pol.providers.items()} | set(pol.providers)
        missing = []
        for adapter_type, (binary, provider) in KNOWN_CLIS.items():
            found = shutil.which(binary)
            if adapter_type in configured or found is None:
                continue
            steps = [f"add `{adapter_type}:` under `providers:` (models are discovered)"]
            if provider not in pol.routing.allowed_providers:
                steps.append(f"add `{provider}` to routing.allowed_providers and `{provider}/*` to allowed_models")
            missing.append({"adapter": adapter_type, "provider": provider, "binary": found, "steps": steps})
        return {
            "path": str(p.meta / "policies.yaml"),
            "unconfigured_clis": missing,
            "mode": pol.mode,
            "lead": f"{pol.orchestration.lead.adapter}:{pol.orchestration.lead.model}",
            "lead_substitute": (
                f"{pol.orchestration.lead_substitute.adapter}:{pol.orchestration.lead_substitute.model}"
                if pol.orchestration.lead_substitute
                else None
            ),
            "approvals": {
                "max_preset": cap,
                "presets": [
                    {"id": pid, "label": label, "description": text, "allowed": pid.rank <= cap.rank}
                    for pid, label, text in PRESETS
                ],
                "never_automatic": list(NEVER_AUTOMATIC),
            },
            "permissions": pol.permissions.model_dump(mode="json"),
            "routing": pol.routing.model_dump(mode="json", exclude={"weights"}),
            "integrations": pol.integrations.model_dump(mode="json"),
            "execution": pol.execution.model_dump(
                mode="json",
                include={"task_timeout_seconds", "verification_timeout_seconds", "max_attempts", "env_passthrough"},
            ),
            "workers": workers,
        }

    def usage_limits(self, project: str, refresh: bool = False) -> dict[str, Any]:
        """Remaining subscription usage per enabled provider, as each CLI reports it. Read in parallel and cached
        for a minute: the CLIs take a few seconds each."""
        p = self.project(project)
        key = str(p.root)
        cached = self._limits.get(key)
        if cached and not refresh and time.monotonic() - cached[0] < LIMITS_TTL_SECONDS:
            return {"limits": cached[1], "cached": True}
        adapters = [a for name, a in p.registry.adapters.items() if p.policy.providers[name].enabled]
        with ThreadPoolExecutor(max_workers=max(1, len(adapters))) as pool:
            results = list(pool.map(lambda a: a.read_limits(), adapters))
        limits = [r.as_dict() for r in results if r is not None]
        self._limits[key] = (time.monotonic(), limits)
        return {"limits": limits, "cached": False}

    def usage_summary(self, project: str, max_runs: int = 20) -> dict[str, Any]:
        return usage_summary(self.project(project), max_runs=max_runs)

    def providers_status(self, project: str) -> dict[str, Any]:
        p = self.project(project)
        return {
            "health": [jsonable(h) for h in p.store.provider_health().values()],
            "models": self.models_list(project),
        }


PRESETS = (
    (ApprovalPreset.ASK, PRESET_LABELS["ask"], "You approve every plan and the final review."),
    (
        ApprovalPreset.AUTO_SAFE,
        PRESET_LABELS["auto_safe"],
        "A plan whose tasks are all low or medium risk and use no integrations starts on its own. You approve the"
        " final review.",
    ),
    (
        ApprovalPreset.BYPASS,
        PRESET_LABELS["bypass"],
        "Any plan that passed policy checks starts on its own, and a run completes when the lead approves and"
        " integration checks pass.",
    ),
)
NEVER_AUTOMATIC = (
    "Changes to accepted architecture decisions (escalations)",
    "Integration identity and account selections",
    "External side effects such as sending or deleting",
    "Data transfer between providers",
    "Promoting lessons into project memory",
    "Provider permission modes: no preset loosens them",
)


def _numstat(p: Project, base: str, commit: str) -> list[dict[str, Any]]:
    files = []
    for line in p.git.git("diff", "--numstat", base, commit).splitlines():
        if not line:
            continue
        added, deleted, path = line.split("\t", 2)
        binary = added == "-"
        files.append(
            {
                "path": path,
                "added": None if binary else int(added),
                "deleted": None if binary else int(deleted),
                "binary": binary,
            }
        )
    return files


def error_payload(exc: Exception) -> tuple[str, str, str | None]:
    if isinstance(exc, BridgeError):
        return exc.kind, exc.message, exc.hint
    for cls, kind in ERROR_KINDS:
        if isinstance(exc, cls):
            message = exc.message if isinstance(exc, SaarathiError) else str(exc)
            hint = exc.hint if isinstance(exc, SaarathiError) else None
            return kind, message, hint
    return "internal", f"{type(exc).__name__}: {exc}", None
