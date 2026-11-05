"""Structured assistant-turn contract shared by the model adapter and the assistant workflow."""

import json
import re

INTENTS = (
    "order_status",
    "return_eligibility",
    "refund_request",
    "damaged_item",
    "order_change",
    "policy_question",
    "customer_history",
    "product_question",
    "greeting",
    "out_of_scope",
    "unclear",
)
# Answers for these intents state business facts, so they need backend tool evidence.
FACT_INTENTS = frozenset(INTENTS) - {"greeting", "out_of_scope", "unclear"}
ACTION_TYPES = ("refund", "order_change")
FIELDS = (
    "intent",
    "needs_clarification",
    "agent_summary",
    "suggested_reply",
    "order_id",
    "proposed_action",
)
ORDER_ID_PATTERN = re.compile(r"^ord-\d{4,}$")
ITEM_ID_PATTERN = re.compile(r"^item-\d{4,}$")

TURN_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "needs_clarification": {
            "type": "boolean",
            "description": "True when required information is missing and you are asking for it.",
        },
        "agent_summary": {
            "type": "string",
            "description": (
                "For the support agent only: facts found with tools, policy reasoning, "
                "or the answer to the agent's question."
            ),
        },
        "suggested_reply": {
            "type": ["string", "null"],
            "description": "Customer-facing draft reply, or null when no draft is needed.",
        },
        "order_id": {
            "type": ["string", "null"],
            "description": "The order this turn is about, as returned by a tool, or null.",
        },
        "proposed_action": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "action_type": {"type": "string", "enum": list(ACTION_TYPES)},
                        "order_id": {"type": "string"},
                        "item_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "For a refund, only the affected items (e.g. the damaged one), "
                                "using item_id values from tool results. Empty means every "
                                "refundable item. Always empty for order_change."
                            ),
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["action_type", "order_id", "item_ids", "reason"],
                    "additionalProperties": False,
                },
            ],
            "description": "A refund or order change for a human to approve, or null.",
        },
    },
    "required": list(FIELDS),
    "additionalProperties": False,
}
TURN_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "support_turn", "strict": True, "schema": TURN_SCHEMA},
}


class TurnFormatError(ValueError):
    """The model's final message does not satisfy the structured-turn contract."""


def parse_turn(content: object) -> dict[str, object]:
    """Validate and normalize a final model message; never trust provider-side enforcement."""
    if not isinstance(content, str):
        raise TurnFormatError("the final message must be a JSON string")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise TurnFormatError("the final message is not valid JSON") from error
    if not isinstance(data, dict) or set(data) != set(FIELDS):
        raise TurnFormatError(f"the JSON object must contain exactly: {', '.join(FIELDS)}")
    if data["intent"] not in INTENTS:
        raise TurnFormatError("intent is not one of the allowed values")
    if not isinstance(data["needs_clarification"], bool):
        raise TurnFormatError("needs_clarification must be a boolean")
    summary = data["agent_summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise TurnFormatError("agent_summary must be a non-empty string")
    reply = data["suggested_reply"]
    if reply is not None and not isinstance(reply, str):
        raise TurnFormatError("suggested_reply must be a string or null")
    order_id = data["order_id"]
    if order_id is not None and not isinstance(order_id, str):
        raise TurnFormatError("order_id must be a string or null")
    return {
        "intent": data["intent"],
        "needs_clarification": data["needs_clarification"],
        "agent_summary": summary.strip(),
        "suggested_reply": reply.strip() or None if isinstance(reply, str) else None,
        # A malformed reference is dropped rather than trusted; tools remain the source of truth.
        "order_id": order_id if order_id and ORDER_ID_PATTERN.fullmatch(order_id) else None,
        "proposed_action": _parse_action(data["proposed_action"]),
    }


def _parse_action(action: object) -> dict[str, object] | None:
    if action is None:
        return None
    if not isinstance(action, dict) or set(action) != {
        "action_type",
        "order_id",
        "item_ids",
        "reason",
    }:
        raise TurnFormatError(
            "proposed_action must be null or {action_type, order_id, item_ids, reason}"
        )
    item_ids = action["item_ids"]
    if (
        not isinstance(item_ids, list)
        or not all(isinstance(item, str) and ITEM_ID_PATTERN.fullmatch(item) for item in item_ids)
        or len(set(item_ids)) != len(item_ids)
    ):
        raise TurnFormatError("proposed_action.item_ids must be a list of distinct item IDs")
    if action["action_type"] not in ACTION_TYPES:
        raise TurnFormatError("proposed_action.action_type must be refund or order_change")
    if not isinstance(action["order_id"], str) or not ORDER_ID_PATTERN.fullmatch(
        action["order_id"]
    ):
        raise TurnFormatError("proposed_action.order_id must be an order identifier")
    if not isinstance(action["reason"], str) or not action["reason"].strip():
        raise TurnFormatError("proposed_action.reason must be a non-empty string")
    return {
        "action_type": action["action_type"],
        "order_id": action["order_id"],
        "item_ids": item_ids if action["action_type"] == "refund" else [],
        "reason": action["reason"].strip(),
    }
