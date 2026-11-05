"""Small JSON HTTP API implemented with Python's standard library."""

import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..application.actions import ActionService
from ..application.assistant import SupportAssistant, execute_assistant_request
from ..application.conversations import ConversationStore
from ..application.tools import SupportTools
from ..domain import fixtures
from ..domain.errors import (
    ForbiddenError,
    NotFoundError,
    SupportError,
    ToolFailureError,
    ValidationError,
)
from ..infrastructure.trace_store import TraceStore
from ..security.accounts import AccountService
from ..security.auth import SUPPORT_ROLES

LOGGER = logging.getLogger("support_agent")
# Increment when the browser and API contract change together; the page compares it on load
# and warns when a server started before a code change is still running.
API_VERSION = 5
MAX_BODY_BYTES = 16_384
WEB_ROOT = Path(__file__).resolve().parents[3] / "frontend"
STATIC_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/support": ("index.html", "text/html; charset=utf-8"),
    "/assets/support.js": ("assets/support.js", "text/javascript; charset=utf-8"),
    "/assets/support.css": ("assets/support.css", "text/css; charset=utf-8"),
}
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'"
)


def _json_response(operation: Callable[[], object]) -> tuple[int, dict[str, object], str]:
    """Run one account operation and map typed errors to a correlated JSON response."""
    request_id = str(uuid.uuid4())
    try:
        return 200, {"request_id": request_id, "result": operation()}, request_id
    except SupportError as error:
        return (
            error.status_code,
            {"request_id": request_id, "error": {"code": error.code, "message": error.message}},
            request_id,
        )


def execute_login(accounts: AccountService, body: object) -> tuple[int, dict[str, object], str]:
    def login() -> object:
        if not isinstance(body, dict) or set(body) != {"email", "password"}:
            raise ValidationError("Body must contain exactly email and password.")
        return accounts.login(body["email"], body["password"])

    return _json_response(login)


def execute_logout(
    accounts: AccountService, authorization: str | None
) -> tuple[int, dict[str, object], str]:
    def logout() -> object:
        accounts.logout(authorization)
        return {"signed_out": True}

    return _json_response(logout)


def execute_current_user(
    accounts: AccountService, authorization: str | None
) -> tuple[int, dict[str, object], str]:
    return _json_response(lambda: accounts.current_user(authorization))


def execute_account_list(
    accounts: AccountService, authorization: str | None
) -> tuple[int, dict[str, object], str]:
    return _json_response(lambda: accounts.list_accounts(accounts.authenticate(authorization)))


def execute_approver_list(
    accounts: AccountService, authorization: str | None
) -> tuple[int, dict[str, object], str]:
    """Approvers a proposal can be routed to; any signed-in staff member may list them."""
    return _json_response(lambda: accounts.list_approvers(accounts.authenticate(authorization)))


def execute_account_create(
    accounts: AccountService, authorization: str | None, body: object
) -> tuple[int, dict[str, object], str]:
    def create() -> object:
        principal = accounts.authenticate(authorization)
        fields = {"email", "display_name", "role", "password", "customer_ids"}
        if not isinstance(body, dict) or set(body) != fields:
            raise ValidationError(
                "Body must contain email, display_name, role, password, and customer_ids."
            )
        return accounts.create_account(
            principal,
            body["email"],
            body["display_name"],
            body["role"],
            body["password"],
            body["customer_ids"],
        )

    return _json_response(create)


def execute_tool_request(
    tools: SupportTools,
    authorization: str | None,
    body: object,
    simulated_failures: frozenset[str] = frozenset(),
) -> tuple[int, dict[str, object], str]:
    """Execute one decoded request; shared by HTTP transport and in-process tests."""
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    tool_name: object = None
    try:
        principal = tools.authenticator.authenticate(authorization)
        if not isinstance(body, dict) or set(body) != {"tool", "arguments"}:
            raise ValidationError("Body must contain exactly tool and arguments.")
        tool_name, arguments = body["tool"], body["arguments"]
        result = tools.call(tool_name, arguments, principal, simulated_failures)
        status, http_status = "succeeded", 200
        payload: dict[str, object] = {"result": result}
    except SupportError as error:
        status, http_status = error.code, error.status_code
        payload = {"error": {"code": error.code, "message": error.message}}
    except Exception as error:
        status, http_status = "internal_error", ToolFailureError().status_code
        LOGGER.exception(
            "tool_unexpected_error",
            extra={
                "request_id": request_id,
                "tool": tool_name,
                "status": status,
                "exception_type": type(error).__name__,
            },
        )
        payload = {
            "error": {
                "code": status,
                "message": "The support service encountered an unexpected error.",
            }
        }
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    trace = {"tool": tool_name, "status": status, "duration_ms": elapsed_ms}
    payload.update({"request_id": request_id, "trace": [trace]})
    LOGGER.info("tool_call", extra={"request_id": request_id, **trace})
    return http_status, payload, request_id


