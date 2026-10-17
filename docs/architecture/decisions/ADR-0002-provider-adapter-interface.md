# ADR-0002: Provider adapter interface and structured completion artifacts

- Status: Accepted
- Date: 2026-10-18

## Context

CLI agents (Claude Code, Codex, later Antigravity/Gemini) and HTTP APIs differ in output formats, permission models and
usage reporting. The brief forbids assuming CLI text output is structured.

## Decision

- One port, `ProviderAdapter`, exposes `probe()`, `list_models()`, `invoke(Invocation)`, and capability declarations.
  Cancellation is done through asyncio cancellation, which kills the worker's process group.
- An `Invocation` has a mode: `read_only` (lead planning, reviews) or `workspace_write` (task execution inside one
  worktree). It also carries a prompt, a JSON Schema, a working directory, a model and a timeout.
- Adapters obtain structured results **only through the provider's documented schema mechanism**:
  Codex `--output-schema` + `-o`, Claude Code `--json-schema` → `structured_output`. If that mechanism is missing, the
  capability `structured_output` is `false` and the adapter cannot be used for roles that require it.
- A worker's self-report is **advisory**. Saarathi computes changed files from Git itself, enforces path scope itself and
  runs verification commands itself. A natural-language "done" never counts as evidence.
- Each adapter pins a minimum CLI version and the flags it relies on. `doctor` checks these, and an adapter that fails a check is
  marked unavailable with a fix-it message. It is never simulated.
- A `fake` adapter exists for deterministic tests. It is registered only when `SAARATHI_ALLOW_FAKE_PROVIDERS=1` is set, and `doctor`
  labels it test-only.

## Consequences

- Adding a provider means one module plus contract tests. Orchestration code does not change.
- Usage is reported only where the provider reports it. Codex does not report the model used, so it is recorded as `unreported`.
