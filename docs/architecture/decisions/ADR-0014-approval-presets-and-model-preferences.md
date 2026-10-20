# ADR-0014: Approval presets and model preferences are run controls capped by policy

- Status: Accepted
- Date: 2026-10-20

## Context

The desktop brief asks for approval presets ("ask before actions", "auto-approve safe actions", "bypass supported
approvals") and per-session provider and model selection. Neither may become a second place where approvals or routing
are decided, and neither may loosen a hard approval or a hard routing filter.

## Decision

- A run carries its preferences as **run controls** (`pref.approvals`, `pref.model`, `pref.model_mode`), recorded
  when the run starts. The CLI (`saarathi run --approvals --prefer-model --pin-model`) and the desktop app (a session's
  stored preferences, applied to each new request) set them the same way. The engine reads them; no client decides.
- **Presets** apply only to the plan approval and the final review:
  - `ask`: a person approves both.
  - `auto_safe`: a plan starts on its own when every task is low or medium risk and no task uses integrations. The
    final review stays manual.
  - `bypass`: any plan that passed the plan policy checks starts, and the run completes when the lead approves and
    integration checks pass.
- The policy's `approvals.max_preset` (default `auto_safe`) caps every preset. A preset above the cap is refused when
  it is saved or a run starts, and a policy lowered during a run caps the running preset too.
- **Never automatic**, under every preset: escalation ADR changes, integration identity and account selections,
  external side effects, cross-provider data transfer, lesson promotion. Presets never change provider permission
  modes.
- Every automatic approval is an `approval.automatic` event (actor `policy`, with the preset and the reason), and a
  preset that declines is an `approval.required` event with its reason. Both appear in the conversation.
- **Model preferences** act after the routing hard filters. *Prefer* adds a score term larger than all other terms
  combined, so an eligible, free preferred model wins; it is waived for a model that already failed the task. *Pin*
  rejects every other candidate; if the pinned model is ineligible, the task waits with the pinned model's own
  reasons. Neither can make an ineligible model eligible. The lead stays pinned by the policy.

## Consequences

- Delegation is visible and auditable in the event log, and owned by whoever controls `policies.yaml`.
- A pinned model that becomes unavailable blocks its tasks instead of silently moving work elsewhere.
- Lead calls now record their provider-reported usage as `lead.usage` events, so usage views cover the lead as well
  as workers (runs started before this change have no lead usage records).
