"""§12A acceptance scenarios G-P at the routing layer, against the real SQLite registry.

Workers are fake: nothing here talks to Gmail, Outlook or any provider. Service names are opaque.
"""

import sqlite3

import pytest
from pydantic import ValidationError

from saarathi.core.contracts import (
    Complexity,
    IntegrationRequirement,
    ModelCapabilities,
    ModelDescriptor,
    ModelMetadata,
    ModelPricing,
    PlannedTask,
    ReasoningTier,
    Transport,
)
from saarathi.core.errors import ConfigError
from saarathi.core.policy import parse_policy
from saarathi.integrations.discovery import NoDocumentedInventory, discovery_for
from saarathi.integrations.model import (
    ConnectionStatus,
    DataSource,
    Effect,
    Evidence,
    IdentityStatus,
    effect_of,
)
from saarathi.integrations.registry import SqliteIntegrationRegistry
from saarathi.routing.router import RoutingRequest, RuntimeLoad, route
from saarathi.storage.store import StateStore

CAPS = ("code_generation", "workspace_write", "structured_output")
WS = "home"
CLAUDE = "claude-code:opus"
CODEX = "codex-cli:default"


def model(adapter, mid, provider, tier):
    return ModelDescriptor(
        id=mid,
        provider=provider,
        adapter=adapter,
        transport=Transport.CLI,
        available=True,
        capabilities=ModelCapabilities(
            code_generation=True,
            repository_tools=True,
            workspace_write=True,
            structured_output=True,
            reasoning_tier=tier,
        ),
        pricing=ModelPricing(metered=False),
        metadata=ModelMetadata(source="test"),
    )


MODELS = [
    model("claude-code", "opus", "anthropic", ReasoningTier.FRONTIER),
    model("codex-cli", "default", "openai", ReasoningTier.ECONOMY),
]


def policy(**integrations):
    return parse_policy(
        {
            "orchestration": {"lead": {"adapter": "claude-code", "model": "opus"}},
            "routing": {"allowed_providers": ["anthropic", "openai"], "allowed_models": ["anthropic/*", "openai/*"]},
            "integrations": {"workspace": WS, **integrations},
            "providers": {
                "claude-code": {"models": [{"id": "opus", "reasoning_tier": "frontier"}]},
                "codex-cli": {"models": [{"id": "default"}]},
            },
        }
    )


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


@pytest.fixture
def reg(store):
    r = SqliteIntegrationRegistry(store)
    for ws in (WS, "work"):
        r.register_worker(ws, worker_id="claude-primary", adapter="claude-code", runtime="claude-code")
        r.register_worker(ws, worker_id="codex-primary", adapter="codex-cli", runtime="codex-cli")
    return r


def connect(
    reg,
    *,
    service,
    account,
    worker,
    ops,
    identity=None,
    identity_status=IdentityStatus.VERIFIED,
    evidence=Evidence.PROBED,
    alias=None,
    tenant=None,
    ws=WS,
):
    acct = reg.register_account(ws, service=service, account_id=account, identity=identity, tenant=tenant)
    if identity_status is IdentityStatus.VERIFIED:
        reg.set_identity_status(ws, acct.id, IdentityStatus.VERIFIED, method="test-probe")
    if alias:
        reg.set_alias(ws, acct.id, alias)
    conn = reg.register_connection(
        ws, account_id=acct.id, worker_id=worker, operations=ops, connection_id=f"conn-{worker}-{account}"
    )
    if evidence is not Evidence.DECLARED:
        reg.record_verification(ws, conn.id, evidence=evidence, method="test")
    return conn.id


def request(*reqs, task="T-001", complexity=Complexity.STANDARD, **kw):
    return RoutingRequest(
        task_id=task, required_capabilities=CAPS, complexity=complexity, integrations=tuple(reqs), **kw
    )


def need(service, operation, account=None, **kw):
    return IntegrationRequirement(service=service, operation=operation, account=account, **kw)


def go(reg, req, pol=None, models=MODELS, load=None, ws=WS):
    return route(req, models, pol or policy(), load, reg.snapshot(ws))


