# ADR-0012: Windows support

- Status: Accepted
- Date: 2026-10-19
- Extends: ADR-0003 (engine lock), ADR-0004 (isolation)

## Context

The control plane relied on POSIX primitives: `fcntl.flock` for the engine lock (ADR-0003 noted that Windows needs an
alternative), sessions and `killpg` for killing a worker's process tree, and `os.kill(pid, 0)` to test whether a
recorded worker is still alive. On Windows, `os.kill(pid, 0)` does not test anything: it terminates the process.

## Decision

- **Process trees:** on Windows each child starts with `CREATE_NEW_PROCESS_GROUP`, and its tree is ended with
  `taskkill /T`, then `/T /F` after a grace period. POSIX keeps new sessions and `killpg`.
- **Liveness:** Windows uses `OpenProcess` and `GetExitCodeProcess` (`STILL_ACTIVE`). It never signals the process.
- **Engine lock:** `msvcrt.locking` on one byte on Windows, `flock` elsewhere. Both are released by the OS when the
  holder dies, so crash recovery (Scenario B) works the same way.
- **Environment:** the allowlist adds the Windows system variables programs need to start (`SYSTEMROOT`,
  `PATHEXT`, `USERPROFILE`, ...) and matches names case-insensitively. None of them carries a secret.
- **Command policy:** program names are normalised (`Python.EXE`, `python3.12`, `npm.cmd`), drive-letter and
  backslash paths count as absolute, and `cmd /c`, PowerShell `-Command`/`-EncodedCommand` and `py -c` count as
  inline code. Windows system tools such as `taskkill`, `reg` and `icacls` are refused.
- **Files:** Saarathi writes its memory files with LF line endings everywhere, and the repository's `.gitattributes`
  keeps LF checkouts.
- **Verification:** a Windows job (Python 3.12) runs the full test suite in CI.

## Consequences

- The offline suite, including the SIGKILL-style crash-recovery tests (TerminateProcess on Windows), passes on
  Windows, Linux and macOS in CI.
- No provider CLI has been run live on Windows yet. The adapters use the same documented flags, but Windows behavior
  of Claude Code, Codex CLI and Antigravity CLI under Saarathi is unverified.
- Deep worktree paths can exceed Windows' 260-character limit. Enable `git config --global core.longpaths true`.
