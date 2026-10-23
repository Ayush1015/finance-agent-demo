"""Duplicate requests must not create duplicate payment records."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import PaymentRecord


def test_double_confirm_creates_one_record(client):
    proposal_id = client.post(
        "/agent/chat", json={"message": "pay INV-1002"}
    ).json()["proposals"][0]

    first = client.post(f"/proposals/{proposal_id}/confirm").json()
    second = client.post(f"/proposals/{proposal_id}/confirm").json()

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["payment"]["id"] == second["payment"]["id"]
    assert len(client.get("/payments").json()) == 1


def test_idempotency_key_unique_at_database_level(client, db_url):
    from app.db import make_engine, make_session_factory

    factory = make_session_factory(make_engine(db_url))
    with factory() as s:
        s.add(
            PaymentRecord(
                invoice_id="INV-1001",
                amount_cents=100,
                currency="INR",
                idempotency_key="dup-key",
            )
        )
        s.commit()
        s.add(
            PaymentRecord(
                invoice_id="INV-1001",
                amount_cents=100,
                currency="INR",
                idempotency_key="dup-key",
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()


def test_agent_wont_propose_on_fully_paid_invoice(client):
    proposal_id = client.post(
        "/agent/chat", json={"message": "pay INV-1001"}
    ).json()["proposals"][0]
    client.post(f"/proposals/{proposal_id}/confirm")

    r = client.post("/agent/chat", json={"message": "pay INV-1001"})
    body = r.json()
    assert body["proposals"] == []
    assert "already fully paid" in body["reply"]


def test_proposal_cannot_exceed_outstanding_balance(client):
    # Pay part of INV-1001, then ask for more than what remains.
    p1 = client.post(
        "/agent/chat", json={"message": "pay INV-1001 40000.00"}
    ).json()["proposals"][0]
    client.post(f"/proposals/{p1}/confirm")

    r = client.post("/agent/chat", json={"message": "pay INV-1001 3000.00"})
    body = r.json()
    assert body["proposals"] == []
    assert "exceeds outstanding balance" in body["reply"]
    assert len(client.get("/payments").json()) == 1


import os
import threading

import pytest as _pytest

POSTGRES_ONLY = _pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="row-lock concurrency semantics require PostgreSQL",
)


def _confirm_in_thread(client, proposal_id, results, barrier):
    barrier.wait()
    r = client.post(f"/proposals/{proposal_id}/confirm")
    results.append(r.status_code)


@POSTGRES_ONLY
def test_two_proposals_race_one_invoice(client):
    """Two different proposals for the full balance race to confirm.
    Exactly one may succeed; the row lock makes the balance re-check atomic."""
    from app.db import make_engine, make_session_factory
    from app.agent import execute_tool

    # create two full-balance proposals directly at the tool layer
    factory = make_session_factory(make_engine(os.environ["TEST_DATABASE_URL"]))
    ids = []
    with factory() as s:
        from app.seed import seed
        seed(s)
        for _ in range(2):
            res = execute_tool(
                "propose_payment_record",
                {"invoice_id": "INV-1004", "amount": "450.00", "currency": "USD"},
                s,
            )
            assert res.ok
            ids.append(res.proposal_id)

    results, barrier = [], threading.Barrier(2)
    threads = [
        threading.Thread(target=_confirm_in_thread, args=(client, pid, results, barrier))
        for pid in ids
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409], results
    assert len(client.get("/payments").json()) == 1


@POSTGRES_ONLY
def test_same_proposal_confirmed_concurrently(client):
    """Two concurrent confirms of the SAME proposal: one record, one duplicate."""
    proposal_id = client.post(
        "/agent/chat", json={"message": "pay INV-1002"}
    ).json()["proposals"][0]

    results, barrier = [], threading.Barrier(2)
    threads = [
        threading.Thread(target=_confirm_in_thread, args=(client, proposal_id, results, barrier))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [200, 200]
    payments = client.get("/payments").json()
    assert len(payments) == 1
