"""Models a person turns off are unavailable everywhere: routing, preferences and the lead."""

import asyncio

import pytest

from saarathi import user_settings
from saarathi.core.errors import PolicyViolation
from saarathi.core.policy import ApprovalPreset
from saarathi.core.states import RunState
from saarathi.orchestration.engine import Engine, SilentIO
from saarathi.orchestration.preferences import RunPreferences
from saarathi.orchestration.project import Project
from tests.e2e.helpers import GOOD_T1, GOOD_T2, init_project, make_script


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")
    monkeypatch.setenv("SAARATHI_DATA_DIR", str(tmp_path / "data"))


def setup(repo, tmp_path):
    script = make_script(tmp_path / "s.json", {"T-001": [GOOD_T1], "T-002": [GOOD_T2]})
    init_project(repo, script, tmp_path / "rec.jsonl")
    return Project.load(repo)


def go(project, tmp_path, prefs=None):
    engine = Engine(project, SilentIO())
    brief = tmp_path / "brief.md"
    brief.write_text("health and widget")
    run_id = asyncio.run(engine.start(brief, prefs or RunPreferences(approvals=ApprovalPreset.AUTO_SAFE)))
    return run_id, asyncio.run(engine.drive(run_id))


def test_a_turned_off_model_is_never_routed_to(repo, tmp_path):
    project = setup(repo, tmp_path)
    user_settings.set_model_enabled("fake-codex:default", False)
    off = {m.candidate_id: m for m in project.registry.models()}["fake-codex:default"]
    assert not off.available and off.unavailable_reason == user_settings.DISABLED_REASON
    run_id, state = go(project, tmp_path)
    assert state is RunState.AWAITING_REVIEW
    assert {a.adapter for a in project.store.attempts(run_id)} == {"fake-claude"}
    for t in project.store.tasks(run_id):
        (decision,) = project.store.decisions(run_id, t.id)
        rejected = {r.candidate: r.reasons for r in decision.rejected}
        assert any(user_settings.DISABLED_REASON in r for r in rejected["fake-codex:default"])


def test_a_turned_off_model_cannot_be_pinned_and_can_be_turned_back_on(repo, tmp_path):
    project = setup(repo, tmp_path)
    user_settings.set_model_enabled("fake-codex:default", False)
    pinned = RunPreferences.parse("auto_safe", "fake-codex:default", "pin")
    with pytest.raises(PolicyViolation, match="turned off in your Saarathi settings"):
        go(project, tmp_path, pinned)
    assert user_settings.set_model_enabled("fake-codex:default", True) == set()
    _, state = go(project, tmp_path, pinned)
    assert state is RunState.AWAITING_REVIEW


def test_turning_off_the_lead_pauses_instead_of_substituting(repo, tmp_path):
    project = setup(repo, tmp_path)
    user_settings.set_model_enabled("fake-claude:opus", False)
    run_id, state = go(project, tmp_path)
    assert state is RunState.PAUSED
    assert "engineering lead unavailable" in project.store.get_run(run_id).pause_reason
    assert user_settings.DISABLED_REASON in project.store.get_run(run_id).pause_reason


def test_model_names_are_checked():
    with pytest.raises(ValueError, match="adapter:model"):
        user_settings.set_model_enabled("gpt-6-astra", False)
