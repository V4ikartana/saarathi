"""Routing avoids a provider whose plan is nearly used up (routing.min_remaining_percent)."""

import asyncio

import pytest
import yaml

from saarathi.core.policy import ApprovalPreset
from saarathi.core.states import RunState
from saarathi.orchestration.engine import Engine, SilentIO
from saarathi.orchestration.preferences import RunPreferences
from saarathi.orchestration.project import Project
from tests.e2e.helpers import GOOD_T1, GOOD_T2, init_project, make_script

NEARLY_OUT = [{"label": "Weekly", "used_percent": 98, "window": "weekly", "resets_at": "2099-01-01T00:00:00+00:00"}]


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


def setup(repo, tmp_path, limits, threshold=5):
    script = make_script(tmp_path / "s.json", {"T-001": [GOOD_T1], "T-002": [GOOD_T2]})
    overrides = {"routing": {"min_remaining_percent": threshold}}
    init_project(repo, script, tmp_path / "rec.jsonl", policy_overrides=overrides)
    path = repo / ".saarathi" / "policies.yaml"
    policy = yaml.safe_load(path.read_text())
    codex = policy["providers"]["fake-codex"]
    codex["options"] = {**codex["options"], "limits": limits}  # the helper shares one options dict between adapters
    path.write_text(yaml.safe_dump(policy))
    return Project.load(repo)


def go(project, tmp_path):
    engine = Engine(project, SilentIO())
    brief = tmp_path / "brief.md"
    brief.write_text("health and widget")
    run_id = asyncio.run(engine.start(brief, RunPreferences(approvals=ApprovalPreset.AUTO_SAFE)))
    return run_id, asyncio.run(engine.drive(run_id))


def test_a_provider_near_its_limit_gets_no_work(repo, tmp_path):
    project = setup(repo, tmp_path, NEARLY_OUT)
    run_id, state = go(project, tmp_path)
    tasks = [(t.id, t.state, t.blocked_reason) for t in project.store.tasks(run_id)]
    assert state is RunState.AWAITING_REVIEW, (project.store.get_run(run_id).pause_reason, tasks)
    assert {a.adapter for a in project.store.attempts(run_id)} == {"fake-claude"}
    events = [e for e in project.store.events(run_id) if e.type == "provider.near_limit"]
    assert events and events[0].payload["candidate"] == "fake-codex:default"
    assert events[0].payload["remaining_percent"] == 2.0


def test_the_check_can_be_turned_off(repo, tmp_path):
    project = setup(repo, tmp_path, NEARLY_OUT, threshold=0)
    run_id, state = go(project, tmp_path)
    assert state is RunState.AWAITING_REVIEW
    assert not [e for e in project.store.events(run_id) if e.type == "provider.near_limit"]
    assert "fake-codex" in {a.adapter for a in project.store.attempts(run_id)}, "spread sends work to both again"


def test_a_lead_at_its_plan_limit_pauses_with_an_actionable_reason(repo, tmp_path):
    import json

    script = make_script(tmp_path / "s.json", {})
    data = json.loads(script.read_text())
    data["complete"]["lead.intake"] = {"__error__": "You've hit your session limit · resets 5:40pm (Asia/Calcutta)"}
    script.write_text(json.dumps(data))
    init_project(repo, script, tmp_path / "rec.jsonl")
    project = Project.load(repo)
    run_id, state = go(project, tmp_path)
    reason = project.store.get_run(run_id).pause_reason
    assert state is RunState.PAUSED
    assert "engineering lead (fake-claude:opus) has reached its plan limit" in reason
    assert "resets 5:40pm" in reason and "lead_substitute" in reason
