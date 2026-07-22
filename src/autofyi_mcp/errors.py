from __future__ import annotations

from typing import Any


class AutoFYIError(RuntimeError):
    """Base error intentionally shown to an MCP client."""


class BusinessRuleError(AutoFYIError):
    """The requested financial operation is ambiguous or unsafe."""


class ConfirmationError(AutoFYIError):
    """A prepared action is missing, expired, cancelled, used, or not confirmed."""


class APIError(AutoFYIError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.outcome_unknown = outcome_unknown
