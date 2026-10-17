# Architecture overview

Saarathi is a local Python application. A deterministic **control plane** owns all state and
authority. Models only propose work, through structured outputs that the control plane validates.

```text
saarathi CLI (typer)
   │
   ├── orchestration/engine.py      run + task state machines, scheduler, recovery, integration
   │      ├── orchestration/lead.py   engineering lead: intake, plan, final review (structured, read-only)
   │      ├── routing/router.py       hard filters → capacity → explainable score
   │      ├── providers/*             ProviderAdapter port: codex_cli, claude_cli, fake (tests)
   │      ├── git/workspace.py        worktrees outside the repo, scope checks, integration merges
   │      ├── verification/           command policy + Saarathi-run checks → evidence
   │      └── memory/store.py         .saarathi/ Markdown memory, append-only ADRs
   ├── storage/                       SQLite projections + append-only event log (one transaction each)
   ├── runtime/process.py             process groups, timeouts, cancellation, capped output
   └── security/                      environment allowlist, redaction
```

## Run lifecycle

```text
CREATED → INTAKE → (CLARIFYING) → PLANNING → AWAITING_PLAN_APPROVAL → EXECUTING → VERIFYING
        → AWAITING_REVIEW → READY_TO_SHIP → COMPLETED          (PAUSED / FAILED / CANCELLED)
```

1. **Intake.** The pinned lead model reads the brief and a summary of the repository from a detached
   worktree at the base commit. The repository text is wrapped as untrusted data. The lead returns
   material questions, each with a default assumption.
2. **Clarify.** Questions are asked interactively. Otherwise the run waits (`saarathi answer`).
   Pass `--accept-defaults` to accept the lead's stated defaults explicitly, and the record shows that you did.
3. **Plan.** The lead returns tasks, each with allowed paths, verification commands and dependencies.
   The control plane rejects plans with cycles, unbounded paths, protected paths or disallowed commands,
   gives the lead one chance to fix them, and otherwise pauses.
4. **Approve.** A person approves. Saarathi records the plan's decisions as accepted ADRs, compiles a
   cited, size-capped context snapshot per task, and writes the task packets.
5. **Execute.** Ready tasks are routed and leased. Each attempt runs in its own worktree and branch.
   Saarathi then computes the diff itself, rejects out-of-scope changes and any change to the user's
   checkout, commits the change with attempt trailers and runs the verification commands. Failures retry with
   feedback up to the attempt budget, then pause the run with a checkpoint file.
6. **Integrate.** Task branches are merged with `--no-ff` in dependency order onto
   `saarathi/<run>/integration`, and the integration checks run.
7. **Review.** The lead reviews the diff against the plan and Saarathi's evidence. A person approves, and
   the report is written to `.saarathi/reports/`. Saarathi never merges into the user's branches.

## Recovery

Only the holder of `.saarathi/engine.lock` (an `fcntl` lock released by the OS on process death)
drives a run. On start, any task still `LEASED`/`RUNNING`/`VERIFYING` is orphaned:

- An attempt whose commit (trailer `Saarathi-Attempt: <id>`) exists is **re-verified only**. The worker is never re-run.
- Otherwise its process group is killed if still alive, the worktree is reset, and the attempt is marked
  `abandoned`. The task returns to `RETRY_WAIT` without consuming its failure budget.

Scenario B tests (`tests/e2e/test_recovery.py`) SIGKILL a real `saarathi` process at both points.

## What is deliberately not here yet

The model-backed operations orchestrator, API adapters and budget ledger, the integration/account registry (§12A)
and Antigravity/Gemini are not built yet. See [`docs/phase0/backlog.md`](../phase0/backlog.md).
