"""HTTP contract tests for authentication, request validation, and trace IDs."""

import re
import unittest
from datetime import date

from support_agent.api.http import (
    API_VERSION,
    STATIC_ASSETS,
    WEB_ROOT,
    execute_customer_list,
    execute_product_list,
    execute_tool_request,
)
from support_agent.application.tools import SupportTools
from tests.fakes import StaticTokenAuthenticator

TOKEN_MAP = {
    "agent-one-secret": {
        "user_id": "agent-001",
        "role": "support_agent",
        "customer_ids": ["cus-1001"],
    },
}


class SupportApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = SupportTools(StaticTokenAuthenticator(TOKEN_MAP), date(2026, 11, 4))

    def post(
        self, payload: object, token: str | None = "agent-one-secret"
    ) -> tuple[int, dict[str, object], str]:
        authorization = f"Bearer {token}" if token is not None else None
        return execute_tool_request(self.tools, authorization, payload)

    def test_tool_endpoint_returns_data_and_correlated_trace(self) -> None:
        status, result, request_id = self.post(
            {"tool": "get_order_details", "arguments": {"order_id": "ord-5001"}}
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["request_id"], request_id)
        self.assertEqual(result["trace"][0]["tool"], "get_order_details")
        self.assertEqual(result["result"]["order_id"], "ord-5001")

    def test_endpoint_requires_bearer_authentication(self) -> None:
        status, result, _ = self.post(
            {"tool": "get_order_details", "arguments": {"order_id": "ord-5001"}}, None
        )
        self.assertEqual(status, 401)
        self.assertEqual(result["error"]["code"], "unauthorized")

    def test_endpoint_rejects_invalid_body_and_hides_cross_customer_record(self) -> None:
        status, result, _ = self.post(
            {"tool": "get_order_details", "arguments": {"order_id": "not-an-id"}}
        )
        self.assertEqual(status, 400)
        self.assertEqual(result["error"]["code"], "invalid_argument")
        status, result, _ = self.post(
            {"tool": "get_order_details", "arguments": {"order_id": "ord-5003"}}
        )
        self.assertEqual(status, 404)
        self.assertEqual(result["error"]["code"], "not_found")

    def test_tool_failure_is_a_clear_traceable_service_error(self) -> None:
        failing = SupportTools(
            StaticTokenAuthenticator(TOKEN_MAP), date(2026, 11, 4), {"get_order_details"}
        )
        status, result, request_id = execute_tool_request(
            failing,
            "Bearer agent-one-secret",
            {"tool": "get_order_details", "arguments": {"order_id": "ord-5001"}},
        )
        self.assertEqual(status, 503)
        self.assertEqual(result["error"]["code"], "tool_failure")
        self.assertEqual(result["request_id"], request_id)
        self.assertEqual(result["trace"][0]["status"], "tool_failure")

    def test_customer_list_is_limited_to_server_authorized_scope(self) -> None:
        status, result, request_id = execute_customer_list(self.tools, "Bearer agent-one-secret")
        self.assertEqual(status, 200)
        self.assertEqual(result["request_id"], request_id)
        self.assertEqual([customer["customer_id"] for customer in result["result"]], ["cus-1001"])
        status, result, _ = execute_customer_list(self.tools, None)
        self.assertEqual(status, 401)
        self.assertEqual(result["error"]["code"], "unauthorized")

    def test_product_list_requires_support_authentication(self) -> None:
        status, result, _ = execute_product_list(self.tools, "Bearer agent-one-secret")
        self.assertEqual(status, 200)
        self.assertTrue(result["result"])
        self.assertEqual(result["result"][0]["product_id"], "prd-1001")
        status, result, _ = execute_product_list(self.tools, None)
        self.assertEqual(status, 401)
        self.assertEqual(result["error"]["code"], "unauthorized")

    def test_every_allow_listed_static_route_maps_to_an_existing_frontend_asset(self) -> None:
        for relative_path, _content_type in STATIC_ASSETS.values():
            with self.subTest(asset=relative_path):
                self.assertTrue((WEB_ROOT / relative_path).is_file())
        index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("Support workspace", index)
        for referenced in re.findall(r'(?:src|href)="(/assets/[^"]+)"', index):
            with self.subTest(referenced=referenced):
                self.assertIn(referenced, STATIC_ASSETS)

    def test_frontend_expects_the_api_version_the_backend_serves(self) -> None:
        script = (WEB_ROOT / "assets" / "support.js").read_text(encoding="utf-8")
        expected = re.search(r"const EXPECTED_API_VERSION = (\d+);", script)
        self.assertIsNotNone(expected, "support.js must declare EXPECTED_API_VERSION")
        self.assertEqual(int(expected[1]), API_VERSION)


if __name__ == "__main__":
    unittest.main()
