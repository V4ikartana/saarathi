"""P2b: dependency results reach a task only field by field, as policy permits."""

from saarathi.core.contracts import (
    IntegrationRequirement,
    IntegrationTrace,
    NamedOutput,
    RoutingDecision,
    TransferCheck,
)
from saarathi.orchestration.handoff import MAX_VALUE_CHARS, DependencyResult, dependency_results, plan_handoff

GMAIL = IntegrationRequirement(service="gmail", operation="mail.read", share_fields=["subject"])
OUT = (NamedOutput(name="subject", value="Quarterly numbers"), NamedOutput(name="body", value="secret body"))


def results(**deps):
    base = {"T-009": DependencyResult("T-009", tuple(deps), (), ())}
    base.update(deps)
    return base


def dep(tid, outputs=OUT, integrations=(), deps=()):
    return DependencyResult(tid, tuple(deps), tuple(integrations), tuple(outputs))


def decision(*transfers, chosen="codex-cli:default"):
    return RoutingDecision(
        decision_id="rd-1",
        task_id="T-009",
        chosen=chosen,
        reason="test",
        integration=IntegrationTrace(workspace="w", transfers=list(transfers)),
    )


def check(source="T-001", mode="same_provider", fields=("subject",), approved=True, to="codex-cli:default"):
    return TransferCheck(
        source_task=source,
        service="gmail",
        account="a",
        from_provider="anthropic",
        to_candidate=to,
        to_provider="openai",
        mode=mode,
        fields=list(fields),
        approved=approved,
    )


def run(rs, d=None):
    return plan_handoff("T-009", rs, d, lambda s: s.replace("secret", "[REDACTED]"))


def test_plain_dependency_passes_all_outputs():
    h = run(results(**{"T-001": dep("T-001")}))
    assert h.passed == ["T-001.subject", "T-001.body"] and h.inputs[1][2] == "[REDACTED] body"


def test_integration_dependency_passes_only_share_fields_same_provider():
    h = run(results(**{"T-001": dep("T-001", integrations=[GMAIL])}), decision(check()))
    assert h.passed == ["T-001.subject"]
    assert len(h.dropped) == 1 and h.dropped[0].startswith("T-001.body")


def test_unapproved_or_missing_transfer_check_drops_everything():
    deps = results(**{"T-001": dep("T-001", integrations=[GMAIL])})
    assert run(deps, decision(check(mode="require_approval", approved=False))).passed == []
    assert run(deps, decision()).passed == []  # no check for this worker: fail closed
    assert run(deps, None).passed == []


def test_check_for_another_candidate_does_not_count():
    deps = results(**{"T-001": dep("T-001", integrations=[GMAIL])})
    assert run(deps, decision(check(to="claude-code:opus"))).passed == []


def test_integration_data_cannot_be_laundered_through_an_intermediate_task():
    rs = results(
        **{
            "T-001": dep("T-001", integrations=[GMAIL]),
            "T-002": dep("T-002", outputs=[NamedOutput(name="digest", value="summary of mail")], deps=["T-001"]),
        }
    )
    rs["T-009"] = DependencyResult("T-009", ("T-002",), (), ())
    cross = run(rs, decision(check(mode="allow", fields=["subject"], approved=True)))
    assert "T-002.digest" not in cross.passed and cross.dropped[-1].startswith("T-002.digest")
    local = run(rs, decision(check(mode="same_provider")))
    assert "T-002.digest" in local.passed and "T-001.subject" in local.passed


def test_size_limits():
    big = [NamedOutput(name=f"f{i}", value="x" * 20_000) for i in range(6)]
    h = run(results(**{"T-001": dep("T-001", outputs=big)}))
    assert all(len(v) <= MAX_VALUE_CHARS + 20 for _, _, v in h.inputs)
    assert any("size limit" in d for d in h.dropped)


def test_failed_dependency_outputs_are_ignored():
    rows = [("T-001", (), (), {"status": "failed", "worker_report": {"outputs": [{"name": "a", "value": "b"}]}})]
    assert dependency_results(rows)["T-001"].outputs == ()
