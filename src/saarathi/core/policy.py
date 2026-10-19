"""Project policy: what Saarathi may use and do. Loaded from ``.saarathi/policies.yaml``."""

from __future__ import annotations

from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError, model_validator

from saarathi.core.contracts import ReasoningTier, Strict
from saarathi.core.errors import ConfigError


class Mode(StrEnum):
    SUBSCRIPTION = "subscription"
    API = "api"
    HYBRID = "hybrid"


class ModelRef(Strict):
    adapter: str
    model: str


class OperationsConfig(Strict):
    strategy: Literal["deterministic"] = "deterministic"


class OrchestrationConfig(Strict):
    lead: ModelRef
    lead_substitute: ModelRef | None = None
    operations: OperationsConfig = Field(default_factory=OperationsConfig)


class RoutingWeights(Strict):
    tier_fit: float = 3.0
    cost: float = 1.0
    history: float = 2.0
    load: float = 1.0
    spread: float = 1.5


class RoutingConfig(Strict):
    allowed_providers: list[str]
    allowed_models: list[str]
    denied_models: list[str] = Field(default_factory=list)
    max_parallel_workers: int = Field(default=2, ge=1, le=16)
    escalate_after_failures: int = Field(default=2, ge=1)
    spread_across_providers: bool = True
    weights: RoutingWeights = Field(default_factory=RoutingWeights)

    def model_allowed(self, provider: str, model_key: str) -> tuple[bool, str | None]:
        if provider not in self.allowed_providers:
            return False, f"provider '{provider}' is not in routing.allowed_providers"
        for pattern in self.denied_models:
            if fnmatchcase(model_key, pattern):
                return False, f"model '{model_key}' matches denied pattern '{pattern}'"
        if not any(fnmatchcase(model_key, p) for p in self.allowed_models):
            return False, f"model '{model_key}' matches no routing.allowed_models pattern"
        return True, None


class BillingConfig(Strict):
    api_enabled: bool = False
    allow_api_fallback: bool = False
    max_run_usd: float = Field(default=0, ge=0)
    max_month_usd: float = Field(default=0, ge=0)


class PermissionsConfig(Strict):
    allow_network: bool = False
    allow_dependency_install: bool = False
    allow_deployment: bool = False
    require_approval_for_merge: bool = True


DEFAULT_IGNORED_ARTIFACTS = (
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/node_modules/**",
    "**/.DS_Store",
)


class ExecutionConfig(Strict):
    task_timeout_seconds: int = Field(default=1800, ge=10)
    verification_timeout_seconds: int = Field(default=600, ge=5)
    lead_timeout_seconds: int = Field(default=900, ge=10)
    max_attempts: int = Field(default=2, ge=1, le=5)
    env_passthrough: list[str] = Field(default_factory=list)
    max_log_bytes: int = Field(default=2_000_000, ge=10_000)
    # Provider limits (Scenario D). A rate-limited adapter cools down for these seconds on consecutive hits;
    # after the last step, or on quota exhaustion, it is treated as exhausted for quota_cooldown_seconds.
    rate_limit_cooldown_seconds: list[float] = Field(default_factory=lambda: [30.0, 120.0, 300.0], min_length=1)
    quota_cooldown_seconds: float = Field(default=3600.0, gt=0)
    # Wait in place for a cooling adapter only if it is back within this many seconds; otherwise pause.
    max_cooldown_wait_seconds: float = Field(default=600.0, ge=0)
    # Provider-limit retries per task before the task is blocked for a person to decide.
    max_rate_limit_retries: int = Field(default=3, ge=1, le=10)
    # Escalations a task may raise before it is left for a person without consulting the lead again.
    max_escalations_per_task: int = Field(default=2, ge=1, le=5)
    # Build artifacts that are never committed or counted as task changes.
    ignored_artifacts: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORED_ARTIFACTS))


class TransferMode(StrEnum):
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    ALLOW = "allow"


