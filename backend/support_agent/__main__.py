"""Run with: python -m support_agent"""

import logging
import os
import sys
from datetime import UTC, date, datetime

import dotenv

from .api.http import AppServices, serve
from .application.actions import ActionService
from .application.assistant import SupportAssistant
from .application.conversations import ConversationStore
from .application.tools import SupportTools
from .domain.errors import SupportError
from .infrastructure.action_store import SqliteActionRepository
from .infrastructure.database import SupportDatabase
from .infrastructure.log_format import JsonFormatter
from .infrastructure.openai_client import OpenAIChatClient
from .infrastructure.trace_store import TraceStore
from .security.accounts import DEFAULT_ADMIN_EMAIL, AccountService

loaded = dotenv.load_dotenv()


def bootstrap_admin(accounts: AccountService) -> None:
    """Create the first admin; a generated password is shown once on the console, never logged."""
    admin_email = os.environ.get("SUPPORT_ADMIN_EMAIL") or DEFAULT_ADMIN_EMAIL
    try:
        generated = accounts.ensure_default_admin(
            admin_email, os.environ.get("SUPPORT_ADMIN_PASSWORD") or None
        )
    except SupportError as error:
        sys.exit(f"Default admin setup failed: {error.message}")
    if generated:
        print(
            "\n=== Default administrator created ===\n"
            f"Email:    {admin_email}\n"
            f"Password: {generated}\n"
            "This password is shown only once. Sign in and create agent and approver accounts.\n",
            file=sys.stderr,
            flush=True,
        )


def main() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    as_of = date.fromisoformat(os.environ.get("SUPPORT_AS_OF_DATE", date.today().isoformat()))
    database = SupportDatabase()
    counts = database.sync_sample_data()
    logging.getLogger("support_agent").info(
        "database_ready", extra={"detail": f"path={database.path}; sample_records={counts}"}
    )
    accounts = AccountService(database)
    database.purge_staff_sessions(datetime.now(UTC).isoformat())
    bootstrap_admin(accounts)
    tools = SupportTools(accounts, as_of, database=database)
    host = os.environ.get("SUPPORT_HOST", "127.0.0.1")
    port = int(os.environ.get("SUPPORT_PORT", "8000"))
    model_client = OpenAIChatClient.from_environment()
    conversations = ConversationStore(database)
    actions = ActionService(tools, SqliteActionRepository(database))
    traces = TraceStore(database)
    assistant = (
        SupportAssistant(tools, model_client, conversations, actions, traces=traces)
        if model_client is not None
        else None
    )
    services = AppServices(
        tools=tools,
        assistant=assistant,
        actions=actions,
        accounts=accounts,
        conversations=conversations,
        traces=traces,
        allow_fault_injection=os.environ.get("SUPPORT_ENVIRONMENT") == "development",
    )
    serve(host, port, services)


if __name__ == "__main__":
    main()
