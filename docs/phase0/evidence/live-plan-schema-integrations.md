# Live check: lead plan schema with integration requirements (P2a)

Status: **verified live on both CLIs** (2026-10-19, macOS, subscription logins).

## What is being checked

`PLAN_SCHEMA` (`src/saarathi/orchestration/prompts.py`) now has a per-task `integrations` array. Each entry is a
closed object where every property is required. `account`, `tenant` and `resource` use `{"type": ["string", "null"]}`.
The two CLIs receive the schema as follows:

- Claude Code: `--json-schema <inline JSON>`, result in `structured_output`.
- Codex CLI: `--output-schema <file>` (OpenAI strict structured output).

Before this check, nullable type arrays and nested closed objects inside arrays were unverified on both CLIs. Each
live test sends the real planning prompt with a brief that needs two Gmail accounts. It then asserts that:

1. the CLI accepts the schema and returns structured output (`result.ok`);
2. the output validates as `Plan` in pydantic (the trust boundary);
3. at least one task has an integration requirement;
4. no task names two accounts of the same service, as the prompt asks.

## How to run

```bash
SAARATHI_LIVE_TESTS=1 uv run pytest -s tests/live -k lead_plan_with_integration
```

`SAARATHI_CLAUDE_MODEL` (default `sonnet`), `SAARATHI_CLAUDE_BIN` and `SAARATHI_CODEX_BIN` work as they do for
the other live tests.

## Results

| CLI | Version | Date | Result |
|---|---|---|---|
| Claude Code | 2.1.302, model `sonnet` | 2026-10-19 | **Pass** (21 s) |
| Codex CLI | codex-cli 0.162.0-alpha.17.2 (bundled with ChatGPT.app, via `SAARATHI_CODEX_BIN`), model `default` | 2026-10-19 | **Pass** (45 s) |

Both CLIs accepted the schema as written: `["string", "null"]` types and closed nested objects in strict mode. No
fallback was needed. Each returned output that validated as `Plan`, and both split the two-account brief into one
task per account:

| CLI | Tasks | Integration requirements |
|---|---|---|
| Claude Code | T-001 script and tests; T-002 work digest; T-003 personal digest (both depend on T-001) | T-002 `mail.gmail`/`mail.read` account `work`; T-003 same with `personal`. Tenant and resource were null, `consent_required: true`, `share_fields: []` |
| Codex CLI | T-001 formatter and CLI; T-002 offline tests; T-003 work digest; T-004 personal digest | T-003 account `work`; T-004 account `personal`. Same fields, except `share_fields: ["sender", "subject"]` |

Notes:

- The two models filled `share_fields` differently for the same brief. This is a model choice, and the
  router and transfer policy still decide what actually crosses a boundary (ADR-0008).
- The test only checks the account-split rule from `plan_policy_problems`. Other plan checks, such as the
  verification-command policy, are covered offline and by the earlier end-to-end runs.
- This verifies that the schema and contract round-trip. It does not verify mail access: neither CLI has a documented
  integration inventory, so no real Gmail account was involved.
