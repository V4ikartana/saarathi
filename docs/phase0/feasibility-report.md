# Phase 0 feasibility report

Date: 2026-10-18 · Host: macOS 27.0 (arm64) · Python 3.14.6 / 3.12 available · git 2.55.0 · uv 0.11.26

This report records what was **observed on the development host** and what was **read in official
documentation**. Anything not in one of those two columns is unverified and is treated as unsupported.

## 1. Installed provider CLIs

| CLI | Found | Version | How it was found | Usable by Saarathi on this host |
|---|---|---|---|---|
| Codex CLI | Yes, not on `PATH` | `codex-cli 0.162.0-alpha.2` | Bundled at `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex` | **Yes**. Logged in with ChatGPT (`codex login status`) |
| Claude Code | No host binary | n/a | Only a Linux/arm64 build inside the Claude desktop app's VM directory (`exec format error` on macOS) | **No**. Needs a native install and login |
| Gemini CLI | No | n/a | `~/.gemini` exists (Antigravity data) but no `gemini` or `agy` binary | **No** |

Saarathi never reads `~/.codex/auth.json`, the macOS keychain or any other credential store. Only
documented status commands (`codex login status`, `claude auth status`) are used.

## 2. Live evidence: Codex CLI hello-world in isolated worktrees

Commands that were run (scratch directory, throwaway repository):

```bash
git init repo && git worktree add -b task-a ../wt-a <base> && git worktree add -b task-b ../wt-b <base>
codex exec --json --ephemeral --ignore-user-config -s workspace-write -C wt-a \
  --output-schema schema.json -o a-last.json "Create a file hello_a.txt ..." < /dev/null
```

Observed:

- Exit code 0 after 7.3 s wall time.
- JSONL events on stdout: `thread.started` → `turn.started` → `item.completed(agent_message)` →
  `item.started/completed(command_execution)` → `item.completed(agent_message)` → `turn.completed`.
- `turn.completed.usage` = `{input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens, reasoning_output_tokens}`.
- The `-o` file contained JSON conforming to the supplied `--output-schema`.
- Only `wt-a` changed. `wt-b` and the primary checkout stayed clean.
- **The event stream does not report which model ran.** Saarathi records the model as `unreported` unless it pins one with `-m`.
- Progress text such as `Reading additional input from stdin...` goes to stderr.

## 3. Documentation evidence (fetched 2026-10-18)

### Claude Code (code.claude.com/docs/en/headless, /cli-reference, /legal-and-compliance)

- Non-interactive: `claude -p`, `--output-format json|stream-json`, `--json-schema` (result in `structured_output`),
  `total_cost_usd` (client-side estimate), `session_id`, `--model` (aliases `opus`, `sonnet`, `haiku`, `fable`, or a full name),
  `--permission-mode default|acceptEdits|plan|auto|dontAsk|bypassPermissions`, `--allowedTools`, `--disallowedTools`, `--tools`,
  `--max-turns`, `--max-budget-usd`, `--no-session-persistence`, `--permission-prompts none` (v2.1.259+), `--bare`.
- `claude auth status` prints JSON with `authMethod` ∈ {`none`, `claude.ai`, `oauth_token`, `api_key`, `api_key_helper`, `third_party`}; exit 1 when logged out.
- Without `--bare`, `-p` loads the user's and the repository's hooks, `.mcp.json` servers and CLAUDE.md, and shows **no workspace trust dialog**.
  `--bare` skips them but does **not** use subscription login (it needs `ANTHROPIC_API_KEY`).
- SIGTERM exits with 143 and terminates the Bash process tree.
- **Policy:** OAuth/subscription authentication is "designed to support ordinary use of Claude Code". Anthropic does not permit
  third-party developers "to route requests through Free, Pro, or Max plan credentials on behalf of their users". This does not
  prevent "an end user from signing in to the unmodified Claude Code binary with their own Claude subscription".
  Advertised Pro/Max limits "assume ordinary, individual usage".

### Codex CLI (learn.chatgpt.com/docs/non-interactive-mode)