def execute_action_request(
    actions: ActionService,
    authorization: str | None,
    operation: str,
    body: object,
) -> tuple[int, dict[str, object], str]:
    """Authenticated boundary for action proposals and human decisions."""
    request_id = str(uuid.uuid4())
    try:
        principal = actions.tools.authenticator.authenticate(authorization)
        if not isinstance(body, dict):
            raise ValidationError("Body must be a JSON object.")
        if operation == "propose":
            required = {"action_type", "order_id", "idempotency_key", "reason"}
            allowed = required | {"details", "approver_id"}
            if not required.issubset(body) or set(body) - allowed:
                raise ValidationError(
                    "Proposal body needs action_type, order_id, idempotency_key, and the "
                    "customer's reason; details and approver_id are optional."
                )
            result = actions.propose(
                body["action_type"],
                body["order_id"],
                body.get("details"),
                body["idempotency_key"],
                principal,
                reason=body["reason"],
                approver_id=body.get("approver_id"),
            )
        elif operation == "assign":
            if set(body) != {"proposal_id", "approver_id"}:
                raise ValidationError("Assign body must contain proposal_id and approver_id.")
            result = actions.assign(body["proposal_id"], body["approver_id"], principal)
        elif operation == "decide":
            if set(body) != {"proposal_id", "decision", "idempotency_key"}:
                raise ValidationError(
                    "Decision body must contain proposal_id, decision, and idempotency_key."
                )
            result = actions.decide(
                body["proposal_id"], body["decision"], body["idempotency_key"], principal
            )
        else:
            raise ValidationError("Unknown action operation.")
        return 200, {"request_id": request_id, "result": result}, request_id
    except SupportError as error:
        return (
            error.status_code,
            {"request_id": request_id, "error": {"code": error.code, "message": error.message}},
            request_id,
        )


def execute_action_list(
    actions: ActionService, authorization: str | None, status: str | None
) -> tuple[int, dict[str, object], str]:
    """The approval queue: proposals visible to this principal, optionally by status."""
    return _json_response(
        lambda: actions.list_proposals(
            actions.tools.authenticator.authenticate(authorization), status
        )
    )


def execute_action_read(
    actions: ActionService,
    authorization: str | None,
    proposal_id: str,
) -> tuple[int, dict[str, object], str]:
    """Retrieve an authorized proposal and its safe audit history."""
    request_id = str(uuid.uuid4())
    try:
        principal = actions.tools.authenticator.authenticate(authorization)
        result = actions.get(proposal_id, principal)
        return 200, {"request_id": request_id, "result": result}, request_id
    except SupportError as error:
        return (
            error.status_code,
            {"request_id": request_id, "error": {"code": error.code, "message": error.message}},
            request_id,
        )


def execute_customer_list(
    tools: SupportTools,
    authorization: str | None,
) -> tuple[int, dict[str, object], str]:
    """Return safe customer labels limited by server-side agent scope."""
    request_id = str(uuid.uuid4())
    try:
        principal = tools.authenticator.authenticate(authorization)
        if principal.role not in SUPPORT_ROLES:
            raise SupportError("forbidden", "A support workspace session is required.", 403)
        if tools.database is not None:
            rows = tools.database.list_customer_rows()
        else:
            rows = [
                {
                    "customer_id": customer_id,
                    "name": customer["name"],
                    "email": customer["email"],
                    "created_at": customer["created_at"],
                    "order_count": sum(
                        order["customer_id"] == customer_id for order in fixtures.ORDERS.values()
                    ),
                    "conversation_count": len(customer["support_conversation_ids"]),
                }
                for customer_id, customer in fixtures.CUSTOMERS.items()
            ]
        result = [
            row
            for row in rows
            if principal.role == "support_admin" or row["customer_id"] in principal.customer_ids
        ]
        return 200, {"request_id": request_id, "result": result}, request_id
    except SupportError as error:
        return (
            error.status_code,
            {"request_id": request_id, "error": {"code": error.code, "message": error.message}},
            request_id,
        )


