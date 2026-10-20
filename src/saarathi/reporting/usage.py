"""Usage as the providers reported it, aggregated from attempts and lead calls. Nothing is estimated here.

Subscription CLIs report tokens per call (some not at all) and never report remaining quota or reset times, so this
summary has no quota figures. Costs are only those a provider or adapter recorded, flagged as estimates when they are.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from saarathi.orchestration.project import Project
from saarathi.storage.store import utcnow

QUOTA_NOTE = (
    "Remaining quota and reset times are not reported by any supported provider CLI, so Saarathi cannot show them."
)


@dataclass
class Tally:
    calls: int = 0
    succeeded: int = 0
    failed: int = 0
    other: int = 0  # cancelled, escalated, orphaned or still running
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    unreported: int = 0  # calls whose provider reported no token counts
    cost_usd: float = 0.0
    cost_reported: int = 0
    cost_is_estimate: bool = False
    duration_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def add(self, usage: dict[str, Any] | None, outcome: str | None, duration_s: float = 0.0) -> None:
        self.calls += 1
        if outcome == "succeeded":
            self.succeeded += 1
        elif outcome == "failed":
            self.failed += 1
        else:
            self.other += 1
        self.duration_s += duration_s
        usage = usage or {}
        tokens = [usage.get(k) for k in ("input_tokens", "output_tokens")]
        if all(t is None for t in tokens):
            self.unreported += 1
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.cached_input_tokens += int(usage.get("cached_input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        if usage.get("cost_usd") is not None:
            self.cost_usd += float(usage["cost_usd"])
            self.cost_reported += 1
            self.cost_is_estimate = self.cost_is_estimate or bool(usage.get("cost_is_estimate", True))

    def out(self) -> dict[str, Any]:
        data = {k: v for k, v in self.__dict__.items() if k != "extra"}
        data["cost_usd"] = round(self.cost_usd, 6) if self.cost_reported else None
        data["duration_s"] = round(self.duration_s, 1)
        return {**self.extra, **data}


def _seconds(start: str, end: str | None) -> float:
    if not end:
        return 0.0
    try:
        return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    except ValueError:
        return 0.0


def usage_summary(project: Project, *, max_runs: int = 20) -> dict[str, Any]:
    store = project.store
    totals = Tally()
    by_model: dict[tuple[str, str], Tally] = {}
    runs: list[dict[str, Any]] = []

    def model_tally(role: str, candidate: str, adapter: str, model: str) -> Tally:
        key = (role, candidate)
        if key not in by_model:
            by_model[key] = Tally(extra={"role": role, "candidate": candidate, "adapter": adapter, "model": model})
        return by_model[key]

    run_rows = store.query("SELECT id, state, created_at FROM runs ORDER BY created_at DESC, rowid DESC")
    for index, row in enumerate(run_rows):
        run_tally = Tally(extra={"run_id": row["id"], "state": row["state"], "created_at": row["created_at"]})
        for a in store.attempts(row["id"]):
            usage = (a.result or {}).get("usage")
            outcome = a.outcome if a.status != "running" else None
            seconds = _seconds(a.started_at, a.finished_at)
            for tally in (totals, run_tally, model_tally("worker", f"{a.adapter}:{a.model}", a.adapter, a.model)):
                tally.add(usage, outcome, seconds)
        for e in store.query(
            "SELECT payload_json FROM events WHERE run_id = ? AND type = 'lead.usage' ORDER BY seq", [row["id"]]
        ):
            payload = json.loads(e[0])
            adapter, model = str(payload.get("adapter", "?")), str(payload.get("model", "?"))
            for tally in (totals, run_tally, model_tally("lead", f"{adapter}:{model}", adapter, model)):
                tally.add(payload, "succeeded" if payload.get("ok", True) else "failed")
        if index < max_runs:
            runs.append(run_tally.out())

    now = utcnow()
    limits = [
        {"adapter": h.adapter, "state": h.state, "until": h.until, "reason": h.reason}
        for h in store.provider_health().values()
        if h.until > now
    ]
    models = sorted((t.out() for t in by_model.values()), key=lambda m: (m["role"] != "lead", -m["calls"]))
    return {
        "generated_at": now,
        "quota_note": QUOTA_NOTE,
        "totals": totals.out(),
        "by_model": models,
        "runs": runs,
        "limits": limits,
    }
