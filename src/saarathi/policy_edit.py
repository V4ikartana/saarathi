"""Edit a project's policy from the desktop app or ``saarathi policy set`` safely.

Only an allowlist of settings can be changed. The file is edited in place with its comments kept, the result is
validated by the same parser the engine uses before anything is written, the previous file is backed up, and every
change is recorded as a ``policy.updated`` event. Changes that loosen what agents may do come back as warnings, so the
person confirms them knowingly. Running drivers keep the policy they started with; changes apply from the next start.
"""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from saarathi.core.contracts import ModelDescriptor
from saarathi.core.errors import ConfigError
from saarathi.core.policy import ApprovalPreset, Policy, parse_policy
from saarathi.providers.model_catalog import classify

BACKUP_DIR = "policy-backups"
KEEP_BACKUPS = 20

# Adapters the app can add to a project: type -> (provider, starter models with tiers).
ADDABLE: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "claude-code": ("anthropic", [("opus", "frontier"), ("sonnet", "balanced"), ("haiku", "economy")]),
    "codex-cli": ("openai", [("default", "unknown")]),
    "antigravity-cli": ("google", [("default", "unknown")]),
    "local-openai": ("local", []),  # models are discovered from the local server
}

TRANSFER_MODES = ("deny", "require_approval", "allow")
SIDE_EFFECTS = ("deny", "require_approval")


class PolicyEditError(ConfigError):
    """A requested change is not allowed or would leave an invalid policy; nothing was written."""


@dataclass
class PolicyEdit:
    text: str  # the new file contents
    summary: list[str] = field(default_factory=list)  # "routing.max_parallel_workers: 2 → 3"
    warnings: list[str] = field(default_factory=list)  # changes that loosen what agents may do
    policy: Policy | None = None


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 120
    return y


def _flow_map(**items: Any) -> CommentedMap:
    m = CommentedMap(items)
    m.fa.set_flow_style()
    return m


def _section(doc: CommentedMap, name: str) -> CommentedMap:
    if not isinstance(doc.get(name), dict):
        doc[name] = CommentedMap()
    section: CommentedMap = doc[name]
    return section


def _bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise PolicyEditError(f"{key} must be true or false")


