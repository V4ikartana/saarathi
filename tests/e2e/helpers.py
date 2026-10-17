"""Shared builders for end-to-end tests with scripted fake providers."""

import json
import subprocess
from pathlib import Path

import yaml

HEALTH = 'def health():\n    return {"status": "ok"}\n'
HEALTH_TEST = (
    "import unittest\nfrom app.health import health\n\n\nclass T(unittest.TestCase):\n"
    "    def test_ok(self):\n        self.assertEqual(health(), {'status': 'ok'})\n"
)
WIDGET = 'def render(status):\n    return f"<span class=status>{status}</span>"\n'
WIDGET_TEST = (
    "import unittest\nfrom ui.widget import render\n\n\nclass T(unittest.TestCase):\n"
    "    def test_render(self):\n        self.assertIn('ok', render('ok'))\n"
)


def task(tid, title, paths, check, deps=()):
    return {
        "id": tid,
        "title": title,
        "objective": title,
        "acceptance_criteria": [f"{title} works"],
        "dependencies": list(deps),
        "allowed_paths": paths,
        "verification_commands": [check],
        "required_capabilities": ["code_generation"],
        "complexity": "routine",
        "risk": "low",
    }


PLAN = {
    "summary": "Add a health function and a status widget.",
    "architecture_notes": "Two independent modules with unit tests.",
    "tasks": [
        task(
            "T-001",
            "Health endpoint",
            ["app/**", "tests/test_health.py"],
            "python3 -m unittest tests.test_health",
        ),
        task(
            "T-002",
            "Status widget",
            ["ui/**", "tests/test_widget.py"],
            "python3 -m unittest tests.test_widget",
        ),
    ],
    "integration_checks": ["python3 -m unittest discover -s tests -t ."],
    "decisions": [{"title": "Keep modules dependency-free", "context": "Tiny demo", "decision": "Stdlib only"}],
}

GOOD_T1 = {"write": {"app/__init__.py": "", "app/health.py": HEALTH, "tests/test_health.py": HEALTH_TEST}}
GOOD_T2 = {"write": {"ui/__init__.py": "", "ui/widget.py": WIDGET, "tests/test_widget.py": WIDGET_TEST}}


def make_script(path: Path, execute: dict, *, intake=None, plan=None, review=None) -> Path:
    script = {
        "complete": {
            "lead.intake": intake or {"questions": [], "assumptions": ["stdlib only"]},
            "lead.plan": plan or PLAN,
            "lead.review": review or {"decision": "approve", "summary": "Meets the plan.", "concerns": []},
        },
        "execute": execute,
    }
    path.write_text(json.dumps(script))
    return path


def init_project(repo: Path, script: Path, record: Path, *, policy_overrides=None) -> Path:
    from saarathi.orchestration.project import Project

    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "__init__.py").write_text("")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "tests pkg"], cwd=repo, check=True)
    Project.init(repo)
    options = {"script": str(script), "record_path": str(record)}
    policy = {
        "version": 1,
        "mode": "subscription",
        "orchestration": {"lead": {"adapter": "fake-claude", "model": "opus"}},
        "routing": {
            "allowed_providers": ["anthropic", "openai"],
            "allowed_models": ["anthropic/*", "openai/*"],
            "max_parallel_workers": 2,
        },
        "execution": {"max_attempts": 2, "task_timeout_seconds": 60, "verification_timeout_seconds": 60},
        "providers": {
            "fake-claude": {
                "type": "fake",
                "provider": "anthropic",
                "options": options,
                "models": [{"id": "opus", "reasoning_tier": "frontier"}],
            },
            "fake-codex": {
                "type": "fake",
                "provider": "openai",
                "options": options,
                "models": [{"id": "default", "reasoning_tier": "unknown"}],
            },
        },
    }
    for key, value in (policy_overrides or {}).items():
        policy[key] = {**policy.get(key, {}), **value} if isinstance(value, dict) else value
    (repo / ".saarathi" / "policies.yaml").write_text(yaml.safe_dump(policy))
    return repo


def records(record: Path):
    return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []
