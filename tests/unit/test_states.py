from itertools import pairwise

import pytest

from saarathi.core.errors import InvalidTransition
from saarathi.core.states import (
    RunState,
    TaskState,
    check_run_transition,
    check_task_transition,
    run_transition_allowed,
    task_transition_allowed,
)

HAPPY_RUN = [
    RunState.CREATED,
    RunState.INTAKE,
    RunState.CLARIFYING,
    RunState.PLANNING,
    RunState.AWAITING_PLAN_APPROVAL,
    RunState.EXECUTING,
    RunState.VERIFYING,
    RunState.AWAITING_REVIEW,
    RunState.READY_TO_SHIP,
    RunState.COMPLETED,
]


def test_run_happy_path_is_allowed():
    for a, b in pairwise(HAPPY_RUN):
        check_run_transition(a, b)


@pytest.mark.parametrize("terminal", [RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED])
def test_terminal_runs_never_move(terminal):
    for target in RunState:
        assert not run_transition_allowed(terminal, target)


def test_run_cannot_skip_plan_approval():
    with pytest.raises(InvalidTransition):
        check_run_transition(RunState.PLANNING, RunState.EXECUTING)


def test_pause_only_from_working_states_and_resume_back():
    assert run_transition_allowed(RunState.EXECUTING, RunState.PAUSED)
    assert not run_transition_allowed(RunState.AWAITING_REVIEW, RunState.PAUSED)
    assert run_transition_allowed(RunState.PAUSED, RunState.EXECUTING)
    assert not run_transition_allowed(RunState.PAUSED, RunState.COMPLETED)


def test_task_happy_path_and_guards():
    path = [
        TaskState.PENDING,
        TaskState.READY,
        TaskState.LEASED,
        TaskState.RUNNING,
        TaskState.VERIFYING,
        TaskState.SUCCEEDED,
    ]
    for a, b in pairwise(path):
        check_task_transition(a, b)
    with pytest.raises(InvalidTransition):
        check_task_transition(TaskState.PENDING, TaskState.RUNNING)  # cannot bypass READY/LEASED
    with pytest.raises(InvalidTransition):
        check_task_transition(TaskState.RUNNING, TaskState.SUCCEEDED)  # cannot bypass verification
    assert not task_transition_allowed(TaskState.SUCCEEDED, TaskState.READY)
    assert task_transition_allowed(TaskState.RUNNING, TaskState.CANCELLED)
