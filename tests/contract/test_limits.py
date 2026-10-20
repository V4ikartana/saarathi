"""Remaining usage as each CLI reports it. Recorded outputs from 2026-10-20 (identifiers removed)."""

import json

import pytest

from saarathi.providers.limits import (
    ProviderLimits,
    parse_agy_usage,
    parse_claude_usage,
    parse_codex_rate_limits,
)

CLAUDE = json.dumps(
    {
        "type": "result",
        "is_error": False,
        "result": "You are currently using your subscription to power your Claude Code usage\n\n"
        "Current session: 53% used · resets Oct 20 at 5:39pm (Asia/Calcutta)\n"
        "Current week (all models): 20% used · resets Oct 27 at 6:29am (Asia/Calcutta)\n\n"
        "What's contributing to your limits usage?\nLast 24h · 718 requests · 29 sessions\n",
    }
)

CODEX = {
    "ordinaryUsageAllowed": True,
    "accountId": "never-kept",
    "rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 1, "windowDurationMins": 300, "resetsAt": 1792499742},
        "secondary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 1793086542},
        "planType": "plus",
    },
}

AGY = json.dumps(
    {
        "status": "SUCCESS",
        "command": {
            "name": "usage",
            "data": {
                "groups": [
                    {
                        "name": "Gemini Models",
                        "buckets": [
                            {
                                "id": "gemini-weekly",
                                "window": "weekly",
                                "remaining_fraction": 0.99,
                                "reset_time": "2026-10-26T10:23:17Z",
                            },
                            {
                                "id": "gemini-5h",
                                "window": "5h",
                                "remaining_fraction": 1,
                                "reset_time": "2026-10-20T13:10:24Z",
                            },
                        ],
                    }
                ]
            },
        },
    }
)


def test_claude_usage_lines():
    windows = parse_claude_usage(CLAUDE + "\nSessionEnd hook\n")
    assert [(w.label, w.used_percent, w.window) for w in windows] == [
        ("Current session", 53.0, "session"),
        ("Current week (all models)", 20.0, "weekly"),
    ]
    assert windows[0].remaining_percent == 47.0 and windows[0].resets_text.startswith("Oct 20 at 5:39pm")


def test_codex_rate_limits_keep_windows_and_plan_but_not_the_account():
    windows, plan = parse_codex_rate_limits(CODEX)
    assert plan == "plus"
    assert [(w.label, w.used_percent, w.window) for w in windows] == [
        ("5-hour window", 1.0, "5h"),
        ("Weekly", 0.0, "weekly"),
    ]
    assert windows[0].resets_at.startswith("2026-")
    limits = ProviderLimits("codex-cli", "test", windows, plan)
    assert "never-kept" not in json.dumps(limits.as_dict())


def test_agy_usage_buckets():
    windows = parse_agy_usage(AGY)
    assert [(w.label, round(w.remaining_percent, 1)) for w in windows] == [
        ("Weekly · Gemini Models", 99.0),
        ("5-hour window · Gemini Models", 100.0),
    ]


@pytest.mark.parametrize(
    ("parser", "text"),
    [
        (parse_claude_usage, json.dumps({"result": "Usage is unavailable right now"})),
        (parse_agy_usage, "not json"),
        (parse_agy_usage, json.dumps({"status": "SUCCESS"})),
    ],
)
def test_unrecognised_output_is_reported_not_guessed(parser, text):
    with pytest.raises(ValueError):
        parser(text)


def test_codex_without_windows_is_refused():
    with pytest.raises(ValueError):
        parse_codex_rate_limits({"rateLimits": {"planType": "plus"}})


def test_windows_apply_only_to_the_model_families_they_name():
    gemini, other = parse_agy_usage(
        json.dumps(
            {
                "command": {
                    "data": {
                        "groups": [
                            {"name": "Gemini Models", "buckets": [{"window": "weekly", "remaining_fraction": 0.5}]},
                            {
                                "name": "Claude and GPT models",
                                "buckets": [{"window": "weekly", "remaining_fraction": 0.02}],
                            },
                        ]
                    }
                }
            }
        )
    )
    assert gemini.applies_to("gemini-3.8-flash-high") and not gemini.applies_to("claude-opus-4-6-thinking")
    assert other.applies_to("claude-opus-4-6-thinking") and other.applies_to("gpt-oss-120b-medium")
    assert not other.applies_to("gemini-3.8-flash-low")
    (week,) = [w for w in parse_claude_usage(CLAUDE) if w.window == "weekly"]
    assert week.scope == () and week.applies_to("haiku"), "'all models' windows apply to every model"
