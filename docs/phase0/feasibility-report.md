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
