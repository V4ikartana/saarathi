# ADR-0010: Escalation protocol

- Status: Accepted
- Date: 2026-10-19
- Extends: ADR-0001 (control plane), ADR-0003 (persistence)

## Context

Brief Scenario E: when implementation reveals a conflict with the approved plan, for example an API contract
that cannot work, the operational orchestrator must pause affected tasks, send a concise, evidence-backed
checkpoint to the lead, and obtain approval before an accepted ADR changes. Before this ADR, a worker that could
not proceed simply failed, and retries ran into the same wall.

## Decision

1. **Workers escalate; they do not work around.** The worker report has `escalations: [{kind, summary, evidence,
   proposed_change}]` with `kind` in `contract_change`, `scope_change`, `ambiguous_requirement` or
   `security_concern`. The prompt tells workers to stop with `status: blocked` and an escalation instead of
   deviating from an accepted decision, contract or scope.
2. **Saarathi pauses only affected work.** The attempt's changes are discarded, keeping their diff stat as evidence,
   and the attempt is recorded as `escalated`. It is not a failure and is excluded from routing history. The task is
   `BLOCKED`, its dependents block through the normal dependency rule, and independent tasks keep running.
   Escalations are persisted (migration 3) and logged.
3. **Operations builds the checkpoint; the lead decides.** Once nothing is running, the operations role
   (`DeterministicOperations`, behind an `OperationsAgent` interface for a later model-backed version) assembles
   the checkpoint. It contains the worker's claim, evidence and proposal, the discarded diff stat, the task as
   approved, the accepted ADRs, the waiting dependents and earlier escalations. The checkpoint is saved under
   `.saarathi/checkpoints/` and sent to the lead as untrusted data. The lead returns `retry_with_guidance`,
   `supersede_adr` (with the replacement ADR and the number it supersedes) or `stop`.
4. **Approval gates architecture changes.**
   - `retry_with_guidance` leaves ADRs untouched. It applies automatically: the guidance is added to the task's next
     prompt and the task and its dependents are unblocked.
   - `supersede_adr` pauses the run. The ADR files do not change until a person runs `saarathi approve`. Then the
     new ADR is written as Accepted with `Supersedes: ADR-NNNN`, the old one becomes `Superseded by ADR-MMMM`
     (append-only, ADR-0003), and the task continues with the lead's guidance and the new decision text.
     `saarathi escalations reject <id>` declines it.
   - `stop`, or a proposal to supersede anything other than an accepted ADR of this project, pauses for a person.
5. **Bounded.** After `execution.max_escalations_per_task` (default 2) escalations, a task's next escalation goes
   straight to a person without consulting the lead again. If the lead cannot be reached, the escalation stays open
   and the run pauses.

## Consequences

- Architecture changes are explicit, evidence-backed and human-approved. Retries cannot quietly drift from the plan.
- The lead's judgment varies by model. In a live check on the same checkpoint, Claude Sonnet proposed superseding
  the ADR while Codex chose guidance within it. Both are valid, so the human approval step matters.
- Work already merged on succeeded tasks is not revisited when an ADR is superseded. The lead's final review sees
  the whole diff, and rework of succeeded tasks is future work.
- A worker can still choose not to escalate. Verification and the lead's final review remain the backstop.
