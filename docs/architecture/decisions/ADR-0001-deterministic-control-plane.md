# ADR-0001: Deterministic control plane; models only propose

- Status: Accepted
- Date: 2026-10-18

## Context

The brief requires that models plan and decide, while only code authorizes providers, spends money, starts processes,
writes durable state, changes permissions or merges work (§3 boundary rule). Agent frameworks blur that line
by letting the model choose tools at run time.

## Decision

- Saarathi's core is a plain Python control plane with a hexagonal layout. Domain contracts (`saarathi.core`) do not depend
  on any provider SDK.
- Model output enters the system only as **validated JSON** (pydantic models generated from fixed JSON Schemas).
  It is then checked against policy (allowed paths, command policy, dependency DAG validity) before it becomes state.
- The **lead** role (default: a Claude Opus-class model) runs at intake/clarification, planning and final review. The
  **operations** role in v0.1 is deterministic code: scheduling, retries with failure feedback, escalation thresholds and
  checkpoint summaries. Delegating routine triage to a cheaper model is a Phase 2 extension behind the same
  `OperationsAgent` interface. This keeps the slice honest: no model call happens where a rule is enough.
- No agent framework is used in the core loop. `asyncio` gives bounded concurrency.

## Consequences

- Every decision that matters (routing, approval, state transition) is reproducible from the event log.
- Model-backed operations triage is not yet available. Failure handling is rule-based in v0.1.
