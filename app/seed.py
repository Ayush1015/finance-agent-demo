"""Synthetic demo invoices. All vendors and amounts are fictional."""
from sqlalchemy.orm import Session

from .models import Invoice

SEED_INVOICES = [
    dict(
        id="INV-1001",
        vendor="Demo Office Supplies Pvt Ltd (synthetic)",
        description="Ergonomic chairs x4 - fictional vendor, demo data only",
        amount_cents=42_000_00,
        currency="INR",
    ),
    dict(
        id="INV-1002",
        vendor="Example Cloud Hosting LLC (synthetic)",
        description="Annual hosting plan - fictional vendor, demo data only",
        amount_cents=1_200_00,
        currency="USD",
    ),
    dict(
        id="INV-1003",
        vendor="Sample Catering Co (synthetic)",
        description="Team offsite lunch - fictional vendor, demo data only",
        amount_cents=18_500_00,
        currency="INR",
    ),
    dict(
        id="INV-1004",
        vendor="Test Analytics Inc (synthetic)",
        description="Dashboard licence, 5 seats - fictional vendor, demo data only",
        amount_cents=450_00,
        currency="USD",
    ),
    dict(
        id="INV-1005",
        vendor="Placeholder Logistics (synthetic)",
        description="Equipment shipping - fictional vendor, demo data only",
        amount_cents=9_750_00,
        currency="INR",
    ),
]


def seed(session: Session) -> int:
    """Insert the synthetic invoices that are not already present."""
    added = 0
    for data in SEED_INVOICES:
        if session.get(Invoice, data["id"]) is None:
            session.add(Invoice(**data))
            added += 1
    session.commit()
    return added
