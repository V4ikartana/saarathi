# Provider compatibility matrix

Legend: **V** verified live on a host · **D** documented officially, not yet run · **–** unsupported or unknown (treated as unsupported).
Evidence and dates are in [`docs/phase0/feasibility-report.md`](../phase0/feasibility-report.md).

| Capability | Codex CLI 0.162.0-alpha.2 / alpha.17.2 | Claude Code 2.1.302 | Antigravity / Gemini CLI | Anthropic / OpenAI / Google APIs |
|---|---|---|---|---|
| Adapter status in v0.1 | **Enabled, verified live** | **Enabled, verified live** (Pro subscription login) | Not implemented (Phase 2) | Not implemented (Phase 2) |
| Non-interactive execution | V `codex exec` | V `claude -p`, prompt on stdin | D `agy -p` (secondary source) | n/a |
| Structured final output | V `--output-schema` + `-o` | V `--json-schema` → `structured_output` | D `--output-format json` (no schema flag verified) | D |
| Event stream | V `--json` JSONL | D `--output-format stream-json` (Saarathi uses `json`) | D | n/a |
| Workspace write restriction | V `--sandbox workspace-write` (OS sandbox) | V writes stayed in the worktree; enforced by permission rules only, and Bash is not OS-sandboxed | – | n/a |
| Read-only mode | V `--sandbox read-only` blocked a requested write | V `--tools Read,Glob,Grep` + `dontAsk` blocked a requested write | – | n/a |
| Model selection | D `-m` (IDs not discoverable through a documented command) | V `--model opus` resolved to `claude-opus-5-5`; `sonnet` also used | – | D |
| Model actually used is reported | – (not in JSONL) | V `modelUsage` keys | – | D |
| Usage reporting | V tokens in `turn.completed.usage` | V tokens + `total_cost_usd` (client-side API-equivalent estimate; not a charge on subscription) | D `stats` | D |
| Cancellation | V process-group kill (Saarathi runner tests) | D SIGTERM → exit 143, kills Bash tree | – | n/a |
| Auth status check | V `codex login status` | V `claude auth status` (`authMethod: claude.ai`) | – | env var presence |
| Ignore user/repo config | V `--ignore-user-config` | D `--setting-sources user`, `--strict-mcp-config` (user hooks still load) | – | n/a |
| Automation policy | API key recommended; ChatGPT auth on trusted machines | Subscription for "ordinary use"; see ADR-0006 | Consumer tiers moved to Antigravity 2026-06-18 | Metered; budgets required |
