"""Four scores per case (intent, tools, faithfulness, handoff) plus deterministic reply checks.

A score is True (pass), False (fail) or None (not checked for this case).
"""

from __future__ import annotations

import json
import re

from loandesk.agent.graph import TurnResult, _parse_json
from loandesk.agent.prompts import load_prompt
from loandesk.evals.cases import Case
from loandesk.llm import LLM, BudgetExceeded


def is_subsequence(expected: list[str], actual: list[str]) -> bool:
    it = iter(actual)
    return all(name in it for name in expected)


def score_deterministic(case: Case, turns: list[TurnResult]) -> dict[str, bool | None]:
    e = case.expect
    tools = [t["name"] for r in turns for t in r.tool_calls]
    reply = turns[-1].reply
    scores: dict[str, bool | None] = {
        "intent": None if e.intent is None else turns[0].intent == e.intent,
        "tools": is_subsequence(e.tools, tools) and not set(e.forbidden_tools) & set(tools),
        "handoff": None if e.handoff is None else any(r.handoff for r in turns) == e.handoff,
        "reply_checks": None,
        "redaction": None,
    }
    if e.must_include or e.must_not_include:
        scores["reply_checks"] = (
            all(re.search(p, reply, re.I) for p in e.must_include)
            and not any(re.search(p, reply, re.I) for p in e.must_not_include)
        )
    if e.redacted:
        found = {label for r in turns for label in r.pii_found}
        scores["redaction"] = set(e.redacted) <= found
    return scores


class Judge:
    def __init__(self, llm: LLM, model: str, prompts_dir):
        self.llm = llm
        self.model = model
        self.prompt = load_prompt(prompts_dir, "judge")

    def __call__(self, case: Case,
                 turns: list[TurnResult]) -> tuple[bool | None, str, float]:
        last = turns[-1]
        payload = {
            "borrower_messages": case.turns,
            "tool_results": [{"tool": t["name"], "result": t["result"]}
                             for r in turns for t in r.tool_calls],
            "policy_passages": [p["text"] for p in last.retrieved],
            "reply": last.reply,
        }
        try:
            res = self.llm.chat(self.model, [
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ], response_format={"type": "json_object"}, max_tokens=1000, tag="judge")
            out = _parse_json(res.content)
            return bool(out["faithful"]), str(out.get("reason", "")), res.cost_usd
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001 - a judge failure is recorded, not fatal
            return None, f"judge_error: {e}"[:300], 0.0
