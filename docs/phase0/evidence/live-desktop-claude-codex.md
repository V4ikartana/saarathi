# Live check: Saarathi Desktop with Claude Code and Codex CLI

Date: 2026-10-20. macOS, Saarathi Desktop debug build (milestones M1–M4), Claude Code 2.1.302 (Claude subscription),
Codex CLI 0.163.0 (ChatGPT sign-in). Project: `examples/health-widget` with `policy.claude-codex.yaml`, fresh data
directory, real providers only (`SAARATHI_ALLOW_FAKE_PROVIDERS` unset).

## What happened

| Step | Result |
|---|---|
| Settings → Check providers / `saarathi doctor` | Both adapters available and unmetered; lead `claude-code:opus` |
| Permissions page | Each worker's containment note and exact command line rendered from the adapters' own `build_argv` |
| Request (the example brief) | Lead intake and plan in 51 s, no clarification questions, two independent low-risk tasks |
| Plan approval | Approved with the app's **Approve plan** button (session preset: Ask before actions) |
| Execution | T-001 on `claude-code:sonnet` (33 s), T-002 on `codex-cli:default` (38 s), in parallel; both passed Saarathi's own verification (`exit 0`) on the first attempt |
| Integration | Both branches merged on `saarathi/<run>/integration`; integration checks `exit 0`; lead review: approve with three minor concerns |
| Final review | Approved with **Approve and finish**; run `COMPLETED`; `main` unchanged, work on the task and integration branches |
| Inspector | Codex's diff in Monaco; Codex's JSONL process log in xterm.js with the session path redacted; Codex's own sandbox refused an `rm -rf` cleanup command during the task |
| Usage page | 5 calls. Claude reported tokens and a client-side cost estimate (≈ $0.43 lead, ≈ $0.08 worker); Codex reported 108,858 input tokens (89,856 cached) and no cost. No quota figures shown, none reported |

## Notes

- Requests and the brief were sent through the bridge protocol by a script, because a backgrounded app cannot be typed
  into by the test harness. Approvals, navigation and inspection used the app itself.
- Found and fixed during the check: the Stop button showed while the run waited for the user, and the usage table
  hid cached input (most of Claude's input is served from its prompt cache).
- The Codex binary resolves to an `fnm` per-shell path. It worked here; installers (M5) should resolve provider
  binaries to stable paths.
