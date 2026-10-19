# Implementation backlog (dependency-ordered)

Each item has a testable definition of done (DoD). `→` lists dependencies. Status reflects the repository, not intent.

## Phase 1: runnable vertical slice

| ID | Item | → | Definition of done | Status |
|---|---|---|---|---|
| S1 | Contracts: `ModelDescriptor`, `TaskPacket`, `WorkerResult`, `Plan`, `RoutingDecision`, policy model | – | pydantic models; invalid policy rejected with a field path | Done |
| S2 | State machines for run and task | S1 | Illegal transitions raise; table-driven tests cover every edge | Done |
| S3 | SQLite store: migrations, events, projections, idempotency keys | S2 | A transition and its event commit atomically; a replayed idempotency key is a no-op | Done |
| S4 | Process runner: process group, timeout, cancel, env allowlist, redacted capped logs | – | Timeout and cancel kill the whole group; secrets in output are redacted | Done |
| S5 | Provider port + Codex CLI adapter + Claude Code adapter + fake adapter | S1, S4 | Contract tests on recorded fixtures; Codex live test passes when opted in | Done (Claude adapter unverified live) |
| S6 | Router: hard filters, capacity, scoring, explanation | S1 | Denied, unknown-capability and metered-when-disabled candidates never chosen | Done |
| S7 | Git workspace: worktrees outside repo, commit, scope diff, checkout integrity, integration merge | – | Two tasks on separate worktrees; out-of-scope edits rejected; conflicts detected | Done |
| S8 | Verifier: command policy, run commands, evidence | S4, S7 | No task succeeds without passing verification evidence | Done |
| S9 | Memory store + context compiler | S3 | Stale-version writes rejected; ADRs cannot be overwritten; packet cites versions | Done |
| S10 | Lead agent: intake/clarify, plan, final review via structured completion | S5, S9 | Plan validated (DAG acyclic, paths, commands) before it is stored | Done |
| S11 | Engine: scheduler, leases, retries with feedback, escalation pause, integration, report | S3–S10 | Scenario A with fakes passes | Done |
| S12 | Recovery: engine lock, orphan reconciliation, no duplicate worker runs | S11 | Scenario B (SIGKILL mid-run, resume) passes | Done |
| S13 | CLI: init, doctor, models list, policy validate, run, resume, answer, approve, status, tasks, logs, route explain, review, report, pause | S11 | `--json` output validated against pydantic schemas; stable exit codes | Done |
| S14 | Live two-provider demonstration (Claude + Codex) on `examples/health-widget` | S13, user installs Claude Code | Scenario A with real CLIs produces a branch and report | Done (2026-10-19; see feasibility report §6) |

## Phase 2: complete v0.1

| ID | Item | → | DoD | Status |
|---|---|---|---|---|
| P1 | Integration and account registry (§12A): accounts, connections, capabilities, aliases, defaults, tenant scope | S3, S6 | Scenarios G–P pass with fake adapters | Done (ADR-0008; no runtime exposes a documented inventory, so discovery reports `unverified`) |
| P2 | Integration-aware hard filters + `integrations` CLI | P1 | `route explain` shows integration rejections | Done |
| P2a | Lead plan schema emits `integrations` (strict subset, nullable account/tenant/resource); plan check rejects two accounts of one service in a task | P2, live CLI re-check | Live lead plan with an integration requirement validates on both CLIs | Done (verified live on Claude Code 2.1.302 and codex-cli 0.162.0-alpha.17.2, 2026-10-19; see [evidence](evidence/live-plan-schema-integrations.md)) |
| P2b | Cross-task data handoff that applies `share_fields` / `shareable_fields` to content | P2a | A dependent task receives only permitted fields of a dependency's result; a denied transfer pauses | Open (design proposal only) |
| P3 | Budget ledger with atomic reservations; API adapters (Anthropic, OpenAI, Google) | S6 | Scenario C passes; concurrent reservations never exceed the cap |  |
| P4 | Antigravity CLI adapter (verify non-TTY `-p` first) | S5 | Live probe or documented disablement |  |
| P5 | Model-backed operations orchestrator (triage, checkpoint summaries) behind `OperationsAgent` | S11 | Scenario E: contract change pauses and needs lead approval to supersede the ADR |  |
| P6 | Rate-limit backoff and provider exhaustion handling | S11 | Scenario D passes |  |
| P7 | Memory proposals → lessons promotion with approval | S9 | Unapproved proposals never reach `lessons.md` |  |
| P8 | CI (Linux/macOS), secret scanning, dependency audit, release packaging | – | Green CI on PRs |  |
| P9 | Windows support (locking, process groups) | S12 | Test suite green on Windows |  |
