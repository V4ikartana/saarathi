# Saarathi

**Many agents. One direction.**

Saarathi is an open-source, local-first orchestrator that coordinates AI coding agents (Claude Code,
Codex CLI and others) to turn a project brief into a verified, reviewable Git branch.

> **Project status: pre-alpha (v0.1 vertical slice).** A Claude-led run with Claude Code and Codex CLI workers
> has been demonstrated live from brief to verified integration branch on macOS. See [Known limitations](#known-limitations).

## What it does

You subscribe to several AI coding tools, but they work in silos. Saarathi gives them one shared plan
and one control plane:

- An **engineering lead** model (by default a Claude Opus-class model, pinned in policy) asks material
  clarification questions, writes the plan and reviews the integrated result.
- A **deterministic control plane** routes each task to an eligible worker, runs it in its own Git worktree,
  checks the diff scope, runs the verification commands itself and merges passing work onto an
  integration branch. It never merges into your branches.
- **Durable state** (SQLite and an append-only event log) lets a run survive crashes and restarts without
  re-running finished work.
- **Every decision is inspectable:** `saarathi route explain T-001` shows why a model was chosen and why
  others were rejected.

## Compatibility and prerequisites

- macOS, Linux or Windows, Python 3.12+, Git 2.40+. The test suite runs on all three in CI; provider CLIs have been
  run live on macOS only. On Windows, enable `git config --global core.longpaths true`.
- At least one supported, logged-in provider CLI:

| Provider CLI | Adapter status | Evidence |
|---|---|---|
| Codex CLI (`codex exec`) | **Verified live** on codex-cli 0.162.0-alpha.2 and alpha.17.2 (macOS) | [feasibility report](docs/phase0/feasibility-report.md) |
| Claude Code (`claude -p`) ≥ 2.1.259 | **Verified live** on 2.1.302 (macOS, subscription login) | [compatibility matrix](docs/providers/compatibility-matrix.md) |
| Antigravity CLI (`agy -p`) ≥ 1.4 | **Verified live** on 1.4.2 (macOS, Google account login); works best as a worker | [compatibility matrix](docs/providers/compatibility-matrix.md) |
| Anthropic / OpenAI / Google APIs | Not implemented (planned, with budgets) | |

## Quick start

```bash
git clone <repository-url> saarathi && cd saarathi
uv sync
source .venv/bin/activate

cd /path/to/your/git/repo
saarathi init              # writes .saarathi/ with a conservative default policy
saarathi doctor            # checks installed CLIs, versions, login and the pinned lead
saarathi doctor --live     # optional: one tiny real completion per adapter (uses quota)
saarathi run brief.md      # intake → questions → plan → approval → execution → review
```

Try it on the bundled example. It copies a starter project into a temporary Git repository:

```bash
examples/health-widget/demo.sh claude-codex     # Claude Code leads, Claude Code + Codex work
examples/health-widget/demo.sh codex-only       # hosts without Claude Code
```

## Usage

| Command | Purpose |
|---|---|
| `saarathi run BRIEF [--non-interactive] [--accept-defaults]` | Start a run |
| `saarathi run BRIEF --approvals auto_safe` / `--prefer-model A:M` / `--pin-model A:M` | Delegate plan and review approval within `approvals.max_preset`; prefer or pin a worker model (ADR-0014) |
| `saarathi usage [--json]` | Tokens and costs per model and run, as providers reported them |
| `saarathi status` / `tasks` | Run and task state (`--json` available) |
| `saarathi answer Q1 "..."` | Answer a clarification question |
| `saarathi approve` | Approve the plan, an escalation's ADR change, or the final review |
| `saarathi escalations` / `escalations reject ID` | Worker escalations, the lead's verdicts and checkpoints |
| `saarathi lessons` / `lessons approve ID [--text ...]` / `lessons reject ID` | Lessons proposed by verified work; only approved ones are kept and reused |
| `saarathi resume` | Continue after a pause, crash or answer |
| `saarathi pause` / `cancel` | Stop dispatching / cancel a run |
| `saarathi review` / `report [--json]` | Evidence, lead verdict, diff summary, final report |
| `saarathi logs [--task T-001]` | Redacted event log |
| `saarathi route explain T-001` | Why each model was (or was not) chosen, including integration checks |
| `saarathi integrations list` / `accounts` / `scan` | Registered connections, accounts (masked) and discovery status |
| `saarathi integrations register` / `verify` / `revoke` | Declare a connection, confirm it, invalidate it |
| `saarathi integrations alias` / `default` | Workspace-scoped account aliases and defaults |
| `saarathi integrations approve` / `select` | Grant a pending approval or choose an account for one task |
| `saarathi models list` / `policy validate` | Model catalogue (with active provider limits) and policy checks |
| `saarathi providers status` / `reset ADAPTER` | Provider cooldowns after rate-limit or quota errors; clear one early |

Exit codes: `0` done · `1` error · `2` usage/config · `3` waiting for you (question, approval, review or pause) ·
`4` failed · `5` policy blocks progress · `6` another engine holds the project lock.

## Desktop app (preview)

[`desktop/`](desktop/) holds Saarathi Desktop (Tauri 2 + React), a control layer over the same engine. It lets you
work through conversation sessions, watch live run events, approve plans and reviews, stop, cancel or resume runs, and
inspect every task: attempts, verification evidence, routing decisions, diffs, logs and a run timeline. Sessions can
prefer or pin a worker model and choose an approval preset within the policy, and the app shows usage and the effective
permissions of every worker.
It talks to the engine only through `saarathi bridge --stdio` (ADR-0013), and runs keep going when the window closes.
It is a v0.1 foundation: no installers yet. See [desktop/README.md](desktop/README.md).

## Configuration

`saarathi init` writes `.saarathi/policies.yaml`. The defaults:

- `mode: subscription` with `billing.api_enabled: false`: metered usage (including CLIs logged in with
  an API key) is **blocked**.
- `permissions`: no network, no dependency installation, no deployment.
- `orchestration.lead`: `claude-code:opus`. If it is unavailable, the run **pauses**; it is never silently replaced.
  Set `lead_substitute` to opt in to a named fallback.
- Models are **declared** per adapter with your own `reasoning_tier`. Saarathi does not infer model IDs or
  entitlements from product names.
- `integrations`: only verified or user-confirmed connections route; unverified identities fail closed;
  user-declared identities, cross-provider data transfer and external side effects (`mail.send`, `*.delete`, ...)
  need explicit approval. Accounts, aliases and defaults are scoped to `integrations.workspace`.

## Security and privacy

- No telemetry. All state stays in `.saarathi/` and your user data directory.
- Saarathi never reads provider credentials. Each CLI uses its own login.
- Workers get an allowlisted environment. Host API keys are not passed through unless you list them.
- **Verification commands run repository code with your permissions and are not sandboxed.** A Git worktree is
  not a security boundary. Read the [threat model](docs/security/threat-model.md) before running untrusted repositories.
- Subscription use: provider terms describe subscription logins as intended for ordinary individual use. See
  [ADR-0006](docs/architecture/decisions/ADR-0006-subscription-cli-use.md). Prefer API keys for heavy automated use.

## Known limitations

- Live verification so far covers one macOS host; Linux has offline tests only.
- Operational orchestration is rule-based: retries with feedback, and escalations that a worker raises are sent to
  the lead, with ADR changes gated on your approval (ADR-0010). Model-backed operations summaries are planned.
  Succeeded tasks are not reworked when an ADR is superseded.
- Provider limits are recognized from error text (no stable codes); real rate-limit and quota messages have not
  yet been captured from a live run, so classification may miss a provider's wording.
- Subscription CLIs only (ADR-0011): API and hybrid modes, API adapters and the budget ledger are deferred.
  A CLI signed in with an API key is treated as metered and never used.
- Integration/account-aware routing (brief §12A) works on what you **register**, not on what Saarathi detects:
  Claude Code and Codex CLI expose no documented integration inventory, so `integrations scan` reports `unverified`
  and `integrations verify` can only record your confirmation, never provider verification. The lead's plan schema
  carries integration requirements (checked live on both CLIs; real mail access is not). Results handed between
  tasks are filtered field by field (`share_fields`, `integrations.shareable_fields`), but Saarathi cannot prove a
  worker's reported value came from the account it names. Real Gmail/Outlook use is untested. See
  [ADR-0008](docs/architecture/decisions/ADR-0008-integration-and-identity-aware-routing.md) and
  [ADR-0009](docs/architecture/decisions/ADR-0009-cross-task-handoff.md).
- A failed task stays failed for that run. Recovery from escalations means a new run.
- Windows support is verified by the offline test suite only (ADR-0012). No provider CLI has been run live on Windows.

## Architecture

See the [architecture overview](docs/architecture/overview.md), the [ADRs](docs/architecture/decisions/),
the [risk register](docs/phase0/risk-register.md) and the [backlog](docs/phase0/backlog.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the checks with:

```bash
uv run pytest && uv run ruff check src tests && uv run mypy
```

## License

Apache-2.0. See [LICENSE](LICENSE).
