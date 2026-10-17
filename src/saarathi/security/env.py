"""Environment allowlisting for child processes.

Children never inherit the host environment wholesale. Only these names (plus explicitly
configured ones) pass through, so API keys and host-specific endpoints are not leaked.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

BASE_ALLOWLIST: frozenset[str] = frozenset(
    {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM", "TZ"}
)


def filtered_env(
    extra_names: Iterable[str] = (),
    *,
    overrides: Mapping[str, str] | None = None,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    src = os.environ if source is None else source
    allowed = BASE_ALLOWLIST | frozenset(extra_names)
    env = {k: v for k, v in src.items() if k in allowed}
    if overrides:
        env.update(overrides)
    return env
