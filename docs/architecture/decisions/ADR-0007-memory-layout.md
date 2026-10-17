# ADR-0007: Project memory layout and context compilation

- Status: Accepted
- Date: 2026-10-18

## Decision

```
.saarathi/
  project.yaml        # project id and name (committed)
  policies.yaml       # routing/billing/permission policy (committed)
  context.md          # brief and clarification answers (committed)
  architecture.md     # accepted plan summary (committed)
  decisions/          # ADR-NNNN-*.md, append-only (committed)
  tasks/              # task packets as JSON (committed)
  checkpoints/        # escalation and checkpoint notes (committed)
  lessons.md          # validated lessons only (committed)
  reports/            # completion reports (committed)
  state.db, logs/, engine.lock   # runtime only, ignored by .saarathi/.gitignore
```

- Global preferences live in `platformdirs.user_config_dir("saarathi")`, never in the repository.
- The context compiler builds each task's context packet from the accepted plan, the task packet and accepted ADRs. Each
  item is cited with its path and SHA-256 version. The packet is size-capped and deduplicated, and repository-derived text is
  wrapped in an explicit untrusted-data envelope. The packet is stored immutably as the task's `context_snapshot_id`.
- Memory proposals from workers are recorded as events, not applied. Promotion to `lessons.md` requires lead or user approval
  (Phase 2).
