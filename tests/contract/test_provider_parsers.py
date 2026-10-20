"""Contract tests: adapters parse real (recorded) CLI output and fail closed on anything else."""

import json
from pathlib import Path

from saarathi.core.policy import ProviderConfig
from saarathi.providers.base import ErrorKind, Invocation, InvocationMode, classify_error
from saarathi.providers.claude_cli import ClaudeCodeAdapter, parse_claude_output, parse_version
from saarathi.providers.codex_cli import CodexCliAdapter, parse_codex_output

FIX = Path(__file__).parent.parent / "fixtures" / "codex"


def test_codex_recorded_success():
    events = (FIX / "exec-0.162.0-alpha.2-success.jsonl").read_text()
    last = (FIX / "exec-0.162.0-alpha.2-last-message.json").read_text()
    r = parse_codex_output(events, "Reading additional input from stdin...", 0, False, 7.3, last)
    assert r.ok and r.structured["changed_files"] == ["hello_a.txt"]
    assert r.usage.input_tokens == 31381 and r.usage.output_tokens == 118 and r.usage.cost_usd is None
    assert r.usage.reported_model is None, "codex does not report the model; never guess"
    assert r.session_id == "01a14b93-d665-7711-ad55-f21e00edae40"


def test_codex_failure_modes_fail_closed():
    assert parse_codex_output("", "", None, True, 1, None).error_kind is ErrorKind.TIMEOUT
    failed = json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit"}})
    assert parse_codex_output(failed, "", 1, False, 1, None).error_kind is ErrorKind.QUOTA_EXHAUSTED
    assert parse_codex_output("", "", 0, False, 1, None).error_kind is ErrorKind.INVALID_OUTPUT
    assert parse_codex_output("", "", 0, False, 1, "not json").error_kind is ErrorKind.INVALID_OUTPUT
    assert parse_codex_output("", "", 0, False, 1, "[1]").error_kind is ErrorKind.INVALID_OUTPUT


def test_codex_argv_is_sandboxed_and_reads_prompt_from_stdin(tmp_path):
    adapter = CodexCliAdapter("codex-cli", ProviderConfig(binary="/bin/codex"))
    inv = Invocation(
        purpose="t",
        mode=InvocationMode.WORKSPACE_WRITE,
        prompt="p",
        schema={},
        cwd=tmp_path,
        model="default",
        timeout_s=1,
        log_dir=tmp_path,
    )
    argv = adapter.build_argv(inv, "s.json", "o.json")
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "--ignore-user-config" in argv and argv[-1] == "-" and "--model" not in argv
    assert "sandbox_workspace_write.network_access=false" in argv
    inv.mode = InvocationMode.READ_ONLY
    inv.model = "gpt-x"
    argv = adapter.build_argv(inv, "s.json", "o.json")
    assert argv[argv.index("--sandbox") + 1] == "read-only" and argv[argv.index("--model") + 1] == "gpt-x"
    assert "danger-full-access" not in argv and "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_codex_missing_binary_is_unavailable(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    probe = CodexCliAdapter("codex-cli", ProviderConfig()).probe()
    assert not probe.available and "not found" in probe.reason


# Claude Code output is modelled on the documented `--output-format json` result object. These are NOT
# recordings: they become recordings once the adapter is verified live (see docs/phase0/backlog.md S14).
CLAUDE_OK = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "done",
    "session_id": "s-1",
    "total_cost_usd": 0.0123,
    "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 2},
    "modelUsage": {"claude-opus-x": {}},
    "structured_output": {"status": "succeeded"},
}


def test_claude_documented_result_shape():
    r = parse_claude_output(json.dumps(CLAUDE_OK), "", 0, False, 2.0)
    assert r.ok and r.structured == {"status": "succeeded"}
    assert r.usage.cost_usd == 0.0123 and r.usage.cost_is_estimate and r.usage.reported_model == "claude-opus-x"


def test_claude_failures_fail_closed():
    err = {**CLAUDE_OK, "is_error": True, "result": "API Error: 429 rate limit"}
    assert parse_claude_output(json.dumps(err), "", 1, False, 1).error_kind is ErrorKind.RATE_LIMIT
    no_struct = {k: v for k, v in CLAUDE_OK.items() if k != "structured_output"}
    assert parse_claude_output(json.dumps(no_struct), "", 0, False, 1).error_kind is ErrorKind.INVALID_OUTPUT
    assert parse_claude_output("", "Not logged in", 1, False, 1).error_kind is ErrorKind.AUTH
    assert parse_claude_output("", "", None, True, 1).error_kind is ErrorKind.TIMEOUT


def test_claude_argv_least_privilege(tmp_path):
    adapter = ClaudeCodeAdapter("claude-code", ProviderConfig(binary="/bin/claude"))
    inv = Invocation(
        purpose="t",
        mode=InvocationMode.READ_ONLY,
        prompt="p",
        schema={"type": "object"},
        cwd=tmp_path,
        model="opus",
        timeout_s=1,
        log_dir=tmp_path,
    )
    argv = adapter.build_argv(inv)
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--strict-mcp-config" in argv and "WebFetch" in argv and "bypassPermissions" not in argv
    inv.mode = InvocationMode.WORKSPACE_WRITE
    inv.allowed_commands = ["pytest -q"]
    argv = adapter.build_argv(inv)
    assert "Bash(pytest -q)" in argv and "Bash" in argv[argv.index("--tools") + 1]
    assert "--dangerously-skip-permissions" not in argv


def test_claude_version_parsing():
    assert parse_version("2.1.299 (Claude Code)") == (2, 1, 299)
    assert parse_version("garbage") is None


def test_error_classification():
    assert classify_error("Error: Too Many Requests") is ErrorKind.RATE_LIMIT
    assert classify_error("API Error: 529 overloaded") is ErrorKind.RATE_LIMIT
    assert classify_error("You've hit your usage limit. Upgrade or try again in 3 hours") is ErrorKind.QUOTA_EXHAUSTED
    assert classify_error("Your credit balance is too low") is ErrorKind.QUOTA_EXHAUSTED
    # Claude Code 2.1.302 on a subscription, 2026-10-20
    assert classify_error("You've hit your session limit · resets 5:40pm (Asia/Calcutta)") is ErrorKind.QUOTA_EXHAUSTED
    assert classify_error("Fable 5.1 requires usage credits. Switch to another model") is ErrorKind.QUOTA_EXHAUSTED
    assert classify_error("401 Unauthorized") is ErrorKind.AUTH
    assert classify_error("segfault") is ErrorKind.PROCESS