def execute_product_list(
    tools: SupportTools,
    authorization: str | None,
) -> tuple[int, dict[str, object], str]:
    """Return the product catalog to authenticated support staff."""
    request_id = str(uuid.uuid4())
    try:
        principal = tools.authenticator.authenticate(authorization)
        if principal.role not in SUPPORT_ROLES:
            raise SupportError("forbidden", "A support workspace session is required.", 403)
        if tools.database is None:
            result = list(fixtures.PRODUCTS.values())
        else:
            result = tools.database.list_products()
        return 200, {"request_id": request_id, "result": result}, request_id
    except SupportError as error:
        return (
            error.status_code,
            {"request_id": request_id, "error": {"code": error.code, "message": error.message}},
            request_id,
        )


def execute_conversation_latest(
    conversations: ConversationStore,
    tools: SupportTools,
    authorization: str | None,
    customer_id: str | None,
) -> tuple[int, dict[str, object], str]:
    """Return the agent's most recent thread for a customer, or null when there is none."""
    return _json_response(
        lambda: conversations.latest(tools.authenticator.authenticate(authorization), customer_id)
    )


def execute_conversation_read(
    conversations: ConversationStore,
    tools: SupportTools,
    authorization: str | None,
    conversation_id: str,
) -> tuple[int, dict[str, object], str]:
    return _json_response(
        lambda: conversations.thread(
            conversation_id, tools.authenticator.authenticate(authorization)
        )
    )


def execute_conversation_reply(
    conversations: ConversationStore,
    tools: SupportTools,
    authorization: str | None,
    conversation_id: str,
    body: object,
) -> tuple[int, dict[str, object], str]:
    def reply() -> object:
        principal = tools.authenticator.authenticate(authorization)
        if not isinstance(body, dict) or set(body) != {"text", "in_reply_to"}:
            raise ValidationError("Body must contain exactly text and in_reply_to.")
        return conversations.record_reply(
            conversation_id, principal, body["text"], body["in_reply_to"]
        )

    return _json_response(reply)


def execute_trace_read(
    traces: TraceStore,
    tools: SupportTools,
    authorization: str | None,
    request_id: str | None,
) -> tuple[int, dict[str, object], str]:
    """Admin-only: recent assistant requests, or one request with its full model calls."""

    def read() -> object:
        principal = tools.authenticator.authenticate(authorization)
        if principal.role != "support_admin":
            raise ForbiddenError("Only an administrator can read LLM traces.")
        if request_id is None:
            return traces.recent()
        found = traces.get(request_id)
        if found is None:
            raise NotFoundError()
        return found

    return _json_response(read)


@dataclass(frozen=True)
class AppServices:
    """Everything the HTTP transport routes to; optional services answer 503 when absent."""

    tools: SupportTools
    assistant: SupportAssistant | None = None
    actions: ActionService | None = None
    accounts: AccountService | None = None
    conversations: ConversationStore | None = None
    traces: TraceStore | None = None
    # Development only: lets a request ask named tools to fail so recovery can be demonstrated.
    allow_fault_injection: bool = False

    def simulated_failures(self, header: str | None) -> frozenset[str]:
        if not header or not self.allow_fault_injection:
            return frozenset()
        requested = {name.strip() for name in header.split(",")}
        return frozenset(requested & set(self.tools.tool_names))


FAULT_INJECTION_HEADER = "X-Simulate-Tool-Failure"
UUID_SEGMENT = r"([0-9a-fA-F-]{36})"


def _unavailable(service: str) -> tuple[int, dict[str, object], str]:
    request_id = str(uuid.uuid4())
    payload: dict[str, object] = {
        "request_id": request_id,
        "error": {"code": "unavailable", "message": f"{service} service is not configured."},
    }
    return 503, payload, request_id


