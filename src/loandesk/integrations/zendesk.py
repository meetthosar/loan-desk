"""Zendesk tickets. The real client is used when credentials are set, the local mock otherwise.

Both write a row to the local `tickets` table, which is what the agent reads as memory.
"""

from __future__ import annotations

import itertools
from typing import Protocol

import httpx

from loandesk.db.models import Ticket
from loandesk.db.session import Database


class TicketSystem(Protocol):
    name: str

    def create_ticket(self, *, borrower_id: str | None, subject: str, description: str,
                      category: str, priority: str) -> str: ...


def _mirror(db: Database, external_id: str, **fields) -> None:
    with db.session() as s:
        s.add(Ticket(external_id=external_id, **fields))


class MockZendesk:
    name = "mock"
    _ids = itertools.count(5000)

    def __init__(self, db: Database):
        self.db = db

    def create_ticket(self, *, borrower_id, subject, description, category, priority) -> str:
        external_id = f"mock-{next(self._ids)}"
        _mirror(self.db, external_id, borrower_id=borrower_id, subject=subject,
                description=description, category=category, priority=priority)
        return external_id


class Zendesk:
    name = "zendesk"

    def __init__(self, db: Database, subdomain: str, email: str, api_token: str):
        self.db = db
        self.http = httpx.Client(
            base_url=f"https://{subdomain}.zendesk.com/api/v2",
            auth=(f"{email}/token", api_token),
            timeout=20,
        )

    def create_ticket(self, *, borrower_id, subject, description, category, priority) -> str:
        resp = self.http.post("/tickets.json", json={"ticket": {
            "subject": subject,
            "comment": {"body": description, "public": False},
            "priority": priority,
            "tags": ["loandesk", category],
            "external_id": borrower_id,
        }})
        resp.raise_for_status()
        external_id = str(resp.json()["ticket"]["id"])
        _mirror(self.db, external_id, borrower_id=borrower_id, subject=subject,
                description=description, category=category, priority=priority)
        return external_id


def get_ticket_system(db: Database, subdomain: str, email: str, token: str) -> TicketSystem:
    if subdomain and email and token:
        return Zendesk(db, subdomain, email, token)
    return MockZendesk(db)