- `codex exec` defaults to a read-only sandbox. `--sandbox workspace-write` allows edits. `--full-auto` is no longer supported.
- `--json` JSONL events: `thread.started`, `turn.started`, `turn.completed` (with `usage`), `turn.failed`, `item.*`, `error`.
- `--output-schema`, `-o/--output-last-message`, `--ephemeral`, `--ignore-user-config`, `--skip-git-repo-check`, `codex exec -` reads the prompt from stdin.
- It reuses the saved CLI login by default. `CODEX_API_KEY` sets an API key for one run. **API keys are "the right default for automation"**, and
  ChatGPT-managed auth in CI is documented as an advanced option for trusted runners only.

### Gemini CLI / Antigravity CLI (geminicli.com/docs/cli/headless; developers.googleblog.com)

- On 2026-06-18, Gemini CLI was **replaced by Antigravity CLI (`agy`)** for unpaid, Google AI Pro/Ultra and Google One users.
  Gemini CLI remains for enterprise and API-key users.
- Gemini CLI headless: `--output-format json` → `{response, stats, error?}`. Exit codes are 0, 1, 42 (input) and 53 (turn limit).
- Antigravity CLI `-p` with `--output-format json` was added in 1.1.8, according to release notes. That is a secondary source
  and has not been verified on this host. A public issue reports `-p` hanging without a TTY in 1.0.6.

## 4. Conclusions

1. **Codex CLI is feasible now** as a real worker and as a structured-completion engine.
2. **Claude Code is feasible on paper** but **blocked on this host** until it is installed natively and logged in. The adapter
   is implemented against the documented flags, but it stays `unverified` and is disabled by `doctor` until a live probe passes.
3. **Gemini is deferred to Phase 2**, and the target is now Antigravity CLI rather than Gemini CLI for most consumer users.
4. The "two providers edit separate worktrees" mechanism is proven with Codex in one worktree while a sibling worktree
   stays untouched. A Claude + Codex run is still pending the Claude install.
5. Running Claude Code on a subscription from an orchestrator is a **policy-sensitive** use. See ADR-0006.

## 5. Live evidence: vertical slice through the real Codex CLI (2026-10-18)

The example in `examples/health-widget` was run with `policy.codex-only.yaml`, with Codex as lead and as both workers,
because Claude Code is not installed on this host:

| Step | Result |
|---|---|
| `saarathi doctor --live` | Structured completion succeeded in 5.8 s; model `unreported` |
| Lead intake + plan (read-only sandbox, detached worktree) | Two independent tasks with bounded paths and stdlib verification commands; passed policy checks |
| Execution | T-001 and T-002 ran **in parallel in separate worktrees** (~23 s); one attempt each |
| Saarathi verification | `python3 -m unittest tests.test_health tests.test_server` → 0; `python3 -m unittest tests.test_widget_files` → 0 |
| Integration | `--no-ff` merges onto `saarathi/<run>/integration`; `python3 -m unittest discover -s tests -t .` → 0 |
| Lead review | `approve`; the user's `main` branch and working tree were unchanged |

Opt-in live tests (`SAARATHI_LIVE_TESTS=1 pytest tests/live`) also passed. They confirm that workspace-write
changes stay in the worktree and that the **read-only sandbox prevented a requested file write**.

Still unverified: the Claude Code adapter (R1), and therefore the two-*provider* live run (backlog S14). The
two-provider path is covered offline by `tests/e2e/test_scenarios.py::test_scenario_a_two_providers_to_reviewable_branch`
with scripted fake adapters, which are labeled as such.

## 6. Live evidence: Claude-led, two-provider run (2026-10-19)

Claude Code 2.1.302 was installed with the official native installer (`curl -fsSL https://claude.ai/install.sh | bash`).
The binary's code signature verified, and the user signed in with a Claude Pro subscription (`authMethod: claude.ai`).
Codex had auto-updated to 0.162.0-alpha.17.2, and its flag checks still passed.

`examples/health-widget` with `policy.claude-codex.yaml` (lead `claude-code:opus`):

