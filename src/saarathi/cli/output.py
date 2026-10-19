"""Published shapes of ``--json`` output. Changing a field is a breaking change."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import typer
from pydantic import BaseModel, TypeAdapter

from saarathi.core.contracts import ModelDescriptor


class TaskOut(BaseModel):
    id: str
    title: str
    state: str
    attempts: int
    failures: int
    dependencies: list[str]
    worker: str | None
    provider: str | None
    transport: str | None
    blocked_reason: str | None
    failure_reason: str | None


class StatusOut(BaseModel):
    schema_version: int = 1
    run_id: str
    state: str
    pause_reason: str | None
    paused_from: str | None
    base_commit: str | None
    integration_branch: str | None
    open_questions: list[str]
    tasks: list[TaskOut]


class ModelOut(BaseModel):
    candidate: str
    provider: str
    adapter: str
    model: str
    transport: str
    available: bool
    unavailable_reason: str | None
    reasoning_tier: str
    metered: bool
    source: str

    @classmethod
    def from_descriptor(cls, m: ModelDescriptor) -> ModelOut:
        return cls(
            candidate=m.candidate_id,
            provider=m.provider,
            adapter=m.adapter,
            model=m.id,
            transport=m.transport,
            available=m.available,
            unavailable_reason=m.unavailable_reason,
            reasoning_tier=m.capabilities.reasoning_tier,
            metered=m.pricing.metered,
            source=m.metadata.source,
        )


class DoctorOut(BaseModel):
    schema_version: int = 1
    project: str
    adapters: list[dict[str, Any]]
    lead: str | None
    lead_problems: list[str]


class PolicyOut(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]


def emit[T](value: T, json_out: bool, human: Callable[[T], None]) -> None:
    if json_out:
        typer.echo(
            TypeAdapter(type(value) if not isinstance(value, list) else list[Any]).dump_json(value, indent=2).decode()
        )
    else:
        human(value)


class CapabilityOut(BaseModel):
    operation: str
    effect: str
    resource_scope: str
    evidence: str
    checked_at: str | None


class ConnectionOut(BaseModel):
    id: str
    service: str
    account: str
    account_label: str
    worker: str
    adapter: str | None
    status: str
    discovery_method: str
    verified_at: str | None
    expires_at: str | None
    capabilities: list[CapabilityOut]


class IntegrationsOut(BaseModel):
    schema_version: int = 1
    workspace: str
    connections: list[ConnectionOut]


class AccountOut(BaseModel):
    id: str
    service: str
    aliases: list[str]
    identity: str | None  # masked; the full identifier is never stored
    identity_status: str
    tenant: str | None
    is_default: bool
    connections: list[str]


class AccountsOut(BaseModel):
    schema_version: int = 1
    workspace: str
    accounts: list[AccountOut]


class ScanEntryOut(BaseModel):
    adapter: str
    status: str
    detail: str
    notes: list[str]
    registered_connections: list[str]


class ScanOut(BaseModel):
    schema_version: int = 1
    workspace: str
    adapters: list[ScanEntryOut]


class VerifyOut(BaseModel):
    schema_version: int = 1
    connection: str
    status: str  # verified | user_confirmed | unavailable | failed
    evidence: str
    detail: str
