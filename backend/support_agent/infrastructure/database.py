"""SQLite schema and idempotent sample-data synchronization."""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain import fixtures

SCHEMA_VERSION = 8
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "support_agent.sqlite3"


class SupportDatabase:
    """SQLite persistence for sample support records and runtime data access."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = path or os.environ.get("SUPPORT_DATABASE_PATH") or DEFAULT_DB_PATH
        self.path = Path(configured_path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        with closing(self.connect()) as connection, connection:
            yield connection

    def initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
            created_at TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shipments (
            shipping_id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(customer_id),
            shipping_id TEXT NOT NULL REFERENCES shipments(shipping_id), status TEXT NOT NULL,
            ordered_at TEXT NOT NULL, delivered_at TEXT, return_status TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
        CREATE TABLE IF NOT EXISTS order_items (
            item_id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_line_items (
            order_id TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
            item_id TEXT NOT NULL REFERENCES order_items(item_id),
            PRIMARY KEY(order_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS products (
            product_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
            stock_quantity INTEGER NOT NULL CHECK(stock_quantity >= 0),
            active INTEGER NOT NULL DEFAULT 1,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS customer_complaints (
            complaint_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL REFERENCES customers(customer_id),
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_complaints_customer
            ON customer_complaints(customer_id, created_at);
        CREATE TABLE IF NOT EXISTS support_conversations (
            conversation_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL REFERENCES customers(customer_id),
            order_id TEXT REFERENCES orders(order_id), payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_messages (
            conversation_id TEXT NOT NULL,
            message_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT,
            content TEXT NOT NULL,
            FOREIGN KEY(conversation_id)
                REFERENCES support_conversations(conversation_id) ON DELETE CASCADE,
            PRIMARY KEY(conversation_id, message_index)
        );
        CREATE TABLE IF NOT EXISTS conversation_events (
            conversation_id TEXT NOT NULL
                REFERENCES support_conversations(conversation_id) ON DELETE CASCADE,
            event_index INTEGER NOT NULL,
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY(conversation_id, event_index)
        );
        CREATE TABLE IF NOT EXISTS policies (
            policy_key TEXT PRIMARY KEY, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS staff_accounts (
            user_id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL
                CHECK(role IN ('support_agent', 'support_approver', 'support_admin')),
            password_hash TEXT NOT NULL,
            customer_ids TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT,
            created_by TEXT REFERENCES staff_accounts(user_id),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS staff_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES staff_accounts(user_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_staff_sessions_user ON staff_sessions(user_id);
        CREATE TABLE IF NOT EXISTS action_proposals (
            proposal_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL REFERENCES customers(customer_id),
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            status TEXT NOT NULL,
            payload TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS action_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id TEXT NOT NULL REFERENCES action_proposals(proposal_id) ON DELETE CASCADE,
            event TEXT NOT NULL, actor_user_id TEXT NOT NULL, created_at TEXT NOT NULL,
            detail TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS idempotency_records (
            scope TEXT NOT NULL, idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(scope, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS action_executions (
            proposal_id TEXT PRIMARY KEY REFERENCES action_proposals(proposal_id),
            result_json TEXT NOT NULL, executed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_outcomes (
            order_id TEXT PRIMARY KEY REFERENCES orders(order_id),
            payload TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
        with self.session() as connection:
            connection.executescript(schema)
            # Remove the retired shopper credentials and the token-era sample staff table.
            connection.execute("DROP TABLE IF EXISTS customer_credentials")
            connection.execute("DROP TABLE IF EXISTS app_users")
            connection.execute("DROP TABLE IF EXISTS store_order_idempotency")
            # Executions used to live only in a JSON blob; the PRIMARY KEY now enforces once.
            connection.execute(
                "INSERT OR IGNORE INTO action_executions (proposal_id, result_json, executed_at) "
                "SELECT proposal_id, json_extract(payload, '$.execution'), updated_at "
                "FROM action_proposals WHERE status='executed' "
                "AND json_extract(payload, '$.execution') IS NOT NULL"
            )
            connection.execute("DROP TABLE IF EXISTS runtime_state")
            # At most one pending proposal per order and action, enforced by the database.
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_action_one_pending ON action_proposals("
                "order_id, json_extract(payload, '$.action_type')) "
                "WHERE status='pending_approval'"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (SCHEMA_VERSION,)
            )

    def sync_sample_data(self) -> dict[str, int]:
        """Upsert versioned sample fixtures without deleting user-created runtime data."""
        with self.session() as connection:
            for customer in fixtures.CUSTOMERS.values():
                connection.execute(
                    "INSERT INTO customers VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(customer_id) DO UPDATE SET name=excluded.name, "
                    "email=excluded.email, "
                    "created_at=excluded.created_at, payload=excluded.payload",
                    (
                        customer["customer_id"],
                        customer["name"],
                        customer["email"],
                        customer["created_at"],
                        _dump(customer),
                    ),
                )
            for shipment in fixtures.SHIPMENTS.values():
                connection.execute(
                    "INSERT INTO shipments VALUES (?, ?, ?) ON CONFLICT(shipping_id) "
                    "DO UPDATE SET status=excluded.status, payload=excluded.payload",
                    (shipment["shipping_id"], shipment["status"], _dump(shipment)),
                )
            for item in fixtures.ORDER_ITEMS.values():
                connection.execute(
                    "INSERT INTO order_items VALUES (?, ?, ?) ON CONFLICT(item_id) "
                    "DO UPDATE SET name=excluded.name, payload=excluded.payload",
                    (item["item_id"], item["name"], _dump(item)),
                )
            for product in fixtures.PRODUCTS.values():
                connection.execute(
                    "INSERT INTO products "
                    "(product_id, name, price_cents, stock_quantity, active, payload) "
                    "VALUES (?, ?, ?, ?, 1, ?) ON CONFLICT(product_id) DO UPDATE SET "
                    "name=excluded.name, price_cents=excluded.price_cents, "
                    "active=excluded.active, payload=excluded.payload",
                    (
                        product["product_id"],
                        product["name"],
                        product["price_cents"],
                        product["stock_quantity"],
                        _dump(product),
                    ),
                )
            for order in fixtures.ORDERS.values():
                connection.execute(
                    "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(order_id) DO UPDATE SET customer_id=excluded.customer_id, "
                    "shipping_id=excluded.shipping_id, status=excluded.status, "
                    "ordered_at=excluded.ordered_at, delivered_at=excluded.delivered_at, "
                    "return_status=excluded.return_status, payload=excluded.payload",
                    (
                        order["order_id"],
                        order["customer_id"],
                        order["shipping_id"],
                        order["status"],
                        order["ordered_at"],
                        order["delivered_at"],
                        order["return_status"],
                        _dump(order),
                    ),
                )
                connection.execute(
                    "DELETE FROM order_line_items WHERE order_id=?", (order["order_id"],)
                )
                connection.executemany(
                    "INSERT INTO order_line_items(order_id, item_id) VALUES (?, ?)",
                    [(order["order_id"], item_id) for item_id in order["item_ids"]],
                )
            for conversation in fixtures.CONVERSATIONS.values():
                connection.execute(
                    "INSERT INTO support_conversations VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(conversation_id) DO UPDATE SET customer_id=excluded.customer_id, "
                    "order_id=excluded.order_id, payload=excluded.payload",
                    (
                        conversation["conversation_id"],
                        conversation["customer_id"],
                        conversation["order_id"],
                        _dump(conversation),
                    ),
                )
                connection.execute(
                    "DELETE FROM conversation_messages WHERE conversation_id=?",
                    (conversation["conversation_id"],),
                )
                connection.executemany(
                    "INSERT INTO conversation_messages VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            conversation["conversation_id"],
                            index,
                            message["role"],
                            message.get("created_at"),
                            message["text"],
                        )
                        for index, message in enumerate(conversation["messages"])
                    ],
                )
            for key, value in fixtures.POLICIES.items():
                connection.execute(
                    "INSERT INTO policies VALUES (?, ?) ON CONFLICT(policy_key) "
                    "DO UPDATE SET payload=excluded.payload",
                    (key, _dump(value)),
                )
        return {
            "customers": len(fixtures.CUSTOMERS),
            "orders": len(fixtures.ORDERS),
            "order_items": len(fixtures.ORDER_ITEMS),
            "shipments": len(fixtures.SHIPMENTS),
            "support_conversations": len(fixtures.CONVERSATIONS),
            "policy_records": len(fixtures.POLICIES),
            "products": len(fixtures.PRODUCTS),
        }

    def load_records(self, table: str, key: str) -> dict[str, dict[str, Any]]:
        allowed = {
            "customers": "customer_id",
            "orders": "order_id",
            "order_items": "item_id",
            "shipments": "shipping_id",
            "support_conversations": "conversation_id",
        }
        if allowed.get(table) != key:
            raise ValueError("Unsupported record table")
        with self.session() as connection:
            # Both identifiers come from the allow-list above, never from callers' input.
            rows = connection.execute(f"SELECT {key}, payload FROM {table}").fetchall()  # noqa: S608
        return {row[key]: json.loads(row["payload"]) for row in rows}

    def order_outcomes(self) -> dict[str, dict[str, Any]]:
        """Runtime changes to orders, kept apart so sample-data sync never overwrites them."""
        with self.session() as connection:
            rows = connection.execute("SELECT order_id, payload FROM order_outcomes").fetchall()
        return {row["order_id"]: json.loads(row["payload"]) for row in rows}

    @staticmethod
    def record_order_outcome(
        connection: sqlite3.Connection, order_id: str, outcome: dict[str, Any]
    ) -> None:
        """Write an order outcome on the caller's connection, inside its transaction."""
        connection.execute(
            "INSERT INTO order_outcomes (order_id, payload, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(order_id) DO UPDATE SET payload=excluded.payload, "
            "updated_at=excluded.updated_at",
            (order_id, _dump(outcome), datetime.now(UTC).isoformat()),
        )

    def load_policies(self) -> dict[str, Any]:
        with self.session() as connection:
            rows = connection.execute("SELECT policy_key, payload FROM policies").fetchall()
        return {row["policy_key"]: json.loads(row["payload"]) for row in rows}

    def list_customer_rows(self) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT c.customer_id, c.name, c.email, c.created_at, "
                "(SELECT COUNT(*) FROM orders o WHERE o.customer_id=c.customer_id) AS order_count, "
                # Same definition as the support history the customer details show; runtime
                # assistant threads (including empty ones from failed turns) are excluded.
                "(SELECT COUNT(*) FROM support_conversations s "
                "WHERE s.customer_id=c.customer_id "
                "AND json_extract(s.payload, '$.runtime') IS NULL) AS conversation_count "
                "FROM customers c ORDER BY c.customer_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_products(self) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT payload, stock_quantity FROM products WHERE active=1 ORDER BY product_id"
            ).fetchall()
        return [
            {**json.loads(row["payload"]), "stock_quantity": row["stock_quantity"]} for row in rows
        ]

    def list_customer_complaints(self, customer_id: str) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT complaint_id, customer_id, order_id, category, message, status, created_at "
                "FROM customer_complaints WHERE customer_id=? ORDER BY created_at DESC",
                (customer_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def open_conversation(
        self, conversation_id: str, user_id: str, customer_id: str
    ) -> list[dict[str, object]] | None:
        with self.session() as connection:
            row = connection.execute(
                "SELECT customer_id, payload FROM support_conversations WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                payload = {"owner_user_id": user_id, "runtime": True}
                connection.execute(
                    "INSERT INTO support_conversations "
                    "(conversation_id, customer_id, order_id, payload) VALUES (?, ?, NULL, ?)",
                    (conversation_id, customer_id, _dump(payload)),
                )
                return []
            payload = json.loads(row["payload"])
            if row["customer_id"] != customer_id or payload.get("owner_user_id") != user_id:
                return None
            rows = connection.execute(
                "SELECT content FROM conversation_messages WHERE conversation_id=? "
                "ORDER BY message_index",
                (conversation_id,),
            ).fetchall()
        return [json.loads(message["content"]) for message in rows]

    def conversation_customer(self, conversation_id: str, user_id: str) -> str | None:
        with self.session() as connection:
            row = connection.execute(
                "SELECT customer_id, payload FROM support_conversations WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        if payload.get("owner_user_id") != user_id:
            return None
        return str(row["customer_id"])

    def save_conversation(self, conversation_id: str, messages: list[dict[str, object]]) -> None:
        with self.session() as connection:
            connection.execute(
                "DELETE FROM conversation_messages WHERE conversation_id=?",
                (conversation_id,),
            )
            connection.executemany(
                "INSERT INTO conversation_messages "
                "(conversation_id, message_index, role, created_at, content) "
                "VALUES (?, ?, ?, NULL, ?)",
                [
                    (conversation_id, index, str(message.get("role", "unknown")), _dump(message))
                    for index, message in enumerate(messages)
                ],
            )

    def append_conversation_events(
        self, conversation_id: str, events: list[dict[str, Any]]
    ) -> None:
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            start = connection.execute(
                "SELECT COALESCE(MAX(event_index), -1) + 1 FROM conversation_events "
                "WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]
            connection.executemany(
                "INSERT INTO conversation_events "
                "(conversation_id, event_index, kind, created_at, payload) VALUES (?, ?, ?, ?, ?)",
                [
                    (conversation_id, start + offset, event["kind"], event["at"], _dump(event))
                    for offset, event in enumerate(events)
                ],
            )

    def conversation_events(self, conversation_id: str) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT payload FROM conversation_events WHERE conversation_id=? "
                "ORDER BY event_index",
                (conversation_id,),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def latest_conversation_id(self, user_id: str, customer_id: str) -> str | None:
        """Most recent conversation with visible events that this agent owns for a customer."""
        with self.session() as connection:
            row = connection.execute(
                "SELECT s.conversation_id FROM support_conversations s "
                "WHERE s.customer_id=? AND json_extract(s.payload, '$.owner_user_id')=? "
                "AND EXISTS (SELECT 1 FROM conversation_events e "
                "WHERE e.conversation_id=s.conversation_id) "
                "ORDER BY s.rowid DESC LIMIT 1",
                (customer_id, user_id),
            ).fetchone()
        return row["conversation_id"] if row else None

    def customer_ids(self) -> set[str]:
        with self.session() as connection:
            rows = connection.execute("SELECT customer_id FROM customers").fetchall()
        return {row["customer_id"] for row in rows}

    def count_active_admins(self) -> int:
        with self.session() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM staff_accounts WHERE role='support_admin' AND active=1"
            ).fetchone()
        return int(row[0])

    def insert_staff_account(self, account: dict[str, Any]) -> bool:
        """Insert an account; return False when the email is already registered."""
        try:
            with self.session() as connection:
                connection.execute(
                    "INSERT INTO staff_accounts (user_id, email, display_name, role, "
                    "password_hash, customer_ids, active, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        account["user_id"],
                        account["email"],
                        account["display_name"],
                        account["role"],
                        account["password_hash"],
                        _dump(account["customer_ids"]),
                        int(account["active"]),
                        account["created_by"],
                        account["created_at"],
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def staff_account_by_email(self, email: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                "SELECT * FROM staff_accounts WHERE email=?", (email,)
            ).fetchone()
        return _staff_account(row) if row else None

    def list_staff_accounts(self) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT * FROM staff_accounts ORDER BY created_at, email"
            ).fetchall()
        return [_staff_account(row) for row in rows]

    def record_failed_login(
        self, user_id: str, failed_attempts: int, locked_until: str | None
    ) -> None:
        with self.session() as connection:
            connection.execute(
                "UPDATE staff_accounts SET failed_attempts=?, locked_until=? WHERE user_id=?",
                (failed_attempts, locked_until, user_id),
            )

    def start_staff_session(
        self, user_id: str, token_hash: str, created_at: str, expires_at: str
    ) -> None:
        with self.session() as connection:
            connection.execute(
                "UPDATE staff_accounts SET failed_attempts=0, locked_until=NULL WHERE user_id=?",
                (user_id,),
            )
            connection.execute(
                "INSERT INTO staff_sessions (token_hash, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token_hash, user_id, created_at, expires_at),
            )

    def staff_session(self, token_hash: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                "SELECT a.*, s.token_hash, s.expires_at, s.revoked_at FROM staff_sessions s "
                "JOIN staff_accounts a ON a.user_id=s.user_id WHERE s.token_hash=?",
                (token_hash,),
            ).fetchone()
        return _staff_account(row) if row else None

    def revoke_staff_session(self, token_hash: str, revoked_at: str) -> None:
        with self.session() as connection:
            connection.execute(
                "UPDATE staff_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (revoked_at, token_hash),
            )

    def reset_staff_password(self, user_id: str, password_hash: str, revoked_at: str) -> None:
        with self.session() as connection:
            connection.execute(
                "UPDATE staff_accounts SET password_hash=?, failed_attempts=0, locked_until=NULL "
                "WHERE user_id=?",
                (password_hash, user_id),
            )
            connection.execute(
                "UPDATE staff_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (revoked_at, user_id),
            )

    def purge_staff_sessions(self, now: str) -> int:
        """Delete expired and revoked sessions; their digests are no longer useful."""
        with self.session() as connection:
            cursor = connection.execute(
                "DELETE FROM staff_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
                (now,),
            )
        return cursor.rowcount


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _staff_account(row: sqlite3.Row) -> dict[str, Any]:
    account = dict(row)
    account["customer_ids"] = json.loads(account["customer_ids"])
    account["active"] = bool(account["active"])
    return account


if __name__ == "__main__":
    database = SupportDatabase()
    counts = database.sync_sample_data()
    print(
        json.dumps(
            {
                "database": str(database.path),
                "schema_version": SCHEMA_VERSION,
                "sample_data": counts,
            },
            indent=2,
        )
    )
