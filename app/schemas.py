"""Pydantic schemas and validation rules.

Validation lives here (not in the LLM) so malformed model output is
rejected before anything touches the database.
"""
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_CURRENCIES = {"INR", "USD"}
MAX_AMOUNT = Decimal("10000000")  # 10 million units, demo safety cap


class ProposalInput(BaseModel):
    """Validated arguments for the propose_payment_record tool."""

    invoice_id: str = Field(min_length=1, max_length=32)
    amount: str = Field(description="Decimal amount as a string, e.g. '1500.00'")
    currency: str
    reason: str = Field(default="", max_length=500)

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive_money(cls, v: str) -> str:
        try:
            d = Decimal(v)
        except InvalidOperation:
            raise ValueError(f"amount is not a number: {v!r}")
        if d <= 0:
            raise ValueError("amount must be greater than zero")
        if d > MAX_AMOUNT:
            raise ValueError("amount exceeds demo safety cap")
        if -d.as_tuple().exponent > 2:
            raise ValueError("amount must have at most 2 decimal places")
        return v

    @field_validator("currency")
    @classmethod
    def currency_must_be_supported(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in ALLOWED_CURRENCIES:
            raise ValueError(
                f"unsupported currency {v!r}; allowed: {sorted(ALLOWED_CURRENCIES)}"
            )
        return v

    def amount_cents(self) -> int:
        return int((Decimal(self.amount) * 100).to_integral_exact())


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ProposalOut(BaseModel):
    id: str
    invoice_id: str
    amount_cents: int
    currency: str
    reason: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class PaymentOut(BaseModel):
    id: str
    invoice_id: str
    amount_cents: int
    currency: str

    model_config = ConfigDict(from_attributes=True)


class InvoiceOut(BaseModel):
    id: str
    vendor: str
    description: str
    amount_cents: int
    currency: str
    status: str

    model_config = ConfigDict(from_attributes=True)
