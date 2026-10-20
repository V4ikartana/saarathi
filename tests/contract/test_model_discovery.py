"""Model discovery from the providers' own CLIs: `codex debug models` and `agy models`.

Discovered models are listed after the declared ones with an unknown tier, never replace a declaration, and any
discovery failure falls back to the declared models with a notice.
"""

import json

import pytest

from saarathi.core.contracts import ReasoningTier
from saarathi.core.policy import DeclaredModel, ProviderConfig
from saarathi.providers import antigravity_cli, codex_cli
from saarathi.providers.antigravity_cli import AntigravityCliAdapter
from saarathi.providers.codex_cli import CodexCliAdapter, parse_codex_models

CATALOG = {
    "models": [
        {"slug": "gpt-6-sol", "display_name": "GPT-6-Sol", "visibility": "list", "priority": 4},
        {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1-Sol", "visibility": "list", "priority": 1},
        {"slug": "gpt-reserve", "display_name": "GPT-Reserve", "visibility": "hide", "priority": 2},
        {"slug": "bad slug!", "visibility": "list", "priority": 3},
    ]
}


def test_codex_catalog_keeps_picker_models_in_codex_order():
    found = parse_codex_models(json.dumps(CATALOG))
    assert [(m.id, m.display_name) for m in found] == [("gpt-6.1-sol", "GPT-6.1-Sol"), ("gpt-6-sol", "GPT-6-Sol")]


HIDDEN_ONLY = '{"models": [{"slug": "x", "visibility": "hide"}]}'


@pytest.mark.parametrize("text", ["not json", "[]", '{"models": {}}', HIDDEN_ONLY])
def test_codex_catalog_shape_changes_are_refused(text):
    with pytest.raises(ValueError):
        parse_codex_models(text)


def fake_codex(monkeypatch, debug_models):
    exec_help = " ".join(codex_cli.REQUIRED_EXEC_FLAGS)

    def run_quick(argv, env, timeout=20.0):
        args = argv[1:]
        if args == ["--version"]:
            return 0, "codex-cli 0.163.0", ""
        if args == ["exec", "--help"]:
            return 0, exec_help, ""
        if args == ["login", "status"]:
            return 0, "Logged in using ChatGPT", ""
        if args == ["debug", "models"]:
            return debug_models
        raise AssertionError(f"unexpected command {args}")

    monkeypatch.setattr(codex_cli, "run_quick", run_quick)
    monkeypatch.setattr(CodexCliAdapter, "_binary", lambda self: "/fake/codex")


def test_codex_lists_declared_then_discovered_models(monkeypatch):
    fake_codex(monkeypatch, (0, json.dumps(CATALOG), ""))
    cfg = ProviderConfig(models=[DeclaredModel(id="default"), DeclaredModel(id="gpt-6-sol", reasoning_tier="frontier")])
    models = CodexCliAdapter("codex-cli", cfg).list_models()
    assert [m.id for m in models] == ["default", "gpt-6-sol", "gpt-6.1-sol"]
    by_id = {m.id: m for m in models}
    assert by_id["gpt-6-sol"].capabilities.reasoning_tier is ReasoningTier.FRONTIER, "a declaration wins"
    found = by_id["gpt-6.1-sol"]
    assert found.capabilities.reasoning_tier is ReasoningTier.UNKNOWN and found.available
    assert found.metadata.source == "discovered: codex debug models" and "GPT-6.1-Sol" in found.metadata.notes
    assert not found.pricing.metered and found.candidate_id == "codex-cli:gpt-6.1-sol"


def test_codex_discovery_failure_keeps_declared_models_and_says_why(monkeypatch):
    fake_codex(monkeypatch, (1, "", "error: unknown subcommand 'debug'"))
    adapter = CodexCliAdapter("codex-cli", ProviderConfig(models=[DeclaredModel(id="default")]))
    probe = adapter.probe()
    assert probe.available, "discovery is optional: the adapter still works"
    assert [m.id for m in adapter.list_models()] == ["default"]
    assert any("Model discovery unavailable" in n and "unknown subcommand" in n for n in probe.notices)


def test_discovery_can_be_turned_off_in_policy(monkeypatch):
    fake_codex(monkeypatch, (0, json.dumps(CATALOG), ""))
    cfg = ProviderConfig(models=[DeclaredModel(id="default")], options={"discover_models": False})
    assert [m.id for m in CodexCliAdapter("codex-cli", cfg).list_models()] == ["default"]


def test_antigravity_lists_offered_models_after_declared_ones(monkeypatch):
    def run_quick(argv, env, timeout=20.0):
        args = argv[1:]
        if args == ["--version"]:
            return 0, "agy 1.4.0", ""
        if args == ["--help"]:
            return 0, " ".join(antigravity_cli.REQUIRED_FLAGS), ""
        if args == ["models"]:
            return 0, "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\ngemini-3.8-pro\tGemini 3.8 Pro\n", ""
        raise AssertionError(f"unexpected command {args}")

    monkeypatch.setattr(antigravity_cli, "run_quick", run_quick)
    monkeypatch.setattr(AntigravityCliAdapter, "_binary", lambda self: "/fake/agy")
    pro = DeclaredModel(id="gemini-3.8-pro", reasoning_tier="frontier")
    cfg = ProviderConfig(models=[DeclaredModel(id="default"), pro])
    models = {m.id: m for m in AntigravityCliAdapter("antigravity-cli", cfg).list_models()}
    assert list(models) == ["default", "gemini-3.8-pro", "gemini-3.8-flash-high"]
    assert models["gemini-3.8-pro"].capabilities.reasoning_tier is ReasoningTier.FRONTIER
    flash = models["gemini-3.8-flash-high"]
    assert flash.available and flash.metadata.source == "discovered: agy models"
    assert "Gemini 3.8 Flash (High)" in flash.metadata.notes
