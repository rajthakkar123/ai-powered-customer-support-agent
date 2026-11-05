"""Human-gated consequential actions with scoped authorization and idempotency.

Proposals are created pending and only a different approver can execute them. Every propose
and decide runs inside one repository transaction; with SQLite that is a ``BEGIN IMMEDIATE``
transaction, so the rules below hold across threads and processes, not only in memory.
"""

import copy
import hashlib
import json
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from ..domain.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from ..security.auth import Principal, require_customer_access
from .tools import SupportTools

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
PROPOSAL_STATUSES = frozenset({"pending_approval", "executed", "rejected"})
REVIEWER_ROLES = frozenset({"support_approver", "support_admin"})


class ActionTransaction(Protocol):
    connection: Any

    def proposal_key(self, user_id: str, key: str) -> tuple[str, str] | None: ...
    def save_proposal_key(
        self, user_id: str, key: str, proposal_id: str, fingerprint: str
    ) -> None: ...
    def decision_key(self, proposal_id: str, key: str) -> str | None: ...
    def save_decision_key(self, proposal_id: str, key: str, decision: str, status: str) -> None: ...
    def get(self, proposal_id: str) -> dict[str, Any] | None: ...
    def all(self) -> list[dict[str, Any]]: ...
    def pending_for(self, order_id: str, action_type: str) -> dict[str, Any] | None: ...
    def insert(self, proposal: dict[str, Any]) -> None: ...
    def update(self, proposal: dict[str, Any]) -> None: ...
    def execution(self, proposal_id: str) -> dict[str, Any] | None: ...
    def record_execution(self, proposal_id: str, result: dict[str, Any]) -> None: ...


class ActionRepository(Protocol):
    def transaction(self) -> Any: ...
    def execution_count(self) -> int: ...


class InMemoryActionRepository:
    """Process-local repository for isolated tests; the application uses SQLite."""

    connection = None

    def __init__(self) -> None:
        self._proposals: dict[str, dict[str, Any]] = {}
        self._proposal_keys: dict[tuple[str, str], tuple[str, str]] = {}
        self._decision_keys: dict[tuple[str, str], str] = {}
        self._executions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self) -> Iterator["InMemoryActionRepository"]:
        with self._lock:
            yield self

    def execution_count(self) -> int:
        return len(self._executions)

    def proposal_key(self, user_id: str, key: str) -> tuple[str, str] | None:
        return self._proposal_keys.get((user_id, key))

    def save_proposal_key(self, user_id: str, key: str, proposal_id: str, fingerprint: str) -> None:
        self._proposal_keys[(user_id, key)] = (proposal_id, fingerprint)

    def decision_key(self, proposal_id: str, key: str) -> str | None:
        return self._decision_keys.get((proposal_id, key))

    def save_decision_key(self, proposal_id: str, key: str, decision: str, status: str) -> None:
        self._decision_keys[(proposal_id, key)] = decision

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        proposal = self._proposals.get(proposal_id)
        return copy.deepcopy(proposal) if proposal else None

    def all(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(proposal) for proposal in self._proposals.values()]

    def pending_for(self, order_id: str, action_type: str) -> dict[str, Any] | None:
        return next(
            (
                copy.deepcopy(proposal)
                for proposal in self._proposals.values()
                if proposal["order_id"] == order_id
                and proposal["action_type"] == action_type
                and proposal["status"] == "pending_approval"
            ),
            None,
        )

    def insert(self, proposal: dict[str, Any]) -> None:
        self._proposals[str(proposal["proposal_id"])] = copy.deepcopy(proposal)

    def update(self, proposal: dict[str, Any]) -> None:
        self._proposals[str(proposal["proposal_id"])] = copy.deepcopy(proposal)

    def execution(self, proposal_id: str) -> dict[str, Any] | None:
        return self._executions.get(proposal_id)

    def record_execution(self, proposal_id: str, result: dict[str, Any]) -> None:
        if proposal_id in self._executions:
            raise ConflictError("This proposal has already been executed.")
        self._executions[proposal_id] = result


