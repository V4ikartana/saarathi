"""`saarathi integrations` and integration reasons in `saarathi route explain`."""

import json

import pytest
from typer.testing import CliRunner

from saarathi.cli.main import app
from saarathi.cli.output import AccountsOut, ConnectionOut, IntegrationsOut, ScanOut, StatusOut, VerifyOut
from saarathi.core.errors import ExitCode
from tests.e2e.helpers import init_project, make_script
from tests.e2e.test_integration_scenarios import itask, note, plan

runner = CliRunner()


@pytest.fixture(autouse=True)
def allow_fakes(monkeypatch):
    monkeypatch.setenv("SAARATHI_ALLOW_FAKE_PROVIDERS", "1")


def cli(repo, monkeypatch, *args):
    monkeypatch.chdir(repo)
    return runner.invoke(app, list(args))


def test_k_scan_reports_unverified_and_registration_is_declared(repo, tmp_path, monkeypatch):
    init_project(repo, make_script(tmp_path / "s.json", {}), tmp_path / "r.jsonl")
    scan = ScanOut.model_validate_json(cli(repo, monkeypatch, "integrations", "scan", "--json").stdout)
    assert {a.status for a in scan.adapters} == {"unverified"}
    assert all(a.registered_connections == [] for a in scan.adapters)
    assert "No integrations registered" in cli(repo, monkeypatch, "integrations", "list").stdout

    r = cli(
        repo,
        monkeypatch,
        "integrations",
        "register",
        "--adapter",
        "fake-claude",
        "--service",
        "gmail",
        "-o",
        "mail.read",
        "-o",
        "mail.draft",
        "--account",
        "acct-p",
        "--identity",
        "me@example.com",
        "--alias",
        "personal",
        "--connection-id",
        "conn-p",
        "--json",
    )
    assert r.exit_code == 0, r.output
    conn = ConnectionOut.model_validate_json(r.stdout)
    assert conn.account_label == "personal" and {c.evidence for c in conn.capabilities} == {"declared"}
    assert {c.effect for c in conn.capabilities} == {"read", "write"}

    scan = ScanOut.model_validate_json(cli(repo, monkeypatch, "integrations", "scan", "--json").stdout)
    claude = next(a for a in scan.adapters if a.adapter == "fake-claude")
    assert claude.status == "unverified" and claude.registered_connections == ["conn-p"]

    r = cli(repo, monkeypatch, "integrations", "verify", "conn-p", "--json")
    assert r.exit_code == ExitCode.AWAITING_INPUT
    assert VerifyOut.model_validate_json(r.stdout).status == "unavailable"
    r = cli(repo, monkeypatch, "integrations", "verify", "conn-p", "--confirm", "--json")
    out = VerifyOut.model_validate_json(r.stdout)
    assert r.exit_code == 0 and out.status == "user_confirmed" and "not provider verification" in out.detail

    listed = IntegrationsOut.model_validate_json(cli(repo, monkeypatch, "integrations", "list", "--json").stdout)
    assert {c.evidence for c in listed.connections[0].capabilities} == {"user_confirmed"}

    accounts = AccountsOut.model_validate_json(cli(repo, monkeypatch, "integrations", "accounts", "--json").stdout)
    [acct] = accounts.accounts
    assert acct.identity == "m***@example.com" and acct.identity_status == "user_declared"
    assert "me@example.com" not in cli(repo, monkeypatch, "integrations", "accounts").stdout

    assert cli(repo, monkeypatch, "integrations", "alias", "acct-p", "home").exit_code == 0
    assert cli(repo, monkeypatch, "integrations", "default", "gmail", "acct-p").exit_code == 0
    accounts = AccountsOut.model_validate_json(cli(repo, monkeypatch, "integrations", "accounts", "--json").stdout)
    assert accounts.accounts[0].is_default and accounts.accounts[0].aliases == ["home", "personal"]
    assert cli(repo, monkeypatch, "integrations", "default", "gmail", "none").exit_code == 0
    other = AccountsOut.model_validate_json(
        cli(repo, monkeypatch, "integrations", "accounts", "--workspace", "work", "--json").stdout
    )
    assert other.accounts == []

    r = cli(repo, monkeypatch, "integrations", "register", "--adapter", "nope", "--service", "gmail", "-o", "mail.read")
    assert r.exit_code != 0 and "not configured" in r.output
    assert cli(repo, monkeypatch, "integrations", "revoke", "conn-p").exit_code == 0
    listed = IntegrationsOut.model_validate_json(cli(repo, monkeypatch, "integrations", "list", "--json").stdout)
    assert listed.connections[0].status == "revoked"


