"""The hand-written strict schemas must stay in the structured-output subset and in step with the contracts."""

import pytest
import yaml

from saarathi.core.contracts import IntegrationRequirement, Plan, PlannedTask
from saarathi.core.policy import DEFAULT_POLICY_YAML, parse_policy
from saarathi.orchestration import prompts
from saarathi.orchestration.lead import plan_policy_problems

SCHEMAS = {
    "intake": prompts.INTAKE_SCHEMA,
    "plan": prompts.PLAN_SCHEMA,
    "worker": prompts.WORKER_SCHEMA,
    "review": prompts.REVIEW_SCHEMA,
}


def objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from objects(value)


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_every_object_is_closed_and_fully_required(name):
    for obj in objects(SCHEMAS[name]):
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])


def test_plan_task_schema_matches_planned_task_fields():
    task_schema = prompts.PLAN_SCHEMA["properties"]["tasks"]["items"]
    assert set(task_schema["properties"]) == set(PlannedTask.model_fields)
    req_schema = task_schema["properties"]["integrations"]["items"]
    assert set(req_schema["properties"]) == set(IntegrationRequirement.model_fields)
    nullable = {k for k, v in req_schema["properties"].items() if v.get("type") == ["string", "null"]}
    assert nullable == {"account", "tenant", "resource"}


def requirement(**overrides):
    req = {
        "service": "mail.gmail",
        "operation": "mail.read",
        "account": "work",
        "tenant": None,
        "resource": None,
        "data_class": "internal",
        "identity_sensitive": True,
        "consent_required": False,
        "share_fields": ["subject"],
    }
    return {**req, **overrides}


def schema_task(tid, integrations):
    return {
        "id": tid,
        "title": "Summarise the inbox",
        "objective": "Summarise unread mail",
        "acceptance_criteria": ["summary written"],
        "dependencies": [],
        "allowed_paths": ["reports/**"],
        "verification_commands": ["pytest -q"],
        "required_capabilities": ["code_generation", "workspace_write", "structured_output"],
        "complexity": "routine",
        "risk": "low",
        "integrations": integrations,
    }


def schema_plan(*tasks):
    return {
        "summary": "s",
        "architecture_notes": "a",
        "tasks": list(tasks),
        "integration_checks": ["pytest -q"],
        "decisions": [],
    }


def default_policy():
    return parse_policy(yaml.safe_load(DEFAULT_POLICY_YAML))


def test_schema_shaped_plan_with_nulls_validates():
    plan = Plan.model_validate(schema_plan(schema_task("T-001", [requirement(account=None)])))
    req = plan.tasks[0].integrations[0]
    assert req.account is None and req.tenant is None
    assert plan_policy_problems(plan, default_policy()) == []


def test_plan_without_integrations_still_validates():
    task = schema_task("T-001", [])
    del task["integrations"]
    assert Plan.model_validate(schema_plan(task)).tasks[0].integrations == []


@pytest.mark.parametrize("account", ["work,personal", "*", "all accounts", ""])
def test_multi_account_selector_rejected_by_contract(account):
    with pytest.raises(ValueError):
        Plan.model_validate(schema_plan(schema_task("T-001", [requirement(account=account)])))


@pytest.mark.parametrize("second", ["personal", None])
def test_two_accounts_of_one_service_in_one_task_is_a_policy_problem(second):
    plan = Plan.model_validate(
        schema_plan(schema_task("T-001", [requirement(), requirement(account=second, operation="mail.search")]))
    )
    problems = plan_policy_problems(plan, default_policy())
    assert problems == ["T-001 uses several mail.gmail accounts; split it into one task per account"]


def test_one_task_per_account_passes_policy():
    plan = Plan.model_validate(
        schema_plan(schema_task("T-001", [requirement()]), schema_task("T-002", [requirement(account="personal")]))
    )
    assert plan_policy_problems(plan, default_policy()) == []


def test_plan_prompt_explains_one_account_per_requirement():
    text = prompts.plan_prompt("brief", "summary", [], [])
    assert "ONE operation on ONE account" in text
    assert "one task per account" in text
