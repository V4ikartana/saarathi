import subprocess
from pathlib import Path

import pytest


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SAARATHI_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "dev@example.com")
    git(root, "config", "user.name", "Dev")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "core.autocrlf", "false")  # byte-for-byte content checks on Windows
    git(root, "config", "core.longpaths", "true")
    (root / "README.md").write_text("# demo\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    return root
