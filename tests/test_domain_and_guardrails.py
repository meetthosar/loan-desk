from datetime import date

from loandesk.domain.loan_math import emi, foreclosure_quote, outstanding_after
from loandesk.guardrails.output_check import check_reply
from loandesk.guardrails.pii import Vault, redact
from loandesk.knowledge.chunking import load_policies
from loandesk.knowledge.retriever import POLICIES_DIR


def test_emi_and_amortisation():
    assert emi(40000, 0.235, 24) == 2104.87  # checked by hand: P·r·(1+r)^n / ((1+r)^n − 1)
    assert outstanding_after(40000, 0.235, 24, 0) == 40000
    assert outstanding_after(40000, 0.235, 24, 24) < 1  # fully repaid, up to rounding


def test_foreclosure_has_no_prepayment_charge_and_penal_only_on_overdue():
    q = foreclosure_quote(loan_id="LN1", outstanding_principal=20000, annual_rate=0.24,
                          interest_from=date(2026, 11, 1), overdue_amount=0, days_past_due=0,
                          on=date(2026, 11, 20))
    assert q.prepayment_charge == 0 and q.penal_charges == 0
    assert q.accrued_interest == round(20000 * 0.24 / 365 * 19, 2)
    assert q.total_payable == round(20000 + q.accrued_interest)
    assert q.valid_until == "2026-11-23"

    overdue = foreclosure_quote(loan_id="LN1", outstanding_principal=20000, annual_rate=0.24,
                                interest_from=date(2026, 10, 1), overdue_amount=2000,
                                days_past_due=20, on=date(2026, 11, 20))
    assert overdue.penal_charges == round(2000 * 0.24 * 20 / 365, 2)


def test_redaction_keeps_contact_details_in_vault_and_drops_aadhaar():
    vault = Vault()
    text, found = redact("Aadhaar 2345 6789 0123, new number 98765 43210, mail a.b@x.in", vault)
    assert "2345" not in text and "98765" not in text and "a.b@x.in" not in text
    assert set(found) == {"AADHAAR", "PHONE", "EMAIL"}
    assert vault.resolve("[PHONE_1]") == "98765 43210"
    assert vault.resolve("[AADHAAR_1]") == "[AADHAAR_1]"  # never stored
    assert vault.mask("updated to [PHONE_1]") == "updated to number ending 3210"


def test_output_check():
    assert "fee_waiver_promise" in check_reply("We will waive your late fee.", [], []).flags
    assert check_reply("I can't waive charges, but I can open a ticket.", [], []).ok
    assert check_reply("Maaf kijiye, aapki EMI ₹2,104.87 hai.", [{"emi": 2104.87}], []).ok
    flagged = check_reply("Pay ₹9,999 to close.", [{"total_payable": 20312.0}], [])
    assert flagged.flags == ["ungrounded_amount"]
    assert check_reply("Pay ₹20,312 to close.", [{"total_payable": 20312.0}], []).ok
    assert "pii_in_reply" in check_reply("Your Aadhaar 2345 6789 0123", [], []).flags


def test_policy_chunks_have_unique_ids_and_section_metadata():
    chunks = load_policies(POLICIES_DIR)
    assert len(chunks) >= 50
    assert len({c.id for c in chunks}) == len(chunks)
    assert all("#" in c.metadata["section_id"] for c in chunks)
