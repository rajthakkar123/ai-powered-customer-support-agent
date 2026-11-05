"""Durable LLM traces: every assistant request and every model call, including failures.

Rows hold the exact messages sent to the model and its response so any turn can be replayed
when debugging. Email addresses are masked before storage; access is admin-only at the API.
"""

import json
import re
import threading
from datetime import UTC, datetime
from typing import Any

from .database import SupportDatabase

EMAIL_PATTERN = re.compile(
    r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)
MAX_PAYLOAD_CHARS = 500_000
REQUEST_COLUMNS = (
    "request_id",
    "created_at",
    "user_id",
    "customer_id",
    "conversation_id",
    "mode",
    "status",
    "error_code",
    "error_detail",
    "duration_ms",
)
CALL_COLUMNS = (
    "request_id",
    "call_index",
    "created_at",
    "model",
    "prompt_version",
    "status",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "finish_reason",
    "provider_request_id",
    "error_code",
    "request_json",
    "response_json",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_requests (
    request_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    user_id TEXT,
    customer_id TEXT,
    conversation_id TEXT,
    mode TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    error_detail TEXT,
    duration_ms REAL,
    trace_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_requests_created ON assistant_requests(created_at);
CREATE TABLE IF NOT EXISTS llm_calls (
    request_id TEXT NOT NULL,
    call_index INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    model TEXT,
    prompt_version TEXT,
    status TEXT NOT NULL,
    duration_ms REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    finish_reason TEXT,
    provider_request_id TEXT,
    error_code TEXT,
    request_json TEXT NOT NULL,
    response_json TEXT,
    PRIMARY KEY(request_id, call_index)
);
"""


def redact(value: Any) -> str:
    """Serialize for storage with email addresses masked (a***@example.test)."""
    text = json.dumps(value, separators=(",", ":"), default=str)
    text = EMAIL_PATTERN.sub(lambda match: f"{match[1]}***@{match[2]}", text)
    if len(text) > MAX_PAYLOAD_CHARS:
        return json.dumps({"truncated": True, "prefix": text[:MAX_PAYLOAD_CHARS]})
    return text


class TraceStore:
    """SQLite-backed in the application; process-local lists for isolated tests."""

    def __init__(self, database: SupportDatabase | None = None) -> None:
        self.database = database
        self._requests: dict[str, dict[str, Any]] = {}
        self._calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if database is not None:
            with database.session() as connection:
                connection.executescript(SCHEMA)
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(assistant_requests)")
                }
                if "error_detail" not in columns:
                    # Databases created before error details were recorded.
                    connection.execute(
                        "ALTER TABLE assistant_requests ADD COLUMN error_detail TEXT"
                    )

    def record_llm_call(self, call: dict[str, Any]) -> None:
        row = {
            "created_at": _now(),
            **call,
            "request_json": redact(call["request"]),
            "response_json": redact(call.get("response")),
        }
        if self.database is None:
            with self._lock:
                self._calls.append(row)
            return
        with self.database.session() as connection:
            connection.execute(
                f"INSERT OR REPLACE INTO llm_calls ({', '.join(CALL_COLUMNS)}) "  # noqa: S608
                f"VALUES ({', '.join('?' for _ in CALL_COLUMNS)})",
                tuple(row.get(column) for column in CALL_COLUMNS),
            )

    def record_request(self, request: dict[str, Any]) -> None:
        row = {"created_at": _now(), **request, "trace_json": redact(request.get("trace", []))}
        if self.database is None:
            with self._lock:
                self._requests[row["request_id"]] = row
            return
        columns = (*REQUEST_COLUMNS, "trace_json")
        with self.database.session() as connection:
            # Column names are module constants; values are always bound parameters.
            connection.execute(
                f"INSERT OR REPLACE INTO assistant_requests ({', '.join(columns)}) "  # noqa: S608
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(row.get(column) for column in columns),
            )

    def get(self, request_id: str) -> dict[str, Any] | None:
        """One request with its model calls, payloads decoded, for debugging."""
        if self.database is None:
            with self._lock:
                request = self._requests.get(request_id)
                calls = [call for call in self._calls if call["request_id"] == request_id]
        else:
            with self.database.session() as connection:
                found = connection.execute(
                    "SELECT * FROM assistant_requests WHERE request_id=?", (request_id,)
                ).fetchone()
                request = dict(found) if found else None
                calls = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM llm_calls WHERE request_id=? ORDER BY call_index",
                        (request_id,),
                    )
                ]
        if request is None and not calls:
            return None
        return {
            "request": _decoded(request, "trace_json", "trace") if request else None,
            "llm_calls": [
                _decoded(_decoded(call, "request_json", "request"), "response_json", "response")
                for call in sorted(calls, key=lambda call: call["call_index"])
            ],
        }

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Most recent requests without payloads, for scanning and picking one to inspect."""
        if self.database is None:
            with self._lock:
                rows = sorted(self._requests.values(), key=lambda row: row["created_at"])
            return [
                {key: row.get(key) for key in REQUEST_COLUMNS} for row in reversed(rows[-limit:])
            ]
        with self.database.session() as connection:
            rows = connection.execute(
                f"SELECT {', '.join(REQUEST_COLUMNS)} FROM assistant_requests "  # noqa: S608
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def _decoded(row: dict[str, Any], column: str, key: str) -> dict[str, Any]:
    result = {name: value for name, value in row.items() if name not in {column, key}}
    raw = row.get(column)
    result[key] = json.loads(raw) if isinstance(raw, str) else None
    return result


def _now() -> str:
    return datetime.now(UTC).isoformat()
