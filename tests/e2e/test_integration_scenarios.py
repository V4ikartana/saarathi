"""§12A scenarios through the real engine with scripted fake workers (no network, no accounts, no quota)."""

import asyncio

import pytest

from saarathi.core.states import RunState, TaskState
from saarathi.integrations.model import ConnectionStatus, Evidence, IdentityStatus
from saarathi.orchestration.engine import GRANT_PREFIX, SELECT_PREFIX, Engine
from saarathi.orchestration.project import Project
from tests.e2e.helpers import init_project, make_script, records, task
from tests.e2e.test_scenarios import ApproveIO


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


def itask(tid, title, integrations, deps=()):
    path = f"notes/{tid.lower()}.md"
    return {**task(tid, title, [path], f"test -f {path}", deps), "integrations": integrations}


def plan(*tasks):
    return {
        "summary": "Summarise mail into notes.",
        "architecture_notes": "One note per task.",
        "tasks": list(tasks),
        "integration_checks": ["git status"],
        "decisions": [],
    }


def note(tid):
    return {"write": {f"notes/{tid.lower()}.md": f"summary for {tid}\n"}}


def setup(repo, tmp_path, plan_, execute):
    script = make_script(tmp_path / "s.json", execute, plan=plan_)
    record = tmp_path / "rec.jsonl"
    init_project(repo, script, record)
    project = Project.load(repo)
    reg, ws = project.integrations, project.workspace
    reg.register_worker(ws, worker_id="claude", adapter="fake-claude", runtime="fake")
    reg.register_worker(ws, worker_id="codex", adapter="fake-codex", runtime="fake")
    return project, record


def account(project, *, service, account_id, worker, ops, alias=None):
    reg, ws = project.integrations, project.workspace
    reg.register_account(ws, service=service, account_id=account_id)
    reg.set_identity_status(ws, account_id, IdentityStatus.VERIFIED, method="test-probe")
    if alias:
        reg.set_alias(ws, account_id, alias)
    conn = reg.register_connection(
        ws, account_id=account_id, worker_id=worker, operations=ops, connection_id=f"conn-{account_id}"
    )
    reg.record_verification(ws, conn.id, evidence=Evidence.PROBED, method="test-probe")
    return conn.id


class HoldPlanIO(ApproveIO):
    def confirm(self, m):
        return False


def start(project, io=None):
    engine = Engine(project, io or ApproveIO())
    brief = project.root.parent / "brief.md"
    brief.write_text("Summarise my mail.")
    run_id = asyncio.run(engine.start(brief))
    return engine, run_id, asyncio.run(engine.drive(run_id))


def executions(record, task_id):
    return [r for r in records(record) if r["key"] == task_id]


def test_g_mixed_request_routes_each_service_to_its_worker(repo, tmp_path):
    p = plan(
        itask("T-001", "Summarise Gmail", [{"service": "gmail", "operation": "mail.read"}]),
        itask("T-002", "Summarise Outlook", [{"service": "microsoft.outlook", "operation": "mail.read"}]),
    )
    project, record = setup(repo, tmp_path, p, {"T-001": [note("T-001")], "T-002": [note("T-002")]})
    gmail = account(project, service="gmail", account_id="acct-g", worker="claude", ops=["mail.read"])
    outlook = account(project, service="microsoft.outlook", account_id="acct-o", worker="codex", ops=["mail.read"])

    _, run_id, state = start(project)
    assert state is RunState.AWAITING_REVIEW
    assert {r["adapter"] for r in executions(record, "T-001")} == {"fake-claude"}
    assert {r["adapter"] for r in executions(record, "T-002")} == {"fake-codex"}
    d1 = project.store.decisions(run_id, "T-001")[-1]
    d2 = project.store.decisions(run_id, "T-002")[-1]
    assert d1.integration.selected_connections == [gmail] and d2.integration.selected_connections == [outlook]
    assert any("not authorized for adapter 'fake-codex'" in r for c in d1.rejected for r in c.reasons)
    rows = project.store.query(
        "SELECT task_id, service, operation, effect FROM task_requirement WHERE run_id=?", [run_id]
    )
    assert sorted(tuple(r) for r in rows) == [
        ("T-001", "gmail", "mail.read", "read"),
        ("T-002", "microsoft.outlook", "mail.read", "read"),
    ]


