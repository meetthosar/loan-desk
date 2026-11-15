"""One-time passwords for contact changes. Sending is mocked: there is no SMS provider.

In `fixed` mode (dev and evals) the code is always 123456. In `random` mode it is a
random 6-digit code, logged to the trace file in place of an SMS.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select

from loandesk.db.models import OtpChallenge, utcnow
from loandesk.db.session import Database

FIXED_CODE = "123456"
TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 3


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class OtpService:
    def __init__(self, db: Database, mode: str = "fixed"):
        self.db = db
        self.mode = mode

    def issue(self, borrower_id: str, purpose: str) -> str:
        code = FIXED_CODE if self.mode == "fixed" else f"{secrets.randbelow(10**6):06d}"
        with self.db.session() as s:
            s.add(OtpChallenge(borrower_id=borrower_id, purpose=purpose, code_hash=_hash(code),
                               expires_at=utcnow() + TTL))
        return code

    def verify(self, borrower_id: str, purpose: str, code: str) -> tuple[bool, str]:
        with self.db.session() as s:
            challenge = s.scalars(
                select(OtpChallenge)
                .where(OtpChallenge.borrower_id == borrower_id, OtpChallenge.purpose == purpose,
                       OtpChallenge.used.is_(False))
                .order_by(OtpChallenge.id.desc())
            ).first()
            if challenge is None:
                return False, "no_active_otp"
            if challenge.expires_at < utcnow():
                return False, "otp_expired"
            if challenge.attempts >= MAX_ATTEMPTS:
                return False, "too_many_attempts"
            challenge.attempts += 1
            if not secrets.compare_digest(challenge.code_hash, _hash(code.strip())):
                return False, "otp_incorrect"
            challenge.used = True
            return True, "ok"
