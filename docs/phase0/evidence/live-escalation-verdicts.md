# Live evidence: escalation schemas (2026-10-19)

`SAARATHI_LIVE_TESTS=1 pytest tests/live -k "escalation or workspace_write"`: 4 passed.

| CLI | Worker schema with `outputs` + `escalations` (strict) | Lead `ESCALATION_SCHEMA` (nested object, nullable integer) | Verdict on the sample checkpoint |
|---|---|---|---|
| Claude Code 2.1.302 (`sonnet`) | accepted | accepted | `supersede_adr`, `supersedes: 1`, JSON health contract |
| codex-cli 0.162.0-alpha.17.2 (default model) | accepted | accepted | `retry_with_guidance`, `supersedes: null` |

The sample checkpoint is in `tests/live/test_escalation_live.py`. Different leads reaching different, valid verdicts
is why `supersede_adr` always waits for a person (ADR-0010).
