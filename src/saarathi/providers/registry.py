"""Builds configured adapters and exposes the model catalogue the router works from."""

from __future__ import annotations

from saarathi import user_settings
from saarathi.core.contracts import ModelDescriptor, ReasoningTier
from saarathi.core.errors import ConfigError
from saarathi.core.policy import Policy, ProviderConfig
from saarathi.providers.antigravity_cli import AntigravityCliAdapter
from saarathi.providers.base import ProviderAdapter
from saarathi.providers.claude_cli import ClaudeCodeAdapter
from saarathi.providers.codex_cli import CodexCliAdapter
from saarathi.providers.fake import FAKE_ENV_FLAG, FakeAdapter, fake_providers_allowed
from saarathi.providers.local_openai import LocalOpenAIAdapter

ADAPTER_TYPES: dict[str, type[ProviderAdapter]] = {
    "antigravity-cli": AntigravityCliAdapter,
    "claude-code": ClaudeCodeAdapter,
    "codex-cli": CodexCliAdapter,
    "local-openai": LocalOpenAIAdapter,
}


def build_adapter(name: str, config: ProviderConfig) -> ProviderAdapter:
    type_name = config.type or name
    if type_name == "fake":
        if not fake_providers_allowed():
            raise ConfigError(
                f"provider '{name}' uses the test-only fake adapter",
                hint=f"set {FAKE_ENV_FLAG}=1 only in tests or demos",
            )
        return FakeAdapter(name, config)
    if config.provider is not None:
        raise ConfigError(f"providers.{name}.provider can only be set for fake adapters")
    cls = ADAPTER_TYPES.get(type_name)
    if cls is None:
        raise ConfigError(
            f"unknown provider adapter type '{type_name}' for '{name}'",
            hint=f"supported: {', '.join(sorted(ADAPTER_TYPES))}",
        )
    return cls(name, config)


class ProviderRegistry:
    def __init__(self, adapters: dict[str, ProviderAdapter]) -> None:
        self.adapters = adapters

    @classmethod
    def from_policy(cls, policy: Policy) -> ProviderRegistry:
        return cls({name: build_adapter(name, cfg) for name, cfg in policy.providers.items()})

    def get(self, name: str) -> ProviderAdapter:
        try:
            return self.adapters[name]
        except KeyError as exc:
            raise ConfigError(f"provider adapter '{name}' is not configured") from exc

    def models(self, *, chosen_tiers: bool = True) -> list[ModelDescriptor]:
        """Every model of every adapter. Models the person turned off are listed but unavailable, which every
        routing and lead check already refuses. A tier the person chose replaces the model's default tier."""
        disabled = user_settings.disabled_models()
        tiers = user_settings.model_tiers() if chosen_tiers else {}
        out: list[ModelDescriptor] = []
        for adapter in self.adapters.values():
            for m in adapter.list_models():
                chosen = tiers.get(m.candidate_id)
                if chosen is not None and chosen != m.capabilities.reasoning_tier:
                    caps = m.capabilities.model_copy(update={"reasoning_tier": ReasoningTier(chosen)})
                    notes = f"{user_settings.TIER_SOURCE} (default {m.capabilities.reasoning_tier.value}); "
                    meta = m.metadata.model_copy(update={"notes": notes + (m.metadata.notes or "")})
                    m = m.model_copy(update={"capabilities": caps, "metadata": meta})
                if m.candidate_id in disabled:
                    m = m.model_copy(update={"available": False, "unavailable_reason": user_settings.DISABLED_REASON})
                out.append(m)
        return out
