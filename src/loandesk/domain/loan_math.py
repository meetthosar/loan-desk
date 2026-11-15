"""Loan arithmetic. These numbers come from code, never from the model.

The rules match data/policies/foreclosure.md and fee-schedule.md:
- interest is on a reducing balance, accrued daily on a 365-day year
- there is no prepayment or foreclosure charge (RBI microfinance directions, 2022)
- penal charges are simple interest at 24% a year on the overdue amount only, never compounded
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

PENAL_RATE = 0.24
QUOTE_VALID_DAYS = 3


def emi(principal: float, annual_rate: float, months: int) -> float:
    r = annual_rate / 12
    if r == 0:
        return round(principal / months, 2)
    factor = (1 + r) ** months
    return round(principal * r * factor / (factor - 1), 2)


def outstanding_after(principal: float, annual_rate: float, months: int, paid: int) -> float:
    r = annual_rate / 12
    instalment = emi(principal, annual_rate, months)
    if r == 0:
        return round(max(principal - instalment * paid, 0.0), 2)
    factor = (1 + r) ** paid
    return round(max(principal * factor - instalment * (factor - 1) / r, 0.0), 2)


@dataclass
class ForeclosureQuote:
    loan_id: str
    foreclosure_date: str
    outstanding_principal: float
    accrued_interest: float
    interest_days: int
    penal_charges: float
    prepayment_charge: float
    total_payable: float
    per_day_interest: float
    valid_until: str

    def as_dict(self) -> dict:
        return asdict(self)


def foreclosure_quote(*, loan_id: str, outstanding_principal: float, annual_rate: float,
                      interest_from: date, overdue_amount: float, days_past_due: int,
                      on: date) -> ForeclosureQuote:
    days = max((on - interest_from).days, 0)
    per_day = outstanding_principal * annual_rate / 365
    accrued = round(per_day * days, 2)
    penal = round(overdue_amount * PENAL_RATE * days_past_due / 365, 2)
    total = round(outstanding_principal + accrued + penal)
    return ForeclosureQuote(
        loan_id=loan_id,
        foreclosure_date=on.isoformat(),
        outstanding_principal=round(outstanding_principal, 2),
        accrued_interest=accrued,
        interest_days=days,
        penal_charges=penal,
        prepayment_charge=0.0,
        total_payable=float(total),
        per_day_interest=round(per_day, 2),
        valid_until=(on + timedelta(days=QUOTE_VALID_DAYS)).isoformat(),
    )
