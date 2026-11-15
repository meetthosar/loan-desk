"""Dashboard KPIs, computed from the conversation_turns table."""

from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np
from sqlalchemy import select

from loandesk.db.models import ConversationTurn
from loandesk.db.session import Database


def compute_kpis(db: Database, source: str | None = None) -> dict:
    with db.session() as s:
        q = select(ConversationTurn)
        if source:
            q = q.where(ConversationTurn.source == source)
        turns = s.scalars(q).all()
    if not turns:
        return {"conversations": 0, "turns": 0}

    by_conv: dict[str, list[ConversationTurn]] = defaultdict(list)
    for t in turns:
        by_conv[t.conversation_id].append(t)
    escalated = sum(1 for ts in by_conv.values() if any(t.handoff for t in ts))
    n = len(by_conv)
    latencies = np.array([t.latency_ms for t in turns])
    return {
        "conversations": n,
        "turns": len(turns),
        "containment_rate": round((n - escalated) / n, 4),
        "escalation_rate": round(escalated / n, 4),
        # Agent processing time per conversation, summed over its turns.
        "avg_handle_time_s": round(
            float(np.mean([sum(t.latency_ms for t in ts) for ts in by_conv.values()])) / 1000, 2),
        "cost_per_conversation_usd": round(sum(t.cost_usd for t in turns) / n, 5),
        "p50_latency_ms": round(float(np.percentile(latencies, 50)), 1),
        "p95_latency_ms": round(float(np.percentile(latencies, 95)), 1),
        "intents": dict(Counter(t.intent or "unknown" for t in turns).most_common()),
        "handoff_reasons": dict(Counter(t.handoff_reason.split(":")[0] for t in turns
                                        if t.handoff_reason).most_common()),
        "guardrail_flags": dict(Counter(f for t in turns for f in (t.flags or []))),
        "models": dict(Counter(t.models for t in turns)),
        "prompt_versions": dict(Counter(t.prompt_version for t in turns)),
    }
