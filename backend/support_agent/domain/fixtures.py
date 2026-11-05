"""Small, deterministic sample dataset. IDs are stable and relations are validated at import."""

from datetime import date
from typing import Any

CUSTOMERS: dict[str, dict[str, Any]] = {
    "cus-1001": {
        "customer_id": "cus-1001",
        "name": "Avery Johnson",
        "email": "avery@example.test",
        "created_at": "2024-02-18",
        "support_conversation_ids": ["conv-7001"],
    },
    "cus-1002": {
        "customer_id": "cus-1002",
        "name": "Morgan Lee",
        "email": "morgan@example.test",
        "created_at": "2023-11-06",
        "support_conversation_ids": ["conv-7002"],
    },
    "cus-1003": {
        "customer_id": "cus-1003",
        "name": "Jordan Patel",
        "email": "jordan@example.test",
        "created_at": "2025-05-12",
        "support_conversation_ids": [],
    },
}

ORDERS: dict[str, dict[str, Any]] = {
    "ord-5001": {
        "order_id": "ord-5001",
        "customer_id": "cus-1001",
        "ordered_at": "2026-10-20",
        "status": "delivered",
        "currency": "USD",
        "total": 89.98,
        "item_ids": ["item-9001", "item-9002"],
        "shipping_id": "ship-3001",
        "delivered_at": "2026-10-25",
        "return_status": "not_requested",
    },
    "ord-5002": {
        "order_id": "ord-5002",
        "customer_id": "cus-1001",
        "ordered_at": "2026-08-01",
        "status": "delivered",
        "currency": "USD",
        "total": 149.00,
        "item_ids": ["item-9003"],
        "shipping_id": "ship-3002",
        "delivered_at": "2026-08-06",
        "return_status": "not_requested",
    },
    "ord-5003": {
        "order_id": "ord-5003",
        "customer_id": "cus-1002",
        "ordered_at": "2026-10-29",
        "status": "in_transit",
        "currency": "USD",
        "total": 39.50,
        "item_ids": ["item-9004"],
        "shipping_id": "ship-3003",
        "delivered_at": None,
        "return_status": "not_requested",
    },
    "ord-5005": {
        "order_id": "ord-5005",
        "customer_id": "cus-1001",
        "ordered_at": "2026-11-03",
        "status": "processing",
        "currency": "USD",
        "total": 59.98,
        "item_ids": ["item-9006"],
        "shipping_id": "ship-3005",
        "delivered_at": None,
        "return_status": "not_requested",
    },
    "ord-5004": {
        "order_id": "ord-5004",
        "customer_id": "cus-1003",
        "ordered_at": "2026-09-10",
        "status": "delivered",
        "currency": "USD",
        "total": 24.00,
        "item_ids": ["item-9005"],
        "shipping_id": "ship-3004",
        "delivered_at": "2026-09-15",
        "return_status": "not_requested",
    },
}

ORDER_ITEMS: dict[str, dict[str, Any]] = {
    "item-9001": {
        "item_id": "item-9001",
        "name": "Ceramic Pour-over Set",
        "quantity": 1,
        "unit_price": 59.99,
    },
    "item-9002": {
        "item_id": "item-9002",
        "name": "Paper Filters (40 pack)",
        "quantity": 1,
        "unit_price": 29.99,
    },
    "item-9003": {
        "item_id": "item-9003",
        "name": "Cast Iron Kettle",
        "quantity": 1,
        "unit_price": 149.00,
    },
    "item-9004": {"item_id": "item-9004", "name": "Travel Mug", "quantity": 1, "unit_price": 39.50},
    "item-9005": {
        "item_id": "item-9005",
        "name": "Coffee Scoop",
        "quantity": 2,
        "unit_price": 12.00,
    },
    "item-9006": {
        "item_id": "item-9006",
        "name": "Paper Filters (40 pack)",
        "quantity": 2,
        "unit_price": 29.99,
    },
}

