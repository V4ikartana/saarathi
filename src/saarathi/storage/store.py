"""Durable state: projections plus an append-only event log, written atomically together."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from saarathi.core.contracts import ClarificationQuestion, RoutingDecision, TaskPacket
from saarathi.core.errors import NotFound
from saarathi.core.states import (
    RUN_TERMINAL,
    RunState,
    TaskState,
    check_run_transition,
    check_task_transition,
)
from saarathi.storage.db import connect

EVENT_SCHEMA_VERSION = 1


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class RunRecord:
    id: str
    project_id: str
    state: RunState
    paused_from: RunState | None
    pause_reason: str | None
    brief_path: str
    brief_sha256: str
    base_commit: str | None
    base_ref: str | None
    integration_branch: str | None
    intake: dict[str, Any] | None
    plan: dict[str, Any] | None
    integration: dict[str, Any] | None
    review: dict[str, Any] | None
    lead: dict[str, Any] | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskRecord:
    run_id: str
    id: str
    state: TaskState
    packet: TaskPacket
    attempts: int
    failures: int
    lease_owner: str | None
    lease_expires_at: str | None
    branch: str | None
    worktree: str | None
    blocked_reason: str | None
    result: dict[str, Any] | None
    updated_at: str


@dataclass(frozen=True)
class AttemptRecord:
    id: str
    run_id: str
    task_id: str
    number: int
    status: str
    adapter: str
    provider: str
    model: str
    transport: str
    routing_decision_id: str
    pid: int | None
    base_commit: str
    feedback: str | None
    outcome: str | None
    result: dict[str, Any] | None
    started_at: str
    finished_at: str | None


@dataclass(frozen=True)
class EventRecord:
    seq: int
    ts: str
    actor: str
    run_id: str | None
    task_id: str | None
    correlation_id: str | None
    type: str
    payload: dict[str, Any]


def _loads(value: str | None) -> Any:
    return None if value is None else json.loads(value)


class StateStore:
    """All durable writes go through this class. Each public mutator is one transaction."""

    def __init__(self, path: Path, *, redact: Callable[[str], str] | None = None) -> None:
        self.path = path
        self._conn: sqlite3.Connection = connect(path)
        self._redact = redact or (lambda s: s)
        self._depth = 0

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ plumbing

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self._depth:
            self._depth += 1
            try:
                yield self._conn
            finally:
                self._depth -= 1
            return
        self._conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        finally:
            self._depth = 0

    def _seen(self, key: str | None) -> bool:
        if key is None:
            return False
        row = self._conn.execute("SELECT 1 FROM events WHERE idempotency_key = ?", (key,)).fetchone()
        return row is not None

    def append_event(
        self,
        type_: str,
        payload: dict[str, Any] | None = None,
        *,
        actor: str,
        run_id: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        body = self._redact(json.dumps(payload or {}, sort_keys=True, default=str))
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO events (ts, actor, run_id, task_id, correlation_id, type, payload_json,"
                " schema_version, idempotency_key) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    utcnow(),
                    actor,
                    run_id,
                    task_id,
                    correlation_id,
                    type_,
                    body,
                    EVENT_SCHEMA_VERSION,
                    idempotency_key,
                ),
            )
            return int(cur.lastrowid or 0)

    def events(self, run_id: str, *, task_id: str | None = None, since_seq: int = 0) -> list[EventRecord]:
        sql = "SELECT * FROM events WHERE run_id = ? AND seq > ?"
        args: list[Any] = [run_id, since_seq]
        if task_id:
            sql += " AND task_id = ?"
            args.append(task_id)
        rows = self._conn.execute(sql + " ORDER BY seq", args).fetchall()
        return [
            EventRecord(
                seq=r["seq"],
                ts=r["ts"],
                actor=r["actor"],
                run_id=r["run_id"],
                task_id=r["task_id"],
                correlation_id=r["correlation_id"],
                type=r["type"],
                payload=json.loads(r["payload_json"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ runs

    def create_run(self, *, project_id: str, brief_path: str, brief_sha256: str, actor: str = "user") -> RunRecord:
        run_id = new_id("run")
        now = utcnow()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO runs (id, project_id, state, brief_path, brief_sha256, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (run_id, project_id, RunState.CREATED, brief_path, brief_sha256, now, now),
            )
            self.append_event(
                "run.created",
                {"brief_path": brief_path, "brief_sha256": brief_sha256},
                actor=actor,
                run_id=run_id,
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound(f"run {run_id} not found")
        return self._run(row)

    def latest_run(self) -> RunRecord | None:
        row = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
        return None if row is None else self._run(row)

    def active_run(self) -> RunRecord | None:
        placeholders = ",".join("?" for _ in RUN_TERMINAL)
        row = self._conn.execute(
            f"SELECT * FROM runs WHERE state NOT IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",  # noqa: S608
            tuple(RUN_TERMINAL),
        ).fetchone()
        return None if row is None else self._run(row)

    @staticmethod
    def _run(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            project_id=row["project_id"],
            state=RunState(row["state"]),
            paused_from=RunState(row["paused_from"]) if row["paused_from"] else None,
            pause_reason=row["pause_reason"],
            brief_path=row["brief_path"],
            brief_sha256=row["brief_sha256"],
            base_commit=row["base_commit"],
            base_ref=row["base_ref"],
            integration_branch=row["integration_branch"],
            intake=_loads(row["intake_json"]),
            plan=_loads(row["plan_json"]),
            integration=_loads(row["integration_json"]),
            review=_loads(row["review_json"]),
            lead=_loads(row["lead_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    _RUN_JSON_FIELDS = frozenset({"intake", "plan", "integration", "review", "lead"})
    _RUN_TEXT_FIELDS = frozenset({"base_commit", "base_ref", "integration_branch"})

    def update_run(self, run_id: str, *, actor: str, event: str | None = None, **fields: Any) -> None:
        sets: list[str] = []
        args: list[Any] = []
        for name, value in fields.items():
            if name in self._RUN_JSON_FIELDS:
                sets.append(f"{name}_json = ?")
                args.append(None if value is None else json.dumps(value, sort_keys=True))
            elif name in self._RUN_TEXT_FIELDS:
                sets.append(f"{name} = ?")
                args.append(value)
            else:
                raise ValueError(f"unknown run field {name}")
        with self.transaction() as conn:
            conn.execute(
                f"UPDATE runs SET {', '.join(sets)}, updated_at = ? WHERE id = ?",  # noqa: S608
                (*args, utcnow(), run_id),
            )
            if event:
                self.append_event(event, {"fields": sorted(fields)}, actor=actor, run_id=run_id)

    def transition_run(
        self,
        run_id: str,
        target: RunState,
        *,
        actor: str,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> RunRecord:
        with self.transaction() as conn:
            if self._seen(idempotency_key):
                return self.get_run(run_id)
            run = self.get_run(run_id)
            check_run_transition(run.state, target)
            if target is RunState.PAUSED:
                conn.execute(
                    "UPDATE runs SET state=?, paused_from=?, pause_reason=?, updated_at=? WHERE id=?",
                    (target, run.state, reason, utcnow(), run_id),
                )
            else:
                conn.execute(
                    "UPDATE runs SET state=?, paused_from=NULL, pause_reason=NULL, updated_at=? WHERE id=?",
                    (target, utcnow(), run_id),
                )
            self.append_event(
                "run.transition",
                {"from": run.state, "to": target, "reason": reason, **(payload or {})},
                actor=actor,
                run_id=run_id,
                idempotency_key=idempotency_key,
            )
        return self.get_run(run_id)

    # ------------------------------------------------------------------ questions

    def add_questions(self, run_id: str, questions: list[ClarificationQuestion], *, actor: str) -> None:
        with self.transaction() as conn:
            for q in questions:
                conn.execute(
                    "INSERT OR IGNORE INTO questions (run_id, id, question, why_it_matters, default_assumption)"
                    " VALUES (?,?,?,?,?)",
                    (run_id, q.id, q.question, q.why_it_matters, q.default_assumption),
                )
            self.append_event("questions.added", {"ids": [q.id for q in questions]}, actor=actor, run_id=run_id)

    def questions(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM questions WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def answer_question(self, run_id: str, question_id: str, answer: str, *, actor: str = "user") -> None:
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE questions SET answer = ?, answered_at = ? WHERE run_id = ? AND id = ?",
                (answer, utcnow(), run_id, question_id),
            )
            if cur.rowcount == 0:
                raise NotFound(f"question {question_id} not found in run {run_id}")
            self.append_event("question.answered", {"id": question_id}, actor=actor, run_id=run_id)

    # ------------------------------------------------------------------ tasks

    def insert_tasks(self, run_id: str, packets: list[TaskPacket], *, actor: str) -> None:
        now = utcnow()
        with self.transaction() as conn:
            for p in packets:
                conn.execute(
                    "INSERT INTO tasks (run_id, id, state, packet_json, updated_at) VALUES (?,?,?,?,?)",
                    (run_id, p.task_id, TaskState.PENDING, p.model_dump_json(), now),
                )
            self.append_event("tasks.created", {"ids": [p.task_id for p in packets]}, actor=actor, run_id=run_id)

    def get_task(self, run_id: str, task_id: str) -> TaskRecord:
        row = self._conn.execute("SELECT * FROM tasks WHERE run_id = ? AND id = ?", (run_id, task_id)).fetchone()
        if row is None:
            raise NotFound(f"task {task_id} not found in run {run_id}")
        return self._task(row)

    def tasks(self, run_id: str) -> list[TaskRecord]:
        rows = self._conn.execute("SELECT * FROM tasks WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [self._task(r) for r in rows]

    @staticmethod
    def _task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            run_id=row["run_id"],
            id=row["id"],
            state=TaskState(row["state"]),
            packet=TaskPacket.model_validate_json(row["packet_json"]),
            attempts=row["attempts"],
            failures=row["failures"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            branch=row["branch"],
            worktree=row["worktree"],
            blocked_reason=row["blocked_reason"],
            result=_loads(row["result_json"]),
            updated_at=row["updated_at"],
        )

    def transition_task(
        self,
        run_id: str,
        task_id: str,
        target: TaskState,
        *,
        actor: str,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        lease_owner: str | None = None,
        lease_expires_at: str | None = None,
        increment_failures: bool = False,
        blocked_reason: str | None = None,
        result: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> TaskRecord:
        with self.transaction() as conn:
            if self._seen(idempotency_key):
                return self.get_task(run_id, task_id)
            task = self.get_task(run_id, task_id)
            check_task_transition(task.state, target)
            holds_lease = target in (TaskState.LEASED, TaskState.RUNNING, TaskState.VERIFYING)
            conn.execute(
                "UPDATE tasks SET state=?, lease_owner=?, lease_expires_at=?, failures=failures+?,"
                " blocked_reason=?, result_json=COALESCE(?, result_json), updated_at=? WHERE run_id=? AND id=?",
                (
                    target,
                    (lease_owner or task.lease_owner) if holds_lease else None,
                    (lease_expires_at or task.lease_expires_at) if holds_lease else None,
                    1 if increment_failures else 0,
                    blocked_reason if target is TaskState.BLOCKED else None,
                    None if result is None else json.dumps(result, sort_keys=True),
                    utcnow(),
                    run_id,
                    task_id,
                ),
            )
            self.append_event(
                "task.transition",
                {"from": task.state, "to": target, "reason": reason, **(payload or {})},
                actor=actor,
                run_id=run_id,
                task_id=task_id,
                idempotency_key=idempotency_key,
            )
        return self.get_task(run_id, task_id)

    def set_task_workspace(self, run_id: str, task_id: str, *, branch: str, worktree: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET branch=?, worktree=?, updated_at=? WHERE run_id=? AND id=?",
                (branch, worktree, utcnow(), run_id, task_id),
            )

    def heartbeat(self, run_id: str, task_id: str, *, owner: str, expires_at: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET lease_expires_at=? WHERE run_id=? AND id=? AND lease_owner=?",
                (expires_at, run_id, task_id, owner),
            )

    # ------------------------------------------------------------------ attempts

    def start_attempt(
        self,
        run_id: str,
        task_id: str,
        *,
        decision: RoutingDecision,
        base_commit: str,
        feedback: str | None,
        actor: str,
    ) -> AttemptRecord:
        assert decision.adapter and decision.provider and decision.model and decision.transport
        attempt_id = new_id("att")
        with self.transaction() as conn:
            number = (
                conn.execute(
                    "SELECT COALESCE(MAX(number), 0) FROM attempts WHERE run_id=? AND task_id=?",
                    (run_id, task_id),
                ).fetchone()[0]
                + 1
            )
            conn.execute(
                "INSERT INTO attempts (id, run_id, task_id, number, status, adapter, provider, model, transport,"
                " routing_decision_id, base_commit, feedback, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    run_id,
                    task_id,
                    number,
                    "running",
                    decision.adapter,
                    decision.provider,
                    decision.model,
                    decision.transport,
                    decision.decision_id,
                    base_commit,
                    feedback,
                    utcnow(),
                ),
            )
            conn.execute("UPDATE tasks SET attempts = attempts + 1 WHERE run_id=? AND id=?", (run_id, task_id))
            self.append_event(
                "attempt.started",
                {"attempt_id": attempt_id, "number": number, "candidate": decision.chosen},
                actor=actor,
                run_id=run_id,
                task_id=task_id,
                correlation_id=attempt_id,
            )
        return self.get_attempt(attempt_id)

    def set_attempt_pid(self, attempt_id: str, pid: int) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE attempts SET pid=? WHERE id=?", (pid, attempt_id))

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        outcome: str,
        result: dict[str, Any] | None,
        actor: str,
    ) -> None:
        with self.transaction() as conn:
            att = self.get_attempt(attempt_id)
            conn.execute(
                "UPDATE attempts SET status=?, outcome=?, result_json=?, finished_at=? WHERE id=?",
                (
                    status,
                    outcome,
                    None if result is None else json.dumps(result, sort_keys=True),
                    utcnow(),
                    attempt_id,
                ),
            )
            self.append_event(
                f"attempt.{status}",
                {"attempt_id": attempt_id, "outcome": outcome},
                actor=actor,
                run_id=att.run_id,
                task_id=att.task_id,
                correlation_id=attempt_id,
            )

    def get_attempt(self, attempt_id: str) -> AttemptRecord:
        row = self._conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise NotFound(f"attempt {attempt_id} not found")
        return self._attempt(row)

    def attempts(self, run_id: str, task_id: str | None = None) -> list[AttemptRecord]:
        sql, args = "SELECT * FROM attempts WHERE run_id=?", [run_id]
        if task_id:
            sql += " AND task_id=?"
            args.append(task_id)
        return [self._attempt(r) for r in self._conn.execute(sql + " ORDER BY started_at, number", args)]

    def model_history(self) -> dict[str, tuple[int, int]]:
        """Return {candidate_id: (successes, finished_attempts)} across all runs."""
        rows = self._conn.execute(
            "SELECT adapter, model, SUM(outcome='succeeded') AS ok, COUNT(*) AS n FROM attempts"
            " WHERE status='finished' GROUP BY adapter, model"
        ).fetchall()
        return {f"{r['adapter']}:{r['model']}": (int(r["ok"] or 0), int(r["n"])) for r in rows}

    @staticmethod
    def _attempt(row: sqlite3.Row) -> AttemptRecord:
        return AttemptRecord(
            id=row["id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            number=row["number"],
            status=row["status"],
            adapter=row["adapter"],
            provider=row["provider"],
            model=row["model"],
            transport=row["transport"],
            routing_decision_id=row["routing_decision_id"],
            pid=row["pid"],
            base_commit=row["base_commit"],
            feedback=row["feedback"],
            outcome=row["outcome"],
            result=_loads(row["result_json"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    # ------------------------------------------------------------------ routing decisions

    def save_decision(self, run_id: str, decision: RoutingDecision, *, actor: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO routing_decisions (id, run_id, task_id, decision_json, created_at) VALUES (?,?,?,?,?)",
                (decision.decision_id, run_id, decision.task_id, decision.model_dump_json(), utcnow()),
            )
            self.append_event(
                "routing.decided",
                {"decision_id": decision.decision_id, "chosen": decision.chosen, "reason": decision.reason},
                actor=actor,
                run_id=run_id,
                task_id=decision.task_id,
                correlation_id=decision.decision_id,
            )

    def decisions(self, run_id: str, task_id: str) -> list[RoutingDecision]:
        rows = self._conn.execute(
            "SELECT decision_json FROM routing_decisions WHERE run_id=? AND task_id=? ORDER BY created_at, rowid",
            (run_id, task_id),
        ).fetchall()
        return [RoutingDecision.model_validate_json(r[0]) for r in rows]

    # ------------------------------------------------------------------ control flags

    def set_control(self, run_id: str, key: str, value: str, *, actor: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO control (run_id, key, value) VALUES (?,?,?)"
                " ON CONFLICT(run_id, key) DO UPDATE SET value=excluded.value",
                (run_id, key, value),
            )
            self.append_event("control.set", {"key": key, "value": value}, actor=actor, run_id=run_id)

    def get_control(self, run_id: str, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM control WHERE run_id=? AND key=?", (run_id, key)).fetchone()
        return None if row is None else str(row[0])

    def pop_control(self, run_id: str, key: str) -> str | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT value FROM control WHERE run_id=? AND key=?", (run_id, key)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM control WHERE run_id=? AND key=?", (run_id, key))
            return str(row[0])
