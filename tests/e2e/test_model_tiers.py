"""Tiers a person chooses in Settings replace a model's default tier everywhere: routing, the lead check, the app."""

import pytest
from typer.testing import CliRunner

from saarathi import user_settings
from saarathi.bridge.service import BridgeService
from saarathi.cli.main import app
from saarathi.core.contracts import ReasoningTier
from saarathi.orchestration.project import Project
from saarathi.routing.router import _tier_fit
from tests.e2e.helpers import GOOD_T1, GOOD_T2, init_project, make_script


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")
    monkeypatch.setenv("SAARATHI_DATA_DIR", str(tmp_path / "data"))


def setup(repo, tmp_path):
    script = make_script(tmp_path / "s.json", {"T-001": [GOOD_T1], "T-002": [GOOD_T2]})
    init_project(repo, script, tmp_path / "rec.jsonl")
    return Project.load(repo)


def tiers(project):
    return {m.candidate_id: m.capabilities.reasoning_tier for m in project.registry.models()}


def test_a_chosen_tier_replaces_the_default_and_can_be_reset(repo, tmp_path):
    project = setup(repo, tmp_path)
    assert tiers(project)["fake-codex:default"] is ReasoningTier.UNKNOWN
    user_settings.set_model_tier("fake-codex:default", "frontier")
    user_settings.set_model_tier("fake-claude:opus", "economy")
    chosen = tiers(project)
    assert (
        chosen["fake-codex:default"] is ReasoningTier.FRONTIER and chosen["fake-claude:opus"] is ReasoningTier.ECONOMY
    )
    note = {m.candidate_id: m for m in project.registry.models()}["fake-codex:default"].metadata.notes
    assert user_settings.TIER_SOURCE in note and "default unknown" in note
    assert tiers(project) != {
        m.candidate_id: m.capabilities.reasoning_tier for m in project.registry.models(chosen_tiers=False)
    }
    assert user_settings.set_model_tier("fake-codex:default", None) == {"fake-claude:opus": "economy"}
    assert tiers(project)["fake-codex:default"] is ReasoningTier.UNKNOWN


def test_routing_scores_the_chosen_tier(repo, tmp_path):
    from saarathi.core.contracts import Complexity

    project = setup(repo, tmp_path)
    before = {m.candidate_id: m for m in project.registry.models()}["fake-codex:default"]
    user_settings.set_model_tier("fake-codex:default", "frontier")
    after = {m.candidate_id: m for m in project.registry.models()}["fake-codex:default"]
    assert _tier_fit(after, Complexity.COMPLEX) == 1.0 > _tier_fit(before, Complexity.COMPLEX)


@pytest.mark.parametrize(("candidate", "tier"), [("no-colon", "frontier"), ("a:b", "genius"), ("a:", "economy")])
def test_bad_tiers_are_refused(candidate, tier):
    with pytest.raises(ValueError):
        user_settings.set_model_tier(candidate, tier)


def test_the_app_and_the_cli_set_tiers(repo, tmp_path):
    project = setup(repo, tmp_path)
    bridge = BridgeService()
    bridge.models_set_tier("fake-codex:default", "balanced")
    rows = {r["candidate"]: r for r in bridge.models_list(str(project.root))}
    row = rows["fake-codex:default"]
    assert row["reasoning_tier"] == "balanced" and row["tier_set_by_you"] and row["default_tier"] == "unknown"
    assert not rows["fake-claude:opus"]["tier_set_by_you"] and rows["fake-claude:opus"]["default_tier"] == "frontier"
    result = CliRunner().invoke(app, ["models", "tier", "fake-codex:default", "default"])
    assert result.exit_code == 0 and "default tier again" in result.output
    result = CliRunner().invoke(app, ["models", "tier", "fake-codex:default", "genius"])
    assert result.exit_code != 0
    assert user_settings.model_tiers() == {}
