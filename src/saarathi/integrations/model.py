"""Normalized integration records: workers, accounts, connections and capabilities.

These are facts Saarathi holds about who may do what. None of them is a credential: authentication
stays inside the provider runtime or the OS credential store.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum


class IdentityStatus(StrEnum):
    VERIFIED = "verified"  # established through a documented interface or safe probe
    USER_DECLARED = "user_declared"  # the user said so; never shown as provider verification
    UNVERIFIED = "unverified"
    STALE = "stale"
    REVOKED = "revoked"


class Evidence(StrEnum):
    """How Saarathi knows a connection permits an operation."""

    DECLARED = "declared"  # registered by hand, not checked
    ADVERTISED = "advertised"  # a runtime says the tool exists; says nothing about this account
    DISCOVERED = "discovered"  # reported by a documented inventory interface
    PROBED = "probed"  # a non-destructive probe succeeded
    USER_CONFIRMED = "user_confirmed"  # the user explicitly confirmed it works


VERIFIED_EVIDENCE = frozenset({Evidence.DISCOVERED, Evidence.PROBED, Evidence.USER_CONFIRMED})


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    REVOKED = "revoked"


class WorkerLifecycle(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class Effect(StrEnum):
    READ = "read"
    WRITE = "write"  # changes the user's own resources (a draft, a label)
    EXTERNAL = "external"  # visible to others or destructive (send, delete, share, publish)


_READ_VERBS = frozenset({"read", "search", "list", "get", "query", "fetch", "view", "download"})
_WRITE_VERBS = frozenset({"draft", "create", "write", "update", "modify", "label", "move", "comment", "upload"})


def effect_of(operation: str) -> Effect:
    """Classify by the operation's last segment. Unknown verbs are treated as external (fail closed)."""
    verb = operation.rsplit(".", 1)[-1]
    if verb in _READ_VERBS:
        return Effect.READ
    if verb in _WRITE_VERBS:
        return Effect.WRITE
    return Effect.EXTERNAL


def identity_digest(workspace: str, service: str, identity: str) -> str:
    """Workspace-salted, so the same mailbox cannot be correlated across workspaces."""
    material = f"{workspace}\x00{service}\x00{identity.strip().lower()}"
    return hashlib.sha256(material.encode()).hexdigest()


def mask_identity(identity: str) -> str:
    identity = identity.strip()
    local, at, domain = identity.partition("@")
    if at:
        shown = local[0] + "***" + (local[-1] if len(local) > 2 else "")
        return f"{shown}@{domain}"
    return identity[:2] + "***" if len(identity) > 2 else "***"


@dataclass(frozen=True)
class WorkerInstance:
    """A provider adapter running under one authenticated profile."""

    id: str
    workspace: str
    adapter: str
    runtime: str
    profile: str | None
    lifecycle: WorkerLifecycle
    health: str
    max_parallel: int


@dataclass(frozen=True)
class IntegrationAccount:
    id: str
    workspace: str
    service: str
    tenant: str | None
    identity_hint: str | None
    identity_digest: str | None
    identity_status: IdentityStatus
    verification_method: str | None
    verified_at: str | None


@dataclass(frozen=True)
class Capability:
    operation: str
    resource_scope: str
    permission_level: Effect
    evidence: Evidence
    checked_at: str | None


@dataclass(frozen=True)
class IntegrationConnection:
    id: str
    workspace: str
    account_id: str
    worker_id: str
    status: ConnectionStatus
    discovery_method: str
    provenance: str
    confidence: str
    verified_at: str | None
    expires_at: str | None
    capabilities: tuple[Capability, ...] = ()


@dataclass(frozen=True)
class IntegrationSnapshot:
    """Everything routing may know about one workspace, read in one go so routing stays pure."""

    workspace: str
    workers: dict[str, WorkerInstance] = field(default_factory=dict)
    accounts: dict[str, IntegrationAccount] = field(default_factory=dict)
    connections: tuple[IntegrationConnection, ...] = ()
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)  # (service, alias) -> account id
    defaults: dict[str, str] = field(default_factory=dict)  # service -> account id

    def aliases_of(self, account_id: str) -> list[str]:
        return sorted(alias for (_, alias), acct in self.aliases.items() if acct == account_id)

    def label(self, account: IntegrationAccount) -> str:
        aliases = self.aliases_of(account.id)
        return aliases[0] if aliases else (account.identity_hint or account.id)


@dataclass(frozen=True)
class DataSource:
    """Data a task receives from an earlier integration task, and where that data came from."""

    task_id: str
    service: str
    account_id: str
    tenant: str | None
    provider: str  # provider of the worker that read it
    fields: tuple[str, ...]