def rejected(decision, candidate):
    return " | ".join(r for c in decision.rejected if c.candidate == candidate for r in c.reasons)


# ----------------------------------------------------------------------------- G


def test_g_each_service_routes_only_to_its_connected_worker(reg):
    gmail = connect(reg, service="gmail", account="acct-g", worker="claude-primary", ops=["mail.read"])
    outlook = connect(reg, service="microsoft.outlook", account="acct-o", worker="codex-primary", ops=["mail.read"])

    d1 = go(reg, request(need("gmail", "mail.read"), task="T-001"))
    assert d1.chosen == CLAUDE
    assert d1.integration.selected_connections == [gmail]
    assert "not authorized for adapter 'codex-cli'" in rejected(d1, CODEX)

    # Outlook goes to Codex even though Claude scores higher on tier fit for complex work.
    d2 = go(reg, request(need("microsoft.outlook", "mail.read"), task="T-002", complexity=Complexity.COMPLEX))
    assert d2.chosen == CODEX
    assert d2.integration.selected_connections == [outlook]
    assert "not authorized for adapter 'claude-code'" in rejected(d2, CLAUDE)
    assert d2.integration.requirements[0].resolved_by == "only_account"


def test_g_mixed_request_is_separate_tasks():
    task = {
        "id": "T-001",
        "title": "t",
        "objective": "o",
        "acceptance_criteria": ["a"],
        "dependencies": [],
        "allowed_paths": ["notes/**"],
        "verification_commands": ["true"],
        "required_capabilities": [],
        "complexity": "routine",
        "risk": "low",
    }
    with pytest.raises(ValidationError):  # one requirement selects exactly one account
        PlannedTask.model_validate(
            {**task, "integrations": [{"service": "gmail", "operation": "mail.read", "account": "*"}]}
        )
    with pytest.raises(ValidationError):
        need("gmail", "mail.read", account="personal,business")
    ok = PlannedTask.model_validate({**task, "integrations": [{"service": "gmail", "operation": "mail.read"}]})
    assert ok.integrations[0].identity_sensitive


# ----------------------------------------------------------------------------- H


def test_h_operation_mismatch_never_falls_back(reg):
    connect(reg, service="gmail", account="acct-g", worker="claude-primary", ops=["mail.read", "mail.draft"])
    req = request(need("gmail", "mail.send"))
    d = go(reg, req)
    assert d.chosen is None
    assert "no eligible connection permits gmail/mail.send" in d.reason
    conn = d.integration.requirements[0].connections[0]
    assert not conn.eligible and "does not permit mail.send (permits mail.draft, mail.read)" in conn.reasons[0]
    # A granted approval does not create a permission that no connection has.
    key = "side_effect:T-001:0:mail.send@acct-g"
    d = go(reg, request(need("gmail", "mail.send"), approvals=frozenset({key})))
    assert d.chosen is None


def test_h_send_needs_authorized_connection_and_explicit_approval(reg):
    connect(reg, service="gmail", account="acct-g", worker="claude-primary", ops=["mail.read", "mail.send"])
    d = go(reg, request(need("gmail", "mail.send")))
    assert d.chosen is None and d.awaiting == "approval"
    [approval] = d.integration.approvals_required
    assert approval.kind == "side_effect" and approval.key == "side_effect:T-001:0:mail.send@acct-g"
    d = go(reg, request(need("gmail", "mail.send"), approvals=frozenset({approval.key})))
    assert d.chosen == CLAUDE and d.integration.approvals_granted == [approval.key]
    assert go(reg, request(need("gmail", "mail.send")), policy(external_side_effects="deny")).chosen is None


def test_operation_effects_fail_closed():
    assert effect_of("mail.read") is Effect.READ and effect_of("mail.search") is Effect.READ
    assert effect_of("mail.draft") is Effect.WRITE
    assert effect_of("mail.send") is Effect.EXTERNAL and effect_of("files.share") is Effect.EXTERNAL
    assert effect_of("mail.teleport") is Effect.EXTERNAL  # unknown verb


