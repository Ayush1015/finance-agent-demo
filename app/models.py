"""Database models for the finance agent demo.

All data is synthetic. No real payments, vendors or customer data.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(String(32), primary_key=True)  # e.g. "INV-1001"
    vendor = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False)
    status = Column(String(16), nullable=False, default="UNPAID")  # UNPAID | PAID
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    proposals = relationship("PaymentProposal", back_populates="invoice")
    payments = relationship("PaymentRecord", back_populates="invoice")


class PaymentProposal(Base):
    """A payment the agent has proposed but a human has NOT confirmed yet.

    The agent can create proposals; it can never create a PaymentRecord
    directly. Confirmation is a separate, explicit API call.
    """

    __tablename__ = "payment_proposals"

    id = Column(String(36), primary_key=True, default=_uuid)
    invoice_id = Column(String(32), ForeignKey("invoices.id"), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False)
    reason = Column(Text, nullable=False, default="")
    idempotency_key = Column(String(80), nullable=False, unique=True)
    status = Column(String(16), nullable=False, default="PENDING")  # PENDING | CONFIRMED | REJECTED
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    invoice = relationship("Invoice", back_populates="proposals")


class PaymentRecord(Base):
    """A confirmed payment record. Only created by the confirm endpoint.

    The unique idempotency_key is the same integrity pattern as the
    payment-webhook-ledger project: a retried or duplicated request
    must not create a second record.
    """

    __tablename__ = "payment_records"

    id = Column(String(36), primary_key=True, default=_uuid)
    invoice_id = Column(String(32), ForeignKey("invoices.id"), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False)
    idempotency_key = Column(String(80), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    invoice = relationship("Invoice", back_populates="payments")
