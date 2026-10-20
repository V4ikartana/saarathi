"""Editing a project's policy from the app or `saarathi policy set`: validated, commented, backed up."""

import pytest
import yaml

from saarathi.core.contracts import ReasoningTier
from saarathi.core.policy import DEFAULT_POLICY_YAML
from saarathi.policy_edit import PolicyEditError, plan_policy_edit, write_policy
from tests.unit.test_router import model

OPUS = model("claude-code", "opus", "anthropic", ReasoningTier.FRONTIER)
SOL = model("codex-cli", "gpt-6-sol", "openai")
MODELS = [OPUS, SOL, model("codex-cli", "default", "openai")]


def edit(changes, text=DEFAULT_POLICY_YAML):
    return plan_policy_edit(text, changes, MODELS)


def test_changing_the_lead_declares_a_discovered_model_and_keeps_comments():
    result = edit({"lead": "codex-cli:gpt-6-sol"})
    lead = result.policy.orchestration.lead
    assert (lead.adapter, lead.model) == ("codex-cli", "gpt-6-sol")
    declared = {m.id: m.reasoning_tier for m in result.policy.providers["codex-cli"].models}
    assert declared["gpt-6-sol"] is ReasoningTier.BALANCED, "the catalogue tier is written with the declaration"
    assert "# The engineering lead runs intake, planning and final review." in result.text, "comments survive"
    assert result.summary == ["orchestration.lead: claude-code:opus → codex-cli:gpt-6-sol"]
    assert any("plan and review every future run" in w for w in result.warnings)


def test_loosening_changes_come_back_as_warnings():
    loosen = {"permissions.allow_network": True, "approvals.max_preset": "bypass", "routing.min_remaining_percent": 0}
    result = edit(loosen)
    assert len(result.warnings) == 3
    assert any("network" in w for w in result.warnings) and any("without you" in w for w in result.warnings)
    tightening = edit({"routing.max_parallel_workers": 1})
    assert tightening.warnings == [] and tightening.summary == ["routing.max_parallel_workers: 2 → 1"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"billing.api_enabled": True}, "cannot be changed from the app"),
        ({"routing.max_parallel_workers": 99}, "from 1 to 16"),
        ({"lead": "codex-cli:o9"}, "unknown model"),
        ({"providers.claude-code.enabled": False}, "lead's adapter claude-code cannot be disabled"),
        ({"routing.allowed_providers": ["openai"]}, "would not be allowed"),
        ({"approvals.max_preset": "anything"}, "must be one of"),
        ({"routing.max_parallel_workers": 2}, "already the project's policy"),
    ],
)
def test_bad_changes_are_refused_before_writing(changes, message):
    with pytest.raises(PolicyEditError, match=message):
        edit(changes)


def test_adding_an_installed_cli_allows_its_provider():
    text = DEFAULT_POLICY_YAML.replace("[anthropic, openai, google]", "[anthropic, openai]")
    text = text.split("  antigravity-cli:")[0]
    result = edit({"providers.add": "antigravity-cli"}, text)
    assert "antigravity-cli" in result.policy.providers
    assert "google" in result.policy.routing.allowed_providers and "google/*" in result.policy.routing.allowed_models
    assert any("google" in w for w in result.warnings)


def test_writing_keeps_a_backup(tmp_path):
    path = tmp_path / "policies.yaml"
    path.write_text(DEFAULT_POLICY_YAML)
    result = edit({"execution.max_attempts": 3})
    backup = write_policy(path, result.text)
    assert backup.read_text() == DEFAULT_POLICY_YAML
    assert yaml.safe_load(path.read_text())["execution"]["max_attempts"] == 3
