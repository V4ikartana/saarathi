"""Best-effort secret redaction for logs, events and prompts.

Redaction lowers the chance of leaking secrets; it is not a guarantee. Values of environment
variables whose names look secret are also redacted verbatim.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

REDACTED = "[REDACTED]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-~+/]{20,}=*"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b(\s*[=:]\s*)['\"]?[^\s'\"]{8,}"),
)

_SECRET_NAME = re.compile(r"(?i)(key|token|secret|password|passwd|credential|auth|cookie|session)")


def secret_env_values(environ: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    return sorted({v for k, v in env.items() if _SECRET_NAME.search(k) and len(v) >= 8}, key=len, reverse=True)


def redact(text: str, extra_values: Iterable[str] = ()) -> str:
    for value in extra_values:
        if value:
            text = text.replace(value, REDACTED)
    for pattern in _PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        elif pattern.groups == 1:
            text = pattern.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


class Redactor:
    """Redactor bound to a snapshot of the host's secret-looking environment values."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._values = secret_env_values(environ)

    def __call__(self, text: str) -> str:
        return redact(text, self._values)
