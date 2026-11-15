"""The LangGraph agent: one request moves through these nodes.

    prepare -> route -> (handoff | retrieve -> respond -> check -> (handoff | finish))

prepare   redact PII, add the message to memory
route     small model sorts the intent; rules decide a handoff
retrieve  policy passages from the vector store
respond   responder model with only the tools allowed for the intent
check     deterministic output guardrails
handoff   open a ticket with a summary and tell the borrower
finish    write the reply to memory and log the turn for the dashboard
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from loandesk.agent.answer_cache import AnswerCache
from loandesk.agent.prompts import FALLBACK_REPLY, HANDOFF_REPLY, load_prompt
from loandesk.agent.state import AgentState, fresh_turn
from loandesk.config import Settings
from loandesk.db.models import Borrower, ConversationTurn, Ticket
from loandesk.db.session import Database, hash_phone
from loandesk.domain.intents import CACHEABLE_INTENTS, TOOL_ALLOWLIST, Intent
from loandesk.guardrails.output_check import check_reply
from loandesk.guardrails.pii import Vault, redact
from loandesk.knowledge.retriever import Retriever
from loandesk.llm import LLM, BudgetExceeded
from loandesk.tools.executor import ToolContext, ToolExecutor
from loandesk.tools.schemas import tool_specs
from loandesk.tracing import current_conversation

MAX_TOOL_ROUNDS = 6
HISTORY_MESSAGES = 10
ACCOUNT_INTENTS = {Intent.EMI_DUE_DATE, Intent.FAILED_PAYMENT, Intent.FORECLOSURE_QUOTE,
                   Intent.CONTACT_UPDATE}


@dataclass
class TurnResult:
    conversation_id: str
    reply: str
    intent: str | None
    confidence: float | None
    handoff: bool
    handoff_reason: str | None
    tool_calls: list[dict]
    flags: list[str]
    pii_found: list[str]
    retrieved: list[dict]
    cost_usd: float
    latency_ms: float
    models_used: list[str] = field(default_factory=list)
    model_inputs_redacted: str = ""


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError(f"No JSON object in model output: {text[:200]!r}")
    return json.loads(match.group(0))


def _clean_assistant(msg: dict) -> dict:
    """Keep only what providers accept back. reasoning_details must be returned for
    models that sign their reasoning (Gemini 3) or the next tool round fails."""
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = [{"id": c["id"], "type": "function",
                              "function": {"name": c["function"]["name"],
                                           "arguments": c["function"].get("arguments") or "{}"}}
                             for c in msg["tool_calls"]]
    if msg.get("reasoning_details"):
        out["reasoning_details"] = msg["reasoning_details"]
    return out


class LoanDeskAgent:
    def __init__(self, *, settings: Settings, llm: LLM, db: Database, retriever: Retriever,
                 executor: ToolExecutor, checkpointer, answer_cache: AnswerCache | None = None):
        self.settings = settings
        self.llm = llm
        self.db = db
        self.retriever = retriever
        self.executor = executor
        self.cfg = settings.models
        self.answer_cache = answer_cache or AnswerCache(self.cfg.get("answer_cache", {}))
        self.graph = self._build().compile(checkpointer=checkpointer)

    # --- model selection (layer 5 routing, eval overrides) --------------------------------

    def _models(self, config: RunnableConfig) -> dict[str, str]:
        overrides = (config.get("configurable") or {}).get("models") or {}
        return {"router": overrides.get("router", self.cfg["roles"]["router"]),
                "responder": overrides.get("responder", self.cfg["roles"]["responder"])}

    def _responder_for(self, intent: str, config: RunnableConfig) -> str:
        models = self._models(config)
        routing = self.cfg.get("routing") or {}
        overridden = "responder" in ((config.get("configurable") or {}).get("models") or {})
        if routing.get("enabled") and not overridden and intent not in routing["strong_intents"]:
            return routing["cheap_responder"]
        return models["responder"]

    # --- nodes -----------------------------------------------------------------------------

    def prepare(self, state: AgentState) -> dict:
        vault = Vault.from_dict(state.get("vault"))
        redacted, found = redact(state["user_text"], vault)
        return {"vault": vault.to_dict(), "pii_found": found, "redacted_text": redacted,
                "messages": [{"role": "user",
                              "content": f"<borrower_message>\n{redacted}\n</borrower_message>"}]}

    def route(self, state: AgentState, config: RunnableConfig) -> dict:
        model = self._models(config)["router"]
        history = state["messages"][-6:]
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in history)
        try:
            res = self.llm.chat(model, [
                {"role": "system", "content": load_prompt(self.settings.prompts_dir, "router")},
                {"role": "user", "content": transcript},
            ], response_format={"type": "json_object"}, max_tokens=1000, tag="router")
            out = _parse_json(res.content)
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001 - a failed router must not break the chat
            return {"handoff": True, "handoff_reason": "router_error", "error": str(e)[:300]}

        intent = out.get("intent") if out.get("intent") in set(Intent) else None
        confidence = float(out.get("confidence") or 0.0)
        update: dict[str, Any] = {
            "intent": intent, "confidence": confidence,
            "language": out.get("language") if out.get("language") in HANDOFF_REPLY else "en",
            "cost_usd": state.get("cost_usd", 0.0) + res.cost_usd,
            "models_used": [*state.get("models_used", []), model],
        }
        threshold = self.cfg.get("handoff", {}).get("min_confidence", 0.75)
        reason = None
        if out.get("abusive"):
            reason = "abusive_or_distressed"
        elif out.get("wants_human"):
            reason = "asked_for_human"
        elif intent is None:
            reason = "unknown_intent"
        elif intent != Intent.OUT_OF_SCOPE and confidence < threshold:
            reason = f"low_confidence_{confidence:.2f}"
        elif state.get("borrower_id") is None and intent in ACCOUNT_INTENTS:
            reason = "unregistered_number"
        if reason:
            update |= {"handoff": True, "handoff_reason": reason}
        return update

    def retrieve(self, state: AgentState) -> dict:
        if state["intent"] == Intent.OUT_OF_SCOPE:
            return {"retrieved": []}
        hits = self.retriever.search(state["redacted_text"])
        return {"retrieved": [{"id": h.id, "text": h.text, "score": round(h.score, 4),
                               "section_id": h.metadata.get("section_id")} for h in hits]}

    def _context_block(self, state: AgentState) -> str:
        lines = [f"Today: {self.settings.as_of.isoformat()}", f"Intent: {state['intent']}"]
        if state.get("borrower_id"):
            with self.db.session() as s:
                b = s.get(Borrower, state["borrower_id"])
                tickets = s.scalars(select(Ticket).where(Ticket.borrower_id == b.id)
                                    .order_by(Ticket.created_at.desc()).limit(5)).all()
                lines.append(f"Borrower first name: {b.name.split()[0]}; "
                             f"preferred language: {b.language}")
                if tickets:
                    lines.append("Recent tickets (memory):")
                    lines += [f"- {t.external_id} [{t.status}] {t.created_at:%d %b %Y}: "
                              f"{t.subject}" for t in tickets]
        else:
            lines.append("This phone number is not registered with Udaan.")
        passages = "\n\n".join(f"[{p['section_id']}]\n{p['text']}" for p in state["retrieved"])
        lines.append(f"<policy_passages>\n{passages or '(none)'}\n</policy_passages>")
        return "\n".join(lines)

    def _system(self, model: str, state: AgentState) -> dict:
        static = load_prompt(self.settings.prompts_dir, "system")
        dynamic = self._context_block(state)
        if self.cfg.get("prompt_caching") and model.startswith("anthropic/"):
            return {"role": "system", "content": [
                {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": dynamic},
            ]}
        return {"role": "system", "content": f"{static}\n\n## Context\n{dynamic}"}

    def respond(self, state: AgentState, config: RunnableConfig) -> dict:
        intent = Intent(state["intent"])
        model = self._responder_for(intent, config)
        cost, models_used = state.get("cost_usd", 0.0), [*state.get("models_used", []), model]

        cacheable = intent in CACHEABLE_INTENTS and self.answer_cache.enabled
        cache_key = (state["messages"][-1]["content"], self.settings.prompt_version, model)
        if cacheable and (cached := self.answer_cache.get(cache_key)):
            return {"reply": cached, "cache_hit": True}

        allowed = TOOL_ALLOWLIST[intent]
        tools = tool_specs(allowed) if allowed else None
        vault = Vault.from_dict(state.get("vault"))
        ctx = ToolContext(conversation_id=state["conversation_id"],
                          borrower_id=state.get("borrower_id"), vault=vault,
                          as_of=self.settings.as_of)
        messages = [self._system(model, state), *state["messages"][-HISTORY_MESSAGES:]]
        tool_log: list[dict] = []
        handoff, ticket = False, None
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                res = self.llm.chat(model, messages, tools=tools, tag="responder")
                cost += res.cost_usd
                if not res.tool_calls:
                    reply = res.content.strip()
                    break
                messages.append(_clean_assistant(res.message))
                for call in res.tool_calls:
                    name, args = call["function"]["name"], call["function"].get("arguments")
                    result = self.executor.execute(name, args or "{}", allowed, ctx)
                    tool_log.append({"name": name, "args": args, "result": result})
                    if name == "handoff_to_human" and "error" not in result:
                        handoff, ticket = True, result["ticket_id"]
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(result, default=str)})
            else:
                return {"tool_log": tool_log, "cost_usd": cost, "models_used": models_used,
                        "handoff": True, "handoff_reason": "too_many_tool_rounds"}
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001 - provider outage: fall back to a human
            return {"tool_log": tool_log, "cost_usd": cost, "models_used": models_used,
                    "handoff": True, "handoff_reason": "model_error", "error": str(e)[:300]}

        if not reply:
            return {"tool_log": tool_log, "cost_usd": cost, "models_used": models_used,
                    "handoff": True, "handoff_reason": "empty_reply"}
        if cacheable and not tool_log:
            self.answer_cache.put(cache_key, reply)
        update = {"reply": vault.mask(reply), "tool_log": tool_log, "cost_usd": cost,
                  "models_used": models_used}
        if handoff:
            update |= {"handoff": True, "handoff_reason": "agent_handoff",
                       "route_reason": f"ticket:{ticket}"}
        return update

    def check(self, state: AgentState) -> dict:
        verdict = check_reply(state["reply"], [t["result"] for t in state["tool_log"]],
                              [p["text"] for p in state["retrieved"]])
        if verdict.ok:
            return {}
        return {"flags": verdict.flags, "handoff": True,
                "handoff_reason": f"guardrail:{','.join(verdict.flags)}", "reply": ""}

    def handoff(self, state: AgentState) -> dict:
        lang = state.get("language", "en")
        # The responder already handed off and wrote its own reply.
        if state.get("handoff_reason") == "agent_handoff" and state.get("reply"):
            return {}
        last_user = state["messages"][-1]["content"]
        tools = ", ".join(t["name"] for t in state.get("tool_log", [])) or "none"
        ticket = self.executor.tickets.create_ticket(
            borrower_id=state.get("borrower_id"),
            subject=f"Handoff ({state.get('handoff_reason')}): {state.get('intent') or 'unknown'}",
            description=(f"Reason: {state.get('handoff_reason')}\nIntent: {state.get('intent')} "
                         f"(confidence {state.get('confidence')})\nTools already called: {tools}\n"
                         f"Latest message (redacted):\n{last_user}\n\n"
                         f"(conversation {state['conversation_id']})"),
            category="complaint" if state.get("intent") == Intent.COMPLAINT else "other",
            priority="urgent" if state.get("handoff_reason") == "abusive_or_distressed"
            else "high",
        )
        prefix = FALLBACK_REPLY[lang] if str(state.get("handoff_reason", "")).startswith(
            ("guardrail", "model_error", "router_error", "empty_reply", "too_many")) else ""
        return {"reply": prefix + HANDOFF_REPLY[lang].format(ticket=ticket)}

    def finish(self, state: AgentState) -> dict:
        return {"messages": [{"role": "assistant", "content": state["reply"]}]}

    # --- wiring ----------------------------------------------------------------------------

    def _build(self) -> StateGraph:
        g = StateGraph(AgentState)
        for name in ("prepare", "route", "retrieve", "respond", "check", "handoff", "finish"):
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "prepare")
        g.add_edge("prepare", "route")
        g.add_conditional_edges("route", lambda s: "handoff" if s.get("handoff") else "retrieve")
        g.add_edge("retrieve", "respond")
        g.add_conditional_edges("respond", lambda s: "check" if s.get("reply") else "handoff")
        g.add_conditional_edges("check", lambda s: "handoff" if s.get("handoff") else "finish")
        g.add_edge("handoff", "finish")
        g.add_edge("finish", END)
        return g

    # --- entry point -----------------------------------------------------------------------

    def conversation_id_for(self, phone: str) -> str:
        return "c_" + hash_phone(phone, self.settings.phone_hash_salt)[:24]

    def turn(self, phone: str, text: str, *, conversation_id: str | None = None,
             models: dict[str, str] | None = None, source: str = "chat") -> TurnResult:
        conversation_id = conversation_id or self.conversation_id_for(phone)
        token = current_conversation.set(conversation_id)
        start = time.perf_counter()
        try:
            borrower = self.executor.find_borrower(phone)
            config = {"configurable": {"thread_id": conversation_id, "models": models or {}}}
            state = self.graph.invoke(
                {**fresh_turn(text), "conversation_id": conversation_id,
                 "borrower_id": borrower.id if borrower else None},
                config,
            )
        finally:
            current_conversation.reset(token)
        latency_ms = (time.perf_counter() - start) * 1000

        result = TurnResult(
            conversation_id=conversation_id, reply=state["reply"], intent=state.get("intent"),
            confidence=state.get("confidence"), handoff=bool(state.get("handoff")),
            handoff_reason=state.get("handoff_reason"),
            tool_calls=[{"name": t["name"], "args": t["args"], "result": t["result"]}
                        for t in state.get("tool_log", [])],
            flags=state.get("flags", []), pii_found=state.get("pii_found", []),
            retrieved=state.get("retrieved", []),
            cost_usd=round(state.get("cost_usd", 0.0), 6), latency_ms=round(latency_ms, 1),
            models_used=state.get("models_used", []),
            model_inputs_redacted=state["messages"][-2]["content"],
        )
        with self.db.session() as s:
            s.add(ConversationTurn(
                conversation_id=conversation_id, intent=result.intent,
                confidence=result.confidence, handoff=result.handoff,
                handoff_reason=result.handoff_reason, models=",".join(dict.fromkeys(
                    result.models_used)), prompt_version=self.settings.prompt_version,
                latency_ms=result.latency_ms, cost_usd=result.cost_usd, flags=result.flags,
                source=source))
        return result


def new_conversation_id(prefix: str = "c") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"
