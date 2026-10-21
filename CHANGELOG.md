# Changelog

All notable changes are documented here. The format follows Keep a Changelog. Versions follow SemVer once
1.0 is released.

## [Unreleased]

## [0.1.0] - 2026-10-21

First release, with installers for macOS (Apple silicon), Windows and Linux. The builds are not yet signed: see
[docs/release/installing.md](docs/release/installing.md) for the one-time first-launch steps.

### Added
- Phase 0: feasibility report, provider compatibility matrix, ADR-0001 to ADR-0007, risk register, backlog.
- Deterministic control plane: run and task state machines, SQLite store with an atomic event log and
  idempotency keys, crash-safe engine lock and orphan reconciliation.
- Provider adapters: Codex CLI (verified live on 0.162.0-alpha.2), Claude Code (verified live on 2.1.302),
  test-only fake adapter.
- Explainable router with hard filters, capacity handling and weighted scoring; pinned engineering lead.
- Git worktree isolation, diff-scope enforcement, checkout-integrity check, integration merges.
- Command policy and Saarathi-run verification with evidence; Markdown/JSON reports.
- CLI: init, doctor (with `--live`), models list, policy validate, run, resume, answer, approve, status,
  tasks, logs, pause, cancel, review, report, route explain.
- Example project `examples/health-widget` and demo script; live Claude-led two-provider run recorded.
- Integration- and identity-aware routing (brief §12A, ADR-0008): workspace-scoped account/connection registry
  (schema migration 2), `IntegrationDiscoveryAdapter` contract, `IntegrationRequirementMatcher` hard filters,
  cross-provider transfer policy, single-use side-effect approvals, a recheck before dispatch, and
  `saarathi integrations list|accounts|scan|register|verify|revoke|alias|default|approve|select`.
  Discovery reports `unverified` for every supported CLI.
- The lead's strict plan schema carries per-task `integrations` (one account per requirement; nullable
  account/tenant/resource), and plan checks reject a task that uses two accounts of one service. Verified live on
  Claude Code and Codex CLI (`docs/phase0/evidence/live-plan-schema-integrations.md`).

- Cross-task data handoff (P2b, ADR-0009): workers return named `outputs`; dependent tasks receive only the
  fields policy permits (`share_fields`, approved cross-provider transfers), redacted and size-capped, with
  field names audited in `handoff.prepared` events. Integration data cannot be passed on through an intermediate task.

- Provider limits (Scenario D): quota exhaustion is told apart from transient rate limits; a limited adapter
  cools down (escalating 30 s / 2 min / 5 min, then "exhausted" for an hour, all configurable) and routing moves
  work to other eligible workers. Short cooldowns are waited out; exhaustion pauses the run with diagnostics.
  Limit retries are bounded per task and do not use the failure budget. New `saarathi providers status|reset`;
  `models list` and `doctor` show active limits.

- Escalation protocol (Scenario E, ADR-0010): workers stop and escalate conflicts with accepted decisions; the
  task and its dependents pause while independent work continues; an evidence-backed checkpoint goes to the
  lead, whose `retry_with_guidance` applies automatically while `supersede_adr` waits for `saarathi approve`
  before any ADR changes. Bounded per task. New `saarathi escalations [reject ID]`.

- v0.1 scope is subscription-only (ADR-0011): `policy validate` rejects API and hybrid modes and enabled API
  billing.

- Packaging: version single-sourced from `saarathi.__init__`; source distribution limited to code, tests, docs
  and examples; CI builds the wheel and smoke-tests it in a clean environment.