class ActionService:
    """Creates pending proposals and executes them only after a distinct approver acts."""

    def __init__(self, tools: SupportTools, repository: ActionRepository | None = None) -> None:
        self.tools = tools
        self.repository: ActionRepository = repository or InMemoryActionRepository()

    def propose(
        self,
        action_type: object,
        order_id: object,
        details: object,
        idempotency_key: object,
        principal: Principal,
        reason: object = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(action_type, str) or action_type not in {"refund", "order_change"}:
            raise ValidationError("action_type must be refund or order_change.")
        if not isinstance(order_id, str) or not re.fullmatch(r"ord-\d{4,}", order_id):
            raise ValidationError("order_id must be a valid order identifier.")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
            raise ValidationError("reason must be a string of at most 500 characters.")
        key = _idempotency_key(idempotency_key)
        # The fingerprint describes the request, not derived state, so a replay still matches
        # after the order has changed (for example once the refund was executed).
        fingerprint = _fingerprint(action_type, order_id, details or {})
        with self.repository.transaction() as tx:
            existing = tx.proposal_key(principal.user_id, key)
            if existing:
                proposal_id, existing_fingerprint = existing
                if existing_fingerprint != fingerprint:
                    raise ConflictError(
                        "The idempotency key was already used for a different proposal."
                    )
                replayed = _require(tx.get(proposal_id))
                require_customer_access(principal, str(replayed["customer_id"]))
                return replayed
            # Read inside the transaction so policy decisions use state no one else can change.
            order = self.tools.get_order_details(order_id, principal)
            normalized = self._normalize_details(action_type, order, details)
            self._check_policy(action_type, order, normalized, principal)
            if tx.pending_for(order_id, action_type) is not None:
                raise ConflictError("An identical action is already awaiting approval.")
            proposal: dict[str, Any] = {
                "proposal_id": str(uuid.uuid4()),
                "action_type": action_type,
                "order_id": order_id,
                "customer_id": order["customer_id"],
                "details": normalized,
                "status": "pending_approval",
                "created_by": principal.user_id,
                "created_at": _now(),
                # Context for the approver; excluded from the idempotency fingerprint.
                "reason": reason.strip() if isinstance(reason, str) and reason.strip() else None,
                "source": "assistant" if conversation_id else "agent",
                "conversation_id": conversation_id,
                "approved_by": None,
                "execution": None,
                "audit": [],
            }
            _audit(proposal, "proposal_created", principal.user_id, "Awaiting human approval.")
            tx.insert(proposal)
            tx.save_proposal_key(principal.user_id, key, proposal["proposal_id"], fingerprint)
            return proposal

    def decide(
        self,
        proposal_id: object,
        decision: object,
        idempotency_key: object,
        principal: Principal,
    ) -> dict[str, Any]:
        if not isinstance(decision, str) or decision not in {"approve", "reject"}:
            raise ValidationError("decision must be approve or reject.")
        key = _idempotency_key(idempotency_key)
        with self.repository.transaction() as tx:
            proposal = self._authorized(tx, proposal_id, principal)
            if principal.role not in REVIEWER_ROLES:
                raise ForbiddenError("A support approver role is required to decide this proposal.")
            if principal.user_id == proposal["created_by"]:
                raise ForbiddenError(
                    "The proposal creator cannot approve or reject their own proposal."
                )
            previous_decision = tx.decision_key(proposal["proposal_id"], key)
            if previous_decision is not None:
                if previous_decision != decision:
                    raise ConflictError(
                        "The decision idempotency key was already used for a different decision."
                    )
                return proposal
            if proposal["status"] != "pending_approval":
                # A second distinct approval request cannot execute an action again.
                return proposal
            if decision == "reject":
                proposal["status"] = "rejected"
                proposal["approved_by"] = principal.user_id
                _audit(
                    proposal,
                    "proposal_rejected",
                    principal.user_id,
                    "Human approver rejected the proposal.",
                )
            else:
                order = self.tools.get_order_details(str(proposal["order_id"]), principal)
                self._check_policy(proposal["action_type"], order, proposal["details"], principal)
                result = tx.execution(proposal["proposal_id"])
                if result is None:
                    result = self._execute(proposal, order, tx)
                proposal.update({"status": "executed", "approved_by": principal.user_id})
                proposal["execution"] = result
                _audit(proposal, "proposal_approved", principal.user_id, "Human approval recorded.")
                _audit(
                    proposal,
                    "action_executed",
                    principal.user_id,
                    "Sample action recorded once; the order outcome was updated.",
                )
            tx.save_decision_key(proposal["proposal_id"], key, decision, proposal["status"])
            tx.update(proposal)
            return proposal

    def get(self, proposal_id: object, principal: Principal) -> dict[str, Any]:
        with self.repository.transaction() as tx:
            return self._authorized(tx, proposal_id, principal)

    def open_proposal(
        self, order_id: str, action_type: str, principal: Principal
    ) -> dict[str, Any] | None:
        """Return the pending proposal that blocks a duplicate, if the principal may see it."""
        with self.repository.transaction() as tx:
            proposal = tx.pending_for(order_id, action_type)
        if proposal is None:
            return None
        require_customer_access(principal, str(proposal["customer_id"]))
        return proposal

    def list_proposals(self, principal: Principal, status: object = None) -> list[dict[str, Any]]:
        """Proposals the principal may see, newest first.

        Approvers and admins see every proposal for customers in their scope; agents see only
        the proposals they created. Out-of-scope proposals are filtered, never reported.
        """
        if status is not None and status not in PROPOSAL_STATUSES:
            raise ValidationError("status must be pending_approval, executed, or rejected.")
        with self.repository.transaction() as tx:
            proposals = tx.all()
        visible = []
        for proposal in proposals:
            if status is not None and proposal["status"] != status:
                continue
            if principal.role not in REVIEWER_ROLES and proposal["created_by"] != principal.user_id:
                continue
            try:
                require_customer_access(principal, str(proposal["customer_id"]))
            except NotFoundError:
                continue
            visible.append(proposal)
        return sorted(visible, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def _normalize_details(
        self, action_type: str, order: dict[str, Any], details: object
    ) -> dict[str, Any]:
        """Turn the request into the exact change; amounts are always computed server-side."""
        if action_type == "order_change":
            if (
                not isinstance(details, dict)
                or set(details) != {"operation"}
                or details["operation"] != "cancel"
            ):
                raise ValidationError(
                    "order_change details must request the supported cancel operation."
                )
            return {"operation": "cancel"}
        refundable = {item["item_id"]: item for item in self.tools.refundable_items(order)}
        if details in (None, {}):
            item_ids = sorted(refundable)
        elif (
            isinstance(details, dict)
            and set(details) == {"item_ids"}
            and isinstance(details["item_ids"], list)
            and details["item_ids"]
            and all(isinstance(item, str) for item in details["item_ids"])
            and len(set(details["item_ids"])) == len(details["item_ids"])
        ):
            order_items = {item["item_id"] for item in order["items"]}
            unknown = set(details["item_ids"]) - order_items
            if unknown:
                raise ValidationError("item_ids must belong to the order.")
            item_ids = sorted(details["item_ids"])
        else:
            raise ValidationError("Refund details may only contain a non-empty list of item_ids.")
        return {
            "item_ids": item_ids,
            "amount_cents": sum(
                refundable[item_id]["amount_cents"] for item_id in item_ids if item_id in refundable
            ),
            "currency": order["currency"],
        }

    def _check_policy(
        self,
        action_type: str,
        order: dict[str, Any],
        details: dict[str, Any],
        principal: Principal,
    ) -> None:
        """Deterministic rules, checked when proposing and again when approving."""
        if action_type == "order_change":
            check = self.tools.check_cancellation_eligibility(order["order_id"], principal)
            if not check["eligible"]:
                raise ConflictError(f"Order cannot be cancelled: {' '.join(check['reasons'])}")
            return
        eligibility = self.tools.check_return_eligibility(order["order_id"], "refund", principal)
        if not eligibility["eligible"]:
            raise ConflictError(f"Refund cannot be proposed: {'; '.join(eligibility['reasons'])}")
        refundable = set(eligibility["refundable_item_ids"])
        already_refunded = set(details["item_ids"]) - refundable
        if already_refunded:
            raise ConflictError(
                f"Items already refunded or not refundable: {', '.join(sorted(already_refunded))}."
            )

    def _execute(
        self, proposal: dict[str, Any], order: dict[str, Any], tx: ActionTransaction
    ) -> dict[str, Any]:
        """Apply the order outcome and record the execution in the same transaction."""
        # No payment processor or carrier is connected; the outcome is recorded locally.
        if proposal["action_type"] == "refund":
            details = proposal["details"]
            refunded = sorted(set(order.get("refunded_item_ids", [])) | set(details["item_ids"]))
            all_items = {item["item_id"] for item in order["items"]}
            outcome = {
                "refunded_item_ids": refunded,
                "refunded_cents": int(order.get("refunded_cents", 0)) + details["amount_cents"],
                "return_status": "refunded" if set(refunded) >= all_items else "partially_refunded",
            }
            result = {
                "outcome": "sample_refund_issued",
                "item_ids": details["item_ids"],
                "amount_cents": details["amount_cents"],
                "currency": details["currency"],
            }
        else:
            outcome = {"status": "cancelled"}
            result = {"outcome": "sample_order_cancelled", "order_id": proposal["order_id"]}
        self.tools.apply_order_outcome(str(proposal["order_id"]), outcome, tx.connection)
        tx.record_execution(proposal["proposal_id"], result)
        return result

    @staticmethod
    def _authorized(
        tx: ActionTransaction, proposal_id: object, principal: Principal
    ) -> dict[str, Any]:
        try:
            proposal_uuid = str(uuid.UUID(str(proposal_id)))
        except (ValueError, TypeError, AttributeError) as error:
            raise NotFoundError() from error
        proposal = tx.get(proposal_uuid)
        if proposal is None:
            raise NotFoundError()
        require_customer_access(principal, str(proposal["customer_id"]))
        return proposal


def _idempotency_key(value: object) -> str:
    if not isinstance(value, str) or not IDEMPOTENCY_PATTERN.fullmatch(value):
        raise ValidationError(
            "idempotency_key must be 16-128 letters, digits, underscores, or hyphens."
        )
    return value


def _fingerprint(action_type: str, order_id: str, details: object) -> str:
    return hashlib.sha256(
        json.dumps(
            {"action_type": action_type, "order_id": order_id, "details": details},
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _audit(proposal: dict[str, Any], event: str, actor: str, detail: str) -> None:
    proposal["audit"].append(
        {"event": event, "actor_user_id": actor, "at": _now(), "detail": detail}
    )


def _require(proposal: dict[str, Any] | None) -> dict[str, Any]:
    if proposal is None:
        raise NotFoundError()
    return proposal


def _now() -> str:
    return datetime.now(UTC).isoformat()
