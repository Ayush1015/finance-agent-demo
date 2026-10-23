"""End-to-end agent behaviour with the deterministic mock client."""


def test_lists_unpaid_invoices(client):
    r = client.post("/agent/chat", json={"message": "what is still unpaid?"})
    assert r.status_code == 200
    body = r.json()
    assert "INV-1001" in body["reply"]
    assert body["agent_mode"] == "mock"
    assert any(t["tool"] == "list_unpaid_invoices" for t in body["tool_log"])


def test_get_invoice_via_chat(client):
    r = client.post("/agent/chat", json={"message": "show me INV-1002"})
    assert r.status_code == 200
    body = r.json()
    assert "1200.00 USD" in body["reply"]


def test_pay_creates_pending_proposal_only(client):
    r = client.post("/agent/chat", json={"message": "pay INV-1001"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["proposals"]) == 1

    # Nothing is recorded until a human confirms.
    assert client.get("/payments").json() == []
    proposals = client.get("/proposals").json()
    assert proposals[0]["status"] == "PENDING"
    assert client.get("/invoices/INV-1001").json()["status"] == "UNPAID"


def test_confirm_records_payment_and_marks_invoice_paid(client):
    proposal_id = client.post(
        "/agent/chat", json={"message": "pay INV-1003"}
    ).json()["proposals"][0]

    r = client.post(f"/proposals/{proposal_id}/confirm")
    assert r.status_code == 200
    assert r.json()["duplicate"] is False

    payments = client.get("/payments").json()
    assert len(payments) == 1
    assert payments[0]["amount_cents"] == 18_500_00
    assert client.get("/invoices/INV-1003").json()["status"] == "PAID"
    assert client.get("/proposals").json()[0]["status"] == "CONFIRMED"


def test_rejected_proposal_cannot_be_confirmed(client):
    proposal_id = client.post(
        "/agent/chat", json={"message": "pay INV-1004"}
    ).json()["proposals"][0]
    assert client.post(f"/proposals/{proposal_id}/reject").status_code == 200
    assert client.post(f"/proposals/{proposal_id}/confirm").status_code == 409
    assert client.get("/payments").json() == []


def test_partial_payment_leaves_invoice_unpaid(client):
    proposal_id = client.post(
        "/agent/chat", json={"message": "pay INV-1005 5000.00"}
    ).json()["proposals"][0]
    client.post(f"/proposals/{proposal_id}/confirm")
    inv = client.get("/invoices/INV-1005").json()
    assert inv["status"] == "UNPAID"
    assert client.get("/payments").json()[0]["amount_cents"] == 5_000_00
