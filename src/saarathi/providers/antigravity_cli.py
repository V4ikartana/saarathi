"""Antigravity CLI adapter (``agy -p``), Google's successor to Gemini CLI for consumer plans.

Verified live against agy 1.4.2 on 2026-10-19 (docs/phase0/feasibility-report.md, section 7):

- ``-p`` takes the prompt as its own value. ``agy`` has no stdin prompt mode, so the prompt goes
  on the command line (visible to the same user's processes; capped below ARG_MAX).
- ``--output-format json`` prints one envelope: ``status``, ``structured_output`` (with
  ``--json-schema``), ``usage``, ``denied_actions``, ``error``. The process exits 0 even when a tool was
  denied and no structured output was produced, so a missing ``structured_output`` is a failure.
- Headless mode cannot ask for permission. Edits are allowed with ``--mode accept-edits``. Without it,
  writes are denied, which is how read-only invocations stay read-only. Shell commands are always
  denied unless the user pre-approves them in their own settings, so workers are told not to run any;
  Saarathi runs the verification commands itself.
- ``agy models`` lists the models the signed-in account can use; a pinned ``--model`` that is not
  available fails instead of falling back, so a pinned model is the model that ran.
- Saarathi never passes API keys (environment allowlist), so ``agy`` can only use the user's cached
  Google account login. It is treated as a subscription, not metered, transport (ADR-0006).
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

REQUIRED_FLAGS = ("--output-format", "--json-schema", "--mode", "--print-timeout", "--disable-slash-commands")
MAX_PROMPT_CHARS = 200_000  # the prompt is an argv value; stay well below macOS/Linux ARG_MAX
ENV_NAMES = ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME")
NO_COMMANDS_RULE = (
    "Environment note from Saarathi: you cannot run shell commands or tests in this session, so do not try "
    "(a denied command ends the session without a result). Read and edit files only. Saarathi runs every "
    "check after you finish.\n\n"
)
READ_ONLY_RULE = (
    "Environment note from Saarathi: this is a read-only session. Do not run shell commands and do not create "
    "or edit files (a denied action ends the session without a result). Everything you need is in this prompt; "
    "read repository files only if you must.\n\n"
)
DENIED_RETRY_NOTE = (
    "\n\nNote from Saarathi: your previous attempt at this request ended without a result because it used a "
    "tool that is not allowed here ({denied}). Do not use it again; answer with the tools you are allowed."
)


def parse_model_entries(text: str) -> list[DiscoveredModel]:
    """Models from ``agy models`` output (``<slug><TAB><display name>`` per line)."""
    found = []
    for line in text.splitlines():
        slug, _, name = line.partition("\t")
        slug = slug.strip()
        if slug and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", slug):
            found.append(DiscoveredModel(slug, name.strip() or None))
    return found


def parse_models(text: str) -> list[str]:
    """Slugs from ``agy models`` output."""
    return [m.id for m in parse_model_entries(text)]


class AntigravityCliAdapter(ProviderAdapter):
    type_name = "antigravity-cli"
    provider = "google"
    isolation = (
        "Edits files only (accept-edits). No shell commands: Saarathi runs every check itself after the attempt."
    )

    def __init__(self, name: str, config: Any) -> None:
        super().__init__(name, config)
        self.offered_models: list[str] = []

    def _binary(self) -> str | None:
        return self.config.binary or shutil.which("agy")

    def _env(self, extra: tuple[str, ...] | list[str] = ()) -> dict[str, str]:
        return filtered_env([*ENV_NAMES, *extra])

    def _probe_impl(self) -> ProbeResult:
        result = ProbeResult(adapter=self.name, provider=self.provider, available=False)
        binary = self._binary()
        if not binary:
            result.reason = "agy binary not found on PATH"
            result.notices.append(
                "Install the Antigravity CLI (https://antigravity.google), then run `agy` once to sign in."
            )
            return result
        result.binary = binary
        env = self._env()

        code, out, err = run_quick([binary, "--version"], env)
        match = re.search(r"(\d+\.\d+\.\d+[\w.\-]*)", out + err)
        result.version = match.group(1) if match else None
        result.checks.append(Check("version", code == 0 and match is not None, (out or err).strip()[:200]))

        code, out, err = run_quick([binary, "--help"], env)
        missing = [flag for flag in REQUIRED_FLAGS if flag not in out + err]
        result.checks.append(
            Check("print-mode flags", not missing, "all present" if not missing else f"missing {missing}")
        )

        code, out, err = run_quick([binary, "models"], env, timeout=60)
        entries = parse_model_entries(out) if code == 0 else []
        self.offered_models = [m.id for m in entries]
        if entries and self.discovery_enabled():
            result.discovered_models = [m for m in entries if m.id != "default"]
            result.discovery_source = "agy models"
        signed_in = code == 0 and bool(self.offered_models)
        detail = f"{len(self.offered_models)} models offered" if signed_in else (err or out).strip()[-200:]
        result.checks.append(Check("sign-in (agy models)", signed_in, detail or "no models listed"))
        if signed_in:
            result.auth_method, result.metered = "google_account", False

        failed = [c.name for c in result.checks if not c.ok]
        result.available = not failed
        if failed:
            result.reason = f"failed checks: {', '.join(failed)}"
            if not signed_in:
                result.notices.append("Run `agy` once in a terminal to sign in with your Google account.")
        else:
            declared = [m.id for m in self.config.models if m.id != "default"]
            unknown = [m for m in declared if m not in self.offered_models]
            if unknown:
                result.notices.append(f"Declared models not offered to this account: {unknown}")
            result.notices.append(f"Models offered: {', '.join(self.offered_models)}")
            result.notices.append(
                "Antigravity workers cannot run shell commands in headless mode; Saarathi runs all checks."
            )
        return result

    def list_models(self) -> list[Any]:
        models = super().list_models()
        probe = self.probe()
        if not probe.available:
            return models
        out = []
        for m in models:
            if m.id != "default" and m.id not in self.offered_models:
                m = m.model_copy(update={"available": False, "unavailable_reason": "not offered by `agy models`"})
            out.append(m)
        return out

    def worker_command(self, *, allow_network: bool, allowed_commands: Sequence[str]) -> list[str] | None:
        if self._binary() is None:
            return None
        inv = preview_invocation(allow_network=allow_network, allowed_commands=allowed_commands)
        return self.build_argv(inv)

    def build_argv(self, inv: Invocation) -> list[str]:
        binary = self._binary()
        assert binary is not None
        timeout = max(30, int(inv.timeout_s) - 15)  # let agy end the turn itself before Saarathi kills it
        argv = [
            binary,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(inv.schema),
            "--print-timeout",
            f"{timeout}s",
            "--disable-slash-commands",
        ]
        if inv.mode is InvocationMode.WORKSPACE_WRITE:
            argv += ["--mode", "accept-edits"]
        if inv.model != "default":
            argv += ["--model", inv.model]
        rule = NO_COMMANDS_RULE if inv.mode is InvocationMode.WORKSPACE_WRITE else READ_ONLY_RULE
        argv += ["-p", rule + inv.prompt]
        return argv

    async def invoke(self, inv: Invocation) -> InvocationResult:
        probe = self.probe()
        if not probe.available:
            return InvocationResult(
                ok=False, structured=None, text="", usage=Usage(), error_kind=ErrorKind.UNAVAILABLE, error=probe.reason
            )
        if len(inv.prompt) > MAX_PROMPT_CHARS:
            return InvocationResult(
                ok=False,
                structured=None,
                text="",
                usage=Usage(),
                error_kind=ErrorKind.INVALID_OUTPUT,
                error=f"prompt of {len(inv.prompt)} characters exceeds the agy command-line limit ({MAX_PROMPT_CHARS})",
            )
        inv.log_dir.mkdir(parents=True, exist_ok=True)
        pinned = None if inv.model == "default" else inv.model
        result = await self._run_once(inv, inv.prompt, "process.log", pinned)
        denied = denied_actions(result)
        if denied:
            # A denied tool discards the whole answer. Retry once, telling the model what to avoid.
            retry = await self._run_once(
                inv, inv.prompt + DENIED_RETRY_NOTE.format(denied=", ".join(denied)), "process-retry.log", pinned
            )
            retry.usage = _sum_usage(result.usage, retry.usage)
            return retry
        return result

    async def _run_once(self, inv: Invocation, prompt: str, log_name: str, pinned: str | None) -> InvocationResult:
        attempt = Invocation(**{**inv.__dict__, "prompt": prompt})
        proc = await run_process(
            self.build_argv(attempt),
            cwd=inv.cwd,
            env=self._env(list(inv.env_passthrough)),
            timeout_s=inv.timeout_s,
            on_start=inv.on_start,
        )
        write_log(inv.log_dir / log_name, proc, Redactor())
        return parse_agy_output(proc.stdout, proc.stderr, proc.exit_code, proc.timed_out, proc.duration_s, pinned)


DENIED_MARKER = "; denied actions: "


def denied_actions(result: InvocationResult) -> list[str]:
    """Names of tools agy denied when it produced no structured output (empty if not that failure)."""
    if result.ok or result.error_kind is not ErrorKind.INVALID_OUTPUT or DENIED_MARKER not in (result.error or ""):
        return []
    blob = (result.error or "").split(DENIED_MARKER, 1)[1]
    return sorted(set(re.findall(r'"action": "([a-z_]+)"', blob))) or ["unknown"]


def _sum_usage(a: Usage, b: Usage) -> Usage:
    def add(x: int | None, y: int | None) -> int | None:
        return None if x is None and y is None else (x or 0) + (y or 0)

    return Usage(
        input_tokens=add(a.input_tokens, b.input_tokens),
        cached_input_tokens=add(a.cached_input_tokens, b.cached_input_tokens),
        output_tokens=add(a.output_tokens, b.output_tokens),
        cost_usd=None,
        cost_is_estimate=True,
        reported_model=b.reported_model,
    )


def parse_agy_output(
    stdout: str, stderr: str, exit_code: int | None, timed_out: bool, duration_s: float, pinned_model: str | None
) -> InvocationResult:
    base: dict[str, Any] = {"exit_code": exit_code, "duration_s": duration_s}
    if timed_out:
        return InvocationResult(
            ok=False,
            structured=None,
            text="",
            usage=Usage(),
            error_kind=ErrorKind.TIMEOUT,
            error="agy -p timed out",
            **base,
        )
    try:
        envelope: dict[str, Any] = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        envelope = {}
    raw: dict[str, Any] = envelope["usage"] if isinstance(envelope.get("usage"), dict) else {}
    usage = Usage(
        input_tokens=raw.get("input_tokens"),
        cached_input_tokens=raw.get("cache_read_tokens"),
        output_tokens=raw.get("output_tokens"),
        cost_usd=None,
        cost_is_estimate=True,
        reported_model=pinned_model,  # agy fails rather than falling back, so a pinned model is the one used
    )
    base["usage"] = usage
    base["session_id"] = envelope.get("conversation_id")
    status = str(envelope.get("status", ""))
    if not envelope or exit_code not in (0, None) or status not in ("SUCCESS", ""):
        detail = envelope.get("error") or stderr.strip()[-2000:] or f"status {status or 'missing'}, exit {exit_code}"
        message = json.dumps(detail) if not isinstance(detail, str) else detail
        kind = classify_error(message)
        if not envelope and exit_code == 0:
            kind = ErrorKind.INVALID_OUTPUT
        return InvocationResult(ok=False, structured=None, text=stdout[-2000:], error_kind=kind, error=message, **base)
    structured = envelope.get("structured_output")
    if not isinstance(structured, dict):
        denied = envelope.get("denied_actions") or []
        why = f"{DENIED_MARKER}{json.dumps(denied)[:500]}" if denied else ""
        note = stderr.strip()[-500:]
        return InvocationResult(
            ok=False,
            structured=None,
            text=str(envelope.get("response", "")),
            error_kind=ErrorKind.INVALID_OUTPUT,
            error=f"agy produced no structured output{why}{'; ' + note if note else ''}",
            **base,
        )
    return InvocationResult(ok=True, structured=structured, text=str(envelope.get("response", "")), **base)
