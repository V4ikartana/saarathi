# Changelog

All notable changes are documented here. The format follows Keep a Changelog. Versions follow SemVer once
1.0 is released.

## [Unreleased]

### Added
- Phase 0: feasibility report, provider compatibility matrix, ADR-0001 to ADR-0007, risk register, backlog.
- Deterministic control plane: run and task state machines, SQLite store with an atomic event log and
  idempotency keys, crash-safe engine lock and orphan reconciliation.
- Provider adapters: Codex CLI (verified live on 0.162.0-alpha.2), Claude Code (verified live on 2.1.302),
  test-only fake adapter.
- Explainable router with hard filters, capacity handling and weighted scoring; pinned engineering lead.
- Git worktree isolation, diff-scope enforcement, checkout-integrity check, integration merges.
- Command policy and Saarathi-run verification with evidence; Markdown/JSON reports.
- CLI: init, doctor (with `--live`), models list, policy validate, run, resume, answer, approve, status,
  tasks, logs, pause, cancel, review, report, route explain.
- Example project `examples/health-widget` and demo script; live Claude-led two-provider run recorded.
- Integration- and identity-aware routing (brief §12A, ADR-0008): workspace-scoped account/connection registry
  (schema migration 2), `IntegrationDiscoveryAdapter` contract, `IntegrationRequirementMatcher` hard filters,
  cross-provider transfer policy, single-use side-effect approvals, a recheck before dispatch, and
  `saarathi integrations list|accounts|scan|register|verify|revoke|alias|default|approve|select`.
  Discovery reports `unverified` for every supported CLI.
- The lead's strict plan schema carries per-task `integrations` (one account per requirement; nullable
  account/tenant/resource), and plan checks reject a task that uses two accounts of one service. Verified live on
  Claude Code and Codex CLI (`docs/phase0/evidence/live-plan-schema-integrations.md`).

- Cross-task data handoff (P2b, ADR-0009): workers return named `outputs`; dependent tasks receive only the
  fields policy permits (`share_fields`, approved cross-provider transfers), redacted and size-capped, with
  field names audited in `handoff.prepared` events. Integration data cannot be passed on through an intermediate task.

- Provider limits (Scenario D): quota exhaustion is told apart from transient rate limits; a limited adapter
  cools down (escalating 30 s / 2 min / 5 min, then "exhausted" for an hour, all configurable) and routing moves
  work to other eligible workers. Short cooldowns are waited out; exhaustion pauses the run with diagnostics.
  Limit retries are bounded per task and do not use the failure budget. New `saarathi providers status|reset`;
  `models list` and `doctor` show active limits.

- Escalation protocol (Scenario E, ADR-0010): workers stop and escalate conflicts with accepted decisions; the
  task and its dependents pause while independent work continues; an evidence-backed checkpoint goes to the
  lead, whose `retry_with_guidance` applies automatically while `supersede_adr` waits for `saarathi approve`
  before any ADR changes. Bounded per task. New `saarathi escalations [reject ID]`.

- v0.1 scope is subscription-only (ADR-0011): `policy validate` rejects API and hybrid modes and enabled API
  billing.

- Packaging: version single-sourced from `saarathi.__init__`; source distribution limited to code, tests, docs
  and examples; CI builds the wheel and smoke-tests it in a clean environment.
- Lessons (P7): workers' memory proposals from verified attempts are recorded as proposals; `saarathi lessons
  approve|reject` decides, approved lessons go to `.saarathi/lessons.md` and into later tasks' context.

- Antigravity CLI adapter (`agy` 1.4.2, provider `google`): structured output via `--json-schema`, edits with
  `--mode accept-edits`, read-only by headless default, real model discovery through `agy models`, a pinned
  model recorded as the model used, and one bounded retry when a denied tool discards the result. Included in
  the default policy and a three-provider example policy.

- Windows support (ADR-0012): portable process-tree termination and liveness checks, `msvcrt` engine lock,
  Windows environment allowlist, LF memory files; a Windows CI job runs the full suite.

- Saarathi Desktop foundation (ADR-0013): `saarathi bridge --stdio` (JSON-RPC protocol 1) with conversation
  sessions (migration 5), detached run drivers, live event notifications, stop/cancel/resume, idempotent sends
  and approvals; a Tauri 2 + React app over it with projects, sessions, the conversation and run controls, and an
  execution panel. CI builds and tests the desktop app on Linux, macOS and Windows.

### Fixed
- The command policy now recognises versioned and Windows program names (`python3.12 -c`, `Python.EXE -c`,
  `cmd /c`, PowerShell `-Command`) and drive-letter paths.
- Unblocking a task on resume no longer makes it READY before its dependencies have succeeded.
- Dependent tasks now start from their dependencies' verified commits instead of the bare run base; conflicting
  dependencies block the task with a checkpoint (ADR-0009).
- Two checkpoints written for one run within the same second no longer collide.
- Build artifacts from workers' test runs (`__pycache__`, caches) no longer fail scope checks
  (`execution.ignored_artifacts`).
