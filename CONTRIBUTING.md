# Contributing

Thanks for helping. Saarathi aims to be small, explicit and verifiable.

## Development setup

```bash
uv sync                      # Python 3.12+, creates .venv with dev tools
uv run pytest                # unit, contract and end-to-end tests (no network, no quota)
uv run ruff check src tests  # lint
uv run ruff format src tests # format
uv run mypy                  # strict type checking
```

Live provider tests are opt-in and use your own quota:

```bash
SAARATHI_LIVE_TESTS=1 uv run pytest tests/live
```

## Ground rules

- **Never claim provider support without evidence.** A new adapter or capability needs a recorded fixture
  under `tests/fixtures/`, a contract test, and an entry in `docs/providers/compatibility-matrix.md` with
  the version and date checked.
- **Models propose; code decides.** Anything that authorizes, spends, starts a process, writes durable state
  or merges belongs in deterministic, tested code.
- **Unknown means unsupported.** Do not default unknown capabilities, pricing or models to permissive values.
- No new runtime dependency without a short justification in the PR.
- Architecture changes need an ADR in `docs/architecture/decisions/`. Supersede ADRs; do not rewrite them.

## Adding a provider adapter

1. Read ADR-0002. Implement `ProviderAdapter` in `src/saarathi/providers/<name>.py`: `_probe_impl` uses only
   documented status commands, and `invoke` uses the provider's documented structured-output mechanism.
2. Register it in `providers/registry.py`.
3. Add parser contract tests from **recorded** output and an opt-in live test.
4. Update the compatibility matrix and README.

## Pull requests

Keep PRs focused, include tests, and describe limitations honestly. CI must pass.
