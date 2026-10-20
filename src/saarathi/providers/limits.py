"""Remaining subscription usage, exactly as each provider CLI reports it (no model call, no credentials read).

- Claude Code: ``claude -p /usage`` prints "Current session: 53% used · resets …" lines.
- Codex CLI: its app-server answers ``account/rateLimits/read`` with used percentages per window and reset times.
- Antigravity CLI: ``agy -p /usage --output-format json`` returns groups of buckets with ``remaining_fraction``.

Saarathi only parses what the CLI prints. Account identifiers are dropped. If a CLI changes its output, the reader
reports that limits are unavailable instead of guessing.
"""

from __future__ import annotations

import contextlib
import json
import queue
import re
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class LimitWindow:
    label: str  # e.g. "Current session", "Weekly · Gemini models"
    used_percent: float  # 0 to 100, as reported (or 100 minus the reported remaining share)
    resets_at: str | None = None  # ISO 8601 when the CLI gives a timestamp
    resets_text: str | None = None  # the CLI's own wording when it gives no timestamp
    window: str | None = None  # "5h", "weekly", "session" … when known

    @property
    def remaining_percent(self) -> float:
        return max(0.0, 100.0 - self.used_percent)


@dataclass
class ProviderLimits:
    adapter: str
    source: str  # the command that reported them
    windows: list[LimitWindow] = field(default_factory=list)
    plan: str | None = None
    error: str | None = None  # set when limits could not be read
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "source": self.source,
            "plan": self.plan,
            "error": self.error,
            "checked_at": self.checked_at,
            "windows": [
                {
                    "label": w.label,
                    "used_percent": round(w.used_percent, 1),
                    "remaining_percent": round(w.remaining_percent, 1),
                    "resets_at": w.resets_at,
                    "resets_text": w.resets_text,
                    "window": w.window,
                }
                for w in self.windows
            ],
        }


# ---------------------------------------------------------------- Claude Code

_CLAUDE_LINE = re.compile(
    r"^\s*(?P<label>[^:\n]{2,60}):\s*(?P<pct>\d{1,3}(?:\.\d+)?)%\s+used(?:\s*·\s*resets\s+(?P<reset>.+))?$"
)


def parse_claude_usage(stdout: str) -> list[LimitWindow]:
    """Windows from ``claude -p /usage --output-format json``. Raises ValueError if none are recognised."""
    text = stdout
    for line in reversed(stdout.strip().splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("result"), str):
            text = payload["result"]
            break
    windows = []
    for line in text.splitlines():
        match = _CLAUDE_LINE.match(line)
        if match:
            label = match["label"].strip()
            lowered = label.lower()
            kind = "weekly" if "week" in lowered else ("session" if "session" in lowered else None)
            windows.append(
                LimitWindow(label, float(match["pct"]), resets_text=(match["reset"] or "").strip() or None, window=kind)
            )
    if not windows:
        raise ValueError("`claude /usage` printed no usage lines")
    return windows


# ---------------------------------------------------------------- Codex CLI


def _window_name(minutes: int | None) -> tuple[str, str | None]:
    if minutes == 300:
        return "5-hour window", "5h"
    if minutes == 10080:
        return "Weekly", "weekly"
    if minutes:
        hours = minutes / 60
        return (f"{hours:g}-hour window", f"{hours:g}h") if hours < 48 else (f"{hours / 24:g}-day window", None)
    return "Usage window", None


def parse_codex_rate_limits(result: Mapping[str, Any]) -> tuple[list[LimitWindow], str | None]:
    """Windows and plan from an ``account/rateLimits/read`` result. Raises ValueError on an unknown shape."""
    snapshot = result.get("rateLimits")
    if not isinstance(snapshot, dict):
        raise ValueError("Codex returned no rate-limit snapshot")
    windows = []
    for key in ("primary", "secondary"):
        w = snapshot.get(key)
        if not isinstance(w, dict) or not isinstance(w.get("usedPercent"), int | float):
            continue
        label, kind = _window_name(w.get("windowDurationMins"))
        resets = w.get("resetsAt")
        iso = datetime.fromtimestamp(resets, UTC).isoformat(timespec="seconds") if isinstance(resets, int) else None
        windows.append(LimitWindow(label, float(w["usedPercent"]), resets_at=iso, window=kind))
    if not windows:
        raise ValueError("Codex reported no usage windows")
    plan = snapshot.get("planType")
    return windows, plan if isinstance(plan, str) else None


def read_codex_limits(
    binary: str, env: Mapping[str, str], timeout: float = 20.0
) -> tuple[list[LimitWindow], str | None]:
    """Ask Codex's own app-server (stdio JSON-RPC) for the account's rate limits, as ``/status`` does."""
    proc = subprocess.Popen(
        [binary, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=dict(env),
    )
    assert proc.stdin is not None and proc.stdout is not None
    deadline = time.monotonic() + timeout

    def send(message: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:  # a reader thread: select() does not work on pipes on Windows
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()

    def reply(request_id: int) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Codex app-server did not answer in time")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                raise ValueError("Codex app-server exited before answering")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise ValueError(f"Codex app-server error: {message['error'].get('message', 'unknown')}")
                return dict(message.get("result") or {})

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "saarathi", "version": "0.1"}},
            }
        )
        reply(1)
        send({"jsonrpc": "2.0", "method": "initialized"})
        send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "account/rateLimits/read",
                "params": {"excludeResetCreditDetails": True},
            }
        )
        return parse_codex_rate_limits(reply(2))
    finally:
        with contextlib.suppress(OSError):
            proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------- Antigravity CLI


def parse_agy_usage(stdout: str) -> list[LimitWindow]:
    """Windows from ``agy -p /usage --output-format json``. Raises ValueError if the shape is not recognised."""
    try:
        payload = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError("unrecognised output from `agy /usage`") from exc
    groups = ((payload.get("command") or {}).get("data") or {}).get("groups") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        raise ValueError("`agy /usage` returned no usage groups")
    windows = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for bucket in group.get("buckets") or []:
            fraction = bucket.get("remaining_fraction") if isinstance(bucket, dict) else None
            if not isinstance(fraction, int | float):
                continue
            kind = bucket.get("window")
            name = (
                "5-hour window"
                if kind == "5h"
                else ("Weekly" if kind == "weekly" else str(bucket.get("name", "Usage")))
            )
            windows.append(
                LimitWindow(
                    f"{name} · {group.get('name', 'models')}",
                    (1.0 - float(fraction)) * 100.0,
                    resets_at=bucket.get("reset_time") if isinstance(bucket.get("reset_time"), str) else None,
                    window=kind if isinstance(kind, str) else None,
                )
            )
    if not windows:
        raise ValueError("`agy /usage` reported no usage buckets")
    return windows


def run_text(argv: Sequence[str], env: Mapping[str, str], timeout: float) -> str:
    proc = subprocess.run(
        list(argv),
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise ValueError((proc.stderr or proc.stdout).strip()[-200:] or f"exit {proc.returncode}")
    return proc.stdout
