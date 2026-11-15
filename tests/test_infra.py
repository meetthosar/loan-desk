import json
import time

import pytest
from fastapi.testclient import TestClient

from loandesk.api.main import api
from loandesk.integrations.payments import SignatureError, sign, verify
from loandesk.llm.budget import BudgetExceeded, Ledger
from loandesk.vectorstores.base import Chunk
from loandesk.vectorstores.chroma_store import ChromaStore
from loandesk.vectorstores.qdrant_store import QdrantStore


def test_ledger_refuses_calls_over_the_cap(tmp_path):
    ledger = Ledger(tmp_path / "spend.json", cap_usd=1.0)
    ledger.record("m", 0.6, "eval")
    ledger.check(0.3)
    with pytest.raises(BudgetExceeded):
        ledger.check(0.5)
    assert Ledger(tmp_path / "spend.json", 1.0).summary()["by_tag"] == {"eval": 0.6}


def test_webhook_signature():
    body = b'{"event_id": "e1"}'
    verify("s3cret", body, sign("s3cret", body))
    with pytest.raises(SignatureError):
        verify("s3cret", body, sign("wrong", body))
    with pytest.raises(SignatureError):
        verify("s3cret", body, sign("s3cret", body, int(time.time()) - 600))


@pytest.mark.parametrize("make_store", [lambda: ChromaStore("memory", "contract"),
                                        lambda: QdrantStore("memory", "contract")])
def test_vector_store_contract(make_store):
    store = make_store()
    store.reset(3)
    chunks = [Chunk(id="a#1-0", text="alpha", metadata={"doc": "a", "section_id": "a#1"}),
              Chunk(id="b#1-0", text="beta", metadata={"doc": "b", "section_id": "b#1"})]
    store.upsert(chunks, [[1, 0, 0], [0, 1, 0]])
    assert store.count() == 2
    hits = store.search([0.9, 0.1, 0], k=2)
    assert hits[0].id == "a#1-0" and hits[0].score > hits[1].score
    assert hits[0].metadata["section_id"] == "a#1" and hits[0].text == "alpha"
    assert [h.id for h in store.search([0.9, 0.1, 0], k=2, filters={"doc": "b"})] == ["b#1-0"]
    store.close()


@pytest.fixture
def client(make_app, monkeypatch):
    app = make_app()
    api.state.loandesk = app
    return TestClient(api), app


def test_chat_endpoint(client):
    c, _ = client
    r = c.post("/chat", json={"phone": "+919000000001", "message": "When is my EMI due?"})
    assert r.status_code == 200 and r.json()["tools"] == ["get_loan_account"]


def test_payment_webhook_is_idempotent(client):
    c, app = client
    body = json.dumps({"event_id": "evt_1", "type": "payment.failed", "loan_id": "LN0000011",
                       "amount": 1500, "due_date": "2026-11-20",
                       "reason": "Insufficient funds"}).encode()
    headers = {"x-signature": sign(app.settings.webhook_secret, body),
               "content-type": "application/json"}
    first = c.post("/webhooks/payment", content=body, headers=headers).json()
    assert first["status"] == "processed" and first["ticket_id"].startswith("mock-")
    assert c.post("/webhooks/payment", content=body, headers=headers).json()["status"] == \
        "duplicate"
    bad = c.post("/webhooks/payment", content=body, headers={"x-signature": "t=1,v1=00"})
    assert bad.status_code == 400


def test_admin_kpis(client):
    c, _ = client
    c.post("/chat", json={"phone": "+919000000001", "message": "When is my EMI due?"})
    k = c.get("/admin/kpis").json()
    assert k["conversations"] == 1 and k["containment_rate"] == 1.0
    assert "Containment" in c.get("/admin").text
