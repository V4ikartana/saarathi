"""CLI behavior: exit codes and --json output that validates against the published models."""

import json

import pytest
from typer.testing import CliRunner

from saarathi.cli.main import app
from saarathi.cli.output import DoctorOut, ModelOut, PolicyOut, StatusOut, TaskOut
from tests.e2e.helpers import GOOD_T1, GOOD_T2, init_project, make_script

runner = CliRunner()


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


def invoke(repo, monkeypatch, *args):
    monkeypatch.chdir(repo)
    return runner.invoke(app, list(args))


def test_init_outside_git_fails_cleanly(tmp_path, monkeypatch):
    r = invoke(tmp_path, monkeypatch, "init")
    assert r.exit_code == 2 and "not inside a Git repository" in r.output


def test_default_init_policy_is_valid_and_safe(repo, monkeypatch):
    assert invoke(repo, monkeypatch, "init").exit_code == 0
    r = invoke(repo, monkeypatch, "policy", "validate", "--json")
    out = PolicyOut.model_validate_json(r.stdout)
    assert out.valid
    text = (repo / ".saarathi" / "policies.yaml").read_text()
    assert "api_enabled: false" in text and "allow_network: false" in text
    gitignore = (repo / ".saarathi" / ".gitignore").read_text()
    assert "state.db" in gitignore and "logs/" in gitignore


def test_fake_adapter_refused_without_test_flag(repo, tmp_path, monkeypatch):
    init_project(repo, make_script(tmp_path / "s.json", {}), tmp_path / "r.jsonl")
    monkeypatch.delenv("SAARATHI_ALLOW_FAKE_PROVIDERS")
    r = invoke(repo, monkeypatch, "doctor")
    assert r.exit_code == 2 and "test-only fake adapter" in r.output


def test_invalid_policy_reports_field(repo, monkeypatch):
    invoke(repo, monkeypatch, "init")
    (repo / ".saarathi" / "policies.yaml").write_text(
        "version: 1\nmode: subscription\norchestration: {lead: {adapter: claude-code, model: gpt-imaginary}}\n"
        "routing: {allowed_providers: [anthropic], allowed_models: ['anthropic/*']}\n"
        "providers: {claude-code: {models: [{id: opus}]}}\n"
    )
    r = invoke(repo, monkeypatch, "policy", "validate")
    assert r.exit_code == 2 and "gpt-imaginary" in r.output


def test_full_flow_json(repo, tmp_path, monkeypatch):
    init_project(repo, make_script(tmp_path / "s.json", {"T-001": [GOOD_T1], "T-002": [GOOD_T2]}), tmp_path / "r.jsonl")
    doctor = DoctorOut.model_validate_json(invoke(repo, monkeypatch, "doctor", "--json").stdout)
    assert doctor.lead == "fake-claude:opus" and all(a["test_only"] for a in doctor.adapters)
    models = [
        ModelOut.model_validate(m) for m in json.loads(invoke(repo, monkeypatch, "models", "list", "--json").stdout)
    ]
    assert {m.candidate for m in models} == {"fake-claude:opus", "fake-codex:default"}

    brief = tmp_path / "brief.md"
    brief.write_text("Add health + widget")
    r = invoke(repo, monkeypatch, "run", str(brief), "--non-interactive", "--json")
    assert r.exit_code == 3
    assert StatusOut.model_validate_json(r.stdout).state == "AWAITING_PLAN_APPROVAL"
    r = invoke(repo, monkeypatch, "approve", "--non-interactive", "--json")
    status = StatusOut.model_validate_json(r.stdout)
    assert r.exit_code == 3 and status.state == "AWAITING_REVIEW"
    tasks = [TaskOut.model_validate(t) for t in json.loads(invoke(repo, monkeypatch, "tasks", "--json").stdout)]
    assert {t.provider for t in tasks} == {"anthropic", "openai"}
    explain = invoke(repo, monkeypatch, "route", "explain", "T-001")
    assert "chose fake-" in explain.stdout and "highest score" in explain.stdout
    assert "Lead review: approve" in invoke(repo, monkeypatch, "review").stdout
    r = invoke(repo, monkeypatch, "approve")
    assert r.exit_code == 0 and "Report:" in r.output
    report = json.loads(invoke(repo, monkeypatch, "report", "--json").stdout)
    assert report["state"] in ("READY_TO_SHIP", "COMPLETED") and len(report["tasks"]) == 2
    assert invoke(repo, monkeypatch, "status", "--json").exit_code == 0


def test_second_run_refused_while_active(repo, tmp_path, monkeypatch):
    init_project(repo, make_script(tmp_path / "s.json", {}), tmp_path / "r.jsonl")
    brief = tmp_path / "brief.md"
    brief.write_text("x")
    invoke(repo, monkeypatch, "run", str(brief), "--non-interactive")
    r = invoke(repo, monkeypatch, "run", str(brief), "--non-interactive")
    assert r.exit_code == 1 and "already active" in r.output
    assert invoke(repo, monkeypatch, "cancel").exit_code == 0
