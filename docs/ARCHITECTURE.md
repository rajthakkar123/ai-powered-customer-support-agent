# Architecture and design decisions

This document explains how the support agent works and answers the questions the review will ask. Paths are relative to `backend/`.

## Request flow

```text
Browser (frontend/)                       Server (support_agent/)
──────────────────                        ──────────────────────────────────────────────────────
"Customer says…" message ──POST /assistant/ask──▶ api/http.py
                                                   │  session → Principal (security/accounts.py)
                                                   ▼
                                    application/assistant.py  (bounded agent loop)
                                                   │  1. frame input: CUSTOMER MESSAGE = untrusted data
                                                   │  2. model call (OpenAI, JSON schema + read-only tools)
                                                   │  3. for each tool call → application/tools.py
                                                   │       validate args → authorize → deterministic logic
                                                   │  4. repeat until a structured final answer (≤5 rounds,
                                                   │     ≤6 tool calls; last round has tools switched off)
                                                   │  5. server guardrails (evidence, required checks,
                                                   │     existence masking, scope)
                                                   │  6. proposed action → application/actions.py
                                                   │       policy re-check → PENDING proposal (never executed)
                                                   ▼
             suggestion card ◀── intent, notes, draft reply, tool timeline, proposal (Approve/Reject)
Approver ──POST /actions/decide──▶ actions.py: different person + re-check + execute once + audit
```

Every request has a `request_id`. Every model call and every assistant request, including failures, is stored in `assistant_requests` / `llm_calls` (`infrastructure/trace_store.py`) and readable by admins at `GET /admin/traces/{request_id}`.

## How the model selects a tool, and how arguments are validated

1. The model receives only **read-only** tools (`application/tool_schemas.py`): customer details, order details, return eligibility, cancellation eligibility, policy, and product details. Consequential actions are not tools; the model can only *suggest* one in its structured answer.
2. With `tool_choice: auto` the model picks tools from their descriptions and the system prompt's rules (for example, "for any order ID, call get_order_details", "return questions must use check_return_eligibility").
3. The server never trusts the call. `SupportTools.call` (`application/tools.py`):
   - rejects unknown tools and any argument set that is not exactly the schema's;
   - checks every identifier's own prefix (`order_id` must look like `ord-1234`, so `cus-1001` is rejected);
   - checks enums (`action`, `topic`) against the same constants the schema uses. A test (`test_model_tool_schemas_match_server_validation`) fails if the schema the model sees and the server's validation drift apart;
   - authorizes with a principal derived from the **session**, narrowed to the **conversation's customer**, never from model-supplied IDs.
4. The final answer must match a strict JSON schema (`application/turns.py`). The server validates it again, asks for one repair if it is malformed, and fails clearly if it is still invalid.

## How unauthorized customer data access is prevented

- **Identity comes from the server.** A sign-in creates a server-side session; only a SHA-256 digest of the token is stored. The principal's role and customer scope come from the account the admin created, never from the browser or the model.
- **Two scopes apply to every lookup.** Staff scope (which customers this account may see) *and* conversation scope (`Principal.for_conversation`): during an assistant turn, tools can only reach the active conversation's customer, even for an agent or admin who may see others.
- **No enumeration.** Missing and out-of-scope records return the same `not_found`. The assistant additionally checks, on the server, every order ID in the customer's message; if the customer cannot see it, wording that would hint it exists elsewhere ("belongs to another customer", "under a different account") is replaced with standard not-found wording. This was found and fixed through a live eval.
- **Lists are filtered, not reported.** `/customers`, the approval queue, and conversation threads only include records in scope; conversations are also owner-scoped.

## Failed tool executions and retries

| Failure | Behaviour |
| --- | --- |
| Tool raises a typed error (`not_found`, `invalid_argument`) | Returned to the model as data; shown in the timeline |
| Tool backend fails (`tool_failure`) | Returned to the model as an error; **the same call is never re-run in that turn** (it gets `repeat_of_failed_call`); the prompt tells the model to tell the customer about a temporary problem |
| Unexpected exception in a tool | Logged with its traceback, reported to the model as `tool_failure`; never crashes the turn |
| Tool budget spent | Over-budget calls get `tool_budget_exhausted`; the next model call has tools switched off, so the turn ends with an answer, not an error |
| Model provider errors | One retry with backoff for 429/5xx/timeouts (honouring `Retry-After`); 401/403/404 are not retried and say what to fix (`invalid_api_key`, `OPENAI_MODEL`) |
| Whole turn | 45 s deadline on the server; the browser waits 60 s; failed turns show a **Retry** button and their trace |

Reads are safe to retry; **consequential actions are never retried automatically**.