# ----------------------------------------------------------------------------- I


def test_i_revoked_or_stale_connection_blocks_without_fallback(reg):
    conn = connect(reg, service="gmail", account="acct-a", worker="claude-primary", ops=["mail.read"], alias="personal")
    connect(reg, service="gmail", account="acct-b", worker="codex-primary", ops=["mail.read"], alias="business")
    assert go(reg, request(need("gmail", "mail.read", account="personal"))).chosen == CLAUDE

    reg.set_connection_status(WS, conn, ConnectionStatus.REVOKED, reason="test")
    d = go(reg, request(need("gmail", "mail.read", account="personal")))
    assert d.chosen is None and "is revoked" in d.integration.requirements[0].connections[0].reasons[0]
    assert d.integration.requirements[0].account == "acct-a"  # never the business mailbox

    reg.set_connection_status(WS, conn, ConnectionStatus.STALE, reason="test")
    assert go(reg, request(need("gmail", "mail.read", account="personal"))).chosen is None


def test_i_old_verification_is_not_eligible(reg, store):
    connect(reg, service="gmail", account="acct-a", worker="claude-primary", ops=["mail.read"])
    with store.transaction() as conn:
        conn.execute("UPDATE integration_capability SET checked_at='2020-01-01T00:00:00+00:00'")
    d = go(reg, request(need("gmail", "mail.read")))
    assert d.chosen is None and "stale" in d.integration.requirements[0].connections[0].reasons[0]


# ----------------------------------------------------------------------------- J


def source(provider="anthropic", fields=("subject", "from", "body"), tenant=None, task="T-001", account="acct-g"):
    return DataSource(
        task_id=task, service="gmail", account_id=account, tenant=tenant, provider=provider, fields=fields
    )


def synthesis(*sources, approvals=frozenset()):
    return RoutingRequest(
        task_id="T-003",
        required_capabilities=CAPS,
        complexity=Complexity.STANDARD,
        data_sources=sources,
        approvals=approvals,
    )


def test_j_cross_provider_transfer_denied(reg):
    codex_only = [MODELS[1]]
    d = route(synthesis(source()), codex_only, policy(cross_provider_data_transfer="deny"), None, reg.snapshot(WS))
    assert d.chosen is None and "is denied by integrations.cross_provider_data_transfer" in rejected(d, CODEX)
    # The same-provider worker may synthesize without any transfer.
    d = route(synthesis(source()), MODELS, policy(cross_provider_data_transfer="deny"), None, reg.snapshot(WS))
    assert d.chosen == CLAUDE and d.integration.transfers[0].mode == "same_provider"


def test_j_transfer_waits_for_approval_and_shares_only_permitted_fields(reg):
    codex_only = [MODELS[1]]
    pol = policy(cross_provider_data_transfer="require_approval", shareable_fields=["subject", "from"])
    d = route(synthesis(source()), codex_only, pol, None, reg.snapshot(WS))
    assert d.chosen is None and d.awaiting == "approval"
    [approval] = d.integration.approvals_required
    assert approval.key == "data_transfer:T-003:T-001:openai" and approval.kind == "data_transfer"

    d = route(synthesis(source(), approvals=frozenset({approval.key})), codex_only, pol, None, reg.snapshot(WS))
    assert d.chosen == CODEX
    [transfer] = d.integration.transfers
    assert transfer.approved and transfer.fields == ["subject", "from"]  # body never crosses

    nothing_shareable = policy(cross_provider_data_transfer="allow")
    d = route(synthesis(source()), codex_only, nothing_shareable, None, reg.snapshot(WS))
    assert d.chosen is None and "may cross providers" in rejected(d, CODEX)


def test_j_enterprise_tenant_prohibits_transfer_even_when_allowed(reg):
    pol = policy(cross_provider_data_transfer="allow", shareable_fields=["subject"], no_transfer_tenants=["acme"])
    d = route(synthesis(source(tenant="acme")), [MODELS[1]], pol, None, reg.snapshot(WS))
    assert d.chosen is None and "tenant 'acme' prohibits" in rejected(d, CODEX)


