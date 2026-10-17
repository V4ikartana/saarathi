# ADR-0003: SQLite state with an append-only event log; Markdown for durable decisions

- Status: Accepted
- Date: 2026-10-18

## Decision

- Operational state lives in `.saarathi/state.db` (SQLite, WAL mode, `foreign_keys=ON`). It is ignored by Git.
- Every state mutation runs in **one transaction** that (a) checks the transition is legal for the current state,
  (b) updates the projection row and (c) appends an event (`seq`, timestamp, actor, run/task ids, correlation id, type,
  redacted JSON payload, schema version). Projections can therefore never disagree with the log.
- Mutating commands accept an idempotency key. Replaying a key returns the original event instead of applying it twice.
- Schema migrations are ordered SQL scripts tracked with `PRAGMA user_version`.
- Canonical long-lived decisions (context, accepted plan, ADRs, lessons) are Markdown under `.saarathi/` so people can
  review them in Git. ADR files are append-only. Superseding writes a new ADR and records the link.
- Mutable memory documents use optimistic concurrency: writers pass the SHA-256 of the version they read, and a mismatch
  is rejected.
- Only one engine may drive a project at a time, enforced by an OS file lock (`fcntl.flock`) on `.saarathi/engine.lock`.
  Holding the lock means any `LEASED`/`RUNNING` task found at start-up is orphaned and must be reconciled.

## Consequences

- No server process is needed, and recovery is a pure function of the database and the Git state.
- `fcntl` makes the engine lock POSIX-only. Windows support needs an alternative (backlog item).
