"""Error types with stable CLI exit codes."""

from __future__ import annotations


class ExitCode:
    OK = 0
    ERROR = 1
    USAGE = 2
    AWAITING_INPUT = 3
    FAILED = 4
    POLICY = 5
    LOCKED = 6


class SaarathiError(Exception):
    """Base class for expected, user-facing errors."""

    exit_code: int = ExitCode.ERROR

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigError(SaarathiError):
    exit_code = ExitCode.USAGE


class PolicyViolation(SaarathiError):
    exit_code = ExitCode.POLICY


class InvalidTransition(SaarathiError):
    pass


class NotFound(SaarathiError):
    pass


class VersionConflict(SaarathiError):
    """An optimistic-concurrency check failed."""


class EngineLocked(SaarathiError):
    exit_code = ExitCode.LOCKED


class ProviderUnavailable(SaarathiError):
    pass
