"""Dependent tasks start from their dependencies' verified work, never from the bare run base."""

import asyncio

import pytest

from saarathi.core.states import RunState, TaskState
from saarathi.orchestration.engine import Engine
from saarathi.orchestration.project import Project
from tests.e2e.helpers import GOOD_T1, GOOD_T2, PLAN, init_project, make_script, records, task


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


class Approve:
    def info(self, m): ...
    def ask(self, q, d):
        return None

    def confirm(self, m):
        return True


def drive(repo, tmp_path):
    project = Project.load(repo)
    engine = Engine(project, Approve())
    brief = tmp_path / "brief.md"
    brief.write_text("x")
    run_id = asyncio.run(engine.start(brief))
    return project, run_id, asyncio.run(engine.drive(run_id))


def test_chain_sees_dependency_code(repo, tmp_path):
    # T-002's own check imports T-001's module: it can only pass if T-002 starts from T-001's commit.
    t2 = {
        **PLAN["tasks"][1],
        "dependencies": ["T-001"],
        "verification_commands": ["python3 -m unittest tests.test_widget tests.test_health"],
    }
    plan = {**PLAN, "tasks": [PLAN["tasks"][0], t2]}
    init_project(
        repo,
        make_script(tmp_path / "s.json", {"T-001": [GOOD_T1], "T-002": [GOOD_T2]}, plan=plan),
        tmp_path / "r.jsonl",
    )
    project, run_id, state = drive(repo, tmp_path)
    assert state is RunState.AWAITING_REVIEW, project.store.get_task(run_id, "T-002").result
    t1, t2r = (project.store.get_task(run_id, t) for t in ("T-001", "T-002"))
    attempt = project.store.attempts(run_id, "T-002")[-1]
    assert attempt.base_commit == t1.result["commit"]
    assert t2r.result["changed_files"] == ["tests/test_widget.py", "ui/__init__.py", "ui/widget.py"], (
        "dependency files are inherited, not counted as the dependent task's changes"
    )


def test_diamond_merges_both_parents(repo, tmp_path):
    t3 = task(
        "T-003",
        "Combine",
        ["combined.txt"],
        "python3 -m unittest tests.test_health tests.test_widget",
        deps=["T-001", "T-002"],
    )
    plan = {**PLAN, "tasks": [*PLAN["tasks"], t3]}
    execute = {"T-001": [GOOD_T1], "T-002": [GOOD_T2], "T-003": [{"write": {"combined.txt": "both\n"}}]}
    init_project(repo, make_script(tmp_path / "s.json", execute, plan=plan), tmp_path / "r.jsonl")
    project, run_id, state = drive(repo, tmp_path)
    assert state is RunState.AWAITING_REVIEW
    base = project.store.attempts(run_id, "T-003")[-1].base_commit
    files = project.git.git("ls-tree", "-r", "--name-only", base).split()
    assert {"app/health.py", "ui/widget.py"} <= set(files)
    assert project.store.get_task(run_id, "T-003").result["changed_files"] == ["combined.txt"]


def test_conflicting_dependencies_block_instead_of_running(repo, tmp_path):
    a = task("T-001", "A", ["shared.txt"], "git status")
    b = task("T-002", "B", ["shared.txt"], "git status")
    c = task("T-003", "C", ["c.txt"], "git status", deps=["T-001", "T-002"])
    plan = {**PLAN, "tasks": [a, b, c], "integration_checks": ["git status"]}
    execute = {
        "T-001": [{"write": {"shared.txt": "A\n"}}],
        "T-002": [{"write": {"shared.txt": "B\n"}}],
        "T-003": [{"write": {"c.txt": "c\n"}}],
    }
    init_project(repo, make_script(tmp_path / "s.json", execute, plan=plan), tmp_path / "r.jsonl")
    project, run_id, state = drive(repo, tmp_path)
    assert state is RunState.PAUSED
    c_rec = project.store.get_task(run_id, "T-003")
    assert c_rec.state is TaskState.BLOCKED and "conflict" in c_rec.blocked_reason
    assert not [r for r in records(tmp_path / "r.jsonl") if r["key"] == "T-003"], "T-003 must not run"
    assert list((repo / ".saarathi" / "checkpoints").glob("*dependency-conflict.md"))