def _log_assistant_trace(payload: dict[str, object], request_id: str) -> None:
    conversation_id = payload.get("conversation_id")
    trace = payload.get("trace")
    for step in trace if isinstance(trace, list) else []:
        if step.get("kind") == "llm":
            for attempt in step.get("provider_attempts", []):
                LOGGER.info(
                    "llm_provider_attempt",
                    extra={
                        "request_id": request_id,
                        "conversation_id": conversation_id,
                        "kind": "llm_attempt",
                        "call_index": step.get("call_index"),
                        "attempt_number": attempt.get("attempt"),
                        "attempt_status": attempt.get("status"),
                        "provider_status": attempt.get("provider_status"),
                        "error_code": attempt.get("provider_error_code"),
                        "provider_request_id": attempt.get("provider_request_id"),
                        "duration_ms": attempt.get("duration_ms"),
                        "exception_type": attempt.get("error_type"),
                    },
                )
        LOGGER.info(
            "assistant_step",
            extra={
                "request_id": request_id,
                "conversation_id": conversation_id,
                "kind": step.get("kind"),
                "round": step.get("round"),
                "tool": step.get("tool"),
                "model": step.get("model"),
                "prompt_version": step.get("prompt_version"),
                "call_index": step.get("call_index"),
                "finish_reason": step.get("finish_reason"),
                "provider_request_id": step.get("provider_request_id"),
                "attempts": step.get("attempts"),
                "provider_attempts": len(step.get("provider_attempts", [])),
                "provider_status": step.get("provider_status"),
                "input_tokens": step.get("input_tokens"),
                "output_tokens": step.get("output_tokens"),
                "error_code": step.get("error_code"),
                "action_type": step.get("action_type"),
                "proposal_id": step.get("proposal_id"),
                "status": step.get("status"),
                "duration_ms": step.get("duration_ms"),
            },
        )


def _log_action_request(
    operation: str, payload: dict[str, object], request_id: str, status: int
) -> None:
    result = payload.get("result")
    result = result if isinstance(result, dict) else {}
    error = payload.get("error")
    error = error if isinstance(error, dict) else {}
    LOGGER.info(
        "action_request",
        extra={
            "request_id": request_id,
            "kind": operation,
            "status": result.get("status", error.get("code", status)),
            "action_type": result.get("action_type"),
            "proposal_id": result.get("proposal_id"),
        },
    )


