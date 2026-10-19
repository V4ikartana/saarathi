# Release checklist

- [ ] `uv run pytest`, `uv run ruff check src tests`, `uv run mypy` pass on Linux and macOS CI.
- [ ] Live tests pass for every adapter marked "verified" in the compatibility matrix, run within 14 days of release.
- [ ] Compatibility matrix lists exact CLI versions and dates.
- [ ] Scenario A demonstrated live with two distinct providers (blocking for v0.1).
- [ ] Scenarios B, D, E, F and G–P pass (G–P blocking for v0.1, see brief §12A). Scenario C only in its
      subscription-only form: API modes are out of scope for v0.1 (ADR-0011).
- [ ] CHANGELOG updated; version bumped in `pyproject.toml` and `src/saarathi/__init__.py`.
- [ ] `uv.lock` committed; the CI `package` job builds the wheel and `scripts/smoke-test-wheel.sh` passes.
- [ ] PyPI distribution name, repository name and trademark checked (R13; PyPI name free as of 2026-10-19, similar `sarathi` exists).
- [ ] Private vulnerability reporting enabled on the GitHub repository when it becomes public (SECURITY.md relies on it).
- [ ] README limitations reviewed for accuracy.
