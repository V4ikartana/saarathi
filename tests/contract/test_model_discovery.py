"""Model discovery from the providers' own CLIs: `codex debug models` and `agy models`.

Discovered models are listed after the declared ones with an unknown tier, never replace a declaration, and any
discovery failure falls back to the declared models with a notice.
"""

import json

import pytest

from saarathi.core.contracts import ReasoningTier
from saarathi.core.policy import DeclaredModel, ProviderConfig
from saarathi.providers import antigravity_cli, claude_cli, codex_cli
from saarathi.providers.antigravity_cli import AntigravityCliAdapter
from saarathi.providers.claude_cli import ClaudeCodeAdapter
from saarathi.providers.codex_cli import CodexCliAdapter, parse_codex_models
from saarathi.providers.model_catalog import classify

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
    assert found.capabilities.reasoning_tier is ReasoningTier.BALANCED and found.available
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


@pytest.mark.parametrize(
    ("model_id", "tier"),
    [
        ("gpt-6-astra", ReasoningTier.FRONTIER),
        ("gpt-7-astra", ReasoningTier.FRONTIER),  # a future release of a known family
        ("gpt-6.1-sol", ReasoningTier.BALANCED),
        ("gpt-5.6-terra", ReasoningTier.BALANCED),
        ("gpt-6-luna", ReasoningTier.ECONOMY),
        ("gpt-oss-120b-medium", ReasoningTier.ECONOMY),
        ("opus", ReasoningTier.FRONTIER),
        ("claude-opus-4-6-thinking", ReasoningTier.FRONTIER),
        ("sonnet[1m]", ReasoningTier.BALANCED),
        ("claude-sonnet-4-6", ReasoningTier.BALANCED),
        ("haiku", ReasoningTier.ECONOMY),
        ("gemini-3.8-pro", ReasoningTier.FRONTIER),
        ("gemini-3.8-flash-high", ReasoningTier.BALANCED),
        ("gemini-3.8-flash-low", ReasoningTier.ECONOMY),
        ("gemini-3.8-flash-lite", ReasoningTier.ECONOMY),
    ],
)
def test_catalogue_classifies_model_families(model_id, tier):
    found = classify(model_id)
    assert found is not None and found.tier is tier and "catalogue" in found.basis


def test_unknown_models_stay_unknown_unless_the_provider_describes_them():
    assert classify("mystery-9") is None
    described = classify("mystery-9", "Frontier intelligence for the most demanding work.")
    assert described is not None and described.tier is ReasoningTier.FRONTIER
    assert described.basis.startswith("provider description")
    assert classify("mystery-9", "Fast and affordable model for easier tasks.").tier is ReasoningTier.ECONOMY


def test_discovered_codex_models_get_catalogue_tiers(monkeypatch):
    fake_codex(monkeypatch, (0, json.dumps(CATALOG), ""))
    adapter = CodexCliAdapter("codex-cli", ProviderConfig(models=[DeclaredModel(id="default")]))
    models = {m.id: m for m in adapter.list_models()}
    assert models["gpt-6.1-sol"].capabilities.reasoning_tier is ReasoningTier.BALANCED
    assert "Saarathi model catalogue" in models["gpt-6.1-sol"].metadata.notes
    assert models["default"].capabilities.reasoning_tier is ReasoningTier.UNKNOWN, "default is whatever Codex picks"


def fake_claude(monkeypatch, auth_method="claude.ai"):
    def run_quick(argv, env, timeout=20.0):
        args = argv[1:]
        if args == ["--version"]:
            return 0, "2.1.302 (Claude Code)", ""
        if args == ["auth", "status"]:
            return 0, json.dumps({"authMethod": auth_method}), ""
        raise AssertionError(f"unexpected command {args}")

    monkeypatch.setattr(claude_cli, "run_quick", run_quick)
    monkeypatch.setattr(ClaudeCodeAdapter, "_binary", lambda self: "/fake/claude")


def test_claude_lists_its_aliases_and_marks_paid_models_unavailable(monkeypatch):
    fake_claude(monkeypatch)
    cfg = ProviderConfig(models=[DeclaredModel(id="opus", reasoning_tier="frontier")])
    models = {m.id: m for m in ClaudeCodeAdapter("claude-code", cfg).list_models()}
    assert list(models) == ["opus", "sonnet", "haiku", "fable"]
    assert models["haiku"].available and models["haiku"].capabilities.reasoning_tier is ReasoningTier.ECONOMY
    fable = models["fable"]
    assert not fable.available and "usage credits" in fable.unavailable_reason and fable.pricing.metered


def test_claude_result_is_found_even_with_text_printed_after_it():
    result = claude_cli.parse_claude_output(
        json.dumps({"type": "result", "is_error": False, "result": "ok", "structured_output": {"a": 1}})
        + "\nSessionEnd hook finished\n",
        "",
        0,
        False,
        1.0,
    )
    assert result.ok and result.structured == {"a": 1}
