"""Scenario D: exhausted or disabled CLI workers give clear diagnostics, bounded retries, and fallback or pause."""

import asyncio
import json
import time

import pytest
import yaml
from typer.testing import CliRunner

from saarathi.cli.main import app
from saarathi.core.states import RunState, TaskState
from saarathi.orchestration.engine import Engine
from saarathi.orchestration.project import Project
from tests.e2e.helpers import GOOD_T1, GOOD_T2, init_project, make_script, records
from tests.e2e.test_scenarios import ApproveIO

runner = CliRunner()


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


def setup(repo, tmp_path, execute, *, execution=None, codex_available=True):
    record = tmp_path / "rec.jsonl"
    init_project(
        repo,
        make_script(tmp_path / "s.json", execute),
        record,
        policy_overrides={"execution": {"max_attempts": 2, **(execution or {})}},
    )
    if not codex_available:
        path = repo / ".saarathi" / "policies.yaml"
        pol = yaml.safe_load(path.read_text())
        # copy: both fake adapters share one options mapping (a YAML anchor)
        pol["providers"]["fake-codex"]["options"] = {**pol["providers"]["fake-codex"]["options"], "available": False}
        path.write_text(yaml.safe_dump(pol))
    return record


def drive(repo, tmp_path, run_id=None):
    project = Project.load(repo)
    engine = Engine(project, ApproveIO())
    if run_id is None:
        brief = tmp_path / "brief.md"
        brief.write_text("health + widget")
        run_id = asyncio.run(engine.start(brief))
    else:
        engine.resume(run_id)
    return project, run_id, asyncio.run(engine.drive(run_id))


def runs_of(record, task_id):
    return [(r["adapter"]) for r in records(record) if r["key"] == task_id]


def test_rate_limited_worker_falls_back_to_other_eligible_provider(repo, tmp_path):
    # Whichever task Codex picks up is rate limited there; the retry must move to the other eligible provider.
    flaky = {"fail": "rate_limit", "fail_on": ["fake-codex"]}
    record = setup(repo, tmp_path, {"T-001": [{**GOOD_T1, **flaky}], "T-002": [{**GOOD_T2, **flaky}]})
    project, run_id, state = drive(repo, tmp_path)
    assert state is RunState.AWAITING_REVIEW
    hit = [t for t in ("T-001", "T-002") if runs_of(record, t)[0] == "fake-codex"]
    assert len(hit) == 1, "routing spreads the two tasks across both providers"
    assert runs_of(record, hit[0]) == ["fake-codex", "fake-claude"]
    task = project.store.get_task(run_id, hit[0])
    assert task.state is TaskState.SUCCEEDED and task.failures == 0, "provider limits do not use the failure budget"
    retry = project.store.decisions(run_id, hit[0])[-1]
    assert retry.chosen == "fake-claude:opus" and retry.cooling == ["fake-codex:default"]
    assert project.store.provider_health()["fake-codex"].state == "cooling_down"
    assert any(e.type == "provider.limited" for e in project.store.events(run_id))


def test_short_cooldown_waits_in_place_when_no_other_worker(repo, tmp_path):
    flaky = [{**GOOD_T1, "fail": "rate_limit"}, GOOD_T1]
    record = setup(
        repo,
        tmp_path,
        {"T-001": flaky, "T-002": [GOOD_T2]},
        execution={"rate_limit_cooldown_seconds": [0.4]},
        codex_available=False,
    )
    started = time.monotonic()
    project, _run_id, state = drive(repo, tmp_path)
    assert state is RunState.AWAITING_REVIEW
    assert time.monotonic() - started >= 0.4, "the retry waited for the cooldown"
    assert runs_of(record, "T-001") == ["fake-claude", "fake-claude"]
    assert "fake-claude" not in project.store.provider_health(), "a successful call clears the cooldown"


