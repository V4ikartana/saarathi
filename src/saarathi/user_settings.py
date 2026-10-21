"""Settings that belong to the person, not to a project: kept in ``<data dir>/settings.json``.

It holds the models the person has turned off and the tiers they have chosen for models.

- A turned-off model is unavailable in every project, for the CLI and the desktop app alike: the router never chooses
  it, a session cannot prefer or pin it, and it cannot act as the lead.
- A chosen tier (frontier, balanced or economy) replaces the tier from the policy or the model catalogue in every
  project, so a person can call any model they trust "frontier". It changes routing and lead checks, never which
  models a project's policy allows.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from saarathi.git.workspace import data_dir

DISABLED_REASON = "turned off in your Saarathi settings"
TIERS = ("frontier", "balanced", "economy")
TIER_SOURCE = "tier set by you in Saarathi settings"


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
    _check_candidate(candidate)
    data = load()
    current = disabled_models()
    current.discard(candidate) if enabled else current.add(candidate)
    data["disabled_models"] = sorted(current)
    _save(data)
    return current


def _check_candidate(candidate: str) -> None:
    if ":" not in candidate or not candidate.split(":", 1)[1]:
        raise ValueError(f"model must be written adapter:model, got '{candidate}'")


def model_tiers() -> dict[str, str]:
    """The tiers the person chose, by ``adapter:model``. Unknown values in the file are ignored."""
    raw = load().get("model_tiers", {})
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v in TIERS}


def set_model_tier(candidate: str, tier: str | None) -> dict[str, str]:
    """Choose a model's tier for every project, or pass None to go back to its default. Returns all chosen tiers."""
    _check_candidate(candidate)
    if tier is not None and tier not in TIERS:
        raise ValueError(f"tier must be one of {', '.join(TIERS)}, got '{tier}'")
    data = load()
    tiers = model_tiers()
    if tier is None:
        tiers.pop(candidate, None)
    else:
        tiers[candidate] = tier
    data["model_tiers"] = dict(sorted(tiers.items()))
    _save(data)
    return tiers
