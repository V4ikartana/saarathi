# ADR-0009: Cross-task handoff of code and data

- Status: Accepted
- Date: 2026-10-19
- Extends: ADR-0004 (isolation), ADR-0008 (integration routing)

## Context

Two handoff gaps remained after the Phase 1 slice and ADR-0008:

1. **Code.** Every task's worktree started at the run's base commit, so a dependent task could not see the code its
   dependencies wrote. The live demo did not hit this because its tasks were independent.
2. **Data.** ADR-0008 decides whether a worker may receive integration data, but nothing carried results between
   tasks. `share_fields` and `integrations.shareable_fields` were recorded but applied to no content (backlog P2b).

## Decision

**Code handoff.** A task with dependencies starts from their verified work. One dependency means its verified commit.
Several mean a `--no-ff` merge of their verified commits on `saarathi/<run>/<task>-base`. The base is computed once at
dispatch, persisted as a run control row, and recorded on each attempt, so retries and crash recovery start from the
same commit. Diff-scope checks compare against that base, so inherited files never count as the task's own changes.
If dependencies conflict when combined, the task is blocked and a checkpoint is written. A worker is never asked to
resolve the conflict silently.

**Data handoff.** Workers may return named results (`outputs: [{name, value}]`) in their structured report. When a
dependent task is dispatched, `orchestration/handoff.py` decides field by field what it receives, for transitive
dependencies:

- A dependency that used an integration hands on only its requirements' `share_fields`. For a receiver on another
  provider, those are narrowed to the fields in the routing decision's approved transfer check for that exact
  candidate. A missing or unapproved check passes nothing.
- A dependency without integrations hands on all its outputs, unless integration data reached it upstream. Its output
  names then reveal nothing about where the data came from, so its outputs pass only when every upstream integration
  source stays with the receiving worker's provider. This blocks laundering through an intermediate task.
- Values are redacted, capped at 4,000 characters each and 20,000 in total, and given to the worker as untrusted data.
  The event log records `handoff.prepared` with the field names passed and dropped, never the values. Each attempt
  records `received_inputs`.

Routing still decides first. A denied transfer means no eligible worker, so the task blocks and the run pauses before
any content moves.

## Consequences

- Dependent code tasks can build on each other. Conflicting parallel dependencies surface as a checkpoint, not as a
  worker's guess.
- Outputs are only as good as the worker's honesty. Saarathi can filter what is passed but cannot prove a value really
  came from the account it claims. That is acceptable because routing already limits which accounts a worker can reach.
- Workers' self-reported `outputs` remain advisory, like the rest of the worker report. Verification commands still
  decide success.
- The worker output schema gained `outputs`. Both CLIs accepted it live in strict mode on 2026-10-19
  (Claude Code 2.1.302, codex-cli 0.162.0-alpha.17.2).