`SUPPORT_ENVIRONMENT=development` enables `X-Simulate-Tool-Failure`, which the UI exposes as "Simulate failure" for scenario 6.

## Preventing duplicate execution

Actions are stored in SQLite (`infrastructure/action_store.py`), and every propose/decide runs in one `BEGIN IMMEDIATE` transaction, so these rules hold across threads **and processes**:

1. **Idempotency keys.** `idempotency_records(scope, key)` is a primary key. Replaying the same propose or decide returns the original result; reusing a key for a different request is a `409`.
2. **One pending proposal per order and action**, enforced twice: by the service and by a partial unique index (`idx_action_one_pending`).
3. **Execute once.** `action_executions(proposal_id)` is a primary key, and the order outcome is written in the same transaction. A second approval with a new key returns the executed proposal without running anything.
4. **Re-check at approval.** Eligibility is checked again before executing; refunds are per item and priced by the server in integer cents, and refunded items cannot be refunded again.

Tests cover concurrent approvals, two independent service instances sharing a database, and the database-level duplicate guard.

## Single agent or multiple agents

This build uses **one agent with a small toolset and server-side guardrails**:

- the domain is narrow (orders, returns, refunds, policies) and fits in one prompt;
- every decision that matters (eligibility, authorization, execution) is deterministic code, so splitting the LLM part would not add safety;
- one loop is easier to trace, test, and explain.

I would move to **multiple specialized agents** (for example a triage/router agent handing off to refunds, shipping, and account-security agents) when:

- the tool count or policy text grows enough that one prompt dilutes tool selection;
- sub-domains need different models, permissions, or data access (a security agent that may unlock accounts should not share tools with a general agent);
- workflows become long-running or branching (wait for a carrier, then decide).

At that point a graph framework such as **LangGraph** earns its place: explicit state, routing between agents, and checkpointed pauses. The tools, authorization, and approval service in this codebase would stay as they are; only the orchestration would change.

### Why no LangChain or LangGraph today

The loop is about 150 lines (`assistant.py`) over OpenAI's Chat Completions API. Everything the assignment grades (validation, authorization, deterministic policy, idempotency, human approval) lives in backend code regardless of framework, and keeping the loop explicit makes each guardrail visible and unit-testable. The human approval step is a durable queue decided by a different person later, which is the production pattern; a framework checkpoint would add a second state store without removing the queue.

## What to monitor in production

| Signal | Why | Source today |
| --- | --- | --- |
| Turn latency p50/p95 and model latency per call | User experience and provider health | `assistant_requests.duration_ms`, `llm_calls.duration_ms` |
| Tokens and cost per turn, per model | Budget and prompt regressions | `llm_calls.input_tokens/output_tokens` |
| Tool error rate by tool and code | Backend health; `tool_failure` spikes | trace steps, `tool_call` logs |
| Guardrail rates: `required_check_missing`, `clarification_required`, `existence_masked`, `invalid_structured_output` | Model drift and prompt regressions | trace steps |
| Authorization denials (`not_found` on out-of-scope lookups), login failures, lockouts | Security signal: probing or misconfiguration | logs, `login_failed` |
| Proposals created vs approved vs rejected; time to decision | Quality of suggestions; approver workload | `action_proposals`, audit |
| Duplicate-prevention hits (idempotent replays, 409s) | Client retry storms | action logs |
| `internal_error` count with `error_detail` | Defects | `assistant_requests` |
| Live eval pass rate per prompt version | Catch regressions before release | `scripts/run_live_evals.py` reports |

Alerts would page on: provider 401/403 (bad key), tool error rate above baseline, any `internal_error` burst, and an eval pass-rate drop after a prompt change.

## Evidence

- `scripts/check.ps1` / `scripts/check.sh`: 110 unit, workflow, and security tests; 89% branch coverage (threshold 85%); offline scenario evals; Ruff; mypy; secret scan; JavaScript syntax. CI runs the same gate on Linux and Windows.
- `scripts/run_live_evals.py --repeat 3` against `gpt-4o-mini` (2026-11-05): **35/36 passed**. All 8 required scenarios, the damaged-item flow, follow-up, prompt injection, and cancellation passed 3/3. "Missing information" passed 2/3: the model sometimes offers the only eligible order instead of asking which order, and once called it the customer's "most recent" order, which was wrong.

## Known limitations

- SQLite with a single writer is right for this demo; production would use a managed database.
- Approval execution records a sample outcome; no payment processor or carrier is connected.
- The approver can sign in on the agent's screen to decide; production would rely on the approver's own queue only.
- There are no browser end-to-end tests yet (Playwright); the UI is checked by syntax and manual review.
