"""SQLite persistence for action proposals: the source of truth for approval and idempotency.

Every operation runs in one ``BEGIN IMMEDIATE`` transaction, so idempotency keys, the
one-pending-proposal rule, and execute-once hold across threads and processes. Constraints in
the schema (primary keys and a partial unique index) back each rule at the database level.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from typing import Any

from ..domain.errors import ConflictError
from .database import SupportDatabase


class SqliteActionTransaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def proposal_key(self, user_id: str, key: str) -> tuple[str, str] | None:
        row = self.connection.execute(
            "SELECT fingerprint, result_json FROM idempotency_records "
            "WHERE scope=? AND idempotency_key=?",
            (f"proposal:{user_id}", key),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["result_json"])["proposal_id"], row["fingerprint"]

    def save_proposal_key(self, user_id: str, key: str, proposal_id: str, fingerprint: str) -> None:
        self._insert_key(f"proposal:{user_id}", key, fingerprint, {"proposal_id": proposal_id})

    def decision_key(self, proposal_id: str, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT fingerprint FROM idempotency_records WHERE scope=? AND idempotency_key=?",
            (f"decision:{proposal_id}", key),
        ).fetchone()
        return row["fingerprint"] if row else None

    def save_decision_key(self, proposal_id: str, key: str, decision: str, status: str) -> None:
        self._insert_key(f"decision:{proposal_id}", key, decision, {"status": status})

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT payload FROM action_proposals WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def all(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT payload FROM action_proposals").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def pending_for(self, order_id: str, action_type: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT payload FROM action_proposals WHERE order_id=? AND status='pending_approval' "
            "AND json_extract(payload, '$.action_type')=?",
            (order_id, action_type),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def insert(self, proposal: dict[str, Any]) -> None:
        try:
            self.connection.execute(
                "INSERT INTO action_proposals "
                "(proposal_id, customer_id, order_id, status, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    proposal["proposal_id"],
                    proposal["customer_id"],
                    proposal["order_id"],
                    proposal["status"],
                    _dump(proposal),
                    _now(),
                ),
            )
        except sqlite3.IntegrityError as error:
            # The partial unique index caught a concurrent duplicate the service check missed.
            raise ConflictError("An identical action is already awaiting approval.") from error
        self._write_audit(proposal)

    def update(self, proposal: dict[str, Any]) -> None:
        self.connection.execute(
            "UPDATE action_proposals SET status=?, payload=?, updated_at=? WHERE proposal_id=?",
            (proposal["status"], _dump(proposal), _now(), proposal["proposal_id"]),
        )
        self.connection.execute(
            "DELETE FROM action_audit WHERE proposal_id=?", (proposal["proposal_id"],)
        )
        self._write_audit(proposal)

    def execution(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT result_json FROM action_executions WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def record_execution(self, proposal_id: str, result: dict[str, Any]) -> None:
        # PRIMARY KEY(proposal_id): a second execution of the same proposal is impossible.
        self.connection.execute(
            "INSERT INTO action_executions (proposal_id, result_json, executed_at) "
            "VALUES (?, ?, ?)",
            (proposal_id, _dump(result), _now()),
        )

    def _insert_key(self, scope: str, key: str, fingerprint: str, result: dict) -> None:
        try:
            self.connection.execute(
                "INSERT INTO idempotency_records "
                "(scope, idempotency_key, fingerprint, result_json) VALUES (?, ?, ?, ?)",
                (scope, key, fingerprint, _dump(result)),
            )
        except sqlite3.IntegrityError as error:
            raise ConflictError("The idempotency key has already been used.") from error

    def _write_audit(self, proposal: dict[str, Any]) -> None:
        self.connection.executemany(
            "INSERT INTO action_audit (proposal_id, event, actor_user_id, created_at, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    proposal["proposal_id"],
                    entry["event"],
                    entry["actor_user_id"],
                    entry["at"],
                    entry["detail"],
                )
                for entry in proposal["audit"]
            ],
        )


class SqliteActionRepository:
    def __init__(self, database: SupportDatabase) -> None:
        self.database = database

    @contextmanager
    def transaction(self) -> Iterator[SqliteActionTransaction]:
        with closing(self.database.connect()) as connection:
            # Autocommit mode with an explicit IMMEDIATE transaction takes the write lock up
            # front, so concurrent proposers and approvers in any process are serialized.
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield SqliteActionTransaction(connection)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    def execution_count(self) -> int:
        with self.database.session() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM action_executions").fetchone()[0])


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()
