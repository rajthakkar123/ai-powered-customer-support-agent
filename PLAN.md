# AI-Powered Customer Support Agent

## Project goal

Build a maintainable support workspace that answers customer questions from sample business data, shows the evidence and tools used, and requires an authorized human approval before any consequential action. Model output is advisory: backend code owns identity checks, policy decisions, and action execution.

## Backend-first delivery plan

Each stage is implemented and reviewed before the next begins. A stage is complete when its acceptance checks and relevant tests/evals pass, and its status below is updated.

| Stage | Scope | Completion checks | Status |
| --- | --- | --- | --- |
| 1. Backend foundation and sample domain | Python standard-library API skeleton, customer/order/items/shipping/conversation/policy fixtures, stable IDs, configuration and secret-handling baseline | API starts; fixture relationships validate; no secrets are committed | Complete |
| 2. Trusted support tools | Customer lookup, order lookup, deterministic return/refund eligibility; argument validation, authorization boundary, typed errors, trace IDs | Valid, missing, malformed, unauthorized, and simulated failure cases are covered | Complete |
| 3. Assistant orchestration | Intent handling, model tool selection, server-side tool dispatch, bounded retries, clarification and out-of-domain behavior, structured answers | Tool calls are validated and authorized in backend code; model cannot directly execute consequential actions | Complete |
| 4. Traceability and diagnostics | Per-request/correlation IDs, LLM request/response metadata, tool input/output/status/duration, redaction, actionable error logs | A conversation can be reconstructed from safe traces without exposing secrets or unnecessary personal data | Complete |
| 5. Human approval and action safety | Approve/reject API workflow, authorization re-check at execution, idempotency keys, duplicate protection, audit record | No refund/order change occurs without approval; repeated submissions cannot execute twice | Complete |
| 6. Quality gates, tests, and evals | Unit/integration/security/workflow tests, scenario eval dataset, formatting/lint/type checks, CI-ready commands and operator notes | Required backend scenarios pass; checks are repeatable and explain failures clearly | Complete |
| 7. User interface | Conversation workspace, suggested replies, tool evidence, loading/error states, scoped customer selection, approval controls | UI consumes the backend contract and exposes trace and action state | Complete |
| 8. SQLite persistence and one-command startup | Relational sample-data schema, idempotent seed sync, persistent conversations/actions/idempotency, venv setup and native PowerShell/Bash browser launchers | Fresh setup creates a usable database and restarting preserves live conversation/action state | Complete |
| 9. Support-only product and customer routes | Make the agent workspace the only UI, expose authenticated `/customers` and `/products` routes, and allow grounded product lookups from the assistant | The root page opens the support console; customer routes require agent scope; product/customer/order/policy questions use backend evidence | Complete |

## Intended completed flow

1. An authenticated support user opens a conversation and selects or confirms the customer they are authorized to support.
2. The user asks a question. The server assigns a correlation ID and records a redacted trace entry.
3. The assistant identifies the information it needs. If a required customer/order identifier is missing or ambiguous, it asks a focused clarification question.
4. For factual questions, the model may request an allow-listed read tool. The server validates the tool name and arguments, derives the acting user's authorization from trusted session state, and invokes deterministic backend code.
5. The tool returns structured data or a typed error. The trace records the tool, validated arguments, outcome, duration, and safely redacted result. The model receives only the result needed to draft the answer.
6. The API returns the suggested response alongside evidence and a tool timeline. Failed calls return a useful error and a safe retry path; bounded retries never bypass authorization.
7. If the workflow proposes a refund or order change, it creates a pending proposal only. An authorized human reviews and approves or rejects it.
8. On approval, the server revalidates permissions and business rules, then executes with an idempotency key and writes an audit record. Duplicate approval requests return the original outcome without repeating the action.
9. The conversation and trace remain available for follow-up and debugging, with secrets and unnecessary personal data excluded.

## Required scenario coverage

