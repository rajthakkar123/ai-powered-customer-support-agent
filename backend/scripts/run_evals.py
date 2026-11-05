"""Run offline scenario contracts without an LLM API key or external service."""

import json
import sys
from datetime import date
from pathlib import Path

from support_agent.api.http import execute_tool_request
from support_agent.application.actions import ActionService
from support_agent.application.assistant import SupportAssistant
from tests.fakes import build_tools

ROOT = Path(__file__).resolve().parents[1]
TOKEN_MAP = {
    "agent-one-secret": {
        "user_id": "agent-001",
        "role": "support_agent",
        "customer_ids": ["cus-1001"],
    },
    "approver-one-secret": {
        "user_id": "approver-001",
        "role": "support_approver",
        "customer_ids": ["cus-1001"],
    },
}


class ScopeContractClient:
    """Deterministic client for validating prompt assembly and tool-free routing."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.call_count = 0
        self.messages: list[dict[str, object]] = []

    def complete(
        self,
        messages: list[dict[str, object]],
        deadline: float | None = None,
        allow_tools: bool = True,
    ) -> dict[str, object]:
        self.call_count += 1
        self.messages = messages
        turn = {
            "intent": "out_of_scope",
            "needs_clarification": False,
            "agent_summary": "Request is outside the support scope.",
            "suggested_reply": self.answer,
            "order_id": None,
            "proposed_action": None,
        }
        return {"choices": [{"message": {"role": "assistant", "content": json.dumps(turn)}}]}


def run_case(case: dict[str, object]) -> None:
    tools = build_tools(TOKEN_MAP, date(2026, 11, 4))
    agent = tools.authenticator.authenticate("Bearer agent-one-secret")
    case_type = case["type"]
    if case_type in {"tool", "failure"}:
        if case_type == "failure":
            tools.failing_tools.add(str(case["tool"]))
        status, payload, _ = execute_tool_request(
            tools,
            f"Bearer {case['token']}",
            {"tool": case["tool"], "arguments": case["arguments"]},
        )
        assert status == case["expected_status"], (status, payload)
        if "expected_error" in case:
            assert payload["error"]["code"] == case["expected_error"], payload
        else:
            for key, expected in case["expected_result"].items():
                assert payload["result"][key] == expected, payload
    elif case_type == "eligibility":
        result = tools.call("check_return_eligibility", case["arguments"], agent)
        assert result["eligible"] is case["expected_eligible"], result
    elif case_type == "scope_contract":
        expected_answer = str(case["expected_answer"])
        client = ScopeContractClient(expected_answer)
        assistant = SupportAssistant(tools, client)
        result = assistant.ask(case["message"], "cus-1001", None, agent, "customer_message")
        assert result["answer"] == expected_answer
        assert result["turn"]["intent"] == "out_of_scope"
        assert client.call_count == 1
        assert "intent out_of_scope, no tools" in client.messages[0]["content"]
    elif case_type == "approval":
        actions = ActionService(tools)
        result = actions.propose(
            case["action_type"], case["order_id"], None, "eval-proposal-key-001", agent
        )
        assert result["status"] == case["expected_status"], result
        assert result["execution"] is None, result
        assert actions.repository.execution_count() == 0
    elif case_type == "idempotency":
        actions = ActionService(tools)
        approver = tools.authenticator.authenticate("Bearer approver-one-secret")
        proposal = actions.propose(
            case["action_type"], case["order_id"], None, "eval-proposal-key-002", agent
        )
        first = actions.decide(
            proposal["proposal_id"], "approve", "eval-decision-key-001", approver
        )
        second = actions.decide(
            proposal["proposal_id"], "approve", "eval-decision-key-001", approver
        )
        assert first["status"] == second["status"] == "executed"
        assert actions.repository.execution_count() == case["expected_execution_count"]
        assert len(first["audit"]) == len(second["audit"])
    else:
        raise AssertionError(f"Unsupported eval type: {case_type}")


def main() -> int:
    scenarios = json.loads((ROOT / "evals" / "support_scenarios.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    for scenario in scenarios:
        try:
            run_case(scenario)
            print(f"PASS {scenario['id']}")
        except Exception as error:
            failures.append(f"{scenario['id']}: {type(error).__name__}: {error}")
            print(f"FAIL {failures[-1]}")
    print(f"{len(scenarios) - len(failures)}/{len(scenarios)} scenario evals passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
