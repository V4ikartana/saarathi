"""Local open-source models through an OpenAI-compatible server (Ollama, LM Studio, llama.cpp, vLLM).

Unlike the CLI adapters, nothing external runs the agent: Saarathi drives the model itself (ADR-0015).

- The model talks to ``POST {base_url}/chat/completions`` with function tools. Saarathi executes every tool call
  in-process: list, read and search files, and (workspace-write only) write a file or replace exact text. Paths are
  confined to the task worktree; ``.git`` is never readable or writable. There is no shell tool: Saarathi runs the
  task's verification commands after the model finishes, exactly as for Antigravity workers.
- The final answer arrives as a call to ``submit_result`` whose parameters are the invocation's JSON schema. A call
  missing required fields is answered with what is missing, so the model can correct it.
- By default only loopback servers are accepted, so code never leaves the computer. ``options.allow_remote: true``
  allows another host, and says so in ``saarathi doctor``.
- Local models are not metered and have no plan limits. Their tier comes from the policy, the model catalogue, or
  stays "unknown"; small models are listed as economy.

Options (``providers.local-openai.options``): ``base_url`` (default ``http://127.0.0.1:11434/v1``, Ollama),
``allow_remote`` (false), ``max_turns`` (60), ``temperature`` (0.2), ``request_timeout_seconds`` (600).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from saarathi.core.contracts import ModelCapabilities, ReasoningTier, Transport, Usage
from saarathi.providers.base import (
    Check,
    DiscoveredModel,
    ErrorKind,
    Invocation,
    InvocationMode,
    InvocationResult,
    ProbeResult,
    ProviderAdapter,
)
from saarathi.security.redaction import Redactor

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
KNOWN_SERVERS = (("Ollama", "http://127.0.0.1:11434/v1"), ("LM Studio", "http://127.0.0.1:1234/v1"))
LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
MAX_TOOL_OUTPUT = 24_000
MAX_FILE_BYTES = 2_000_000
MAX_LIST = 600
MAX_SEARCH_HITS = 200
MAX_NUDGES = 3
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"}

SYSTEM_PROMPT = """You are a careful software engineer working inside one Git worktree for Saarathi.

How to work:
- Use the tools to look around before you change anything: list_files, read_file, search.
- {write_rule}
- You cannot run shell commands, tests or installs. Saarathi runs every check after you finish, so write code
  you are confident in and keep to the files the task allows.
