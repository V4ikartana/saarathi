# ADR-0005: Explainable two-stage routing

- Status: Accepted
- Date: 2026-10-18

## Decision

Routing is a pure function `route(task, candidates, policy, runtime_state) -> RoutingDecision`.

1. **Hard filters** (any failure makes a candidate ineligible, with a recorded reason): provider allowed; `provider/model` matches
   `allowed_models` and no `denied_models`; adapter probe healthy; every required capability **declared true** (unknown
   counts as false); metered transport is rejected unless `billing.api_enabled`; data class permitted.
2. **Capacity:** eligible candidates at their `max_parallel` are *busy*. If every eligible candidate is busy, the task waits.
   It is never sent to an ineligible candidate.
3. **Scoring** (weights configurable): fit between task complexity and model `reasoning_tier`, known cost (unknown metered pricing
   is penalized), success history from previous attempts, current load and a spread bonus for providers not already busy in
   the run. Unknown values score neutrally or worse, never better.
4. The decision records the chosen candidate, a per-term score breakdown, rejected candidates with reasons and the alternatives.
   It is persisted and shown by `saarathi route explain <task>`.
5. Retries re-route. A failed candidate is excluded from the next attempt when an alternative exists, but the hard filters still apply.

The lead model is **pinned** by policy (`orchestration.lead`). If it is unavailable, Saarathi uses `orchestration.lead_substitute` only
when that is explicitly configured. Otherwise it pauses.

## Consequences

- Routing is a heuristic, not a quality claim. Learned routing is Phase 3.
- Integration and account-aware filters (§12A) are additional hard filters before scoring; see ADR-0008.
