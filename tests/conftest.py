"""Test fixtures: a scripted LLM and a hashing embedder, so tests need no network or key."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date

import numpy as np
import pytest

from loandesk.config import Settings
from loandesk.db.seed import seed
from loandesk.db.session import Database, reset_db
from loandesk.knowledge.retriever import ingest
from loandesk.llm import LLMResult
from loandesk.wiring import build_app

AS_OF = date(2026, 11, 20)


class HashEmbedder:
    """Bag-of-words hashed into 64 dims: deterministic, and similar texts score higher."""

    dim = 64

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(self.dim, dtype=np.float32)
        for word in re.findall(r"\w+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        return (v / (np.linalg.norm(v) or 1.0)).tolist()

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


ROUTER_RULES = [
    (r"thieves|hurt them", {"intent": "complaint", "abusive": True}),
    (r"stock", {"intent": "out_of_scope"}),
    (r"number|email|otp", {"intent": "contact_update"}),
    (r"close|foreclos", {"intent": "foreclosure_quote"}),
    (r"failed|pending", {"intent": "failed_payment"}),
    (r"waive", {"intent": "complaint"}),
    (r"emi|due", {"intent": "emi_due_date"}),
]


class ScriptedLLM:
    """Plays the router, responder and judge with fixed rules. `waive_reply` makes the
    responder break policy, to test the output guardrail."""

    def __init__(self, waive_reply: bool = False, forbidden_tool: bool = False):
        self.waive_reply = waive_reply
        self.forbidden_tool = forbidden_tool
        self.calls: list[tuple[str, str]] = []

    def _result(self, model, content="", tool_calls=None) -> LLMResult:
        msg = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                                  "function": {"name": n, "arguments": json.dumps(a)}}
                                 for i, (n, a) in enumerate(tool_calls)]
        return LLMResult(model=model, message=msg, prompt_tokens=100, completion_tokens=20,
                         cost_usd=0.0001, latency_ms=5)

    def chat(self, model, messages, *, tools=None, response_format=None, temperature=0.0,
             max_tokens=800, tag="default"):
        self.calls.append((tag, model))
        if tag == "router":
            last = messages[-1]["content"].split("user: ")[-1].lower()
            out = {"intent": "emi_due_date", "confidence": 0.95, "wants_human": False,
                   "abusive": False, "language": "en"}
            for pattern, update in ROUTER_RULES:
                if re.search(pattern, last):
                    out |= update
                    break
            return self._result(model, json.dumps(out))
        if tag == "judge":
            return self._result(model, '{"faithful": true, "reason": "ok"}')

        intent = re.search(r"Intent: (\w+)", messages[0]["content"]).group(1)
        last = messages[-1]
        if last["role"] == "tool":
            result = json.loads(last["content"])
            if self.waive_reply:
                return self._result(model, "Good news, we will waive your penalty this month.")
            if "error" in result:
                return self._result(model, f"Sorry: {result['error']}")
            if "loans" in result:
                ln = result["loans"][0]
                return self._result(model, f"Your EMI of ₹{ln['emi_amount']:,.2f} is due on "
                                           f"{ln['next_due_date']}.")
            if "total_payable" in result:
                return self._result(model, f"To close the loan pay ₹{result['total_payable']:,.0f} "
                                           f"by {result['valid_until']}. No prepayment charge.")
            if result.get("status") == "otp_sent":
                return self._result(model, "I have sent a code to your registered number.")
            if result.get("status") == "updated":
                return self._result(model, f"Done, your {result['field']} has been updated.")
            return self._result(model, f"Done. Reference {result.get('ticket_id', '')}.")

        text = last["content"]
        if self.forbidden_tool:
            return self._result(model, tool_calls=[("update_contact",
                                                    {"field": "email", "new_value": "a@b.co"})])
        if intent == "emi_due_date":
            return self._result(model, tool_calls=[("get_loan_account", {})])
        if intent == "foreclosure_quote":
            return self._result(model, tool_calls=[("create_foreclosure_quote", {})])
        if intent == "failed_payment":
            return self._result(model, tool_calls=[("get_payment_status", {})])
        if intent == "contact_update":
            otp = re.search(r"\b(\d{6})\b", text)
            args = {"field": "phone", "new_value": "[PHONE_1]"}
            return self._result(model, tool_calls=[("update_contact",
                                                    args | ({"otp": otp.group(1)} if otp else {}))])
        if intent == "complaint":
            return self._result(model, tool_calls=[("create_zendesk_ticket", {
                "category": "complaint", "priority": "high", "subject": "Complaint",
                "description": "Borrower complaint"})])
        return self._result(model, "I can only help with your Udaan loan.")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(openrouter_api_key="test", state_dir=tmp_path / "state",
                    database_url=f"sqlite:///{tmp_path / 'test.db'}", vector_store="chroma",
                    chroma_url="memory", as_of=AS_OF, otp_mode="fixed")


@pytest.fixture
def db(settings) -> Database:
    reset_db(settings.database_url)
    database = Database(settings.database_url)
    seed(database, settings.as_of, settings.phone_hash_salt, n_borrowers=30)
    return database


@pytest.fixture
def make_app(settings, db):
    apps = []

    def _make(llm=None):
        app = build_app(settings, llm=llm or ScriptedLLM(), embedder=HashEmbedder())
        ingest(app.agent.retriever.store, app.agent.retriever.embedder)
        apps.append(app)
        return app

    yield _make
    for app in apps:
        app.close()