class IntegrationPolicy(Strict):
    """Rules for integration-aware routing (§12A, ADR-0008). Defaults fail closed."""

    # Accounts, aliases, defaults and connections are read only from this workspace.
    workspace: str = Field(default="default", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    # Only discovered, probed or user-confirmed capabilities count. False also admits declared ones.
    require_verified_integration: bool = True
    # A user-declared account identity is not provider verification.
    user_declared_identity: TransferMode = TransferMode.REQUIRE_APPROVAL
    cross_provider_data_transfer: TransferMode = TransferMode.REQUIRE_APPROVAL
    # Result fields that may cross a provider boundary when a transfer is allowed or approved.
    shareable_fields: list[str] = Field(default_factory=list)
    # Tenants whose data never crosses a provider boundary, whatever the transfer mode.
    no_transfer_tenants: list[str] = Field(default_factory=list)
    # Sending, deleting, sharing or publishing. "allow" is deliberately not offered.
    external_side_effects: Literal["deny", "require_approval"] = "require_approval"
    max_verification_age_hours: int = Field(default=168, ge=1)


class DeclaredModel(Strict):
    id: str
    reasoning_tier: ReasoningTier = ReasoningTier.UNKNOWN
    max_parallel: int = Field(default=1, ge=1)
    source: str = "user_declared"


class ProviderConfig(Strict):
    enabled: bool = True
    # Adapter implementation; defaults to the entry's key (e.g. "codex-cli"). "fake" is test-only.
    type: str | None = None
    # Provider identity, only settable for fake adapters; real adapters declare their own.
    provider: str | None = None
    binary: str | None = None
    models: list[DeclaredModel] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class Policy(Strict):
    version: Literal[1] = 1
    mode: Mode = Mode.SUBSCRIPTION
    orchestration: OrchestrationConfig
    routing: RoutingConfig
    billing: BillingConfig = Field(default_factory=BillingConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    integrations: IntegrationPolicy = Field(default_factory=IntegrationPolicy)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> Policy:
        if self.mode is not Mode.SUBSCRIPTION:
            raise ValueError(
                f"mode '{self.mode}' is not supported in v0.1: Saarathi runs on subscription CLIs only (ADR-0011)"
            )
        if self.billing.api_enabled or self.billing.allow_api_fallback:
            raise ValueError(
                "billing.api_enabled and billing.allow_api_fallback must be false in v0.1: metered API use is not "
                "supported yet (ADR-0011)"
            )
        refs = [self.orchestration.lead]
        if self.orchestration.lead_substitute:
            refs.append(self.orchestration.lead_substitute)
        for ref in refs:
            cfg = self.providers.get(ref.adapter)
            if cfg is None:
                raise ValueError(f"orchestration references unknown provider adapter '{ref.adapter}'")
            if ref.model not in {m.id for m in cfg.models}:
                raise ValueError(
                    f"orchestration model '{ref.model}' is not declared under providers.{ref.adapter}.models"
                )
        return self


def load_policy(path: Path) -> Policy:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"policy file not found: {path}", hint="run `saarathi init`") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"policy file is not valid YAML: {exc}") from exc
    return parse_policy(raw, source=str(path))


def parse_policy(raw: object, *, source: str = "<policy>") -> Policy:
    try:
        return Policy.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors())
        raise ConfigError(f"invalid policy in {source}: {details}") from exc


DEFAULT_POLICY_YAML = """\
# Saarathi policy. Every field is enforced by deterministic code, not by a model.
version: 1
mode: subscription            # v0.1 supports subscription CLIs only (ADR-0011)

orchestration:
  # The engineering lead runs intake, planning and final review. It must be a declared model below.
  lead:
    adapter: claude-code
    model: opus
  # Used only if the lead is unavailable. Leave null to pause instead of substituting.
  lead_substitute: null
  operations:
    strategy: deterministic

routing:
  allowed_providers: [anthropic, openai]
  allowed_models: ["anthropic/*", "openai/*"]
  denied_models: []
  max_parallel_workers: 2
  escalate_after_failures: 2
  spread_across_providers: true

billing:
  api_enabled: false          # must stay false in v0.1; CLIs signed in with an API key are blocked as metered
  allow_api_fallback: false
  max_run_usd: 0
  max_month_usd: 0

permissions:
  allow_network: false
  allow_dependency_install: false
  allow_deployment: false
  require_approval_for_merge: true

execution:
  task_timeout_seconds: 1800
  verification_timeout_seconds: 600
  lead_timeout_seconds: 900
  max_attempts: 2
  env_passthrough: []         # extra environment variable names workers may see
  # ignored_artifacts: build outputs never committed or counted as changes
  # (default: __pycache__, *.pyc, .pytest_cache, .mypy_cache, .ruff_cache, node_modules, .DS_Store)

integrations:
  workspace: default          # accounts, aliases and defaults are scoped to this workspace
  require_verified_integration: true
  user_declared_identity: require_approval    # deny | require_approval | allow
  cross_provider_data_transfer: require_approval
  shareable_fields: []        # result fields that may cross a provider boundary
  external_side_effects: require_approval     # mail.send, *.delete, *.share ... (deny | require_approval)

providers:
  claude-code:
    enabled: true
    binary: null              # null = look up `claude` on PATH
    models:
      # Documented Claude Code aliases. Tiers are your declaration, not a provider guarantee.
      - {id: opus, reasoning_tier: frontier}
      - {id: sonnet, reasoning_tier: balanced}
  codex-cli:
    enabled: true
    binary: null              # null = look up `codex` on PATH
    options:
      isolate_user_config: true   # pass --ignore-user-config (auth is still read by Codex itself)
    models:
      # "default" means the model selected by your Codex configuration; Codex does not report which.
      - {id: default, reasoning_tier: unknown}
"""
