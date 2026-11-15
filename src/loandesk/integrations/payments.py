"""Payment gateway webhook: signature check, replay window, idempotency.

The gateway signs `"{timestamp}.{raw body}"` with HMAC-SHA256 and sends
`X-Signature: t=<timestamp>,v1=<hex digest>` (the same scheme Stripe uses).
An event id that was already processed returns 200 without doing anything again.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import date

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from loandesk.db.models import Loan, Payment, WebhookEvent
from loandesk.db.session import Database
from loandesk.integrations.zendesk import TicketSystem

TOLERANCE_S = 300


class SignatureError(Exception):
    pass


class PaymentEvent(BaseModel):
    event_id: str
    type: str  # payment.failed | payment.succeeded
    loan_id: str
    amount: float
    due_date: date
    mode: str = "nach"
    reason: str | None = None


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    ts = int(timestamp or time.time())
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def verify(secret: str, body: bytes, header: str | None, now: float | None = None) -> None:
    if not header:
        raise SignatureError("missing signature")
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, given = int(parts["t"]), parts["v1"]
    except (KeyError, ValueError) as e:
        raise SignatureError("malformed signature header") from e
    if abs((now or time.time()) - ts) > TOLERANCE_S:
        raise SignatureError("timestamp outside the 5-minute window")
    expected = sign(secret, body, ts).split("v1=")[1]
    if not hmac.compare_digest(expected, given):
        raise SignatureError("signature mismatch")


def handle_event(db: Database, tickets: TicketSystem, event: PaymentEvent) -> dict:
    """Recording the event id and updating the payment commit together, so a crash
    cannot leave an event marked as seen but not applied."""
    try:
        with db.session() as s:
            s.add(WebhookEvent(event_id=event.event_id, event_type=event.type,
                               payload=event.model_dump(mode="json")))
            s.flush()  # raises IntegrityError here for a duplicate event id
            loan = s.get(Loan, event.loan_id)
            if loan is None:
                return {"status": "ignored", "reason": "unknown loan"}
            payment = s.scalars(select(Payment).where(Payment.loan_id == loan.id,
                                                      Payment.due_date == event.due_date)).first()
            if payment is None:
                payment = Payment(loan_id=loan.id, due_date=event.due_date, amount=event.amount,
                                  mode=event.mode, status="pending")
                s.add(payment)
            payment.attempted_on = date.today()
            payment.mode = event.mode
            if event.type == "payment.succeeded":
                payment.status, payment.failure_reason = "paid", None
                return {"status": "processed", "event_id": event.event_id}
            payment.status, payment.failure_reason = "failed", event.reason
            borrower_id = loan.borrower_id
            summary = (f"EMI payment of ₹{event.amount:,.2f} for loan {loan.id}, due "
                       f"{event.due_date:%d %b %Y}, failed via {event.mode.upper()}: "
                       f"{event.reason or 'no reason given'}. Loan status: {loan.status}, "
                       f"overdue ₹{loan.overdue_amount:,.2f}, {loan.days_past_due} days past "
                       f"due. Gateway event {event.event_id}.")
    except IntegrityError:
        return {"status": "duplicate", "event_id": event.event_id}

    ticket_id = tickets.create_ticket(
        borrower_id=borrower_id, subject=f"Failed EMI payment on {event.loan_id}",
        description=summary, category="payment_issue", priority="normal",
    )
    return {"status": "processed", "event_id": event.event_id, "ticket_id": ticket_id}
