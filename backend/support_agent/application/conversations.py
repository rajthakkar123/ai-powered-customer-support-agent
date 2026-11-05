"""Conversation history scoped to its authenticated agent and customer.

Two records are kept per conversation: the bounded model history sent to the LLM, and the
visible thread of events (customer messages, agent questions, AI suggestions, sent replies).
"""

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from ..domain.errors import ConflictError, NotFoundError, ValidationError
from ..infrastructure.database import SupportDatabase
from ..security.auth import Principal, require_customer_access

MAX_REPLY_CHARS = 4_000
# Internal copilot questions are not seen by the customer, so they never make a draft stale.
CUSTOMER_VISIBLE_KINDS = frozenset({"customer_message", "agent_reply"})


class ConversationStore:
    """Use SQLite in the application or process-local state for isolated unit tests."""

    def __init__(self, database: SupportDatabase | None = None) -> None:
        self.database = database
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._reply_lock = threading.Lock()

    def open(
        self,
        conversation_id: str | None,
        principal: Principal,
        customer_id: str | None,
    ) -> tuple[str, str, list[dict[str, Any]]]:
        if self.database is not None:
            if conversation_id is None:
                if customer_id is None:
                    raise ValidationError("A customer must be selected to start a conversation.")
                conversation_id = str(uuid.uuid4())
            if customer_id is None:
                customer_id = self.database.conversation_customer(
                    conversation_id, principal.user_id
                )
                if customer_id is None:
                    raise NotFoundError()
            messages = self.database.open_conversation(
                conversation_id, principal.user_id, customer_id
            )
            if messages is None:
                raise NotFoundError()
            return conversation_id, customer_id, messages
        with self._lock:
            if conversation_id is None:
                if customer_id is None:
                    raise ValidationError("A customer must be selected to start a conversation.")
                conversation_id = str(uuid.uuid4())
                self._records[conversation_id] = {
                    "user_id": principal.user_id,
                    "customer_id": customer_id,
                    "messages": [],
                    "events": [],
                }
            record = self._records.get(conversation_id)
            if (
                record is None
                or record["user_id"] != principal.user_id
                or (customer_id is not None and record["customer_id"] != customer_id)
            ):
                raise NotFoundError()
            return conversation_id, str(record["customer_id"]), list(record["messages"])

    def save(self, conversation_id: str, messages: list[dict[str, Any]]) -> None:
        # Preserve complete tool exchanges while keeping the prompt bounded to recent turns.
        user_turns = [
            index for index, message in enumerate(messages) if message.get("role") == "user"
        ]
        first_kept_turn = user_turns[-3] if len(user_turns) > 3 else 0
        if self.database is not None:
            self.database.save_conversation(conversation_id, messages[first_kept_turn:])
            return
        with self._lock:
            self._records[conversation_id]["messages"] = messages[first_kept_turn:]

    def append_events(self, conversation_id: str, events: list[dict[str, Any]]) -> None:
        if self.database is not None:
            self.database.append_conversation_events(conversation_id, events)
            return
        with self._lock:
            stored = self._records[conversation_id]["events"]
            assert isinstance(stored, list)
            stored.extend(events)

    def thread(self, conversation_id: object, principal: Principal) -> dict[str, Any]:
        """Return the visible thread of a conversation the principal owns."""
        if not isinstance(conversation_id, str):
            raise NotFoundError()
        conversation_id, customer_id, _ = self.open(conversation_id, principal, None)
        require_customer_access(principal, customer_id)
        if self.database is not None:
            events = self.database.conversation_events(conversation_id)
        else:
            with self._lock:
                events = list(self._records[conversation_id]["events"])
        return {"conversation_id": conversation_id, "customer_id": customer_id, "events": events}

    def latest(self, principal: Principal, customer_id: object) -> dict[str, Any] | None:
        if not isinstance(customer_id, str):
            raise ValidationError("customer_id is required.")
        require_customer_access(principal, customer_id)
        if self.database is not None:
            conversation_id = self.database.latest_conversation_id(principal.user_id, customer_id)
        else:
            with self._lock:
                owned = [
                    key
                    for key, record in self._records.items()
                    if record["user_id"] == principal.user_id
                    and record["customer_id"] == customer_id
                    and record["events"]
                ]
            conversation_id = owned[-1] if owned else None
        return self.thread(conversation_id, principal) if conversation_id else None

    def record_reply(
        self,
        conversation_id: object,
        principal: Principal,
        text: object,
        in_reply_to: object,
    ) -> dict[str, Any]:
        """Record a reply sent from an AI draft; the model sees it as context on later turns.

        A draft can be sent only while nothing customer-visible follows it, so a stale draft
        cannot be sent from a second tab or by a repeated click.
        """
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_REPLY_CHARS:
            raise ValidationError("text must be a non-empty string of at most 4000 characters.")
        if not isinstance(in_reply_to, str) or not in_reply_to:
            raise ValidationError("in_reply_to must name the suggestion being sent.")
        if not isinstance(conversation_id, str):
            raise NotFoundError()
        # Serialises check-and-append within this process; one SQLite writer serves the app.
        with self._reply_lock:
            conversation_id, customer_id, history = self.open(conversation_id, principal, None)
            require_customer_access(principal, customer_id)
            events = self._events(conversation_id)
            position = next(
                (
                    index
                    for index, event in enumerate(events)
                    if event.get("kind") == "ai_suggestion"
                    and event.get("suggestion_id") == in_reply_to
                ),
                None,
            )
            if position is None:
                raise NotFoundError()
            if not events[position].get("turn", {}).get("suggested_reply"):
                raise ConflictError("This suggestion has no customer reply to send.")
            if any(event.get("kind") in CUSTOMER_VISIBLE_KINDS for event in events[position + 1 :]):
                raise ConflictError(
                    "A newer message follows this draft, so it can no longer be sent."
                )
            history.append(
                {
                    "role": "assistant",
                    "content": "[The support agent sent this reply to the customer]\n"
                    f"{text.strip()}",
                }
            )
            self.save(conversation_id, history)
            event: dict[str, Any] = {
                "kind": "agent_reply",
                "text": text.strip(),
                "in_reply_to": in_reply_to,
                "author_user_id": principal.user_id,
                "at": utc_now(),
            }
            self.append_events(conversation_id, [event])
        return event

    def _events(self, conversation_id: str) -> list[dict[str, Any]]:
        if self.database is not None:
            return self.database.conversation_events(conversation_id)
        with self._lock:
            return list(self._records[conversation_id]["events"])


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
