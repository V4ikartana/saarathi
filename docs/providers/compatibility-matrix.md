# Provider compatibility matrix

Legend: **V** verified live on a host · **D** documented officially, not yet run · **–** unsupported or unknown (treated as unsupported).
Evidence and dates are in [`docs/phase0/feasibility-report.md`](../phase0/feasibility-report.md).

| Capability | Codex CLI 0.162.0-alpha.2 | Claude Code (≥ 2.1.259) | Antigravity / Gemini CLI | Anthropic / OpenAI / Google APIs |
|---|---|---|---|---|
| Adapter status in v0.1 | **Enabled** | Implemented, disabled until a live probe passes | Not implemented (Phase 2) | Not implemented (Phase 2) |
| Non-interactive execution | V `codex exec` | D `claude -p` | D `agy -p` (secondary source) | n/a |
| Structured final output | V `--output-schema` + `-o` | D `--json-schema` → `structured_output` | D `--output-format json` (no schema flag verified) | D |
| Event stream | V `--json` JSONL | D `--output-format stream-json` | D | n/a |
| Workspace write restriction | V `--sandbox workspace-write` (OS sandbox) | D permission rules only; Bash is not OS-sandboxed | – | n/a |
| Read-only mode | V `--sandbox read-only` blocked a requested write | D `--tools Read,Glob,Grep` + `--permission-mode dontAsk` | – | n/a |
| Model selection | D `-m` (IDs not discoverable through a documented command) | D `--model` aliases `opus`/`sonnet`/`haiku`/`fable` or full ID | – | D |
| Model actually used is reported | – (not in JSONL) | D `modelUsage` keys in JSON result | – | D |
| Usage reporting | V tokens in `turn.completed.usage` | D tokens + `total_cost_usd` (client estimate) | D `stats` | D |
| Cancellation | V process-group kill (Saarathi runner tests) | D SIGTERM → exit 143, kills Bash tree | – | n/a |
| Auth status check | V `codex login status` | D `claude auth status` (JSON `authMethod`) | – | env var presence |
| Ignore user/repo config | V `--ignore-user-config` | D `--bare` (forces API key) or `--setting-sources` | – | n/a |
| Automation policy | API key recommended; ChatGPT auth on trusted machines | Subscription for "ordinary use"; see ADR-0006 | Consumer tiers moved to Antigravity 2026-06-18 | Metered; budgets required |
