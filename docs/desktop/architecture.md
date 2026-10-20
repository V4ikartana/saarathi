# Saarathi Desktop: architecture, contract and plan

Status: Phase 1 deliverable (repository audit and architecture), 2026-10-19.

The desktop app is a presentation and control layer. The Python engine stays the only place where routing,
approvals, state transitions, Git changes and provider calls are decided. The desktop app never runs a second engine,
keeps no second database and never decides eligibility itself.

## 1. Audit: what the engine already provides

| Desktop need | Already in the engine | Gap |
|---|---|---|
| Durable state | SQLite: `runs`, `tasks`, `attempts`, `routing_decisions`, `events`, `questions`, `escalations`, `lesson_proposals`, `provider_health`, integration tables | No **conversation sessions** or messages |
| Live progress | Append-only `events` table with a monotonic `seq` (31 event types) | No push channel. The bridge tails `seq` (below) |
| Run lifecycle | `Engine.start/drive/resume/approve_plan/approve_review/approve_escalation`; `pause` control flag; `cancel` | `cancel` refuses while a run is being driven. Stop and cancel semantics must be defined for the UI |
| Background work | One engine per project behind an OS lock; crash recovery reconciles orphaned attempts | The driver must outlive the window, so it runs in its own process (below) |
| Structured output | `--json` for status, tasks, models, doctor, report, route explain, escalations, lessons, providers | Human-readable output is not a contract: the bridge calls library functions, not the CLI |
| Approvals | Plan, final review, escalation ADR changes, integration grants and selections, lessons | No **approval presets**: plan and review approval are always manual |
| Model choice | Router with hard filters and a pinned lead | No **per-session provider or model preference or pin** |
| Diffs | Task branches and an integration branch in worktrees; `GitRepo.diff` | No per-task diff API |
| Usage | Per-attempt provider-reported tokens; Claude's client-side cost estimate; provider cooldowns | Subscription quotas and reset times are not available from any supported interface, so they are shown as unknown |
| Token streaming | Adapters collect each CLI's final structured result | No token-level streaming. The UI streams **events**, not model text |
| Reasoning | None captured | Nothing to show. The UI will not invent reasoning |

**Constraint carried into the UI:** a project has at most one active run at a time (engine lock plus
`active_run`). Several sessions can exist per project, but only one can be executing in that project at once. The UI
shows the others as idle or waiting.

## 2. Architecture

```text
React UI (TypeScript, Zustand, Tailwind, shadcn/ui, Monaco, xterm.js)
   │  invoke() / listen()                      Tauri IPC (in-process, no network)
Tauri 2 shell (Rust, ~200 lines)
   │  stdin/stdout, newline-delimited JSON-RPC 2.0
saarathi bridge --stdio  (Python, part of the saarathi package)
   │  library calls                            spawns detached drivers
Engine / StateStore / GitRepo / ProviderRegistry   ──►   saarathi resume --run R --non-interactive
   │                                                          (one per active run, survives the window)
.saarathi/state.db  (WAL; the bridge reads it while a driver writes)
```

- **No network listener.** The UI talks to Rust over Tauri IPC. Rust talks to the bridge over stdio pipes, and the
  bridge talks to the engine in-process.
- **Drivers are separate processes.** The bridge never drives a run itself. It starts `saarathi resume` as a detached
  process (its own session or process group). Closing the window or a bridge crash therefore does not stop execution,
  and the engine lock prevents a second driver. On restart the bridge reattaches by reading state and checking the
  lock-holder pid.
- **Live updates.** The bridge polls each subscribed project's `events` table by `seq` (200 ms, indexed) and pushes
  `events/appended` notifications with the new events and a fresh status snapshot. No new streaming infrastructure is
  needed, and a broken stream is repaired by resubscribing from the last seen `seq`.

## 3. Conversation sessions (backend addition)

Migration 5 adds two tables to the existing `state.db`. There is no new database.

- `sessions(id, project_id, title, status active|archived, preferences_json, created_at, updated_at)`
- `session_items(id, session_id, seq, kind, role, body_json, run_id, created_at)`

A session is a conversation. A **run** is one execution of a request inside it, and an **attempt** is one worker try
at one task. The UI keeps these three apart.

- A user message starts a run, with the message as its brief, if the project has no active run. Otherwise the bridge
  refuses with `project_busy`, and the UI says which session is executing.
- Messages answer the run's open questions when the run is `CLARIFYING`.
- "Orchestrator responses" are **derived from real events**: intake questions, the proposed plan, task progress,
  escalations, the lead's review and pause reasons. The bridge renders them from the run and event records. Nothing is
  generated by the UI.

## 4. Integration contract (protocol 1)

JSON-RPC 2.0, one JSON object per line on stdout. stderr is for logs only. Every response carries
`protocol: 1`. Breaking changes bump the protocol, and `system.initialize` refuses incompatible clients.

