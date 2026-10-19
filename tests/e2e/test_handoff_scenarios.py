"""P2b through the real engine: what a dependent worker's prompt actually contains."""

import json

import pytest
import yaml

from saarathi.core.states import RunState
from tests.e2e.test_integration_scenarios import account, executions, itask, note, plan, setup, start

REPORT = {
    "status": "succeeded",
    "summary": "read mail",
    "changed_files": [],
    "tests_run": [],
    "assumptions": [],
    "unresolved_risks": [],
    "memory_proposals": [],
    "outputs": [
        {"name": "subject", "value": "Board meeting moved"},
        {"name": "body", "value": "CONFIDENTIAL-BODY-TEXT"},
    ],
}


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


def reader(tid):
    return {**note(tid), "report": {**REPORT, "changed_files": [f"notes/{tid.lower()}.md"]}}


def set_integration_policy(repo, **values):
    path = repo / ".saarathi" / "policies.yaml"
    pol = yaml.safe_load(path.read_text())
    pol["integrations"] = {**pol.get("integrations", {}), **values}
    path.write_text(yaml.safe_dump(pol))


def prompt_of(record, tid):
    return executions(record, tid)[-1]["prompt"]


def test_same_provider_dependent_sees_only_share_fields(repo, tmp_path):
    p = plan(
        itask("T-001", "Read Gmail", [{"service": "gmail", "operation": "mail.read", "share_fields": ["subject"]}]),
        itask("T-002", "Summarise", [{"service": "gmail", "operation": "mail.read"}], deps=["T-001"]),
    )
    project, record = setup(repo, tmp_path, p, {"T-001": [reader("T-001")], "T-002": [note("T-002")]})
    account(project, service="gmail", account_id="acct-g", worker="claude", ops=["mail.read"])
    _, run_id, state = start(project)
    assert state is RunState.AWAITING_REVIEW
    prompt = prompt_of(record, "T-002")
    assert "Board meeting moved" in prompt and "CONFIDENTIAL-BODY-TEXT" not in prompt
    assert '<untrusted-data source="saarathi:task-handoff">' in prompt
    event = next(e for e in project.store.events(run_id, task_id="T-002") if e.type == "handoff.prepared")
    assert event.payload["passed"] == ["T-001.subject"] and event.payload["dropped"][0].startswith("T-001.body")
    assert "CONFIDENTIAL" not in json.dumps([e.payload for e in project.store.events(run_id)])
    assert project.store.get_task(run_id, "T-002").result["received_inputs"] == ["T-001.subject"]


def test_cross_provider_dependent_gets_only_policy_shareable_fields(repo, tmp_path):
    p = plan(
        itask(
            "T-001", "Read Gmail", [{"service": "gmail", "operation": "mail.read", "share_fields": ["subject", "body"]}]
        ),
        itask("T-002", "File to Outlook", [{"service": "microsoft.outlook", "operation": "mail.read"}], deps=["T-001"]),
    )
    project, record = setup(repo, tmp_path, p, {"T-001": [reader("T-001")], "T-002": [note("T-002")]})
    set_integration_policy(repo, cross_provider_data_transfer="allow", shareable_fields=["subject"])
    account(project, service="gmail", account_id="acct-g", worker="claude", ops=["mail.read"])
    account(project, service="microsoft.outlook", account_id="acct-o", worker="codex", ops=["mail.read"])
    from saarathi.orchestration.project import Project

    _, _run_id, state = start(Project.load(repo))
    assert state is RunState.AWAITING_REVIEW
    assert {r["adapter"] for r in executions(record, "T-002")} == {"fake-codex"}
    prompt = prompt_of(record, "T-002")
    assert "Board meeting moved" in prompt and "CONFIDENTIAL-BODY-TEXT" not in prompt


def test_denied_transfer_pauses_and_nothing_is_handed_over(repo, tmp_path):
    p = plan(
        itask("T-001", "Read Gmail", [{"service": "gmail", "operation": "mail.read", "share_fields": ["subject"]}]),
        itask("T-002", "File to Outlook", [{"service": "microsoft.outlook", "operation": "mail.read"}], deps=["T-001"]),
    )
    project, record = setup(repo, tmp_path, p, {"T-001": [reader("T-001")], "T-002": [note("T-002")]})
    set_integration_policy(repo, cross_provider_data_transfer="deny", shareable_fields=["subject"])
    account(project, service="gmail", account_id="acct-g", worker="claude", ops=["mail.read"])
    account(project, service="microsoft.outlook", account_id="acct-o", worker="codex", ops=["mail.read"])
    from saarathi.orchestration.project import Project

    _, _run_id, state = start(Project.load(repo))
    assert state is RunState.PAUSED
    assert executions(record, "T-002") == [], "the dependent worker must never be invoked"
