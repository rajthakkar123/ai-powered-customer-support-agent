"""Real-socket tests of the HTTP transport: routing, fault injection, and error responses.

The server is built in-process from test doubles, so no environment file or credential is read.
"""

import json
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from support_agent.api.http import AppServices, create_handler
from support_agent.application.actions import ActionService
from support_agent.application.assistant import SupportAssistant
from support_agent.application.conversations import ConversationStore
from support_agent.infrastructure.trace_store import TraceStore
from tests.fakes import build_tools
from tests.test_assistant import FakeChatClient, final_response, tool_response

TOKEN_MAP = {
    "agent-one-secret": {
        "user_id": "agent-001",
        "role": "support_agent",
        "customer_ids": ["cus-1001"],
    },
    "admin-one-secret": {"user_id": "admin-001", "role": "support_admin", "customer_ids": []},
    "approver-one-secret": {
        "user_id": "approver-001",
        "role": "support_approver",
        "customer_ids": ["cus-1001"],
    },
}
AGENT = "Bearer agent-one-secret"


class HttpServerTests(unittest.TestCase):
    def start(self, responses: list[object], allow_fault_injection: bool = True) -> None:
        tools = build_tools(TOKEN_MAP, date(2026, 11, 4))
        self.client = FakeChatClient(responses)
        conversations = ConversationStore()
        actions = ActionService(tools)
        traces = TraceStore()
        services = AppServices(
            tools=tools,
            assistant=SupportAssistant(tools, self.client, conversations, actions, traces=traces),
            actions=actions,
            conversations=conversations,
            traces=traces,
            allow_fault_injection=allow_fault_injection,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(services))
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(
        self,
        method: str,
        path: str,
        body: object = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        data = None if body is None else json.dumps(body).encode()
        request = Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"Authorization": AGENT, "Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def ask(self, message: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
        body = {"message": message, "customer_id": "cus-1001", "mode": "customer_message"}
        return self.request("POST", "/assistant/ask", body, headers)

    def test_customer_message_round_trip_and_thread_reload(self) -> None:
        self.start(
            [
                tool_response("get_order_details", {"order_id": "ord-5001"}),
                final_response(suggested_reply="Your order was delivered on October 25."),
            ]
        )
        status, health = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["fault_injection"])
        self.assertTrue(health["assistant_configured"])
        self.assertIsInstance(health["api_version"], int)
        status, payload = self.ask("Where is ord-5001?")
        self.assertEqual(status, 200)
        conversation_id = payload["conversation_id"]
        status, latest = self.request("GET", "/conversations?customer_id=cus-1001")
        self.assertEqual(latest["result"]["conversation_id"], conversation_id)
        reply_body = {
            "text": "Delivered Oct 25.",
            "in_reply_to": payload["events"][1]["suggestion_id"],
        }
        reply_path = f"/conversations/{conversation_id}/replies"
        status, reply = self.request("POST", reply_path, reply_body)
        self.assertEqual((status, reply["result"]["kind"]), (200, "agent_reply"))
        status, repeat = self.request("POST", reply_path, reply_body)
        self.assertEqual((status, repeat["error"]["code"]), (409, "conflict"))
        status, thread = self.request("GET", f"/conversations/{conversation_id}")
        self.assertEqual(len(thread["result"]["events"]), 3)
        status, empty = self.request("GET", "/conversations?customer_id=cus-1002")
        self.assertEqual((status, empty["error"]["code"]), (404, "not_found"))
        # LLM traces are admin-only and include every model call of the request.
        trace_path = f"/admin/traces/{payload['request_id']}"
        status, denied = self.request("GET", trace_path)
        self.assertEqual((status, denied["error"]["code"]), (403, "forbidden"))
        admin = {"Authorization": "Bearer admin-one-secret"}
        status, trace = self.request("GET", trace_path, headers=admin)
        self.assertEqual(status, 200)
        self.assertEqual(len(trace["result"]["llm_calls"]), 2)
        status, recent = self.request("GET", "/admin/traces", headers=admin)
        self.assertEqual(recent["result"][0]["request_id"], payload["request_id"])

    def test_fault_injection_header_is_honoured_only_when_enabled(self) -> None:
        header = {"X-Simulate-Tool-Failure": "get_order_details"}
        for enabled, expected in ((True, "tool_failure"), (False, "succeeded")):
            with self.subTest(enabled=enabled):
                self.start(
                    [
                        tool_response("get_order_details", {"order_id": "ord-5001"}),
                        final_response(suggested_reply="Checked."),
                    ],
                    allow_fault_injection=enabled,
                )
                _, payload = self.ask("Where is ord-5001?", header)
                tool_step = next(step for step in payload["trace"] if step["kind"] == "tool")
                self.assertEqual(tool_step["status"], expected)
                status, _ = self.request(
                    "POST",
                    "/tools/call",
                    {"tool": "get_order_details", "arguments": {"order_id": "ord-5001"}},
                    header,
                )
                self.assertEqual(status, 503 if enabled else 200)

    def test_directory_and_approval_routes_end_to_end(self) -> None:
        self.start([])
        status, customers = self.request("GET", "/customers")
        self.assertEqual([row["customer_id"] for row in customers["result"]], ["cus-1001"])
        status, products = self.request("GET", "/products")
        self.assertEqual(status, 200)
        self.assertTrue(products["result"])
        status, proposed = self.request(
            "POST",
            "/actions/propose",
            {
                "action_type": "refund",
                "order_id": "ord-5001",
                "details": {"item_ids": ["item-9002"]},
                "idempotency_key": "http-proposal-key-01",
            },
        )
        self.assertEqual((status, proposed["result"]["status"]), (200, "pending_approval"))
        proposal_id = proposed["result"]["proposal_id"]
        approver = {"Authorization": "Bearer approver-one-secret"}
        status, queue = self.request("GET", "/actions?status=pending_approval", headers=approver)
        self.assertEqual([item["proposal_id"] for item in queue["result"]], [proposal_id])
        status, read = self.request("GET", f"/actions/{proposal_id}", headers=approver)
        self.assertEqual(read["result"]["details"]["amount_cents"], 2999)
        decision = {
            "proposal_id": proposal_id,
            "decision": "approve",
            "idempotency_key": "http-decision-key-01",
        }
        status, decided = self.request("POST", "/actions/decide", decision, approver)
        self.assertEqual((status, decided["result"]["status"]), (200, "executed"))
        status, invalid = self.request("GET", "/actions?status=bogus", headers=approver)
        self.assertEqual((status, invalid["error"]["code"]), (400, "invalid_argument"))

    def test_unconfigured_services_and_malformed_requests_fail_clearly(self) -> None:
        tools = build_tools(TOKEN_MAP, date(2026, 11, 4))
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), create_handler(AppServices(tools=tools))
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        for method, path, body in (
            ("GET", "/auth/me", None),
            ("GET", "/actions", None),
            ("GET", "/admin/traces", None),
            ("GET", "/conversations?customer_id=cus-1001", None),
            ("POST", "/auth/login", {"email": "a@b.co", "password": "x"}),
            ("POST", "/actions/propose", {"action_type": "refund"}),
        ):
            with self.subTest(path=path):
                status, payload = self.request(method, path, body)
                self.assertEqual((status, payload["error"]["code"]), (503, "unavailable"))
        status, payload = self.request("POST", "/assistant/ask", {"message": "hi"})
        self.assertEqual((status, payload["error"]["code"]), (503, "model_unavailable"))
        status, payload = self.request("POST", "/nope", {"a": 1})
        self.assertEqual((status, payload["error"]["code"]), (404, "not_found"))
        raw = Request(
            f"{self.base}/tools/call",
            data=b"{not json",
            method="POST",
            headers={"Authorization": AGENT, "Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(raw, timeout=5)
        self.assertEqual(json.loads(raised.exception.read())["error"]["code"], "invalid_argument")

    def test_unknown_routes_and_unexpected_errors_return_json_with_request_ids(self) -> None:
        self.start([])
        status, payload = self.request("GET", "/nope")
        self.assertEqual((status, payload["error"]["code"]), (404, "not_found"))
        self.assertTrue(payload["request_id"])
        self.server.RequestHandlerClass._handle_get = lambda handler: 1 / 0  # type: ignore[method-assign]
        with self.assertLogs("support_agent", "ERROR") as logs:
            status, payload = self.request("GET", "/health")
        self.assertEqual((status, payload["error"]["code"]), (500, "internal_error"))
        self.assertEqual(logs.records[0].request_id, payload["request_id"])
        self.assertNotIn("ZeroDivision", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
