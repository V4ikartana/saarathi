"""Run preferences (ADR-0014): approval presets are capped by policy and never touch hard approvals;
model preferences and pins act only after the routing hard filters."""

import asyncio

import pytest

from saarathi.core.errors import ConfigError, PolicyViolation
from saarathi.core.policy import ApprovalPreset
from saarathi.core.states import RunState
from saarathi.orchestration.engine import Engine, SilentIO
from saarathi.orchestration.preferences import RunPreferences, effective_preset
from saarathi.orchestration.project import Project
from tests.e2e.helpers import GOOD_T1, GOOD_T2, PLAN, init_project, make_script
from tests.e2e.test_escalation import BLOCKED, SUPERSEDE, escalation_plan


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


def setup(repo, tmp_path, *, plan=None, execute=None, max_preset=None, extra=None):
    script = make_script(tmp_path / "s.json", execute or {"T-001": [GOOD_T1], "T-002": [GOOD_T2]}, plan=plan)
    if extra:
        import json

        data = json.loads(script.read_text())
        data["complete"].update(extra)
        script.write_text(json.dumps(data))
    overrides = {"approvals": {"max_preset": max_preset}} if max_preset else None
    init_project(repo, script, tmp_path / "rec.jsonl", policy_overrides=overrides)
    return Project.load(repo)


def go(project, tmp_path, prefs):
    """Start and drive a run with an IO that never approves anything: only the preset can."""
    engine = Engine(project, SilentIO())
    brief = tmp_path / "brief.md"
    brief.write_text("health and widget")
    run_id = asyncio.run(engine.start(brief, prefs))
    return engine, run_id, asyncio.run(engine.drive(run_id))


def approvals(project, run_id):
    return [(e.type, e.payload["kind"]) for e in project.store.events(run_id) if e.type.startswith("approval.")]


def test_ask_is_the_default_and_waits_for_a_person(repo, tmp_path):
    project = setup(repo, tmp_path)
    _, run_id, state = go(project, tmp_path, None)
    assert state is RunState.AWAITING_PLAN_APPROVAL
    assert approvals(project, run_id) == []


def test_auto_safe_starts_a_low_risk_plan_but_keeps_review_manual(repo, tmp_path):
    project = setup(repo, tmp_path)
    _, run_id, state = go(project, tmp_path, RunPreferences(approvals=ApprovalPreset.AUTO_SAFE))
    assert state is RunState.AWAITING_REVIEW
    assert approvals(project, run_id) == [("approval.automatic", "plan"), ("approval.required", "review")]
    events = project.store.events(run_id)
    started = next(e for e in events if e.type == "run.transition" and e.payload["to"] == "EXECUTING")
    assert started.actor == "policy" and "auto_safe" in started.payload["reason"]


def test_auto_safe_leaves_a_high_risk_plan_to_a_person(repo, tmp_path):
    risky = {**PLAN, "tasks": [{**PLAN["tasks"][0], "risk": "high"}, PLAN["tasks"][1]]}
    project = setup(repo, tmp_path, plan=risky)
    _, run_id, state = go(project, tmp_path, RunPreferences(approvals=ApprovalPreset.AUTO_SAFE))
    assert state is RunState.AWAITING_PLAN_APPROVAL
    (required,) = [e for e in project.store.events(run_id) if e.type == "approval.required"]
    assert "high-risk tasks need a person: T-001" in required.payload["reason"]


def test_bypass_needs_the_policy_to_allow_it(repo, tmp_path):
    project = setup(repo, tmp_path)
    with pytest.raises(PolicyViolation, match=r"exceeds this project's approvals\.max_preset 'auto_safe'"):
        go(project, tmp_path, RunPreferences(approvals=ApprovalPreset.BYPASS))
    assert project.store.active_run() is None, "a refused preference starts no run"


def test_bypass_completes_a_run_the_lead_approves(repo, tmp_path):
    project = setup(repo, tmp_path, max_preset="bypass")
    _, run_id, state = go(project, tmp_path, RunPreferences(approvals=ApprovalPreset.BYPASS))
    assert state is RunState.COMPLETED
    assert approvals(project, run_id) == [("approval.automatic", "plan"), ("approval.automatic", "review")]


def test_bypass_never_approves_an_escalation(repo, tmp_path):
    execute = {"T-001": [BLOCKED, GOOD_T1], "T-002": [GOOD_T2], "T-003": [{"write": {"docs/health.md": "doc\n"}}]}
    extra = {"lead.escalation": [SUPERSEDE]}
    project = setup(repo, tmp_path, plan=escalation_plan(), execute=execute, max_preset="bypass", extra=extra)
    engine, run_id, state = go(project, tmp_path, RunPreferences(approvals=ApprovalPreset.BYPASS))
    assert state is RunState.PAUSED and engine.pending_approval(run_id), "the ADR change still waits for a person"
    assert {a.number: a.status for a in project.memory.list_adrs()} == {1: "Accepted"}


def test_a_lowered_policy_caps_a_running_preset(repo, tmp_path):
    project = setup(repo, tmp_path, max_preset="bypass")
    prefs = RunPreferences(approvals=ApprovalPreset.BYPASS)
    assert effective_preset(prefs, project.policy) is ApprovalPreset.BYPASS
    ask_only = project.policy.approvals.model_copy(update={"max_preset": ApprovalPreset.ASK})
    lowered = project.policy.model_copy(update={"approvals": ask_only})
    assert effective_preset(prefs, lowered) is ApprovalPreset.ASK


def test_pinned_model_takes_every_task(repo, tmp_path):
    project = setup(repo, tmp_path)
    prefs = RunPreferences.parse("auto_safe", "fake-codex:default", "pin")
    _, run_id, state = go(project, tmp_path, prefs)
    assert state is RunState.AWAITING_REVIEW
    assert {a.adapter for a in project.store.attempts(run_id)} == {"fake-codex"}
    for t in project.store.tasks(run_id):
        (decision,) = project.store.decisions(run_id, t.id)
        assert any("pinned" in r.reasons[0] for r in decision.rejected)


def test_model_preferences_are_checked_before_the_run_starts(repo, tmp_path):
    project = setup(repo, tmp_path)
    with pytest.raises(ConfigError, match="unknown model 'fake-codex:o9'"):
        go(project, tmp_path, RunPreferences.parse("ask", "fake-codex:o9", "pin"))
    with pytest.raises(ConfigError, match="invalid preference"):
        RunPreferences.parse("everything", None, None)
