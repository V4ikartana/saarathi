# ADR-0006: Subscription CLI use and authentication boundaries

- Status: Accepted
- Date: 2026-10-18

## Context

Anthropic's Claude Code terms (checked 2026-10-18) say OAuth/subscription authentication supports "ordinary use", that
third-party developers may not route requests through Free/Pro/Max credentials on behalf of their users, and that an end user
may sign in to the unmodified Claude Code binary with their own subscription. OpenAI documents API keys as the default for
automation, with ChatGPT-managed auth as an advanced option on trusted machines.

## Decision

- Saarathi is a local tool that the user runs themselves. It invokes the **unmodified, user-installed** CLI binary, which
  keeps its own authentication. Saarathi never collects, stores, proxies or extracts credentials or session tokens.
- `doctor` reports each CLI's authentication method from documented status commands. When a subscription login drives
  automated runs, it shows a notice that links to the provider's terms and recommends API-key authentication for
  heavy or unattended use.
- Billing classification follows the authentication method: a CLI authenticated with an API key is **metered**, and
  `billing.api_enabled: false` blocks it.
- Saarathi does not offer hosted operation or shared credentials in v0.1.

## Consequences

- Whether a given subscription allows a given volume of automated use is the user's responsibility under their provider
  agreement. Saarathi surfaces this and does not claim entitlement.
