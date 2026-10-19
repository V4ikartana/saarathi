"""Scripted, deterministic adapter for tests and offline demos. Never usable in normal operation.

Registered only when ``SAARATHI_ALLOW_FAKE_PROVIDERS=1``. Its behavior comes from a JSON script:

    {
      "complete": {"lead.intake": {...}, "lead.plan": {...}, "lead.review": {...}},
      "execute": {"T-001": [ {attempt 1 actions}, {attempt 2 actions} ]}
    }

Attempt actions: ``write`` {relative path: content}, ``write_outside`` {absolute path: content},
``sleep`` seconds, ``fail`` (an ErrorKind value, e.g. "rate_limit" or "quota_exhausted"), ``fail_on`` (adapter
names the failure applies to; default all), ``report`` (WorkerReport).
Every invocation is appended to ``options.record_path`` (JSONL) so tests can count executions.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from saarathi.core.contracts import Usage
from saarathi.providers.base import (
    ErrorKind,
    Invocation,
    InvocationResult,
    ProbeResult,
    ProviderAdapter,
)

FAKE_ENV_FLAG = "SAARATHI_ALLOW_FAKE_PROVIDERS"


def fake_providers_allowed() -> bool:
    return os.environ.get(FAKE_ENV_FLAG) == "1"


class FakeAdapter(ProviderAdapter):
    type_name = "fake"

    def __init__(self, name: str, config: Any) -> None:
        super().__init__(name, config)
        self.provider = config.provider or "fake"
        self._attempts: dict[str, int] = {}

    def _script(self) -> dict[str, Any]:
        path = self.config.options.get("script")
        if not path:
            return {}
        data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        return data

    def _probe_impl(self) -> ProbeResult:
        available = bool(self.config.options.get("available", True))
        return ProbeResult(
            adapter=self.name,
            provider=self.provider,
            available=available,
            version="fake",
            auth_method="none",
            metered=bool(self.config.options.get("metered", False)),
            reason=None if available else "fake adapter configured as unavailable",
            test_only=True,
            notices=["Test-only fake adapter. Results are scripted, not produced by a model."],
        )

    def _record(self, inv: Invocation) -> int:
        key = inv.task_id or inv.purpose
        record_path = self.config.options.get("record_path")
        attempt = self._attempts.get(key, 0)
        if record_path:
            path = Path(record_path)
            if path.exists():
                previous = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                attempt = sum(1 for p in previous if p["key"] == key and p["adapter"] == self.name)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "adapter": self.name,
                            "key": key,
                            "purpose": inv.purpose,
                            "model": inv.model,
                            "prompt": inv.prompt,
                            "cwd": str(inv.cwd),
                            "ts": time.time(),
                        }
                    )
                    + "\n"
                )
        self._attempts[key] = attempt + 1
        return attempt

    async def invoke(self, inv: Invocation) -> InvocationResult:
        attempt = self._record(inv)
        script = self._script()
        usage = Usage(input_tokens=100, output_tokens=20, reported_model=inv.model, cost_usd=None)
        if inv.task_id is None:
            response = script.get("complete", {}).get(inv.purpose)
            if response is None:
                return InvocationResult(
                    ok=False,
                    structured=None,
                    text="",
                    usage=usage,
                    error_kind=ErrorKind.INVALID_OUTPUT,
                    error=f"fake script has no response for {inv.purpose}",
                )
            if isinstance(response, list):
                response = response[min(attempt, len(response) - 1)]
            return InvocationResult(ok=True, structured=response, text=json.dumps(response), usage=usage)

        steps = script.get("execute", {}).get(inv.task_id, [])
        if not steps:
            return InvocationResult(
                ok=False,
                structured=None,
                text="",
                usage=usage,
                error_kind=ErrorKind.PROCESS,
                error=f"fake script has no actions for {inv.task_id}",
            )
        step: dict[str, Any] = steps[min(attempt, len(steps) - 1)]
        if step.get("sleep"):
            await asyncio.sleep(float(step["sleep"]))
        for rel, content in step.get("write", {}).items():
            target = inv.cwd / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        for absolute, content in step.get("write_outside", {}).items():
            Path(absolute).write_text(content, encoding="utf-8")
        if step.get("fail") and self.name in step.get("fail_on", [self.name]):
            kind = ErrorKind(step["fail"])
            return InvocationResult(
                ok=False,
                structured=None,
                text="",
                usage=usage,
                error_kind=kind,
                error=f"scripted failure: {kind}",
            )
        report = step.get("report") or {
            "status": "succeeded",
            "summary": "scripted change",
            "changed_files": sorted(step.get("write", {})),
            "tests_run": [],
            "assumptions": [],
            "unresolved_risks": [],
            "memory_proposals": [],
        }
        return InvocationResult(ok=True, structured=report, text=json.dumps(report), usage=usage)
