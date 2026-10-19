"""Two-stage routing: deterministic hard filters, then an explainable weighted score (ADR-0005).

Integration requirements (ADR-0008) add hard filters: the account is resolved and every candidate must
reach it through an authorized connection before any candidate is scored.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from saarathi.core.contracts import (
    ApprovalNeed,
    Complexity,
    IntegrationRequirement,
    ModelDescriptor,
    ReasoningTier,
    RejectedCandidate,
    RoutingDecision,
)
from saarathi.core.policy import Policy
from saarathi.integrations.matcher import Awaiting, IntegrationMatch, IntegrationRequirementMatcher
from saarathi.integrations.model import DataSource, IntegrationSnapshot

_TIER_RANK = {ReasoningTier.ECONOMY: 1, ReasoningTier.BALANCED: 2, ReasoningTier.FRONTIER: 3}
_DESIRED_RANK = {Complexity.ROUTINE: 1, Complexity.STANDARD: 2, Complexity.COMPLEX: 3}
UNKNOWN_TIER_FIT = 0.25  # below any matching known tier: unknown is never assumed strong
FAILED_BEFORE_PENALTY = 5.0


@dataclass(frozen=True)
class RoutingRequest:
    task_id: str
    required_capabilities: tuple[str, ...]
    complexity: Complexity
    data_class: str = "internal"
    previously_failed: frozenset[str] = frozenset()
    integrations: tuple[IntegrationRequirement, ...] = ()
    data_sources: tuple[DataSource, ...] = ()  # integration data this task would receive
    approvals: frozenset[str] = frozenset()  # approval keys granted for this run
    selections: tuple[tuple[int, str], ...] = ()  # (requirement index, account id) chosen by the user
    now: datetime | None = None


@dataclass
class RuntimeLoad:
    in_flight: dict[str, int] = field(default_factory=dict)  # candidate_id -> running attempts
    providers_in_flight: dict[str, int] = field(default_factory=dict)
    history: dict[str, tuple[int, int]] = field(default_factory=dict)  # candidate_id -> (ok, finished)
    # adapter -> (state, until ISO time, reason) for adapters currently cooling down after provider limits
    cooldowns: dict[str, tuple[str, str, str]] = field(default_factory=dict)


def hard_filter(model: ModelDescriptor, req: RoutingRequest, policy: Policy) -> list[str]:
    """Return every reason the candidate is ineligible. Empty means eligible."""
    reasons: list[str] = []
    if not model.available:
        reasons.append(f"unavailable: {model.unavailable_reason or 'probe failed'}")
    allowed, why = policy.routing.model_allowed(model.provider, model.key)
    if not allowed and why:
        reasons.append(why)
    for capability in req.required_capabilities:
        if not model.capabilities.supports(capability):
            reasons.append(f"capability '{capability}' is not verified for this model")
    if model.pricing.metered:
        if not policy.billing.api_enabled:
            reasons.append("metered usage is blocked (billing.api_enabled is false)")
        elif model.pricing.input_usd_per_million is None or model.pricing.output_usd_per_million is None:
            reasons.append("metered model has unknown pricing; budgets cannot be enforced")
    if req.data_class not in model.constraints.allowed_data_classes:
        reasons.append(f"data class '{req.data_class}' not permitted for this model")
    return reasons


def _tier_fit(model: ModelDescriptor, complexity: Complexity) -> float:
    rank = _TIER_RANK.get(model.capabilities.reasoning_tier)
    if rank is None:
        return UNKNOWN_TIER_FIT
    return max(0.0, 1.0 - abs(rank - _DESIRED_RANK[complexity]) / 2)


def score(model: ModelDescriptor, req: RoutingRequest, policy: Policy, load: RuntimeLoad) -> dict[str, float]:
    w = policy.routing.weights
    ok, n = load.history.get(model.candidate_id, (0, 0))
    in_flight = load.in_flight.get(model.candidate_id, 0)
    terms = {
        "tier_fit": w.tier_fit * _tier_fit(model, req.complexity),
        "cost": w.cost * (1.0 if not model.pricing.metered else 0.5),
        "history": w.history * ((ok + 1) / (n + 2)),
        "load": w.load * (1.0 - in_flight / model.constraints.max_parallel),
        "spread": (
            w.spread * (0.0 if load.providers_in_flight.get(model.provider, 0) else 1.0)
            if policy.routing.spread_across_providers
            else 0.0
        ),
    }
    if model.candidate_id in req.previously_failed:
        terms["previously_failed"] = -FAILED_BEFORE_PENALTY
    terms["total"] = round(sum(terms.values()), 4)
    return {k: round(v, 4) for k, v in terms.items()}


def _match_integrations(
    req: RoutingRequest, policy: Policy, snapshot: IntegrationSnapshot | None
) -> IntegrationMatch | None:
    if not req.integrations and not req.data_sources:
        return None
    matcher = IntegrationRequirementMatcher(policy.integrations, now=req.now)
    return matcher.match(
        req.task_id,
        req.integrations,
        req.data_sources,
        snapshot or IntegrationSnapshot(workspace=policy.integrations.workspace),
        approvals=req.approvals,
        selections=dict(req.selections),
    )


def route(
    req: RoutingRequest,
    candidates: Iterable[ModelDescriptor],
    policy: Policy,
    load: RuntimeLoad | None = None,
    integrations: IntegrationSnapshot | None = None,
) -> RoutingDecision:
    load = load or RuntimeLoad()
    decision_id = f"rd-{uuid.uuid4().hex[:12]}"
    match = _match_integrations(req, policy, integrations)
    rejected: list[RejectedCandidate] = []
    busy: list[str] = []
    cooling: list[str] = []
    cooling_notes: dict[str, str] = {}
    available_at: str | None = None
    pending: list[ApprovalNeed] = []
    scored: list[tuple[float, str, ModelDescriptor, dict[str, float]]] = []
    connections: dict[str, list[str]] = {}
    for model in candidates:
        reasons = hard_filter(model, req, policy)
        if match is not None:
            check = match.check_candidate(model)
            reasons += check.reasons
            if not reasons and check.pending:
                pending.extend(p for p in check.pending if p not in pending)
                reasons = [f"awaiting approval '{p.key}': {p.detail}" for p in check.pending]
            if not reasons:
                connections[model.candidate_id] = check.connections
        if reasons:
            rejected.append(RejectedCandidate(candidate=model.candidate_id, reasons=reasons))
            continue
        limit = load.cooldowns.get(model.adapter)
        if limit is not None:
            state, until, why = limit
            cooling.append(model.candidate_id)
            cooling_notes[model.adapter] = f"{model.adapter} {state.replace('_', ' ')} until {until} ({why})"
            available_at = until if available_at is None else min(available_at, until)
            continue
        if load.in_flight.get(model.candidate_id, 0) >= model.constraints.max_parallel:
            busy.append(model.candidate_id)
            continue
        breakdown = score(model, req, policy, load)
        scored.append((breakdown["total"], model.candidate_id, model, breakdown))

    trace = None
    if match is not None:
        trace = match.trace
        trace.candidate_connections = connections
        trace.approvals_required += [p for p in pending if p not in trace.approvals_required]

    def blocked(reason: str, awaiting: Awaiting | None = None, *, waiting: list[str] | None = None) -> RoutingDecision:
        return RoutingDecision(
            decision_id=decision_id,
            task_id=req.task_id,
            chosen=None,
            reason=reason,
            busy=waiting or [],
            rejected=rejected,
            awaiting=awaiting,
            integration=trace,
            cooling=cooling,
            available_at=available_at,
        )

    if match is not None and match.blocked:  # task-level: no candidate may run, busy or not
        return blocked(match.blocked, match.awaiting)
    if not scored:
        if busy:
            return blocked("all eligible candidates are at capacity; task waits", waiting=busy)
        if cooling:
            notes = "; ".join(cooling_notes[a] for a in sorted(cooling_notes))
            return blocked(f"every eligible worker is limited by its provider: {notes}")
        if pending:
            keys = "; ".join(f"{p.key} ({p.detail})" for p in pending)
            return blocked(f"awaiting approval: {keys}", "approval")
        return blocked("no candidate satisfies the hard filters; task cannot run until policy or providers change")

    scored.sort(key=lambda item: (-item[0], item[1]))
    total, cid, model, breakdown = scored[0]
    terms = ", ".join(f"{k}={v}" for k, v in breakdown.items() if k != "total")
    if trace is not None:
        trace.selected_connections = connections.get(cid, [])
    return RoutingDecision(
        decision_id=decision_id,
        task_id=req.task_id,
        chosen=cid,
        provider=model.provider,
        model=model.id,
        adapter=model.adapter,
        transport=model.transport,
        reason=f"highest score {total} among {len(scored)} eligible ({terms})",
        score_breakdown=breakdown,
        alternatives=[c for _, c, _, _ in scored[1:]],
        busy=busy,
        cooling=cooling,
        rejected=rejected,
        estimated_cost_usd=0.0 if not model.pricing.metered else None,
        integration=trace,
    )


def resolve_lead(candidates: Iterable[ModelDescriptor], policy: Policy) -> tuple[ModelDescriptor | None, list[str]]:
    """Return the pinned lead (or its explicit substitute) if it passes the hard filters.

    Never picks anything else: if neither qualifies the caller must pause.
    """
    by_id = {m.candidate_id: m for m in candidates}
    req = RoutingRequest(task_id="lead", required_capabilities=("structured_output",), complexity=Complexity.COMPLEX)
    problems: list[str] = []
    refs = [policy.orchestration.lead]
    if policy.orchestration.lead_substitute:
        refs.append(policy.orchestration.lead_substitute)
    for ref in refs:
        model = by_id.get(f"{ref.adapter}:{ref.model}")
        if model is None:
            problems.append(f"{ref.adapter}:{ref.model}: not in the model registry")
            continue
        reasons = hard_filter(model, req, policy)
        if not reasons:
            return model, problems
        problems.append(f"{model.candidate_id}: {'; '.join(reasons)}")
    return None, problems