# ----------------------------------------------------------------------------- K


def test_k_cli_without_inventory_reports_unverified():
    for runtime in ("claude-code", "codex-cli", "fake"):
        report = discovery_for(runtime).scan(runtime)
        assert report.status == "unverified" and report.connections == ()
        assert "no documented interface" in report.detail


def test_k_declared_connection_is_not_routable_until_verified(reg):
    conn = connect(
        reg, service="gmail", account="acct-g", worker="claude-primary", ops=["mail.read"], evidence=Evidence.DECLARED
    )
    d = go(reg, request(need("gmail", "mail.read")))
    assert d.chosen is None and "only declared, not verified" in d.integration.requirements[0].connections[0].reasons[0]
    snap = reg.snapshot(WS)
    probe = NoDocumentedInventory("claude-code").probe(snap.connections[0], "mail.read")
    assert probe.status == "unavailable"
    reg.record_verification(WS, conn, evidence=Evidence.USER_CONFIRMED, method="user_confirmation")
    d = go(reg, request(need("gmail", "mail.read")))
    assert d.chosen == CLAUDE and d.integration.requirements[0].connections[0].evidence == "user_confirmed"
    # An operator may accept declared capabilities, and the trace still says they were declared.
    other = connect(
        reg, service="github", account="acct-gh", worker="codex-primary", ops=["repo.read"], evidence=Evidence.DECLARED
    )
    d = go(reg, request(need("github", "repo.read")), policy(require_verified_integration=False))
    assert d.chosen == CODEX and d.integration.selected_connections == [other]
    assert d.integration.requirements[0].connections[0].evidence == "declared"


# ----------------------------------------------------------------------------- L


def test_l_same_service_different_accounts_never_cross(reg):
    connect(
        reg,
        service="gmail",
        account="acct-a",
        worker="claude-primary",
        ops=["mail.read"],
        identity="personal@gmail.com",
        alias="personal",
    )
    conn_b = connect(
        reg,
        service="gmail",
        account="acct-b",
        worker="codex-primary",
        ops=["mail.read"],
        identity="business@gmail.com",
        alias="business",
    )
    for selector, how in (("business", "alias"), ("acct-b", "id"), ("Business@Gmail.com", "identity")):
        d = go(reg, request(need("gmail", "mail.read", account=selector), complexity=Complexity.COMPLEX))
        assert d.chosen == CODEX, selector
        assert d.integration.requirements[0].account == "acct-b"
        assert d.integration.requirements[0].resolved_by == how
        assert d.integration.selected_connections == [conn_b]
        assert "not authorized for adapter 'claude-code'" in rejected(d, CLAUDE)

    reg.set_connection_status(WS, conn_b, ConnectionStatus.REVOKED, reason="test")
    d = go(reg, request(need("gmail", "mail.read", account="business")))
    assert d.chosen is None and d.integration.requirements[0].account == "acct-b"

    d = go(reg, request(need("gmail", "mail.read", account="shared")))
    assert d.chosen is None and "never substitutes another account" in d.reason


# ----------------------------------------------------------------------------- M


def test_m_ambiguous_account_pauses_and_never_picks_by_cost(reg):
    connect(reg, service="gmail", account="acct-a", worker="claude-primary", ops=["mail.read"], alias="personal")
    connect(reg, service="gmail", account="acct-b", worker="codex-primary", ops=["mail.read"], alias="business")
    d = go(reg, request(need("gmail", "mail.read")))
    assert d.chosen is None and d.awaiting == "account_selection"
    assert {c.account for c in d.integration.account_choices} == {"acct-a", "acct-b"}
    assert "never chooses by cost" in d.reason

    sel = go(reg, request(need("gmail", "mail.read"), selections=((0, "acct-b"),)))
    assert sel.chosen == CODEX and sel.integration.requirements[0].resolved_by == "selection"

    # A default in another workspace has no effect here ...
    connect(reg, service="gmail", account="acct-w", worker="claude-primary", ops=["mail.read"], ws="work")
    reg.set_default("work", "gmail", "acct-w")
    assert go(reg, request(need("gmail", "mail.read"))).awaiting == "account_selection"
    # ... and a default in this workspace resolves only here.
    reg.set_default(WS, "gmail", "acct-a")
    d = go(reg, request(need("gmail", "mail.read")))
    assert d.chosen == CLAUDE and d.integration.requirements[0].resolved_by == "default"
    reg.set_default(WS, "gmail", None)
    assert go(reg, request(need("gmail", "mail.read"))).awaiting == "account_selection"


