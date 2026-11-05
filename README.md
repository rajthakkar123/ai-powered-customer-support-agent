# AI-Powered Customer Support Agent

A support agent workspace for customer care teams. The assistant uses backend tools for authorized customer, product, order, complaint, and policy facts; it shows tool evidence and drafts replies. Consequential actions remain pending until a separate authorized approver approves them.

## Root layout

```text
frontend/   Browser application: HTML, CSS, JavaScript
backend/    Python API, domain logic, tests, evals, and developer scripts
start.ps1   Native Windows setup and startup script
start.sh    Bash setup and startup script for Unix environments
PLAN.md     Delivery plan and completed end-to-end flow
```

## Run locally

On Windows, use PowerShell from the project root:

```powershell
.\start.ps1
```

The script does not require WSL or Git Bash. It creates `backend/.venv` if needed, installs backend requirements, syncs the SQLite schema and sample data, starts the API with logs in the PowerShell window, and opens the support workspace. Set `OPENAI_API_KEY` in the shell before running the script to enable AI replies; without it, support records and policies remain available but assistant replies report that AI is not configured.

## Staff accounts

Everyone signs in with a work email and password.

1. **First start:** the server creates an administrator (`admin@northstar.test` unless `SUPPORT_ADMIN_EMAIL` is set). If `SUPPORT_ADMIN_PASSWORD` is unset, a random password is printed **once** on the console; it is never written to logs.
2. **Provisioning:** only the administrator can create accounts, from the **Team** page. Each account is a support agent or an approver, and the administrator assigns which customers it may access.
3. **Approvals:** a proposed refund or order change is decided by an approver entering their own email and password on the proposal card. The proposer can never approve their own proposal.
4. **Sessions:** a sign-in creates an 8-hour server-side session (only a SHA-256 digest of the token is stored). The browser keeps the token in `sessionStorage`, so a refresh stays signed in while closing the tab ends it; on reload the token is re-validated with `GET /auth/me`. Five wrong passwords lock the account for 15 minutes. Signing out revokes the session server-side and clears the browser copy. Approver sessions opened for a single decision are never stored.
5. **Recovery:** if the admin password is lost, stop the server and run `python -m support_agent.security.accounts reset-admin-password` from `backend/` with `SUPPORT_ADMIN_PASSWORD` set. This also signs out existing sessions.

Passwords are hashed with scrypt and must be 12–128 characters.

On macOS/Linux, run `bash ./start.sh` from the root instead.

The database is `backend/data/support_agent.sqlite3` by default. The app creates or upgrades its schema and re-syncs sample customers, products, orders, items, shipping, support history, and policies every startup. Complaints, conversations, action proposals, approval audit entries, and idempotency records are retained. `SUPPORT_DATABASE_PATH` can point to another SQLite file. The support API scopes customer and order data to the connected agent.

The staff API exposes `GET /customers` for the authenticated agent's customer scope and `GET /products` for the sample product catalog. Both require a signed-in staff session. The assistant can also retrieve individual product facts through its validated read-only tool set. The former shopper login, checkout, and complaint submission routes are not exposed in this support-only build.

## Simulating customer conversations

The composer has two modes. **Customer says…** simulates an inbound customer message: the copilot classifies the intent, looks facts up with backend tools, and returns a suggestion card with notes for the agent, an editable customer reply (**Send reply** records it in the thread), the tools it called with their arguments and results, and, when policy allows, a pending refund or cancellation with Approve/Reject. **Ask copilot** sends an internal question that is never shown to the customer. Threads reload after a refresh; **New conversation** starts a fresh one.

The **Sample messages** menu covers the required scenarios for Avery Johnson (`cus-1001`). Local launchers set `SUPPORT_ENVIRONMENT=development`, which shows a **Simulate failure** menu that makes one backend tool fail for the next turn.

| Scenario | How to run it | Expected result |
| --- | --- | --- |
| Valid order | Sample "Order status" | `get_order_details` succeeds; reply cites delivery facts |
| Missing order | Sample "Order that does not exist" | Tool returns `not_found`; reply asks to confirm the number |
| Eligible return | Sample "Damaged item" | Eligibility passes; a pending refund with Approve/Reject |
| Ineligible return | Sample "Return outside the window" | Eligibility fails with the reason; no proposal |
| Unrelated question | Sample "Unrelated question" | `out_of_scope`, no tools, polite redirect |
| Tool failure | Choose a tool in Simulate failure, then send any order question | Timeline shows `tool_failure`; reply explains the problem |
| Another customer's data | Sample "Another customer's order" | Server returns `not_found`; nothing is disclosed |
| No action without approval | Approve the damaged-item refund with the agent's own login, then with an approver | First attempt is refused; the approver's runs once |

The model's final answer must match a JSON schema (`support_agent/application/turns.py`) and is validated again on the server. Policy is re-checked in code before any proposal is created, and tools can only reach the conversation's customer.

## Database structure

SQLite uses foreign keys for customer-to-order, order-to-shipment/items, conversation-to-customer/order, complaint-to-customer/order, and action-to-order relationships. Tables include `customers`, `staff_accounts`, `staff_sessions`, `products`, `orders`, `order_items`, `order_line_items`, `shipments`, `customer_complaints`, `support_conversations`, `conversation_messages`, `policies`, `action_proposals`, `action_audit`, `idempotency_records`, and `schema_migrations`. Structured payload columns preserve flexible domain details while indexed columns enforce relationships and support common lookups.

## Checks

Run the backend quality gate from the project root:

```powershell
& .\backend\scripts\check.ps1
```

On macOS/Linux use `bash backend/scripts/check.sh`. Both run the same gate as CI (`.github/workflows/ci.yml`, Linux and Windows): installs pinned dev tools if missing, tests with branch coverage (fails under 85%), offline scenario evals, Ruff lint and format, mypy, a secret scan that never opens `.env` files, and a JavaScript syntax check. None of it needs an API key.

### Live LLM evals

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.run_live_evals --repeat 3
```

Runs the required scenarios plus damaged item, follow-up, prompt injection, missing information, and cancellation against the real model. The key is read from the environment or `backend/.env` inside the eval process and never printed. Each case uses a fresh temporary database and is graded deterministically (tools called, tool results, reply facts, safety outcomes). Reports are written to `backend/evals/reports/` (git-ignored).

### Debugging a request

Every response carries a `request_id`. Administrators can read the full record, including every model call's exact input and output (emails masked), at `GET /admin/traces/{request_id}`; `GET /admin/traces` lists recent requests. Unexpected errors store their exception type, message, and code location there, and log lines include the traceback.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design, the answers to the review questions (tool selection, authorization, retries, idempotency, single vs multiple agents, monitoring), and the latest eval results.
