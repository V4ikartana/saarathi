"""Provider-independent contracts shared by every layer.

Models here are the only shapes that cross layer boundaries. Model output is parsed into these
types and validated before it can influence state.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TASK_ID_PATTERN = r"^T-\d{3}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


# --------------------------------------------------------------------------- models


class Transport(StrEnum):
    CLI = "cli"
    API = "api"
    LOCAL = "local"


class ReasoningTier(StrEnum):
    ECONOMY = "economy"
    BALANCED = "balanced"
    FRONTIER = "frontier"
    UNKNOWN = "unknown"


class ModelCapabilities(Strict):
    """Capabilities are tri-state: True (verified/declared), False, or None (unknown).

    Unknown is never treated as supported.
    """

    code_generation: bool | None = None
    repository_tools: bool | None = None
    structured_output: bool | None = None
    workspace_write: bool | None = None
    reasoning_tier: ReasoningTier = ReasoningTier.UNKNOWN
    context_window_tokens: int | None = None

    def supports(self, capability: str) -> bool:
        value = getattr(self, capability, None)
        return value is True


class ModelConstraints(Strict):
    allowed_data_classes: list[str] = Field(default_factory=lambda: ["public", "internal"])
    max_parallel: int = Field(default=1, ge=1)


class ModelPricing(Strict):
    metered: bool
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None


class ModelMetadata(Strict):
    source: str
    checked_at: datetime | None = None
    notes: str | None = None


class ModelDescriptor(Strict):
    id: str
    provider: str
    adapter: str
    transport: Transport
    available: bool
    unavailable_reason: str | None = None
    capabilities: ModelCapabilities
    constraints: ModelConstraints = Field(default_factory=ModelConstraints)
    pricing: ModelPricing
    metadata: ModelMetadata

    @property
    def key(self) -> str:
        """Policy-matching key, e.g. ``anthropic/opus``."""
        return f"{self.provider}/{self.id}"

    @property
    def candidate_id(self) -> str:
        """Unique id across adapters, e.g. ``claude-code:opus``."""
        return f"{self.adapter}:{self.id}"


# --------------------------------------------------------------------------- integrations (§12A)

_SELECTOR_FORBIDDEN = re.compile(r"[*?\[\],\s]")


class IntegrationRequirement(Strict):
    """One operation a task needs on one connected account. Resolved by the router, never by a model.

    ``account`` selects exactly one account: its id, a workspace-scoped alias or a provider identity.
    ``None`` means "the scoped default, or ask". Several accounts mean several tasks.
    """

    service: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$", max_length=80)
    operation: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", max_length=80)
    account: str | None = Field(default=None, max_length=200)
    tenant: str | None = None
    resource: str | None = None
    data_class: str = "internal"
    identity_sensitive: bool = True
    consent_required: bool = False
    # Fields of this task's result that may be passed on to later tasks (subject to transfer policy).
    share_fields: list[str] = Field(default_factory=list)

    @field_validator("account")
    @classmethod
    def _one_account(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or _SELECTOR_FORBIDDEN.search(value)):
            raise ValueError("account must name exactly one account (no wildcards, lists or spaces)")
        return value


# --------------------------------------------------------------------------- planning


class Complexity(StrEnum):
    ROUTINE = "routine"
    STANDARD = "standard"
    COMPLEX = "complex"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ClarificationQuestion(Strict):
    id: str
    question: str
    why_it_matters: str
    default_assumption: str


class IntakeResult(Strict):
    """Lead output at intake: material questions plus the assumptions it would otherwise make."""

    questions: list[ClarificationQuestion] = Field(max_length=5)
    assumptions: list[str]


class PlannedTask(Strict):
    id: str = Field(pattern=TASK_ID_PATTERN)
    title: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    dependencies: list[str]
    allowed_paths: list[str] = Field(min_length=1)
    verification_commands: list[str] = Field(min_length=1)
    required_capabilities: list[str]
    complexity: Complexity
    risk: Risk
    integrations: list[IntegrationRequirement] = Field(default_factory=list, max_length=8)

    @field_validator("allowed_paths")
    @classmethod
    def _relative_paths(cls, paths: list[str]) -> list[str]:
        for p in paths:
            if p.startswith("/") or ".." in p.split("/") or p.strip() in ("", "**", "*", "**/*"):
                raise ValueError(f"allowed path must be a relative, bounded pattern: {p!r}")
        return paths


class ProposedDecision(Strict):
    title: str
    context: str
    decision: str


class Plan(Strict):
    summary: str
    architecture_notes: str
    tasks: list[PlannedTask] = Field(min_length=1, max_length=20)
    integration_checks: list[str] = Field(min_length=1)
    decisions: list[ProposedDecision]

    @model_validator(mode="after")
    def _valid_dag(self) -> Plan:
        ids = [t.id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task ids must be unique")
        known = set(ids)
        for t in self.tasks:
            missing = [d for d in t.dependencies if d not in known]
            if missing:
                raise ValueError(f"{t.id} depends on unknown tasks {missing}")
            if t.id in t.dependencies:
                raise ValueError(f"{t.id} depends on itself")
        topological_order(self.tasks)  # raises on cycles
        return self


def topological_order(tasks: list[PlannedTask]) -> list[str]:
    """Kahn's algorithm with stable ordering by task id. Raises ValueError on cycles."""
    deps = {t.id: set(t.dependencies) for t in tasks}
    order: list[str] = []
    ready = sorted(t for t, d in deps.items() if not d)
    while ready:
        current = ready.pop(0)
        order.append(current)
        for t, d in deps.items():
            if current in d:
                d.remove(current)
                if not d and t not in order and t not in ready:
                    ready.append(t)
                    ready.sort()
    if len(order) != len(deps):
        raise ValueError("task dependencies contain a cycle")
    return order