# ----------------------------------------------------------------------------- N


def test_n_same_account_two_workers_either_may_run_and_connection_is_recorded(reg):
    c1 = connect(reg, service="gmail", account="acct-a", worker="claude-primary", ops=["mail.read"])
    c2 = reg.register_connection(
        WS, account_id="acct-a", worker_id="codex-primary", operations=["mail.read"], connection_id="conn-codex-a"
    ).id
    reg.record_verification(WS, c2, evidence=Evidence.PROBED, method="test")

    d = go(reg, request(need("gmail", "mail.read"), complexity=Complexity.COMPLEX))
    assert d.chosen == CLAUDE and d.alternatives == [CODEX]
    assert d.integration.candidate_connections == {CLAUDE: [c1], CODEX: [c2]}
    assert d.integration.selected_connections == [c1]

    busy = RuntimeLoad(in_flight={CLAUDE: 1})
    d = go(reg, request(need("gmail", "mail.read")), load=busy)
    assert d.chosen == CODEX and d.integration.selected_connections == [c2]


# ----------------------------------------------------------------------------- O


def test_o_multi_account_tasks_route_separately_and_synthesis_honours_policy(reg):
    a = connect(reg, service="gmail", account="acct-a", worker="claude-primary", ops=["mail.search"], alias="personal")
    b = connect(reg, service="gmail", account="acct-b", worker="codex-primary", ops=["mail.search"], alias="business")
    t1 = go(reg, request(need("gmail", "mail.search", account="personal"), task="T-001"))
    t2 = go(reg, request(need("gmail", "mail.search", account="business"), task="T-002"))
    assert (t1.chosen, t1.integration.selected_connections) == (CLAUDE, [a])
    assert (t2.chosen, t2.integration.selected_connections) == (CODEX, [b])

    sources = (
        source("anthropic", ("subject",), task="T-001", account="acct-a"),
        source("openai", ("subject",), task="T-002", account="acct-b"),
    )
    denied = route(synthesis(*sources), MODELS, policy(cross_provider_data_transfer="deny"), None, reg.snapshot(WS))
    assert denied.chosen is None and len(denied.rejected) == 2  # every worker would receive the other's mail

    pol = policy(cross_provider_data_transfer="require_approval", shareable_fields=["subject"])
    waiting = route(synthesis(*sources), MODELS, pol, None, reg.snapshot(WS))
    assert waiting.awaiting == "approval"
    keys = {a.key for a in waiting.integration.approvals_required}
    assert keys == {"data_transfer:T-003:T-002:anthropic", "data_transfer:T-003:T-001:openai"}
    approved = route(
        synthesis(*sources, approvals=frozenset({"data_transfer:T-003:T-002:anthropic"})),
        MODELS,
        pol,
        None,
        reg.snapshot(WS),
    )
    assert approved.chosen == CLAUDE


# ----------------------------------------------------------------------------- P


