"""Allow-listed read tools with validated arguments and deterministic policy decisions."""

import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..domain import fixtures
from ..domain.errors import NotFoundError, SupportError, ToolFailureError, ValidationError
from ..infrastructure.database import SupportDatabase
from ..security.auth import SUPPORT_ROLES, Authenticator, Principal, require_customer_access

# Each identifier argument must carry its own prefix, so "cus-1001" is never an order ID.
ID_PATTERNS = {
    "customer_id": re.compile(r"^cus-\d{4,}$"),
    "order_id": re.compile(r"^ord-\d{4,}$"),
    "product_id": re.compile(r"^prd-\d{4,}$"),
}
POLICY_TOPICS = ("returns", "refunds", "damaged_items", "shipping", "support_scope")
# One rule for the cancellation tool the model uses and the approval-time re-check.
CANCELLABLE_STATUSES = frozenset({"pending", "processing", "in_transit"})
# Runtime changes applied by approved actions; fixture data is never mutated.
OUTCOME_FIELDS = ("status", "return_status", "refunded_item_ids", "refunded_cents")


def to_cents(value: object) -> int:
    """Convert a decimal currency amount to integer cents without float rounding drift."""
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class SupportTools:
    def __init__(
        self,
        authenticator: Authenticator,
        as_of: date,
        failing_tools: set[str] | None = None,
        database: SupportDatabase | None = None,
    ) -> None:
        self.authenticator = authenticator
        self.as_of = as_of
        self.failing_tools = failing_tools or set()
        self.database = database
        # Without a database, outcomes live on this instance so tests never share state.
        self._memory_outcomes: dict[str, dict[str, Any]] = {}
        self._outcome_lock = threading.Lock()
        self._definitions: dict[str, tuple[set[str], Callable[..., dict[str, object]]]] = {
            "get_customer_details": ({"customer_id"}, self.get_customer_details),
            "get_order_details": ({"order_id"}, self.get_order_details),
            "get_product_details": ({"product_id"}, self.get_product_details),
            "check_return_eligibility": ({"order_id", "action"}, self.check_return_eligibility),
            "check_cancellation_eligibility": ({"order_id"}, self.check_cancellation_eligibility),
            "get_policy_details": ({"topic"}, self.get_policy_details),
        }

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def customer_exists(self, customer_id: object) -> bool:
        customers, _, _ = self._records()
        return isinstance(customer_id, str) and customer_id in customers

    def call(
        self,
        name: object,
        arguments: object,
        principal: Principal,
        simulated_failures: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Validate and run one allow-listed tool; simulated failures serve development demos."""
        if not isinstance(name, str) or name not in self._definitions:
            raise ValidationError("Unknown support tool.")
        if not isinstance(arguments, dict):
            raise ValidationError("Tool arguments must be a JSON object.")
        required_keys, operation = self._definitions[name]
        if set(arguments) != required_keys:
            raise ValidationError("Tool arguments do not match the tool schema.")
        for key, value in arguments.items():
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"{key} must be a non-empty string.")
            pattern = ID_PATTERNS.get(key)
            if pattern is not None and not pattern.fullmatch(value):
                prefix = pattern.pattern[1:4]
                raise ValidationError(f"{key} must look like {prefix}-1234.")
        if name == "check_return_eligibility" and arguments["action"] not in {"return", "refund"}:
            raise ValidationError("action must be return or refund.")
        if name == "get_policy_details" and arguments["topic"] not in POLICY_TOPICS:
            raise ValidationError(f"topic must be one of: {', '.join(POLICY_TOPICS)}.")
        if name in self.failing_tools or name in simulated_failures:
            raise ToolFailureError()
        return operation(principal=principal, **arguments)

    def _scoped_record(self, principal: Principal, customer_id: str, records: dict) -> dict:
        # Check record existence and scope in one branch to avoid cross-customer enumeration.
        if customer_id not in records:
            raise NotFoundError()
        require_customer_access(principal, customer_id)
        return records[customer_id]

    def get_customer_details(self, customer_id: str, principal: Principal) -> dict[str, Any]:
        customers, orders, conversations = self._records()
        customer = self._scoped_record(principal, customer_id, customers)
        customer_orders = sorted(
            (order for order in orders.values() if order["customer_id"] == customer_id),
            key=lambda order: (str(order["ordered_at"]), str(order["order_id"])),
            reverse=True,
        )
        return {
            **customer,
            "orders": [order["order_id"] for order in customer_orders],
            # Summaries let the model and the workspace see every order without N lookups.
            "order_summaries": [
                {
                    key: order.get(key)
                    for key in (
                        "order_id",
                        "status",
                        "ordered_at",
                        "delivered_at",
                        "total",
                        "currency",
                        "return_status",
                    )
                }
                for order in customer_orders
            ],
            "conversations": [conversations[key] for key in customer["support_conversation_ids"]],
            "complaints": (
                self.database.list_customer_complaints(customer_id) if self.database else []
            ),
        }

    def get_order_details(self, order_id: str, principal: Principal) -> dict[str, Any]:
        _, orders, _ = self._records()
        items = (
            self.database.load_records("order_items", "item_id")
            if self.database
            else fixtures.ORDER_ITEMS
        )
        shipments = (
            self.database.load_records("shipments", "shipping_id")
            if self.database
            else fixtures.SHIPMENTS
        )
        order = orders.get(order_id)
        if order is None:
            raise NotFoundError()
        require_customer_access(principal, str(order["customer_id"]))
        return {
            **order,
            "items": [items[item_id] for item_id in order["item_ids"]],
            "shipping": shipments[order["shipping_id"]],
        }

    def get_product_details(self, product_id: str, principal: Principal) -> dict[str, Any]:
        if principal.role not in SUPPORT_ROLES:
            raise SupportError("forbidden", "A support workspace session is required.", 403)
        if self.database is None:
            product = fixtures.PRODUCTS.get(product_id)
        else:
            product = next(
                (
                    item
                    for item in self.database.list_products()
                    if item["product_id"] == product_id
                ),
                None,
            )
        if product is None:
            raise NotFoundError()
        return dict(product)

    def check_return_eligibility(
        self, order_id: str, action: str, principal: Principal
    ) -> dict[str, Any]:
        _, orders, _ = self._records()
        policies = self.database.load_policies() if self.database else fixtures.POLICIES
        order = orders.get(order_id)
        if order is None:
            raise NotFoundError()
        require_customer_access(principal, str(order["customer_id"]))
        reasons: list[str] = []
        delivered_at = order["delivered_at"]
        deadline = None
        if order["status"] == "cancelled":
            reasons.append("The order was cancelled.")
        elif order["status"] != "delivered" or delivered_at is None:
            reasons.append("Order has not been delivered.")
        else:
            deadline = (
                date.fromisoformat(str(delivered_at)).toordinal() + policies["return_window_days"]
            )
            deadline_date = date.fromordinal(deadline)
            if self.as_of > deadline_date:
                reasons.append("The return window has expired.")
            deadline = deadline_date.isoformat()
        refundable = self.refundable_items(self._with_items(order))
        if order["return_status"] == "refunded" or (
            order["return_status"] == "partially_refunded" and not refundable
        ):
            reasons.append("Every item in this order has already been refunded.")
        elif order["return_status"] not in {"not_requested", "partially_refunded"}:
            reasons.append("A return or refund request already exists for this order.")
        return {
            "order_id": order_id,
            "action": action,
            "eligible": not reasons,
            "reasons": reasons,
            # Items the customer can still be refunded for, priced in integer cents.
            "refundable_item_ids": [item["item_id"] for item in refundable] if not reasons else [],
            "refundable_items": refundable if not reasons else [],
            "policy": {
                "return_window_days": policies["return_window_days"],
                "deadline": deadline,
                "human_approval_required": action == "refund"
                and policies["refund_requires_human_approval"],
            },
        }

    def check_cancellation_eligibility(self, order_id: str, principal: Principal) -> dict[str, Any]:
        """Deterministic cancellation rule: only orders that have not been delivered."""
        _, orders, _ = self._records()
        order = orders.get(order_id)
        if order is None:
            raise NotFoundError()
        require_customer_access(principal, str(order["customer_id"]))
        status = str(order["status"])
        reasons = []
        if status == "cancelled":
            reasons.append("The order is already cancelled.")
        elif status not in CANCELLABLE_STATUSES:
            reasons.append(
                f"Orders that are {status.replace('_', ' ')} can no longer be cancelled."
            )
        return {
            "order_id": order_id,
            "status": status,
            "eligible": not reasons,
            "reasons": reasons,
            "policy": {
                "cancellable_statuses": sorted(CANCELLABLE_STATUSES),
                "human_approval_required": True,
            },
        }

    def refundable_items(self, order: dict[str, Any]) -> list[dict[str, Any]]:
        """Items not yet refunded, each with its line amount in integer cents."""
        refunded = set(order.get("refunded_item_ids") or [])
        return [
            {
                "item_id": item["item_id"],
                "name": item["name"],
                "quantity": item["quantity"],
                "amount_cents": to_cents(item["unit_price"]) * int(item["quantity"]),
            }
            for item in order["items"]
            if item["item_id"] not in refunded
        ]

    def apply_order_outcome(
        self,
        order_id: str,
        outcome: dict[str, Any],
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Record an approved action's effect; with SQLite it joins the caller's transaction."""
        if set(outcome) - set(OUTCOME_FIELDS):
            raise ValueError("Unsupported order outcome field.")
        if self.database is not None:
            if connection is None:
                raise ValueError("A database outcome must be written inside a transaction.")
            current = self.database.order_outcomes().get(order_id, {})
            self.database.record_order_outcome(connection, order_id, {**current, **outcome})
            return
        with self._outcome_lock:
            self._memory_outcomes[order_id] = {
                **self._memory_outcomes.get(order_id, {}),
                **outcome,
            }

    def _with_items(self, order: dict[str, Any]) -> dict[str, Any]:
        items = (
            self.database.load_records("order_items", "item_id")
            if self.database
            else fixtures.ORDER_ITEMS
        )
        return {**order, "items": [items[item_id] for item_id in order["item_ids"]]}

    def get_policy_details(self, topic: str, principal: Principal) -> dict[str, Any]:
        """Return sample business policy facts without inferring them from model knowledge."""
        policies = self.database.load_policies() if self.database else fixtures.POLICIES
        policy_views: dict[str, Any] = {
            "returns": {
                "return_window_days": policies["return_window_days"],
                "window_basis": policies["return_window_basis"],
                "eligible_order_status": "delivered",
                "in_transit_orders_returnable": policies["in_transit_orders_returnable"],
            },
            "refunds": {
                "return_window_days": policies["return_window_days"],
                "window_basis": policies["return_window_basis"],
                "human_approval_required": policies["refund_requires_human_approval"],
                "partial_refunds": "Refunds can cover individual items.",
            },
            "damaged_items": policies["damaged_items"],
            "shipping": {"estimated_delivery_days": policies["shipping_estimate_days"]},
            "support_scope": {"supported_topics": policies["support_scope"]},
        }
        return {"topic": topic, "policy": policy_views[topic]}

    def _records(self) -> tuple[dict, dict, dict]:
        """Customers, orders with approved outcomes applied, and support conversations."""
        if self.database is None:
            with self._outcome_lock:
                outcomes = dict(self._memory_outcomes)
            return (
                fixtures.CUSTOMERS,
                _apply_outcomes(fixtures.ORDERS, outcomes),
                fixtures.CONVERSATIONS,
            )
        return (
            self.database.load_records("customers", "customer_id"),
            _apply_outcomes(
                self.database.load_records("orders", "order_id"), self.database.order_outcomes()
            ),
            self.database.load_records("support_conversations", "conversation_id"),
        )

    @staticmethod
    def error_payload(error: SupportError) -> dict[str, str]:
        return {"code": error.code, "message": error.message}


def _apply_outcomes(
    orders: dict[str, dict[str, Any]], outcomes: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    # New dicts every time, so neither fixtures nor cached rows are ever mutated.
    return {order_id: {**order, **outcomes.get(order_id, {})} for order_id, order in orders.items()}
