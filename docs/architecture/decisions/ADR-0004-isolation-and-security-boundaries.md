# ADR-0004: Execution isolation and security boundaries

- Status: Accepted
- Date: 2026-10-18

## Decision

1. **One Git worktree and branch per task**, created from an immutable base commit recorded at plan approval. Worktrees
   live under the user data directory (`platformdirs.user_data_dir("saarathi")/worktrees/<project-id>/`), outside the
   repository. Agents therefore never discover the parent repository's agent configuration, and the user's checkout is never written.
2. **A worktree is not a sandbox.** Filesystem and process restrictions come from the provider's own documented sandbox:
   - Codex: `--sandbox workspace-write` (or `read-only`) and `--ignore-user-config` by default.
   - Claude Code: `--permission-mode dontAsk` with an explicit `--allowedTools` list, `--permission-prompts none` and MCP/web tools
     disallowed. Claude Code's Bash tool is **not** OS-sandboxed in this configuration, and the docs and `doctor` say so.
3. **Post-hoc enforcement Saarathi controls itself:**
   - It diffs the worktree against the base, and any path outside `allowed_paths` rejects the attempt.
   - It snapshots `git status --porcelain` of the user's checkout before and after each attempt. Any change is a security event, and the task fails.
   - Verification commands are run by Saarathi with `shell=False`, a filtered environment, a timeout and capped, redacted logs.
   - A command policy rejects plans whose verification commands use network, install, push or destructive tools unless policy allows them.
4. **Environment filtering:** child processes get an allowlisted environment (`PATH`, `HOME`, locale, `TMPDIR`, plus
   adapter-specific variables explicitly configured). Host variables such as `ANTHROPIC_BASE_URL`, `*_API_KEY` and tokens are dropped
   unless the policy names them.
5. **Untrusted content:** repository files, worker output and external tool output are wrapped as data in prompts and are never
   treated as instructions. Routing and permissions are decided by code, so injected text cannot change them.
6. **No credential handling.** Saarathi never reads or stores provider credentials. It invokes unmodified, user-installed CLIs that keep their own
   authentication.

## Consequences

- Saarathi does not claim OS-level isolation. Users who need it should run workers inside a container or VM (Phase 3).
- Scope violations are detected after the fact for providers without path-level write restrictions.
