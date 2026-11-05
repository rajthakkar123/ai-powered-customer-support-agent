"""Staff sign-in, session, lockout, and admin-only provisioning tests."""

import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from support_agent.api.http import (
    execute_account_create,
    execute_account_list,
    execute_current_user,
    execute_login,
    execute_logout,
)
from support_agent.application.tools import SupportTools
from support_agent.domain.errors import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    InvalidCredentialsError,
    NotFoundError,
    ValidationError,
)
from support_agent.infrastructure.database import SupportDatabase
from support_agent.security.accounts import DEFAULT_ADMIN_EMAIL, AccountService
from support_agent.security.passwords import PasswordHasher

ADMIN_PASSWORD = "admin-password-for-tests"
AGENT_PASSWORD = "agent-password-for-tests"
# Minimal scrypt cost keeps the suite fast; production uses the PasswordHasher defaults.
FAST_HASHER = PasswordHasher(n=2**4, r=1, p=1)


class AccountServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.database = SupportDatabase(Path(self.temp_dir.name) / "accounts.sqlite3")
        self.database.sync_sample_data()
        self.now = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
        self.accounts = AccountService(self.database, FAST_HASHER, clock=lambda: self.now)
        self.accounts.ensure_default_admin(None, ADMIN_PASSWORD)
        self.admin = self.accounts.authenticate(self.bearer(DEFAULT_ADMIN_EMAIL, ADMIN_PASSWORD))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def bearer(self, email: str, password: str) -> str:
        return f"Bearer {self.accounts.login(email, password)['session_token']}"

    def create_agent(self, email: str = "agent@northstar.test", **overrides: object) -> dict:
        fields: dict[str, object] = {
            "email": email,
            "display_name": "Avery Agent",
            "role": "support_agent",
            "password": AGENT_PASSWORD,
            "customer_ids": ["cus-1001"],
        }
        fields.update(overrides)
        return self.accounts.create_account(self.admin, **fields)

    def test_default_admin_is_created_once(self) -> None:
        self.assertEqual(self.admin.role, "support_admin")
        self.assertIsNone(self.accounts.ensure_default_admin(None, "another-admin-password"))
        self.assertEqual(self.database.count_active_admins(), 1)

    def test_default_admin_password_is_generated_when_not_configured(self) -> None:
        database = SupportDatabase(Path(self.temp_dir.name) / "fresh.sqlite3")
        accounts = AccountService(database, FAST_HASHER)
        generated = accounts.ensure_default_admin("owner@northstar.test", None)
        self.assertIsNotNone(generated)
        self.assertEqual(
            accounts.login("owner@northstar.test", generated)["user"]["role"], "support_admin"
        )

    def test_weak_configured_admin_password_is_rejected(self) -> None:
        database = SupportDatabase(Path(self.temp_dir.name) / "weak.sqlite3")
        with self.assertRaises(ValidationError):
            AccountService(database, FAST_HASHER).ensure_default_admin(None, "short")

    def test_passwords_are_stored_only_as_scrypt_hashes(self) -> None:
        stored = self.database.staff_account_by_email(DEFAULT_ADMIN_EMAIL)
        self.assertNotIn(ADMIN_PASSWORD, stored["password_hash"])
        self.assertTrue(stored["password_hash"].startswith("scrypt$"))
        self.assertFalse(FAST_HASHER.verify(ADMIN_PASSWORD, "scrypt$tampered"))

    def test_wrong_password_and_unknown_email_fail_identically(self) -> None:
        with self.assertLogs("support_agent", "WARNING") as logs:
            with self.assertRaises(InvalidCredentialsError) as wrong:
                self.accounts.login(DEFAULT_ADMIN_EMAIL, "not-the-right-password")
            with self.assertRaises(InvalidCredentialsError) as unknown:
                self.accounts.login("nobody@northstar.test", "not-the-right-password")
        self.assertEqual(wrong.exception.message, unknown.exception.message)
        self.assertEqual([record.getMessage() for record in logs.records], ["login_failed"] * 2)
        # Audit logs identify accounts by user_id; emails and passwords never reach them.
        logged = str([vars(record) for record in logs.records])
        self.assertNotIn("northstar.test", logged)
        self.assertNotIn("not-the-right-password", logged)

    def test_email_is_case_insensitive(self) -> None:
        self.create_agent("Agent@Northstar.Test")
        self.assertTrue(self.bearer("AGENT@northstar.test", AGENT_PASSWORD))

    def test_account_locks_after_repeated_failures_and_unlocks_later(self) -> None:
        with self.assertLogs("support_agent", "WARNING"):
            for _ in range(5):
                with self.assertRaises(InvalidCredentialsError):
                    self.accounts.login(DEFAULT_ADMIN_EMAIL, "not-the-right-password")
            with self.assertRaises(InvalidCredentialsError):
                self.accounts.login(DEFAULT_ADMIN_EMAIL, ADMIN_PASSWORD)
        self.now += timedelta(minutes=16)
        self.assertTrue(self.bearer(DEFAULT_ADMIN_EMAIL, ADMIN_PASSWORD))

    def test_session_expires_and_logout_revokes_it(self) -> None:
        authorization = self.bearer(DEFAULT_ADMIN_EMAIL, ADMIN_PASSWORD)
        self.accounts.logout(authorization)
        with self.assertRaises(AuthenticationError):
            self.accounts.authenticate(authorization)
        authorization = self.bearer(DEFAULT_ADMIN_EMAIL, ADMIN_PASSWORD)
        self.now += timedelta(hours=8, seconds=1)
        with self.assertRaises(AuthenticationError):
            self.accounts.authenticate(authorization)
        with self.assertRaises(AuthenticationError):
            self.accounts.authenticate(None)

    def test_only_admin_creates_agents_and_approvers(self) -> None:
        self.create_agent()
        approver = self.create_agent(
            "approver@northstar.test", role="support_approver", display_name="Sam Approver"
        )
        self.assertEqual(approver["role"], "support_approver")
        self.assertNotIn("password_hash", approver)
        agent = self.accounts.authenticate(self.bearer("agent@northstar.test", AGENT_PASSWORD))
        with self.assertRaises(ForbiddenError):
            self.accounts.create_account(
                agent, "new@northstar.test", "New", "support_agent", AGENT_PASSWORD, []
            )
        with self.assertRaises(ForbiddenError):
            self.accounts.list_accounts(agent)

    def test_account_creation_validates_every_field(self) -> None:
        invalid_overrides = [
            {"role": "support_admin"},
            {"password": "short"},
            {"email": "not-an-email"},
            {"display_name": " "},
            {"customer_ids": ["cus-9999"]},
            {"customer_ids": "cus-1001"},
        ]
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.create_agent(**overrides)
        self.create_agent()
        with self.assertRaises(ConflictError):
            self.create_agent("AGENT@northstar.test")

    def test_created_agent_session_enforces_assigned_customer_scope(self) -> None:
        self.create_agent()
        tools = SupportTools(self.accounts, date(2026, 11, 4), database=self.database)
        agent = tools.authenticator.authenticate(
            self.bearer("agent@northstar.test", AGENT_PASSWORD)
        )
        self.assertEqual(agent.customer_ids, frozenset({"cus-1001"}))
        tools.call("get_order_details", {"order_id": "ord-5001"}, agent)
        with self.assertRaises(NotFoundError):
            tools.call("get_order_details", {"order_id": "ord-5003"}, agent)

    def test_approver_directory_lists_only_active_approvers(self) -> None:
        self.create_agent()
        approver = self.create_agent(
            "approver@northstar.test", role="support_approver", display_name="Sam Approver"
        )
        agent = self.accounts.authenticate(self.bearer("agent@northstar.test", AGENT_PASSWORD))
        listed = self.accounts.list_approvers(agent)
        self.assertEqual(
            listed,
            [
                {
                    "user_id": approver["user_id"],
                    "display_name": "Sam Approver",
                    "email": "approver@northstar.test",
                }
            ],
        )
        self.assertEqual(
            self.accounts.find_approver(approver["user_id"])["display_name"], "Sam Approver"
        )
        self.assertIsNone(self.accounts.find_approver(agent.user_id))
        self.assertIsNone(self.accounts.find_approver("missing"))

    def test_password_reset_clears_lock_and_signs_out_sessions(self) -> None:
        authorization = self.bearer(DEFAULT_ADMIN_EMAIL, ADMIN_PASSWORD)
        self.accounts.reset_password(DEFAULT_ADMIN_EMAIL, "replacement-admin-password")
        with self.assertRaises(AuthenticationError):
            self.accounts.authenticate(authorization)
        self.assertTrue(self.bearer(DEFAULT_ADMIN_EMAIL, "replacement-admin-password"))


class AccountApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        database = SupportDatabase(Path(self.temp_dir.name) / "api.sqlite3")
        database.sync_sample_data()
        self.accounts = AccountService(database, FAST_HASHER)
        self.accounts.ensure_default_admin(None, ADMIN_PASSWORD)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_login_route_returns_session_without_echoing_credentials(self) -> None:
        status, payload, request_id = execute_login(
            self.accounts, {"email": DEFAULT_ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["result"]["user"]["role"], "support_admin")
        self.assertNotIn(ADMIN_PASSWORD, str(payload))
        authorization = f"Bearer {payload['result']['session_token']}"
        status, payload, _ = execute_current_user(self.accounts, authorization)
        self.assertEqual(payload["result"]["email"], DEFAULT_ADMIN_EMAIL)
        status, _, _ = execute_logout(self.accounts, authorization)
        self.assertEqual(status, 200)
        status, payload, _ = execute_current_user(self.accounts, authorization)
        self.assertEqual((status, payload["error"]["code"]), (401, "unauthorized"))

    def test_login_route_rejects_bad_credentials_and_bodies(self) -> None:
        with self.assertLogs("support_agent", "WARNING"):
            status, payload, _ = execute_login(
                self.accounts, {"email": DEFAULT_ADMIN_EMAIL, "password": "not-the-right-password"}
            )
        self.assertEqual((status, payload["error"]["code"]), (401, "invalid_credentials"))
        status, payload, _ = execute_login(self.accounts, {"email": DEFAULT_ADMIN_EMAIL})
        self.assertEqual((status, payload["error"]["code"]), (400, "invalid_argument"))

    def test_admin_account_routes_require_admin_session(self) -> None:
        login = execute_login(
            self.accounts, {"email": DEFAULT_ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )[1]
        admin_authorization = f"Bearer {login['result']['session_token']}"
        body = {
            "email": "agent@northstar.test",
            "display_name": "Avery Agent",
            "role": "support_agent",
            "password": AGENT_PASSWORD,
            "customer_ids": ["cus-1001"],
        }
        status, payload, _ = execute_account_create(self.accounts, admin_authorization, body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["customer_ids"], ["cus-1001"])
        status, payload, _ = execute_account_list(self.accounts, admin_authorization)
        self.assertEqual(len(payload["result"]), 2)
        agent_login = execute_login(
            self.accounts, {"email": "agent@northstar.test", "password": AGENT_PASSWORD}
        )[1]
        agent_authorization = f"Bearer {agent_login['result']['session_token']}"
        status, payload, _ = execute_account_create(
            self.accounts, agent_authorization, {**body, "email": "other@northstar.test"}
        )
        self.assertEqual((status, payload["error"]["code"]), (403, "forbidden"))
        status, payload, _ = execute_account_create(self.accounts, None, body)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
