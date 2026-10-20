import pytest

from saarathi.core.contracts import (
    Complexity,
    ModelCapabilities,
    ModelDescriptor,
    ModelMetadata,
    ModelPricing,
    ReasoningTier,
    Transport,
)
from saarathi.core.policy import parse_policy
from saarathi.routing.router import RoutingRequest, RuntimeLoad, resolve_lead, route

CAPS = ("code_generation", "workspace_write", "structured_output")


def model(
    adapter,
    mid,
    provider,
    tier=ReasoningTier.UNKNOWN,
    metered=False,
    available=True,
    structured=True,
    max_parallel=1,
):
    return ModelDescriptor(
        id=mid,
        provider=provider,
        adapter=adapter,
        transport=Transport.CLI,
        available=available,
        unavailable_reason=None if available else "binary missing",
        capabilities=ModelCapabilities(
            code_generation=True,
            repository_tools=True,
            workspace_write=True,
            structured_output=structured if structured else None,
            reasoning_tier=tier,
        ),
        constraints={"max_parallel": max_parallel},
        pricing=ModelPricing(metered=metered),
        metadata=ModelMetadata(source="test"),
    )


def policy(**routing_overrides):
    raw = {
        "orchestration": {"lead": {"adapter": "claude-code", "model": "opus"}},
        "routing": {
            "allowed_providers": ["anthropic", "openai"],
            "allowed_models": ["anthropic/*", "openai/*"],
            **routing_overrides,
        },
        "providers": {
            "claude-code": {"models": [{"id": "opus", "reasoning_tier": "frontier"}]},
            "codex-cli": {"models": [{"id": "default"}]},
        },
    }
    return parse_policy(raw)


OPUS = model("claude-code", "opus", "anthropic", ReasoningTier.FRONTIER)
SONNET = model("claude-code", "sonnet", "anthropic", ReasoningTier.BALANCED)
CODEX = model("codex-cli", "default", "openai")


def req(complexity=Complexity.STANDARD, **kw):
    return RoutingRequest(task_id="T-001", required_capabilities=CAPS, complexity=complexity, **kw)


def test_denied_model_never_chosen():
    d = route(req(Complexity.COMPLEX), [OPUS, CODEX], policy(denied_models=["anthropic/opus"]))
    assert d.chosen == "codex-cli:default"
    assert any(r.candidate == "claude-code:opus" and "denied" in r.reasons[0] for r in d.rejected)


def test_disallowed_provider_rejected():
    d = route(req(), [OPUS], policy(allowed_providers=["openai"]))
    assert d.chosen is None and "no candidate" in d.reason


def test_unknown_capability_not_treated_as_supported():
    unknown = model("codex-cli", "default", "openai", structured=False)
    d = route(req(), [unknown], policy())
    assert d.chosen is None
    assert "structured_output" in d.rejected[0].reasons[0]


def test_metered_blocked_when_api_disabled():
    metered = model("claude-code", "opus", "anthropic", ReasoningTier.FRONTIER, metered=True)
    d = route(req(), [metered], policy())
    assert d.chosen is None and "billing.api_enabled" in d.rejected[0].reasons[0]


def test_complex_prefers_frontier_routine_prefers_cheaper_tier():
    assert route(req(Complexity.COMPLEX), [OPUS, SONNET], policy()).chosen == "claude-code:opus"
    assert route(req(Complexity.ROUTINE), [OPUS, SONNET], policy()).chosen == "claude-code:sonnet"


def test_unknown_tier_does_not_beat_matching_known_tier_for_complex_work():
    assert route(req(Complexity.COMPLEX), [CODEX, OPUS], policy()).chosen == "claude-code:opus"


def test_busy_candidates_wait_instead_of_falling_back_to_ineligible():
    load = RuntimeLoad(in_flight={"codex-cli:default": 1}, providers_in_flight={"openai": 1})
    d = route(req(), [CODEX, model("claude-code", "opus", "anthropic", available=False)], policy(), load)
    assert d.chosen is None and d.busy == ["codex-cli:default"] and "capacity" in d.reason


def test_spread_prefers_idle_provider():
    load = RuntimeLoad(in_flight={"claude-code:opus": 1}, providers_in_flight={"anthropic": 1})
    opus2 = model("claude-code", "opus", "anthropic", ReasoningTier.FRONTIER, max_parallel=2)
    d = route(req(Complexity.ROUTINE), [opus2, CODEX], policy(), load)
    assert d.chosen == "codex-cli:default"
    assert d.alternatives == ["claude-code:opus"]


