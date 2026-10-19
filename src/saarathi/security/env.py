"""Environment allowlisting for child processes.

Children never inherit the host environment wholesale. Only these names (plus explicitly
configured ones) pass through, so API keys and host-specific endpoints are not leaked.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping

BASE_ALLOWLIST: frozenset[str] = frozenset(
    {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM", "TZ"}
)
# Windows programs (Python, Git, the provider CLIs) cannot start without these. None of them holds a secret.
WINDOWS_ALLOWLIST: frozenset[str] = frozenset(
    {
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "USERNAME",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "HOMEDRIVE",
        "HOMEPATH",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
    }
)


def filtered_env(
    extra_names: Iterable[str] = (),
    *,
    overrides: Mapping[str, str] | None = None,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    src = os.environ if source is None else source
    allowed = BASE_ALLOWLIST | frozenset(extra_names)
    if sys.platform == "win32":  # variable names are case-insensitive on Windows
        allowed = frozenset(name.upper() for name in allowed | WINDOWS_ALLOWLIST)
        env = {k: v for k, v in src.items() if k.upper() in allowed}
    else:
        env = {k: v for k, v in src.items() if k in allowed}
    if overrides:
        env.update(overrides)
    return env