| Scenario | Expected behavior |
| --- | --- |
| Retrieve an existing order | Return SQLite-backed order details and visible tool evidence |
| Retrieve a nonexistent order | Return a clear not-found result; do not invent details |
| Eligible return | Deterministic policy result with reasons and applicable deadline |
| Ineligible return | Deterministic denial with the specific rule/reason |
| Unrelated question | Clearly state the support scope and offer a relevant next step |
| Backend tool failure | Show a traceable error; apply only bounded safe retry or explain recovery |
| Cross-customer lookup | Deny using server-derived authorization; do not disclose whether protected data exists |
| Consequential action without approval | Leave action unexecuted and visibly pending approval |
| Duplicate action submission | Execute at most once for a given idempotency key |

## Engineering and security rules

- Keep policy and authorization decisions in deterministic server-side code.
- Treat model tool calls, identifiers, and generated text as untrusted input.
- Keep API keys in environment configuration; commit only a placeholder example file.
- Validate all tool arguments and reject unknown tools and unexpected fields.
- Use stable fixture IDs and explicit customer/order relationships.
- Give each request and tool operation a correlation ID; log structured, redacted diagnostics.
- Bound model/tool timeouts, retries, and conversation context. Do not retry consequential actions automatically.
- Keep tests and evals deterministic where possible; never use a passing model response as proof of authorization or policy correctness.

## Completed end-to-end flow (current build)

1. Start the support workspace with `start.ps1` on Windows or `start.sh` on Unix. The launcher prepares the virtual environment, syncs SQLite schema/sample data, and opens the support console at `/`.
2. The agent enters the generated agent token. The API uses that token to determine which customer records the agent may access; customer IDs supplied by the browser or model never expand that scope.
3. The agent selects an authorized customer and asks about customer history, a product, an order, a return, or a policy. Assistant turns use persistent conversation context and validated read tools.
4. `/customers` returns only customers in the authenticated agent's scope. `/products` returns the catalog to authenticated support staff. Product, order, and customer detail tools return validated SQLite-backed records.
5. Policy eligibility is computed deterministically from delivery state, return status, policy window, and configured as-of date. Cross-customer access is denied.
6. The response includes tool/model status and evidence. The evidence guard replaces factual answers with a clarification if the model skips a required backend lookup.
7. Consequential proposals remain pending until a separate authorized approver decides. Execution is idempotent and written to the audit log.
8. Conversations, complaints, action proposals, audit entries, and idempotency records persist in SQLite. Model and tool failures return clear errors; secrets and message content are not written to general logs.
9. Tests cover valid/missing order, eligible/ineligible return, unrelated queries, simulated tool failures, cross-customer denial, product lookup, and human approval/idempotency.

SQLite preserves customer records, products, orders, complaints, conversation history, action proposals, decisions, audit history, and idempotency state across restarts. Approval records local sample outcomes; no payment is collected. SQLite is suitable for this single-instance demo; a multi-instance production deployment should use a managed database.

## Current completion record

- [x] Inspected the workspace; it was empty, so no prior framework or local conventions needed migration.
- [x] Agreed on staged delivery before implementation.
- [x] Added this plan and the end-to-end target flow.
- [x] Stage 1: backend foundation and sample domain.
- [x] Stage 2: trusted support tools.
- [x] Stage 3: assistant orchestration.
- [x] Stage 4: traceability and diagnostics.
- [x] Stage 5: human approval and action safety.
- [x] Stage 6: quality gates, tests, and evals.
- [x] Stage 7: UI workspace, customer context, assistant conversation, tool evidence, and human approval controls in `frontend/`.
- [x] Stage 8: SQLite schema, idempotent fixture sync, persistent conversations/actions/idempotency, and root `start.ps1`/`start.sh` setup and launch flows.
- [x] Stage 9: support-only UI at `/`, authorized `/customers` and `/products` routes, and product details in the assistant tool set. `/support` remains a compatibility path to the same console.
- [x] Verified backend checks: 44 support workflow/security/persistence tests passed, 10/10 offline scenario evals passed, bytecode compilation passed, and Ruff lint/format checks passed through `backend/scripts/check.ps1`.
- [x] Verified frontend JavaScript syntax with Node.js `--check`.
