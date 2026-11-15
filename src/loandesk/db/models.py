"""Mock core banking schema, plus the agent's own tables (audit, tickets, OTP, turns)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC, so SQLite and Postgres store the same thing."""
    return datetime.now(UTC).replace(tzinfo=None)


class Borrower(Base):
    __tablename__ = "borrowers"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # UDN000001
    name: Mapped[str] = mapped_column(String(120))
    # Only a salted hash of the phone number is stored, plus the last 4 digits for display.
    phone_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    phone_last4: Mapped[str] = mapped_column(String(4))
    email: Mapped[str | None] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(8), default="en")
    branch: Mapped[str] = mapped_column(String(60))
    group_name: Mapped[str | None] = mapped_column(String(80))  # joint liability group

    loans: Mapped[list[Loan]] = relationship(back_populates="borrower")


class Loan(Base):
    __tablename__ = "loans"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # LN0000001
    borrower_id: Mapped[str] = mapped_column(ForeignKey("borrowers.id"), index=True)
    product: Mapped[str] = mapped_column(String(40))
    principal: Mapped[float] = mapped_column(Float)
    annual_rate: Mapped[float] = mapped_column(Float)  # 0.235 = 23.5% reducing balance
    tenure_months: Mapped[int] = mapped_column(Integer)
    emi_amount: Mapped[float] = mapped_column(Float)
    disbursed_on: Mapped[date] = mapped_column(Date)
    next_due_date: Mapped[date | None] = mapped_column(Date)
    last_paid_due_date: Mapped[date | None] = mapped_column(Date)
    outstanding_principal: Mapped[float] = mapped_column(Float)
    emis_paid: Mapped[int] = mapped_column(Integer, default=0)
    overdue_amount: Mapped[float] = mapped_column(Float, default=0.0)
    days_past_due: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))  # active | overdue | closed

    borrower: Mapped[Borrower] = relationship(back_populates="loans")
    payments: Mapped[list[Payment]] = relationship(back_populates="loan",
                                                   order_by="Payment.due_date.desc()")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    loan_id: Mapped[str] = mapped_column(ForeignKey("loans.id"), index=True)
    due_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16))  # paid | failed | pending
    mode: Mapped[str] = mapped_column(String(20))  # nach | upi | cash
    attempted_on: Mapped[date | None] = mapped_column(Date)
    failure_reason: Mapped[str | None] = mapped_column(String(120))
    external_ref: Mapped[str | None] = mapped_column(String(64), unique=True)

    loan: Mapped[Loan] = relationship(back_populates="payments")


class WebhookEvent(Base):
    """One row per payment-gateway event id. The primary key is the idempotency check."""

    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    payload: Mapped[dict] = mapped_column(JSON)


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    borrower_id: Mapped[str] = mapped_column(ForeignKey("borrowers.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(40))  # e.g. "update_contact:phone"
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Ticket(Base):
    """Local mirror of every Zendesk ticket, used as the borrower's long-term memory."""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(40))
    borrower_id: Mapped[str | None] = mapped_column(String(16), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    priority: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    borrower_id: Mapped[str | None] = mapped_column(String(16))
    tool: Mapped[str] = mapped_column(String(40))
    args: Mapped[dict] = mapped_column(JSON)  # redacted
    ok: Mapped[bool] = mapped_column(Boolean)
    result_summary: Mapped[str] = mapped_column(Text)


class ConversationTurn(Base):
    """One row per agent turn. The admin dashboard computes its KPIs from this table."""

    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    intent: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)
    handoff: Mapped[bool] = mapped_column(Boolean)
    handoff_reason: Mapped[str | None] = mapped_column(String(200))
    models: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(16), default="chat")  # chat | eval | simulate
