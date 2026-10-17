# Security policy

## Supported versions

Saarathi is pre-alpha. Only the latest commit on the default branch receives security fixes.

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use the repository host's private
vulnerability reporting feature (for GitHub: *Security → Report a vulnerability*). Until the public
repository exists, contact the maintainer directly: **contact: TBD**.

Include a description, reproduction steps, affected version or commit, and impact. We aim to acknowledge
reports within 7 days and to agree on a disclosure timeline with you. Please give us a reasonable chance to fix the problem
before public disclosure.

## Threat model

See [docs/security/threat-model.md](docs/security/threat-model.md). In short, Saarathi enforces routing,
permissions, command policy, diff scope and checkout integrity in deterministic code. It does **not** provide
OS-level isolation. Verification commands and some agent tools run with your user permissions.

In scope: bypasses of policy, routing, scope or checkout checks; secret leakage through logs, events or
prompts; prompt injection that changes Saarathi's own decisions; unsafe Git operations.
Out of scope: vulnerabilities in provider CLIs themselves (report those to the vendor).
