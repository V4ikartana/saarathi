"""Per-run preferences a person chooses for a session: approval preset and worker model (ADR-0014).

Preferences are recorded as run controls when the run starts, so the engine, the CLI and the desktop app read the
same values. They only ever narrow or delegate within policy: the policy's ``approvals.max_preset`` caps the preset,
and a pinned model still has to pass every routing hard filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from saarathi.core.contracts import ModelDescriptor, Plan, ReviewVerdict, Risk
from saarathi.core.errors import ConfigError, PolicyViolation
from saarathi.core.policy import ApprovalPreset, Policy
from saarathi.storage.store import StateStore

APPROVALS_CONTROL = "pref.approvals"
MODEL_CONTROL = "pref.model"
MODEL_MODE_CONTROL = "pref.model_mode"


PRESET_LABELS = {
    ApprovalPreset.ASK.value: "Ask before actions",
    ApprovalPreset.AUTO_SAFE.value: "Auto-approve safe actions",
    ApprovalPreset.BYPASS.value: "Bypass supported approvals",
}


class ModelMode(StrEnum):
    PREFER = "prefer"  # a score bonus among eligible candidates
    PIN = "pin"  # only this candidate, and only if it is eligible


@dataclass(frozen=True)
class RunPreferences:
    approvals: ApprovalPreset = ApprovalPreset.ASK
    model: str | None = None  # candidate id, "adapter:model"
    model_mode: ModelMode = ModelMode.PREFER

    @classmethod
    def parse(
        cls, approvals: str | None = None, model: str | None = None, model_mode: str | None = None
    ) -> RunPreferences:
        try:
            return cls(
                approvals=ApprovalPreset(approvals or ApprovalPreset.ASK),
                model=model or None,
                model_mode=ModelMode(model_mode or ModelMode.PREFER),
            )
        except ValueError as exc:
            raise ConfigError(f"invalid preference: {exc}") from exc

    def validate(self, policy: Policy, models: list[ModelDescriptor]) -> None:
        """Refuse preferences the policy does not allow, before any run uses them."""
        cap = policy.approvals.max_preset
        if self.approvals.rank > cap.rank:
            raise PolicyViolation(
                f"approval preset '{self.approvals}' exceeds this project's approvals.max_preset '{cap}'",
                hint="the policy owner can raise approvals.max_preset in .saarathi/policies.yaml",
            )
        if self.model is None:
            return
        known = {m.candidate_id: m for m in models}
        model = known.get(self.model)
        if model is None:
            raise ConfigError(f"unknown model '{self.model}'", hint="choose one from `saarathi models list`")
        allowed, why = policy.routing.model_allowed(model.provider, model.key)
        if not allowed:
            raise PolicyViolation(f"model '{self.model}' is not allowed: {why}")

    def record(self, store: StateStore, run_id: str, *, actor: str) -> None:
        store.set_control(run_id, APPROVALS_CONTROL, self.approvals.value, actor=actor)
        if self.model:
            store.set_control(run_id, MODEL_CONTROL, self.model, actor=actor)
            store.set_control(run_id, MODEL_MODE_CONTROL, self.model_mode.value, actor=actor)

    @classmethod
    def load(cls, store: StateStore, run_id: str) -> RunPreferences:
        values = store.controls(run_id, "pref.")
        return cls.parse(values.get(APPROVALS_CONTROL), values.get(MODEL_CONTROL), values.get(MODEL_MODE_CONTROL))


def effective_preset(prefs: RunPreferences, policy: Policy) -> ApprovalPreset:
    """The run's preset, capped by the current policy (a policy lowered after the run started still wins)."""
    cap = policy.approvals.max_preset
    return prefs.approvals if prefs.approvals.rank <= cap.rank else cap


def plan_auto_approval(preset: ApprovalPreset, plan: Plan) -> tuple[bool, str]:
    """Whether the plan may start without a person, and why. Plans reach this point only after policy checks."""
    if preset is ApprovalPreset.ASK:
        return False, "the session asks before every plan"
    if preset is ApprovalPreset.BYPASS:
        return True, "the plan passed policy checks and the session bypasses plan approval"
    risky = [t.id for t in plan.tasks if t.risk is Risk.HIGH]
    if risky:
        return False, f"high-risk tasks need a person: {', '.join(risky)}"
    external = [t.id for t in plan.tasks if t.integrations]
    if external:
        return False, f"tasks that use integrations need a person: {', '.join(external)}"
    return True, "every task is low or medium risk and uses no integrations"


def review_auto_approval(preset: ApprovalPreset, review: ReviewVerdict, integration_passed: bool) -> tuple[bool, str]:
    """Whether the final review may complete the run without a person, and why."""
    if preset is not ApprovalPreset.BYPASS:
        return False, "only the bypass preset completes a run without you"
    if review.decision != "approve":
        return False, "the lead requested changes"
    if not integration_passed:
        return False, "integration checks did not pass"
    return True, "the lead approved and integration checks passed"
