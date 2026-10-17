"""Run and task state machines. Every transition in the system is checked here."""

from __future__ import annotations

from enum import StrEnum

from saarathi.core.errors import InvalidTransition


class RunState(StrEnum):
    CREATED = "CREATED"
    INTAKE = "INTAKE"
    CLARIFYING = "CLARIFYING"
    PLANNING = "PLANNING"
    AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    READY_TO_SHIP = "READY_TO_SHIP"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


RUN_TERMINAL = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})
TASK_TERMINAL = frozenset({TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED})
TASK_IN_FLIGHT = frozenset({TaskState.LEASED, TaskState.RUNNING, TaskState.VERIFYING})

# States from which a run may be paused, and to which it may resume.
RUN_PAUSABLE = frozenset({RunState.INTAKE, RunState.PLANNING, RunState.EXECUTING, RunState.VERIFYING})

_R = RunState
_RUN_EDGES: dict[RunState, frozenset[RunState]] = {
    _R.CREATED: frozenset({_R.INTAKE}),
    _R.INTAKE: frozenset({_R.CLARIFYING, _R.PLANNING}),
    _R.CLARIFYING: frozenset({_R.PLANNING}),
    _R.PLANNING: frozenset({_R.AWAITING_PLAN_APPROVAL}),
    _R.AWAITING_PLAN_APPROVAL: frozenset({_R.EXECUTING, _R.PLANNING}),
    _R.EXECUTING: frozenset({_R.VERIFYING}),
    _R.VERIFYING: frozenset({_R.AWAITING_REVIEW}),
    _R.AWAITING_REVIEW: frozenset({_R.READY_TO_SHIP}),
    _R.READY_TO_SHIP: frozenset({_R.COMPLETED}),
    _R.PAUSED: RUN_PAUSABLE,
}

_T = TaskState
_TASK_EDGES: dict[TaskState, frozenset[TaskState]] = {
    _T.PENDING: frozenset({_T.READY, _T.BLOCKED}),
    _T.READY: frozenset({_T.LEASED, _T.BLOCKED}),
    _T.LEASED: frozenset({_T.RUNNING, _T.READY, _T.RETRY_WAIT, _T.FAILED}),
    _T.RUNNING: frozenset({_T.VERIFYING, _T.RETRY_WAIT, _T.FAILED, _T.BLOCKED}),
    _T.VERIFYING: frozenset({_T.SUCCEEDED, _T.RETRY_WAIT, _T.FAILED}),
    _T.RETRY_WAIT: frozenset({_T.READY, _T.FAILED, _T.BLOCKED}),
    _T.BLOCKED: frozenset({_T.READY, _T.FAILED}),
}


def run_transition_allowed(current: RunState, target: RunState) -> bool:
    if current in RUN_TERMINAL:
        return False
    if target in (RunState.FAILED, RunState.CANCELLED):
        return True
    if target is RunState.PAUSED:
        return current in RUN_PAUSABLE
    return target in _RUN_EDGES.get(current, frozenset())


def task_transition_allowed(current: TaskState, target: TaskState) -> bool:
    if current in TASK_TERMINAL:
        return False
    if target is TaskState.CANCELLED:
        return True
    return target in _TASK_EDGES.get(current, frozenset())


def check_run_transition(current: RunState, target: RunState) -> None:
    if not run_transition_allowed(current, target):
        raise InvalidTransition(f"run cannot move from {current} to {target}")


def check_task_transition(current: TaskState, target: TaskState) -> None:
    if not task_transition_allowed(current, target):
        raise InvalidTransition(f"task cannot move from {current} to {target}")