- Paths are relative to the worktree root. Never touch .git.
- When you are done, call submit_result exactly once with your final answer. Its fields are the answer Saarathi
  needs; fill every required field truthfully. Do not put the answer in plain text instead."""
WRITE_RULE = "Change files with write_file (whole file) or replace_in_file (exact text, prefer this for small edits)."
READ_RULE = "This is a read-only session: read and search only, do not try to change files."
NUDGE = (
    "You have not called submit_result. Call it now with your final answer as its arguments "
    "(every required field). Do not answer in plain text."
)


class ToolError(Exception):
    """A tool call the model can recover from; its message goes back to the model."""


def is_loopback(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in LOOPBACK


# ---------------------------------------------------------------- HTTP (stdlib only)
def http_json(method: str, url: str, body: dict[str, Any] | None, timeout: float) -> Any:
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"base_url must be http or https, not {url!r}")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL comes from the policy)
        return json.loads(resp.read().decode("utf-8", errors="replace") or "null")


def describe_http_error(exc: Exception) -> tuple[ErrorKind, str]:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1500]
        except Exception:  # pragma: no cover - best effort
            detail = ""
        text = f"HTTP {exc.code}: {detail or exc.reason}"
        low = text.lower()
        if exc.code == 404 or "not found" in low:
            return ErrorKind.UNAVAILABLE, text
        if "does not support tools" in low or ("tool" in low and "support" in low):
            return ErrorKind.UNAVAILABLE, text + " (choose a model that supports tool calling)"
        if exc.code == 429:
            return ErrorKind.RATE_LIMIT, text
        return ErrorKind.PROCESS, text
    if isinstance(exc, (TimeoutError, urllib.error.URLError)) and "timed out" in str(exc).lower():
        return ErrorKind.TIMEOUT, f"the local model server did not answer in time: {exc}"
    if isinstance(exc, urllib.error.URLError):
        return ErrorKind.UNAVAILABLE, f"cannot reach the local model server: {exc.reason}"
    return ErrorKind.PROCESS, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- tools, confined to the worktree
@dataclass
class Workspace:
    root: Path
    writable: bool
    changed: list[str] = field(default_factory=list)

    def resolve(self, rel: str, *, must_exist: bool = True) -> Path:
        if not isinstance(rel, str) or not rel.strip():
            raise ToolError("path is required")
        rel = rel.strip().lstrip("/") if not os.path.isabs(rel) else rel
        target = (self.root / rel).resolve() if not os.path.isabs(rel) else Path(rel).resolve()
        root = self.root.resolve()
        if target != root and root not in target.parents:
            raise ToolError(f"{rel!r} is outside the worktree")
        parts = target.relative_to(root).parts
        if ".git" in parts:
            raise ToolError(".git is not accessible")
        if must_exist and not target.exists():
            raise ToolError(f"{rel!r} does not exist")
        return target

    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root.resolve()).as_posix() or "."

    def list_files(self, path: str = ".", max_depth: int = 3) -> str:
        base = self.resolve(path or ".")
        if base.is_file():
            return self.rel(base)
        out: list[str] = []
        depth0 = len(base.parts)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            d = Path(dirpath)
            if len(d.parts) - depth0 >= max(1, int(max_depth)):
                dirnames[:] = []
            for name in sorted(filenames):
                out.append(self.rel(d / name))
                if len(out) >= MAX_LIST:
                    return "\n".join(out) + f"\n… (stopped at {MAX_LIST} entries; list a subfolder)"
        return "\n".join(out) or "(empty)"

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        p = self.resolve(path)
        if p.is_dir():
            raise ToolError(f"{path!r} is a folder; use list_files")
        if p.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(f"{path!r} is larger than {MAX_FILE_BYTES} bytes")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if not lines:
            return "(empty file)"
        s = max(1, int(start_line or 1))
        e = len(lines) if end_line is None else min(len(lines), int(end_line))
        # Plain text, no line-number prefixes: small models copy prefixes back into the file they write.
        if (s, e) == (1, len(lines)):
            return "\n".join(lines)
        return f"(lines {s}-{e} of {len(lines)})\n" + "\n".join(lines[s - 1 : e])

    def search(self, pattern: str, path: str = ".") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}") from exc
        base = self.resolve(path or ".")
        files = [base] if base.is_file() else []
        if base.is_dir():
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                files += [Path(dirpath) / f for f in sorted(filenames)]
        hits: list[str] = []
        root = self.root.resolve()
        for f in files:
            real = f.resolve()
            if root not in real.parents or ".git" in real.relative_to(root).parts:
                continue  # a symlink that leads outside the worktree, or into .git
            try:
                if f.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{self.rel(f)}:{n}: {line.strip()[:240]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return "\n".join(hits) + "\n… (more matches; narrow the search)"
        return "\n".join(hits) or "(no matches)"

    def write_file(self, path: str, content: str) -> str:
        if not self.writable:
            raise ToolError("this session is read-only")
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        p = self.resolve(path, must_exist=False)
        if p.is_dir():
            raise ToolError(f"{path!r} is a folder")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        self._changed(p)
        return f"wrote {self.rel(p)} ({len(content.splitlines())} lines)"

    def replace_in_file(self, path: str, old_text: str, new_text: str) -> str:
        if not self.writable:
            raise ToolError("this session is read-only")
        p = self.resolve(path)
        text = p.read_text(encoding="utf-8")
        count = text.count(old_text) if old_text else 0
        if count == 0:
            raise ToolError("old_text was not found; read the file again and copy the exact text")
        if count > 1:
            raise ToolError(f"old_text appears {count} times; include more surrounding lines so it is unique")
        p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        self._changed(p)
        return f"replaced text in {self.rel(p)}"

    def _changed(self, p: Path) -> None:
        r = self.rel(p)
        if r not in self.changed:
            self.changed.append(r)


def tool_specs(writable: bool, schema: dict[str, Any]) -> list[dict[str, Any]]:
    def fn(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {"type": "object", "properties": props, "required": required},
            },
        }

    s = {"type": "string"}
    specs = [
        fn(
            "list_files",
            "List files under a folder of the worktree (recursively, up to max_depth levels).",
            {"path": {**s, "description": "folder, relative to the worktree root"}, "max_depth": {"type": "integer"}},
            [],
        ),
        fn(
            "read_file",
            "Read a text file exactly as it is. Use start_line/end_line (1-based) for part of a long file.",
            {"path": s, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
            ["path"],
        ),
        fn(
            "search",
            "Search files for a regular expression; returns path:line: text.",
            {"pattern": s, "path": s},
            ["pattern"],
        ),
    ]
    if writable:
        specs += [
            fn(
                "write_file",
                "Create or overwrite a whole file with the given content.",
                {"path": s, "content": s},
                ["path", "content"],
            ),
            fn(
                "replace_in_file",
                "Replace one exact, unique piece of text in a file.",
                {"path": s, "old_text": s, "new_text": s},
                ["path", "old_text", "new_text"],
            ),
        ]
    params = schema if schema.get("type") == "object" else {"type": "object", "properties": {}}
    specs.append(
        {
            "type": "function",
            "function": {
                "name": "submit_result",
                "description": "Submit your final answer. Call exactly once, when you are done.",
                "parameters": params,
            },
        }
    )
    return specs


def missing_fields(schema: dict[str, Any], value: Any) -> list[str]:
    """Top-level problems with a submitted answer: missing required fields, wrong basic types, invalid enums."""
    if not isinstance(value, dict):
        return ["the answer must be a JSON object"]
    problems = [f"missing required field '{k}'" for k in schema.get("required", []) if k not in value]
    types: dict[str, tuple[type, ...]] = {
        "string": (str,),
        "array": (list,),
        "object": (dict,),
        "boolean": (bool,),
        "integer": (int,),
        "number": (int, float),
    }
    for k, sub in (schema.get("properties") or {}).items():
        if k not in value or not isinstance(sub, dict):
            continue
        t = sub.get("type")
        allowed = t if isinstance(t, list) else [t] if t else []
        if allowed and value[k] is None and "null" in allowed:
            continue
        py = tuple(t for x in allowed if x in types for t in types[x])
        if py and not isinstance(value[k], py):
            problems.append(f"field '{k}' must be {' or '.join(a for a in allowed if a != 'null')}")
        elif "enum" in sub and value[k] not in sub["enum"]:
            problems.append(f"field '{k}' must be one of {sub['enum']}")
    return problems


def parse_json_answer(text: str) -> dict[str, Any] | None:
    """A JSON object a model put in plain text (optionally fenced), as a fallback for submit_result."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    blob = m.group(1) if m else text[text.find("{") : text.rfind("}") + 1] if "{" in text else ""
    try:
        val = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return None
    return val if isinstance(val, dict) else None


def json_values(text: str) -> list[Any]:
    """Every JSON object or array that appears in ``text``, outermost first."""
    dec, i = json.JSONDecoder(), 0
    out: list[Any] = []
    while True:
        starts = [k for k in (text.find("{", i), text.find("[", i)) if k != -1]
        if not starts:
            return out
        j = min(starts)
        try:
            value, i = dec.raw_decode(text, j)
            out.append(value)
        except json.JSONDecodeError:
            i = j + 1


def text_tool_calls(text: str, names: set[str]) -> list[tuple[str, Any]]:
    """Tool calls a model wrote as JSON text ({"name": ..., "arguments": {...}}, bare, fenced or in
    <tool_call> tags) instead of using the API's tool calls. Only known tool names count."""
    calls = []
    for value in json_values(text or ""):
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict) and item.get("name") in names:
                calls.append((str(item["name"]), item.get("arguments", item.get("parameters", {}))))
    return calls


