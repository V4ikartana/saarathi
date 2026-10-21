"""Contract tests for the local-model adapter (ADR-0015), against a scripted OpenAI-compatible server."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from saarathi.core.contracts import ReasoningTier
from saarathi.core.policy import DEFAULT_POLICY_YAML, ProviderConfig
from saarathi.policy_edit import plan_policy_edit
from saarathi.providers.base import ErrorKind, Invocation, InvocationMode
from saarathi.providers.local_openai import LocalOpenAIAdapter, ToolError, Workspace, missing_fields
from saarathi.providers.registry import build_adapter

SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string", "enum": ["succeeded", "failed"]}, "summary": {"type": "string"}},
    "required": ["status", "summary"],
}


class FakeServer:
    """Answers /v1/models with ``models`` and each chat request with the next scripted message.

    ``ollama=True`` behaves like Ollama (``/api/version`` and native ``/api/chat``, tool arguments as objects);
    otherwise like LM Studio or llama.cpp (OpenAI ``/v1/chat/completions`` only)."""

    def __init__(self, models: list[str], script: list[dict[str, Any]], ollama: bool) -> None:
        self.models, self.script, self.requests, self.paths = models, list(script), [], []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, code: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path == "/v1/models":
                    self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in outer.models]})
                elif self.path == "/api/version" and outer.ollama:
                    self._send(200, {"version": "9.9.9"})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                outer.paths.append(self.path)
                native = self.path == "/api/chat"
                if self.path != ("/api/chat" if outer.ollama else "/v1/chat/completions"):
                    self._send(404, {"error": "not found"})
                    return
                if not outer.script:
                    self._send(500, {"error": "script exhausted"})
                    return
                msg = {"role": "assistant", **outer.script.pop(0)}
                if native:
                    for c in msg.get("tool_calls", []):
                        c["function"]["arguments"] = json.loads(c["function"]["arguments"])
                    self._send(200, {"message": msg, "prompt_eval_count": 100, "eval_count": 10})
                    return
                usage = {"prompt_tokens": 100, "completion_tokens": 10}
                self._send(200, {"choices": [{"message": msg}], "usage": usage})

        self.ollama = ollama
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


def call(name: str, **args: Any) -> dict[str, Any]:
    return {
        "content": "",
        "tool_calls": [
            {"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


@pytest.fixture(params=["ollama", "openai"])
def serve(request):
    servers: list[FakeServer] = []

    def start(script: list[dict[str, Any]], models: list[str] | None = None) -> FakeServer:
        s = FakeServer(models if models is not None else ["qwen2.5-coder:7b"], script, request.param == "ollama")
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.close()


def adapter(url: str, **options: Any) -> LocalOpenAIAdapter:
    return LocalOpenAIAdapter("local-openai", ProviderConfig(options={"base_url": url, **options}))


def invoke(a: LocalOpenAIAdapter, tmp: Path, mode: InvocationMode = InvocationMode.WORKSPACE_WRITE, timeout=60):
    inv = Invocation(
        purpose="worker",
        mode=mode,
        prompt="Add a greeting.",
        schema=SCHEMA,
        cwd=tmp / "wt",
        model="qwen2.5-coder:7b",
        timeout_s=timeout,
        log_dir=tmp / "logs",
    )
    return asyncio.run(a.invoke(inv))


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    (wt / "src").mkdir(parents=True)
    (wt / "src" / "app.py").write_text("def main():\n    return 1\n")
    (wt / ".git").mkdir()
    (wt / ".git" / "config").write_text("[core]\n")
    return tmp_path


def test_probe_discovers_installed_models_unmetered_and_economy(serve):
    s = serve([], models=["qwen2.5-coder:7b", "my-own-finetune"])
    a = adapter(s.url)
    probe = a.probe()
    assert probe.available and probe.metered is False
    assert probe.version == ("Ollama 9.9.9" if s.ollama else None)
    models = {m.id: m for m in a.list_models()}
    assert set(models) == {"qwen2.5-coder:7b", "my-own-finetune"}
    assert all(m.provider == "local" and m.pricing.metered is False for m in models.values())
    assert models["my-own-finetune"].capabilities.reasoning_tier is ReasoningTier.ECONOMY, "never unknown or strong"
    assert a.worker_command(allow_network=False, allowed_commands=[]) is None


def test_probe_reports_no_server_and_no_models(serve):
    down = adapter("http://127.0.0.1:9/v1").probe()
    assert not down.available and "Start Ollama or LM Studio" in down.reason
    empty = adapter(serve([], models=[]).url).probe()
    assert not empty.available and "ollama pull" in empty.reason


def test_remote_servers_need_allow_remote():
    probe = adapter("http://10.1.2.3:11434/v1").probe()
    assert not probe.available and "allow_remote" in probe.reason


def test_a_worker_reads_edits_and_submits(serve, worktree):
    s = serve(
        [
            call("list_files", path="."),
            call("read_file", path="src/app.py"),
            call("replace_in_file", path="src/app.py", old_text="return 1", new_text="return 'hello'"),
            call("submit_result", status="succeeded", summary="greets"),
        ]
    )
    r = invoke(adapter(s.url), worktree)
    assert r.ok and r.structured == {"status": "succeeded", "summary": "greets"}
    assert "return 'hello'" in (worktree / "wt" / "src" / "app.py").read_text()
    assert r.usage.input_tokens == 400 and r.usage.output_tokens == 40 and r.usage.cost_usd == 0.0
    listing = s.requests[1]["messages"][-1]["content"]
    assert "src/app.py" in listing and ".git" not in listing
    tools = {t["function"]["name"] for t in s.requests[0]["tools"]}
    assert {"write_file", "replace_in_file", "submit_result"} <= tools and "run" not in str(tools)
    assert (worktree / "logs" / "local-model.log").exists()


def test_read_only_sessions_get_no_write_tools(serve, worktree):
    s = serve([call("write_file", path="x.txt", content="no"), call("submit_result", status="failed", summary="x")])
    r = invoke(adapter(s.url), worktree, mode=InvocationMode.READ_ONLY)
    assert r.ok and not (worktree / "wt" / "x.txt").exists()
    tools = {t["function"]["name"] for t in s.requests[0]["tools"]}
    assert tools == {"list_files", "read_file", "search", "submit_result"}
    assert "read-only" in s.requests[1]["messages"][-1]["content"]


def test_paths_stay_inside_the_worktree(serve, worktree):
    (worktree / "secret.txt").write_text("outside")
    s = serve(
        [
            call("read_file", path="../secret.txt"),
            call("read_file", path=".git/config"),
            call("write_file", path="/etc/evil", content="x"),
            call("submit_result", status="failed", summary="blocked"),
        ]
    )
    r = invoke(adapter(s.url), worktree)
    replies = [m["content"] for m in s.requests[-1]["messages"] if m["role"] == "tool"]
    assert "outside the worktree" in replies[0] and ".git is not accessible" in replies[1]
    assert "outside the worktree" in replies[2] and r.ok


def test_incomplete_answers_are_sent_back_and_plain_text_is_nudged(serve, worktree):
    s = serve(
        [
            call("submit_result", status="maybe"),
            {"content": "I am done."},
            {"content": '```json\n{"status": "succeeded", "summary": "ok"}\n```'},
        ]
    )
    r = invoke(adapter(s.url), worktree)
    assert r.ok and r.structured["summary"] == "ok"
    feedback = s.requests[1]["messages"][-1]["content"]
    assert "missing required field 'summary'" in feedback and "must be one of" in feedback
    assert "submit_result" in s.requests[2]["messages"][-1]["content"]


def test_a_model_that_never_submits_fails_closed(serve, worktree):
    s = serve([{"content": "hmm"}] * 5)
    r = invoke(adapter(s.url), worktree)
    assert not r.ok and r.error_kind is ErrorKind.INVALID_OUTPUT


def test_server_errors_are_classified(serve, worktree):
    s = serve([])  # every chat request answers 500
    r = invoke(adapter(s.url), worktree)
    assert not r.ok and r.error_kind is ErrorKind.PROCESS and "500" in r.error


def test_replace_needs_one_exact_match(tmp_path):
    (tmp_path / "f.py").write_text("a\na\n")
    ws = Workspace(tmp_path, writable=True)
    with pytest.raises(ToolError, match="2 times"):
        ws.replace_in_file("f.py", "a", "b")
    with pytest.raises(ToolError, match="not found"):
        ws.replace_in_file("f.py", "zzz", "b")


def test_missing_fields_checks_types_and_nulls():
    schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": ["integer", "null"]}}}
    assert missing_fields(schema, {"n": None}) == []
    assert missing_fields(schema, {"n": "x"}) == ["field 'n' must be integer"]
    assert missing_fields(schema, []) == ["the answer must be a JSON object"]


def test_the_registry_and_policy_editor_know_it():
    assert isinstance(build_adapter("local-openai", ProviderConfig()), LocalOpenAIAdapter)
    result = plan_policy_edit(DEFAULT_POLICY_YAML, {"providers.add": "local-openai"}, [])
    assert "local-openai" in result.policy.providers
    assert "local" in result.policy.routing.allowed_providers and "local/*" in result.policy.routing.allowed_models


def test_ollama_gets_a_larger_context_window(serve, worktree):
    s = serve([call("submit_result", status="failed", summary="x")])
    invoke(adapter(s.url, context_tokens=8192), worktree)
    if s.ollama:
        assert s.paths == ["/api/chat"] and s.requests[0]["options"]["num_ctx"] == 8192
    else:
        assert s.paths == ["/v1/chat/completions"]


def test_tool_calls_written_as_text_still_run(serve, worktree):
    s = serve(
        [
            {"content": '{"name": "read_file", "arguments": {"path": "src/app.py"}}'},
            {
                "content": '<tool_call>\n{"name": "write_file", '
                '"arguments": {"path": "src/app.py", "content": "x = 2\\n"}}\n</tool_call>'
            },
            {
                "content": '```json\n{"name": "submit_result", '
                '"arguments": {"status": "succeeded", "summary": "done"}}\n```'
            },
        ]
    )
    r = invoke(adapter(s.url), worktree)
    assert r.ok and r.structured == {"status": "succeeded", "summary": "done"}
    assert (worktree / "wt" / "src" / "app.py").read_text() == "x = 2\n"
    assert "Result of read_file" in s.requests[1]["messages"][-1]["content"]


def test_claiming_changes_without_making_any_is_sent_back(serve, worktree):
    schema_with_files = {**SCHEMA, "properties": {**SCHEMA["properties"], "changed_files": {"type": "array"}}}
    s = serve(
        [
            call("submit_result", status="succeeded", summary="did it", changed_files=["src/app.py"]),
            call("submit_result", status="failed", summary="could not", changed_files=[]),
        ]
    )
    inv = Invocation(
        purpose="worker",
        mode=InvocationMode.WORKSPACE_WRITE,
        prompt="p",
        schema=schema_with_files,
        cwd=worktree / "wt",
        model="m",
        timeout_s=60,
        log_dir=worktree / "logs",
    )
    r = asyncio.run(adapter(s.url).invoke(inv))
    assert r.ok and r.structured["status"] == "failed"
    assert "have not changed any file" in s.requests[1]["messages"][-1]["content"]


def test_detects_running_local_servers(serve, monkeypatch):
    from saarathi.providers import local_openai

    s = serve([], models=["a", "b"])
    monkeypatch.setattr(local_openai, "KNOWN_SERVERS", (("Down", "http://127.0.0.1:9/v1"), ("Up", s.url)))
    assert local_openai.detect_local_servers() == [("Up", s.url, 2)]


def test_symlinks_out_of_the_worktree_are_not_followed(tmp_path):
    (tmp_path / "outside.txt").write_text("TOKEN=secret\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "link.txt").symlink_to(tmp_path / "outside.txt")
    ws = Workspace(wt, writable=True)
    assert ws.search("TOKEN") == "(no matches)"
    with pytest.raises(ToolError, match="outside the worktree"):
        ws.read_file("link.txt")
    with pytest.raises(ToolError, match="outside the worktree"):
        ws.write_file("link.txt", "x")


def test_a_broken_text_call_gets_specific_feedback(serve, worktree):
    s = serve(
        [
            {"content": '{"name": "write_file", "arguments": {"path": "a.py", "content": """x"""}}'},
            call("submit_result", status="failed", summary="gave up"),
        ]
    )
    r = invoke(adapter(s.url), worktree)
    assert r.ok
    feedback = s.requests[1]["messages"][-1]["content"]
    assert "was not valid JSON" in feedback and "write_file" in feedback


def test_a_worktree_git_file_is_not_listed(tmp_path):
    (tmp_path / ".git").write_text("gitdir: /elsewhere\n")
    (tmp_path / "a.py").write_text("x = 1\n")
    ws = Workspace(tmp_path, writable=False)
    assert ws.list_files() == "a.py"
    assert ws.search("gitdir") == "(no matches)"


def test_a_call_missing_its_last_brace_is_repaired(serve, worktree):
    from saarathi.providers.local_openai import close_brackets

    assert close_brackets('{"a": {"b": "x}"}') == {"a": {"b": "x}"}}
    assert close_brackets('{"a": 1}') is None and close_brackets('{"a": "open') is None
    s = serve([{"content": '{"name": "submit_result", "arguments": {"status": "succeeded", "summary": "ok"}'}])
    r = invoke(adapter(s.url), worktree)
    assert r.ok and r.structured == {"status": "succeeded", "summary": "ok"}


def test_repeating_the_same_failing_call_stops_early(serve, worktree):
    def bad():
        return call("replace_in_file", path="src/app.py", old_text="nope", new_text="x")

    s = serve([bad(), bad(), bad(), call("submit_result", status="succeeded", summary="never reached")])
    r = invoke(adapter(s.url), worktree)
    assert not r.ok and r.error_kind is ErrorKind.INVALID_OUTPUT and "3 times" in r.error
    assert len(s.requests) == 3
