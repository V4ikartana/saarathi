"""Command policy and verification runs. Saarathi, not the worker, runs every check.

Verification commands execute repository code with the user's OS permissions (filtered
environment, no shell, timeout). They are not sandboxed; see docs/security/threat-model.md.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

from saarathi.core.contracts import CommandEvidence
from saarathi.core.policy import PermissionsConfig
from saarathi.runtime.process import run_process, write_log
from saarathi.security.env import filtered_env
from saarathi.security.redaction import Redactor

SHELL_OPERATORS = ("&&", "||", ";", "|", ">", "<", "`", "$(", "\n")
NETWORK_TOOLS = frozenset({"curl", "wget", "ssh", "scp", "sftp", "nc", "ncat", "telnet", "rsync", "ftp"})
DEPLOY_TOOLS = frozenset(
    {
        "kubectl",
        "terraform",
        "helm",
        "vercel",
        "fly",
        "flyctl",
        "heroku",
        "gcloud",
        "aws",
        "az",
        "netlify",
        "serverless",
    }
)
DESTRUCTIVE_TOOLS = frozenset(
    {
        "rm",
        "rmdir",
        "dd",
        "mkfs",
        "chmod",
        "chown",
        "sudo",
        "su",
        "doas",
        "shred",
        "kill",
        "killall",
        "pkill",
        "launchctl",
        "systemctl",
        "crontab",
    }
)
PACKAGE_MANAGERS = frozenset(
    {
        "pip",
        "pip3",
        "uv",
        "npm",
        "yarn",
        "pnpm",
        "poetry",
        "pipenv",
        "gem",
        "cargo",
        "go",
        "brew",
        "apt",
        "apt-get",
        "dnf",
        "yum",
        "apk",
        "conda",
        "mamba",
        "bun",
        "composer",
    }
)
INSTALL_VERBS = frozenset({"install", "i", "add", "ci", "update", "upgrade", "sync", "get"})
INLINE_CODE_FLAGS = {
    "sh": "-c",
    "bash": "-c",
    "zsh": "-c",
    "python": "-c",
    "python3": "-c",
    "node": "-e",
    "ruby": "-e",
    "perl": "-e",
}
GIT_READ_ONLY = frozenset({"status", "diff", "log", "show", "ls-files", "rev-parse"})


def check_command(command: str, permissions: PermissionsConfig) -> str | None:
    """Return a rejection reason, or None if the command is allowed by policy."""
    if any(op in command for op in SHELL_OPERATORS):
        return "shell operators are not supported; list each command separately"
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"cannot parse command: {exc}"
    if not argv:
        return "empty command"
    exe = Path(argv[0]).name
    args = argv[1:]
    if "/" in argv[0] and not argv[0].startswith(("./", "bin/", "scripts/")):
        return "absolute or parent-relative executables are not allowed"
    if exe in NETWORK_TOOLS and not permissions.allow_network:
        return f"'{exe}' needs network access (permissions.allow_network is false)"
    if exe in DEPLOY_TOOLS and not permissions.allow_deployment:
        return f"'{exe}' is a deployment tool (permissions.allow_deployment is false)"
    if exe in DESTRUCTIVE_TOOLS:
        return f"'{exe}' is not allowed in verification commands"
    if exe in INLINE_CODE_FLAGS and INLINE_CODE_FLAGS[exe] in args:
        return f"inline code via '{exe} {INLINE_CODE_FLAGS[exe]}' is not allowed"
    if exe == "git" and (not args or args[0] not in GIT_READ_ONLY):
        return "only read-only git subcommands are allowed"
    tokens = {Path(a).name for a in argv}
    if (tokens & PACKAGE_MANAGERS) and (set(args) & INSTALL_VERBS) and not permissions.allow_dependency_install:
        return "dependency installation is not allowed (permissions.allow_dependency_install is false)"
    return None


def check_commands(commands: Sequence[str], permissions: PermissionsConfig) -> list[str]:
    problems = []
    for cmd in commands:
        reason = check_command(cmd, permissions)
        if reason:
            problems.append(f"{cmd!r}: {reason}")
    return problems


async def run_verification(
    commands: Sequence[str],
    *,
    cwd: Path,
    permissions: PermissionsConfig,
    timeout_s: float,
    log_dir: Path,
    env_passthrough: Sequence[str] = (),
    max_output_bytes: int = 2_000_000,
) -> list[CommandEvidence]:
    redactor = Redactor()
    evidence: list[CommandEvidence] = []
    env = filtered_env(env_passthrough, overrides={"CI": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    for index, command in enumerate(commands, start=1):
        reason = check_command(command, permissions)
        if reason:
            evidence.append(
                CommandEvidence(
                    command=command,
                    exit_code=None,
                    duration_s=0.0,
                    timed_out=False,
                    output_tail="",
                    rejected_reason=reason,
                )
            )
            break
        log_path = log_dir / f"verify-{index:02d}.log"
        try:
            result = await run_process(
                shlex.split(command), cwd=cwd, env=env, timeout_s=timeout_s, max_output_bytes=max_output_bytes
            )
        except FileNotFoundError:
            evidence.append(
                CommandEvidence(
                    command=command,
                    exit_code=127,
                    duration_s=0.0,
                    timed_out=False,
                    output_tail=f"executable not found: {shlex.split(command)[0]}",
                )
            )
            break
        write_log(log_path, result, redactor)
        tail = redactor((result.stdout + "\n" + result.stderr).strip()[-4000:])
        item = CommandEvidence(
            command=command,
            exit_code=result.exit_code,
            duration_s=result.duration_s,
            timed_out=result.timed_out,
            output_tail=tail,
            log_path=str(log_path),
        )
        evidence.append(item)
        if not item.passed:
            break  # later commands would run against a known-bad state
    return evidence


def all_passed(evidence: Sequence[CommandEvidence], expected: int) -> bool:
    """True only with complete, passing evidence for every expected command."""
    return len(evidence) == expected and expected > 0 and all(e.passed for e in evidence)
