"""Conversation sessions for the desktop app (ADR-0013), stored in the existing state database."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from saarathi.core.errors import NotFound
from saarathi.storage.store import StateStore, new_id, utcnow


@dataclass(frozen=True)
class Session:
    id: str
    project_id: str
    title: str
    status: str  # active | archived
    preferences: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SessionItem:
    id: str
    session_id: str
    seq: int
    kind: str  # message | note
    role: str  # user | system
    body: dict[str, Any]
    run_id: str | None
    created_at: str


class SessionStore:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def create(self, project_id: str, title: str, *, actor: str = "user") -> Session:
        session_id = new_id("ses")
        now = utcnow()
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions (id, project_id, title, status, preferences_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (session_id, project_id, title.strip() or "New session", "active", "{}", now, now),
            )
            self.store.append_event("session.created", {"session_id": session_id}, actor=actor)
        return self.get(session_id)

    def get(self, session_id: str) -> Session:
        rows = self.store.query("SELECT * FROM sessions WHERE id = ?", [session_id])
        if not rows:
            raise NotFound(f"session {session_id} not found")
        return _session(rows[0])

    def list_for(self, project_id: str, *, include_archived: bool = False, query: str | None = None) -> list[Session]:
        sql = "SELECT * FROM sessions WHERE project_id = ?"
        args: list[Any] = [project_id]
        if not include_archived:
            sql += " AND status = 'active'"
        if query:
            sql += (
                " AND (title LIKE ? ESCAPE '\\' OR id IN (SELECT session_id FROM session_items"
                " WHERE body_json LIKE ? ESCAPE '\\'))"
            )
            needle = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            args += [needle, needle]
        return [_session(r) for r in self.store.query(sql + " ORDER BY updated_at DESC, rowid DESC", args)]

    def rename(self, session_id: str, title: str, *, actor: str = "user") -> Session:
        if not title.strip():
            raise ValueError("a session title cannot be empty")
        self.get(session_id)
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title.strip()[:200], utcnow(), session_id)
            )
            self.store.append_event("session.renamed", {"session_id": session_id}, actor=actor)
        return self.get(session_id)

    def set_preferences(self, session_id: str, preferences: dict[str, Any], *, actor: str = "user") -> Session:
        self.get(session_id)
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET preferences_json=?, updated_at=? WHERE id=?",
                (json.dumps(preferences, sort_keys=True), utcnow(), session_id),
            )
            self.store.append_event("session.preferences", {"session_id": session_id, **preferences}, actor=actor)
        return self.get(session_id)

    def set_status(self, session_id: str, status: str, *, actor: str = "user") -> Session:
        if status not in ("active", "archived"):
            raise ValueError(f"invalid session status {status}")
        self.get(session_id)
        with self.store.transaction() as conn:
            conn.execute("UPDATE sessions SET status=?, updated_at=? WHERE id=?", (status, utcnow(), session_id))
            self.store.append_event(f"session.{status}", {"session_id": session_id}, actor=actor)
        return self.get(session_id)

    def add_item(
        self,
        session_id: str,
        *,
        kind: str,
        role: str,
        body: dict[str, Any],
        run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> SessionItem:
        with self.store.transaction() as conn:
            if idempotency_key:
                found = conn.execute("SELECT id FROM session_items WHERE idempotency_key = ?", (idempotency_key,))
                row = found.fetchone()
                if row:
                    return self.item(str(row[0]))
            seq = int(
                conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM session_items WHERE session_id = ?", (session_id,)
                ).fetchone()[0]
            )
            item_id = new_id("itm")
            conn.execute(
                "INSERT INTO session_items (id, session_id, seq, kind, role, body_json, run_id, idempotency_key,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    session_id,
                    seq,
                    kind,
                    role,
                    json.dumps(body, sort_keys=True),
                    run_id,
                    idempotency_key,
                    utcnow(),
                ),
            )
            conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (utcnow(), session_id))
        return self.item(item_id)

    def link_run(self, item_id: str, run_id: str) -> None:
        with self.store.transaction() as conn:
            conn.execute("UPDATE session_items SET run_id=? WHERE id=?", (run_id, item_id))

    def item(self, item_id: str) -> SessionItem:
        rows = self.store.query("SELECT * FROM session_items WHERE id = ?", [item_id])
        if not rows:
            raise NotFound(f"session item {item_id} not found")
        return _item(rows[0])

    def items(self, session_id: str) -> list[SessionItem]:
        rows = self.store.query("SELECT * FROM session_items WHERE session_id = ? ORDER BY seq", [session_id])
        return [_item(r) for r in rows]

    def runs(self, session_id: str) -> list[str]:
        rows = self.store.query(
            "SELECT run_id FROM session_items WHERE session_id = ? AND run_id IS NOT NULL ORDER BY seq", [session_id]
        )
        seen: list[str] = []
        for r in rows:
            if r[0] not in seen:
                seen.append(str(r[0]))
        return seen

    def session_of_run(self, run_id: str) -> str | None:
        rows = self.store.query("SELECT session_id FROM session_items WHERE run_id = ? LIMIT 1", [run_id])
        return str(rows[0][0]) if rows else None


def _session(row: Any) -> Session:
    return Session(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        status=row["status"],
        preferences=json.loads(row["preferences_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _item(row: Any) -> SessionItem:
    return SessionItem(
        id=row["id"],
        session_id=row["session_id"],
        seq=int(row["seq"]),
        kind=row["kind"],
        role=row["role"],
        body=json.loads(row["body_json"]),
        run_id=row["run_id"],
        created_at=row["created_at"],
    )
