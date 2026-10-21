# ADR-0015: Local open-source models run in Saarathi's own agent loop

- Status: Accepted
- Date: 2026-10-21

## Context

People who have downloaded an open-weight model (through Ollama, LM Studio, llama.cpp or vLLM) want Saarathi to use
it, free and without sending code anywhere. These servers speak the OpenAI chat API with function tools, but unlike
Claude Code, Codex and Antigravity there is no agent CLI around the model: nothing reads files, edits them or stays in
a sandbox. Codex's `--oss` mode was considered and set aside: it ties local models to one vendor CLI and its sandbox.

## Decision

- A new adapter, `local-openai` (provider `local`, transport `local`), drives the model itself
  (`providers/local_openai.py`). It needs no binary, API key or account.
- **Tools are Saarathi's own and confined to the task worktree:** `list_files`, `read_file`, `search`, and, for
  workspace-write invocations only, `write_file` and `replace_in_file` (exact, unique text). Paths are resolved and must
  stay inside the worktree; `.git` is never readable or writable; symlinks that leave the worktree are refused.
- **No shell tool.** As with Antigravity workers, Saarathi runs the task's verification commands after the model
  finishes; a worker's report is never proof.
- **Structured answers** come through a `submit_result` tool whose parameters are the invocation's JSON schema. Missing
  required fields, wrong basic types and invalid enum values are sent back so the model can correct them. A report that
  lists changed files when nothing changed is sent back too. Some models write tool calls as JSON text instead of API
  tool calls; those are recognised (known tool names only) and answered in text. A model that never submits fails closed
  with `invalid_output`.
- **This computer only, by default.** A `base_url` whose host is not loopback is refused unless
  `options.allow_remote: true`, and `doctor` then says where code is sent.
- **Ollama is driven through its native `/api/chat`** when detected, because its OpenAI endpoint cannot set the
  context window (the server default is often 4096 tokens, too small for a worker prompt plus tool descriptions).
  `options.context_tokens` (default 16384) sets it; `options.api: openai` forces the OpenAI endpoint.
- **Models are discovered** from `GET /v1/models`. They are unmetered (cost 0) and have no plan limits. A tier declared
  in the policy wins; otherwise the catalogue lists known open-weight families as economy, and an unrecognised local
  model is economy too, never "unknown" and never assumed strong. A local model can be preferred or pinned for a session
  like any other.
- The desktop app offers "Add to this project" for `local-openai`, and the policy screen notes a running Ollama or LM
  Studio server that the project does not use yet.

## Consequences

- Anyone with a local model can run Saarathi workers at no cost, offline, with code staying on the computer.
- Small local models are weaker at multi-step edits and strict answers than the subscription CLIs; Saarathi's
  verification and retries are what make them usable, and a frontier lead is still recommended.
- Saarathi now owns an agent loop and its tool sandbox, so the confinement rules above are security-relevant code with
  their own tests (`tests/contract/test_local_openai.py`).