| Method | Purpose |
|---|---|
| `system.initialize {client, protocol}` | Handshake: versions, platform, capabilities |
| `system.doctor {project}` | Provider probes (the same data as `saarathi doctor --json`) |
| `projects.open {path}` | Validate a Git repo; report whether it is initialized, plus its policy and lead |
| `projects.init {path}` | `saarathi init` equivalent |
| `sessions.list {project, include_archived}` / `sessions.create {project, title}` / `sessions.rename` / `sessions.archive` / `sessions.search {project, query}` | Session management |
| `sessions.get {session}` | Items, linked runs, current run snapshot |
| `sessions.send {session, text, idempotency_key}` | Start a run with this brief, or answer open questions |
| `runs.status {run}` / `runs.tasks {run}` / `runs.events {run, since_seq}` | Snapshots |
| `runs.approve {run}` | Approve the plan, a pending escalation or the final review (engine decides which) |
| `runs.answer {run, question, text}` | Clarification answer |
| `runs.stop {run}` | Graceful: stop dispatching; in-flight attempts finish, then the run pauses |
| `runs.cancel {run}` | Interrupt the driver (SIGINT / CTRL_BREAK), wait for it to pause, then mark the run `CANCELLED` |
| `runs.resume {run}` | Start a driver for a paused or crashed run |
| `tasks.diff {run, task}` / `tasks.logs {run, task}` | Git diff of a task branch against its base; redacted logs |
| `models.list {project}` / `providers.status {project}` | Catalogue, availability, cooldowns |
| `subscribe {project}` / `unsubscribe` | Start or stop `events/appended` notifications |

Errors use JSON-RPC codes plus `data.kind`: `invalid_params`, `not_found`, `project_busy`, `invalid_state`,
`policy`, `locked`, `internal`. Mutating methods take an `idempotency_key`, and a retried request with the same key
returns the first result, so a double click or replay never starts two runs.

**Window, session switch and cancel are different.**

- *Closing the window:* the UI and bridge exit. Drivers keep running, and the next launch reattaches.
- *Switching sessions:* only changes what is displayed.
- *Stop:* graceful pause.
- *Cancel:* interrupt and cancel. Worktrees and branches are kept, as in the CLI.

## 5. Later backend additions (Phases 3–4)

- **Approval presets** mapped to a new policy section (`approvals.plan` and `approvals.review`: manual | auto):
  - *Ask before actions:* both manual.
  - *Auto-approve safe actions:* auto-approve a plan only after it passes policy checks; review stays manual.
  - *Bypass supported approvals:* plan and review auto.

  Never automatic in any preset: escalation ADR changes, external side effects, cross-provider data transfer,
  identity approvals and lesson promotion. Provider permission modes are not loosened by any preset.
- **Provider and model preferences:** a per-session `preferred` candidate (a score bonus) and `pinned` candidate (a
  filter to that candidate), both applied after hard filters, so a pin can never make an ineligible model eligible.
- `tasks.diff` and `tasks.logs` already have their data on disk and only need bridge methods.

## 6. Milestones

Status (2026-10-20): **M1 done** (bridge with end-to-end tests). **M2 done**: verified by driving a full run through the
real app on macOS (open repo → session → request → plan approval → execution → review → completion → restart
restores the session). Windows and Linux are compile- and test-checked in CI only.

| # | Milestone | Validated by |
|---|---|---|
| M1 | Bridge v1: protocol, sessions (migration 5), runs start/approve/answer/stop/cancel/resume, detached drivers, event subscription | pytest: lifecycle, idempotency, cancellation, crash and reattach, protocol errors |
| M2 | Tauri + React shell: project picker, session sidebar, conversation view streaming real events, approve and answer controls | Launch, run the fake-provider demo through the UI, restart and see the session restored |
| M3 | Execution panel: tasks, DAG timeline, attempts, verification, routing explanations, logs, Monaco diffs | Real run data only |
| M4 | Provider and model controls, approval presets (backend policy), permission review screen, usage screen | Backend tests that presets never bypass hard approvals |
| M5 | Hardening and packaging on Windows, macOS and Linux; bridge discovery or a bundled sidecar; install docs | CI build matrix; manual smoke test per OS |

## 7. Blockers and risks

1. ~~Rust toolchain missing~~: resolved. Stable Rust 1.99.1 was installed with rustup on 2026-10-19.
2. **Shipping Python with the app** (M5). v0.1 can require an installed `saarathi` (`uv tool install`). A self-contained
   installer needs a bundled Python sidecar (PyInstaller or similar), plus code signing and notarization on macOS
   and Windows, which need developer certificates the owner would have to obtain.
3. **No token streaming.** Provider adapters return final results, so the conversation streams progress events, not
   text. Adding token streaming would change the adapters and is out of scope for v0.1.
4. **One active run per project.** This is an engine invariant, surfaced in the UI instead of being worked around.
5. **Subscription quotas.** No supported interface reports remaining quota or reset times, so they are shown as unknown.
