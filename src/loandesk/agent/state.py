from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    # Persisted across turns by the checkpointer (conversation memory).
    messages: Annotated[list[dict], operator.add]  # redacted user and assistant messages
    vault: dict  # placeholder -> real value, see guardrails/pii.py
    conversation_id: str
    borrower_id: str | None

    # Reset at the start of every turn.
    user_text: str
    redacted_text: str
    pii_found: list[str]
    intent: str | None
    confidence: float | None
    language: str
    route_reason: str | None
    retrieved: list[dict]
    tool_log: list[dict]  # {"name", "args", "result"} in call order
    reply: str
    handoff: bool
    handoff_reason: str | None
    flags: list[str]
    cost_usd: float
    models_used: list[str]
    cache_hit: bool
    error: str | None


def fresh_turn(text: str) -> dict[str, Any]:
    return {"user_text": text, "redacted_text": "", "pii_found": [], "intent": None,
            "confidence": None, "language": "en", "route_reason": None, "retrieved": [],
            "tool_log": [], "reply": "", "handoff": False, "handoff_reason": None, "flags": [],
            "cost_usd": 0.0, "models_used": [], "cache_hit": False, "error": None}
