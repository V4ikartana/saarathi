# Release checklist

- [ ] `uv run pytest`, `uv run ruff check src tests`, `uv run mypy` pass on Linux and macOS CI.
- [ ] Live tests pass for every adapter marked "verified" in the compatibility matrix, run within 14 days of release.
- [ ] Compatibility matrix lists exact CLI versions and dates.
- [ ] Scenario A demonstrated live with two distinct providers (blocking for v0.1).
- [ ] Scenarios B–F and G–P pass (G–P blocking for v0.1, see brief §12A).
- [ ] CHANGELOG updated; version bumped in `pyproject.toml` and `src/saarathi/__init__.py`.
- [ ] `uv.lock` committed; `uv build` produces a wheel that installs and runs `saarathi --help`.
- [ ] PyPI distribution name, repository name and trademark checked (R13).
- [ ] SECURITY.md and CODE_OF_CONDUCT.md contacts filled in.
- [ ] README limitations reviewed for accuracy.
