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

- macOS or Linux (Windows is not supported yet), Python 3.12+, Git 2.40+.
- At least one supported, logged-in provider CLI:

| Provider CLI | Adapter status | Evidence |
|---|---|---|
| Codex CLI (`codex exec`) | **Verified live** on codex-cli 0.162.0-alpha.2 and alpha.17.2 (macOS) | [feasibility report](docs/phase0/feasibility-report.md) |
| Claude Code (`claude -p`) ≥ 2.1.259 | **Verified live** on 2.1.302 (macOS, subscription login) | [compatibility matrix](docs/providers/compatibility-matrix.md) |
| Antigravity / Gemini CLI | Not implemented (planned) | |
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
| `saarathi status` / `tasks` | Run and task state (`--json` available) |
| `saarathi answer Q1 "..."` | Answer a clarification question |
| `saarathi approve` | Approve the plan, or the final review |
| `saarathi resume` | Continue after a pause, crash or answer |
| `saarathi pause` / `cancel` | Stop dispatching / cancel a run |
| `saarathi review` / `report [--json]` | Evidence, lead verdict, diff summary, final report |
| `saarathi logs [--task T-001]` | Redacted event log |
| `saarathi route explain T-001` | Why each model was (or was not) chosen |
| `saarathi models list` / `policy validate` | Model catalogue and policy checks |

Exit codes: `0` done · `1` error · `2` usage/config · `3` waiting for you (question, approval, review or pause) ·
`4` failed · `5` policy blocks progress · `6` another engine holds the project lock.

## Configuration

`saarathi init` writes `.saarathi/policies.yaml`. The defaults:

- `mode: subscription` with `billing.api_enabled: false`: metered usage (including CLIs logged in with
  an API key) is **blocked**.
- `permissions`: no network, no dependency installation, no deployment.
- `orchestration.lead`: `claude-code:opus`. If it is unavailable, the run **pauses**; it is never silently replaced.
  Set `lead_substitute` to opt in to a named fallback.
- Models are **declared** per adapter with your own `reasoning_tier`. Saarathi does not infer model IDs or
  entitlements from product names.

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
- Operational orchestration is rule-based (retries with feedback, escalation by pausing). Model-backed triage is planned.
- No API adapters, budget ledger or integration/account-aware routing (brief §12A) yet.
- A failed task stays failed for that run. Recovery from escalations means a new run.
- POSIX only (`fcntl` locking, process groups).

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
