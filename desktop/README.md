# Saarathi Desktop

A desktop app for Saarathi (Tauri 2, React, TypeScript, Tailwind). It is a control layer over the engine. Every
action goes through `saarathi bridge --stdio` (JSON-RPC, protocol 1, no network listener; see
[ADR-0013](../docs/architecture/decisions/ADR-0013-desktop-bridge.md) and
[the architecture](../docs/desktop/architecture.md)).

> **Status: v0.1 (milestones M1–M4 and the redesign in [DESIGN.md](DESIGN.md)).** Open a repository, manage sessions, send a request, watch real engine events
> stream in, answer the lead's questions, approve the plan and final review, stop, cancel and resume runs, and see
> sessions restored after a restart. Inspect any task (attempts, verification evidence, routing decisions, Monaco
> diffs, xterm.js logs) and follow the run on a timeline. Choose a session's approval preset and worker model, review
> the project's permissions, and see usage. Packaging comes in M5.

## Requirements

- An installed `saarathi` (the Python package) with at least one provider CLI signed in, as for the CLI.
- For development: Node 22+, Rust stable (via [rustup](https://rustup.rs)) and the
  [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/) for your OS.

## Develop

```bash
cd desktop
npm install
SAARATHI_BRIDGE="$(cd .. && pwd)/.venv/bin/saarathi" npx tauri dev
```

`SAARATHI_BRIDGE` points the app at a specific `saarathi` executable. Without it, the app looks up `saarathi` on the
PATH of your login shell. The app reads that PATH explicitly, because apps opened from the Finder or Dock do not
inherit your shell's PATH, and provider CLIs installed through `fnm`, `nvm` or `~/.local/bin` would otherwise be invisible.

## Checks

```bash
npm run typecheck && npm test && npx vite build
cargo fmt --manifest-path src-tauri/Cargo.toml --check
cargo clippy --manifest-path src-tauri/Cargo.toml -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml
```

The engine side of the desktop app (sessions, runs, cancellation, restart and reattach) is tested in
`tests/e2e/test_bridge.py` at the repository root.

## Behavior you can rely on

- **Closing the window** ends the app and the bridge. Runs keep going in their own processes, and the next launch
  reattaches.
- **Switching sessions** only changes what you see.
- **Stop** lets in-flight attempts finish, then pauses. **Cancel** interrupts the run and marks it cancelled. Branches
  and worktrees are kept.
- A project runs one request at a time. A second session's request waits until the first is finished, stopped or
  cancelled.
- The task inspector, timeline, diffs and logs are read-only views of what the engine recorded. A diff shows the
  task's committed change; while the task has not passed verification it is marked as not accepted.
- Enter sends a request; Shift+Enter adds a new line. A guided tour runs on first launch (replay it from Settings or ⌘K).
- The Policy page shows and edits the project's rules, including the engineering lead. Changes are previewed with
  warnings, validated by the engine, backed up and logged; comments in `policies.yaml` are kept.
- A session's approval preset and model apply to its next request. The engine checks both against the policy
  (`approvals.max_preset`, routing filters); the app never approves or routes anything itself.
- Usage shows only what providers reported: tokens and costs per call, and remaining plan usage from each CLI's own usage
  command (`claude /usage`, Codex's rate-limit read, `agy /usage`). Nothing is estimated or read from credentials.
- Models you turn off in Settings are never used in any project. Appearance (theme, accent, size, motion, panels) is
  stored per computer.
- Everything shown comes from the engine's records and events. The app never invents progress or reasoning, and it does
  not stream model text (the provider adapters return final results).
