"""Structured JSON log lines: named diagnostic fields plus tracebacks, never record contents."""

import json
import logging


class JsonFormatter(logging.Formatter):
    """Emit structured diagnostics without credentials or customer record contents."""

    FIELDS = (
        "request_id",
        "conversation_id",
        "kind",
        "round",
        "prompt_version",
        "finish_reason",
        "tool",
        "model",
        "call_index",
        "provider_request_id",
        "attempts",
        "provider_status",
        "provider_attempts",
        "attempt_number",
        "attempt_status",
        "input_tokens",
        "output_tokens",
        "error_code",
        "action_type",
        "proposal_id",
        "user_id",
        "status",
        "duration_ms",
        "host",
        "port",
        "detail",
        "exception_type",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        payload.update({key: getattr(record, key) for key in self.FIELDS if hasattr(record, key)})
        if record.exc_info:
            # Without this, LOGGER.exception(...) would log the event but lose the traceback.
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)