def test_long_cooldown_pauses_instead_of_waiting(repo, tmp_path):
    record = setup(
        repo,
        tmp_path,
        {"T-001": [{**GOOD_T1, "fail": "rate_limit"}], "T-002": [GOOD_T2]},
        execution={"rate_limit_cooldown_seconds": [3600]},
        codex_available=False,
    )
    project, run_id, state = drive(repo, tmp_path)
    assert state is RunState.PAUSED
    t1 = project.store.get_task(run_id, "T-001")
    assert t1.state is TaskState.BLOCKED and "limited by its provider" in t1.blocked_reason
    assert runs_of(record, "T-001") == ["fake-claude"]


def test_exhaustion_everywhere_pauses_with_diagnostics_and_stays_bounded(repo, tmp_path, monkeypatch):
    dead = {**GOOD_T1, "fail": "quota_exhausted"}
    record = setup(repo, tmp_path, {"T-001": [dead], "T-002": [GOOD_T2]})
    project, run_id, state = drive(repo, tmp_path)
    assert state is RunState.PAUSED
    assert sorted(runs_of(record, "T-001")) == ["fake-claude", "fake-codex"], "each provider tried once, no loop"
    health = project.store.provider_health()
    assert {h.state for h in health.values()} == {"exhausted"}
    assert "limited by its provider" in project.store.get_task(run_id, "T-001").blocked_reason
    assert project.store.get_task(run_id, "T-001").failures == 0

    monkeypatch.chdir(repo)
    listing = json.loads(runner.invoke(app, ["models", "list", "--json"]).stdout)
    assert all(m["provider_limit"].startswith("exhausted until") for m in listing)
    doctor = runner.invoke(app, ["doctor"]).stdout
    assert "provider limit: exhausted" in doctor and "saarathi providers reset" in doctor

    # A person clears one limit (e.g. after upgrading); the third limit hit then stops the task for good.
    assert "Cleared" in runner.invoke(app, ["providers", "reset", "fake-claude"]).stdout
    project, run_id, state = drive(repo, tmp_path, run_id)
    assert state is RunState.PAUSED
    assert len(runs_of(record, "T-001")) == 3
    assert "max_rate_limit_retries" in project.store.get_task(run_id, "T-001").blocked_reason


def test_disabled_worker_is_never_claimed_usable(repo, tmp_path, monkeypatch):
    record = setup(repo, tmp_path, {"T-001": [GOOD_T1], "T-002": [GOOD_T2]}, codex_available=False)
    project, run_id, state = drive(repo, tmp_path)
    assert state is RunState.AWAITING_REVIEW
    assert {r["adapter"] for r in records(record) if r["key"] in ("T-001", "T-002")} == {"fake-claude"}
    for tid in ("T-001", "T-002"):
        rejected = {r.candidate: r.reasons for r in project.store.decisions(run_id, tid)[-1].rejected}
        assert "unavailable" in rejected["fake-codex:default"][0]
    monkeypatch.chdir(repo)
    listing = {m["candidate"]: m for m in json.loads(runner.invoke(app, ["models", "list", "--json"]).stdout)}
    assert listing["fake-codex:default"]["available"] is False


def test_cli_signed_in_with_api_key_is_never_used(repo, tmp_path):
    """Scenario C as it applies to subscription-only v0.1: a metered worker is ineligible, never a fallback."""
    record = setup(repo, tmp_path, {"T-001": [GOOD_T1], "T-002": [GOOD_T2]})
    path = repo / ".saarathi" / "policies.yaml"
    pol = yaml.safe_load(path.read_text())
    pol["providers"]["fake-codex"]["options"] = {**pol["providers"]["fake-codex"]["options"], "metered": True}
    path.write_text(yaml.safe_dump(pol))
    project, run_id, state = drive(repo, tmp_path)
    assert state is RunState.AWAITING_REVIEW
    assert {r["adapter"] for r in records(record) if r["key"] in ("T-001", "T-002")} == {"fake-claude"}
    for tid in ("T-001", "T-002"):
        rejected = {r.candidate: r.reasons for r in project.store.decisions(run_id, tid)[-1].rejected}
        assert "metered usage is blocked" in rejected["fake-codex:default"][0]
