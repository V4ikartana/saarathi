"""A Saarathi-managed repository: paths, policy, state store, memory and providers."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml

from saarathi.core.errors import ConfigError, EngineLocked
from saarathi.core.policy import DEFAULT_POLICY_YAML, Policy, load_policy
from saarathi.git.workspace import GitRepo, data_dir
from saarathi.integrations.registry import SqliteIntegrationRegistry
from saarathi.memory.store import ProjectMemory, init_layout
from saarathi.providers.registry import ProviderRegistry
from saarathi.security.redaction import Redactor
from saarathi.storage.store import StateStore

META = ".saarathi"


@dataclass
class Project:
    root: Path
    project_id: str
    name: str
    policy: Policy
    store: StateStore
    memory: ProjectMemory
    registry: ProviderRegistry
    git: GitRepo

    @property
    def meta(self) -> Path:
        return self.root / META

    @property
    def integrations(self) -> SqliteIntegrationRegistry:
        return SqliteIntegrationRegistry(self.store)

    @property
    def workspace(self) -> str:
        return self.policy.integrations.workspace

    @property
    def logs(self) -> Path:
        return self.meta / "logs"

    def worktree_root(self, run_id: str) -> Path:
        return data_dir() / "worktrees" / self.project_id / run_id

    @classmethod
    def load(cls, start: Path, *, registry: ProviderRegistry | None = None) -> Project:
        git = GitRepo(start)
        if not git.is_repo():
            raise ConfigError(f"{start} is not inside a Git repository", hint="run `git init` first")
        root = git.toplevel()
        meta = root / META
        project_file = meta / "project.yaml"
        if not project_file.exists():
            raise ConfigError(f"{root} is not a Saarathi project", hint="run `saarathi init`")
        info = yaml.safe_load(project_file.read_text(encoding="utf-8")) or {}
        policy = load_policy(meta / "policies.yaml")
        return cls(
            root=root,
            project_id=str(info["id"]),
            name=str(info.get("name", root.name)),
            policy=policy,
            store=StateStore(meta / "state.db", redact=Redactor()),
            memory=ProjectMemory(meta),
            registry=registry or ProviderRegistry.from_policy(policy),
            git=GitRepo(root),
        )

    @staticmethod
    def init(start: Path, *, name: str | None = None) -> Path:
        git = GitRepo(start)
        if not git.is_repo():
            raise ConfigError(f"{start} is not inside a Git repository", hint="run `git init` first")
        root = git.toplevel()
        meta = root / META
        init_layout(meta)
        project_file = meta / "project.yaml"
        if not project_file.exists():
            project_file.write_text(
                yaml.safe_dump({"id": uuid.uuid4().hex[:12], "name": name or root.name, "schema_version": 1}),
                encoding="utf-8",
            )
        policy_file = meta / "policies.yaml"
        if not policy_file.exists():
            policy_file.write_text(DEFAULT_POLICY_YAML, encoding="utf-8")
        return root

    @contextmanager
    def engine_lock(self) -> Iterator[None]:
        """Exclusive, crash-safe lock: the OS releases it when the holder dies (flock / msvcrt.locking)."""
        self.meta.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.meta / "engine.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                _lock_exclusive(fd)
            except OSError as exc:
                raise EngineLocked(
                    "another Saarathi engine is driving this project", hint="wait for it or stop it first"
                ) from exc
            os.lseek(fd, 1, os.SEEK_SET)  # byte 0 is the lock byte on Windows; the pid follows it
            os.write(fd, str(os.getpid()).encode().ljust(16))
            yield
        finally:
            os.close(fd)

    def close(self) -> None:
        self.store.close()


def _lock_exclusive(fd: int) -> None:
    """Non-blocking exclusive lock; raises OSError if another process holds it."""
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
