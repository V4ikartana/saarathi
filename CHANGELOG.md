# Changelog

All notable changes are documented here. The format follows Keep a Changelog. Versions follow SemVer once
1.0 is released.

## [Unreleased]

### Added
- Phase 0: feasibility report, provider compatibility matrix, ADR-0001 to ADR-0007, risk register, backlog.
- Deterministic control plane: run and task state machines, SQLite store with an atomic event log and
  idempotency keys, crash-safe engine lock and orphan reconciliation.
- Provider adapters: Codex CLI (verified live on 0.162.0-alpha.2), Claude Code (documented, unverified live),
  test-only fake adapter.
- Explainable router with hard filters, capacity handling and weighted scoring; pinned engineering lead.
- Git worktree isolation, diff-scope enforcement, checkout-integrity check, integration merges.
- Command policy and Saarathi-run verification with evidence; Markdown/JSON reports.
- CLI: init, doctor (with `--live`), models list, policy validate, run, resume, answer, approve, status,
  tasks, logs, pause, cancel, review, report, route explain.
- Example project `examples/health-widget` and demo script.
