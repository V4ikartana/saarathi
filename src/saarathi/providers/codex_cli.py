"""Codex CLI adapter (``codex exec``).

Verified against codex-cli 0.162.0-alpha.2 (see docs/phase0/feasibility-report.md):
``--json`` JSONL events, ``--output-schema`` + ``-o`` for the structured final message,
``--sandbox read-only|workspace-write``, ``--ephemeral``, ``--ignore-user-config``, prompt on stdin via ``-``.
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

REQUIRED_EXEC_FLAGS = (
    "--json",
    "--output-schema",
    "--output-last-message",
    "--sandbox",
    "--ephemeral",
    "--ignore-user-config",
)
ENV_NAMES = ("CODEX_HOME",)


class CodexCliAdapter(ProviderAdapter):
    type_name = "codex-cli"
    provider = "openai"
    isolation = (
        "Codex's OS sandbox in workspace-write mode: writes stay inside the task worktree, network follows the"
        " policy, and Codex never asks for approval mid-task."
    )

    def _binary(self) -> str | None:
        return self.config.binary or shutil.which("codex")

    def _env(self, extra: tuple[str, ...] | list[str] = ()) -> dict[str, str]:
        return filtered_env([*ENV_NAMES, *extra])

    def _probe_impl(self) -> ProbeResult:
        result = ProbeResult(adapter=self.name, provider=self.provider, available=False)
        binary = self._binary()
        if not binary:
            result.reason = "codex binary not found on PATH"
            result.notices.append(
                "Install Codex CLI or set providers.codex-cli.binary to its path"
                " (the ChatGPT desktop app bundles one under Contents/Resources/codex-cli/bin/codex)."
            )
            return result
        result.binary = binary
        env = self._env()

        code, out, err = run_quick([binary, "--version"], env)
        match = re.search(r"(\d+\.\d+\.\d+[\w.\-]*)", out + err)
        result.version = match.group(1) if match else None
        result.checks.append(Check("version", code == 0 and match is not None, (out or err).strip()[:200]))

        code, out, err = run_quick([binary, "exec", "--help"], env)
        missing = [flag for flag in REQUIRED_EXEC_FLAGS if flag not in out]
        result.checks.append(
            Check(
                "exec flags",
                code == 0 and not missing,
                "all present" if not missing else f"missing {missing}",
            )
        )

        code, out, err = run_quick([binary, "login", "status"], env)
        status = (out + err).strip()
        if code == 0 and "chatgpt" in status.lower():
            result.auth_method, result.metered = "chatgpt", False
        elif code == 0 and "api key" in status.lower():
            result.auth_method, result.metered = "api_key", True
        elif code == 0:
            result.auth_method, result.metered = "unknown", None
        result.checks.append(Check("login", code == 0, status.splitlines()[0][:200] if status else "no output"))

        failed = [c.name for c in result.checks if not c.ok]
        result.available = not failed
        if failed:
            result.reason = f"failed checks: {', '.join(failed)}"
        if result.auth_method == "chatgpt":
            result.notices.append(
                "Codex is signed in with ChatGPT. OpenAI documents API keys as the default for automation;"
                " ChatGPT-managed auth is intended for trusted machines. See ADR-0006."
            )
        if result.version and "alpha" in result.version:
            result.notices.append(f"Codex {result.version} is a pre-release build; flags may change.")
        return result

    def worker_command(self, *, allow_network: bool, allowed_commands: Sequence[str]) -> list[str] | None:
        if self._binary() is None:
            return None
        inv = preview_invocation(allow_network=allow_network, allowed_commands=allowed_commands)
        return self.build_argv(inv, "<schema.json>", "<last-message.txt>")

    def build_argv(self, inv: Invocation, schema_path: str, last_path: str) -> list[str]:
        binary = self._binary()
        assert binary is not None
        sandbox = "workspace-write" if inv.mode is InvocationMode.WORKSPACE_WRITE else "read-only"
        argv = [
            binary,
            "exec",
            "--json",
            "--ephemeral",
            "--color",
            "never",
            "--sandbox",
            sandbox,
            "-C",
            str(inv.cwd),
            "--output-schema",
            schema_path,
            "--output-last-message",
            last_path,
            "-c",
            'approval_policy="never"',
            "-c",
            f"sandbox_workspace_write.network_access={'true' if inv.allow_network else 'false'}",
        ]
        if self.config.options.get("isolate_user_config", True):
            argv.append("--ignore-user-config")
        if inv.model != "default":
            argv += ["--model", inv.model]
        argv.append("-")  # read the prompt from stdin
        return argv

    async def invoke(self, inv: Invocation) -> InvocationResult:
        if not self.probe().available:
            return InvocationResult(
                ok=False,
                structured=None,
                text="",
                usage=Usage(),
                error_kind=ErrorKind.UNAVAILABLE,
                error=self.probe().reason,
            )
        inv.log_dir.mkdir(parents=True, exist_ok=True)
        schema_path = inv.log_dir / "output-schema.json"
        last_path = inv.log_dir / "last-message.json"
        schema_path.write_text(json.dumps(inv.schema), encoding="utf-8")
        last_path.unlink(missing_ok=True)

        proc = await run_process(
            self.build_argv(inv, str(schema_path), str(last_path)),
            cwd=inv.cwd,
            env=self._env(list(inv.env_passthrough)),
            timeout_s=inv.timeout_s,
            stdin_data=inv.prompt.encode("utf-8"),
            on_start=inv.on_start,
        )
        write_log(inv.log_dir / "process.log", proc, Redactor())
        return parse_codex_output(
            proc.stdout,
            proc.stderr,
            proc.exit_code,
            proc.timed_out,
            proc.duration_s,
            last_path.read_text(encoding="utf-8") if last_path.exists() else None,
        )


def parse_codex_output(
    stdout: str,
    stderr: str,
    exit_code: int | None,
    timed_out: bool,
    duration_s: float,
    last_message: str | None,
) -> InvocationResult:
    usage = Usage(cost_is_estimate=True, reported_model=None)
    errors: list[str] = []
    session_id: str | None = None
    for line in stdout.splitlines():
        try:
            event: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "thread.started":
            session_id = event.get("thread_id")
        elif kind == "turn.completed" and isinstance(event.get("usage"), dict):
            u = event["usage"]
            usage = Usage(
                input_tokens=(usage.input_tokens or 0) + int(u.get("input_tokens", 0)),
                cached_input_tokens=(usage.cached_input_tokens or 0) + int(u.get("cached_input_tokens", 0)),
                output_tokens=(usage.output_tokens or 0) + int(u.get("output_tokens", 0)),
                cost_usd=None,
                reported_model=None,
            )
        elif kind in ("turn.failed", "error"):
            detail = event.get("error") or event.get("message") or event
            errors.append(json.dumps(detail) if not isinstance(detail, str) else detail)

    def result(
        ok: bool, kind: ErrorKind, error: str | None, *, structured: dict[str, Any] | None = None, text: str = ""
    ) -> InvocationResult:
        return InvocationResult(
            ok=ok,
            structured=structured,
            text=text,
            usage=usage,
            error_kind=kind,
            error=error,
            exit_code=exit_code,
            duration_s=duration_s,
            session_id=session_id,
        )

    if timed_out:
        return result(False, ErrorKind.TIMEOUT, "codex exec timed out")
    if exit_code != 0 or errors:
        message = "; ".join(errors) or stderr.strip()[-2000:] or f"exit code {exit_code}"
        return result(False, classify_error(message), message)
    if not last_message:
        return result(False, ErrorKind.INVALID_OUTPUT, "codex produced no final message")
    try:
        structured = json.loads(last_message)
    except json.JSONDecodeError:
        return result(False, ErrorKind.INVALID_OUTPUT, "final message is not JSON", text=last_message)
    if not isinstance(structured, dict):
        return result(False, ErrorKind.INVALID_OUTPUT, "final message is not a JSON object", text=last_message)
    return result(True, ErrorKind.NONE, None, structured=structured, text=last_message)
