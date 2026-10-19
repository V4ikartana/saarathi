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
    # 2: integration and account registry (§12A, ADR-0008). Every record is scoped to a workspace.
    # No secrets: authentication stays inside provider runtimes. Identities are stored as a
    # workspace-salted digest plus a masked hint, never in plain text.
    """
    CREATE TABLE worker_instance (
        workspace_id TEXT NOT NULL,
        id TEXT NOT NULL,
        adapter TEXT NOT NULL,
        runtime TEXT NOT NULL,
        profile TEXT,
        lifecycle TEXT NOT NULL,
        health TEXT NOT NULL DEFAULT 'unknown',
        max_parallel INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, id)
    );
    CREATE UNIQUE INDEX worker_instance_active_adapter ON worker_instance(workspace_id, adapter)
        WHERE lifecycle = 'active';
    CREATE TABLE integration_account (
        workspace_id TEXT NOT NULL,
        id TEXT NOT NULL,
        service TEXT NOT NULL,
        tenant TEXT,
        identity_hint TEXT,
        identity_digest TEXT,
        identity_status TEXT NOT NULL,
        verification_method TEXT,
        verified_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, id)
    );
    CREATE INDEX integration_account_service ON integration_account(workspace_id, service);
    CREATE TABLE integration_connection (
        workspace_id TEXT NOT NULL,
        id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        worker_id TEXT NOT NULL,
        status TEXT NOT NULL,
        discovery_method TEXT NOT NULL,
        provenance TEXT NOT NULL,
        confidence TEXT NOT NULL,
        verified_at TEXT,
        expires_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, id),
        FOREIGN KEY (workspace_id, account_id) REFERENCES integration_account(workspace_id, id),
        FOREIGN KEY (workspace_id, worker_id) REFERENCES worker_instance(workspace_id, id)
    );
    CREATE TABLE integration_capability (
        workspace_id TEXT NOT NULL,
        connection_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        resource_scope TEXT NOT NULL DEFAULT '*',
        permission_level TEXT NOT NULL,
        evidence TEXT NOT NULL,
        checked_at TEXT,
        PRIMARY KEY (workspace_id, connection_id, operation, resource_scope),
        FOREIGN KEY (workspace_id, connection_id) REFERENCES integration_connection(workspace_id, id)
    );
    CREATE TABLE account_alias (
        workspace_id TEXT NOT NULL,
        service TEXT NOT NULL,
        alias TEXT NOT NULL,
        account_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, service, alias),
        FOREIGN KEY (workspace_id, account_id) REFERENCES integration_account(workspace_id, id)
    );
    CREATE TABLE account_default (
        workspace_id TEXT NOT NULL,
        service TEXT NOT NULL,
        account_id TEXT NOT NULL,
        set_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, service),
        FOREIGN KEY (workspace_id, account_id) REFERENCES integration_account(workspace_id, id)
    );
    CREATE TABLE task_requirement (
        run_id TEXT NOT NULL REFERENCES runs(id),
        task_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        service TEXT NOT NULL,
        operation TEXT NOT NULL,
        effect TEXT NOT NULL,
        account_selector TEXT,
        tenant TEXT,
        resource TEXT,
        data_class TEXT NOT NULL,
        identity_sensitive INTEGER NOT NULL,
        consent_required INTEGER NOT NULL,
        share_fields_json TEXT NOT NULL,
        PRIMARY KEY (run_id, task_id, idx)
    );
    """,
    # 3: provider health (Scenario D) and escalations (Scenario E)
    """
    CREATE TABLE provider_health (
        adapter TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        until TEXT NOT NULL,
        reason TEXT NOT NULL,
        consecutive INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE escalations (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        task_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        summary TEXT NOT NULL,
        report_json TEXT NOT NULL,
        status TEXT NOT NULL,
        checkpoint TEXT,
        verdict_json TEXT,
        resolution TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX escalations_run ON escalations(run_id, status);
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
