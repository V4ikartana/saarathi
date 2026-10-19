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
    Transport,
    Usage,
)
from saarathi.core.policy import ProviderConfig


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
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


_QUOTA = re.compile(
    r"(?i)usage limit|quota|credit balance|out of credits|billing|limit (?:has been )?reached|plan limit|weekly limit"
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
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


class ProviderAdapter(ABC):
    """One configured worker runtime (e.g. the user's Codex CLI)."""

    type_name: str
    provider: str
    transport: Transport = Transport.CLI
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

    @abstractmethod
    async def invoke(self, inv: Invocation) -> InvocationResult: ...

    def list_models(self) -> list[ModelDescriptor]:
        probe = self.probe()
        out: list[ModelDescriptor] = []
        for declared in self.config.models:
            caps = self.base_capabilities.model_copy(update={"reasoning_tier": declared.reasoning_tier})
            out.append(
                ModelDescriptor(
                    id=declared.id,
                    provider=self.provider,
                    adapter=self.name,
                    transport=self.transport,
                    available=probe.available and self.config.enabled,
                    unavailable_reason=None if probe.available else probe.reason,
                    capabilities=caps,
                    constraints=ModelConstraints(max_parallel=declared.max_parallel),
                    # Unknown metering (no auth info) is treated as metered: never assume free.
                    pricing=ModelPricing(metered=probe.metered is not False),
                    metadata=ModelMetadata(
                        source=declared.source,
                        checked_at=datetime.fromisoformat(probe.checked_at),
                        notes=f"{self.type_name} {probe.version or 'unknown version'}; auth={probe.auth_method}",
                    ),
                )
            )
        return out
