"""P7: lesson proposals never reach lessons.md without approval; approvals are conflict-safe."""

import pytest

from saarathi.core.errors import InvalidTransition, VersionConflict
from saarathi.memory.lessons import LESSONS, approve_lesson, approved_lessons
from saarathi.memory.store import ProjectMemory, init_layout
from saarathi.storage.store import StateStore


@pytest.fixture
def env(tmp_path):
    init_layout(tmp_path / ".saarathi")
    store = StateStore(tmp_path / "state.db")
    run = store.create_run(project_id="p", brief_path="b", brief_sha256="x")
    yield store, ProjectMemory(tmp_path / ".saarathi"), run.id
    store.close()


def test_proposals_are_deduplicated_capped_and_not_applied(env):
    store, memory, run_id = env
    texts = [
        "Use  the existing ROUTES table.",
        "use the existing routes table.",
        "x" * 900,
        *[f"l{i}" for i in range(20)],
    ]
    made = store.propose_lessons(run_id, "T-001", "att-1", source="w", texts=texts, actor="t")
    assert len(made) == 9, "case/space duplicates dropped, at most 10 considered"
    assert len(made[1].text) == StateStore.MAX_LESSON_CHARS
    assert approved_lessons(memory) is None, "nothing reaches lessons.md on proposal"


def test_approve_edit_reject_and_idempotency(env):
    store, memory, run_id = env
    a, b = store.propose_lessons(
        run_id, "T-001", "att-1", source="w", texts=["Keep tests stdlib-only", "Bad idea"], actor="t"
    )
    approve_lesson(store, memory, a.id, text="Keep example tests standard-library only")
    store.decide_lesson(b.id, status="rejected", actor="user")
    content = memory.read(LESSONS).content
    assert "- Keep example tests standard-library only _(from T-001" in content and "Bad idea" not in content
    with pytest.raises(InvalidTransition):
        approve_lesson(store, memory, b.id)
    with pytest.raises(InvalidTransition):
        approve_lesson(store, memory, a.id)
    assert memory.read(LESSONS).content == content
    path, version, text = approved_lessons(memory)
    assert path == ".saarathi/lessons.md" and version and "standard-library" in text


def test_concurrent_edit_is_not_overwritten(env, monkeypatch):
    store, memory, run_id = env
    (lesson,) = store.propose_lessons(run_id, "T-001", "att-1", source="w", texts=["A lesson"], actor="t")
    stale = memory.read(LESSONS)
    memory.write(LESSONS, stale.content + "- someone else's edit\n", expected_version=stale.version)
    real_read = memory.read
    calls = []

    def first_read_is_stale(rel):  # the approval's own read is outdated; the store's check sees the truth
        calls.append(rel)
        return stale if len(calls) == 1 else real_read(rel)

    monkeypatch.setattr(memory, "read", first_read_is_stale)
    with pytest.raises(VersionConflict):
        approve_lesson(store, memory, lesson.id)
    assert store.get_lesson(lesson.id).status == "proposed"
