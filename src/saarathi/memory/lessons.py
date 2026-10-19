"""Promoting worker lesson proposals into ``.saarathi/lessons.md`` (backlog P7, ADR-0007).

Only proposals from attempts that passed verification are recorded, and none reaches lessons.md
without a person's approval. Writes use the memory store's optimistic version check, so two
concurrent approvals cannot silently overwrite each other.
"""

from __future__ import annotations

from datetime import UTC, datetime

from saarathi.core.errors import InvalidTransition
from saarathi.memory.store import ProjectMemory
from saarathi.storage.store import LessonProposal, StateStore

LESSONS = "lessons.md"
HEADER = "# Lessons\n\nValidated lessons only. Agent proposals are not added automatically.\n"


def approve_lesson(
    store: StateStore, memory: ProjectMemory, lesson_id: str, *, text: str | None = None, actor: str = "user"
) -> LessonProposal:
    lesson = store.get_lesson(lesson_id)
    if lesson.status != "proposed":
        raise InvalidTransition(f"lesson {lesson_id} is already {lesson.status}")
    body = " ".join((text if text is not None else lesson.text).split())
    if not body:
        raise ValueError("a lesson cannot be empty")
    doc = memory.read(LESSONS)
    current = doc.content if doc else HEADER
    if body not in current:  # idempotent if a previous approval wrote the file but crashed before recording it
        today = datetime.now(UTC).date().isoformat()
        entry = f"- {body} _(from {lesson.task_id} in {lesson.run_id}; approved {today})_\n"
        memory.write(LESSONS, current.rstrip("\n") + "\n" + entry, expected_version=doc.version if doc else None)
    return store.decide_lesson(lesson_id, status="approved", actor=actor, text=text)


def approved_lessons(memory: ProjectMemory) -> tuple[str, str, str] | None:
    """(path, version, content) of lessons.md when it holds at least one approved lesson."""
    doc = memory.read(LESSONS)
    if doc is None or not any(line.startswith("- ") for line in doc.content.splitlines()):
        return None
    return (f".saarathi/{LESSONS}", doc.version, doc.content)
