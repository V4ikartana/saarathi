# Risk register

Scale: likelihood/impact H/M/L. Owners default to the maintainers.

| # | Risk | L | I | Mitigation | Status |
|---|---|---|---|---|---|
| R1 | Claude Code not installed on the dev host, so the Claude-led slice cannot be verified live | H | H | Adapter built against docs and disabled until `doctor --live` passes. Lead role can be pinned to any verified structured-output model meanwhile | **Open: needs user action** |
| R2 | Subscription terms restrict orchestrated use of consumer plans | M | H | ADR-0006: unmodified CLI, user's own login, doctor notice, API key recommended for heavy use | Mitigated (documented) |
| R3 | CLI flags and output change between releases (Codex is on an alpha channel) | H | M | Minimum-version gates, flag checks in `doctor`, version-specific parsers, contract tests with recorded fixtures | Mitigated |
| R4 | Claude Code `-p` loads repository hooks and `.mcp.json` without a trust prompt, so a malicious repository could run code | M | H | Worktrees live outside the user's repo, but they still contain repository files. Default `--setting-sources user` plus `--strict-mcp-config` with empty config, and document the risk. `--bare` when API-key mode | Partially mitigated |
| R5 | Claude Code Bash tool is not OS-sandboxed | M | H | Restrict `--allowedTools` to edit tools plus exact verification commands, `dontAsk` mode, post-hoc scope and checkout-integrity checks | Partially mitigated |
| R6 | Model IDs and entitlements vary per account; the Codex model list has no documented discovery command | H | M | Models are user-declared or CLI default. `policy validate` rejects unknown IDs. Record "unreported" rather than guessing | Mitigated |
| R7 | Gemini CLI replaced by Antigravity CLI for consumer users | H | M | Target Antigravity CLI in Phase 2. Verify `-p` TTY behavior first | Open |
| R8 | Orphaned worker processes keep writing after an engine crash | M | M | Workers run in their own process group. The pgid is persisted and killed during reconciliation, and the worktree is reset before retry | Mitigated |
| R9 | Rate-limit and usage-limit errors are only text, with no stable codes for Codex | M | M | Pattern-based classification, bounded backoff, then pause | Partially mitigated |
| R10 | Cost runaway via API keys inherited by CLIs | M | H | Environment allowlist drops `*_API_KEY` unless configured. API-key-authenticated CLIs are classified as metered and blocked when the API is disabled | Mitigated |
| R11 | Prompt injection in repository docs steering workers | H | M | Untrusted-data envelopes, deterministic routing and permissions, command policy on plans, scope enforcement | Mitigated (tests in Scenario F) |
| R12 | `fcntl` lock and process-group handling are POSIX-only | M | L | Windows support is backlog; CI on Linux and macOS first | Open |
| R13 | PyPI name `saarathi` and trademark availability unknown | M | L | Verify before first public release | Open |
