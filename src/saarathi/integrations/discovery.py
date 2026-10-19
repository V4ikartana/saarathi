"""Integration discovery: the contract, and the honest default for runtimes without an inventory interface.

Discovery may use only documented CLI/tool interfaces and supported permission inspection. It never
enumerates secrets, reads session files, extracts OAuth tokens or parses undocumented state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from saarathi.integrations.model import IntegrationConnection

ScanStatus = Literal["verified", "unverified", "unsupported"]
ProbeStatus = Literal["verified", "failed", "unavailable"]


@dataclass(frozen=True)
class DiscoveredConnection:
    service: str
    operations: tuple[str, ...]
    account_identity: str | None = None  # only when the interface reports it


@dataclass(frozen=True)
class DiscoveryReport:
    adapter: str
    status: ScanStatus
    detail: str
    connections: tuple[DiscoveredConnection, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProbeOutcome:
    connection: str
    status: ProbeStatus
    detail: str


class IntegrationDiscoveryAdapter(ABC):
    """Per-runtime discovery. ``scan`` must report ``unverified`` when it cannot see an inventory."""

    @abstractmethod
    def scan(self, adapter: str) -> DiscoveryReport: ...

    @abstractmethod
    def probe(self, connection: IntegrationConnection, operation: str) -> ProbeOutcome:
        """Run a non-destructive check of one operation, or report ``unavailable``."""


class NoDocumentedInventory(IntegrationDiscoveryAdapter):
    """Default for Claude Code and Codex CLI as of 2026-10-19.

    Neither CLI documents a non-interactive way to list connected integrations, the accounts behind
    them, or their granted scopes, so nothing is detected and nothing is probed.
    """

    def __init__(self, runtime: str) -> None:
        self.runtime = runtime

    def scan(self, adapter: str) -> DiscoveryReport:
        return DiscoveryReport(
            adapter=adapter,
            status="unverified",
            detail=(
                f"{self.runtime} exposes no documented interface for listing connected integrations or accounts; "
                "nothing was detected"
            ),
            notes=(
                "Register connections explicitly with `saarathi integrations register`.",
                "Registered connections stay unverified until confirmed with `saarathi integrations verify --confirm`.",
            ),
        )

    def probe(self, connection: IntegrationConnection, operation: str) -> ProbeOutcome:
        return ProbeOutcome(
            connection=connection.id,
            status="unavailable",
            detail=f"{self.runtime} has no documented non-destructive probe for '{operation}'",
        )


def discovery_for(adapter_type: str) -> IntegrationDiscoveryAdapter:
    """Every runtime Saarathi supports today lacks a documented inventory; see the compatibility matrix."""
    return NoDocumentedInventory(adapter_type)
