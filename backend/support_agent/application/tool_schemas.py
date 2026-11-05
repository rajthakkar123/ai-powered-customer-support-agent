"""Read-only tool schemas. Consequential action tools are intentionally absent."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_details",
            "description": (
                "Retrieve an authorized customer's account, orders, product complaints, "
                "and support history."
            ),
            "parameters": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": "Retrieve sample catalog facts for a product in a customer query.",
            "parameters": {
                "type": "object",
                "properties": {"product_id": {"type": "string"}},
                "required": ["product_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_details",
            "description": "Retrieve an authorized order with items and shipping status.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_return_eligibility",
            "description": "Check return or refund eligibility for an authorized order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["return", "refund"]},
                },
                "required": ["order_id", "action"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_cancellation_eligibility",
            "description": (
                "Check whether an authorized order can still be cancelled. Use it for every "
                "cancellation request; never decide cancellability yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_policy_details",
            "description": (
                "Retrieve sample return, refund, damaged-item, shipping, or support-scope policy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": [
                            "returns",
                            "refunds",
                            "damaged_items",
                            "shipping",
                            "support_scope",
                        ],
                    }
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
        },
    },
]
