"""Tool inputs as the model sees them.

No tool takes a borrower id. The borrower comes from the channel (the phone number the
message arrived from) and is injected by the harness, so no prompt can make the agent
read someone else's account.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class GetLoanAccount(BaseModel):
    """Look up the borrower's loans: EMI amount, next due date, outstanding principal,
    overdue amount and status. Use for any question about due dates or the loan itself."""

    loan_id: str | None = Field(None, description="Only if the borrower has several loans "
                                                  "and named one, e.g. LN0000041.")


class GetPaymentStatus(BaseModel):
    """Recent EMI payments for a loan: due date, amount, status (paid, failed, pending),
    payment mode and the failure reason when a payment failed."""

    loan_id: str | None = Field(None, description="Leave empty for the borrower's only loan.")
    last_n: int = Field(3, ge=1, le=12, description="How many recent payments to return.")


class CreateForeclosureQuote(BaseModel):
    """Calculate the exact amount to close a loan early on a given date. Always use this
    for any foreclosure or prepayment amount; never calculate it yourself."""

    loan_id: str | None = Field(None, description="Leave empty for the borrower's only loan.")
    foreclosure_date: date | None = Field(None, description="Defaults to today. At most 30 "
                                                            "days ahead.")


class UpdateContact(BaseModel):
    """Change the borrower's phone number or email. Call first without `otp`: this sends a
    one-time password to the number on file. Call again with the otp the borrower gives."""

    field: Literal["phone", "email"]
    new_value: str = Field(description="The new value exactly as the borrower wrote it, "
                                       "including placeholders like [PHONE_1].")
    otp: str | None = Field(None, description="The 6-digit code, only once the borrower has "
                                              "given it.")


class CreateZendeskTicket(BaseModel):
    """Open a support ticket for a human team to follow up. Use for complaints and for
    problems you cannot fix in the conversation. Returns the ticket number."""

    category: Literal["complaint", "collections_conduct", "payment_issue", "account_change",
                      "other"]
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    subject: str = Field(max_length=150)
    description: str = Field(description="What happened, what the borrower wants, and what "
                                         "you already checked.")


class HandoffToHuman(BaseModel):
    """Pass the conversation to a human agent. Use when the borrower asks for a person, is
    distressed or abusive, or when you cannot answer safely."""

    reason: str = Field(description="One line on why.")
    summary: str = Field(description="A summary for the human agent, so the borrower does "
                                     "not have to repeat themselves.")


TOOL_MODELS: dict[str, type[BaseModel]] = {
    "get_loan_account": GetLoanAccount,
    "get_payment_status": GetPaymentStatus,
    "create_foreclosure_quote": CreateForeclosureQuote,
    "update_contact": UpdateContact,
    "create_zendesk_ticket": CreateZendeskTicket,
    "handoff_to_human": HandoffToHuman,
}


def tool_specs(names: list[str]) -> list[dict]:
    """OpenAI-format tool definitions, which OpenRouter passes on to every provider."""
    specs = []
    for name in names:
        model = TOOL_MODELS[name]
        schema = model.model_json_schema()
        schema.pop("title", None)
        schema.pop("description", None)
        specs.append({"type": "function", "function": {
            "name": name, "description": " ".join(model.__doc__.split()), "parameters": schema,
        }})
    return specs
