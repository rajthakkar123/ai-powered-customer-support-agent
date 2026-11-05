"""Deterministic tests for authorized backend tools and policy decisions."""

import unittest
from datetime import date

from support_agent.application.tool_schemas import TOOL_SCHEMAS
from support_agent.application.tools import POLICY_TOPICS, SupportTools
from support_agent.domain.errors import (
    NotFoundError,
    ToolFailureError,
    ValidationError,
)
from tests.fakes import build_tools

TOKEN_MAP = {
    "agent-one-secret": {
        "user_id": "agent-001",
        "role": "support_agent",
        "customer_ids": ["cus-1001"],
    },
    "agent-two-secret": {
        "user_id": "agent-002",
        "role": "support_agent",
        "customer_ids": ["cus-1002"],
    },
    "admin-secret-token": {"user_id": "admin-001", "role": "support_admin", "customer_ids": []},
}


class SupportToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = build_tools(TOKEN_MAP, date(2026, 11, 4))
        self.agent = self.tools.authenticator.authenticate("Bearer agent-one-secret")

    def test_get_valid_order_includes_items_and_shipping(self) -> None:
        order = self.tools.call("get_order_details", {"order_id": "ord-5001"}, self.agent)
        self.assertEqual(order["customer_id"], "cus-1001")
        self.assertEqual(order["items"][0]["item_id"], "item-9001")
        self.assertEqual(order["shipping"]["shipping_id"], "ship-3001")

    def test_product_lookup_returns_sample_catalog_facts(self) -> None:
        product = self.tools.call("get_product_details", {"product_id": "prd-1001"}, self.agent)
        self.assertEqual(product["name"], "Ceramic Pour-over Set")
        self.assertGreater(product["price_cents"], 0)

    def test_missing_order_returns_not_found(self) -> None:
        with self.assertRaises(NotFoundError):
            self.tools.call("get_order_details", {"order_id": "ord-9999"}, self.agent)

    def test_eligible_and_ineligible_return_scenarios(self) -> None:
        eligible = self.tools.call(
            "check_return_eligibility", {"order_id": "ord-5001", "action": "return"}, self.agent
        )
        expired = self.tools.call(
            "check_return_eligibility", {"order_id": "ord-5002", "action": "refund"}, self.agent
        )
        self.assertTrue(eligible["eligible"])
        self.assertEqual(eligible["policy"]["deadline"], "2026-11-24")
        self.assertFalse(expired["eligible"])
        self.assertIn("The return window has expired.", expired["reasons"])
        self.assertTrue(expired["policy"]["human_approval_required"])

    def test_not_delivered_order_is_ineligible(self) -> None:
        agent_two = self.tools.authenticator.authenticate("Bearer agent-two-secret")
        result = self.tools.call(
            "check_return_eligibility", {"order_id": "ord-5003", "action": "return"}, agent_two
        )
        self.assertFalse(result["eligible"])
        self.assertIn("Order has not been delivered.", result["reasons"])

    def test_customer_lookup_returns_order_and_conversation_context(self) -> None:
        customer = self.tools.call("get_customer_details", {"customer_id": "cus-1001"}, self.agent)
        self.assertEqual(customer["orders"], ["ord-5005", "ord-5001", "ord-5002"])
        self.assertEqual(
            [(order["order_id"], order["status"]) for order in customer["order_summaries"]],
            [("ord-5005", "processing"), ("ord-5001", "delivered"), ("ord-5002", "delivered")],
        )
        self.assertEqual(customer["conversations"][0]["conversation_id"], "conv-7001")

    def test_policy_tool_returns_fixture_policy_facts(self) -> None:
        policy = self.tools.call("get_policy_details", {"topic": "returns"}, self.agent)
        self.assertEqual(policy["policy"]["return_window_days"], 30)
        self.assertEqual(policy["policy"]["window_basis"], "delivery_date")

    def test_cross_customer_access_is_hidden_as_not_found(self) -> None:
        with self.assertRaises(NotFoundError):
            self.tools.call("get_order_details", {"order_id": "ord-5003"}, self.agent)

    def test_unknown_and_malformed_arguments_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.tools.call("delete_order", {}, self.agent)
        with self.assertRaises(ValidationError):
            self.tools.call(
                "get_order_details", {"order_id": "ord-5001", "extra": "value"}, self.agent
            )
        with self.assertRaises(ValidationError):
            self.tools.call("get_order_details", {"order_id": {"id": "ord-5001"}}, self.agent)
        with self.assertRaises(ValidationError):
            self.tools.call(
                "check_return_eligibility", {"order_id": "ord-5001", "action": "erase"}, self.agent
            )

    def test_cancellation_eligibility_is_decided_by_order_status(self) -> None:
        processing = self.tools.call(
            "check_cancellation_eligibility", {"order_id": "ord-5005"}, self.agent
        )
        self.assertTrue(processing["eligible"])
        delivered = self.tools.call(
            "check_cancellation_eligibility", {"order_id": "ord-5001"}, self.agent
        )
        self.assertFalse(delivered["eligible"])
        self.assertIn("delivered can no longer be cancelled", delivered["reasons"][0])
        self.tools.apply_order_outcome("ord-5005", {"status": "cancelled"})
        cancelled = self.tools.call(
            "check_cancellation_eligibility", {"order_id": "ord-5005"}, self.agent
        )
        self.assertEqual(cancelled["reasons"], ["The order is already cancelled."])
        with self.assertRaises(NotFoundError):
            self.tools.call("check_cancellation_eligibility", {"order_id": "ord-5003"}, self.agent)

    def test_identifiers_must_carry_their_own_prefix(self) -> None:
        for name, arguments in (
            ("get_order_details", {"order_id": "cus-1001"}),
            ("get_customer_details", {"customer_id": "ord-5001"}),
            ("get_product_details", {"product_id": "ord-5001"}),
        ):
            with self.subTest(name=name), self.assertRaises(ValidationError) as raised:
                self.tools.call(name, arguments, self.agent)
            self.assertIn("must look like", raised.exception.message)

    def test_model_tool_schemas_match_server_validation(self) -> None:
        # The model sees TOOL_SCHEMAS; the server validates with its own table. They must agree.
        schemas = {schema["function"]["name"]: schema["function"] for schema in TOOL_SCHEMAS}
        self.assertEqual(set(schemas), set(self.tools.tool_names))
        for name, (required, _) in self.tools._definitions.items():
            with self.subTest(tool=name):
                parameters = schemas[name]["parameters"]
                self.assertEqual(set(parameters["required"]), required)
                self.assertEqual(set(parameters["properties"]), required)
                self.assertFalse(parameters["additionalProperties"])
        topics = schemas["get_policy_details"]["parameters"]["properties"]["topic"]["enum"]
        self.assertEqual(tuple(topics), POLICY_TOPICS)
        for topic in POLICY_TOPICS:
            with self.subTest(topic=topic):
                self.assertIn(
                    "policy", self.tools.call("get_policy_details", {"topic": topic}, self.agent)
                )

    def test_tool_failure_has_safe_typed_error(self) -> None:
        failing_tools = SupportTools(
            self.tools.authenticator, date(2026, 11, 4), {"get_order_details"}
        )
        with self.assertRaises(ToolFailureError):
            failing_tools.call("get_order_details", {"order_id": "ord-5001"}, self.agent)


if __name__ == "__main__":
    unittest.main()