def test_p_user_declared_identity_cannot_masquerade_as_verified(reg):
    connect(
        reg,
        service="gmail",
        account="acct-d",
        worker="claude-primary",
        ops=["mail.read"],
        identity="business@gmail.com",
        identity_status=IdentityStatus.USER_DECLARED,
    )
    d = go(reg, request(need("gmail", "mail.read", account="business@gmail.com")))
    assert d.chosen is None and d.awaiting == "approval"
    trace = d.integration.requirements[0]
    assert trace.identity_status == "user_declared" and trace.resolved_by == "identity"
    assert d.integration.approvals_required[0].key == "identity:acct-d"
    assert "not provider-verified" in d.integration.approvals_required[0].detail
    ok = go(
        reg, request(need("gmail", "mail.read", account="business@gmail.com"), approvals=frozenset({"identity:acct-d"}))
    )
    assert ok.chosen == CLAUDE and ok.integration.requirements[0].identity_status == "user_declared"
    denied = go(reg, request(need("gmail", "mail.read")), policy(user_declared_identity="deny"))
    assert denied.chosen is None and "only user-declared" in denied.reason
    # Identity-insensitive work (e.g. public data) does not need the identity at all.
    assert go(reg, request(need("gmail", "mail.read", identity_sensitive=False))).chosen == CLAUDE


def test_p_unverified_identity_fails_closed(reg):
    connect(
        reg,
        service="gmail",
        account="acct-u",
        worker="claude-primary",
        ops=["mail.read"],
        identity_status=IdentityStatus.UNVERIFIED,
    )
    d = go(reg, request(need("gmail", "mail.read")))
    assert d.chosen is None and "fails closed" in d.reason


def test_p_tenant_isolation_of_accounts_aliases_and_defaults(reg, store, tmp_path):
    connect(
        reg,
        service="gmail",
        account="acct-a",
        worker="claude-primary",
        ops=["mail.read"],
        identity="boss@corp.example",
        alias="business",
    )
    reg.set_default(WS, "gmail", "acct-a")
    work = reg.snapshot("work")
    assert work.accounts == {} and work.aliases == {} and work.defaults == {}
    d = go(reg, request(need("gmail", "mail.read", account="business")), ws="work")
    assert d.chosen is None and "workspace 'home'" not in d.reason
    d = go(reg, request(need("gmail", "mail.read", account="boss@corp.example")), ws="work")
    assert d.chosen is None and "boss@corp.example" not in d.model_dump_json()  # identity masked in the trace

    # Identities are stored as a masked hint and a workspace-salted digest, never in plain text.
    raw = sqlite3.connect(tmp_path / "state.db").iterdump()
    assert not any("boss@corp.example" in line for line in raw)
    assert reg.snapshot(WS).accounts["acct-a"].identity_hint == "b***s@corp.example"


# ----------------------------------------------------------------------------- registry rules


def test_registry_rules(reg):
    with pytest.raises(ConfigError, match="already runs as worker"):
        reg.register_worker(WS, worker_id="claude-second", adapter="claude-code", runtime="claude-code")
    connect(reg, service="gmail", account="acct-a", worker="claude-primary", ops=["mail.read"])
    connect(reg, service="microsoft.outlook", account="acct-o", worker="codex-primary", ops=["mail.read"])
    with pytest.raises(ConfigError, match="not 'gmail'"):
        reg.set_default(WS, "gmail", "acct-o")
    with pytest.raises(ConfigError, match="invalid operation"):
        reg.register_connection(WS, account_id="acct-a", worker_id="claude-primary", operations=["send"])
    reg.set_alias(WS, "acct-a", "personal")
    reg.set_alias(WS, "acct-o", "personal")  # same alias, different service: both stand
    assert reg.snapshot(WS).aliases == {("gmail", "personal"): "acct-a", ("microsoft.outlook", "personal"): "acct-o"}


def test_requests_without_integrations_are_unchanged(reg):
    d = route(
        RoutingRequest(task_id="T-001", required_capabilities=CAPS, complexity=Complexity.COMPLEX), MODELS, policy()
    )
    assert d.chosen == CLAUDE and d.integration is None


def test_existing_v1_database_upgrades_in_place(tmp_path):
    from saarathi.storage.db import MIGRATIONS, _split, connect

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    for statement in _split(MIGRATIONS[0]):
        old.execute(statement)
    old.execute("PRAGMA user_version = 1")
    old.commit()
    old.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "worker_instance",
        "integration_account",
        "integration_connection",
        "integration_capability",
        "account_alias",
        "account_default",
        "task_requirement",
        "provider_health",
        "escalations",
        "runs",
    } <= tables
