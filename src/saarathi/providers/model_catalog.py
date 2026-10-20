"""Reasoning tiers for models Saarathi discovers, from vendor documentation (checked 2026-10-20).

Tiers feed routing (frontier for complex work, balanced for standard, economy for routine). A tier declared in the
policy always wins; this catalogue fills in discovered models so they are not all "unknown". Entries match model
families, not single versions, so a newer release of a known family is classified too. Anything that matches no entry
stays "unknown" and is never assumed strong.

Sources, 2026-10-20:
- OpenAI: GPT-6 Astra is the flagship; Sol is the cheaper workhorse for complex coding and agentic work; Luna is the
  low-cost model for focused, high-volume tasks (OpenAI announcement of GPT-6 Sol and Luna, 2026-09-22). Codex's own
  model descriptions agree ("Frontier intelligence", "workhorse", "balanced", "fast and affordable").
- Google: Gemini 3.8 Flash is Google's "most intelligent workhorse" with low, medium and high thinking levels; Pro
  models are the top tier (Gemini API docs, "What's new in Gemini 3.8 Flash").
- Anthropic: Claude Code aliases ``opus``, ``sonnet`` and ``haiku`` (Claude Code model configuration docs); ``fable``
  is listed by ``claude --help``. Verified on a Claude subscription: ``haiku`` ran as claude-haiku-5-5, and Fable 5.1
  answered "requires usage credits", i.e. paid per use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from saarathi.core.contracts import ReasoningTier

CATALOG_DATE = "2026-10-20"

F, B, E = ReasoningTier.FRONTIER, ReasoningTier.BALANCED, ReasoningTier.ECONOMY


@dataclass(frozen=True)
class CatalogEntry:
    pattern: str  # full match against the model id, case-insensitive
    tier: ReasoningTier
    family: str
    # Some subscription plans charge separately for this model; it is shown but treated as metered in v0.1.
    paid_on_subscription: str | None = None


ENTRIES: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        r"(claude-)?fable([-.].*)?",
        F,
        "Anthropic Claude Fable",
        paid_on_subscription="requires usage credits on a Claude subscription (paid per use)",
    ),
    CatalogEntry(r"(claude-)?opus([-.\[].*)?", F, "Anthropic Claude Opus"),
    CatalogEntry(r"(claude-)?sonnet([-.\[].*)?", B, "Anthropic Claude Sonnet"),
    CatalogEntry(r"(claude-)?haiku([-.].*)?", E, "Anthropic Claude Haiku"),
    CatalogEntry(r"gpt-\d+(\.\d+)?-astra(-.*)?", F, "OpenAI GPT Astra (flagship)"),
    CatalogEntry(r"gpt-\d+(\.\d+)?-sol(-.*)?", B, "OpenAI GPT Sol (workhorse)"),
    CatalogEntry(r"gpt-\d+(\.\d+)?-terra(-.*)?", B, "OpenAI GPT Terra (balanced)"),
    CatalogEntry(r"gpt-\d+(\.\d+)?-luna(-.*)?", E, "OpenAI GPT Luna (fast, low cost)"),
    CatalogEntry(r"gpt-oss-.*", E, "OpenAI GPT-OSS (open-weight)"),
    CatalogEntry(r"gemini-[\d.]+-pro(-.*)?", F, "Google Gemini Pro"),
    CatalogEntry(r"gemini-[\d.]+-flash-lite(-.*)?", E, "Google Gemini Flash-Lite"),
    CatalogEntry(r"gemini-[\d.]+-flash-(low|minimal)", E, "Google Gemini Flash, low thinking"),
    CatalogEntry(r"gemini-[\d.]+-flash(-.*)?", B, "Google Gemini Flash (workhorse)"),
)

# A provider's own one-line description, used only when no catalogue entry matches.
_DESCRIPTION_HINTS: tuple[tuple[str, ReasoningTier], ...] = (
    (r"\bfrontier\b|most demanding|flagship", F),
    (r"fast and (affordable|efficient)|\bcheap|easier tasks|lightweight", E),
    (r"workhorse|balanced|everyday", B),
)


@dataclass(frozen=True)
class Classification:
    tier: ReasoningTier
    basis: str  # shown to people: where the tier came from
    paid_on_subscription: str | None = None


def entry_for(model_id: str) -> CatalogEntry | None:
    for entry in ENTRIES:
        if re.fullmatch(entry.pattern, model_id, flags=re.IGNORECASE):
            return entry
    return None


def classify(model_id: str, description: str | None = None) -> Classification | None:
    """The tier for a model id, or None if neither the catalogue nor the provider's description says."""
    entry = entry_for(model_id)
    if entry is not None:
        return Classification(
            entry.tier, f"{entry.family}; Saarathi model catalogue {CATALOG_DATE}", entry.paid_on_subscription
        )
    if description:
        for pattern, tier in _DESCRIPTION_HINTS:
            if re.search(pattern, description, flags=re.IGNORECASE):
                return Classification(tier, f"provider description: {description.strip()[:120]}")
    return None