def test_route_explain_shows_integration_rejections_and_approval_flow(repo, tmp_path, monkeypatch):
    p = plan(
        itask(
            "T-001", "Summarise personal mail", [{"service": "gmail", "operation": "mail.read", "account": "personal"}]
        )
    )
    init_project(repo, make_script(tmp_path / "s.json", {"T-001": [note("T-001")]}, plan=p), tmp_path / "r.jsonl")
    for args in (
        (
            "--adapter",
            "fake-claude",
            "--account",
            "acct-p",
            "--identity",
            "me@example.com",
            "--alias",
            "personal",
            "--connection-id",
            "conn-p",
        ),
        ("--adapter", "fake-codex", "--account", "acct-b", "--alias", "business", "--connection-id", "conn-b"),
    ):
        assert (
            cli(repo, monkeypatch, "integrations", "register", "--service", "gmail", "-o", "mail.read", *args).exit_code
            == 0
        )
        assert cli(repo, monkeypatch, "integrations", "verify", args[-1], "--confirm").exit_code == 0

    brief = tmp_path / "brief.md"
    brief.write_text("Summarise my personal mail")
    assert cli(repo, monkeypatch, "run", str(brief), "--non-interactive").exit_code == 3
    r = cli(repo, monkeypatch, "approve", "--non-interactive", "--json")
    assert StatusOut.model_validate_json(r.stdout).state == "PAUSED"

    explain = cli(repo, monkeypatch, "route", "explain", "T-001").stdout
    assert "account 'personal' (acct-p) via alias, identity user_declared" in explain
    assert "conn-p on worker fake-claude (fake-claude) [user_confirmed" in explain
    assert "approval required: identity:acct-p" in explain
    assert "saarathi integrations approve T-001 'identity:acct-p'" in explain
    assert "me@example.com" not in explain
    decisions = json.loads(cli(repo, monkeypatch, "route", "explain", "T-001", "--json").stdout)
    assert decisions[-1]["awaiting"] == "approval"
    assert any(
        "not authorized for adapter 'fake-codex'" in reason
        for c in decisions[-1]["rejected"]
        for reason in c["reasons"]
    )

    r = cli(repo, monkeypatch, "integrations", "approve", "T-001", "identity:acct-b")
    assert r.exit_code != 0 and "not an approval pending" in r.output
    assert cli(repo, monkeypatch, "integrations", "approve", "T-001", "identity:acct-p").exit_code == 0
    r = cli(repo, monkeypatch, "resume", "--non-interactive", "--json")
    assert StatusOut.model_validate_json(r.stdout).state == "AWAITING_REVIEW"
    explain = cli(repo, monkeypatch, "route", "explain", "T-001").stdout
    assert "chose fake-claude:opus" in explain and "selected connection(s): conn-p" in explain
    assert "approval granted: identity:acct-p" in explain


def test_select_validates_account_and_requirement(repo, tmp_path, monkeypatch):
    p = plan(itask("T-001", "Check Gmail", [{"service": "gmail", "operation": "mail.read"}]))
    init_project(repo, make_script(tmp_path / "s.json", {"T-001": [note("T-001")]}, plan=p), tmp_path / "r.jsonl")
    for adapter, acct in (("fake-claude", "acct-a"), ("fake-codex", "acct-b")):
        cli(
            repo,
            monkeypatch,
            "integrations",
            "register",
            "--adapter",
            adapter,
            "--service",
            "gmail",
            "-o",
            "mail.read",
            "--account",
            acct,
            "--connection-id",
            f"conn-{acct}",
        )
        cli(repo, monkeypatch, "integrations", "verify", f"conn-{acct}", "--confirm")
    # Identity-sensitive by default, and these accounts declare no identity: fail closed.
    brief = tmp_path / "brief.md"
    brief.write_text("Check my Gmail")
    cli(repo, monkeypatch, "run", str(brief), "--non-interactive")
    status = StatusOut.model_validate_json(cli(repo, monkeypatch, "approve", "--non-interactive", "--json").stdout)
    assert status.state == "PAUSED" and "account selection required" in status.tasks[0].blocked_reason

    assert "saarathi integrations select T-001 acct-a" in cli(repo, monkeypatch, "route", "explain", "T-001").stdout
    r = cli(repo, monkeypatch, "integrations", "select", "T-001", "acct-x")
    assert r.exit_code != 0 and "not a gmail account" in r.output
    assert cli(repo, monkeypatch, "integrations", "select", "T-001", "acct-a", "--requirement", "3").exit_code != 0
    assert cli(repo, monkeypatch, "integrations", "select", "T-001", "acct-a").exit_code == 0
    status = StatusOut.model_validate_json(cli(repo, monkeypatch, "resume", "--non-interactive", "--json").stdout)
    assert status.state == "PAUSED" and "unverified; identity-sensitive routing fails closed" in (
        status.tasks[0].blocked_reason
    )
