import os
import sys

import pytest

from saarathi.runtime.binaries import cli_env, engine_command, stable_path


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra privileges on Windows; fnm paths are POSIX")
def test_per_shell_directories_resolve_to_the_real_installation(tmp_path):
    real = tmp_path / "fnm" / "node-versions" / "v24" / "bin"
    real.mkdir(parents=True)
    (real / "codex").write_text("#!/usr/bin/env node\n")
    shell = tmp_path / "state" / "fnm_multishells" / "123_456"
    shell.parent.mkdir(parents=True)
    os.symlink(real, shell)
    assert stable_path(str(shell / "codex")) == str(real.resolve() / "codex")
    assert stable_path("/usr/local/bin/claude") == "/usr/local/bin/claude", "ordinary paths are untouched"


def test_a_cli_runs_with_its_own_directory_first_on_path(tmp_path):
    exe = tmp_path / "bin" / "codex"
    env = cli_env([str(exe), "exec"], {"PATH": os.pathsep.join(["/usr/bin", "/bin"])})
    assert env["PATH"].split(os.pathsep)[0] == str(exe.parent)
    assert cli_env(["python3", "-m", "x"], {"PATH": "/usr/bin"}) == {"PATH": "/usr/bin"}, "relative commands unchanged"


def test_the_engine_restarts_itself_as_a_module_or_a_frozen_binary(monkeypatch):
    assert engine_command() == [sys.executable, "-m", "saarathi"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert engine_command() == [sys.executable]
