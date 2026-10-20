"""Provider adapter port. Orchestration code depends only on this module."""

from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from saarathi.core.contracts import (
    ModelCapabilities,
    ModelConstraints,
    ModelDescriptor,
    ModelMetadata,
    ModelPricing,
    ReasoningTier,
    Transport,
    Usage,
)
from saarathi.core.policy import ProviderConfig
from saarathi.providers.limits import ProviderLimits
from saarathi.providers.model_catalog import classify
from saarathi.runtime.binaries import NO_WINDOW, cli_env


class InvocationMode(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"


class ErrorKind(StrEnum):
    NONE = "none"
    RATE_LIMIT = "rate_limit"  # transient: retry after a short cooldown
    QUOTA_EXHAUSTED = "quota_exhausted"  # plan or credit limit: the provider is out until its window resets
    AUTH = "auth"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"
    PROCESS = "process"
    UNAVAILABLE = "unavailable"


@dataclass
class Invocation:
    purpose: str
    mode: InvocationMode
    prompt: str
    schema: dict[str, Any]
    cwd: Path
    model: str
    timeout_s: float
    log_dir: Path
    task_id: str | None = None
    allowed_commands: Sequence[str] = ()
    allow_network: bool = False
    env_passthrough: Sequence[str] = ()
    on_start: Callable[[int], None] | None = None


@dataclass
class InvocationResult:
    ok: bool
    structured: dict[str, Any] | None
    text: str
    usage: Usage
    error_kind: ErrorKind = ErrorKind.NONE
    error: str | None = None
    exit_code: int | None = None
    duration_s: float = 0.0
    session_id: str | None = None


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class DiscoveredModel:
    """A model the provider's own CLI listed for this account. Not a declaration: its tier is unknown."""

    id: str
    display_name: str | None = None
    description: str | None = None  # the provider's own one-line description, when it gives one


@dataclass
class ProbeResult:
    adapter: str
    provider: str
    available: bool
    binary: str | None = None
    version: str | None = None
    auth_method: str | None = None
    metered: bool | None = None
    reason: str | None = None
    checks: list[Check] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    test_only: bool = False
    discovered_models: list[DiscoveredModel] = field(default_factory=list)
    discovery_source: str | None = None  # the command that listed them, e.g. "codex debug models"
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


_QUOTA = re.compile(
    r"(?i)usage limit|quota|credit balance|out of credits|usage credits|billing|limit (?:has been )?reached|plan limit"
    r"|weekly limit|session limit|5-hour limit|hit your (?:\w+[ -])?limit"
)
_RATE_LIMIT = re.compile(r"(?i)rate[ _-]?limit|too many requests|\b429\b|overloaded|\b529\b|try again later")
_AUTH = re.compile(r"(?i)not logged in|unauthori[sz]ed|\b401\b|authentication|please log ?in|invalid api key")


def classify_error(text: str) -> ErrorKind:
    """Classify provider error text. Quota beats rate limit: "rate limit ... usage quota" is exhaustion."""
    if _QUOTA.search(text):
        return ErrorKind.QUOTA_EXHAUSTED
    if _RATE_LIMIT.search(text):
        return ErrorKind.RATE_LIMIT
    if _AUTH.search(text):
        return ErrorKind.AUTH
    return ErrorKind.PROCESS


def run_quick(argv: Sequence[str], env: Mapping[str, str], timeout: float = 20.0) -> tuple[int | None, str, str]:
    """Run a short, read-only diagnostic command synchronously (used by probes only)."""
    try:
        proc = subprocess.run(
            list(argv),
            env=cli_env(argv, env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


class ProviderAdapter(ABC):
    """One configured worker runtime (e.g. the user's Codex CLI)."""

    type_name: str
    provider: str
    transport: Transport = Transport.CLI
    # How a worker's actions are contained, for the permission review screen (ADR-0004).
    isolation: str = "No isolation is described for this adapter."
    # Capabilities this adapter's integration has been built and tested for.
    base_capabilities = ModelCapabilities(
        code_generation=True, repository_tools=True, structured_output=True, workspace_write=True
    )

    def __init__(self, name: str, config: ProviderConfig) -> None:
        self.name = name
        self.config = config
        self._probe: ProbeResult | None = None

    def probe(self, *, refresh: bool = False) -> ProbeResult:
        if self._probe is None or refresh:
            self._probe = self._probe_impl()
        return self._probe

    @abstractmethod
    def _probe_impl(self) -> ProbeResult: ...

    def read_limits(self) -> ProviderLimits | None:
        """Remaining subscription usage as this provider's CLI reports it, or None if it has no such report.
        Never calls a model and never reads credentials; a failure is returned in ``ProviderLimits.error``."""
        return None

    def worker_command(self, *, allow_network: bool, allowed_commands: Sequence[str]) -> list[str] | None:
        """The exact command line this adapter runs for a worker task, with placeholders for the prompt, schema,
        worktree and model. Shown on the permission review screen. None when the CLI is not installed or the
        adapter runs no external command."""
        return None

    @abstractmethod
    async def invoke(self, inv: Invocation) -> InvocationResult: ...

    def discovery_enabled(self) -> bool:
        """Model discovery is on unless the policy sets ``options.discover_models: false`` for this adapter."""
        return bool(self.config.options.get("discover_models", True))

    def list_models(self) -> list[ModelDescriptor]:
        """Declared models first, then models the CLI discovered for this account that are not declared.

        Tiers: a tier declared in the policy wins. Otherwise the model catalogue (vendor documentation) or the
        provider's own description classifies the model; anything unclassified stays "unknown" and is never assumed
        strong. A model a subscription charges for separately is listed but unavailable, since v0.1 is unmetered.
        """
        probe = self.probe()
        out: list[ModelDescriptor] = []
        notes = f"{self.type_name} {probe.version or 'unknown version'}; auth={probe.auth_method}"
        for d in self.config.models:
            out.append(self._descriptor(probe, d.id, d.reasoning_tier, d.max_parallel, d.source, notes))
        if self.discovery_enabled():
            declared_ids = {d.id for d in self.config.models}
            for found in probe.discovered_models:
                if found.id in declared_ids:
                    continue
                label = f"{found.display_name}; " if found.display_name else ""
                source = f"discovered: {probe.discovery_source or 'provider CLI'}"
                out.append(
                    self._descriptor(
                        probe, found.id, ReasoningTier.UNKNOWN, 1, source, f"{label}{notes}", found.description
                    )
                )
        return out

    def _descriptor(
        self,
        probe: ProbeResult,
        model_id: str,
        tier: ReasoningTier,
        max_parallel: int,
        source: str,
        notes: str,
        description: str | None = None,
    ) -> ModelDescriptor:
        available = probe.available and self.config.enabled
        reason = None if probe.available else probe.reason
        metered = probe.metered is not False  # unknown metering (no auth info) is treated as metered
        found = classify(model_id, description) if model_id != "default" else None
        if found is not None:
            if tier is ReasoningTier.UNKNOWN:
                tier = found.tier
                notes = f"tier from {found.basis}; {notes}"
            if found.paid_on_subscription and probe.auth_method not in ("api_key",):
                available, reason, metered = False, found.paid_on_subscription, True
        caps = self.base_capabilities.model_copy(update={"reasoning_tier": tier})
        return ModelDescriptor(
            id=model_id,
            provider=self.provider,
            adapter=self.name,
            transport=self.transport,
            available=available,
            unavailable_reason=reason,
            capabilities=caps,
            constraints=ModelConstraints(max_parallel=max_parallel),
            pricing=ModelPricing(metered=metered),
            metadata=ModelMetadata(source=source, checked_at=datetime.fromisoformat(probe.checked_at), notes=notes),
        )


def preview_invocation(*, allow_network: bool, allowed_commands: Sequence[str]) -> Invocation:
    """A worker invocation with placeholders, for showing what an adapter would run."""
    return Invocation(
        purpose="worker",
        mode=InvocationMode.WORKSPACE_WRITE,
        prompt="<task prompt>",
        schema={"<worker report schema>": True},
        cwd=Path("<task worktree>"),
        model="<model>",
        timeout_s=1800,
        log_dir=Path("<logs>"),
        allowed_commands=tuple(allowed_commands),
        allow_network=allow_network,
    )
