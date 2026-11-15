"""Intents and the tools each one may call (the tool allowlist, OWASP LLM06)."""

from __future__ import annotations

from enum import StrEnum


class Intent(StrEnum):
    EMI_DUE_DATE = "emi_due_date"
    FAILED_PAYMENT = "failed_payment"
    FORECLOSURE_QUOTE = "foreclosure_quote"
    CONTACT_UPDATE = "contact_update"
    COMPLAINT = "complaint"
    # Supporting intents: general policy questions (no account data) and everything else.
    POLICY_QUESTION = "policy_question"
    OUT_OF_SCOPE = "out_of_scope"


CORE_INTENTS = [Intent.EMI_DUE_DATE, Intent.FAILED_PAYMENT, Intent.FORECLOSURE_QUOTE,
                Intent.CONTACT_UPDATE, Intent.COMPLAINT]

TOOL_ALLOWLIST: dict[Intent, list[str]] = {
    Intent.EMI_DUE_DATE: ["get_loan_account", "handoff_to_human"],
    Intent.FAILED_PAYMENT: ["get_payment_status", "get_loan_account", "create_zendesk_ticket",
                            "handoff_to_human"],
    Intent.FORECLOSURE_QUOTE: ["get_loan_account", "create_foreclosure_quote", "handoff_to_human"],
    Intent.CONTACT_UPDATE: ["update_contact", "handoff_to_human"],
    Intent.COMPLAINT: ["get_loan_account", "create_zendesk_ticket", "handoff_to_human"],
    Intent.POLICY_QUESTION: ["handoff_to_human"],
    Intent.OUT_OF_SCOPE: [],
}

# Intents whose reply depends only on the policy pages, so it can be cached (layer 5).
CACHEABLE_INTENTS = {Intent.POLICY_QUESTION}