def test_previously_failed_candidate_deprioritised():
    d = route(req(previously_failed=frozenset({"claude-code:opus"})), [OPUS, CODEX], policy())
    assert d.chosen == "codex-cli:default"


def test_decision_is_explainable():
    d = route(req(), [OPUS, CODEX], policy())
    assert d.reason and d.score_breakdown["total"] > 0 and d.decision_id.startswith("rd-")


def test_lead_is_pinned_and_never_silently_substituted():
    lead, problems = resolve_lead([OPUS, CODEX], policy())
    assert lead is OPUS and not problems
    down = model("claude-code", "opus", "anthropic", ReasoningTier.FRONTIER, available=False)
    lead, problems = resolve_lead([down, CODEX], policy())
    assert lead is None and "unavailable" in problems[0]


def test_cooling_adapter_is_skipped_and_reported():
    load = RuntimeLoad(cooldowns={"codex-cli": ("cooling_down", "2026-10-19T10:00:00.000+00:00", "429")})
    d = route(req(), [CODEX, OPUS], policy(), load)
    assert d.chosen == "claude-code:opus" and d.cooling == ["codex-cli:default"]
    only = route(req(), [CODEX], policy(), load)
    assert only.chosen is None and "limited by its provider" in only.reason
    assert only.available_at == "2026-10-19T10:00:00.000+00:00"


@pytest.mark.parametrize("override", [{"mode": "hybrid"}, {"mode": "api"}, {"billing": {"api_enabled": True}}])
def test_v01_policy_rejects_metered_modes(override):
    from saarathi.core.errors import ConfigError

    raw = {
        "orchestration": {"lead": {"adapter": "claude-code", "model": "opus"}},
        "routing": {"allowed_providers": ["anthropic"], "allowed_models": ["anthropic/*"]},
        "providers": {"claude-code": {"models": [{"id": "opus"}]}},
        **override,
    }
    with pytest.raises(ConfigError, match="ADR-0011"):
        parse_policy(raw)


def test_preference_adds_an_explained_bonus_without_changing_eligibility():
    plain = route(req(Complexity.COMPLEX), [OPUS, CODEX], policy())
    assert plain.chosen == "claude-code:opus"
    preferred = route(req(Complexity.COMPLEX, preferred="codex-cli:default"), [OPUS, CODEX], policy())
    assert preferred.chosen == "codex-cli:default" and preferred.score_breakdown["preference"] > 0
    denied = route(req(preferred="codex-cli:default"), [OPUS, CODEX], policy(denied_models=["openai/default"]))
    assert denied.chosen == "claude-code:opus", "a preference never makes a filtered model eligible"


def test_preference_does_not_outweigh_a_recent_failure():
    d = route(
        req(Complexity.COMPLEX, preferred="codex-cli:default", previously_failed=frozenset({"codex-cli:default"})),
        [OPUS, CODEX],
        policy(),
    )
    assert d.chosen == "claude-code:opus"


def test_pin_restricts_to_one_eligible_candidate():
    d = route(req(Complexity.COMPLEX, pinned="codex-cli:default"), [OPUS, CODEX], policy())
    assert d.chosen == "codex-cli:default" and d.alternatives == []
    assert any(r.candidate == "claude-code:opus" and "pinned" in r.reasons[0] for r in d.rejected)


def test_pinned_ineligible_model_blocks_with_its_reasons():
    down = model("codex-cli", "default", "openai", available=False)
    d = route(req(pinned="codex-cli:default"), [OPUS, down], policy())
    assert d.chosen is None and "pinned model codex-cli:default is not eligible" in d.reason
    assert "binary missing" in d.reason, "the pinned model's own hard-filter reasons are shown"
    missing = route(req(pinned="codex-cli:o9"), [OPUS], policy())
    assert missing.chosen is None and "not in the model registry" in missing.reason


def test_a_model_near_its_plan_limit_is_skipped_until_the_window_resets():
    load = RuntimeLoad(near_limit={"claude-code:opus": ("2026-10-21T00:00:00+00:00", "has 2% of its weekly plan left")})
    d = route(req(Complexity.COMPLEX), [OPUS, CODEX], policy(), load)
    assert d.chosen == "codex-cli:default"
    assert "claude-code:opus" in d.cooling
    alone = route(req(Complexity.COMPLEX), [OPUS], policy(), load)
    assert alone.chosen is None and "2% of its weekly plan left" in alone.reason
    assert alone.available_at == "2026-10-21T00:00:00+00:00", "the task can wait for the reset"
