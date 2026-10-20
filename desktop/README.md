# Saarathi Desktop

A desktop app for Saarathi (Tauri 2, React, TypeScript, Tailwind). It is a control layer over the engine. Every
action goes through `saarathi bridge --stdio` (JSON-RPC, protocol 1, no network listener; see
[ADR-0013](../docs/architecture/decisions/ADR-0013-desktop-bridge.md) and
[the architecture](../docs/desktop/architecture.md)).

> **Status: v0.1 foundation (milestones M1–M2).** Open a repository, manage sessions, send a request, watch real
> engine events stream in, answer the lead's questions, approve the plan and final review, stop, cancel and resume
> runs, and see sessions restored after a restart. Diffs, logs, provider and model controls, approval presets and
> packaging come in M3–M5.

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
- Everything shown comes from the engine's records and events. The app never invents progress or reasoning, and it does
  not stream model text (the provider adapters return final results).
