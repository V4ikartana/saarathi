"""Desktop bridge (ADR-0013) through its real stdio protocol, with real detached drivers and scripted fake providers."""

import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

from saarathi.bridge.drivers import INTERRUPT_CONTROL
from saarathi.storage.store import StateStore
from tests.e2e.helpers import GOOD_T1, GOOD_T2, init_project, make_script


class BridgeClient:
    def __init__(self, env, cwd):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "saarathi", "bridge", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=cwd,
        )
        self.responses: queue.Queue = queue.Queue()
        self.notifications: list[dict] = []
        self._next = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            msg = json.loads(line)
            if "id" in msg and "method" not in msg:
                self.responses.put(msg)
            else:
                self.notifications.append(msg)

    def raw(self, line, timeout=30):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return self.responses.get(timeout=timeout)

    def call(self, method, timeout=60, **params):
        self._next += 1
        msg = self.raw(json.dumps({"jsonrpc": "2.0", "id": self._next, "method": method, "params": params}), timeout)
        assert msg["id"] == self._next
        return msg

    def ok(self, method, **params):
        msg = self.call(method, **params)
        assert "result" in msg, msg
        return msg["result"]

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.wait(10)


@pytest.fixture
def env(tmp_path):
    return dict(os.environ, SAARATHI_ALLOW_FAKE_PROVIDERS="1", SAARATHI_DATA_DIR=str(tmp_path / "data"))


@pytest.fixture
def client(env, tmp_path):
    c = BridgeClient(env, tmp_path)
    yield c
    c.close()


