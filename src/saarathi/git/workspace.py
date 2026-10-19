"""Git worktree management, diff-scope enforcement and integration merges (ADR-0004).

Saarathi never writes to the user's checkout, never force-pushes and never merges into the user's
branches. All work happens on ``saarathi/<run>/<task>`` branches in worktrees outside the repository.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_dir

from saarathi.core.errors import SaarathiError
from saarathi.security.env import filtered_env

SAARATHI_AUTHOR = ("Saarathi", "saarathi@localhost")
ALWAYS_DENIED = (".saarathi/**", ".git/**")
PRIVATE_PREFIXES = (".saarathi/",)


class GitError(SaarathiError):
    pass


def data_dir() -> Path:
    override = os.environ.get("SAARATHI_DATA_DIR")
    return Path(override) if override else Path(user_data_dir("saarathi", appauthor=False))


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += r"(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += r".*"
            i += 2
        elif pattern[i] == "*":
            out += r"[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += r"[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(rf"^{out}$")


def path_matches(path: str, pattern: str) -> bool:
    """Glob match where ``*`` stays within a path segment and ``**`` spans segments."""
    if pattern.endswith("/"):
        pattern += "**"
    return bool(_glob_regex(pattern).match(path))


def scope_violations(changed: list[str], allowed: list[str]) -> list[str]:
    violations = []
    for path in changed:
        if any(path_matches(path, p) for p in ALWAYS_DENIED) or not any(path_matches(path, p) for p in allowed):
            violations.append(path)
    return violations


@dataclass(frozen=True)
class MergeResult:
    ok: bool
    merged: list[str]
    conflict_branch: str | None = None
    conflict_files: tuple[str, ...] = ()


class GitRepo:
    def __init__(self, root: Path) -> None:
        self.root = root

    def git_proc(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = filtered_env(overrides={"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )

    def git(self, *args: str, cwd: Path | None = None, check: bool = True) -> str:
        proc = self.git_proc(*args, cwd=cwd)
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args[:3])} failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout

    # -------------------------------------------------------------- inspection

    def is_repo(self) -> bool:
        try:
            return self.git("rev-parse", "--is-inside-work-tree").strip() == "true"
        except GitError:
            return False

    def toplevel(self) -> Path:
        return Path(self.git("rev-parse", "--show-toplevel").strip())

    def head(self, cwd: Path | None = None) -> str:
        return self.git("rev-parse", "HEAD", cwd=cwd).strip()

    def current_ref(self) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def ls_files(self, limit: int = 400) -> list[str]:
        return self.git("ls-files").splitlines()[:limit]

    def _dirty_entries(self) -> list[str]:
        status = self.git("status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules")
        return [line for line in status.splitlines() if not any(line[3:].startswith(p) for p in PRIVATE_PREFIXES)]

    def checkout_snapshot(self) -> str:
        """Fingerprint of the user's checkout, ignoring Saarathi's own files.

        HEAD + status, plus size and mtime of every dirty path, so further edits to an
        already-modified or untracked file are also detected.
        """
        entries = []
        for line in sorted(self._dirty_entries()):
            path = self.root / line[3:].split(" -> ")[-1].strip('"')
            try:
                st = path.stat()
                entries.append(f"{line}\t{st.st_size}\t{st.st_mtime_ns}")
            except OSError:
                entries.append(f"{line}\tmissing")
        return self.head() + "\n" + "\n".join(entries)

    def has_uncommitted_changes(self) -> bool:
        return bool(self._dirty_entries())

    # -------------------------------------------------------------- worktrees

    def branch_exists(self, branch: str) -> bool:
        return self.git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).strip() != ""

    def ensure_worktree(self, path: Path, branch: str, base: str) -> None:
        """Create (or reuse) a worktree for ``branch`` starting at ``base``. Idempotent."""
        if (path / ".git").exists():
            current = self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=path).strip()
            if current != branch:
                raise GitError(f"worktree {path} is on {current}, expected {branch}")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "prune")
        if self.branch_exists(branch):
            self.git("worktree", "add", str(path), branch)
        else:
            self.git("worktree", "add", "-b", branch, str(path), base)

    def reset_worktree(self, path: Path, commit: str) -> None:
        """Discard everything in a Saarathi-owned worktree and return it to ``commit``."""
        self.git("merge", "--abort", cwd=path, check=False)
        self.git("reset", "--hard", commit, cwd=path)
        self.git("clean", "-fdx", cwd=path)

    def remove_worktree(self, path: Path) -> None:
        if path.exists():
            self.git("worktree", "remove", "--force", str(path), check=False)
        self.git("worktree", "prune", check=False)

    # -------------------------------------------------------------- changes

    def stage_and_list_changes(self, path: Path, base: str, ignored: Sequence[str] = ()) -> list[str]:
        """Stage the worktree and list paths differing from ``base`` (commits included).

        ``ignored`` globs (build artifacts such as ``**/__pycache__/**``) are never staged, so running
        tests inside a worktree does not count as an out-of-scope change. Anything a worker committed
        itself is still listed.
        """
        excludes = [f":(exclude,glob){pattern}" for pattern in ignored]
        self.git("add", "-A", "--", ".", *excludes, cwd=path)
        out = self.git("diff", "--cached", "--name-only", "--no-renames", base, cwd=path)
        return sorted(line for line in out.splitlines() if line)

    def commit_staged(self, path: Path, message: str) -> str | None:
        if not self.git("diff", "--cached", "--name-only", cwd=path).strip():
            return None
        name, email = SAARATHI_AUTHOR
        self.git(
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--no-verify",
            "-q",
            "-m",
            message,
            cwd=path,
        )
        return self.head(cwd=path)

    def find_commit_with_trailer(self, path: Path, base: str, trailer: str) -> str | None:
        out = self.git(
            "log",
            "--format=%H",
            f"--grep={trailer}",
            "--fixed-strings",
            f"{base}..HEAD",
            cwd=path,
            check=False,
        )
        lines = out.split()
        return lines[0] if lines else None

    def diff_stat(self, base: str, head: str) -> str:
        return self.git("diff", "--stat", f"{base}..{head}")

    def diff(self, base: str, head: str, max_chars: int = 60_000) -> str:
        text = self.git("diff", f"{base}..{head}")
        return text if len(text) <= max_chars else text[:max_chars] + "\n[diff truncated]\n"

    # -------------------------------------------------------------- integration

    def merge_branches(self, path: Path, branches: list[tuple[str, str]]) -> MergeResult:
        """Merge ``(branch, message)`` pairs in order with --no-ff; stop and abort at the first conflict."""
        name, email = SAARATHI_AUTHOR
        merged: list[str] = []
        for branch, message in branches:
            proc = self.git_proc(
                "-c",
                f"user.name={name}",
                "-c",
                f"user.email={email}",
                "-c",
                "commit.gpgsign=false",
                "merge",
                "--no-ff",
                "--no-verify",
                "-m",
                message,
                branch,
                cwd=path,
            )
            if proc.returncode != 0:
                conflicts = self.git("diff", "--name-only", "--diff-filter=U", cwd=path, check=False).split()
                self.git("merge", "--abort", cwd=path, check=False)
                return MergeResult(False, merged, branch, tuple(conflicts))
            merged.append(branch)
        return MergeResult(True, merged)
