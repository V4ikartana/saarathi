"""Scenario E: a contract conflict pauses affected work, reaches the lead with evidence, and changes an accepted ADR
only after approval."""

import asyncio

import pytest
from typer.testing import CliRunner

from saarathi.cli.main import app
from saarathi.core.states import RunState, TaskState
from saarathi.orchestration.engine import Engine
from saarathi.orchestration.project import Project
from tests.e2e.helpers import GOOD_T1, GOOD_T2, PLAN, init_project, make_script, records, task
from tests.e2e.test_scenarios import ApproveIO

runner = CliRunner()

CONTRACT_ADR = {
    "title": "Health returns plain text",
    "context": "Simplest possible probe.",
    "decision": "GET /health returns the plain-text body 'ok'.",
}
ESCALATION = {
    "kind": "contract_change",
    "summary": "Monitoring requires JSON from /health, but ADR-0001 fixes a plain-text body.",
    "evidence": "tests/test_health.py expects {'status': 'ok'}; ADR-0001 says plain text.",
    "proposed_change": "Return JSON {'status': 'ok'}.",
}
BLOCKED = {
    "write": {"app/health.py": "partial work\n"},
    "report": {
        "status": "blocked",
        "summary": "Cannot satisfy both the ADR and the tests.",
        "changed_files": [],
        "tests_run": [],
        "assumptions": [],
        "unresolved_risks": [],
        "memory_proposals": [],
        "outputs": [],
        "escalations": [ESCALATION],
    },
}
SUPERSEDE = {
    "decision": "supersede_adr",
    "rationale": "Monitoring needs JSON; the plain-text decision was too narrow.",
    "guidance": 'Return JSON {"status": "ok"} from health().',
    "adr": {
        "title": "Health returns JSON",
        "context": "Monitoring parses JSON.",
        "decision": 'GET /health returns JSON {"status": "ok"}.',
        "supersedes": 1,
    },
}
RETRY = {
    "decision": "retry_with_guidance",
    "rationale": "The ADR stands; the test was misread.",
    "guidance": "Keep the plain-text contract; align the test with ADR-0001.",
    "adr": {"title": "", "context": "", "decision": "", "supersedes": None},
}
STOP = {
    "decision": "stop",
    "rationale": "Changing the public contract needs the product owner.",
    "guidance": "",
    "adr": {"title": "", "context": "", "decision": "", "supersedes": None},
}


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


def escalation_plan():
    t2 = {**PLAN["tasks"][1], "dependencies": ["T-001"]}
    t3 = task("T-003", "Docs", ["docs/**"], "git status")
    return {**PLAN, "tasks": [PLAN["tasks"][0], t2, t3], "decisions": [CONTRACT_ADR]}


def setup(repo, tmp_path, t1_steps, lead_verdicts):
    record = tmp_path / "rec.jsonl"
    execute = {"T-001": t1_steps, "T-002": [GOOD_T2], "T-003": [{"write": {"docs/health.md": "doc\n"}}]}
    script = make_script(tmp_path / "s.json", execute, plan=escalation_plan())
    import json

    data = json.loads(script.read_text())
    data["complete"]["lead.escalation"] = lead_verdicts
    script.write_text(json.dumps(data))
    init_project(repo, script, record)
    return record


def start(repo, tmp_path):
    project = Project.load(repo)
    engine = Engine(project, ApproveIO())
    brief = tmp_path / "brief.md"
    brief.write_text("health")
    run_id = asyncio.run(engine.start(brief))
    return project, engine, run_id, asyncio.run(engine.drive(run_id))


def keys(record, key):
    return [r for r in records(record) if r["key"] == key]


def adr_status(project):
    return {a.number: a.status for a in project.memory.list_adrs()}


