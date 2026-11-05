"""OpenAI Chat Completions adapter with structured output, a turn deadline, and one safe retry."""

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..application.tool_schemas import TOOL_SCHEMAS
from ..application.turns import TURN_RESPONSE_FORMAT
from ..domain.errors import AssistantTimeoutError, ModelProviderError

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
# Provider messages can echo a masked key, so only the provider's error code is surfaced.
NON_RETRYABLE_MESSAGES = {
    401: "The model provider rejected the API key ({code}). Check OPENAI_API_KEY.",
    403: "The model provider denied access ({code}). Check the key's project and permissions.",
    404: "The configured model was not found ({code}). Check OPENAI_MODEL.",
}


class OpenAIChatClient:
    """Minimal provider adapter. Credentials only come from process environment."""

    def __init__(
        self, api_key: str, model: str = "gpt-4o-mini", timeout_seconds: float = 15.0
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY must not be empty.")
        self._api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "OpenAIChatClient | None":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        return cls(api_key, os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))

    def complete(
        self,
        messages: list[dict[str, object]],
        deadline: float | None = None,
        allow_tools: bool = True,
    ) -> dict[str, object]:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": TOOL_SCHEMAS,
                # "none" forces a final structured answer once the tool budget is spent.
                "tool_choice": "auto" if allow_tools else "none",
                "response_format": TURN_RESPONSE_FORMAT,
            }
        ).encode("utf-8")
        request = Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        provider_attempts: list[dict[str, object]] = []
        for attempt in range(1, 3):
            timeout = self._attempt_timeout(deadline)
            attempt_started = time.monotonic()
            try:
                # The URL is the fixed https endpoint above, never caller input.
                with urlopen(request, timeout=timeout) as response:  # noqa: S310
                    decoded = json.loads(response.read(2_000_000))
                    provider_request_id = response.headers.get("x-request-id")
                if not isinstance(decoded, dict):
                    raise ModelProviderError(attempts=attempt, provider_attempts=provider_attempts)
                provider_attempts.append(
                    {
                        "attempt": attempt,
                        "status": "succeeded",
                        "duration_ms": _elapsed_ms(attempt_started),
                        "provider_request_id": provider_request_id,
                    }
                )
                decoded["_support_trace"] = {
                    "attempts": attempt,
                    "provider_request_id": provider_request_id,
                    "provider_attempts": provider_attempts,
                }
                return decoded
            except HTTPError as error:
                code = _provider_error_code(error)
                retry = attempt == 1 and error.code in RETRYABLE_STATUSES
                provider_attempts.append(
                    {
                        "attempt": attempt,
                        "status": "retrying" if retry else "failed",
                        "provider_status": error.code,
                        "provider_error_code": code,
                        "duration_ms": _elapsed_ms(attempt_started),
                        "provider_request_id": error.headers.get("x-request-id")
                        if error.headers
                        else None,
                    }
                )
                if retry and self._sleep_before_retry(error, deadline):
                    continue
                raise ModelProviderError(
                    _failure_message(error.code, code),
                    attempts=attempt,
                    provider_status=error.code,
                    provider_attempts=provider_attempts,
                ) from error
            except (TimeoutError, URLError, json.JSONDecodeError, OSError) as error:
                provider_attempts.append(
                    {
                        "attempt": attempt,
                        "status": "retrying" if attempt == 1 else "failed",
                        "error_type": type(error).__name__,
                        "duration_ms": _elapsed_ms(attempt_started),
                    }
                )
                if attempt == 1 and self._sleep_before_retry(None, deadline):
                    continue
                raise ModelProviderError(
                    attempts=attempt, provider_attempts=provider_attempts
                ) from error
        raise ModelProviderError()

    def _attempt_timeout(self, deadline: float | None) -> float:
        if deadline is None:
            return self.timeout_seconds
        remaining = deadline - time.monotonic()
        if remaining < 1:
            raise AssistantTimeoutError()
        return min(self.timeout_seconds, remaining)

    @staticmethod
    def _sleep_before_retry(error: HTTPError | None, deadline: float | None) -> bool:
        """Wait before the single retry; give up when the turn deadline leaves no time."""
        delay = 0.2
        retry_after = error.headers.get("retry-after") if error and error.headers else None
        if retry_after and retry_after.replace(".", "", 1).isdigit():
            delay = min(float(retry_after), 5.0)
        if deadline is not None and deadline - time.monotonic() < delay + 1:
            return False
        time.sleep(delay)
        return True


def _provider_error_code(error: HTTPError) -> str | None:
    try:
        body = json.loads(error.read(65_536) or b"{}")
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    detail = body.get("error") if isinstance(body, dict) else None
    if not isinstance(detail, dict):
        return None
    code = detail.get("code") or detail.get("type")
    return str(code)[:80] if code else None


def _failure_message(status: int, code: str | None) -> str:
    label = code or f"HTTP {status}"
    if status in NON_RETRYABLE_MESSAGES:
        return NON_RETRYABLE_MESSAGES[status].format(code=label)
    if status == 429:
        return f"The model provider is rate limiting requests ({label}). Retry shortly."
    if 400 <= status < 500:
        return f"The model provider rejected the request ({label})."
    return "The assistant model is temporarily unavailable."


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 2)
