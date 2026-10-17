# Threat model (v0.1)

Saarathi runs on a developer's machine with that developer's permissions. It coordinates AI agents
that read and modify code. This document says what is defended, how, and what is not.

## Trust boundaries

| Source | Trust | Handling |
|---|---|---|
| The user (CLI, brief, answers, approvals) | Trusted | Instructions |
| `.saarathi/policies.yaml` | Trusted (user-owned) | Validated, enforced by code |
| Repository content (README, code, docs) | **Untrusted** | Wrapped in `<untrusted-data>` envelopes; never authority |
| Model output (plans, reports, reviews) | **Untrusted** | Schema- and policy-validated; reports are advisory |
| Provider CLIs | Trusted to enforce their own documented sandbox | Version- and flag-checked by `doctor` |

## Controls

- **Routing and permissions are code.** Allow and deny lists, capability checks, metered-usage blocks and lead pinning
  are evaluated deterministically, so injected text cannot change them (`tests/unit/test_router.py`).
- **Command policy** on plans and verification: no shell operators, no network, install, deploy, destructive or
  inline-code commands, and only read-only git (`tests/unit/test_verifier.py`).
- **Scope enforcement:** diffs outside `allowed_paths` or in `.saarathi/`/`.git/` reject the attempt.
- **Checkout integrity:** the user's checkout is fingerprinted before and after each attempt, and a change fails the
  attempt and logs `security.checkout_modified`.
- **Environment allowlist:** child processes see `PATH`, `HOME`, locale and `TMPDIR`, plus names you list in
  `execution.env_passthrough`. API keys and endpoint overrides are dropped by default.
- **Redaction:** event payloads and process logs pass through pattern- and environment-based redaction. This is best effort.
- **No credential handling:** Saarathi never reads credential stores or tokens. CLIs authenticate themselves.
- **Bounded execution:** every process has a timeout, its own process group, capped output, and is killed on cancel.

## Known gaps (do not rely on these for isolation)

1. **Verification commands are not sandboxed.** They run repository code (for example tests) with your OS
   permissions and the filtered environment. Run untrusted repositories inside a VM or container.
2. **Claude Code's Bash tool is not OS-sandboxed** in Saarathi's configuration. Bash is limited to the task's
   verification commands by permission rules, which is weaker than an OS sandbox.
3. **Without `--bare`, Claude Code loads user-level hooks and settings.** Saarathi limits setting sources to
   `user` and disables MCP servers, but your own hooks still run.
4. **Codex's sandbox is the OS-level control for Codex workers.** Saarathi has not independently audited it.
5. **Out-of-scope writes are detected after the fact**, not prevented, for files outside the user's checkout and
   the task worktree.
6. **`uv run` and similar launchers may sync dependencies.** The command policy blocks explicit install verbs only.
7. Local state (`.saarathi/state.db`, logs) is not encrypted at rest.

Report vulnerabilities as described in [SECURITY.md](../../SECURITY.md).
