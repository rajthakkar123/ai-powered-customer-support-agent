"""Test doubles shared by unit tests and offline evals. Not used by the running application."""

import hmac
from datetime import date

from support_agent.application.tools import SupportTools
from support_agent.domain.errors import AuthenticationError
from support_agent.security.auth import Principal


class StaticTokenAuthenticator:
    """Maps fixed bearer tokens to principals so tool tests need no database sessions."""

    def __init__(self, token_map: dict[str, dict[str, object]]) -> None:
        self._entries = [
            (
                token,
                Principal(
                    str(claims["user_id"]),
                    str(claims["role"]),
                    frozenset(claims["customer_ids"]),  # type: ignore[arg-type]
                ),
            )
            for token, claims in token_map.items()
        ]

    def authenticate(self, authorization: str | None) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError()
        presented = authorization[7:]
        for token, principal in self._entries:
            if hmac.compare_digest(presented, token):
                return principal
        raise AuthenticationError()


def build_tools(
    token_map: dict[str, dict[str, object]],
    as_of: date,
    failing_tools: set[str] | None = None,
) -> SupportTools:
    return SupportTools(StaticTokenAuthenticator(token_map), as_of, failing_tools)
