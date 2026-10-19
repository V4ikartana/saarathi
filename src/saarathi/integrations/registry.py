"""The integration and account registry: an interface plus its SQLite implementation.

Every read and write is scoped to one workspace. Writes are audited in the event log with masked
identities only. Nothing here stores, reads or forwards a credential.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

from saarathi.core.errors import ConfigError, NotFound
from saarathi.integrations.model import (
    Capability,
    ConnectionStatus,
    Effect,
    Evidence,
    IdentityStatus,
    IntegrationAccount,
    IntegrationConnection,
    IntegrationSnapshot,
    WorkerInstance,
    WorkerLifecycle,
    effect_of,
    identity_digest,
    mask_identity,
)
from saarathi.storage.store import StateStore, utcnow

ACTOR = "integrations"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SERVICE = re.compile(r"^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$")
_OPERATION = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.match(value):
        raise ConfigError(f"invalid {what}: {value!r}")
    return value


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class IntegrationRegistry(ABC):
    """Port for the integration/account registry. Routing reads it only through ``snapshot``."""

    @abstractmethod
    def snapshot(self, workspace: str) -> IntegrationSnapshot: ...

    @abstractmethod
    def register_worker(
        self,
        workspace: str,
        *,
        worker_id: str,
        adapter: str,
        runtime: str,
        profile: str | None = None,
        max_parallel: int = 1,
    ) -> WorkerInstance: ...

    @abstractmethod
    def register_account(
        self,
        workspace: str,
        *,
        service: str,
        account_id: str | None = None,
        identity: str | None = None,
        tenant: str | None = None,
    ) -> IntegrationAccount:
        """Register an account. An identity supplied here is ``user_declared``; without one it is ``unverified``."""

    @abstractmethod
    def register_connection(
        self,
        workspace: str,
        *,
        account_id: str,
        worker_id: str,
        operations: Iterable[str],
        connection_id: str | None = None,
        evidence: Evidence = Evidence.DECLARED,
        discovery_method: str = "manual_registration",
        provenance: str = "user",
        confidence: str = "unknown",
        resource_scope: str = "*",
        expires_at: str | None = None,
    ) -> IntegrationConnection: ...

    @abstractmethod
    def record_verification(
        self, workspace: str, connection_id: str, *, evidence: Evidence, method: str
    ) -> IntegrationConnection: ...

    @abstractmethod
    def set_connection_status(
        self, workspace: str, connection_id: str, status: ConnectionStatus, *, reason: str
    ) -> None: ...

    @abstractmethod
    def set_identity_status(
        self, workspace: str, account_id: str, status: IdentityStatus, *, method: str | None = None
    ) -> None: ...

    @abstractmethod
    def set_alias(self, workspace: str, account_id: str, alias: str) -> None: ...

    @abstractmethod
    def set_default(self, workspace: str, service: str, account_id: str | None) -> None: ...


class SqliteIntegrationRegistry(IntegrationRegistry):
    def __init__(self, store: StateStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ reads

    def snapshot(self, workspace: str) -> IntegrationSnapshot:
        q = self.store.query
        workers = {
            r["id"]: self._worker(r) for r in q("SELECT * FROM worker_instance WHERE workspace_id=?", [workspace])
        }
        accounts = {
            r["id"]: self._account(r) for r in q("SELECT * FROM integration_account WHERE workspace_id=?", [workspace])
        }
        caps: dict[str, list[Capability]] = {}
        for r in q(
            "SELECT * FROM integration_capability WHERE workspace_id=? ORDER BY connection_id, operation", [workspace]
        ):
            caps.setdefault(r["connection_id"], []).append(
                Capability(
                    operation=r["operation"],
                    resource_scope=r["resource_scope"],
                    permission_level=Effect(r["permission_level"]),
                    evidence=Evidence(r["evidence"]),
                    checked_at=r["checked_at"],
                )
            )
        connections = tuple(
            IntegrationConnection(
                id=r["id"],
                workspace=workspace,
                account_id=r["account_id"],
                worker_id=r["worker_id"],
                status=ConnectionStatus(r["status"]),
                discovery_method=r["discovery_method"],
                provenance=r["provenance"],
                confidence=r["confidence"],
                verified_at=r["verified_at"],
                expires_at=r["expires_at"],
                capabilities=tuple(caps.get(r["id"], ())),
            )
            for r in q("SELECT * FROM integration_connection WHERE workspace_id=? ORDER BY id", [workspace])
        )
        aliases = {
            (r["service"], r["alias"]): r["account_id"]
            for r in q("SELECT * FROM account_alias WHERE workspace_id=?", [workspace])
        }
        defaults = {
            r["service"]: r["account_id"] for r in q("SELECT * FROM account_default WHERE workspace_id=?", [workspace])
        }
        return IntegrationSnapshot(
            workspace=workspace,
            workers=workers,
            accounts=accounts,
            connections=connections,
            aliases=aliases,
            defaults=defaults,
        )

    @staticmethod
    def _worker(r: sqlite3.Row) -> WorkerInstance:
        return WorkerInstance(
            id=r["id"],
            workspace=r["workspace_id"],
            adapter=r["adapter"],
            runtime=r["runtime"],
            profile=r["profile"],
            lifecycle=WorkerLifecycle(r["lifecycle"]),
            health=r["health"],
            max_parallel=r["max_parallel"],
        )

    @staticmethod
    def _account(r: sqlite3.Row) -> IntegrationAccount:
        return IntegrationAccount(
            id=r["id"],
            workspace=r["workspace_id"],
            service=r["service"],
            tenant=r["tenant"],
            identity_hint=r["identity_hint"],
            identity_digest=r["identity_digest"],
            identity_status=IdentityStatus(r["identity_status"]),
            verification_method=r["verification_method"],
            verified_at=r["verified_at"],
        )

    def _require(self, table: str, workspace: str, key: str, what: str) -> sqlite3.Row:
        rows = self.store.query(f"SELECT * FROM {table} WHERE workspace_id=? AND id=?", [workspace, key])  # noqa: S608
        if not rows:
            raise NotFound(f"{what} '{key}' not found in workspace '{workspace}'")
        return rows[0]

    def _audit(self, type_: str, workspace: str, payload: dict[str, Any]) -> None:
        self.store.append_event(f"integration.{type_}", {"workspace": workspace, **payload}, actor=ACTOR)

    # ------------------------------------------------------------------ writes

    def register_worker(
        self,
        workspace: str,
        *,
        worker_id: str,
        adapter: str,
        runtime: str,
        profile: str | None = None,
        max_parallel: int = 1,
    ) -> WorkerInstance:
        _check(_NAME, worker_id, "worker id")
        now = utcnow()
        with self.store.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM worker_instance WHERE workspace_id=? AND id=?", (workspace, worker_id)
            ).fetchone()
            if existing is not None:
                if existing["adapter"] != adapter:
                    raise ConfigError(
                        f"worker '{worker_id}' is bound to adapter '{existing['adapter']}', not '{adapter}'"
                    )
                return self._worker(existing)
            clash = conn.execute(
                "SELECT id FROM worker_instance WHERE workspace_id=? AND adapter=? AND lifecycle='active'",
                (workspace, adapter),
            ).fetchone()
            if clash is not None:
                raise ConfigError(
                    f"adapter '{adapter}' already runs as worker '{clash['id']}' in workspace '{workspace}'",
                    hint="an adapter runs one authenticated profile, so it maps to one active worker",
                )
            conn.execute(
                "INSERT INTO worker_instance (workspace_id, id, adapter, runtime, profile, lifecycle, max_parallel,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (workspace, worker_id, adapter, runtime, profile, WorkerLifecycle.ACTIVE, max_parallel, now, now),
            )
            self._audit("worker_registered", workspace, {"worker": worker_id, "adapter": adapter})
        return self._worker(self._require("worker_instance", workspace, worker_id, "worker"))

    def register_account(
        self,
        workspace: str,
        *,
        service: str,
        account_id: str | None = None,
        identity: str | None = None,
        tenant: str | None = None,
    ) -> IntegrationAccount:
        _check(_SERVICE, service, "service")
        account_id = _check(_NAME, account_id, "account id") if account_id else _id("acct")
        now = utcnow()
        with self.store.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM integration_account WHERE workspace_id=? AND id=?", (workspace, account_id)
            ).fetchone()
            if existing is not None:
                if existing["service"] != service:
                    raise ConfigError(f"account '{account_id}' belongs to service '{existing['service']}'")
                return self._account(existing)
            digest = identity_digest(workspace, service, identity) if identity else None
            if digest:
                dup = conn.execute(
                    "SELECT id FROM integration_account WHERE workspace_id=? AND service=? AND identity_digest=?",
                    (workspace, service, digest),
                ).fetchone()
                if dup is not None:
                    raise ConfigError(f"that identity is already registered as account '{dup['id']}'")
            status = IdentityStatus.USER_DECLARED if identity else IdentityStatus.UNVERIFIED
            conn.execute(
                "INSERT INTO integration_account (workspace_id, id, service, tenant, identity_hint, identity_digest,"
                " identity_status, verification_method, verified_at, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    workspace,
                    account_id,
                    service,
                    tenant,
                    mask_identity(identity) if identity else None,
                    digest,
                    status,
                    "user_declaration" if identity else None,
                    None,
                    now,
                    now,
                ),
            )
            self._audit("account_registered", workspace, {"account": account_id, "service": service, "status": status})
        return self._account(self._require("integration_account", workspace, account_id, "account"))

    def register_connection(
        self,
        workspace: str,
        *,
        account_id: str,
        worker_id: str,
        operations: Iterable[str],
        connection_id: str | None = None,
        evidence: Evidence = Evidence.DECLARED,
        discovery_method: str = "manual_registration",
        provenance: str = "user",
        confidence: str = "unknown",
        resource_scope: str = "*",
        expires_at: str | None = None,
    ) -> IntegrationConnection:
        ops = sorted({_check(_OPERATION, op, "operation") for op in operations})
        if not ops:
            raise ConfigError("a connection needs at least one operation")
        connection_id = _check(_NAME, connection_id, "connection id") if connection_id else _id("conn")
        self._require("integration_account", workspace, account_id, "account")
        self._require("worker_instance", workspace, worker_id, "worker")
        now = utcnow()
        checked = None if evidence is Evidence.DECLARED else now
        with self.store.transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM integration_connection WHERE workspace_id=? AND id=?", (workspace, connection_id)
            ).fetchone():
                raise ConfigError(f"connection '{connection_id}' already exists")
            conn.execute(
                "INSERT INTO integration_connection (workspace_id, id, account_id, worker_id, status, discovery_method,"
                " provenance, confidence, verified_at, expires_at, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    workspace,
                    connection_id,
                    account_id,
                    worker_id,
                    ConnectionStatus.ACTIVE,
                    discovery_method,
                    provenance,
                    confidence,
                    checked,
                    expires_at,
                    now,
                    now,
                ),
            )
            for op in ops:
                conn.execute(
                    "INSERT INTO integration_capability (workspace_id, connection_id, operation, resource_scope,"
                    " permission_level, evidence, checked_at) VALUES (?,?,?,?,?,?,?)",
                    (workspace, connection_id, op, resource_scope, effect_of(op), evidence, checked),
                )
            self._audit(
                "connection_registered",
                workspace,
                {
                    "connection": connection_id,
                    "account": account_id,
                    "worker": worker_id,
                    "operations": ops,
                    "evidence": evidence,
                },
            )
        return self._connection(workspace, connection_id)

    def _connection(self, workspace: str, connection_id: str) -> IntegrationConnection:
        self._require("integration_connection", workspace, connection_id, "connection")
        return next(c for c in self.snapshot(workspace).connections if c.id == connection_id)

    def record_verification(
        self, workspace: str, connection_id: str, *, evidence: Evidence, method: str
    ) -> IntegrationConnection:
        if evidence not in (Evidence.PROBED, Evidence.DISCOVERED, Evidence.USER_CONFIRMED):
            raise ConfigError(f"'{evidence}' is not verification evidence")
        self._require("integration_connection", workspace, connection_id, "connection")
        now = utcnow()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE integration_capability SET evidence=?, checked_at=? WHERE workspace_id=? AND connection_id=?",
                (evidence, now, workspace, connection_id),
            )
            conn.execute(
                "UPDATE integration_connection SET verified_at=?, discovery_method=?, updated_at=?"
                " WHERE workspace_id=? AND id=?",
                (now, method, now, workspace, connection_id),
            )
            self._audit("connection_verified", workspace, {"connection": connection_id, "evidence": evidence})
        return self._connection(workspace, connection_id)

    def set_connection_status(
        self, workspace: str, connection_id: str, status: ConnectionStatus, *, reason: str
    ) -> None:
        self._require("integration_connection", workspace, connection_id, "connection")
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE integration_connection SET status=?, updated_at=? WHERE workspace_id=? AND id=?",
                (status, utcnow(), workspace, connection_id),
            )
            self._audit(
                "connection_status", workspace, {"connection": connection_id, "status": status, "reason": reason}
            )

    def set_identity_status(
        self, workspace: str, account_id: str, status: IdentityStatus, *, method: str | None = None
    ) -> None:
        self._require("integration_account", workspace, account_id, "account")
        now = utcnow()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE integration_account SET identity_status=?,"
                " verification_method=COALESCE(?, verification_method), verified_at=?, updated_at=?"
                " WHERE workspace_id=? AND id=?",
                (status, method, now if status is IdentityStatus.VERIFIED else None, now, workspace, account_id),
            )
            self._audit("identity_status", workspace, {"account": account_id, "status": status, "method": method})

    def set_alias(self, workspace: str, account_id: str, alias: str) -> None:
        _check(_NAME, alias, "alias")
        account = self._account(self._require("integration_account", workspace, account_id, "account"))
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO account_alias (workspace_id, service, alias, account_id, created_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(workspace_id, service, alias) DO UPDATE SET account_id=excluded.account_id",
                (workspace, account.service, alias, account_id, utcnow()),
            )
            self._audit("alias_set", workspace, {"account": account_id, "service": account.service, "alias": alias})

    def set_default(self, workspace: str, service: str, account_id: str | None) -> None:
        _check(_SERVICE, service, "service")
        with self.store.transaction() as conn:
            if account_id is None:
                conn.execute("DELETE FROM account_default WHERE workspace_id=? AND service=?", (workspace, service))
            else:
                account = self._account(self._require("integration_account", workspace, account_id, "account"))
                if account.service != service:
                    raise ConfigError(f"account '{account_id}' is a '{account.service}' account, not '{service}'")
                conn.execute(
                    "INSERT INTO account_default (workspace_id, service, account_id, set_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(workspace_id, service) DO UPDATE SET account_id=excluded.account_id,"
                    " set_at=excluded.set_at",
                    (workspace, service, account_id, utcnow()),
                )
            self._audit("default_set", workspace, {"service": service, "account": account_id})