def _int(value: Any, key: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PolicyEditError(f"{key} must be a whole number from {low} to {high}")
    return value


def _choice(value: Any, key: str, options: tuple[str, ...]) -> str:
    if value not in options:
        raise PolicyEditError(f"{key} must be one of: {', '.join(options)}")
    return str(value)


def _candidate(value: Any, key: str, models: Mapping[str, ModelDescriptor]) -> ModelDescriptor:
    model = models.get(str(value))
    if model is None:
        raise PolicyEditError(f"{key}: unknown model '{value}'", hint="choose one from `saarathi models list`")
    if not model.capabilities.structured_output:
        raise PolicyEditError(f"{key}: {value} cannot return structured output, which the lead needs")
    return model


def _declare_model(doc: CommentedMap, model: ModelDescriptor) -> None:
    """The policy requires the lead to be declared under its adapter; declare a discovered model with its tier."""
    providers = _section(doc, "providers")
    if not isinstance(providers.get(model.adapter), dict):
        raise PolicyEditError(f"adapter {model.adapter} is not configured in this project")
    adapter = providers[model.adapter]
    models = adapter.get("models")
    if not isinstance(models, list):
        adapter["models"] = models = CommentedSeq()
    if any(isinstance(m, dict) and m.get("id") == model.id for m in models):
        return
    found = classify(model.id)
    tier = found.tier.value if found else model.capabilities.reasoning_tier.value
    models.append(_flow_map(id=model.id, reasoning_tier=tier))


def _allow_provider(routing: CommentedMap, provider: str) -> None:
    allowed = routing.get("allowed_providers")
    if not isinstance(allowed, list):
        routing["allowed_providers"] = allowed = CommentedSeq()
    if provider not in allowed:
        allowed.append(provider)
    patterns = routing.get("allowed_models")
    if not isinstance(patterns, list):
        routing["allowed_models"] = patterns = CommentedSeq()
    if not any(str(p).startswith(f"{provider}/") for p in patterns):
        patterns.append(f"{provider}/*")


def plan_policy_edit(
    text: str, changes: Mapping[str, Any], models: list[ModelDescriptor], *, source: str = "<policy>"
) -> PolicyEdit:
    """Apply ``changes`` to policy ``text`` in memory. Raises PolicyEditError; never writes."""
    if not changes:
        raise PolicyEditError("no changes requested")
    y = _yaml()
    doc = y.load(text)
    if not isinstance(doc, CommentedMap):
        raise PolicyEditError("the policy file is not a mapping")
    before = parse_policy(yaml.safe_load(text), source=source)
    by_id = {m.candidate_id: m for m in models}
    edit = PolicyEdit(text="")

    def note(key: str, old: Any, new: Any) -> None:
        if old != new:
            edit.summary.append(f"{key}: {old} → {new}")

    handlers: dict[str, Callable[[Any], None]] = {}

    def lead(value: Any) -> None:
        model = _candidate(value, "orchestration.lead", by_id)
        _declare_model(doc, model)
        _section(doc, "orchestration")["lead"] = _flow_map(adapter=model.adapter, model=model.id)
        old = f"{before.orchestration.lead.adapter}:{before.orchestration.lead.model}"
        note("orchestration.lead", old, model.candidate_id)
        if old != model.candidate_id:
            edit.warnings.append(f"{model.candidate_id} will plan and review every future run in this project.")

    def substitute(value: Any) -> None:
        orch = _section(doc, "orchestration")
        old = before.orchestration.lead_substitute
        old_id = f"{old.adapter}:{old.model}" if old else None
        if value in (None, "", "none"):
            orch["lead_substitute"] = None
            note("orchestration.lead_substitute", old_id, None)
            return
        model = _candidate(value, "orchestration.lead_substitute", by_id)
        _declare_model(doc, model)
        orch["lead_substitute"] = _flow_map(adapter=model.adapter, model=model.id)
        note("orchestration.lead_substitute", old_id, model.candidate_id)

    def max_preset(value: Any) -> None:
        preset = ApprovalPreset(_choice(value, "approvals.max_preset", tuple(p.value for p in ApprovalPreset)))
        _section(doc, "approvals")["max_preset"] = preset.value
        note("approvals.max_preset", before.approvals.max_preset.value, preset.value)
        if preset.rank > before.approvals.max_preset.rank:
            if preset is ApprovalPreset.BYPASS:
                edit.warnings.append("Sessions may let plans start and runs complete without you approving them.")
            else:
                edit.warnings.append("Sessions may let low-risk plans start without you approving them.")

    def providers_allowed(value: Any) -> None:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value) or not value:
            raise PolicyEditError("routing.allowed_providers must be a non-empty list of provider names")
        routing = _section(doc, "routing")
        routing["allowed_providers"] = CommentedSeq(value)
        for provider in value:
            _allow_provider(routing, provider)
        note("routing.allowed_providers", before.routing.allowed_providers, value)

    Warn = Callable[[Any, Any], str | None]

    def setter(
        section: str, key: str, convert: Callable[[Any, str], Any], warn: Warn | None = None
    ) -> Callable[[Any], None]:
        def apply(value: Any) -> None:
            full = f"{section}.{key}"
            new = convert(value, full)
            old = getattr(getattr(before, section), key)
            old = old.value if hasattr(old, "value") else old
            _section(doc, section)[key] = new
            note(full, old, new)
            message = warn(old, new) if warn else None
            if message and old != new:
                edit.warnings.append(message)

        return apply

    handlers["lead"] = lead
    handlers["orchestration.lead"] = lead
    handlers["lead_substitute"] = substitute
    handlers["orchestration.lead_substitute"] = substitute
    handlers["approvals.max_preset"] = max_preset
    handlers["routing.allowed_providers"] = providers_allowed
    handlers["routing.max_parallel_workers"] = setter("routing", "max_parallel_workers", lambda v, k: _int(v, k, 1, 16))
    handlers["routing.min_remaining_percent"] = setter(
        "routing",
        "min_remaining_percent",
        lambda v, k: _int(v, k, 0, 50),
        lambda old, new: None if new else "Saarathi will stop avoiding providers that are nearly out of plan usage.",
    )
    handlers["routing.spread_across_providers"] = setter("routing", "spread_across_providers", _bool)
    handlers["permissions.allow_network"] = setter(
        "permissions",
        "allow_network",
        _bool,
        lambda old, new: "Workers will be able to reach the network." if new else None,
    )
    handlers["permissions.allow_dependency_install"] = setter(
        "permissions",
        "allow_dependency_install",
        _bool,
        lambda old, new: "Workers will be allowed to install dependencies." if new else None,
    )
    handlers["execution.max_attempts"] = setter("execution", "max_attempts", lambda v, k: _int(v, k, 1, 5))
    handlers["execution.task_timeout_seconds"] = setter(
        "execution", "task_timeout_seconds", lambda v, k: _int(v, k, 60, 86_400)
    )
    handlers["execution.verification_timeout_seconds"] = setter(
        "execution", "verification_timeout_seconds", lambda v, k: _int(v, k, 30, 86_400)
    )
    handlers["integrations.external_side_effects"] = setter(
        "integrations",
        "external_side_effects",
        lambda v, k: _choice(v, k, SIDE_EFFECTS),
        lambda old, new: (
            "Agents may send, delete or share through integrations, after your approval."
            if new == "require_approval"
            else None
        ),
    )
    handlers["integrations.cross_provider_data_transfer"] = setter(
        "integrations",
        "cross_provider_data_transfer",
        lambda v, k: _choice(v, k, TRANSFER_MODES),
        lambda old, new: "Results may move between providers without asking you." if new == "allow" else None,
    )
    handlers["integrations.user_declared_identity"] = setter(
        "integrations",
        "user_declared_identity",
        lambda v, k: _choice(v, k, TRANSFER_MODES),
        lambda old, new: "Accounts you declare will be used without per-run approval." if new == "allow" else None,
    )
    handlers["integrations.require_verified_integration"] = setter(
        "integrations",
        "require_verified_integration",
        _bool,
        lambda old, new: None if new else "Integrations will count even when no provider has verified them.",
    )

    for key, value in changes.items():
        if key.startswith("providers.") and key.endswith(".enabled"):
            name = key[len("providers.") : -len(".enabled")]
            providers = _section(doc, "providers")
            if not isinstance(providers.get(name), dict):
                raise PolicyEditError(f"adapter {name} is not configured in this project")
            enabled = _bool(value, key)
            old = before.providers[name].enabled
            providers[name]["enabled"] = enabled
            note(key, old, enabled)
        elif key == "providers.add":
            adapter_type = _choice(value, key, tuple(ADDABLE))
            providers = _section(doc, "providers")
            if adapter_type in providers:
                raise PolicyEditError(f"{adapter_type} is already configured in this project")
            provider, starters = ADDABLE[adapter_type]
            models = CommentedSeq(_flow_map(id=mid, reasoning_tier=tier) for mid, tier in starters)
            providers[adapter_type] = CommentedMap(enabled=True, models=models)
            _allow_provider(_section(doc, "routing"), provider)
            edit.summary.append(f"providers: add {adapter_type} ({provider})")
            edit.warnings.append(f"Work in this project may be sent to {provider} through {adapter_type}.")
        elif key in handlers:
            handlers[key](value)
        else:
            hint = "edit .saarathi/policies.yaml instead"
            raise PolicyEditError(f"'{key}' cannot be changed from the app", hint=hint)

    out = io.StringIO()
    y.dump(doc, out)
    edit.text = out.getvalue()
    try:
        edit.policy = parse_policy(yaml.safe_load(edit.text), source=source)
    except ConfigError as exc:
        raise PolicyEditError(exc.message, hint="nothing was saved") from exc
    _check_lead_reachable(edit.policy, by_id)
    if not edit.summary:
        raise PolicyEditError("these values are already the project's policy")
    return edit


def _check_lead_reachable(policy: Policy, models: Mapping[str, ModelDescriptor]) -> None:
    """Refuse a policy whose own rules would keep its lead from running."""
    ref = policy.orchestration.lead
    if not policy.providers[ref.adapter].enabled:
        raise PolicyEditError(f"the lead's adapter {ref.adapter} cannot be disabled", hint="choose another lead first")
    model = models.get(f"{ref.adapter}:{ref.model}")
    if model is not None:
        allowed, why = policy.routing.model_allowed(model.provider, model.key)
        if not allowed:
            raise PolicyEditError(f"the lead {model.candidate_id} would not be allowed: {why}")


def write_policy(path: Path, text: str) -> Path:
    """Back up the current file, then replace it atomically. Returns the backup's path."""
    backups = path.parent / BACKUP_DIR
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backups / f"policies-{stamp}.yaml"
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    for old in sorted(backups.glob("policies-*.yaml"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".policies-", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return backup