def test_contract_change_waits_for_approval_before_superseding_adr(repo, tmp_path):
    record = setup(repo, tmp_path, [BLOCKED, GOOD_T1], [SUPERSEDE])
    project, engine, run_id, state = start(repo, tmp_path)

    assert state is RunState.PAUSED and "superseding ADR-0001" in project.store.get_run(run_id).pause_reason
    tasks = {t.id: t for t in project.store.tasks(run_id)}
    assert tasks["T-001"].state is TaskState.BLOCKED and tasks["T-001"].failures == 0
    assert tasks["T-002"].state is TaskState.BLOCKED, "dependent work pauses"
    assert tasks["T-003"].state is TaskState.SUCCEEDED, "independent work continues"
    assert keys(record, "T-002") == []
    assert len(keys(record, "lead.escalation")) == 1
    assert adr_status(project) == {1: "Accepted"}, "nothing changes before approval"
    esc = project.store.escalations(run_id)[0]
    assert esc.status == "awaiting_approval" and esc.kind == "contract_change"
    assert "tests/test_health.py expects" in esc.checkpoint and "ADR-0001 [Accepted]" in esc.checkpoint
    assert "app/health.py" in esc.checkpoint, "the discarded attempt's diff is evidence"
    assert list((repo / ".saarathi" / "checkpoints").glob(f"*escalation-{esc.id}.md"))
    assert (
        "partial work" not in (project.worktree_root(run_id) / "T-001" / "app" / "health.py").read_text("utf-8")
        if (project.worktree_root(run_id) / "T-001" / "app" / "health.py").exists()
        else True
    )

    new_path = engine.approve_escalation(run_id)
    assert adr_status(project) == {1: "Superseded by ADR-0002", 2: "Accepted"}
    assert "Supersedes: ADR-0001" in project.memory.read(new_path).content
    assert asyncio.run(engine.drive(run_id)) is RunState.AWAITING_REVIEW
    retry_prompt = keys(record, "T-001")[-1]["prompt"]
    assert "Guidance from the engineering lead" in retry_prompt and "Health returns JSON" in retry_prompt
    assert [a.outcome for a in project.store.attempts(run_id, "T-001")] == ["escalated", "succeeded"]
    assert any(e.type == "adr.superseded" for e in project.store.events(run_id))


def test_retry_with_guidance_continues_without_touching_adrs(repo, tmp_path):
    record = setup(repo, tmp_path, [BLOCKED, GOOD_T1], [RETRY])
    project, _, run_id, state = start(repo, tmp_path)
    assert state is RunState.AWAITING_REVIEW
    assert adr_status(project) == {1: "Accepted"}
    assert "align the test with ADR-0001" in keys(record, "T-001")[-1]["prompt"]
    assert project.store.escalations(run_id)[0].status == "applied"


def test_lead_stop_pauses_for_a_person(repo, tmp_path):
    setup(repo, tmp_path, [BLOCKED], [STOP])
    project, _, run_id, state = start(repo, tmp_path)
    assert state is RunState.PAUSED and "product owner" in project.store.get_run(run_id).pause_reason
    assert project.store.escalations(run_id)[0].status == "stopped"
    assert adr_status(project) == {1: "Accepted"}


def test_escalations_are_bounded(repo, tmp_path):
    record = setup(repo, tmp_path, [BLOCKED], [RETRY])  # the worker escalates every time
    project, _, run_id, state = start(repo, tmp_path)
    assert state is RunState.PAUSED
    assert len(keys(record, "lead.escalation")) == 2, "max_escalations_per_task: the third goes to a person"
    assert [e.status for e in project.store.escalations(run_id)] == ["applied", "applied", "stopped"]
    assert "max_escalations_per_task" in project.store.get_task(run_id, "T-001").blocked_reason


def test_proposal_to_supersede_unknown_adr_is_refused(repo, tmp_path):
    bad = {**SUPERSEDE, "adr": {**SUPERSEDE["adr"], "supersedes": 7}}
    setup(repo, tmp_path, [BLOCKED], [bad])
    project, _engine, run_id, state = start(repo, tmp_path)
    assert state is RunState.PAUSED and project.store.escalations(run_id)[0].status == "stopped"
    assert "not an accepted decision" in project.store.escalations(run_id)[0].resolution
    assert adr_status(project) == {1: "Accepted"}


def test_lead_unavailable_keeps_escalation_open(repo, tmp_path):
    setup(repo, tmp_path, [BLOCKED], [])  # the fake lead has no scripted escalation answer
    project, _, run_id, state = start(repo, tmp_path)
    assert state is RunState.PAUSED and "could not review escalation" in project.store.get_run(run_id).pause_reason
    assert project.store.escalations(run_id)[0].status == "open"


def test_cli_lists_and_approves(repo, tmp_path, monkeypatch):
    setup(repo, tmp_path, [BLOCKED, GOOD_T1], [SUPERSEDE])
    start(repo, tmp_path)
    monkeypatch.chdir(repo)
    listing = runner.invoke(app, ["escalations"]).stdout
    assert "awaiting_approval" in listing and "Health returns JSON" in listing and "saarathi approve" in listing
    result = runner.invoke(app, ["approve", "--non-interactive"])
    assert result.exit_code == 3 and "AWAITING_REVIEW" in result.stdout