- Lessons (P7): workers' memory proposals from verified attempts are recorded as proposals; `saarathi lessons
  approve|reject` decides, approved lessons go to `.saarathi/lessons.md` and into later tasks' context.

- Antigravity CLI adapter (`agy` 1.4.2, provider `google`): structured output via `--json-schema`, edits with
  `--mode accept-edits`, read-only by headless default, real model discovery through `agy models`, a pinned
  model recorded as the model used, and one bounded retry when a denied tool discards the result. Included in
  the default policy and a three-provider example policy.

- Windows support (ADR-0012): portable process-tree termination and liveness checks, `msvcrt` engine lock,
  Windows environment allowlist, LF memory files; a Windows CI job runs the full suite.

- Saarathi Desktop foundation (ADR-0013): `saarathi bridge --stdio` (JSON-RPC protocol 1) with conversation
  sessions (migration 5), detached run drivers, live event notifications, stop/cancel/resume, idempotent sends
  and approvals; a Tauri 2 + React app over it with projects, sessions, the conversation and run controls, and an
  execution panel. CI builds and tests the desktop app on Linux, macOS and Windows.
- Desktop M3, orchestration visibility: a task inspector (objective, criteria, paths, attempts with Saarathi's
  verification evidence, changed files, usage as reported, every routing decision with scores and filtered-out
  candidates), a run timeline (task graph by dependency depth and attempt spans), side-by-side Monaco diffs of a
  task's change and an xterm.js log viewer. New read-only bridge methods: `tasks.inspect`, `tasks.file` (only paths
  in the task's own diff) and `runs.timeline`; `tasks.logs` re-redacts on read. Monaco and xterm.js are bundled
  locally (no network under the CSP).
- Approval presets and model preferences (ADR-0014): `ask`, `auto_safe` and `bypass`, capped by the new policy field
  `approvals.max_preset` (default `auto_safe`); escalations, integration approvals, transfers and lessons are never
  automatic. Workers can be preferred or pinned per run, after the routing hard filters. CLI: `saarathi run
  --approvals --prefer-model --pin-model` and `saarathi usage`. Lead calls record `lead.usage` events.
- Desktop M4: per-session approval preset and worker model controls, a permissions page (policy ceiling, what is never
  automatic, each worker's containment and its exact command line), a usage page (provider-reported tokens and costs
  per model and run, active provider limits) and settings (theme, defaults for new sessions,
  provider check, engine details). New bridge methods: `sessions.set_preferences`, `policy.get`, `usage.summary`. The
  bridge reloads a project's policy when `policies.yaml` changes.
- Live check of the desktop app with Claude Code and Codex CLI (`docs/phase0/evidence/live-desktop-claude-codex.md`).
- Model discovery: Codex CLI models from `codex debug models` (picker-visible only) and Antigravity models from
  `agy models` are listed after the declared models, labelled as discovered with an unknown reasoning tier. A
  declaration in the policy always wins, any discovery failure falls back to the declared models with a notice, and
  `options.discover_models: false` turns it off per adapter.
- Model tiers and Claude aliases: discovered models get a reasoning tier from a model catalogue built from vendor
  documentation (OpenAI Astra/Sol/Terra/Luna, Gemini Pro/Flash, Claude Opus/Sonnet/Haiku), or from Codex's own model
  descriptions; unmatched models stay "unknown". Claude Code lists its aliases (`opus`, `sonnet`, `haiku`, `fable`);
  `haiku` is in the default policy, and Fable is shown as unavailable because subscriptions charge usage credits for
  it. The Claude output parser now finds the result object even if text is printed after it. With known tiers the
  router can choose discovered models on its own, within `routing.allowed_models`; restrict them with
  `routing.denied_models` or turn discovery off per adapter with `options.discover_models: false`.
- Remaining plan usage from each CLI: `claude -p /usage`, Codex's app-server `account/rateLimits/read` and
  `agy -p /usage` (no model call, no credentials read, account ids dropped). Shown as meters on the Usage page and by
  `saarathi limits`; the bridge reads them on a background thread. This corrects the earlier statement that no
  supported interface reports quotas.
- Turn models off: `saarathi models disable/enable` and a Models panel in Settings. A turned-off model is never
  routed to, preferred, pinned or used as lead, in any project. Installed CLIs a project's policy leaves out are flagged
  with the steps to add them.
- Desktop design refresh: higher-contrast light and dark themes, the system font, frosted sidebar and headers, and
  spring animations (page and tab transitions, the inspector sliding in, sliding selection indicators, animated
  meters). Settings → Appearance adds theme, six accent colours, interface size, motion (system, full, reduced) and the
  execution panel toggle.
- Routing avoids providers that are nearly out of plan usage: `routing.min_remaining_percent` (default 5, 0 turns it
  off). Drivers read each CLI's limits on a background thread (refreshed every ten minutes; the first dispatch waits at
  most 15 seconds) and treat a model below the threshold like a cooldown until its window resets. Antigravity's
  per-group windows apply only to their own model families. A `provider.near_limit` event explains every skip.
- Desktop redesign from the UI/UX Pro Max skill (`desktop/DESIGN.md`): Minimalism & Swiss with AI-native touches, dark
  first with an indigo accent, system font plus bundled JetBrains Mono. An animated launch screen and mark, shimmer
  skeletons for every async view, a live working row with the engine's real phase, a run progress bar, busy buttons,
  count-up figures, toasts for every confirmation and error, a ⌘K command palette, ⌘, for Settings, starter prompts in
  empty sessions and an error boundary. Shared motion tokens: springs in, faster exits, 40 ms staggers, reduced
  motion respected.
- Claude's "You've hit your session limit" and "requires usage credits" are classified as plan limits, and a lead that
  hits its plan limit pauses the run with who is limited and what to do (resume after the reset, or set
  `orchestration.lead_substitute`).
- Policy editing without the file: the desktop Policy page (formerly Permissions) edits the engineering lead and its
  substitute, the approval ceiling, providers (enable, allow, add an installed CLI), routing, worker permissions,
  attempt and time limits, and integration rules. Every change is previewed, warnings list anything that loosens what
  agents may do (confirmation required), the engine validates the result before saving, the old file is backed up to
  `.saarathi/policy-backups/`, comments in `policies.yaml` are kept, and a `policy.updated` event records it. CLI:
  `saarathi policy set KEY VALUE`. Bridge: `policy.update` (dry run by default).
- Desktop: Enter sends a request and Shift+Enter adds a line; a first-launch guided tour (replay from Settings or ⌘K);
  the session controls show a loading state while the provider CLIs answer.
- Desktop M5, packaging: the engine is bundled with the app (a PyInstaller sidecar built by
  `scripts/build_engine.py`), so no Python or terminal setup is needed; the app prefers it over a `saarathi` on the
  PATH (`SAARATHI_BRIDGE` still overrides). Installers: macOS .dmg (ad-hoc signed, hardened runtime), Windows NSIS
  .exe and .msi, Linux .deb and .AppImage, built by the new Release workflow (manual run, or a tag for a draft
  release). Signing and notarization switch on when the secrets exist (`docs/release/signing.md`). Provider CLIs found
  in per-shell directories (fnm) resolve to their real installation, Node-based CLIs get their own directory on PATH,
  and child processes never open console windows on Windows. Install guide: `docs/release/installing.md`.
- Fixed: on a machine booted less than ten minutes earlier, the first plan-limits read was skipped because "never
  read" was stored as monotonic time 0.

### Fixed
- Desktop: cancelling a run on Windows timed out, because a detached driver cannot receive CTRL_BREAK. Drivers now
  poll a stored interrupt request, and POSIX still also gets SIGINT.
- Desktop: a session with several runs showed the alphabetically last run as current, because JSON object key order
  does not survive the Rust shell. `sessions.get` now returns an explicit `run_order`.
- The command policy now recognises versioned and Windows program names (`python3.12 -c`, `Python.EXE -c`,
  `cmd /c`, PowerShell `-Command`) and drive-letter paths.
- Unblocking a task on resume no longer makes it READY before its dependencies have succeeded.
- Dependent tasks now start from their dependencies' verified commits instead of the bare run base; conflicting
  dependencies block the task with a checkpoint (ADR-0009).
- Two checkpoints written for one run within the same second no longer collide.
- Build artifacts from workers' test runs (`__pycache__`, caches) no longer fail scope checks
  (`execution.ignored_artifacts`).
