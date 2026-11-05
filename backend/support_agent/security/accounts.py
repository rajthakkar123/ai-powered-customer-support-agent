"""Staff accounts: email/password sign-in, server-side sessions, and admin-only provisioning."""

import hashlib
import logging
import os
import re
import secrets
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ..domain.errors import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    InvalidCredentialsError,
    ValidationError,
)
from ..infrastructure.database import SupportDatabase
from .auth import SUPPORT_ROLES, Principal
from .passwords import PasswordHasher

LOGGER = logging.getLogger("support_agent")
DEFAULT_ADMIN_EMAIL = "admin@northstar.test"
EMAIL_PATTERN = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)+$")
PROVISIONABLE_ROLES = frozenset({"support_agent", "support_approver"})
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
MAX_FAILED_LOGINS = 5
LOCKOUT_PERIOD = timedelta(minutes=15)
SESSION_TTL = timedelta(hours=8)


class AccountService:
    """Authenticates staff sessions; only administrators provision agent and approver accounts."""

    def __init__(
        self,
        database: SupportDatabase,
        hasher: PasswordHasher | None = None,
        session_ttl: timedelta = SESSION_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.hasher = hasher or PasswordHasher()
        self.session_ttl = session_ttl
        self._now = clock or (lambda: datetime.now(UTC))
        # Unknown emails are verified against this hash so they take as long as real accounts.
        self._unknown_account_hash = self.hasher.hash(secrets.token_urlsafe(16))

    def ensure_default_admin(self, email: str | None, password: str | None) -> str | None:
        """Create the first administrator; return the password only when it was generated."""
        if self.database.count_active_admins() > 0:
            return None
        generated = None if password else secrets.token_urlsafe(18)
        account = self._new_account(
            email=email or DEFAULT_ADMIN_EMAIL,
            display_name="Administrator",
            role="support_admin",
            password=password or generated,
            customer_ids=[],
            created_by=None,
        )
        if not self.database.insert_staff_account(account):
            raise ConflictError("An inactive account already uses the default admin email.")
        LOGGER.info(
            "staff_account_created",
            extra={"kind": "auth", "user_id": account["user_id"], "detail": "default_admin"},
        )
        return generated

    def login(self, email: object, password: object) -> dict[str, Any]:
        if (
            not isinstance(email, str)
            or not isinstance(password, str)
            or len(email) > 254
            or len(password) > MAX_PASSWORD_LENGTH
        ):
            raise InvalidCredentialsError()
        account = self.database.staff_account_by_email(email.strip().lower())
        now = self._now()
        if account is None or not account["active"]:
            self.hasher.verify(password, self._unknown_account_hash)
            LOGGER.warning("login_failed", extra={"kind": "auth", "detail": "unknown_account"})
            raise InvalidCredentialsError()
        locked_until = account["locked_until"]
        lock_active = locked_until is not None and datetime.fromisoformat(locked_until) > now
        if lock_active or not self.hasher.verify(password, account["password_hash"]):
            if not lock_active:
                self._record_failure(account, now)
            LOGGER.warning(
                "login_failed",
                extra={
                    "kind": "auth",
                    "user_id": account["user_id"],
                    "detail": "locked" if lock_active else "wrong_password",
                },
            )
            raise InvalidCredentialsError()
        session_token = secrets.token_urlsafe(32)
        expires_at = now + self.session_ttl
        self.database.start_staff_session(
            account["user_id"], _token_hash(session_token), now.isoformat(), expires_at.isoformat()
        )
        LOGGER.info("login_succeeded", extra={"kind": "auth", "user_id": account["user_id"]})
        return {
            "session_token": session_token,
            "expires_at": expires_at.isoformat(),
            "user": _public_account(account),
        }

    def authenticate(self, authorization: str | None) -> Principal:
        session = self._session(authorization)
        return Principal(session["user_id"], session["role"], frozenset(session["customer_ids"]))

    def current_user(self, authorization: str | None) -> dict[str, Any]:
        return _public_account(self._session(authorization))

    def logout(self, authorization: str | None) -> None:
        session = self._session(authorization)
        self.database.revoke_staff_session(session["token_hash"], self._now().isoformat())
        LOGGER.info("logout", extra={"kind": "auth", "user_id": session["user_id"]})

    def create_account(
        self,
        principal: Principal,
        email: object,
        display_name: object,
        role: object,
        password: object,
        customer_ids: object,
    ) -> dict[str, Any]:
        self._require_admin(principal)
        if not isinstance(role, str) or role not in PROVISIONABLE_ROLES:
            raise ValidationError("role must be support_agent or support_approver.")
        if not isinstance(customer_ids, list) or not all(
            isinstance(item, str) for item in customer_ids
        ):
            raise ValidationError("customer_ids must be a list of customer identifiers.")
        unknown = set(customer_ids) - self.database.customer_ids()
        if unknown:
            raise ValidationError("customer_ids contains unknown customers.")
        account = self._new_account(
            email=email,
            display_name=display_name,
            role=role,
            password=password,
            customer_ids=sorted(set(customer_ids)),
            created_by=principal.user_id,
        )
        if not self.database.insert_staff_account(account):
            raise ConflictError("An account with this email already exists.")
        LOGGER.info(
            "staff_account_created",
            extra={
                "kind": "auth",
                "user_id": principal.user_id,
                "detail": f"created={account['user_id']}; role={role}",
            },
        )
        return _public_account(account)

    def list_accounts(self, principal: Principal) -> list[dict[str, Any]]:
        self._require_admin(principal)
        return [_public_account(account) for account in self.database.list_staff_accounts()]

    def list_approvers(self, principal: Principal) -> list[dict[str, Any]]:
        """Active approvers, for routing a proposal; only names and emails are exposed."""
        if principal.role not in SUPPORT_ROLES:
            raise ForbiddenError("A support workspace session is required.")
        return [
            {key: account[key] for key in ("user_id", "display_name", "email")}
            for account in self.database.list_staff_accounts()
            if account["role"] == "support_approver" and account["active"]
        ]

    def find_approver(self, user_id: str) -> dict[str, Any] | None:
        """The active approver with this ID, or None; used to validate proposal routing."""
        account = next(
            (item for item in self.database.list_staff_accounts() if item["user_id"] == user_id),
            None,
        )
        if account is None or account["role"] != "support_approver" or not account["active"]:
            return None
        return {key: account[key] for key in ("user_id", "display_name", "email")}

    def reset_password(self, email: str, password: str) -> None:
        """Operator recovery path: set a password, clear lockout, and end existing sessions."""
        account = self.database.staff_account_by_email(self._email(email))
        if account is None:
            raise ValidationError("No account uses that email.")
        self.database.reset_staff_password(
            account["user_id"], self.hasher.hash(self._password(password)), self._now().isoformat()
        )

    def _session(self, authorization: str | None) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError()
        session = self.database.staff_session(_token_hash(authorization[7:]))
        if (
            session is None
            or session["revoked_at"] is not None
            or not session["active"]
            or datetime.fromisoformat(session["expires_at"]) <= self._now()
        ):
            raise AuthenticationError()
        return session

    def _record_failure(self, account: dict[str, Any], now: datetime) -> None:
        previous_lock = account["locked_until"]
        # A lock that has already expired starts a fresh failure window.
        attempts = 1 if previous_lock is not None else int(account["failed_attempts"]) + 1
        locked_until = (now + LOCKOUT_PERIOD).isoformat() if attempts >= MAX_FAILED_LOGINS else None
        self.database.record_failed_login(str(account["user_id"]), attempts, locked_until)

    def _new_account(
        self,
        email: object,
        display_name: object,
        role: str,
        password: object,
        customer_ids: list[str],
        created_by: str | None,
    ) -> dict[str, Any]:
        if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 80:
            raise ValidationError("display_name must be 1-80 characters.")
        return {
            "user_id": f"{role.removeprefix('support_')}-{secrets.token_hex(4)}",
            "email": self._email(email),
            "display_name": display_name.strip(),
            "role": role,
            "password_hash": self.hasher.hash(self._password(password)),
            "customer_ids": customer_ids,
            "active": True,
            "created_by": created_by,
            "created_at": self._now().isoformat(),
        }

    @staticmethod
    def _email(value: object) -> str:
        if not isinstance(value, str) or len(value) > 254:
            raise ValidationError("email must be a valid email address.")
        email = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(email):
            raise ValidationError("email must be a valid email address.")
        return email

    @staticmethod
    def _password(value: object) -> str:
        if not isinstance(value, str) or not (
            MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH
        ):
            raise ValidationError(
                f"password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters."
            )
        return value

    @staticmethod
    def _require_admin(principal: Principal) -> None:
        if principal.role != "support_admin":
            raise ForbiddenError("Only an administrator can manage staff accounts.")


def _token_hash(token: str) -> str:
    # Only digests are stored, so a database leak does not expose usable session tokens.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _public_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        key: account[key]
        for key in (
            "user_id",
            "email",
            "display_name",
            "role",
            "customer_ids",
            "active",
            "created_by",
            "created_at",
        )
    }


if __name__ == "__main__":
    # Operator recovery; reads the new password from SUPPORT_ADMIN_PASSWORD.
    if sys.argv[1:] != ["reset-admin-password"]:
        sys.exit("Usage: python -m support_agent.security.accounts reset-admin-password")
    new_password = os.environ.get("SUPPORT_ADMIN_PASSWORD", "")
    admin_email = os.environ.get("SUPPORT_ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL)
    try:
        AccountService(SupportDatabase()).reset_password(admin_email, new_password)
    except ValidationError as error:
        sys.exit(f"Reset failed: {error.message} Set SUPPORT_ADMIN_PASSWORD and retry.")
    print(f"Password reset for {admin_email}; existing sessions were signed out.")
