# Release checklist

- [ ] `uv run pytest`, `uv run ruff check src tests`, `uv run mypy` pass on Linux and macOS CI.
- [ ] Live tests pass for every adapter marked "verified" in the compatibility matrix, run within 14 days of release.
- [ ] Compatibility matrix lists exact CLI versions and dates.
- [ ] Scenario A demonstrated live with two distinct providers (blocking for v0.1).
- [ ] Scenarios B, D, E, F and G–P pass (G–P blocking for v0.1, see brief §12A). Scenario C only in its
      subscription-only form: API modes are out of scope for v0.1 (ADR-0011).
- [ ] CHANGELOG updated; version bumped in `pyproject.toml` and `src/saarathi/__init__.py`.
- [ ] `uv.lock` committed; the CI `package` job builds the wheel and `scripts/smoke-test-wheel.sh` passes.
- [x] PyPI distribution name, repository name and trademark checked (R13, [name-clearance-2026-10-19](name-clearance-2026-10-19.md)); owner accepts the residual India/`sarathi` risk before going public.
- [ ] Private vulnerability reporting enabled on the GitHub repository when it becomes public (SECURITY.md relies on it).
- [ ] README limitations reviewed for accuracy.
- [ ] Desktop: the Release workflow (Actions → Release → Run workflow) builds the macOS, Windows and Linux installers,
      and its "Check the bundled engine starts" step passes on all three.
- [ ] Desktop: manual smoke test of each installer on its OS (install, open a repository, check providers, run a small
      request through plan approval and review). macOS done 2026-10-20; Windows and Linux not yet.
- [ ] Desktop: signing secrets set ([signing.md](signing.md)), or the unsigned first-launch steps in
      [installing.md](installing.md) accepted for this release.
- [ ] Desktop: version bumped in `desktop/package.json`, `desktop/src-tauri/tauri.conf.json` and `Cargo.toml`.
- [ ] Tag `vX.Y.Z` pushed; the draft release reviewed (installers and `SHA256SUMS.txt`) and published by a person.