def test_i_revocation_after_planning_pauses_without_fallback(repo, tmp_path):
    p = plan(
        itask(
            "T-001", "Summarise personal mail", [{"service": "gmail", "operation": "mail.read", "account": "personal"}]
        )
    )
    project, record = setup(repo, tmp_path, p, {"T-001": [note("T-001")]})
    conn = account(project, service="gmail", account_id="acct-a", worker="claude", ops=["mail.read"], alias="personal")
    account(project, service="gmail", account_id="acct-b", worker="codex", ops=["mail.read"], alias="business")

    engine, run_id, state = start(project, HoldPlanIO())
    assert state is RunState.AWAITING_PLAN_APPROVAL
    project.integrations.set_connection_status(project.workspace, conn, ConnectionStatus.REVOKED, reason="test")
    engine.approve_plan(run_id)
    state = asyncio.run(engine.drive(run_id))

    assert state is RunState.PAUSED
    t = project.store.get_task(run_id, "T-001")
    assert t.state is TaskState.BLOCKED and "is revoked" in t.blocked_reason
    assert executions(record, "T-001") == []  # no worker ran, so no side effect could happen
    # Resuming does not quietly fall back to the business mailbox on Codex.
    engine.resume(run_id)
    assert asyncio.run(engine.drive(run_id)) is RunState.PAUSED
    assert executions(record, "T-001") == []
    assert all(d.chosen is None for d in project.store.decisions(run_id, "T-001"))


def test_i_revocation_between_routing_and_dispatch_is_caught_by_recheck(repo, tmp_path, monkeypatch):
    p = plan(itask("T-001", "Summarise mail", [{"service": "gmail", "operation": "mail.read"}]))
    project, record = setup(repo, tmp_path, p, {"T-001": [note("T-001")]})
    conn = account(project, service="gmail", account_id="acct-a", worker="claude", ops=["mail.read"])

    original = Engine._spawn

    def revoke_then_spawn(self, run_id, t, model, decision_id=""):
        project.integrations.set_connection_status(project.workspace, conn, ConnectionStatus.REVOKED, reason="race")
        original(self, run_id, t, model, decision_id)

    monkeypatch.setattr(Engine, "_spawn", revoke_then_spawn)
    _, run_id, state = start(project)
    assert state is RunState.PAUSED
    t = project.store.get_task(run_id, "T-001")
    assert t.state is TaskState.BLOCKED and "recheck before dispatch failed" in t.blocked_reason
    assert executions(record, "T-001") == [] and project.store.attempts(run_id, "T-001") == []


def test_m_ambiguous_account_pauses_until_selected(repo, tmp_path):
    p = plan(itask("T-001", "Check my Gmail", [{"service": "gmail", "operation": "mail.read"}]))
    project, record = setup(repo, tmp_path, p, {"T-001": [note("T-001")]})
    account(project, service="gmail", account_id="acct-a", worker="claude", ops=["mail.read"])
    account(project, service="gmail", account_id="acct-b", worker="codex", ops=["mail.read"])

    engine, run_id, state = start(project)
    assert state is RunState.PAUSED and executions(record, "T-001") == []
    decision = project.store.decisions(run_id, "T-001")[-1]
    assert decision.awaiting == "account_selection"
    assert [c.account for c in decision.integration.account_choices] == ["acct-a", "acct-b"]

    project.store.set_control(run_id, f"{SELECT_PREFIX}T-001:0", "acct-b", actor="user")
    engine.resume(run_id)
    assert asyncio.run(engine.drive(run_id)) is RunState.AWAITING_REVIEW
    assert {r["adapter"] for r in executions(record, "T-001")} == {"fake-codex"}


def test_side_effect_approval_is_single_use(repo, tmp_path):
    p = plan(itask("T-001", "Send the reply", [{"service": "gmail", "operation": "mail.send"}]))
    project, record = setup(repo, tmp_path, p, {"T-001": [{"fail": "process"}, note("T-001")]})
    account(project, service="gmail", account_id="acct-a", worker="claude", ops=["mail.read", "mail.send"])

    engine, run_id, state = start(project)
    assert state is RunState.PAUSED and executions(record, "T-001") == []
    [need] = project.store.decisions(run_id, "T-001")[-1].integration.approvals_required
    assert need.key == "side_effect:T-001:0:mail.send@acct-a"

    project.store.set_control(run_id, GRANT_PREFIX + need.key, "user", actor="user")
    engine.resume(run_id)
    state = asyncio.run(engine.drive(run_id))
    # The first attempt failed after the worker ran; the retry needs a fresh approval, so nothing is re-sent.
    assert state is RunState.PAUSED
    assert len(executions(record, "T-001")) == 1
    assert project.store.decisions(run_id, "T-001")[-1].awaiting == "approval"
    assert project.store.controls(run_id, GRANT_PREFIX) == {}
