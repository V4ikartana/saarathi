"""Claude Code adapter (``claude -p``).

Built against the official CLI reference and headless docs (fetched 2026-10-18) and verified live on
Claude Code 2.1.302 (docs/phase0/feasibility-report.md, section 6). `doctor` marks it unavailable unless
the version and authentication checks pass; `saarathi doctor --live` performs a real structured completion.

Security notes (ADR-0004): Claude Code's Bash tool is not OS-sandboxed in this configuration. The
adapter uses ``--permission-mode dontAsk`` with an explicit allowlist (edit tools plus the task's
exact verification commands), disables MCP servers and web tools, and never passes credentials.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from typing import Any

from saarathi.core.contracts import Usage
from saarathi.providers.base import (
    Check,
    DiscoveredModel,
    ErrorKind,
    Invocation,
    InvocationMode,
    InvocationResult,
    ProbeResult,
    ProviderAdapter,
    classify_error,
    preview_invocation,
    run_quick,
)
from saarathi.runtime.process import run_process, write_log
from saarathi.security.env import filtered_env
from saarathi.security.redaction import Redactor

LIVE_UNVERIFIED_PREFIX = "Live behavior of this adapter is unverified"
MIN_VERSION = (2, 1, 259)  # --permission-prompts none
ENV_NAMES = ("CLAUDE_CONFIG_DIR",)
SUBSCRIPTION_AUTH = frozenset({"claude.ai", "oauth_token"})
METERED_AUTH = frozenset({"api_key", "api_key_helper", "third_party"})
READ_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = ("Read", "Glob", "Grep", "Edit", "Write")


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


# Documented aliases (Claude Code model configuration) plus ``fable`` from ``claude --help``.
MODEL_ALIASES = ("opus", "sonnet", "haiku", "fable")


class ClaudeCodeAdapter(ProviderAdapter):
    type_name = "claude-code"
    provider = "anthropic"
    isolation = (
        "Not OS-sandboxed. Tools are limited to Read, Glob, Grep, Edit, Write and Bash for the task's own"
        " verification commands; MCP servers are disabled, and web tools too unless the policy allows network."
    )

    def _binary(self) -> str | None:
        return self.config.binary or shutil.which("claude")

    def _env(self, extra: tuple[str, ...] | list[str] = ()) -> dict[str, str]:
        return filtered_env([*ENV_NAMES, *extra])

    def _probe_impl(self) -> ProbeResult:
        result = ProbeResult(adapter=self.name, provider=self.provider, available=False)
        binary = self._binary()
        if not binary:
            result.reason = "claude binary not found on PATH"
            result.notices.append(
                "Install Claude Code natively (https://code.claude.com/docs/en/setup), then run `claude auth login`."
            )
            return result
        result.binary = binary
        env = self._env(list(self.config.options.get("env_passthrough", [])))

        code, out, err = run_quick([binary, "--version"], env)
        version = parse_version(out + err)
        result.version = ".".join(map(str, version)) if version else None
        version_ok = code == 0 and version is not None and version >= MIN_VERSION
        result.checks.append(
            Check(
                "version",
                version_ok,
                f"{result.version or 'unknown'} (need >= {'.'.join(map(str, MIN_VERSION))})",
            )
        )

        code, out, err = run_quick([binary, "auth", "status"], env)
        auth_ok = False
        try:
            status: dict[str, Any] = json.loads(out)
            method = str(status.get("authMethod", "none"))
            result.auth_method = method
            auth_ok = code == 0 and method != "none"
            result.metered = True if method in METERED_AUTH else (False if method in SUBSCRIPTION_AUTH else None)
        except json.JSONDecodeError:
            method = "unknown"
        result.checks.append(Check("auth", auth_ok, f"authMethod={method}"))

        failed = [c.name for c in result.checks if not c.ok]
        result.available = not failed
        if failed:
            result.reason = f"failed checks: {', '.join(failed)}"
        elif self.discovery_enabled():
            # Claude Code has no command that lists models; its documented aliases always resolve to the latest
            # model of each family, so they are the discovered set.
            result.discovered_models = [DiscoveredModel(alias) for alias in MODEL_ALIASES]
            result.discovery_source = "Claude Code model aliases"
        if result.auth_method in SUBSCRIPTION_AUTH:
            result.notices.append(
                "Claude Code is signed in with a Claude subscription. Anthropic's terms describe subscription"
                " auth as intended for ordinary use; prefer an API key for heavy or unattended runs. See ADR-0006."
            )
        result.notices.append(f"{LIVE_UNVERIFIED_PREFIX} until `saarathi doctor --live` passes on this machine.")
        return result

    def worker_command(self, *, allow_network: bool, allowed_commands: Sequence[str]) -> list[str] | None:
        if self._binary() is None:
            return None
        inv = preview_invocation(allow_network=allow_network, allowed_commands=allowed_commands)
        return self.build_argv(inv)

    def build_argv(self, inv: Invocation) -> list[str]:
        binary = self._binary()
        assert binary is not None
        opts = self.config.options
        argv = [
            binary,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(inv.schema),
            "--no-session-persistence",
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            str(opts.get("setting_sources", "user")),
            "--model",
            inv.model,
        ]
        if inv.mode is InvocationMode.READ_ONLY:
            argv += ["--tools", ",".join(READ_TOOLS), "--allowedTools", *READ_TOOLS]
        else:
            allowed = [*WRITE_TOOLS, *(f"Bash({cmd})" for cmd in inv.allowed_commands)]
            argv += ["--tools", ",".join((*WRITE_TOOLS, "Bash")), "--allowedTools", *allowed]
        disallowed = ["mcp__*"]
        if not inv.allow_network:
            disallowed += ["WebFetch", "WebSearch"]
        argv += ["--disallowedTools", *disallowed]
        if "max_turns" in opts:
            argv += ["--max-turns", str(int(opts["max_turns"]))]
        return argv

    async def invoke(self, inv: Invocation) -> InvocationResult:
        probe = self.probe()
        if not probe.available:
            return InvocationResult(
                ok=False,
                structured=None,
                text="",
                usage=Usage(),
                error_kind=ErrorKind.UNAVAILABLE,
                error=probe.reason,
            )
        inv.log_dir.mkdir(parents=True, exist_ok=True)
        passthrough = [*self.config.options.get("env_passthrough", []), *inv.env_passthrough]
        proc = await run_process(
            self.build_argv(inv),
            cwd=inv.cwd,
            env=self._env(passthrough),
            timeout_s=inv.timeout_s,
            stdin_data=inv.prompt.encode("utf-8"),
            on_start=inv.on_start,
        )
        write_log(inv.log_dir / "process.log", proc, Redactor())
        return parse_claude_output(proc.stdout, proc.stderr, proc.exit_code, proc.timed_out, proc.duration_s)


def parse_claude_output(
    stdout: str, stderr: str, exit_code: int | None, timed_out: bool, duration_s: float
) -> InvocationResult:
    base: dict[str, Any] = {"exit_code": exit_code, "duration_s": duration_s}
    if timed_out:
        return InvocationResult(
            ok=False,
            structured=None,
            text="",
            usage=Usage(),
            error_kind=ErrorKind.TIMEOUT,
            error="claude -p timed out",
            **base,
        )
    payload: dict[str, Any] = {}
    for line in reversed(stdout.strip().splitlines()):  # the result object; tolerate text printed after it
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    raw_usage: dict[str, Any] = payload["usage"] if isinstance(payload.get("usage"), dict) else {}
    model_usage: dict[str, Any] = payload["modelUsage"] if isinstance(payload.get("modelUsage"), dict) else {}
    usage = Usage(
        input_tokens=raw_usage.get("input_tokens"),
        cached_input_tokens=raw_usage.get("cache_read_input_tokens"),
        output_tokens=raw_usage.get("output_tokens"),
        cost_usd=payload.get("total_cost_usd"),
        cost_is_estimate=True,
        reported_model=",".join(sorted(model_usage)) or None,
    )
    base["usage"] = usage
    base["session_id"] = payload.get("session_id")
    if not payload:
        message = stderr.strip()[-2000:] or stdout.strip()[-2000:] or f"exit code {exit_code}"
        kind = classify_error(message) if exit_code not in (0, None) else ErrorKind.INVALID_OUTPUT
        return InvocationResult(ok=False, structured=None, text=stdout, error_kind=kind, error=message, **base)
    if payload.get("is_error") or exit_code != 0:
        message = str(payload.get("result") or payload.get("subtype") or stderr.strip()[-2000:])
        return InvocationResult(
            ok=False, structured=None, text=message, error_kind=classify_error(message), error=message, **base
        )
    structured = payload.get("structured_output")
    if not isinstance(structured, dict):
        return InvocationResult(
            ok=False,
            structured=None,
            text=str(payload.get("result", "")),
            error_kind=ErrorKind.INVALID_OUTPUT,
            error="no structured_output in result",
            **base,
        )
    return InvocationResult(ok=True, structured=structured, text=str(payload.get("result", "")), **base)
