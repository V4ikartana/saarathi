import pytest

from saarathi.core.contracts import TaskPacket
from saarathi.core.errors import InvalidTransition
from saarathi.core.states import RunState, TaskState
from saarathi.storage.store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


def packet(run_id, tid="T-001"):
    return TaskPacket(
        task_id=tid,
        run_id=run_id,
        title="t",
        objective="o",
        acceptance_criteria=["a"],
        dependencies=[],
        allowed_paths=["src/**"],
        context_snapshot_id="ctx",
        required_capabilities=[],
        complexity="routine",
        risk="low",
        max_attempts=2,
        verification_commands=["true"],
    )


def test_transition_and_event_are_atomic(store):
    run = store.create_run(project_id="p", brief_path="b.md", brief_sha256="x")
    store.transition_run(run.id, RunState.INTAKE, actor="test")
    with pytest.raises(InvalidTransition):
        store.transition_run(run.id, RunState.COMPLETED, actor="test")
    assert store.get_run(run.id).state is RunState.INTAKE
    types = [e.type for e in store.events(run.id)]
    assert types == ["run.created", "run.transition"]


def test_idempotency_key_applies_once(store):
    run = store.create_run(project_id="p", brief_path="b.md", brief_sha256="x")
    store.transition_run(run.id, RunState.INTAKE, actor="t", idempotency_key="k1")
    store.transition_run(run.id, RunState.INTAKE, actor="t", idempotency_key="k1")  # replay: no error, no event
    assert len([e for e in store.events(run.id) if e.type == "run.transition"]) == 1


def test_pause_records_origin(store):
    run = store.create_run(project_id="p", brief_path="b.md", brief_sha256="x")
    store.transition_run(run.id, RunState.INTAKE, actor="t")
    paused = store.transition_run(run.id, RunState.PAUSED, actor="t", reason="user")
    assert paused.paused_from is RunState.INTAKE and paused.pause_reason == "user"
    resumed = store.transition_run(run.id, RunState.INTAKE, actor="t")
    assert resumed.paused_from is None


def test_task_lease_cleared_when_leaving_in_flight(store):
    run = store.create_run(project_id="p", brief_path="b.md", brief_sha256="x")
    store.insert_tasks(run.id, [packet(run.id)], actor="t")
    store.transition_task(run.id, "T-001", TaskState.READY, actor="t")
    t = store.transition_task(run.id, "T-001", TaskState.LEASED, actor="t", lease_owner="e1", lease_expires_at="z")
    assert t.lease_owner == "e1"
    t = store.transition_task(run.id, "T-001", TaskState.RETRY_WAIT, actor="t", increment_failures=True)
    assert t.lease_owner is None and t.failures == 1


def test_secrets_redacted_in_events(tmp_path):
    from saarathi.security.redaction import Redactor

    s = StateStore(tmp_path / "s.db", redact=Redactor({"MY_API_KEY": "supersecretvalue123"}))
    run = s.create_run(project_id="p", brief_path="b", brief_sha256="x")
    s.append_event("x", {"note": "token supersecretvalue123 and sk-ant-abcdefghijklmnop"}, actor="t", run_id=run.id)
    payload = s.events(run.id)[-1].payload["note"]
    assert "supersecretvalue123" not in payload and "sk-ant-abcdefghijklmnop" not in payload


def test_provider_cooldowns_escalate_then_exhaust_and_clear(store):
    a = store.record_provider_limit(
        "codex", exhausted=False, reason="429", cooldowns=[1, 2], exhausted_seconds=99, actor="t"
    )
    b = store.record_provider_limit(
        "codex", exhausted=False, reason="429", cooldowns=[1, 2], exhausted_seconds=99, actor="t"
    )
    c = store.record_provider_limit(
        "codex", exhausted=False, reason="429", cooldowns=[1, 2], exhausted_seconds=99, actor="t"
    )
    assert [a.state, b.state, c.state] == ["cooling_down", "cooling_down", "exhausted"]
    assert [a.consecutive, b.consecutive, c.consecutive] == [1, 2, 3] and a.until < b.until < c.until
    q = store.record_provider_limit(
        "claude", exhausted=True, reason="usage limit", cooldowns=[1], exhausted_seconds=9, actor="t"
    )
    assert q.state == "exhausted"
    assert store.clear_provider_health("codex", actor="t", reason="ok") and "codex" not in store.provider_health()
    assert not store.clear_provider_health("codex", actor="t", reason="ok")