| Step | Result |
|---|---|
| `saarathi doctor --live` | Claude: structured completion in 9.6 s, model reported `claude-opus-5-5`. Codex: 7.8 s, model unreported |
| Lead intake (Claude Opus) | Two material questions (XSS escaping, whether the page should call `/health`), each with a default. `--accept-defaults` recorded them as accepted defaults |
| Plan (Claude Opus) | Two parallel tasks, two ADRs, bounded paths, stdlib checks; passed policy validation |
| Routing | T-001 → `claude-code:sonnet` (best tier fit for routine work); T-002 → `codex-cli:default` (provider-spread bonus) |
| T-002 on Codex | Succeeded on attempt 1 |
| T-001 attempt 1 on Claude Sonnet | **Rejected**: running the tests left `__pycache__/*.pyc` files, which the scope check flagged |
| T-001 attempt 2 | Routed to `claude-code:opus` (Sonnet deprioritized after failing; Codex busy); succeeded using the failure feedback |
| Integration | Three checks, all exit 0; `--no-ff` merges on `saarathi/<run>/integration`; user's `main` unchanged |
| Lead review (Claude Opus) | `approve`, with three accurate non-blocking concerns (no JS-level escaping test, untracked `__pycache__`, widget not yet wired) |

The full report is saved in [`evidence/live-claude-codex-report.md`](evidence/live-claude-codex-report.md).

**Defect found and fixed:** build artifacts produced by running tests counted as task changes. Saarathi now never
stages paths matching `execution.ignored_artifacts` (default: `__pycache__`, `*.pyc`, tool caches, `node_modules`,
`.DS_Store`). Artifacts a worker commits itself still count.

Opt-in live tests for Claude (`tests/live/test_claude_live.py`) passed: worktree writes stayed isolated, and read-only
mode blocked a requested write.

## 7. Live evidence: Antigravity CLI and a three-provider run (2026-10-19)

`agy` 1.4.2 was already installed (`~/.local/bin/agy`) and signed in with the user's Google account. `agy models` works
without a TTY and listed six models for this account (Gemini 3.8 Flash high/medium/low, Claude Sonnet 4.6, Claude Opus
4.6 Thinking, GPT-OSS 120B). Saarathi treats all of them as provider `google`, because that is where the data goes.

What the probes established (all through Saarathi's filtered environment):

| Question | Finding |
|---|---|
| Prompt input | `-p` takes the prompt as its value; there is no stdin prompt mode (`-p -` is a literal prompt) |
| Structured output | `--output-format json --json-schema` → `structured_output` in a single envelope |
| Edits | `--mode accept-edits` wrote only in the working directory (a Git worktree) |
| Read-only | Default headless mode denied a requested `write_file`; reads of repository files worked |
| `--mode plan` | Only effective with slash commands enabled; it is not a read-only guarantee, so Saarathi does not use it |
| Denied tools | A denied tool (shell command, write, sometimes a read) ends the run with `status: SUCCESS`, exit 0 and **no** `structured_output`. Saarathi treats that as a failure, retries once with a note naming the denied tool, then fails closed |
| Shell commands | Always denied headless unless pre-approved in the user's own settings; workers are told not to run any. Saarathi runs verification itself |

Opt-in live tests (`tests/live/test_antigravity_live.py`) passed: workspace write stays in the worktree, read-only
cannot write, and the lead escalation schema (nested object, nullable integer) is accepted.

**Three-provider run** on `examples/health-widget` with `policy.three-providers.yaml`:

| Step | Result |
|---|---|
| Lead (Claude Opus) | Intake asked two material questions (accepted defaults); plan with two parallel tasks and two ADRs |
| Routing | T-001 → `antigravity-cli:gemini-3.8-flash-medium` (balanced tier fits routine work); T-002 → `codex-cli:default` (provider spread) |
| Workers | Both succeeded on the first attempt in separate worktrees |
| Integration | Two checks, both exit 0; the user's `main` was unchanged |
| Lead review | `approve`, with three accurate non-blocking concerns |

Report: [`evidence/live-three-provider-report.md`](evidence/live-three-provider-report.md).