# ---------------------------------------------------------------- the adapter
class LocalOpenAIAdapter(ProviderAdapter):
    type_name = "local-openai"
    provider = "local"
    transport = Transport.LOCAL
    isolation = (
        "Runs inside Saarathi: the local model can only list, read and search files in the task worktree and, for "
        "workers, write or edit them there, through Saarathi's own tools. It has no shell and no network; requests "
        "go only to the configured model server (this computer unless allow_remote is set)."
    )
    base_capabilities = ModelCapabilities(
        code_generation=True, repository_tools=True, structured_output=True, workspace_write=True
    )

    # -- configuration
    def base_url(self) -> str:
        return str(self.config.options.get("base_url", DEFAULT_BASE_URL)).rstrip("/")

    def _opt(self, key: str, default: Any) -> Any:
        return self.config.options.get(key, default)

    # -- probe
    def _probe_impl(self) -> ProbeResult:
        url = self.base_url()
        res = ProbeResult(
            adapter=self.name, provider=self.provider, available=False, binary=url, auth_method="local", metered=False
        )
        remote_ok = bool(self._opt("allow_remote", False))
        if not is_loopback(url) and not remote_ok:
            res.reason = f"{url} is not on this computer; set options.allow_remote: true to use it"
            res.checks.append(Check("local server", False, res.reason))
            return res
        if not is_loopback(url):
            res.notices.append(f"Code is sent to {url}, which is not this computer (allow_remote is set).")
        try:
            listing = http_json("GET", f"{url}/models", None, timeout=4)
        except Exception as exc:
            _, why = describe_http_error(exc)
            res.reason = f"no OpenAI-compatible model server at {url} ({why}). Start Ollama or LM Studio."
            res.checks.append(Check("server", False, res.reason))
            return res
        ids = [str(m.get("id")) for m in (listing or {}).get("data", []) if isinstance(m, dict) and m.get("id")]
        res.version = self._server_version(url)
        res.discovered_models = [DiscoveredModel(i, None, "local open-source model") for i in ids]
        res.discovery_source = f"GET {url}/models"
        res.checks.append(Check("server", True, f"{res.version or 'OpenAI-compatible server'} at {url}"))
        res.checks.append(Check("models", bool(ids), f"{len(ids)} installed" if ids else "no models installed"))
        if not ids:
            res.reason = "the server has no models; download one first (for example `ollama pull qwen2.5-coder:7b`)"
            return res
        res.available = True
        res.notices.append(
            "Local models are free and private, but small ones are weaker at multi-step edits and strict answers. "
            "Pick a model that supports tool calling."
        )
        return res

    def _server_version(self, url: str) -> str | None:
        root = url[: -len("/v1")] if url.endswith("/v1") else url
        try:
            v = http_json("GET", f"{root}/api/version", None, timeout=2)
            if isinstance(v, dict) and v.get("version"):
                return f"Ollama {v['version']}"
        except Exception:  # noqa: S110 - the version is an optional detail
            pass
        return None

    def list_models(self) -> list[Any]:
        """Like every adapter, plus: a local model the catalogue does not know is economy, never "unknown"
        (it is someone's own download, so it is usable, but never assumed strong)."""
        out = []
        for m in super().list_models():
            if m.capabilities.reasoning_tier is ReasoningTier.UNKNOWN:
                caps = m.capabilities.model_copy(update={"reasoning_tier": ReasoningTier.ECONOMY})
                meta = m.metadata.model_copy(update={"notes": f"economy: unrecognised local model; {m.metadata.notes}"})
                m = m.model_copy(update={"capabilities": caps, "metadata": meta})
            out.append(m)
        return out

    def worker_command(self, *, allow_network: bool, allowed_commands: Sequence[str]) -> list[str] | None:
        return None  # no external command: Saarathi drives the model itself

    # -- invocation
    async def invoke(self, inv: Invocation) -> InvocationResult:
        probe = self.probe()
        if not probe.available:
            return InvocationResult(
                ok=False, structured=None, text="", usage=Usage(), error_kind=ErrorKind.UNAVAILABLE, error=probe.reason
            )
        inv.log_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        result = await asyncio.to_thread(self._run_loop, inv, started)
        result.duration_s = round(time.monotonic() - started, 3)
        return result

    def _native_ollama(self) -> bool:
        """Ollama's own /api/chat lets Saarathi set the context window; its OpenAI endpoint is fixed at the
        server default (often 4096 tokens, too small for a worker prompt plus tools)."""
        api = str(self._opt("api", "auto"))
        if api in ("openai", "ollama"):
            return api == "ollama"
        return (self.probe().version or "").startswith("Ollama")

    def _chat(
        self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout: float
    ) -> tuple[dict[str, Any], int, int]:
        """One model turn: the assistant message, prompt tokens and completion tokens."""
        temperature = float(self._opt("temperature", 0.2))
        if self._native_ollama():
            url = self.base_url()
            root = url[: -len("/v1")] if url.endswith("/v1") else url
            options = {"num_ctx": int(self._opt("context_tokens", 16384)), "temperature": temperature}
            body = {"model": model, "messages": messages, "tools": tools, "stream": False, "options": options}
            resp = http_json("POST", f"{root}/api/chat", body, timeout) or {}
            return resp.get("message") or {}, int(resp.get("prompt_eval_count") or 0), int(resp.get("eval_count") or 0)
        body = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
            "stream": False,
        }
        resp = http_json("POST", f"{self.base_url()}/chat/completions", body, timeout) or {}
        u = resp.get("usage") or {}
        msg = ((resp.get("choices") or [{}])[0]).get("message") or {}
        return msg, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)

    def _run_loop(self, inv: Invocation, started: float) -> InvocationResult:
        writable = inv.mode is InvocationMode.WORKSPACE_WRITE
        ws = Workspace(inv.cwd, writable)
        tools = tool_specs(writable, inv.schema)
        names = {t["function"]["name"] for t in tools}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(write_rule=WRITE_RULE if writable else READ_RULE)},
            {"role": "user", "content": inv.prompt},
        ]
        usage = Usage(input_tokens=0, output_tokens=0, cost_usd=0.0, cost_is_estimate=False, reported_model=inv.model)
        transcript: list[str] = [
            f"model {inv.model} via {'Ollama /api/chat' if self._native_ollama() else 'OpenAI API'}"
        ]
        deadline = started + inv.timeout_s
        max_turns = int(self._opt("max_turns", 60))
        per_request = float(self._opt("request_timeout_seconds", 600))
        nudges = 0

        def finish(res: InvocationResult) -> InvocationResult:
            (inv.log_dir / "local-model.log").write_text(Redactor()("\n".join(transcript)), encoding="utf-8")
            res.usage = usage
            return res

        def fail(kind: ErrorKind, why: str, text: str = "") -> InvocationResult:
            return finish(
                InvocationResult(ok=False, structured=None, text=text, usage=usage, error_kind=kind, error=why)
            )

        def handle(name: str, raw: Any) -> tuple[str, dict[str, Any] | None]:
            """Run one tool call: (what to tell the model, the final answer if this was an accepted submit)."""
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError as exc:
                return f"error: could not parse the arguments as JSON ({exc})", None
            if not isinstance(args, dict):
                return "error: arguments must be a JSON object", None
            if name != "submit_result":
                return self._run_tool(ws, name, args), None
            problems = missing_fields(inv.schema, args)
            if problems:
                return "error: the answer is not complete: " + "; ".join(problems) + ". Call submit_result again.", None
            if writable and args.get("changed_files") and not ws.changed:
                return (
                    "error: your answer lists changed_files, but you have not changed any file. Make the change "
                    "with write_file or replace_in_file first, then call submit_result again.",
                    None,
                )
            return "accepted", args

        for turn in range(1, max_turns + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 2:
                return fail(ErrorKind.TIMEOUT, "the local model ran out of time")
            t0 = time.monotonic()
            try:
                msg, prompt_tokens, completion_tokens = self._chat(
                    inv.model, messages, tools, min(remaining, per_request)
                )
            except Exception as exc:
                kind, why = describe_http_error(exc)
                transcript.append(f"[turn {turn}] request failed: {why}")
                return fail(kind, why)
            usage.input_tokens = (usage.input_tokens or 0) + prompt_tokens
            usage.output_tokens = (usage.output_tokens or 0) + completion_tokens
            calls = [c for c in (msg.get("tool_calls") or []) if isinstance(c, dict)]
            content = msg.get("content") or ""
            transcript.append(
                f"[turn {turn}, {time.monotonic() - t0:.1f}s, {prompt_tokens} in / {completion_tokens} out] "
                f"assistant: {content[:2000]}"
                + "".join(
                    f"\n  call {(c.get('function') or {}).get('name')}"
                    f"({str((c.get('function') or {}).get('arguments'))[:1500]})"
                    for c in calls
                )
            )
            if calls:  # native tool calls: answer each with a tool message
                messages.append({"role": "assistant", "content": content, "tool_calls": calls})
                for call in calls:
                    fn = call.get("function") or {}
                    name = str(fn.get("name") or "")
                    out, final = handle(name, fn.get("arguments"))
                    transcript.append(f"  -> {out[:1500]}")
                    if final is not None:
                        transcript.append(f"[submitted; changed: {', '.join(ws.changed) or 'nothing'}]")
                        return finish(InvocationResult(ok=True, structured=final, text=content, usage=usage))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or name,
                            "name": name,
                            "tool_name": name,
                            "content": out[:MAX_TOOL_OUTPUT],
                        }
                    )
                continue
            messages.append({"role": "assistant", "content": content})
            written = text_tool_calls(content, names)
            if written:  # some models write tool calls as JSON text instead: run them and reply in text
                replies = []
                for name, raw in written:
                    out, final = handle(name, raw)
                    transcript.append(f"  (text call {name}) -> {out[:1500]}")
                    if final is not None:
                        transcript.append(f"[submitted; changed: {', '.join(ws.changed) or 'nothing'}]")
                        return finish(InvocationResult(ok=True, structured=final, text=content, usage=usage))
                    replies.append(f"Result of {name}:\n{out[:MAX_TOOL_OUTPUT]}")
                messages.append({"role": "user", "content": "\n\n".join(replies)})
                continue
            answer = parse_json_answer(content)
            if answer is not None and not missing_fields(inv.schema, answer):
                transcript.append("[accepted a JSON answer from plain text]")
                return finish(InvocationResult(ok=True, structured=answer, text=content, usage=usage))
            nudges += 1
            if nudges > MAX_NUDGES:
                return fail(ErrorKind.INVALID_OUTPUT, "the local model never called submit_result", content[-2000:])
            messages.append({"role": "user", "content": NUDGE})
        return fail(ErrorKind.INVALID_OUTPUT, f"the local model used {max_turns} turns without submitting a result")

    @staticmethod
    def _run_tool(ws: Workspace, name: str, args: dict[str, Any]) -> str:
        handlers: dict[str, Callable[..., str]] = {
            "list_files": ws.list_files,
            "read_file": ws.read_file,
            "search": ws.search,
            "write_file": ws.write_file,
            "replace_in_file": ws.replace_in_file,
        }
        handler = handlers.get(name)
        if handler is None:
            return f"error: unknown tool {name!r}; use {', '.join(handlers)} or submit_result"
        try:
            return str(handler(**args))
        except ToolError as exc:
            return f"error: {exc}"
        except TypeError as exc:
            return f"error: wrong arguments for {name}: {exc}"
        except OSError as exc:
            return f"error: {exc}"


def detect_local_servers() -> list[tuple[str, str, int]]:
    """Known local model servers that answer, with how many models each has: (label, base_url, model count)."""
    found = []
    for label, url in KNOWN_SERVERS:
        try:
            listing = http_json("GET", f"{url}/models", None, timeout=1.5)
        except Exception:  # noqa: S112 - that server is not running
            continue
        n = len([m for m in (listing or {}).get("data", []) if isinstance(m, dict)])
        found.append((label, url, n))
    return found
