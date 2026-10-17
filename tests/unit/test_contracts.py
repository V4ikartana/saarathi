import pytest
from pydantic import ValidationError

from saarathi.core.contracts import Plan, topological_order


def task(id_, deps=(), paths=("src/**",)):
    return {
        "id": id_,
        "title": id_,
        "objective": "do it",
        "acceptance_criteria": ["works"],
        "dependencies": list(deps),
        "allowed_paths": list(paths),
        "verification_commands": ["pytest -q"],
        "required_capabilities": ["code_generation"],
        "complexity": "routine",
        "risk": "low",
    }


def plan(*tasks):
    return {
        "summary": "s",
        "architecture_notes": "a",
        "tasks": list(tasks),
        "integration_checks": ["pytest -q"],
        "decisions": [],
    }


def test_valid_plan_orders_dependencies():
    p = Plan.model_validate(plan(task("T-002", ["T-001"]), task("T-001"), task("T-003")))
    order = topological_order(p.tasks)
    assert sorted(order) == ["T-001", "T-002", "T-003"]
    assert order.index("T-001") < order.index("T-002")


@pytest.mark.parametrize(
    "bad",
    [
        plan(task("T-001", ["T-002"]), task("T-002", ["T-001"])),  # cycle
        plan(task("T-001", ["T-009"])),  # unknown dependency
        plan(task("T-001"), task("T-001")),  # duplicate
        plan(task("T-001", paths=["**"])),  # unbounded scope
        plan(task("T-001", paths=["../etc/*"])),  # escapes repo
        plan(task("bad-id")),
    ],
)
def test_invalid_plans_rejected(bad):
    with pytest.raises(ValidationError):
        Plan.model_validate(bad)


def test_unknown_capability_is_not_supported():
    from saarathi.core.contracts import ModelCapabilities

    caps = ModelCapabilities(code_generation=True)
    assert caps.supports("code_generation")
    assert not caps.supports("structured_output")  # None == unknown
    assert not caps.supports("nonexistent")
