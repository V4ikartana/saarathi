"""Policy-aware matching of task integration requirements to authorized workers.

Resolution order (§12A): service -> exact account -> eligible connection -> required operation ->
authorized worker. Everything here is a hard filter evaluated before any cost or quality scoring, and
none of it depends on what a service is: ``gmail`` and ``microsoft.outlook`` are just strings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from typing import Literal

from saarathi.core.contracts import (
    AccountOption,
    ApprovalNeed,
    ConnectionCheck,
    IntegrationRequirement,
    IntegrationTrace,
    ModelDescriptor,
    RequirementTrace,
    TransferCheck,
)
from saarathi.core.policy import IntegrationPolicy, TransferMode
from saarathi.integrations.model import (
    VERIFIED_EVIDENCE,
    ConnectionStatus,
    DataSource,
    Effect,
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

Awaiting = Literal["account_selection", "approval"]


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    value = datetime.fromisoformat(ts)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _show_selector(selector: str) -> str:
    return mask_identity(selector) if "@" in selector else selector


@dataclass
class _Resolved:
    requirement: IntegrationRequirement
    trace: RequirementTrace
    account: IntegrationAccount | None = None
    eligible: dict[str, list[str]] = field(default_factory=dict)  # adapter -> eligible connection ids
    rejected: dict[str, list[str]] = field(default_factory=dict)  # adapter -> why its connections failed


@dataclass
class CandidateCheck:
    reasons: list[str]
    pending: list[ApprovalNeed]
    connections: list[str]


@dataclass
class IntegrationMatch:
    """Task-level resolution plus a per-candidate check. ``blocked`` set means no candidate may run."""

    trace: IntegrationTrace
    blocked: str | None
    awaiting: Awaiting | None
    task_id: str
    policy: IntegrationPolicy
    approvals: frozenset[str]
    resolved: list[_Resolved]
    sources: tuple[DataSource, ...]

    def check_candidate(self, model: ModelDescriptor) -> CandidateCheck:
        reasons: list[str] = []
        pending: list[ApprovalNeed] = []
        connections: list[str] = []
        for r in self.resolved:
            if r.account is None:
                continue  # the task is blocked on account resolution; nothing to say per candidate
            label = r.trace.account_label
            ids = sorted(r.eligible.get(model.adapter, []))
            if ids:
                connections.append(ids[0])
                continue
            why = r.rejected.get(model.adapter)
            detail = f": {'; '.join(why)}" if why else ": no connection to this account on its worker"
            reasons.append(
                f"integration {r.trace.service}/{r.trace.operation} on account '{label}' "
                f"is not authorized for adapter '{model.adapter}'{detail}"
            )
        for src in self.sources:
            check, reason, need = self._transfer(src, model)
            self.trace.transfers.append(check)
            if reason:
                reasons.append(reason)
            if need:
                pending.append(need)
        return CandidateCheck(reasons, pending, connections)

    def _transfer(
        self, src: DataSource, model: ModelDescriptor
    ) -> tuple[TransferCheck, str | None, ApprovalNeed | None]:
        fields = [f for f in src.fields if f in self.policy.shareable_fields]
        base = {
            "source_task": src.task_id,
            "service": src.service,
            "account": src.account_id,
            "from_provider": src.provider,
            "to_candidate": model.candidate_id,
            "to_provider": model.provider,
        }
        if model.provider == src.provider:
            return TransferCheck(**base, mode="same_provider", fields=list(src.fields), approved=True), None, None
        what = f"{src.task_id} {src.service} data from provider '{src.provider}' to '{model.provider}'"
        if src.tenant and src.tenant in self.policy.no_transfer_tenants:
            return (
                TransferCheck(**base, mode="tenant_denied", fields=[], approved=False),
                f"tenant '{src.tenant}' prohibits cross-provider transfer of {what}",
                None,
            )
        mode = self.policy.cross_provider_data_transfer
        if mode is TransferMode.DENY:
            return (
                TransferCheck(**base, mode="deny", fields=[], approved=False),
                f"cross-provider transfer of {what} is denied by integrations.cross_provider_data_transfer",
                None,
            )
        if not fields:
            return (
                TransferCheck(
                    **base,
                    mode="allow" if mode is TransferMode.ALLOW else "require_approval",
                    fields=[],
                    approved=False,
                ),
                f"no field of {src.task_id}'s output may cross providers (integrations.shareable_fields)",
                None,
            )
        if mode is TransferMode.ALLOW:
            return TransferCheck(**base, mode="allow", fields=fields, approved=True), None, None
        key = f"data_transfer:{self.task_id}:{src.task_id}:{model.provider}"
        approved = key in self.approvals
        check = TransferCheck(**base, mode="require_approval", fields=fields, approved=approved)
        if approved:
            if key not in self.trace.approvals_granted:
                self.trace.approvals_granted.append(key)
            return check, None, None
        need = ApprovalNeed(key=key, kind="data_transfer", detail=f"share fields {fields} of {what}")
        return check, None, need


class IntegrationRequirementMatcher:
    def __init__(self, policy: IntegrationPolicy, *, now: datetime | None = None) -> None:
        self.policy = policy
        self.now = now or datetime.now(UTC)
        self.max_age = timedelta(hours=policy.max_verification_age_hours)

    def match(
        self,
        task_id: str,
        requirements: Sequence[IntegrationRequirement],
        sources: Sequence[DataSource],
        snapshot: IntegrationSnapshot,
        *,
        approvals: frozenset[str] = frozenset(),
        selections: Mapping[int, str] | None = None,
    ) -> IntegrationMatch:
        selections = selections or {}
        trace = IntegrationTrace(workspace=snapshot.workspace)
        resolved: list[_Resolved] = []
        task_approvals: list[ApprovalNeed] = []
        for idx, req in enumerate(requirements):
            r = self._requirement(task_id, idx, req, snapshot, selections, trace, task_approvals)
            resolved.append(r)
            trace.requirements.append(r.trace)
        trace.approvals_granted = sorted(a.key for a in task_approvals if a.key in approvals)
        trace.approvals_required = [a for a in task_approvals if a.key not in approvals]

        problems = [f"[{t.index}] {t.problem}" for t in trace.requirements if t.problem]
        blocked: str | None = None
        awaiting: Awaiting | None = None
        if problems:
            blocked = "integration requirements cannot be met: " + "; ".join(problems)
        elif trace.account_choices:
            awaiting = "account_selection"
            pending = sorted({c.requirement for c in trace.account_choices})
            blocked = (
                f"account selection required for requirement(s) {pending}: several accounts match and no scoped "
                "default is set (Saarathi never chooses by cost or order)"
            )
        elif trace.approvals_required:
            awaiting = "approval"
            blocked = "awaiting approval: " + "; ".join(f"{a.key} ({a.detail})" for a in trace.approvals_required)
        return IntegrationMatch(
            trace=trace,
            blocked=blocked,
            awaiting=awaiting,
            task_id=task_id,
            policy=self.policy,
            approvals=approvals,
            resolved=resolved,
            sources=tuple(sources),
        )

    # ------------------------------------------------------------------ per requirement

    def _requirement(
        self,
        task_id: str,
        idx: int,
        req: IntegrationRequirement,
        snap: IntegrationSnapshot,
        selections: Mapping[int, str],
        trace: IntegrationTrace,
        approvals: list[ApprovalNeed],
    ) -> _Resolved:
        effect = effect_of(req.operation)
        r = _Resolved(
            requirement=req,
            trace=RequirementTrace(index=idx, service=req.service, operation=req.operation, effect=effect),
        )
        account, how, problem = self._resolve_account(idx, req, snap, selections, trace)
        if account is None:
            r.trace.problem = problem
            return r
        r.account = account
        r.trace.account = account.id
        r.trace.account_label = snap.label(account)
        r.trace.tenant = account.tenant
        r.trace.resolved_by = how
        r.trace.identity_status = account.identity_status

        problems: list[str] = []
        if req.tenant is not None and account.tenant != req.tenant:
            problems.append(f"account '{r.trace.account_label}' is outside tenant '{req.tenant}'")
        identity_problem, identity_need = self._identity(req, account, r.trace.account_label)
        if identity_problem:
            problems.append(identity_problem)
        if identity_need:
            approvals.append(identity_need)

        for conn in snap.connections:
            if conn.account_id != account.id:
                continue
            worker = snap.workers.get(conn.worker_id)
            adapter = worker.adapter if worker else "?"
            reasons = self._connection_reasons(req, conn, worker)
            cap = next((c for c in conn.capabilities if c.operation == req.operation), None)
            r.trace.connections.append(
                ConnectionCheck(
                    connection=conn.id,
                    worker=conn.worker_id,
                    adapter=adapter,
                    eligible=not reasons,
                    evidence=cap.evidence if cap else None,
                    checked_at=cap.checked_at if cap else None,
                    reasons=reasons,
                )
            )
            if reasons:
                r.rejected.setdefault(adapter, []).extend(f"{conn.id} {why}" for why in reasons)
            else:
                r.eligible.setdefault(adapter, []).append(conn.id)
        if not r.eligible:
            why = [f"{c.connection} {'; '.join(c.reasons)}" for c in r.trace.connections]
            problems.append(
                f"no eligible connection permits {req.service}/{req.operation} on account '{r.trace.account_label}'"
                + (f" ({' | '.join(why)})" if why else " (no connection registered)")
            )

        if effect is Effect.EXTERNAL or req.consent_required:
            what = "external side effect" if effect is Effect.EXTERNAL else "consent-required operation"
            if effect is Effect.EXTERNAL and self.policy.external_side_effects == "deny":
                problems.append(f"{what} '{req.operation}' is denied by integrations.external_side_effects")
            else:
                approvals.append(
                    ApprovalNeed(
                        key=f"side_effect:{task_id}:{idx}:{req.operation}@{account.id}",
                        kind="side_effect",
                        detail=f"{what} '{req.operation}' on account '{r.trace.account_label}'",
                    )
                )
        if problems:
            r.trace.problem = "; ".join(problems)
        return r

    def _resolve_account(
        self,
        idx: int,
        req: IntegrationRequirement,
        snap: IntegrationSnapshot,
        selections: Mapping[int, str],
        trace: IntegrationTrace,
    ) -> tuple[IntegrationAccount | None, str | None, str | None]:
        ws = snap.workspace
        if req.account is not None:  # explicit selection always wins, and is never substituted
            account, how = self._by_selector(req.account, req.service, snap)
            if account is None:
                return (
                    None,
                    None,
                    f"no {req.service} account matches '{_show_selector(req.account)}' in workspace '{ws}'; "
                    "Saarathi never substitutes another account",
                )
            return account, how, None
        chosen = selections.get(idx)
        if chosen is not None:
            account = snap.accounts.get(chosen)
            if account is None or account.service != req.service:
                return None, None, f"selected account '{chosen}' is not a {req.service} account in workspace '{ws}'"
            return account, "selection", None
        default = snap.defaults.get(req.service)
        if default is not None and default in snap.accounts:
            return snap.accounts[default], "default", None
        active = {c.account_id for c in snap.connections if c.status is ConnectionStatus.ACTIVE}
        accessible = sorted(
            (
                a
                for a in snap.accounts.values()
                if a.service == req.service and a.identity_status is not IdentityStatus.REVOKED and a.id in active
            ),
            key=lambda a: a.id,
        )
        if not accessible:
            return None, None, f"no accessible {req.service} account in workspace '{ws}'"
        if len(accessible) == 1:
            return accessible[0], "only_account", None
        trace.account_choices.extend(
            AccountOption(requirement=idx, account=a.id, label=snap.label(a), identity_status=a.identity_status)
            for a in accessible
        )
        return None, None, None

    @staticmethod
    def _by_selector(
        selector: str, service: str, snap: IntegrationSnapshot
    ) -> tuple[IntegrationAccount | None, str | None]:
        account = snap.accounts.get(selector)
        if account is not None and account.service == service:
            return account, "id"
        alias_target = snap.aliases.get((service, selector))
        if alias_target is not None and alias_target in snap.accounts:
            return snap.accounts[alias_target], "alias"
        digest = identity_digest(snap.workspace, service, selector)
        for account in snap.accounts.values():
            if account.service == service and account.identity_digest == digest:
                return account, "identity"
        return None, None

    def _identity(
        self, req: IntegrationRequirement, account: IntegrationAccount, label: str
    ) -> tuple[str | None, ApprovalNeed | None]:
        if not req.identity_sensitive:
            return None, None
        status = account.identity_status
        if status is IdentityStatus.VERIFIED:
            verified_at = _parse(account.verified_at)
            if verified_at is None or self.now - verified_at > self.max_age:
                return f"identity verification of account '{label}' is stale; re-verify it", None
            return None, None
        if status is IdentityStatus.USER_DECLARED:
            mode = self.policy.user_declared_identity
            if mode is TransferMode.ALLOW:
                return None, None
            if mode is TransferMode.DENY:
                return (
                    f"identity of account '{label}' is only user-declared and "
                    "integrations.user_declared_identity is deny",
                    None,
                )
            return None, ApprovalNeed(
                key=f"identity:{account.id}",
                kind="identity",
                detail=f"account '{label}' identity is user-declared, not provider-verified; confirm it for this run",
            )
        if status is IdentityStatus.UNVERIFIED:
            return (
                f"identity of account '{label}' is unverified; identity-sensitive routing fails closed "
                "(declare it with `integrations register --identity` or verify it through a supported interface)",
                None,
            )
        return f"identity of account '{label}' is {status}", None

    def _connection_reasons(
        self, req: IntegrationRequirement, conn: IntegrationConnection, worker: WorkerInstance | None
    ) -> list[str]:
        reasons: list[str] = []
        if worker is None or worker.lifecycle is not WorkerLifecycle.ACTIVE:
            reasons.append(f"worker '{conn.worker_id}' is not active")
        if conn.status is not ConnectionStatus.ACTIVE:
            reasons.append(f"is {conn.status}")
        expires = _parse(conn.expires_at)
        if expires is not None and expires <= self.now:
            reasons.append(f"expired at {conn.expires_at}")
        caps = [c for c in conn.capabilities if c.operation == req.operation]
        if req.resource is not None:
            caps = [c for c in caps if c.resource_scope == "*" or fnmatchcase(req.resource, c.resource_scope)]
        else:
            caps = [c for c in caps if c.resource_scope == "*"]
        if not caps:
            permitted = sorted({c.operation for c in conn.capabilities}) or ["nothing"]
            reasons.append(f"does not permit {req.operation} (permits {', '.join(permitted)})")
            return reasons
        cap = caps[0]
        if self.policy.require_verified_integration and cap.evidence not in VERIFIED_EVIDENCE:
            reasons.append(f"{req.operation} is only {cap.evidence}, not verified (run `saarathi integrations verify`)")
        checked = _parse(cap.checked_at)
        if cap.evidence in VERIFIED_EVIDENCE and (checked is None or self.now - checked > self.max_age):
            reasons.append(f"verification of {req.operation} is stale (older than {self.max_age})")
        return reasons
