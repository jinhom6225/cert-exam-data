"""Collector exceptions.

Messages carry only operation names, HTTP status codes, result codes and
fixed reason words. They never contain a URL, query string or key.
"""

from typing import Optional


class CollectorError(Exception):
    """Base class for expected collector failures."""


class ConfigError(CollectorError):
    """Missing or invalid configuration (for example no service key)."""


class CallFailed(CollectorError):
    """An API call failed after retries or was refused for safety."""

    def __init__(
        self,
        operation: str,
        reason: str,
        status: Optional[int] = None,
        result_code: Optional[str] = None,
        retryable: bool = True,
    ) -> None:
        self.operation = operation
        self.reason = reason
        self.status = status
        self.result_code = result_code
        self.retryable = retryable
        super().__init__(self.describe())

    def describe(self) -> str:
        parts = [self.operation, self.reason]
        if self.status is not None:
            parts.append(f"HTTP {self.status}")
        if self.result_code is not None:
            parts.append(f"resultCode={self.result_code}")
        return " ".join(parts)


class BudgetExceeded(CollectorError):
    """The daily call guard for an API would be exceeded."""


class ValidationFailed(CollectorError):
    """The generated document failed validation and must not be written."""


class TooManyFailures(CollectorError):
    """Per-item calls failed for more than the allowed share of targets."""
