"""Authenticated staff principals and server-side customer scope checks."""

from dataclasses import dataclass, replace
from typing import Protocol

from ..domain.errors import NotFoundError

SUPPORT_ROLES = frozenset({"support_agent", "support_approver", "support_admin"})


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    customer_ids: frozenset[str]
    # Set for assistant turns so tools only reach the conversation's customer, even for admins.
    active_customer_id: str | None = None

    def for_conversation(self, customer_id: str) -> "Principal":
        return replace(self, active_customer_id=customer_id)


class Authenticator(Protocol):
    def authenticate(self, authorization: str | None) -> Principal: ...


def require_customer_access(principal: Principal, customer_id: str) -> None:
    # Absent and out-of-scope records raise the same error to avoid cross-customer enumeration.
    if principal.active_customer_id is not None and customer_id != principal.active_customer_id:
        raise NotFoundError()
    if principal.role != "support_admin" and customer_id not in principal.customer_ids:
        raise NotFoundError()
