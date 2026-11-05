"""Persistence checks for schema setup, fixture synchronization, and database-backed tools."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from support_agent.api.http import execute_customer_list
from support_agent.application.actions import ActionService
from support_agent.application.conversations import ConversationStore
from support_agent.application.tools import SupportTools
from support_agent.domain.errors import ConflictError
from support_agent.infrastructure.action_store import SqliteActionRepository
from support_agent.infrastructure.database import SupportDatabase
from support_agent.infrastructure.trace_store import TraceStore
from support_agent.security.auth import Principal
from tests.fakes import StaticTokenAuthenticator


class SupportDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.database_path = Path(self.temp_dir.name) / "support.sqlite3"
        self.database = SupportDatabase(self.database_path)
        self.database.sync_sample_data()
        self.principal = Principal(
            "agent-001", "support_agent", frozenset({"cus-1001", "cus-1002", "cus-1003"})
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sample_sync_is_idempotent_and_relations_are_persisted(self) -> None:
        self.database.sync_sample_data()
        with self.database.session() as connection:
            customers = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            orders = connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            line_items = connection.execute("SELECT COUNT(*) FROM order_line_items").fetchone()[0]
            messages = connection.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[
                0
            ]
        self.assertEqual((customers, orders, line_items, messages), (3, 5, 6, 3))

    def test_tools_read_order_and_policy_facts_from_sqlite(self) -> None:
        tools = SupportTools(
            StaticTokenAuthenticator(
                {
                    "a" * 24: {
                        "user_id": "agent-001",
                        "role": "support_agent",
                        "customer_ids": ["cus-1001", "cus-1002", "cus-1003"],
                    }
                }
            ),
            date(2026, 11, 4),
            database=self.database,
        )
        result = tools.call("get_order_details", {"order_id": "ord-5001"}, self.principal)
        self.assertEqual(result["items"][0]["name"], "Ceramic Pour-over Set")
        self.assertEqual(result["shipping"]["tracking_number"], "PP10004567")
        self.assertEqual(self.database.list_customer_rows()[0]["customer_id"], "cus-1001")

    def test_frontend_customer_api_reads_database_and_applies_token_scope(self) -> None:
        token = "a" * 24
        tools = SupportTools(
            StaticTokenAuthenticator(
                {
                    token: {
                        "user_id": "agent-001",
                        "role": "support_agent",
                        "customer_ids": ["cus-1002"],
                    }
                }
            ),
            date(2026, 11, 4),
            database=self.database,
        )
        status, payload, _ = execute_customer_list(tools, f"Bearer {token}")
        self.assertEqual(status, 200)
        self.assertEqual([record["customer_id"] for record in payload["result"]], ["cus-1002"])

    def test_conversation_history_survives_store_recreation(self) -> None:
        store = ConversationStore(self.database)
        conversation_id, customer_id, history = store.open(None, self.principal, "cus-1001")
        history.append({"role": "user", "content": "Where is my order?"})
        store.save(conversation_id, history)

        reopened = ConversationStore(self.database)
        saved_id, saved_customer_id, saved_history = reopened.open(
            conversation_id, self.principal, customer_id
        )
        self.assertEqual(saved_id, conversation_id)
        self.assertEqual(saved_customer_id, "cus-1001")
        self.assertEqual(saved_history[-1]["content"], "Where is my order?")

    def test_visible_thread_persists_and_latest_conversation_is_owner_scoped(self) -> None:
        store = ConversationStore(self.database)
        conversation_id, _, history = store.open(None, self.principal, "cus-1001")
        store.save(conversation_id, history)
        self.assertIsNone(store.latest(self.principal, "cus-1001"))
        store.append_events(
            conversation_id,
            [
                {"kind": "customer_message", "text": "Box arrived crushed.", "at": "2026-11-04"},
                {
                    "kind": "ai_suggestion",
                    "suggestion_id": "suggestion-001",
                    "turn": {"intent": "damaged_item", "suggested_reply": "Sorry!"},
                    "at": "2026-11-04",
                },
            ],
        )
        store.record_reply(
            conversation_id, self.principal, "Sorry! A refund is in review.", "suggestion-001"
        )
        reopened = ConversationStore(self.database)
        latest = reopened.latest(self.principal, "cus-1001")
        self.assertEqual(latest["conversation_id"], conversation_id)
        self.assertEqual(
            [event["kind"] for event in latest["events"]],
            ["customer_message", "ai_suggestion", "agent_reply"],
        )
        _, _, saved_history = reopened.open(conversation_id, self.principal, None)
        self.assertIn("Sorry! A refund is in review.", saved_history[-1]["content"])
        other_agent = Principal("agent-002", "support_agent", self.principal.customer_ids)
        self.assertIsNone(reopened.latest(other_agent, "cus-1001"))
        # Runtime threads never inflate the support-history count shown in the customer list.
        counts = {
            row["customer_id"]: row["conversation_count"]
            for row in self.database.list_customer_rows()
        }
        self.assertEqual(counts["cus-1001"], 1)

    def test_trace_store_persists_requests_and_calls_and_migrates_old_tables(self) -> None:
        with self.database.session() as connection:
            # A table created before error_detail existed must be upgraded in place.
            connection.execute(
                "CREATE TABLE assistant_requests (request_id TEXT PRIMARY KEY, created_at TEXT "
                "NOT NULL, user_id TEXT, customer_id TEXT, conversation_id TEXT, mode TEXT, "
                "status TEXT NOT NULL, error_code TEXT, duration_ms REAL, trace_json TEXT NOT NULL)"
            )
        traces = TraceStore(self.database)
        traces.record_llm_call(
            {
                "request_id": "req-1",
                "call_index": 1,
                "model": "gpt-test",
                "status": "succeeded",
                "request": [{"role": "user", "content": "Mail jordan@example.test"}],
                "response": {"role": "assistant", "content": "{}"},
            }
        )
        traces.record_request(
            {
                "request_id": "req-1",
                "user_id": "agent-001",
                "status": "failed",
                "error_code": "internal_error",
                "error_detail": "TypeError: boom at assistant.py:1 in ask",
                "trace": [{"kind": "llm", "status": "succeeded"}],
            }
        )
        reopened = TraceStore(self.database).get("req-1")
        self.assertEqual(
            reopened["request"]["error_detail"], "TypeError: boom at assistant.py:1 in ask"
        )
        self.assertEqual(reopened["request"]["trace"][0]["kind"], "llm")
        self.assertEqual(
            reopened["llm_calls"][0]["request"][0]["content"], "Mail j***@example.test"
        )
        self.assertEqual(TraceStore(self.database).recent()[0]["request_id"], "req-1")
        self.assertIsNone(traces.get("missing"))

    def database_tools(self) -> SupportTools:
        return SupportTools(StaticTokenAuthenticator({}), date(2026, 11, 4), database=self.database)

    def test_pending_action_and_approval_records_survive_store_recreation(self) -> None:
        tools = self.database_tools()
        service = ActionService(tools, SqliteActionRepository(self.database))
        proposal = service.propose(
            "refund", "ord-5001", None, "refund-idempotency-001", self.principal
        )
        approver = Principal("approver-001", "support_approver", self.principal.customer_ids)
        restored = ActionService(tools, SqliteActionRepository(self.database))
        result = restored.decide(
            proposal["proposal_id"], "approve", "decision-idempotency-001", approver
        )
        self.assertEqual(result["status"], "executed")
        with self.database.session() as connection:
            persisted = connection.execute(
                "SELECT status FROM action_proposals WHERE proposal_id=?",
                (proposal["proposal_id"],),
            ).fetchone()
            audit_events = connection.execute(
                "SELECT event FROM action_audit WHERE proposal_id=? ORDER BY audit_id",
                (proposal["proposal_id"],),
            ).fetchall()
        self.assertEqual(persisted["status"], "executed")
        self.assertEqual(
            [row["event"] for row in audit_events],
            ["proposal_created", "proposal_approved", "action_executed"],
        )

    def test_separate_workers_share_idempotency_and_execute_once(self) -> None:
        # Regression for the review finding: each worker used to keep its own in-memory state.
        tools = self.database_tools()
        approver = Principal("approver-001", "support_approver", self.principal.customer_ids)
        worker_a = ActionService(tools, SqliteActionRepository(self.database))
        worker_b = ActionService(tools, SqliteActionRepository(self.database))
        proposal = worker_a.propose("refund", "ord-5001", None, "refund-key-000001", self.principal)
        self.assertEqual(
            worker_b.propose("refund", "ord-5001", None, "refund-key-000001", self.principal)[
                "proposal_id"
            ],
            proposal["proposal_id"],
        )
        with self.assertRaises(ConflictError):
            worker_b.propose("refund", "ord-5001", None, "refund-key-000002", self.principal)
        worker_a.decide(proposal["proposal_id"], "approve", "decision-key-000001", approver)
        second = worker_b.decide(
            proposal["proposal_id"], "approve", "decision-key-000002", approver
        )
        self.assertEqual(second["status"], "executed")
        self.assertEqual(len(second["audit"]), 3)
        self.assertEqual(SqliteActionRepository(self.database).execution_count(), 1)
        # Another worker's proposals are never wiped by a save, unlike the old full rewrite.
        worker_b.propose(
            "order_change", "ord-5005", {"operation": "cancel"}, "cancel-key-000001", self.principal
        )
        self.assertEqual(len(worker_a.list_proposals(approver)), 2)

    def test_database_rejects_a_second_pending_proposal_even_without_the_service_check(
        self,
    ) -> None:
        repository = SqliteActionRepository(self.database)
        service = ActionService(self.database_tools(), repository)
        proposal = service.propose("refund", "ord-5001", None, "refund-key-000003", self.principal)
        duplicate = {**proposal, "proposal_id": "00000000-0000-4000-8000-000000000001"}
        with self.assertRaises(ConflictError), repository.transaction() as tx:
            tx.insert(duplicate)

    def test_approved_outcomes_survive_sample_data_resync(self) -> None:
        tools = self.database_tools()
        service = ActionService(tools, SqliteActionRepository(self.database))
        approver = Principal("approver-001", "support_approver", self.principal.customer_ids)
        proposal = service.propose(
            "order_change", "ord-5005", {"operation": "cancel"}, "cancel-key-000002", self.principal
        )
        service.decide(proposal["proposal_id"], "approve", "decision-key-000003", approver)
        self.database.sync_sample_data()
        self.assertEqual(tools.get_order_details("ord-5005", self.principal)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
