from saarathi.git.workspace import GitRepo, path_matches, scope_violations


def test_glob_semantics():
    assert path_matches("backend/api/health.py", "backend/**")
    assert path_matches("tests/test_x.py", "tests/*.py")
    assert not path_matches("tests/sub/test_x.py", "tests/*.py")
    assert path_matches("a/b/c.txt", "**/c.txt")
    assert path_matches("c.txt", "**/c.txt")
    assert path_matches("ui/x.js", "ui/")


def test_scope_violations_include_always_denied():
    changed = ["src/a.py", "docs/x.md", ".saarathi/policies.yaml"]
    assert scope_violations(changed, ["src/**", ".saarathi/**"]) == ["docs/x.md", ".saarathi/policies.yaml"]


def test_two_worktrees_are_independent_and_user_checkout_untouched(repo, tmp_path):
    g = GitRepo(repo)
    base = g.head()
    before = g.checkout_snapshot()
    a, b = tmp_path / "wt" / "a", tmp_path / "wt" / "b"
    g.ensure_worktree(a, "saarathi/r1/T-001", base)
    g.ensure_worktree(b, "saarathi/r1/T-002", base)
    g.ensure_worktree(a, "saarathi/r1/T-001", base)  # idempotent
    (a / "src" / "health.py").write_text("OK = True\n")
    (b / "ui.txt").write_text("widget\n")
    assert g.stage_and_list_changes(a, base) == ["src/health.py"]
    assert g.stage_and_list_changes(b, base) == ["ui.txt"]
    ca = g.commit_staged(a, "T-001\n\nSaarathi-Attempt: att-1")
    assert ca and g.find_commit_with_trailer(a, base, "Saarathi-Attempt: att-1") == ca
    assert g.commit_staged(a, "nothing") is None
    assert g.checkout_snapshot() == before


def test_reset_discards_partial_work(repo, tmp_path):
    g = GitRepo(repo)
    base = g.head()
    wt = tmp_path / "wt"
    g.ensure_worktree(wt, "saarathi/r/T-001", base)
    (wt / "junk.txt").write_text("partial")
    (wt / "src" / "app.py").write_text("broken")
    g.reset_worktree(wt, base)
    assert not (wt / "junk.txt").exists() and (wt / "src" / "app.py").read_text() == "VALUE = 1\n"


def test_merge_detects_conflicts(repo, tmp_path):
    g = GitRepo(repo)
    base = g.head()
    for name, content in (("x", "A\n"), ("y", "B\n")):
        wt = tmp_path / name
        g.ensure_worktree(wt, f"saarathi/r/{name}", base)
        (wt / "src" / "app.py").write_text(content)
        g.stage_and_list_changes(wt, base)
        g.commit_staged(wt, name)
    integ = tmp_path / "integ"
    g.ensure_worktree(integ, "saarathi/r/integration", base)
    result = g.merge_branches(integ, [("saarathi/r/x", "x"), ("saarathi/r/y", "y")])
    assert not result.ok and result.merged == ["saarathi/r/x"] and result.conflict_files == ("src/app.py",)
    assert g.git("status", "--porcelain", cwd=integ).strip() == ""  # merge aborted cleanly


def test_checkout_snapshot_detects_user_checkout_change(repo):
    g = GitRepo(repo)
    before = g.checkout_snapshot()
    (repo / ".saarathi").mkdir()
    (repo / ".saarathi" / "context.md").write_text("ours")  # Saarathi's own files are ignored
    assert g.checkout_snapshot() == before
    (repo / "src" / "app.py").write_text("tampered")
    dirty = g.checkout_snapshot()
    assert dirty != before
    import os
    import time

    time.sleep(0.01)
    (repo / "src" / "app.py").write_text("tampered again, longer")  # already-dirty file edited again
    os.utime(repo / "src" / "app.py")
    assert g.checkout_snapshot() != dirty
