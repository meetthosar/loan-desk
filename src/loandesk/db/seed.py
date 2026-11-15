"""1,000 synthetic borrowers. Deterministic: the same seed and as-of date give the same data.

Phone numbers are +91 90000 0NNNN, where NNNN is the borrower number. The first ten
borrowers are hand-built scenarios that the eval cases rely on; see SCENARIOS.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from faker import Faker

from loandesk.db.models import Borrower, Loan, Payment, Ticket
from loandesk.db.session import Database, hash_phone
from loandesk.domain.loan_math import emi, outstanding_after

N_BORROWERS = 1000
BRANCHES = ["Nashik", "Pune Rural", "Aurangabad", "Indore", "Bhopal", "Nagpur", "Satara"]
FAILURE_REASONS = ["Insufficient funds", "NACH mandate expired", "Account frozen",
                   "UPI timeout"]


def phone_for(n: int) -> str:
    return f"+9190000{n:05d}"


def add_months(d: date, months: int, day: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(day, 28))


def _loan(loan_id: str, borrower_id: str, *, principal: float, rate: float, tenure: int,
          disbursed: date, as_of: date, missed: int = 0, pending: bool = False,
          rng: random.Random) -> tuple[Loan, list[Payment]]:
    day = min(disbursed.day, 28)
    dues = [add_months(disbursed, k, day) for k in range(1, tenure + 1)]
    elapsed = sum(1 for d in dues if d <= as_of)
    paid = max(elapsed - missed - (1 if pending else 0), 0)
    instalment = emi(principal, rate, tenure)
    closed = paid >= tenure
    first_unpaid = None if closed else dues[paid]
    overdue = 0.0 if closed else round(missed * instalment, 2)
    dpd = (as_of - first_unpaid).days if missed and first_unpaid else 0

    payments = [Payment(loan_id=loan_id, due_date=dues[k], amount=instalment, status="paid",
                        mode=rng.choice(["nach", "nach", "upi", "cash"]),
                        attempted_on=dues[k], external_ref=f"PAY-{loan_id}-{k + 1}")
                for k in range(paid)]
    for k in range(paid, paid + missed):
        payments.append(Payment(loan_id=loan_id, due_date=dues[k], amount=instalment,
                                status="failed", mode="nach", attempted_on=dues[k],
                                failure_reason=rng.choice(FAILURE_REASONS),
                                external_ref=f"PAY-{loan_id}-{k + 1}"))
    if pending:
        k = paid + missed
        payments.append(Payment(loan_id=loan_id, due_date=dues[k], amount=instalment,
                                status="pending", mode="upi", attempted_on=as_of,
                                external_ref=f"PAY-{loan_id}-{k + 1}"))

    loan = Loan(
        id=loan_id, borrower_id=borrower_id, product="Joint liability group loan",
        principal=principal, annual_rate=rate, tenure_months=tenure, emi_amount=instalment,
        disbursed_on=disbursed, next_due_date=first_unpaid,
        last_paid_due_date=dues[paid - 1] if paid else None,
        outstanding_principal=outstanding_after(principal, rate, tenure, paid),
        emis_paid=paid, overdue_amount=overdue, days_past_due=dpd,
        status="closed" if closed else ("overdue" if missed else "active"),
    )
    return loan, payments


def _borrower(n: int, name: str, salt: str, *, language: str = "en", email: str | None = None,
              branch: str = "Nashik") -> Borrower:
    phone = phone_for(n)
    return Borrower(id=f"UDN{n:06d}", name=name, phone_hash=hash_phone(phone, salt),
                    phone_last4=phone[-4:], email=email, language=language, branch=branch,
                    group_name=f"{branch} Mahila Group {n % 40 + 1}")


SCENARIOS = {
    1: "Asha Patil: current, next EMI in about 5 days",
    2: "Ravi Kumar: latest NACH debit failed 3 days ago",
    3: "Lakshmi Devi: two missed EMIs, overdue",
    4: "Imran Shaikh: two active loans",
    5: "Sunita Yadav: loan fully repaid and closed",
    6: "Priya Nair: current, has an email on file",
    7: "Mohan Das: current, one open complaint ticket",
    8: "Fatima Begum: UPI payment pending today",
    9: "Deepak Verma: current, prefers Hindi",
    10: "Kavita Joshi: disbursed 10 days ago, first EMI not yet due",
}


def seed(db: Database, as_of: date, salt: str, n_borrowers: int = N_BORROWERS) -> None:
    rng = random.Random(42)
    fake = Faker("en_IN")
    Faker.seed(42)
    borrowers: list[Borrower] = []
    loans: list[Loan] = []
    payments: list[Payment] = []

    def add(b: Borrower, *loan_specs: dict) -> None:
        borrowers.append(b)
        for i, spec in enumerate(loan_specs):
            loan, pays = _loan(f"LN{int(b.id[3:]):06d}{i + 1}", b.id, as_of=as_of, rng=rng, **spec)
            loans.append(loan)
            payments.extend(pays)

    def months_ago(m: int, plus_days: int = 0) -> date:
        d = as_of + timedelta(days=plus_days)
        return add_months(d, -m, d.day)

    add(_borrower(1, "Asha Patil", salt),
        dict(principal=40000, rate=0.235, tenure=24, disbursed=months_ago(7, plus_days=5)))
    add(_borrower(2, "Ravi Kumar", salt, branch="Pune Rural"),
        dict(principal=30000, rate=0.24, tenure=18, disbursed=months_ago(5, plus_days=-3),
             missed=1))
    add(_borrower(3, "Lakshmi Devi", salt, branch="Satara"),
        dict(principal=50000, rate=0.24, tenure=24, disbursed=months_ago(9, plus_days=-12),
             missed=2))
    add(_borrower(4, "Imran Shaikh", salt, branch="Aurangabad"),
        dict(principal=35000, rate=0.235, tenure=24, disbursed=months_ago(14, plus_days=8)),
        dict(principal=15000, rate=0.25, tenure=12, disbursed=months_ago(3, plus_days=15)))
    add(_borrower(5, "Sunita Yadav", salt, branch="Indore"),
        dict(principal=20000, rate=0.24, tenure=12, disbursed=months_ago(13, plus_days=-2)))
    add(_borrower(6, "Priya Nair", salt, email="priya.nair@example.com"),
        dict(principal=45000, rate=0.23, tenure=24, disbursed=months_ago(4, plus_days=10)))
    add(_borrower(7, "Mohan Das", salt, branch="Nagpur"),
        dict(principal=25000, rate=0.245, tenure=18, disbursed=months_ago(6, plus_days=12)))
    add(_borrower(8, "Fatima Begum", salt, branch="Bhopal"),
        dict(principal=30000, rate=0.24, tenure=24, disbursed=months_ago(8), pending=True))
    add(_borrower(9, "Deepak Verma", salt, language="hi", branch="Indore"),
        dict(principal=35000, rate=0.24, tenure=24, disbursed=months_ago(10, plus_days=6)))
    add(_borrower(10, "Kavita Joshi", salt, branch="Nashik"),
        dict(principal=30000, rate=0.235, tenure=12, disbursed=as_of - timedelta(days=10)))

    for n in range(11, n_borrowers + 1):
        b = _borrower(n, fake.name(), salt, language="hi" if rng.random() < 0.15 else "en",
                      branch=rng.choice(BRANCHES),
                      email=fake.email() if rng.random() < 0.3 else None)
        specs = []
        for _ in range(2 if rng.random() < 0.1 else 1):
            tenure = rng.choice([12, 18, 24])
            roll = rng.random()
            age = tenure + 1 if roll < 0.05 else rng.randint(1, tenure - 1)
            specs.append(dict(principal=float(rng.randrange(15000, 65000, 5000)),
                              rate=rng.choice([0.22, 0.23, 0.235, 0.24, 0.25]), tenure=tenure,
                              disbursed=months_ago(age, plus_days=rng.randint(-14, 14)),
                              missed=rng.randint(1, 3) if 0.05 <= roll < 0.15 else 0))
        add(b, *specs)

    with db.session() as s:
        s.add_all(borrowers)
        s.flush()
        s.add_all(loans)
        s.flush()
        s.add_all(payments)
        s.add(Ticket(external_id="mock-1001", borrower_id="UDN000007",
                     subject="Field agent visited after 8 pm",
                     description="Borrower reports a field agent came to the house at 9 pm "
                                 "asking for payment.",
                     category="collections_conduct", priority="high", status="open",
                     created_at=datetime.combine(as_of - timedelta(days=10), datetime.min.time())))
