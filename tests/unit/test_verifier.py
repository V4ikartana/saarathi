import asyncio

import pytest

from saarathi.core.policy import PermissionsConfig
from saarathi.verification.verifier import all_passed, check_command, run_verification

DEFAULT = PermissionsConfig()


@pytest.mark.parametrize(
    "cmd",
    [
        "pytest -q",
        "python -m pytest tests",
        "npm test",
        "uv run pytest",
        "ruff check .",
        "git diff --stat",
        "go test ./...",
    ],
)
def test_allowed(cmd):
    assert check_command(cmd, DEFAULT) is None


@pytest.mark.parametrize(
    "cmd,fragment",
    [
        ("curl https://evil.example", "network"),
        ("pip install requests", "dependency"),
        ("python -m pip install x", "dependency"),
        ("npm install left-pad", "dependency"),
        ("rm -rf /", "not allowed"),
        ("pytest && curl x", "shell operators"),
        ("bash -c 'echo hi'", "inline code"),
        ("git push origin main", "read-only git"),
        ("kubectl apply -f x", "deployment"),
        ("/usr/bin/env pytest", "absolute"),
    ],
)
def test_rejected(cmd, fragment):
    reason = check_command(cmd, DEFAULT)
    assert reason and fragment in reason


def test_permissions_unlock_network():
    assert check_command("curl https://example.com", PermissionsConfig(allow_network=True)) is None


def test_run_verification_stops_on_failure_and_records_evidence(tmp_path):
    (tmp_path / "ok.py").write_text("print('fine')")
    (tmp_path / "bad.py").write_text("import sys; sys.exit(3)")
    # The interpreter must be resolvable on the filtered PATH; use python3 which CI images provide.
    cmds = ["python3 ok.py", "python3 bad.py", "python3 ok.py"]
    ev = asyncio.run(run_verification(cmds, cwd=tmp_path, permissions=DEFAULT, timeout_s=30, log_dir=tmp_path / "l"))
    assert [e.exit_code for e in ev] == [0, 3]
    assert not all_passed(ev, len(cmds))
    assert "fine" in ev[0].output_tail


def test_rejected_command_is_evidence_not_execution(tmp_path):
    ev = asyncio.run(
        run_verification(["curl http://x"], cwd=tmp_path, permissions=DEFAULT, timeout_s=5, log_dir=tmp_path)
    )
    assert ev[0].rejected_reason and ev[0].exit_code is None and not all_passed(ev, 1)


def test_no_commands_is_not_success():
    assert not all_passed([], 0)
