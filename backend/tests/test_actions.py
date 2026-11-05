"""Approval, authorization, and idempotency tests for consequential actions."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from support_agent.api.http import execute_action_list, execute_action_request
from support_agent.application.actions import ActionService
from support_agent.domain.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from support_agent.security.auth import Principal
from tests.fakes import build_tools

TOKEN_MAP = {
    "agent-one-secret": {
        "user_id": "agent-001",
        "role": "support_agent",
        "customer_ids": ["cus-1001"],
    },
    "approver-one-secret": {
        "user_id": "approver-001",
        "role": "support_approver",
        "customer_ids": ["cus-1001", "cus-1002"],
    },
    "agent-two-secret": {
        "user_id": "agent-002",
        "role": "support_agent",
        "customer_ids": ["cus-1002"],
    },
    "agent-third-secret": {
        "user_id": "agent-003",
        "role": "support_agent",
        "customer_ids": ["cus-1001"],
    },
    "admin-secret-token": {"user_id": "admin-001", "role": "support_admin", "customer_ids": []},
}


class ActionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = build_tools(TOKEN_MAP, date(2026, 11, 4))
        self.actions = ActionService(self.tools)
        self.agent = self.tools.authenticator.authenticate("Bearer agent-one-secret")
        self.approver = self.tools.authenticator.authenticate("Bearer approver-one-secret")

    def propose_refund(self, key: str = "proposal-key-0001") -> dict[str, object]:
        return self.actions.propose(
            "refund", "ord-5001", None, key, self.agent, reason="Customer reason for the test."
        )

    def test_refund_proposal_waits_for_distinct_human_approval(self) -> None:
        proposal = self.propose_refund()
        self.assertEqual(proposal["status"], "pending_approval")
        self.assertIsNone(proposal["execution"])
        with self.assertRaises(ForbiddenError):
            self.actions.decide(proposal["proposal_id"], "approve", "decision-key-0001", self.agent)
        other_agent = self.tools.authenticator.authenticate("Bearer agent-third-secret")
        with self.assertRaises(ForbiddenError):
            self.actions.decide(
                proposal["proposal_id"], "approve", "decision-key-0002", other_agent
            )

    def test_approver_executes_once_and_replays_same_outcome(self) -> None:
        proposal = self.propose_refund()
        approved = self.actions.decide(
            proposal["proposal_id"], "approve", "decision-key-0001", self.approver
        )
        self.assertEqual(approved["status"], "executed")
        self.assertEqual(approved["execution"]["outcome"], "sample_refund_issued")
        audit_count = len(approved["audit"])
        replay = self.actions.decide(
            proposal["proposal_id"], "approve", "decision-key-0001", self.approver
        )
        self.assertEqual(replay["execution"], approved["execution"])
        self.assertEqual(len(replay["audit"]), audit_count)
        replay_with_new_key = self.actions.decide(
            proposal["proposal_id"], "approve", "decision-key-0002", self.approver
        )
        self.assertEqual(replay_with_new_key["execution"], approved["execution"])
        self.assertEqual(len(replay_with_new_key["audit"]), audit_count)
        self.assertEqual(self.actions.repository.execution_count(), 1)
        with self.assertRaises(ConflictError):
            self.propose_refund("another-proposal-key")

    def test_concurrent_approvals_still_execute_once(self) -> None:
        proposal = self.propose_refund()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda _: self.actions.decide(
                        proposal["proposal_id"], "approve", "decision-key-0001", self.approver
                    ),
                    range(8),
                )
            )
        self.assertTrue(all(result["status"] == "executed" for result in results))
        self.assertEqual(self.actions.repository.execution_count(), 1)
        self.assertEqual(len(results[0]["audit"]), 3)

    def test_duplicate_proposal_key_returns_existing_proposal(self) -> None:
        first = self.propose_refund()
        # Even after the order changes, replaying the same request returns the same proposal.
        self.tools.apply_order_outcome("ord-5001", {"return_status": "already_requested"})
        replay = self.propose_refund()
        self.assertEqual(first["proposal_id"], replay["proposal_id"])
        with self.assertRaises(ConflictError):
            self.actions.propose(
                "order_change",
                "ord-5001",
                {"operation": "cancel"},
                "proposal-key-0001",
                self.agent,
                reason="Customer reason for the test.",
            )

    def test_refund_is_priced_per_item_in_cents_on_the_server(self) -> None:
        damaged_only = self.actions.propose(
            "refund",
            "ord-5001",
            {"item_ids": ["item-9002"]},
            "proposal-key-0010",
            self.agent,
            reason="Customer reason for the test.",
        )
        self.assertEqual(
            damaged_only["details"],
            {"item_ids": ["item-9002"], "amount_cents": 2999, "currency": "USD"},
        )
        for bad_details in (
            {"item_ids": ["item-9003"]},
            {"item_ids": []},
            {"item_ids": ["item-9002", "item-9002"]},
            {"amount": 1},
        ):
            with self.subTest(details=bad_details), self.assertRaises(ValidationError):
                self.actions.propose(
                    "refund",
                    "ord-5001",
                    bad_details,
                    "proposal-key-0011",
                    self.agent,
                    reason="Customer reason for the test.",
                )

    def test_approved_refund_changes_the_order_and_its_eligibility(self) -> None:
        partial = self.actions.propose(
            "refund",
            "ord-5001",
            {"item_ids": ["item-9002"]},
            "proposal-key-0012",
            self.agent,
            reason="Customer reason for the test.",
        )
        self.actions.decide(partial["proposal_id"], "approve", "decision-key-0012", self.approver)
        order = self.tools.get_order_details("ord-5001", self.agent)
        self.assertEqual(
            (order["return_status"], order["refunded_item_ids"], order["refunded_cents"]),
            ("partially_refunded", ["item-9002"], 2999),
        )
        eligibility = self.tools.check_return_eligibility("ord-5001", "refund", self.agent)
        self.assertEqual(eligibility["refundable_item_ids"], ["item-9001"])
        with self.assertRaises(ConflictError):
            self.actions.propose(
                "refund",
                "ord-5001",
                {"item_ids": ["item-9002"]},
                "proposal-key-0013",
                self.agent,
                reason="Customer reason for the test.",
            )
        rest = self.actions.propose(
            "refund",
            "ord-5001",
            None,
            "proposal-key-0014",
            self.agent,
            reason="Customer reason for the test.",
        )
        self.assertEqual(rest["details"]["amount_cents"], 5999)
        self.actions.decide(rest["proposal_id"], "approve", "decision-key-0014", self.approver)
        refunded = self.tools.check_return_eligibility("ord-5001", "refund", self.agent)
        self.assertFalse(refunded["eligible"])
        self.assertIn("Every item in this order has already been refunded.", refunded["reasons"])

    def test_approved_cancellation_cancels_the_order(self) -> None:
        proposal = self.actions.propose(
            "order_change",
            "ord-5005",
            {"operation": "cancel"},
            "proposal-key-0015",
            self.agent,
            reason="Customer reason for the test.",
        )
        self.actions.decide(proposal["proposal_id"], "approve", "decision-key-0015", self.approver)
        self.assertEqual(
            self.tools.get_order_details("ord-5005", self.agent)["status"], "cancelled"
        )
        with self.assertRaises(ConflictError):
            self.actions.propose(
                "order_change",
                "ord-5005",
                {"operation": "cancel"},
                "proposal-key-0016",
                self.agent,
                reason="Customer reason for the test.",
            )

    def test_rejected_proposal_never_executes(self) -> None:
        proposal = self.propose_refund()
        rejected = self.actions.decide(
            proposal["proposal_id"], "reject", "decision-key-0001", self.approver
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertIsNone(rejected["execution"])
        self.assertEqual(self.actions.repository.execution_count(), 0)

    def test_ineligible_refund_cannot_be_proposed(self) -> None:
        with self.assertRaises(ConflictError):
            self.actions.propose(
                "refund",
                "ord-5002",
                None,
                "proposal-key-0002",
                self.agent,
                reason="Customer reason for the test.",
            )

    def test_order_change_is_also_gated_by_approval(self) -> None:
        agent_two = self.tools.authenticator.authenticate("Bearer agent-two-secret")
        proposal = self.actions.propose(
            "order_change",
            "ord-5003",
            {"operation": "cancel"},
            "proposal-key-0003",
            agent_two,
            reason="Customer reason for the test.",
        )
        self.assertEqual(proposal["status"], "pending_approval")
        approved = self.actions.decide(
            proposal["proposal_id"], "approve", "decision-key-0003", self.approver
        )
        self.assertEqual(approved["execution"]["outcome"], "sample_order_cancelled")

    def test_cross_customer_proposal_and_lookup_are_denied(self) -> None:
        with self.assertRaises(NotFoundError):
            self.actions.propose(
                "refund",
                "ord-5003",
                None,
                "proposal-key-0004",
                self.agent,
                reason="Customer reason for the test.",
            )
        proposal = self.propose_refund()
        agent_two = self.tools.authenticator.authenticate("Bearer agent-two-secret")
        with self.assertRaises(NotFoundError):
            self.actions.get(proposal["proposal_id"], agent_two)

    def test_approval_queue_is_scoped_by_role_and_customer(self) -> None:
        refund = self.actions.propose(
            "refund",
            "ord-5001",
            None,
            "proposal-key-0006",
            self.agent,
            reason="Damaged on arrival.",
            conversation_id="11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual((refund["reason"], refund["source"]), ("Damaged on arrival.", "assistant"))
        self.assertTrue(refund["created_at"])
        agent_two = self.tools.authenticator.authenticate("Bearer agent-two-secret")
        cancel = self.actions.propose(
            "order_change",
            "ord-5003",
            {"operation": "cancel"},
            "proposal-key-0007",
            agent_two,
            reason="Customer reason for the test.",
        )
        self.assertEqual(cancel["source"], "agent")
        queue = self.actions.list_proposals(self.approver, "pending_approval")
        self.assertEqual(
            {item["proposal_id"] for item in queue}, {refund["proposal_id"], cancel["proposal_id"]}
        )
        # Agents only see their own proposals; scope filters rather than reveals the rest.
        self.assertEqual(
            [item["proposal_id"] for item in self.actions.list_proposals(self.agent)],
            [refund["proposal_id"]],
        )
        narrow_approver = Principal("approver-002", "support_approver", frozenset({"cus-1002"}))
        self.assertEqual(
            [item["proposal_id"] for item in self.actions.list_proposals(narrow_approver)],
            [cancel["proposal_id"]],
        )
        self.actions.decide(refund["proposal_id"], "reject", "decision-key-0006", self.approver)
        self.assertEqual(
            [
                item["proposal_id"]
                for item in self.actions.list_proposals(self.approver, "rejected")
            ],
            [refund["proposal_id"]],
        )
        with self.assertRaises(ValidationError):
            self.actions.list_proposals(self.approver, "anything")
        status, payload, _ = execute_action_list(self.actions, None, None)
        self.assertEqual(status, 401)
        status, payload, _ = execute_action_list(
            self.actions, "Bearer approver-one-secret", "pending_approval"
        )
        self.assertEqual(
            [item["proposal_id"] for item in payload["result"]], [cancel["proposal_id"]]
        )

    def test_idempotency_key_must_be_well_formed(self) -> None:
        with self.assertRaises(ValidationError):
            self.actions.propose(
                "refund",
                "ord-5001",
                None,
                "tiny",
                self.agent,
                reason="Customer reason for the test.",
            )

    def test_every_proposal_records_the_customers_reason(self) -> None:
        for reason in (None, "", "   ", "ok", 42, "x" * 501):
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                self.actions.propose(
                    "order_change",
                    "ord-5005",
                    {"operation": "cancel"},
                    "proposal-key-0020",
                    self.agent,
                    reason=reason,
                )
        proposal = self.actions.propose(
            "order_change",
            "ord-5005",
            {"operation": "cancel"},
            "proposal-key-0020",
            self.agent,
            reason="  Ordered the wrong size.  ",
        )
        self.assertEqual(proposal["reason"], "Ordered the wrong size.")

    def test_action_api_boundary_reports_pending_and_requires_auth(self) -> None:
        body = {
            "action_type": "refund",
            "order_id": "ord-5001",
            "idempotency_key": "proposal-key-0005",
        }
        status, payload, _ = execute_action_request(
            self.actions, "Bearer agent-one-secret", "propose", body
        )
        self.assertEqual((status, payload["error"]["code"]), (400, "invalid_argument"))
        status, payload, request_id = execute_action_request(
            self.actions,
            "Bearer agent-one-secret",
            "propose",
            {**body, "reason": "Arrived damaged."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["result"]["status"], "pending_approval")
        status, payload, _ = execute_action_request(self.actions, None, "decide", {})
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")


class FakeApprovers:
    """Approver directory double: approver-001 and approver-out are active approvers."""

    ACCOUNTS = {
        "approver-001": {"user_id": "approver-001", "display_name": "Sam Approver"},
        "approver-out": {"user_id": "approver-out", "display_name": "Out-of-scope Approver"},
    }

    def find_approver(self, user_id: str) -> dict[str, str] | None:
        return self.ACCOUNTS.get(user_id)


class ApproverRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = build_tools(TOKEN_MAP, date(2026, 11, 4))
        self.actions = ActionService(self.tools, approvers=FakeApprovers())
        self.agent = self.tools.authenticator.authenticate("Bearer agent-one-secret")
        self.in_scope_approver = self.tools.authenticator.authenticate("Bearer approver-one-secret")
        # An approver with no customers assigned: only routing can give them a proposal.
        self.routed_approver = Principal("approver-out", "support_approver", frozenset())

    def propose(self, approver_id: str | None, key: str = "routing-key-000001") -> dict:
        return self.actions.propose(
            "refund",
            "ord-5001",
            {"item_ids": ["item-9002"]},
            key,
            self.agent,
            reason="The filter box arrived crushed.",
            approver_id=approver_id,
        )

    def test_assigned_approver_sees_and_decides_without_customer_scope(self) -> None:
        proposal = self.propose("approver-out")
        self.assertEqual(proposal["assigned_approver_name"], "Out-of-scope Approver")
        queue = self.actions.list_proposals(self.routed_approver, "pending_approval")
        self.assertEqual([item["proposal_id"] for item in queue], [proposal["proposal_id"]])
        self.assertEqual(
            self.actions.get(proposal["proposal_id"], self.routed_approver)["order_id"], "ord-5001"
        )
        decided = self.actions.decide(
            proposal["proposal_id"], "approve", "routing-decision-01", self.routed_approver
        )
        self.assertEqual((decided["status"], decided["approved_by"]), ("executed", "approver-out"))
        # The delegation covers this proposal only, not browsing the customer.
        with self.assertRaises(NotFoundError):
            self.tools.get_order_details("ord-5001", self.routed_approver)

    def test_other_approvers_cannot_decide_or_see_an_assigned_proposal(self) -> None:
        proposal = self.propose("approver-out")
        self.assertEqual(
            self.actions.list_proposals(self.in_scope_approver, "pending_approval"), []
        )
        with self.assertRaises(ForbiddenError):
            self.actions.decide(
                proposal["proposal_id"], "approve", "routing-decision-02", self.in_scope_approver
            )
        # Admins oversee the queue but only the approver role decides.
        admin = self.tools.authenticator.authenticate("Bearer admin-secret-token")
        self.assertEqual(
            [item["proposal_id"] for item in self.actions.list_proposals(admin)],
            [proposal["proposal_id"]],
        )
        for decision in ("approve", "reject"):
            with self.assertRaises(ForbiddenError):
                self.actions.decide(proposal["proposal_id"], decision, "routing-decision-03", admin)
        self.assertEqual(
            self.actions.get(proposal["proposal_id"], admin)["status"], "pending_approval"
        )

    def test_unassigned_proposals_stay_with_in_scope_approvers(self) -> None:
        proposal = self.propose(None)
        self.assertIsNone(proposal["assigned_approver_id"])
        self.assertEqual(self.actions.list_proposals(self.routed_approver), [])
        self.assertEqual(
            [item["proposal_id"] for item in self.actions.list_proposals(self.in_scope_approver)],
            [proposal["proposal_id"]],
        )

    def test_assignment_is_validated_and_limited_to_the_proposer_or_an_admin(self) -> None:
        with self.assertRaises(ValidationError):
            self.propose("agent-003")
        proposal = self.propose(None)
        other_agent = self.tools.authenticator.authenticate("Bearer agent-third-secret")
        with self.assertRaises(ForbiddenError):
            self.actions.assign(proposal["proposal_id"], "approver-001", other_agent)
        assigned = self.actions.assign(proposal["proposal_id"], "approver-out", self.agent)
        self.assertEqual(assigned["assigned_approver_id"], "approver-out")
        self.assertEqual(assigned["audit"][-1]["event"], "approver_assigned")
        self.actions.decide(
            proposal["proposal_id"], "approve", "routing-decision-04", self.routed_approver
        )
        with self.assertRaises(ConflictError):
            self.actions.assign(proposal["proposal_id"], "approver-001", self.agent)
        status, payload, _ = execute_action_request(
            self.actions,
            "Bearer agent-one-secret",
            "assign",
            {"proposal_id": proposal["proposal_id"], "approver_id": "approver-001"},
        )
        self.assertEqual((status, payload["error"]["code"]), (409, "conflict"))

    def test_proposer_cannot_route_a_proposal_to_themselves(self) -> None:
        approver_as_proposer = self.tools.authenticator.authenticate("Bearer approver-one-secret")
        with self.assertRaises(ValidationError):
            self.actions.propose(
                "refund",
                "ord-5001",
                None,
                "routing-key-000002",
                approver_as_proposer,
                approver_id="approver-001",
                reason="Customer reason for the test.",
            )


if __name__ == "__main__":
    unittest.main()
