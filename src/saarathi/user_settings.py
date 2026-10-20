"""Settings that belong to the person, not to a project: kept in ``<data dir>/settings.json``.

Today this holds the models the person has turned off. A turned-off model is unavailable in every project, for the
CLI and the desktop app alike: the router never chooses it, a session cannot prefer or pin it, and it cannot act as
the lead. Settings only ever narrow what a project's policy allows.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from saarathi.git.workspace import data_dir

DISABLED_REASON = "turned off in your Saarathi settings"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)  # atomic: a reader never sees a half-written file


def disabled_models() -> set[str]:
    raw = load().get("disabled_models", [])
    return {str(m) for m in raw} if isinstance(raw, list) else set()


def set_model_enabled(candidate: str, enabled: bool) -> set[str]:
    """Turn a model (``adapter:model``) on or off for every project. Returns the models now turned off."""
    if ":" not in candidate or not candidate.split(":", 1)[1]:
        raise ValueError(f"model must be written adapter:model, got '{candidate}'")
    data = load()
    current = disabled_models()
    current.discard(candidate) if enabled else current.add(candidate)
    data["disabled_models"] = sorted(current)
    _save(data)
    return current
