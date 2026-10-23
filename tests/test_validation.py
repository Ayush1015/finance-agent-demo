"""Malformed model output and invalid amounts are rejected before any
database write. These call the tool layer directly, the way a sloppy or
confused LLM response would arrive."""
import pytest

from app.agent import execute_tool
from app.db import make_engine, make_session_factory
from app.models import PaymentProposal, PaymentRecord


@pytest.fixture()
def session(db_url, reset_schema):
    factory = make_session_factory(make_engine(db_url))
    with factory() as s:
        from app.seed import seed

        seed(s)
        yield s


def _counts(s):
    return (
        s.query(PaymentProposal).count(),
        s.query(PaymentRecord).count(),
    )


@pytest.mark.parametrize(
    "amount",
    ["-100", "0", "0.00", "10.999", "not-a-number", "99999999"],
)
def test_bad_amounts_rejected(session, amount):
    res = execute_tool(
        "propose_payment_record",
        {"invoice_id": "INV-1001", "amount": amount, "currency": "INR"},
        session,
    )
    assert not res.ok
    assert _counts(session) == (0, 0)


def test_unsupported_currency_rejected(session):
    res = execute_tool(
        "propose_payment_record",
        {"invoice_id": "INV-1001", "amount": "100.00", "currency": "EUR"},
        session,
    )
    assert not res.ok
    assert "unsupported currency" in res.error
    assert _counts(session) == (0, 0)


def test_currency_mismatch_rejected(session):
    res = execute_tool(
        "propose_payment_record",
        {"invoice_id": "INV-1001", "amount": "100.00", "currency": "USD"},
        session,
    )
    assert not res.ok
    assert "currency mismatch" in res.error
    assert _counts(session) == (0, 0)


def test_unknown_invoice_rejected(session):
    res = execute_tool(
        "propose_payment_record",
        {"invoice_id": "INV-9999", "amount": "100.00", "currency": "INR"},
        session,
    )
    assert not res.ok
    assert _counts(session) == (0, 0)


def test_unknown_tool_rejected(session):
    res = execute_tool("delete_everything", {}, session)
    assert not res.ok
    assert _counts(session) == (0, 0)


def test_missing_argument_rejected(session):
    res = execute_tool(
        "propose_payment_record",
        {"invoice_id": "INV-1001", "currency": "INR"},  # no amount at all
        session,
    )
    assert not res.ok
    assert _counts(session) == (0, 0)


def test_confirm_unknown_proposal_404(client):
    assert client.post("/proposals/nope/confirm").status_code == 404
    assert client.post("/proposals/nope/reject").status_code == 404
