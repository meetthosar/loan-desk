"""HTTP API. /chat stands in for the WhatsApp channel: the phone number plays the role of
the sender id WhatsApp would provide."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from loandesk.api.auth import require_admin
from loandesk.api.kpis import compute_kpis
from loandesk.config import get_settings
from loandesk.integrations.payments import PaymentEvent, SignatureError, handle_event, verify
from loandesk.llm import BudgetExceeded
from loandesk.wiring import App, build_app

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.loandesk = build_app(get_settings())
    yield
    app.state.loandesk.close()


api = FastAPI(title="LoanDesk", version="0.1.0", lifespan=lifespan)


def get_app(request: Request) -> App:
    return request.app.state.loandesk


class ChatIn(BaseModel):
    phone: str = Field(examples=["+919000000001"])
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


@api.get("/health")
def health(app: App = Depends(get_app)) -> dict:
    s = app.settings
    return {"status": "ok", "prompt_version": s.prompt_version, "models": s.models["roles"],
            "vector_store": s.vector_store, "budget_remaining_usd": round(app.ledger.remaining, 4)}


@api.post("/chat")
async def chat(body: ChatIn, app: App = Depends(get_app)) -> dict:
    try:
        result = await run_in_threadpool(app.agent.turn, body.phone, body.message,
                                         conversation_id=body.conversation_id)
    except BudgetExceeded as e:
        raise HTTPException(503, str(e)) from e
    return {"conversation_id": result.conversation_id, "reply": result.reply,
            "intent": result.intent, "confidence": result.confidence,
            "handoff": result.handoff, "handoff_reason": result.handoff_reason,
            "tools": [t["name"] for t in result.tool_calls], "flags": result.flags,
            "latency_ms": result.latency_ms, "cost_usd": result.cost_usd}


@api.post("/webhooks/payment")
async def payment_webhook(request: Request, app: App = Depends(get_app)) -> dict:
    body = await request.body()
    try:
        verify(app.settings.webhook_secret, body, request.headers.get("x-signature"))
    except SignatureError as e:
        raise HTTPException(400, f"Bad signature: {e}") from e
    event = PaymentEvent.model_validate_json(body)
    return await run_in_threadpool(handle_event, app.db, app.agent.executor.tickets, event)


@api.get("/admin/kpis", dependencies=[Depends(require_admin)])
def kpis(source: str | None = None, app: App = Depends(get_app)) -> dict:
    return compute_kpis(app.db, source) | {"spend": app.ledger.summary()}


@api.get("/admin", response_class=HTMLResponse)
def dashboard() -> str:
    # The page holds no data; it calls /admin/kpis with the token the admin pastes in.
    return (STATIC / "dashboard.html").read_text()
