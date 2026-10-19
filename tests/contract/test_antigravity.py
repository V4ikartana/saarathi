"""Contract tests for the Antigravity CLI adapter, on recorded agy 1.4.2 output."""

import json
from pathlib import Path

from saarathi.core.policy import DeclaredModel, ProviderConfig
from saarathi.providers.antigravity_cli import (
    MAX_PROMPT_CHARS,
    READ_ONLY_RULE,
    AntigravityCliAdapter,
    parse_agy_output,
    parse_models,
)
from saarathi.providers.base import ErrorKind, Invocation, InvocationMode

FIX = Path(__file__).parent.parent / "fixtures" / "antigravity"


def test_recorded_success():
    r = parse_agy_output((FIX / "print-1.4.2-success.json").read_text(), "", 0, False, 20.0, "gemini-3.8-flash-medium")
    assert r.ok and r.structured["status"] == "succeeded"
    assert r.usage.input_tokens and r.usage.output_tokens and r.usage.cost_usd is None
    assert r.usage.reported_model == "gemini-3.8-flash-medium" and r.session_id


def test_recorded_denial_without_output_fails_closed():
    r = parse_agy_output((FIX / "print-1.4.2-denied.json").read_text(), "auto-denied", 0, False, 20.0, None)
    assert not r.ok and r.error_kind is ErrorKind.INVALID_OUTPUT
    assert "write_file" in r.error and r.usage.reported_model is None


def test_error_statuses_and_limits():
    err = json.dumps({"status": "ERROR", "error": "Quota exceeded for this account"})
    assert parse_agy_output(err, "", 1, False, 1, None).error_kind is ErrorKind.QUOTA_EXHAUSTED
    assert parse_agy_output("", "authentication required", 1, False, 1, None).error_kind is ErrorKind.AUTH
    assert parse_agy_output("", "", None, True, 1, None).error_kind is ErrorKind.TIMEOUT
    assert parse_agy_output("not json", "", 0, False, 1, None).error_kind is ErrorKind.INVALID_OUTPUT
    canceled = json.dumps({"status": "CANCELED", "structured_output": {"x": 1}})
    assert not parse_agy_output(canceled, "", 0, False, 1, None).ok


def test_parse_models():
    out = "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\nclaude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n\n"
    assert parse_models(out) == ["gemini-3.8-flash-high", "claude-sonnet-4-6"]
    assert parse_models("Fetching available models...\n") == []


def test_argv(tmp_path):
    a = AntigravityCliAdapter("antigravity-cli", ProviderConfig(binary="/bin/agy"))
    inv = Invocation(
        purpose="t",
        mode=InvocationMode.READ_ONLY,
        prompt="P",
        schema={"type": "object"},
        cwd=tmp_path,
        model="default",
        timeout_s=300,
        log_dir=tmp_path,
    )
    argv = a.build_argv(inv)
    assert "--mode" not in argv, "read-only relies on headless mode denying writes"
    assert argv[-2:] == ["-p", READ_ONLY_RULE + "P"] and "--model" not in argv
    assert argv[argv.index("--print-timeout") + 1] == "285s"
    assert "--dangerously-skip-permissions" not in argv
    inv.mode, inv.model = InvocationMode.WORKSPACE_WRITE, "gemini-3.8-flash-high"
    argv = a.build_argv(inv)
    assert (
        argv[argv.index("--mode") + 1] == "accept-edits" and argv[argv.index("--model") + 1] == "gemini-3.8-flash-high"
    )


def test_oversized_prompt_refused(tmp_path):
    import asyncio

    a = AntigravityCliAdapter("antigravity-cli", ProviderConfig(binary="/bin/agy"))
    from saarathi.providers.base import ProbeResult

    a._probe = ProbeResult(adapter="antigravity-cli", provider="google", available=True)
    inv = Invocation(
        purpose="t",
        mode=InvocationMode.READ_ONLY,
        prompt="x" * (MAX_PROMPT_CHARS + 1),
        schema={},
        cwd=tmp_path,
        model="default",
        timeout_s=30,
        log_dir=tmp_path,
    )
    r = asyncio.run(a.invoke(inv))
    assert not r.ok and "command-line limit" in r.error


def test_undeclared_models_marked_unavailable(monkeypatch):
    from saarathi.providers.base import ProbeResult

    cfg = ProviderConfig(
        models=[
            DeclaredModel(id="gemini-3.8-flash-high"),
            DeclaredModel(id="gemini-9-imaginary"),
            DeclaredModel(id="default"),
        ]
    )
    a = AntigravityCliAdapter("antigravity-cli", cfg)
    a._probe = ProbeResult(
        adapter="antigravity-cli", provider="google", available=True, auth_method="google_account", metered=False
    )
    a.offered_models = ["gemini-3.8-flash-high"]
    models = {m.id: m for m in a.list_models()}
    assert models["gemini-3.8-flash-high"].available and models["default"].available
    assert not models["gemini-9-imaginary"].available
    assert models["gemini-9-imaginary"].unavailable_reason == "not offered by `agy models`"
    assert models["default"].provider == "google" and not models["default"].pricing.metered


def test_missing_binary_is_unavailable(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    probe = AntigravityCliAdapter("antigravity-cli", ProviderConfig()).probe()
    assert not probe.available and "not found" in probe.reason


def test_denied_tool_is_retried_once_with_a_note(tmp_path, monkeypatch):
    import asyncio

    import saarathi.providers.antigravity_cli as mod
    from saarathi.providers.base import ProbeResult
    from saarathi.runtime.process import ProcessResult

    denied = (FIX / "print-1.4.2-denied.json").read_text()
    success = (FIX / "print-1.4.2-success.json").read_text()
    prompts_seen = []

    def fake_run(outputs):
        async def run(argv, **kw):
            prompts_seen.append(argv[-1])
            return ProcessResult(tuple(argv), 0, outputs.pop(0), "denied", 1.0, False, False, False)

        return run

    a = AntigravityCliAdapter("antigravity-cli", ProviderConfig(binary="/bin/agy"))
    a._probe = ProbeResult(adapter="antigravity-cli", provider="google", available=True)
    inv = Invocation(
        purpose="t",
        mode=InvocationMode.READ_ONLY,
        prompt="Q",
        schema={},
        cwd=tmp_path,
        model="default",
        timeout_s=60,
        log_dir=tmp_path,
    )
    monkeypatch.setattr(mod, "run_process", fake_run([denied, success]))
    r = asyncio.run(a.invoke(inv))
    assert r.ok and len(prompts_seen) == 2 and "not allowed here (write_file)" in prompts_seen[1]
    assert r.usage.input_tokens > 0

    prompts_seen.clear()
    monkeypatch.setattr(mod, "run_process", fake_run([denied, denied]))
    r = asyncio.run(a.invoke(inv))
    assert not r.ok and len(prompts_seen) == 2, "exactly one retry, then fail closed"
