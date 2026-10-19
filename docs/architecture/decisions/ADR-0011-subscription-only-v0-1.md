# ADR-0011: v0.1 ships subscription-only

- Status: Accepted
- Date: 2026-10-19
- Decided by: project owner

## Context

The brief describes three operating modes (subscription CLI, API and hybrid) and lists "subscription-only,
API-only and hybrid configurations pass their respective acceptance tests" as a Phase 2 gate. API mode needs
official API adapters, a concurrency-safe budget ledger, verified pricing and live testing with real spend
(Scenario C). The owner decided that the first version should focus on subscription CLIs.

## Decision

- v0.1 supports `mode: subscription` only. `policy validate` rejects `api` and `hybrid`, and rejects
  `billing.api_enabled` or `billing.allow_api_fallback` set to true, with a message that points here.
- Metered use stays blocked in code, not just by default. A provider CLI signed in with an API key is classified
  as metered (ADR-0006) and is never eligible, so it is never used as a fallback.
- Scenario C is reduced to what applies without API adapters: denied and unknown models never run, metered workers
  are never chosen, and no paid fallback happens. These are covered by router, policy and end-to-end tests.
- API adapters, the budget ledger and the API-only and hybrid acceptance tests move to a later release
  (backlog P3). The `Mode` enum and billing fields stay in the schema so that release does not need a format change.

## Consequences

- The v0.1 release gate is narrowed. The README and release checklist say so explicitly.
- Users who want API-key billing cannot use v0.1 for it. They can still run the CLIs themselves with API keys,
  but Saarathi will refuse to route to them.
