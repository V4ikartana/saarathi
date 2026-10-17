"""SQLite connection management and ordered schema migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS: tuple[str, ...] = (
    # 1: initial schema
    """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        state TEXT NOT NULL,
        paused_from TEXT,
        pause_reason TEXT,
        brief_path TEXT NOT NULL,
        brief_sha256 TEXT NOT NULL,
        base_commit TEXT,
        base_ref TEXT,
        integration_branch TEXT,
        intake_json TEXT,
        plan_json TEXT,
        integration_json TEXT,
        review_json TEXT,
        lead_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE questions (
        run_id TEXT NOT NULL REFERENCES runs(id),
        id TEXT NOT NULL,
        question TEXT NOT NULL,
        why_it_matters TEXT NOT NULL,
        default_assumption TEXT NOT NULL,
        answer TEXT,
        answered_at TEXT,
        PRIMARY KEY (run_id, id)
    );
    CREATE TABLE tasks (
        run_id TEXT NOT NULL REFERENCES runs(id),
        id TEXT NOT NULL,
        state TEXT NOT NULL,
        packet_json TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        failures INTEGER NOT NULL DEFAULT 0,
        lease_owner TEXT,
        lease_expires_at TEXT,
        branch TEXT,
        worktree TEXT,
        blocked_reason TEXT,
        result_json TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (run_id, id)
    );
    CREATE TABLE attempts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        task_id TEXT NOT NULL,
        number INTEGER NOT NULL,
        status TEXT NOT NULL,
        adapter TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        transport TEXT NOT NULL,
        routing_decision_id TEXT NOT NULL,
        pid INTEGER,
        base_commit TEXT NOT NULL,
        feedback TEXT,
        outcome TEXT,
        result_json TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        UNIQUE (run_id, task_id, number)
    );
    CREATE TABLE routing_decisions (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        decision_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        actor TEXT NOT NULL,
        run_id TEXT,
        task_id TEXT,
        correlation_id TEXT,
        type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        idempotency_key TEXT UNIQUE
    );
    CREATE INDEX events_run ON events(run_id, seq);
    CREATE TABLE control (
        run_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (run_id, key)
    );
    """,
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > len(MIGRATIONS):
        raise RuntimeError(
            f"state database schema v{current} is newer than this Saarathi supports (v{len(MIGRATIONS)})"
        )
    for version in range(current, len(MIGRATIONS)):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _split(MIGRATIONS[version]):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version + 1}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)


def _split(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]