def wait_for(client, project, run_id, predicate, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.ok("runs.status", project=project, run=run_id)
        if predicate(snap):
            return snap
        time.sleep(0.25)
    pytest.fail(f"condition not reached; last state {snap['state']}")


def setup_project(repo, tmp_path, execute):
    init_project(repo, make_script(tmp_path / "s.json", execute), tmp_path / "rec.jsonl")
    return str(repo)


def test_protocol_handshake_and_errors(client, repo):
    init = client.ok("system.initialize", client="test", protocol=1)
    assert init["protocol"] == 1 and init["saarathi_version"]
    bad = client.call("system.initialize", protocol=99)
    assert bad["error"]["data"]["kind"] == "incompatible"
    assert client.call("no.such.method")["error"]["code"] == -32601
    params = client.call("sessions.create", nonsense=True)
    assert params["error"]["data"]["kind"] == "invalid_params"
    assert client.raw("{not json")["error"]["code"] == -32700
    opened = client.ok("projects.open", path=str(repo))
    assert opened == {"root": str(repo.resolve()), "name": "repo", "initialized": False}
    assert client.call("projects.open", path="/")["error"]["data"]["kind"] == "not_a_repo"


def test_session_lifecycle_through_to_completion(client, repo, tmp_path):
    project = setup_project(repo, tmp_path, {"T-001": [GOOD_T1], "T-002": [GOOD_T2]})
    info = client.ok("projects.open", path=project)
    assert info["initialized"] and info["active_run"] is None
    session = client.ok("sessions.create", project=project, title="Health widget")
    client.ok("subscribe", project=project)
    sent = client.ok(
        "sessions.send", project=project, session=session["id"], text="Add health and widget", idempotency_key="k-1"
    )
    run_id = sent["run_id"]
    snap = wait_for(client, project, run_id, lambda s: s["pending_approval"] == "plan" and not s["driver"]["running"])
    assert snap["plan"]["tasks"], "the driver exits once the run waits for a person"

    view = client.ok("sessions.get", project=project, session=session["id"])
    kinds = [e["kind"] for e in view["conversation"]]
    assert kinds[0] == "message" and "plan" in kinds and "lead" in kinds
    assert any(n["method"] == "events/appended" for n in client.notifications), "live events were pushed"

    approved = client.ok("runs.approve", project=project, run=run_id, idempotency_key="approve-1")
    assert approved["approved"] == "plan"
    snap = wait_for(client, project, run_id, lambda s: s["pending_approval"] == "review")
    assert {t["state"] for t in snap["tasks"]} == {"SUCCEEDED"}
    diff = client.ok("tasks.diff", project=project, run=run_id, task="T-001")
    assert "app/health.py" in {f["path"] for f in diff["files"]} and "def health" in diff["diff"]
    assert client.ok("tasks.logs", project=project, run=run_id, task="T-001")["events"]
    assert_inspection(client, project, run_id)

    done = client.ok("runs.approve", project=project, run=run_id)
    assert done["approved"] == "review" and done["state"] == "COMPLETED"
    listed = client.ok("sessions.list", project=project)
    assert listed[0]["last_state"] == "COMPLETED" and listed[0]["run_count"] == 1
    view = client.ok("sessions.get", project=project, session=session["id"])
    assert any(e["kind"] == "review" for e in view["conversation"])
    assert any(e["kind"] == "delegation" for e in view["conversation"]), "agent delegation is visible"


def assert_inspection(client, project, run_id):
    """M3 data: everything the task inspector, timeline, diff viewer and log viewer show comes from these calls."""
    timeline = client.ok("runs.timeline", project=project, run=run_id)
    assert [(t["id"], t["depth"]) for t in timeline["tasks"]] == [("T-001", 0), ("T-002", 0)]
    spans = {a["task_id"]: a for a in timeline["attempts"]}
    assert set(spans) == {"T-001", "T-002"}
    assert all(a["outcome"] == "succeeded" and a["finished_at"] >= a["started_at"] for a in spans.values())

    inspected = client.ok("tasks.inspect", project=project, run=run_id, task="T-001")
    assert inspected["task"]["title"] == "Health endpoint" and inspected["task"]["state"] == "SUCCEEDED"
    assert inspected["task"]["verification_commands"] and inspected["task"]["allowed_paths"]
    (attempt,) = inspected["attempts"]
    assert attempt["result"]["verification"][0]["exit_code"] == 0
    assert "app/health.py" in attempt["result"]["changed_files"]
    (decision,) = inspected["decisions"]
    assert decision["chosen"] and decision["reason"] and decision["decision_id"] == attempt["routing_decision_id"]

    diff = client.ok("tasks.diff", project=project, run=run_id, task="T-001")
    health = next(f for f in diff["files"] if f["path"] == "app/health.py")
    assert health["added"] >= 2 and not health["binary"]
    file = client.ok("tasks.file", project=project, run=run_id, task="T-001", path="app/health.py")
    assert file["original"] == "" and "def health" in file["modified"], "an added file has no original side"
    outside = client.call("tasks.file", project=project, run=run_id, task="T-001", path="ui/widget.py")
    assert outside["error"]["data"]["kind"] == "invalid_params", "only paths in the task's own change are readable"
    escape = client.call("tasks.file", project=project, run=run_id, task="T-001", path="../../etc/passwd")
    assert escape["error"]["data"]["kind"] == "invalid_params"

    logs = client.ok("tasks.logs", project=project, run=run_id, task="T-001")["logs"]
    assert any(log["name"].startswith("attempt-1/verify-") for log in logs), [log["name"] for log in logs]


def test_send_is_idempotent_and_one_run_per_project(client, repo, tmp_path):
    project = setup_project(repo, tmp_path, {})
    a = client.ok("sessions.create", project=project, title="A")
    b = client.ok("sessions.create", project=project, title="B")
    first = client.ok("sessions.send", project=project, session=a["id"], text="x", idempotency_key="same")
    again = client.ok("sessions.send", project=project, session=a["id"], text="x", idempotency_key="same")
    assert first["run_id"] == again["run_id"]
    busy = client.call("sessions.send", project=project, session=b["id"], text="y")
    assert busy["error"]["data"]["kind"] == "project_busy" and a["id"] in busy["error"]["data"]["hint"]
    view = client.ok("sessions.get", project=project, session=a["id"])
    assert [e for e in view["conversation"] if e["kind"] == "message"] and len(view["runs"]) == 1
    assert view["run_order"] == [first["run_id"]]


def test_cancel_interrupts_the_driver(client, repo, tmp_path):
    project = setup_project(repo, tmp_path, {"T-001": [GOOD_T1], "T-002": [{"sleep": 60, **GOOD_T2}]})
    session = client.ok("sessions.create", project=project)
    run_id = client.ok("sessions.send", project=project, session=session["id"], text="go")["run_id"]
    wait_for(client, project, run_id, lambda s: s["pending_approval"] == "plan")
    client.ok("runs.approve", project=project, run=run_id)
    wait_for(
        client,
        project,
        run_id,
        lambda s: any(t["id"] == "T-002" and t["state"] == "RUNNING" for t in s["tasks"]) and s["driver"]["running"],
    )
    result = client.ok("runs.cancel", project=project, run=run_id, timeout=90)
    assert result["state"] == "CANCELLED"
    snap = client.ok("runs.status", project=project, run=run_id)
    assert not snap["driver"]["running"] and snap["terminal"]
    assert client.call("runs.cancel", project=project, run=run_id)["error"]["data"]["kind"] == "invalid_state"


def test_stored_interrupt_request_stops_the_driver_without_a_signal(client, repo, tmp_path):
    """The only cancel channel on Windows, where a detached driver cannot receive a console control event."""
    project = setup_project(repo, tmp_path, {"T-001": [GOOD_T1], "T-002": [{"sleep": 60, **GOOD_T2}]})
    session = client.ok("sessions.create", project=project)
    run_id = client.ok("sessions.send", project=project, session=session["id"], text="go")["run_id"]
    wait_for(client, project, run_id, lambda s: s["pending_approval"] == "plan")
    client.ok("runs.approve", project=project, run=run_id)
    wait_for(
        client,
        project,
        run_id,
        lambda s: any(t["id"] == "T-002" and t["state"] == "RUNNING" for t in s["tasks"]) and s["driver"]["running"],
    )
    StateStore(repo / ".saarathi" / "state.db").set_control(run_id, INTERRUPT_CONTROL, "1", actor="test")
    snap = wait_for(client, project, run_id, lambda s: not s["driver"]["running"], timeout=30)
    assert snap["state"] == "PAUSED"


def test_stop_pauses_after_in_flight_work(client, repo, tmp_path):
    project = setup_project(repo, tmp_path, {"T-001": [{"sleep": 3, **GOOD_T1}], "T-002": [{"sleep": 3, **GOOD_T2}]})
    session = client.ok("sessions.create", project=project)
    run_id = client.ok("sessions.send", project=project, session=session["id"], text="go")["run_id"]
    wait_for(client, project, run_id, lambda s: s["pending_approval"] == "plan")
    client.ok("runs.approve", project=project, run=run_id)
    wait_for(client, project, run_id, lambda s: any(t["state"] == "RUNNING" for t in s["tasks"]))
    assert client.ok("runs.stop", project=project, run=run_id)["requested"]
    snap = wait_for(client, project, run_id, lambda s: s["state"] == "PAUSED")
    assert snap["pause_reason"] == "paused by user"
    resumed = client.ok("runs.resume", project=project, run=run_id)
    assert resumed["driver_pid"]
    wait_for(client, project, run_id, lambda s: s["pending_approval"] == "review")


def test_execution_survives_bridge_exit_and_new_bridge_reattaches(env, repo, tmp_path):
    project = setup_project(repo, tmp_path, {"T-001": [GOOD_T1], "T-002": [{"sleep": 4, **GOOD_T2}]})
    first = BridgeClient(env, tmp_path)
    session = first.ok("sessions.create", project=project)
    run_id = first.ok("sessions.send", project=project, session=session["id"], text="go")["run_id"]
    wait_for(first, project, run_id, lambda s: s["pending_approval"] == "plan")
    first.ok("runs.approve", project=project, run=run_id)
    wait_for(first, project, run_id, lambda s: s["driver"]["running"])
    first.close()  # the window closes: the bridge exits, the driver keeps going

    second = BridgeClient(env, tmp_path)
    try:
        snap = second.ok("runs.status", project=project, run=run_id)
        assert snap["state"] in ("EXECUTING", "VERIFYING", "AWAITING_REVIEW")
        wait_for(second, project, run_id, lambda s: s["pending_approval"] == "review")
        restored = second.ok("sessions.list", project=project)
        assert restored[0]["id"] == session["id"], "the session is restored from the database"
    finally:
        second.close()


def test_session_rename_archive_search(client, repo, tmp_path):
    project = setup_project(repo, tmp_path, {})
    s = client.ok("sessions.create", project=project, title="First idea")
    client.ok("sessions.rename", project=project, session=s["id"], title="Health endpoint")
    assert client.ok("sessions.list", project=project, query="health")[0]["title"] == "Health endpoint"
    assert client.ok("sessions.list", project=project, query="nothing-like-this") == []
    client.ok("sessions.archive", project=project, session=s["id"])
    assert client.ok("sessions.list", project=project) == []
    assert client.ok("sessions.list", project=project, include_archived=True)[0]["status"] == "archived"
    assert (
        client.call("sessions.rename", project=project, session=s["id"], title=" ")["error"]["data"]["kind"]
        == "invalid_params"
    )


def test_session_preferences_policy_view_and_usage(client, repo, tmp_path):
    project = setup_project(repo, tmp_path, {"T-001": [GOOD_T1], "T-002": [GOOD_T2]})
    session = client.ok("sessions.create", project=project)

    refused = client.call("sessions.set_preferences", project=project, session=session["id"], approvals="bypass")
    assert refused["error"]["data"]["kind"] == "policy" and "max_preset" in refused["error"]["message"]
    unknown = client.call("sessions.set_preferences", project=project, session=session["id"], model="fake-codex:o9")
    assert unknown["error"]["data"]["kind"] == "invalid_params"
    stored = client.ok(
        "sessions.set_preferences",
        project=project,
        session=session["id"],
        approvals="auto_safe",
        model="fake-codex:default",
        model_mode="pin",
    )
    assert stored["preferences"] == {"approvals": "auto_safe", "model": "fake-codex:default", "model_mode": "pin"}

    view = client.ok("policy.get", project=project)
    assert view["approvals"]["max_preset"] == "auto_safe"
    assert [p["allowed"] for p in view["approvals"]["presets"]] == [True, True, False]
    assert any("architecture decisions" in n for n in view["approvals"]["never_automatic"])
    workers = {w["adapter"]: w for w in view["workers"]}
    assert workers["fake-codex"]["worker_command"] is None and "Test-only" in workers["fake-codex"]["isolation"]

    run_id = client.ok("sessions.send", project=project, session=session["id"], text="go")["run_id"]
    snap = wait_for(client, project, run_id, lambda s: s["pending_approval"] == "review")
    assert {t["provider"] for t in snap["tasks"]} == {"openai"}, "the pinned model took every task"
    conversation = client.ok("sessions.get", project=project, session=session["id"])["conversation"]
    auto = [e for e in conversation if e["kind"] == "approval"]
    assert [e["level"] for e in auto] == ["info", "warn"], "plan approved by the preset; review still needs a person"

    usage = client.ok("usage.summary", project=project)
    roles = {(m["role"], m["candidate"]) for m in usage["by_model"]}
    assert ("worker", "fake-codex:default") in roles and ("lead", "fake-claude:opus") in roles
    assert usage["totals"]["calls"] >= 4 and "usage command" in usage["quota_note"]
    assert usage["runs"][0]["run_id"] == run_id

    policy_file = repo / ".saarathi" / "policies.yaml"
    policy_file.write_text(policy_file.read_text() + "approvals:\n  max_preset: bypass\n")
    os.utime(policy_file, (time.time() + 5, time.time() + 5))  # a distinct mtime even on coarse clocks
    raised = client.ok("sessions.set_preferences", project=project, session=session["id"], approvals="bypass")
    assert raised["preferences"]["approvals"] == "bypass", "the bridge reloads a changed policy"
