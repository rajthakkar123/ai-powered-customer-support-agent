"""Typed, safe-to-return errors shared by API and backend tools."""

from dataclasses import dataclass


@dataclass
class SupportError(Exception):
    code: str
    message: str
    status_code: int
    trace: list[dict[str, object]] | None = None

    def __str__(self) -> str:
        return self.message


class ValidationError(SupportError):
    def __init__(self, message: str = "Request arguments are invalid.") -> None:
        super().__init__("invalid_argument", message, 400)


class AuthenticationError(SupportError):
    def __init__(self) -> None:
        super().__init__("unauthorized", "Your session is missing or has expired. Sign in.", 401)


class InvalidCredentialsError(SupportError):
    def __init__(self) -> None:
        # One message for unknown, wrong, locked, and inactive accounts avoids account enumeration.
        super().__init__(
            "invalid_credentials",
            "Email or password is incorrect, or the account is temporarily locked.",
            401,
        )


class ForbiddenError(SupportError):
    def __init__(self, message: str = "Your role is not allowed to perform this action.") -> None:
        super().__init__("forbidden", message, 403)


class NotFoundError(SupportError):
    def __init__(self) -> None:
        # The same message is used for absent and out-of-scope records to avoid enumeration.
        super().__init__("not_found", "The requested support record was not found.", 404)


class ConflictError(SupportError):
    def __init__(self, message: str) -> None:
        super().__init__("conflict", message, 409)


class ToolFailureError(SupportError):
    def __init__(self) -> None:
        super().__init__("tool_failure", "The support tool could not complete the request.", 503)


class AssistantTimeoutError(SupportError):
    def __init__(self) -> None:
        super().__init__(
            "assistant_timeout",
            "The assistant ran out of time before preparing an answer. "
            "Retry or narrow the question.",
            504,
        )


class ModelProviderError(SupportError):
    def __init__(
        self,
        message: str = "The assistant model is temporarily unavailable.",
        attempts: int = 1,
        provider_status: int | None = None,
        provider_attempts: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__("model_unavailable", message, 503)
        self.attempts = attempts
        self.provider_status = provider_status
        self.provider_attempts = provider_attempts or []