class ReviewVerdict(Strict):
    decision: Literal["approve", "changes_requested"]
    summary: str
    concerns: list[str]


# --------------------------------------------------------------------------- execution


class TaskPermissions(Strict):
    network: bool = False
    secrets: bool = False


class TaskPacket(Strict):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    run_id: str
    title: str
    objective: str
    acceptance_criteria: list[str]
    dependencies: list[str]
    allowed_paths: list[str]
    context_snapshot_id: str
    required_capabilities: list[str]
    complexity: Complexity
    risk: Risk
    max_attempts: int = Field(ge=1, le=5)
    verification_commands: list[str]
    permissions: TaskPermissions = Field(default_factory=TaskPermissions)
    integrations: list[IntegrationRequirement] = Field(default_factory=list)


class NamedOutput(Strict):
    """One result field a task hands to later tasks, e.g. ``subject`` of a message it read."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: str = Field(max_length=20_000)


class WorkerReport(Strict):
    """What a worker says about its own work. Advisory only; never evidence on its own."""

    status: Literal["succeeded", "failed", "blocked"]
    summary: str
    changed_files: list[str]
    tests_run: list[str]
    assumptions: list[str]
    unresolved_risks: list[str]
    memory_proposals: list[str]
    # Results for dependent tasks. Saarathi filters them by policy before anyone else sees them.
    outputs: list[NamedOutput] = Field(default_factory=list)


class Usage(Strict):
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    cost_is_estimate: bool = True
    reported_model: str | None = None


class CommandEvidence(Strict):
    command: str
    exit_code: int | None
    duration_s: float
    timed_out: bool
    output_tail: str
    log_path: str | None = None
    rejected_reason: str | None = None

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.rejected_reason is None


class WorkerResult(Strict):
    """Saarathi's assessment of one attempt, built from Git state and verification, not from claims."""

    task_id: str
    attempt_id: str
    status: Literal["succeeded", "failed"]
    failure_reason: str | None = None
    adapter: str
    provider: str
    model: str
    transport: Transport
    commit: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    scope_violations: list[str] = Field(default_factory=list)
    checkout_modified: bool = False
    verification: list[CommandEvidence] = Field(default_factory=list)
    worker_report: WorkerReport | None = None
    usage: Usage = Field(default_factory=Usage)
    # "<task>.<field>" inputs this attempt received from dependencies after policy filtering.
    received_inputs: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- routing


class RejectedCandidate(Strict):
    candidate: str
    reasons: list[str]


class ConnectionCheck(Strict):
    connection: str
    worker: str
    adapter: str
    eligible: bool
    evidence: str | None = None
    checked_at: str | None = None
    reasons: list[str] = Field(default_factory=list)


class RequirementTrace(Strict):
    index: int
    service: str
    operation: str
    effect: str
    account: str | None = None  # resolved opaque account id
    account_label: str | None = None  # alias or masked identity, never a raw identifier
    tenant: str | None = None
    resolved_by: str | None = None  # selection | id | alias | identity | default | only_account
    identity_status: str | None = None
    connections: list[ConnectionCheck] = Field(default_factory=list)
    problem: str | None = None


class ApprovalNeed(Strict):
    key: str
    kind: Literal["side_effect", "identity", "data_transfer"]
    detail: str


class AccountOption(Strict):
    requirement: int
    account: str
    label: str
    identity_status: str


class TransferCheck(Strict):
    source_task: str
    service: str
    account: str
    from_provider: str
    to_candidate: str
    to_provider: str
    mode: Literal["same_provider", "allow", "require_approval", "deny", "tenant_denied"]
    fields: list[str]
    approved: bool


class IntegrationTrace(Strict):
    """Why integration filters admitted or rejected workers. Part of the persisted routing decision."""

    workspace: str
    requirements: list[RequirementTrace] = Field(default_factory=list)
    candidate_connections: dict[str, list[str]] = Field(default_factory=dict)
    selected_connections: list[str] = Field(default_factory=list)
    approvals_required: list[ApprovalNeed] = Field(default_factory=list)
    approvals_granted: list[str] = Field(default_factory=list)
    account_choices: list[AccountOption] = Field(default_factory=list)
    transfers: list[TransferCheck] = Field(default_factory=list)


class RoutingDecision(Strict):
    decision_id: str
    task_id: str
    chosen: str | None
    provider: str | None = None
    model: str | None = None
    adapter: str | None = None
    transport: Transport | None = None
    reason: str
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    alternatives: list[str] = Field(default_factory=list)
    busy: list[str] = Field(default_factory=list)
    rejected: list[RejectedCandidate] = Field(default_factory=list)
    estimated_cost_usd: float | None = None
    awaiting: Literal["account_selection", "approval"] | None = None
    integration: IntegrationTrace | None = None


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(slug) > max_len:
        cut = slug[:max_len]
        slug = cut.rsplit("-", 1)[0] if "-" in cut else cut  # end on a whole word
    return slug.rstrip("-") or "item"
