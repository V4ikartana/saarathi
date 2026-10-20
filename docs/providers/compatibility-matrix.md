# Provider compatibility matrix

Legend: **V** verified live on a host · **D** documented officially, not yet run · **–** unsupported or unknown (treated as unsupported).
Evidence and dates are in [`docs/phase0/feasibility-report.md`](../phase0/feasibility-report.md).

| Capability | Codex CLI 0.162.0-alpha.2 → 0.163.0 | Claude Code 2.1.302 | Antigravity CLI (`agy`) 1.4.2 | Anthropic / OpenAI / Google APIs |
|---|---|---|---|---|
| Adapter status in v0.1 | **Enabled, verified live** (0.163.0 stable via npm, 2026-10-19) | **Enabled, verified live** (Pro subscription login) | **Enabled, verified live** (Google account login) | Deferred past v0.1 (ADR-0011) |
| Non-interactive execution | V `codex exec` | V `claude -p`, prompt on stdin | V `agy -p <prompt>` (prompt is an argument; no stdin mode) | n/a |
| Structured final output | V `--output-schema` + `-o` | V `--json-schema` → `structured_output` | V `--output-format json --json-schema` → `structured_output`; a denied tool leaves it empty with exit 0 | D |
| Strict schema with nullable fields and nested closed objects (lead plan with `integrations`) | V (alpha.17.2, 2026-10-19) | V (2.1.302, 2026-10-19) | V escalation schema (nested object, nullable integer), 2026-10-19; plan schema with `integrations` not yet run | n/a |
| Event stream | V `--json` JSONL | D `--output-format stream-json` (Saarathi uses `json`) | D `--output-format stream-json` (Saarathi uses `json`) | n/a |
| Workspace write restriction | V `--sandbox workspace-write` (OS sandbox) | V writes stayed in the worktree; enforced by permission rules only, and Bash is not OS-sandboxed | V `--mode accept-edits` wrote only in the working directory; shell commands are denied in headless mode | n/a |
| Read-only mode | V `--sandbox read-only` blocked a requested write | V `--tools Read,Glob,Grep` + `dontAsk` blocked a requested write | V default headless mode denied a requested write; reads allowed. Any denial ends the run without output (one retry) | n/a |
| Model selection | D `-m` (IDs not discoverable through a documented command) | V `--model opus` resolved to `claude-opus-5-5`; `sonnet` also used | V `--model <slug>`; `agy models` lists the account's models; no silent fallback | D |
| Model discovery | V `codex debug models` (0.163.0, an undocumented debug command): 7 picker models discovered, `--model gpt-5.6-luna` answered (2026-10-20); falls back to declared models if it fails | V no listing command; documented aliases `opus`, `sonnet`, `haiku` plus `fable` from `--help`. `haiku` ran as claude-haiku-5-5; Fable 5.1 answered "requires usage credits" on a Pro subscription, so it is listed as unavailable (2026-10-20) | V `agy models`: 6 models discovered (2026-10-20) | n/a |
| Reasoning tiers of discovered models | From Saarathi's model catalogue (`providers/model_catalog.py`, vendor docs 2026-10-20) or Codex's own model descriptions | Catalogue | Catalogue | n/a |
| Model actually used is reported | – (not in JSONL) | V `modelUsage` keys | D not in the envelope; a pinned model cannot silently fall back, so it is recorded | D |
| Usage reporting | V tokens in `turn.completed.usage` | V tokens + `total_cost_usd` (client-side API-equivalent estimate; not a charge on subscription) | V tokens (input, output, thinking, cache read); no cost | D |
| Cancellation | V process-group kill (Saarathi runner tests) | D SIGTERM → exit 143, kills Bash tree | D process-group kill; `--print-timeout` set below Saarathi's timeout | n/a |
| Auth status check | V `codex login status` | V `claude auth status` (`authMethod: claude.ai`) | V `agy models` succeeds when signed in; works without a TTY | env var presence |
| Ignore user/repo config | V `--ignore-user-config` | D `--setting-sources user`, `--strict-mcp-config` (user hooks still load) | – user settings in `~/.gemini/antigravity-cli/settings.json` apply | n/a |
| Integration inventory (connected services, accounts, scopes) | – no documented interface; `integrations scan` reports `unverified` | – no documented interface; `integrations scan` reports `unverified` | – `agy mcp list` shows MCP servers, not accounts or scopes; `integrations scan` reports `unverified` | n/a |
| Safe integration probe | – (`verify` can only record user confirmation) | – (`verify` can only record user confirmation) | – | n/a |
| Automation policy | API key recommended; ChatGPT auth on trusted machines | Subscription for "ordinary use"; see ADR-0006 | Docs present headless mode for scripting and CI; no usage-policy statement found | Deferred |
