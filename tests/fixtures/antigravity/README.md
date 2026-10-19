Recorded from `agy` 1.4.2 (Antigravity CLI) on 2026-10-19 through `AntigravityCliAdapter.invoke` with model
`gemini-3.8-flash-medium`:

- `print-1.4.2-success.json`: workspace-write invocation (`--mode accept-edits`) that created a file.
- `print-1.4.2-denied.json`: read-only invocation asked to write a file; the write was denied and no
  structured output was produced (exit code 0, status `SUCCESS`).

The only edit: a local scratch path in `changed_files` was replaced with `/tmp/agy-fixture/repo`.
Re-record when supporting a new CLI version.