PRODUCTS: dict[str, dict[str, Any]] = {
    "prd-1001": {
        "product_id": "prd-1001",
        "name": "Ceramic Pour-over Set",
        "description": "A balanced dripper and server set for a slower morning ritual.",
        "price_cents": 5999,
        "currency": "USD",
        "stock_quantity": 25,
        "accent": "sage",
    },
    "prd-1002": {
        "product_id": "prd-1002",
        "name": "Paper Filters · 40 pack",
        "description": "Clean, even-flow filters sized for the Northstar pour-over.",
        "price_cents": 2999,
        "currency": "USD",
        "stock_quantity": 80,
        "accent": "sand",
    },
    "prd-1003": {
        "product_id": "prd-1003",
        "name": "Cast Iron Kettle",
        "description": "A steady-pour kettle with a comfortable balanced handle.",
        "price_cents": 14900,
        "currency": "USD",
        "stock_quantity": 12,
        "accent": "clay",
    },
    "prd-1004": {
        "product_id": "prd-1004",
        "name": "Travel Mug",
        "description": "A double-wall mug made for the commute and the long walk.",
        "price_cents": 3950,
        "currency": "USD",
        "stock_quantity": 40,
        "accent": "blue",
    },
    "prd-1005": {
        "product_id": "prd-1005",
        "name": "Coffee Scoop",
        "description": "A sturdy two-tablespoon scoop in brushed stainless steel.",
        "price_cents": 1200,
        "currency": "USD",
        "stock_quantity": 60,
        "accent": "stone",
    },
}

SHIPMENTS: dict[str, dict[str, Any]] = {
    "ship-3001": {
        "shipping_id": "ship-3001",
        "carrier": "ParcelPost",
        "tracking_number": "PP10004567",
        "status": "delivered",
        "estimated_delivery": "2026-10-25",
    },
    "ship-3002": {
        "shipping_id": "ship-3002",
        "carrier": "ParcelPost",
        "tracking_number": "PP10003991",
        "status": "delivered",
        "estimated_delivery": "2026-08-06",
    },
    "ship-3003": {
        "shipping_id": "ship-3003",
        "carrier": "Northstar",
        "tracking_number": "NS88000142",
        "status": "in_transit",
        "estimated_delivery": "2026-11-06",
    },
    "ship-3004": {
        "shipping_id": "ship-3004",
        "carrier": "Northstar",
        "tracking_number": "NS87994213",
        "status": "delivered",
        "estimated_delivery": "2026-09-15",
    },
    "ship-3005": {
        "shipping_id": "ship-3005",
        "carrier": "Northstar",
        "tracking_number": None,
        "status": "processing",
        "estimated_delivery": "2026-11-08",
    },
}

CONVERSATIONS: dict[str, dict[str, Any]] = {
    "conv-7001": {
        "conversation_id": "conv-7001",
        "customer_id": "cus-1001",
        "order_id": "ord-5001",
        "messages": [
            {
                "role": "customer",
                "created_at": "2026-10-27T10:15:00Z",
                "text": "One filter box arrived open.",
            },
            {
                "role": "agent",
                "created_at": "2026-10-27T10:22:00Z",
                "text": "I can help review the order and available options.",
            },
        ],
    },
    "conv-7002": {
        "conversation_id": "conv-7002",
        "customer_id": "cus-1002",
        "order_id": "ord-5003",
        "messages": [
            {
                "role": "customer",
                "created_at": "2026-10-30T08:05:00Z",
                "text": "Can I change the delivery address?",
            }
        ],
    },
}

POLICIES: dict[str, Any] = {
    "return_window_days": 30,
    "return_window_basis": "delivery_date",
    "refund_requires_human_approval": True,
    "order_change_requires_human_approval": True,
    "in_transit_orders_returnable": False,
    "damaged_items": {
        "report_within_days": 30,
        "window_basis": "delivery_date",
        "remedy": "Refund the damaged items only; a replacement can be offered on request.",
        "return_shipment_required": False,
        "photo_evidence_requested": True,
    },
    "shipping_estimate_days": 3,
    "support_scope": [
        "orders",
        "shipping",
        "returns",
        "refunds",
        "damaged items",
        "order cancellation",
        "products",
        "support history",
    ],
}


def validate_fixtures() -> None:
    """Fail fast when a sample record points to a missing related record."""
    for order in ORDERS.values():
        assert order["customer_id"] in CUSTOMERS
        assert order["shipping_id"] in SHIPMENTS
        assert all(item_id in ORDER_ITEMS for item_id in order["item_ids"])
        # Refund amounts are computed from items, so totals must agree to the cent.
        item_cents = sum(
            round(ORDER_ITEMS[item_id]["unit_price"] * 100) * ORDER_ITEMS[item_id]["quantity"]
            for item_id in order["item_ids"]
        )
        assert item_cents == round(order["total"] * 100), order["order_id"]
        if order["delivered_at"]:
            date.fromisoformat(order["delivered_at"])
    for conversation in CONVERSATIONS.values():
        assert conversation["customer_id"] in CUSTOMERS
        assert conversation["order_id"] in ORDERS
        assert ORDERS[conversation["order_id"]]["customer_id"] == conversation["customer_id"]
    for customer in CUSTOMERS.values():
        assert all(
            conversation_id in CONVERSATIONS
            for conversation_id in customer["support_conversation_ids"]
        )


validate_fixtures()
