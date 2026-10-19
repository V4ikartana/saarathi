"""Project memory under ``.saarathi/`` (ADR-0007).

Documents are human-readable Markdown/JSON. Writes use optimistic concurrency (callers pass the
SHA-256 of the version they read). ADR files are append-only: only their status line may change,
through an explicit, logged operation.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from saarathi.core.contracts import slugify
from saarathi.core.errors import ConfigError, VersionConflict

ADR_DIR = "decisions"
STATUS_LINE = re.compile(r"^- Status: (?P<status>.+)$", re.MULTILINE)
ADR_STATUS_TRANSITIONS = {
    "Proposed": {"Accepted", "Rejected"},
    "Accepted": {"Superseded"},
}

GITIGNORE = """\
# Runtime state: never commit
state.db
state.db-*
engine.lock
logs/
"""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Document:
    path: str
    content: str
    version: str


@dataclass(frozen=True)
class AdrInfo:
    number: int
    title: str
    status: str
    path: str
    version: str


class ProjectMemory:
    def __init__(self, meta_dir: Path) -> None:
        self.dir = meta_dir

    def _resolve(self, rel: str) -> Path:
        target = (self.dir / rel).resolve()
        if not target.is_relative_to(self.dir.resolve()):
            raise ConfigError(f"memory path escapes .saarathi/: {rel}")
        return target

    def read(self, rel: str) -> Document | None:
        path = self._resolve(rel)
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        return Document(rel, content, sha256(content))

    def write(self, rel: str, content: str, *, expected_version: str | None) -> Document:
        """Write ``rel`` if its current version equals ``expected_version`` (None: must not exist)."""
        if rel.startswith(f"{ADR_DIR}/"):
            raise ConfigError("ADRs are append-only; use add_adr / set_adr_status")
        current = self.read(rel)
        current_version = current.version if current else None
        if current_version != expected_version:
            raise VersionConflict(
                f"memory document {rel} changed (expected {expected_version}, found {current_version})"
            )
        self._atomic_write(self._resolve(rel), content)
        return Document(rel, content, sha256(content))

    def write_new_or_same(self, rel: str, content: str) -> Document:
        """Create ``rel``; succeed without change if identical content exists, else conflict."""
        current = self.read(rel)
        if current is not None:
            if current.content == content:
                return current
            raise VersionConflict(f"immutable memory document {rel} already exists with different content")
        return self.write(rel, content, expected_version=None)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:  # LF on every platform
                fh.write(content)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------ ADRs

    def list_adrs(self) -> list[AdrInfo]:
        out = []
        for path in sorted((self.dir / ADR_DIR).glob("ADR-*.md")):
            content = path.read_text(encoding="utf-8")
            match = re.match(r"ADR-(\d{4})", path.name)
            status = STATUS_LINE.search(content)
            title = content.splitlines()[0].split(":", 1)[-1].strip() if content else path.stem
            if match:
                out.append(
                    AdrInfo(
                        int(match.group(1)),
                        title,
                        status.group("status") if status else "Unknown",
                        f"{ADR_DIR}/{path.name}",
                        sha256(content),
                    )
                )
        return out

    def add_adr(self, title: str, context: str, decision: str, *, date: str, supersedes: int | None = None) -> AdrInfo:
        number = max((a.number for a in self.list_adrs()), default=0) + 1
        rel = f"{ADR_DIR}/ADR-{number:04d}-{slugify(title)}.md"
        lines = [f"# ADR-{number:04d}: {title}", "", "- Status: Proposed", f"- Date: {date}"]
        if supersedes:
            lines.append(f"- Supersedes: ADR-{supersedes:04d}")
        lines += ["", "## Context", "", context.strip(), "", "## Decision", "", decision.strip(), ""]
        content = "\n".join(lines)
        path = self._resolve(rel)
        if path.exists():
            raise VersionConflict(f"{rel} already exists; ADRs are never overwritten")
        self._atomic_write(path, content)
        return AdrInfo(number, title, "Proposed", rel, sha256(content))

    def set_adr_status(self, number: int, status: str, *, expected_version: str) -> AdrInfo:
        adr = next((a for a in self.list_adrs() if a.number == number), None)
        if adr is None:
            raise ConfigError(f"ADR-{number:04d} not found")
        if adr.version != expected_version:
            raise VersionConflict(f"ADR-{number:04d} changed since it was read")
        base_status = adr.status.split(" by ")[0]
        target_base = status.split(" by ")[0]
        if target_base not in ADR_STATUS_TRANSITIONS.get(base_status, set()):
            raise ConfigError(f"ADR-{number:04d} cannot move from {adr.status} to {status}")
        path = self._resolve(adr.path)
        content = path.read_text(encoding="utf-8")
        updated = STATUS_LINE.sub(f"- Status: {status}", content, count=1)
        self._atomic_write(path, updated)
        return AdrInfo(number, adr.title, status, adr.path, sha256(updated))


def init_layout(meta_dir: Path) -> None:
    for sub in (ADR_DIR, "tasks", "checkpoints", "reports"):
        (meta_dir / sub).mkdir(parents=True, exist_ok=True)
    gitignore = meta_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE, encoding="utf-8")
    lessons = meta_dir / "lessons.md"
    if not lessons.exists():
        lessons.write_text(
            "# Lessons\n\nValidated lessons only. Agent proposals are not added automatically.\n",
            encoding="utf-8",
        )
