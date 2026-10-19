# Risk register

Scale: likelihood/impact H/M/L. Owners default to the maintainers.

| # | Risk | L | I | Mitigation | Status |
|---|---|---|---|---|---|
| R1 | Claude Code not installed on the dev host, so the Claude-led slice cannot be verified live | H | H | Installed 2.1.302 natively; `doctor --live`, live tests and the two-provider demo passed on 2026-10-19 | **Closed** |
| R2 | Subscription terms restrict orchestrated use of consumer plans | M | H | ADR-0006: unmodified CLI, user's own login, doctor notice, API key recommended for heavy use | Mitigated (documented) |
| R3 | CLI flags and output change between releases (Codex is on an alpha channel) | H | M | Minimum-version gates, flag checks in `doctor`, version-specific parsers, contract tests with recorded fixtures | Mitigated |
| R4 | Claude Code `-p` loads repository hooks and `.mcp.json` without a trust prompt, so a malicious repository could run code | M | H | Worktrees live outside the user's repo, but they still contain repository files. Default `--setting-sources user` plus `--strict-mcp-config` with empty config, and document the risk. `--bare` when API-key mode | Partially mitigated |
| R5 | Claude Code Bash tool is not OS-sandboxed | M | H | Restrict `--allowedTools` to edit tools plus exact verification commands, `dontAsk` mode, post-hoc scope and checkout-integrity checks | Partially mitigated |
| R6 | Model IDs and entitlements vary per account; the Codex model list has no documented discovery command | H | M | Models are user-declared or CLI default. `policy validate` rejects unknown IDs. Record "unreported" rather than guessing | Mitigated |
| R7 | Gemini CLI replaced by Antigravity CLI for consumer users; agy headless mode denies tools it cannot ask about and then returns no result | H | M | Adapter targets `agy` 1.4.2: workers told not to run commands (Saarathi verifies), read-only calls told not to write, one bounded retry after a denial, missing output fails closed | Mitigated |
| R8 | Orphaned worker processes keep writing after an engine crash | M | M | Workers run in their own process group. The pgid is persisted and killed during reconciliation, and the worktree is reset before retry | Mitigated |
| R14 | Workers' own test runs leave build artifacts that fail scope checks | H | M | `execution.ignored_artifacts` excludes them from staging (found in the live run) | Mitigated |
| R9 | Rate-limit and usage-limit errors are only text, with no stable codes for Codex | M | M | Pattern-based classification into transient rate limits and quota exhaustion; per-adapter escalating cooldowns; bounded per-task limit retries outside the failure budget; pause when every eligible worker is exhausted. Real limit messages are not yet recorded as fixtures | Partially mitigated |
| R10 | Cost runaway via API keys inherited by CLIs | M | H | Environment allowlist drops `*_API_KEY` unless configured. API-key-authenticated CLIs are classified as metered and blocked when the API is disabled | Mitigated |
| R11 | Prompt injection in repository docs steering workers | H | M | Untrusted-data envelopes, deterministic routing and permissions, command policy on plans, scope enforcement | Mitigated (tests in Scenario F) |
| R12 | POSIX-only locking and process handling | M | L | ADR-0012: msvcrt lock, taskkill tree kill, safe liveness check, Windows env allowlist; Windows CI job green. Provider CLIs not yet run live on Windows | Mitigated |
| R13 | PyPI name and trademark availability | M | L | 2026-10-19: `saarathi`, `saarathi-ai`, `saarathi-cli` unregistered on PyPI; `sarathi` (one "a") is taken by "A CLI coding assistant", a confusion risk in the same space. Trademark not checked | Open |