def create_handler(services: AppServices) -> type[BaseHTTPRequestHandler]:
    tools = services.tools

    class Handler(BaseHTTPRequestHandler):
        server_version = "SupportAgentAPI/0.1"

        def log_message(self, format_string: str, *args: Any) -> None:
            # Request bodies, credentials, and record contents are intentionally never logged.
            LOGGER.info("http_access", extra={"detail": urlsplit(self.path).path})

        def do_GET(self) -> None:
            self._guarded(self._handle_get)

        def do_POST(self) -> None:
            self._guarded(self._handle_post)

        def _guarded(self, handle: Callable[[], None]) -> None:
            """Every failure becomes a JSON error with a request ID; nothing drops the socket."""
            try:
                handle()
            except SupportError as error:
                request_id = str(uuid.uuid4())
                self._send(
                    error.status_code,
                    {
                        "request_id": request_id,
                        "error": {"code": error.code, "message": error.message},
                    },
                    request_id,
                )
            except Exception as error:
                request_id = str(uuid.uuid4())
                LOGGER.exception(
                    "http_unexpected_error",
                    extra={
                        "request_id": request_id,
                        "detail": urlsplit(self.path).path,
                        "exception_type": type(error).__name__,
                    },
                )
                self._send(
                    500,
                    {
                        "request_id": request_id,
                        "error": {
                            "code": "internal_error",
                            "message": "The support service hit an unexpected error. "
                            "Quote the request ID when reporting it.",
                        },
                    },
                    request_id,
                )

        def _handle_get(self) -> None:
            route = urlsplit(self.path)
            path = route.path
            authorization = self.headers.get("Authorization")
            if path in STATIC_ASSETS:
                relative_path, content_type = STATIC_ASSETS[path]
                self._send_static((WEB_ROOT / relative_path).read_bytes(), content_type)
                return
            if path == "/health":
                request_id = str(uuid.uuid4())
                health = {
                    "status": "ok",
                    "api_version": API_VERSION,
                    "tools": list(tools.tool_names),
                    "assistant_configured": services.assistant is not None,
                    "fault_injection": services.allow_fault_injection,
                }
                self._send(200, health, request_id)
                return
            if path == "/customers":
                self._send(*execute_customer_list(tools, authorization))
                return
            if path == "/products":
                self._send(*execute_product_list(tools, authorization))
                return
            if path in {"/auth/me", "/admin/accounts", "/approvers"}:
                if services.accounts is None:
                    self._send(*_unavailable("Account"))
                elif path == "/auth/me":
                    self._send(*execute_current_user(services.accounts, authorization))
                elif path == "/approvers":
                    self._send(*execute_approver_list(services.accounts, authorization))
                else:
                    self._send(*execute_account_list(services.accounts, authorization))
                return
            action_match = re.fullmatch(rf"/actions/{UUID_SEGMENT}", path)
            if path == "/actions" or action_match:
                if services.actions is None:
                    self._send(*_unavailable("Action"))
                elif not action_match:
                    status = parse_qs(route.query).get("status", [None])[0]
                    self._send(*execute_action_list(services.actions, authorization, status))
                else:
                    self._send(
                        *execute_action_read(services.actions, authorization, action_match[1])
                    )
                return
            trace_match = re.fullmatch(rf"/admin/traces/{UUID_SEGMENT}", path)
            if path == "/admin/traces" or trace_match:
                if services.traces is None:
                    self._send(*_unavailable("Trace"))
                else:
                    self._send(
                        *execute_trace_read(
                            services.traces,
                            tools,
                            authorization,
                            trace_match[1] if trace_match else None,
                        )
                    )
                return
            conversation_match = re.fullmatch(rf"/conversations/{UUID_SEGMENT}", path)
            if path == "/conversations" or conversation_match:
                if services.conversations is None:
                    self._send(*_unavailable("Conversation"))
                elif conversation_match:
                    self._send(
                        *execute_conversation_read(
                            services.conversations, tools, authorization, conversation_match[1]
                        )
                    )
                else:
                    customer_id = parse_qs(route.query).get("customer_id", [None])[0]
                    self._send(
                        *execute_conversation_latest(
                            services.conversations, tools, authorization, customer_id
                        )
                    )
                return
            raise SupportError("not_found", "Route not found.", 404)

        def _handle_post(self) -> None:
            path = urlsplit(self.path).path
            authorization = self.headers.get("Authorization")
            simulated = services.simulated_failures(self.headers.get(FAULT_INJECTION_HEADER))
            action_route = {
                "/actions/propose": "propose",
                "/actions/assign": "assign",
                "/actions/decide": "decide",
            }.get(path)
            reply_match = re.fullmatch(rf"/conversations/{UUID_SEGMENT}/replies", path)
            account_routes = {"/auth/login", "/auth/logout", "/admin/accounts"}
            if (
                path not in {"/tools/call", "/assistant/ask", *account_routes}
                and action_route is None
                and reply_match is None
            ):
                raise SupportError("not_found", "Route not found.", 404)
            body = self._read_json()
            if path in account_routes:
                self._send(*self._account_post(path, authorization, body))
            elif action_route is not None:
                if services.actions is None:
                    self._send(*_unavailable("Action"))
                    return
                status, payload, request_id = execute_action_request(
                    services.actions, authorization, action_route, body
                )
                _log_action_request(action_route, payload, request_id, status)
                self._send(status, payload, request_id)
            elif reply_match is not None:
                if services.conversations is None:
                    self._send(*_unavailable("Conversation"))
                    return
                self._send(
                    *execute_conversation_reply(
                        services.conversations, tools, authorization, reply_match[1], body
                    )
                )
            elif path == "/assistant/ask":
                status, payload, request_id = execute_assistant_request(
                    services.assistant, tools, authorization, body, simulated
                )
                _log_assistant_trace(payload, request_id)
                self._send(status, payload, request_id)
            else:
                self._send(*execute_tool_request(tools, authorization, body, simulated))

        def _account_post(
            self, path: str, authorization: str | None, body: object
        ) -> tuple[int, dict[str, object], str]:
            # Credentials arrive only in request bodies, which are never logged.
            accounts = services.accounts
            if accounts is None:
                return _unavailable("Account")
            if path == "/auth/login":
                return execute_login(accounts, body)
            if path == "/auth/logout":
                return execute_logout(accounts, authorization)
            return execute_account_create(accounts, authorization, body)

        def _read_json(self) -> object:
            raw_length = self.headers.get("Content-Length", "0")
            if not re.fullmatch(r"\d{1,8}", raw_length):
                raise ValidationError("Content-Length is invalid.")
            length = int(raw_length)
            if length == 0 or length > MAX_BODY_BYTES:
                raise ValidationError("Request body is empty or exceeds the size limit.")
            try:
                return json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ValidationError("Request body must be valid JSON.") from error

        def _send(self, status: int, payload: dict[str, object], request_id: str) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("X-Request-ID", request_id)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_static(self, content: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Request-ID", str(uuid.uuid4()))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(content)

    return Handler


def serve(host: str, port: int, services: AppServices) -> None:
    server = ThreadingHTTPServer((host, port), create_handler(services))
    LOGGER.info("server_started", extra={"host": host, "port": port})
    if services.allow_fault_injection:
        LOGGER.warning(
            "fault_injection_enabled",
            extra={"detail": f"{FAULT_INJECTION_HEADER} is honoured; development use only"},
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("server_stopping")
    finally:
        server.server_close()
