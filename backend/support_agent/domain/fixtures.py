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
    "prd-1006": {
        "product_id": "prd-1006",
        "name": "Burr Grinder",
        "description": "A conical burr grinder with 30 grind settings.",
        "price_cents": 12900,
        "currency": "USD",
        "stock_quantity": 8,
        "accent": "clay",
    },
    "prd-1007": {
        "product_id": "prd-1007",
        "name": "Glass Carafe",
        "description": "A heat-proof 600 ml borosilicate carafe.",
        "price_cents": 3400,
        "currency": "USD",
        "stock_quantity": 30,
        "accent": "blue",
    },
    "prd-1008": {
        "product_id": "prd-1008",
        "name": "Gooseneck Thermometer",
        "description": "A clip-on thermometer for kettles and carafes.",
        "price_cents": 1850,
        "currency": "USD",
        "stock_quantity": 50,
        "accent": "sage",
    },
    "prd-1009": {
        "product_id": "prd-1009",
        "name": "House Blend Beans (1 kg)",
        "description": "A medium roast whole-bean blend.",
        "price_cents": 2400,
        "currency": "USD",
        "stock_quantity": 120,
        "accent": "sand",
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


# ---- Manual-testing orders ----------------------------------------------------------------
# A wider spread of order states for hands-on testing, kept in their own ID range (ord-51xx,
# item-91xx, ship-31xx) so the core scenario orders above stay stable for tests and evals.
# Dates are relative to the demo as-of date 2026-11-04 (return window: 30 days from delivery).
# Each spec: order_id, customer_id, status, ordered_at, delivered_at, return_status,
# items as (product_id, quantity), and the zero-based items already refunded.
MANUAL_TEST_CUSTOMERS = {
    "cus-1004": {
        "customer_id": "cus-1004",
        "name": "Riley Chen",
        "email": "riley@example.test",
        "created_at": "2025-08-30",
        "support_conversation_ids": [],
    },
    "cus-1005": {
        "customer_id": "cus-1005",
        "name": "Sam Rivera",
        "email": "sam@example.test",
        "created_at": "2026-01-14",
        "support_conversation_ids": [],
    },
}
MANUAL_TEST_ORDERS: tuple[tuple[Any, ...], ...] = (
    # Avery Johnson
    (
        "ord-5101",
        "cus-1001",
        "delivered",
        "2026-10-29",
        "2026-11-02",
        "not_requested",
        [("prd-1006", 1), ("prd-1009", 2)],
        [],
    ),
    (
        "ord-5102",
        "cus-1001",
        "delivered",
        "2026-10-01",
        "2026-10-05",
        "not_requested",
        [("prd-1007", 1)],
        [],
    ),
    (
        "ord-5103",
        "cus-1001",
        "delivered",
        "2026-09-30",
        "2026-10-04",
        "not_requested",
        [("prd-1004", 2)],
        [],
    ),
    ("ord-5104", "cus-1001", "pending", "2026-11-04", None, "not_requested", [("prd-1005", 3)], []),
    (
        "ord-5105",
        "cus-1001",
        "delivered",
        "2026-10-24",
        "2026-10-28",
        "partially_refunded",
        [("prd-1001", 1), ("prd-1008", 1), ("prd-1002", 1)],
        [0],
    ),
    (
        "ord-5106",
        "cus-1001",
        "delivered",
        "2026-10-11",
        "2026-10-15",
        "refunded",
        [("prd-1003", 1)],
        [0],
    ),
    # Morgan Lee
    (
        "ord-5107",
        "cus-1002",
        "in_transit",
        "2026-11-01",
        None,
        "not_requested",
        [("prd-1006", 1)],
        [],
    ),
    (
        "ord-5108",
        "cus-1002",
        "delivered",
        "2026-10-16",
        "2026-10-20",
        "not_requested",
        [("prd-1004", 1), ("prd-1005", 1)],
        [],
    ),
    (
        "ord-5109",
        "cus-1002",
        "cancelled",
        "2026-10-25",
        None,
        "not_requested",
        [("prd-1009", 3)],
        [],
    ),
    (
        "ord-5110",
        "cus-1002",
        "delivered",
        "2026-06-08",
        "2026-06-12",
        "not_requested",
        [("prd-1007", 1), ("prd-1002", 1)],
        [],
    ),
    (
        "ord-5111",
        "cus-1002",
        "delivered",
        "2026-10-26",
        "2026-10-30",
        "return_requested",
        [("prd-1008", 1)],
        [],
    ),
    # Jordan Patel
    (
        "ord-5112",
        "cus-1003",
        "delivered",
        "2026-10-22",
        "2026-10-26",
        "not_requested",
        [("prd-1006", 1), ("prd-1003", 1), ("prd-1001", 1)],
        [],
    ),
    (
        "ord-5113",
        "cus-1003",
        "processing",
        "2026-11-03",
        None,
        "not_requested",
        [("prd-1009", 1)],
        [],
    ),
    (
        "ord-5114",
        "cus-1003",
        "delivered",
        "2026-08-28",
        "2026-09-01",
        "not_requested",
        [("prd-1005", 2)],
        [],
    ),
    # Riley Chen (new customer)
    (
        "ord-5115",
        "cus-1004",
        "delivered",
        "2026-10-18",
        "2026-10-22",
        "not_requested",
        [("prd-1007", 1), ("prd-1002", 2)],
        [],
    ),
    (
        "ord-5116",
        "cus-1004",
        "in_transit",
        "2026-11-02",
        None,
        "not_requested",
        [("prd-1004", 1)],
        [],
    ),
    (
        "ord-5117",
        "cus-1004",
        "delivered",
        "2026-08-11",
        "2026-08-15",
        "not_requested",
        [("prd-1003", 1)],
        [],
    ),
    # Sam Rivera (new customer)
    (
        "ord-5118",
        "cus-1005",
        "delivered",
        "2026-10-28",
        "2026-11-01",
        "not_requested",
        [("prd-1001", 1), ("prd-1008", 1), ("prd-1009", 2), ("prd-1005", 1)],
        [],
    ),
    ("ord-5119", "cus-1005", "pending", "2026-11-04", None, "not_requested", [("prd-1006", 1)], []),
    (
        "ord-5120",
        "cus-1005",
        "delivered",
        "2026-10-08",
        "2026-10-12",
        "partially_refunded",
        [("prd-1009", 1), ("prd-1004", 1)],
        [0],
    ),
)
SHIPMENT_STATUS = {
    "delivered": "delivered",
    "in_transit": "in_transit",
    "processing": "processing",
    "pending": "label_created",
    "cancelled": "cancelled",
}


def _add_manual_test_orders() -> None:
    """Expand the specs into orders, items, and shipments; totals come from the items."""
    CUSTOMERS.update(MANUAL_TEST_CUSTOMERS)
    item_number = 9101
    for index, spec in enumerate(MANUAL_TEST_ORDERS):
        order_id, customer_id, status, ordered_at, delivered_at, return_status, lines, refunded = (
            spec
        )
        shipping_id = f"ship-{3101 + index}"
        item_ids = []
        for product_id, quantity in lines:
            product = PRODUCTS[product_id]
            item_id = f"item-{item_number}"
            item_number += 1
            ORDER_ITEMS[item_id] = {
                "item_id": item_id,
                "name": product["name"],
                "quantity": quantity,
                "unit_price": product["price_cents"] / 100,
            }
            item_ids.append(item_id)
        refunded_ids = [item_ids[position] for position in refunded]
        total_cents = sum(
            PRODUCTS[product_id]["price_cents"] * quantity for product_id, quantity in lines
        )
        order: dict[str, Any] = {
            "order_id": order_id,
            "customer_id": customer_id,
            "ordered_at": ordered_at,
            "status": status,
            "currency": "USD",
            "total": total_cents / 100,
            "item_ids": item_ids,
            "shipping_id": shipping_id,
            "delivered_at": delivered_at,
            "return_status": return_status,
        }
        if refunded_ids:
            order["refunded_item_ids"] = refunded_ids
            order["refunded_cents"] = sum(
                PRODUCTS[lines[position][0]]["price_cents"] * lines[position][1]
                for position in refunded
            )
        ORDERS[order_id] = order
        shipped = status in {"delivered", "in_transit"}
        SHIPMENTS[shipping_id] = {
            "shipping_id": shipping_id,
            "carrier": "Northstar" if index % 2 else "ParcelPost",
            "tracking_number": f"{'NS' if index % 2 else 'PP'}{20004100 + index}"
            if shipped
            else None,
            "status": SHIPMENT_STATUS[status],
            "estimated_delivery": delivered_at or "2026-11-08",
        }


_add_manual_test_orders()


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
        refunded = order.get("refunded_item_ids", [])
        assert set(refunded) <= set(order["item_ids"]), order["order_id"]
        assert order.get("refunded_cents", 0) == sum(
            round(ORDER_ITEMS[item_id]["unit_price"] * 100) * ORDER_ITEMS[item_id]["quantity"]
            for item_id in refunded
        ), order["order_id"]
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
