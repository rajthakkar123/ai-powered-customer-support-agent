"""Structured-turn contract validation; the server never trusts provider-side schema enforcement."""

import json
import unittest

from support_agent.application.turns import TURN_SCHEMA, TurnFormatError, parse_turn

VALID_TURN = {
    "intent": "damaged_item",
    "needs_clarification": False,
    "agent_summary": "  Order delivered 2026-10-25; eligible until 2026-11-24.  ",
    "suggested_reply": "  Sorry about the damage.  ",
    "order_id": "ord-5001",
    "proposed_action": {
        "action_type": "refund",
        "order_id": "ord-5001",
        "item_ids": ["item-9002"],
        "reason": "Damaged.",
    },
}


class ParseTurnTests(unittest.TestCase):
    def parse(self, **overrides: object) -> dict[str, object]:
        return parse_turn(json.dumps({**VALID_TURN, **overrides}))

    def test_valid_turn_is_normalized(self) -> None:
        turn = self.parse()
        self.assertEqual(
            turn["agent_summary"], "Order delivered 2026-10-25; eligible until 2026-11-24."
        )
        self.assertEqual(turn["suggested_reply"], "Sorry about the damage.")
        self.assertEqual(turn["proposed_action"]["action_type"], "refund")

    def test_blank_reply_and_malformed_order_reference_become_null(self) -> None:
        turn = self.parse(suggested_reply="   ", order_id="#5001")
        self.assertIsNone(turn["suggested_reply"])
        self.assertIsNone(turn["order_id"])

    def test_contract_violations_are_rejected(self) -> None:
        violations = [
            "not json",
            json.dumps([VALID_TURN]),
            json.dumps({**VALID_TURN, "extra": 1}),
            json.dumps({key: value for key, value in VALID_TURN.items() if key != "intent"}),
            json.dumps({**VALID_TURN, "intent": "issue_refund_now"}),
            json.dumps({**VALID_TURN, "needs_clarification": "no"}),
            json.dumps({**VALID_TURN, "agent_summary": " "}),
            json.dumps({**VALID_TURN, "proposed_action": {"action_type": "delete_account"}}),
            json.dumps(
                {
                    **VALID_TURN,
                    "proposed_action": {"action_type": "refund", "order_id": "x", "reason": "r"},
                }
            ),
        ]
        for content in violations:
            with self.subTest(content=content[:60]), self.assertRaises(TurnFormatError):
                parse_turn(content)
        bad_items = (
            {"item_ids": "item-9002"},
            {"item_ids": ["9002"]},
            {"item_ids": ["item-9002"] * 2},
        )
        for override in bad_items:
            action = {**VALID_TURN["proposed_action"], **override}
            with self.subTest(item_ids=override), self.assertRaises(TurnFormatError):
                parse_turn(json.dumps({**VALID_TURN, "proposed_action": action}))
        with self.assertRaises(TurnFormatError):
            parse_turn(None)

    def test_order_change_never_carries_item_ids(self) -> None:
        action = {**VALID_TURN["proposed_action"], "action_type": "order_change"}
        turn = parse_turn(json.dumps({**VALID_TURN, "proposed_action": action}))
        self.assertEqual(turn["proposed_action"]["item_ids"], [])

    def test_schema_is_strict_mode_compatible(self) -> None:
        # OpenAI strict mode requires every property listed as required and no extras allowed.
        self.assertFalse(TURN_SCHEMA["additionalProperties"])
        self.assertEqual(set(TURN_SCHEMA["required"]), set(TURN_SCHEMA["properties"]))


if __name__ == "__main__":
    unittest.main()
