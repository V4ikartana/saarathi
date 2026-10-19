# ADR-0008: Integration- and identity-aware routing

- Status: Accepted
- Date: 2026-10-19

## Context

Brief §12A: a user may connect Gmail to Claude Code and Outlook to Codex, or one Gmail account to Claude and a different
Gmail account to Codex. A model's general ability, or a provider's API support, says nothing about which **account**
a given worker can reach or which **operations** that connection allows. Routing must resolve
service → exact account → eligible connection → required operation → authorized worker before scoring, and must
never substitute one account for another.

As of 2026-10-19, neither Claude Code nor Codex CLI documents a non-interactive way to list connected integrations,
the accounts behind them, or their granted scopes. Discovery is therefore not possible without scraping sessions or
reading undocumented state, which Saarathi does not do (ADR-0004, ADR-0006).

## Decision

**Registry.** Migration 2 adds `worker_instance`, `integration_account`, `integration_connection`,
`integration_capability`, `account_alias`, `account_default` and `task_requirement`. Every record except
`task_requirement` (which is run-scoped) carries a `workspace_id`. Reads go through `IntegrationRegistry.snapshot(workspace)`,
so no query can see another workspace's accounts, aliases or defaults. An adapter runs one authenticated profile, so at
most one active worker per adapter per workspace. Account identities are stored as a workspace-salted SHA-256 digest
plus a masked hint (`b***s@corp.example`); the plain identifier is never stored or logged. No secrets are stored.

**Evidence, not assertion.** Capabilities record how they are known: `declared` (registered by hand), `advertised`,
`discovered`, `probed` or `user_confirmed`. With `integrations.require_verified_integration: true` (default) only the
last three count. `saarathi integrations verify --confirm` records `user_confirmed`, and every surface labels it as user
confirmation, not provider verification. Account identity is separately `verified`, `user_declared`, `unverified`,
`stale` or `revoked`. For identity-sensitive requirements (the default), `unverified`, `stale` and `revoked` fail closed,
and `user_declared` follows `integrations.user_declared_identity` (`require_approval` by default).

**Discovery contract.** `IntegrationDiscoveryAdapter.scan` / `.probe`. The only implementation today,
`NoDocumentedInventory`, reports `unverified` with no connections and `unavailable` probes for every runtime.

**Matching.** Tasks carry `integrations: [IntegrationRequirement]` (service, operation, at most one account selector,
optional tenant/resource, `identity_sensitive`, `consent_required`, `share_fields`). `IntegrationRequirementMatcher` is
pure and service-agnostic. For each requirement it:

1. Resolves the account: an explicit selector (id, then scoped alias, then identity digest) wins and is never
   substituted. Without one: a per-task user selection, else the workspace's default, else the single accessible
   account. Several accessible accounts and no default → the task waits for selection (`awaiting: account_selection`).
   Price and ordering are never used.
2. Checks tenant and identity status.
3. Checks every connection to that account: active worker, `active` status, not expired, a capability for exactly that
   operation (and resource scope), accepted evidence, and freshness within `max_verification_age_hours`.
4. Classifies the operation by its last segment: read (`read`, `search`, ...), write (`draft`, `label`, ...) or
   external (`send`, `delete`, `share`, and any unknown verb). External effects and `consent_required` need an
   approval keyed to the task, requirement, operation and account (`external_side_effects: deny` refuses them).

The router applies the matcher as additional hard filters, before scoring. A candidate whose adapter has no
eligible connection for every requirement is rejected with the reason. Any task-level problem (unresolvable account,
identity, no eligible connection, pending approval or selection) means no candidate runs.

**Cross-provider data.** The engine passes each transitive dependency that read an account as a `DataSource` (service,
account, tenant, provider that read it, `share_fields`). For a candidate on a different provider,
`cross_provider_data_transfer` applies (`deny` / `require_approval` / `allow`), only fields listed in
`integrations.shareable_fields` may cross, and tenants in `no_transfer_tenants` never cross.

**Approvals and selections** are run-scoped control rows (`grant:<key>`, `select:<task>:<n>`), set by
`saarathi integrations approve|select` only for keys the router actually asked for, and audited in the event log.
Side-effect approvals are **single-use**: they are consumed when an attempt starts, so a retry, crash recovery or
resume after a failed send needs a new approval.

**Dispatch.** Immediately before a worker is invoked the engine re-evaluates the integration filters for the chosen
candidate. If the selected connection is no longer eligible, the task is blocked and the run pauses. It is not rerouted.

**Audit.** `RoutingDecision.integration` persists the per-requirement resolution, every connection check, eligible
connections per candidate, the selected connection(s), approvals required and granted, account choices and transfer
checks. `saarathi route explain` prints them.

## Consequences

- Nothing is detected automatically. Users register connections, then confirm them. This is slower, but nothing is
  claimed that has not been checked.
- The default policy fails closed: with no verified identity, an identity-sensitive task waits for an explicit
  `identity:<account>` approval in each run, or the user opts in with `user_declared_identity: allow`.
- The lead's strict `PLAN_SCHEMA` includes `integrations` (P2a). The strict subset has no optional properties, so
  every field is required and `account`, `tenant` and `resource` are nullable (`["string", "null"]`). The schema only
  guides the model: pydantic `PlannedTask` stays the trust boundary (selector patterns, lengths, single account), and
  the plan check rejects a task that names two accounts of the same service, so the lead is asked to split it. Both CLIs
  accept the schema in strict mode, as verified live on 2026-10-19 (Claude Code 2.1.302, codex-cli 0.162.0-alpha.17.2).
  Both split a two-account brief into one task per account (`docs/phase0/evidence/live-plan-schema-integrations.md`).
  This covers the plan contract only. Real mail access is still not verified.
- Workers in v0.1 are coding agents in worktrees. There is no cross-task data handoff yet (backlog P2b), so `share_fields` and the
  permitted-field list in each transfer check are recorded constraints for that handoff. They do not yet redact live
  content. The data-source check is conservative: any dependency's output is assumed to reach its dependents.
- Worker `max_parallel` and `health` are stored but not enforced separately from the model's own limits.
- Real Gmail/Outlook verification is out of scope until a runtime documents a safe inventory or probe interface and a
  test account is authorized (brief §12A release gate).
