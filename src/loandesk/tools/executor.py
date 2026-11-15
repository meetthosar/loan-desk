"""Runs tool calls: allowlist check, input validation, the tool itself, an audit row.

Every outcome, including a refusal, is returned to the model as a result with a clear
error message it can act on, rather than raised.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from loandesk.db.models import AuditLog, Borrower, Loan, Payment
from loandesk.db.session import Database, hash_phone
from loandesk.domain.loan_math import foreclosure_quote
from loandesk.guardrails.pii import Vault, redact
from loandesk.integrations.otp import OtpService
from loandesk.integrations.zendesk import TicketSystem
from loandesk.tools import schemas as s
from loandesk.tracing import Tracer

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")
PHONE_RE = re.compile(r"^(?:\+?91)?[6-9]\d{9}$")


@dataclass
class ToolContext:
    conversation_id: str
    borrower_id: str | None
    vault: Vault
    as_of: date


class ToolError(Exception):
    pass


class ToolExecutor:
    def __init__(self, db: Database, tickets: TicketSystem, otp: OtpService, tracer: Tracer,
                 phone_salt: str):
        self.db = db
        self.tickets = tickets
        self.otp = otp
        self.tracer = tracer
        self.phone_salt = phone_salt

    def execute(self, name: str, raw_args: str | dict, allowed: list[str],
                ctx: ToolContext) -> dict[str, Any]:
        start = time.perf_counter()
        args: dict = {}
        try:
            if name not in allowed:
                raise ToolError(f"Tool {name} is not available for this request. "
                                f"Available: {', '.join(allowed) or 'none'}.")
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            params = s.TOOL_MODELS[name].model_validate(args)
            if ctx.borrower_id is None and name not in ("handoff_to_human",
                                                        "create_zendesk_ticket"):
                raise ToolError("This phone number is not registered with Udaan, so no "
                                "account can be looked up. Offer a handoff to a human.")
            result = getattr(self, name)(params, ctx)
            ok = True
        except ValidationError as e:
            result, ok = {"error": f"Invalid input: {e.errors(include_url=False)}"}, False
        except (ToolError, json.JSONDecodeError) as e:
            result, ok = {"error": str(e)}, False

        latency_ms = (time.perf_counter() - start) * 1000
        safe_args = json.loads(redact(json.dumps(args, default=str), Vault())[0])
        with self.db.session() as session:
            session.add(AuditLog(conversation_id=ctx.conversation_id, borrower_id=ctx.borrower_id,
                                 tool=name, args=safe_args, ok=ok,
                                 result_summary=json.dumps(result, default=str)[:2000]))
        self.tracer.span("tool", tool=name, ok=ok, latency_ms=round(latency_ms, 1))
        return result

    # --- helpers -------------------------------------------------------------------------

    def find_borrower(self, phone: str) -> Borrower | None:
        with self.db.session() as session:
            return session.scalars(
                select(Borrower).where(Borrower.phone_hash == hash_phone(phone, self.phone_salt))
            ).first()

    def _loan(self, session, ctx: ToolContext, loan_id: str | None,
              include_closed: bool = False) -> Loan:
        loans = session.scalars(select(Loan).where(Loan.borrower_id == ctx.borrower_id)).all()
        if loan_id:
            match = [ln for ln in loans if ln.id == loan_id.strip().upper()]
            if not match:
                raise ToolError(f"No loan {loan_id} for this borrower. Their loans: "
                                f"{', '.join(ln.id for ln in loans) or 'none'}.")
            return match[0]
        open_loans = [ln for ln in loans if include_closed or ln.status != "closed"]
        if not open_loans:
            raise ToolError("The borrower has no open loan; all loans are closed.")
        if len(open_loans) > 1:
            raise ToolError("The borrower has several open loans: "
                            + ", ".join(f"{ln.id} (EMI ₹{ln.emi_amount:,.2f})" for ln in open_loans)
                            + ". Ask which one, then call again with loan_id.")
        return open_loans[0]

    @staticmethod
    def _loan_dict(ln: Loan) -> dict:
        return {
            "loan_id": ln.id, "status": ln.status, "product": ln.product,
            "principal": ln.principal, "annual_interest_rate_pct": round(ln.annual_rate * 100, 2),
            "tenure_months": ln.tenure_months, "emis_paid": ln.emis_paid,
            "emi_amount": ln.emi_amount,
            "next_due_date": ln.next_due_date.isoformat() if ln.next_due_date else None,
            "outstanding_principal": ln.outstanding_principal,
            "overdue_amount": ln.overdue_amount, "days_past_due": ln.days_past_due,
            "disbursed_on": ln.disbursed_on.isoformat(),
        }

    # --- the six tools -------------------------------------------------------------------

    def get_loan_account(self, p: s.GetLoanAccount, ctx: ToolContext) -> dict:
        with self.db.session() as session:
            if p.loan_id:
                return {"loans": [self._loan_dict(self._loan(session, ctx, p.loan_id))]}
            loans = session.scalars(select(Loan).where(Loan.borrower_id == ctx.borrower_id)).all()
            return {"today": ctx.as_of.isoformat(), "loans": [self._loan_dict(ln) for ln in loans]}

    def get_payment_status(self, p: s.GetPaymentStatus, ctx: ToolContext) -> dict:
        with self.db.session() as session:
            loan = self._loan(session, ctx, p.loan_id, include_closed=True)
            rows = session.scalars(select(Payment).where(Payment.loan_id == loan.id)
                                   .order_by(Payment.due_date.desc()).limit(p.last_n)).all()
            return {"loan_id": loan.id, "loan_status": loan.status,
                    "overdue_amount": loan.overdue_amount, "days_past_due": loan.days_past_due,
                    "payments": [{"due_date": r.due_date.isoformat(), "amount": r.amount,
                                  "status": r.status, "mode": r.mode,
                                  "attempted_on": r.attempted_on.isoformat()
                                  if r.attempted_on else None,
                                  "failure_reason": r.failure_reason} for r in rows]}

    def create_foreclosure_quote(self, p: s.CreateForeclosureQuote, ctx: ToolContext) -> dict:
        on = p.foreclosure_date or ctx.as_of
        if not ctx.as_of <= on <= ctx.as_of + timedelta(days=30):
            raise ToolError("foreclosure_date must be between today and 30 days from today.")
        with self.db.session() as session:
            loan = self._loan(session, ctx, p.loan_id)
            quote = foreclosure_quote(
                loan_id=loan.id, outstanding_principal=loan.outstanding_principal,
                annual_rate=loan.annual_rate,
                interest_from=loan.last_paid_due_date or loan.disbursed_on,
                overdue_amount=loan.overdue_amount, days_past_due=loan.days_past_due, on=on,
            )
            return quote.as_dict()

    def update_contact(self, p: s.UpdateContact, ctx: ToolContext) -> dict:
        new_value = ctx.vault.resolve(p.new_value).strip()
        if p.field == "phone":
            digits = re.sub(r"[\s-]", "", new_value)
            if not PHONE_RE.match(digits):
                raise ToolError("That is not a valid Indian mobile number.")
            new_value = digits[-10:]
        elif not EMAIL_RE.match(new_value):
            raise ToolError("That is not a valid email address.")

        purpose = f"update_contact:{p.field}:{new_value}"
        if not p.otp:
            code = self.otp.issue(ctx.borrower_id, purpose)
            self.tracer.span("otp_sent", borrower=ctx.borrower_id,
                             code=code if self.otp.mode == "fixed" else "***")
            return {"status": "otp_sent", "sent_to": "the registered mobile number",
                    "next_step": "Ask the borrower for the 6-digit code, then call again "
                                 "with otp."}
        ok, reason = self.otp.verify(ctx.borrower_id, purpose, p.otp)
        if not ok:
            raise ToolError({"no_active_otp": "No code was sent for this change. Call without "
                                              "otp first.",
                             "otp_expired": "The code expired. Call without otp to send a "
                                            "new one.",
                             "too_many_attempts": "Too many wrong codes. Hand off to a human.",
                             "otp_incorrect": "The code is wrong. Ask the borrower to check "
                                              "it."}[reason])
        with self.db.session() as session:
            borrower = session.get(Borrower, ctx.borrower_id)
            if p.field == "phone":
                borrower.phone_hash = hash_phone(new_value, self.phone_salt)
                borrower.phone_last4 = new_value[-4:]
            else:
                borrower.email = new_value
        return {"status": "updated", "field": p.field,
                "masked": f"ending {new_value[-4:]}" if p.field == "phone"
                else f"{new_value[:1]}***@{new_value.partition('@')[2]}"}

    def create_zendesk_ticket(self, p: s.CreateZendeskTicket, ctx: ToolContext) -> dict:
        ticket_id = self.tickets.create_ticket(
            borrower_id=ctx.borrower_id, subject=p.subject,
            description=f"{p.description}\n\n(conversation {ctx.conversation_id})",
            category=p.category, priority=p.priority,
        )
        return {"ticket_id": ticket_id, "status": "open",
                "response_time": "within 2 working days" if p.priority in ("low", "normal")
                else "within 1 working day"}

    def handoff_to_human(self, p: s.HandoffToHuman, ctx: ToolContext) -> dict:
        ticket_id = self.tickets.create_ticket(
            borrower_id=ctx.borrower_id, subject=f"Handoff: {p.reason}"[:150],
            description=f"{p.summary}\n\n(conversation {ctx.conversation_id})",
            category="other", priority="high",
        )
        return {"status": "handed_off", "ticket_id": ticket_id,
                "note": "A human agent will reply in this chat within working hours "
                        "(Mon–Sat, 9:00–18:00)."}
