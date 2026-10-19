"""P7 through the engine and CLI: only verified work proposes; approved lessons reach later runs' context."""

import asyncio

import pytest
from typer.testing import CliRunner

from saarathi.cli.main import app
from saarathi.core.states import RunState
from saarathi.orchestration.engine import Engine
from saarathi.orchestration.project import Project
from tests.e2e.helpers import GOOD_T1, GOOD_T2, HEALTH_TEST, init_project, make_script
from tests.e2e.test_scenarios import ApproveIO

runner = CliRunner()


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


def report(files, proposals):
    return {
        "status": "succeeded",
        "summary": "done",
        "changed_files": files,
        "tests_run": [],
        "assumptions": [],
        "unresolved_risks": [],
        "memory_proposals": proposals,
    }


def test_only_verified_attempts_propose_and_approved_lessons_reach_next_run(repo, tmp_path, monkeypatch):
    broken_t1 = {
        "write": {
            "app/__init__.py": "",
            "app/health.py": "def health():\n    return {}\n",
            "tests/test_health.py": HEALTH_TEST,
        },
        "report": report(["app/health.py"], ["Speculation from a failed attempt"]),
    }
    good_t1 = {**GOOD_T1, "report": report(["app/health.py"], ["Register routes in the ROUTES table"])}
    script = make_script(tmp_path / "s.json", {"T-001": [broken_t1, good_t1], "T-002": [GOOD_T2]})
    init_project(repo, script, tmp_path / "r.jsonl")
    project = Project.load(repo)
    engine = Engine(project, ApproveIO())
    brief = tmp_path / "brief.md"
    brief.write_text("x")
    run_id = asyncio.run(engine.start(brief))
    assert asyncio.run(engine.drive(run_id)) is RunState.AWAITING_REVIEW
    texts = [lesson.text for lesson in project.store.lessons()]
    assert texts == ["Register routes in the ROUTES table"], "failed attempts never propose lessons"

    monkeypatch.chdir(repo)
    listing = runner.invoke(app, ["lessons"]).stdout
    lesson_id = listing.split()[0]
    assert "[proposed]" in listing
    out = runner.invoke(app, ["lessons", "approve", lesson_id, "--text", "Register every route in ROUTES"])
    assert out.exit_code == 0, out.output
    assert "Register every route in ROUTES" in (repo / ".saarathi" / "lessons.md").read_text()
    assert runner.invoke(app, ["approve"]).exit_code == 0  # finish the first run

    run2 = asyncio.run(engine.start(brief))
    assert asyncio.run(engine.drive(run2)) is RunState.AWAITING_REVIEW
    context = project.memory.read(f"tasks/{run2}/T-001.context.md").content
    assert "Approved lessons from earlier runs" in context and "Register every route in ROUTES" in context
    assert '<untrusted-data source=".saarathi/lessons.md">' in context and ".saarathi/lessons.md @ " in context
