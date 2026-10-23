"""The agent layer: tool definitions and the tool-call loop.

Safety rules enforced here:
  * The LLM can READ invoices freely.
  * The LLM can only PROPOSE a payment. Proposals start PENDING and no
    PaymentRecord exists until a human calls the confirm endpoint.
  * Tool arguments are validated with pydantic before anything is stored;
    malformed model output becomes an error message, never a write.
"""
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy.orm import Session

from .models import Invoice, PaymentProposal, PaymentRecord
from .schemas import ProposalInput

TOOL_SPECS = [
    {
        "name": "get_invoice",
        "description": "Look up one invoice by its ID (e.g. INV-1001).",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID"},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "list_unpaid_invoices",
        "description": "List all invoices that still have an outstanding balance.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "propose_payment_record",
        "description": (
            "Propose recording a payment against an invoice. This does NOT "
            "save a payment: it creates a pending proposal that a human must "
            "confirm before any payment record is written."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "amount": {
                    "type": "string",
                    "description": "Decimal amount, e.g. '1500.00'",
                },
                "currency": {"type": "string", "description": "INR or USD"},
                "reason": {"type": "string"},
            },
            "required": ["invoice_id", "amount", "currency"],
        },
    },
]

READ_TOOLS = {"get_invoice", "list_unpaid_invoices"}
WRITE_PROPOSAL_TOOL = "propose_payment_record"


@dataclass
class ToolResult:
    ok: bool
    output: dict | list | None = None
    error: str | None = None
    proposal_id: str | None = None


@dataclass
class AgentResult:
    reply: str
    tool_log: list[dict] = field(default_factory=list)
    proposal_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _invoice_dict(inv: Invoice, session: Session) -> dict:
    paid = sum(
        p.amount_cents
        for p in session.query(PaymentRecord).filter_by(invoice_id=inv.id).all()
    )
    return {
        "invoice_id": inv.id,
        "vendor": inv.vendor,
        "description": inv.description,
        "amount_cents": inv.amount_cents,
        "paid_cents": paid,
        "outstanding_cents": inv.amount_cents - paid,
        "currency": inv.currency,
        "status": inv.status,
    }


def execute_tool(name: str, args: dict, session: Session) -> ToolResult:
    """Run one tool call. Read tools query; the write tool only creates a
    PENDING proposal. All argument validation happens here."""
    if name == "get_invoice":
        inv = session.get(Invoice, str(args.get("invoice_id", "")))
        if inv is None:
            return ToolResult(ok=False, error=f"invoice {args.get('invoice_id')!r} not found")
        return ToolResult(ok=True, output=_invoice_dict(inv, session))

    if name == "list_unpaid_invoices":
        rows = session.query(Invoice).filter(Invoice.status == "UNPAID").all()
        return ToolResult(ok=True, output=[_invoice_dict(i, session) for i in rows])

    if name == WRITE_PROPOSAL_TOOL:
        try:
            data = ProposalInput(**args)
        except ValidationError as exc:
            return ToolResult(ok=False, error=f"invalid proposal arguments: {exc.errors()}")

        inv = session.get(Invoice, data.invoice_id)
        if inv is None:
            return ToolResult(ok=False, error=f"invoice {data.invoice_id!r} not found")
        if inv.currency != data.currency:
            return ToolResult(
                ok=False,
                error=f"currency mismatch: invoice is in {inv.currency}, proposal in {data.currency}",
            )

        paid = sum(
            p.amount_cents
            for p in session.query(PaymentRecord).filter_by(invoice_id=inv.id).all()
        )
        outstanding = inv.amount_cents - paid
        if outstanding <= 0:
            return ToolResult(ok=False, error=f"invoice {inv.id} is already fully paid")
        if data.amount_cents() > outstanding:
            return ToolResult(
                ok=False,
                error=(
                    f"amount {Decimal(data.amount)} exceeds outstanding balance "
                    f"{Decimal(outstanding) / 100} {inv.currency}"
                ),
            )

        proposal = PaymentProposal(
            invoice_id=inv.id,
            amount_cents=data.amount_cents(),
            currency=data.currency,
            reason=data.reason,
            idempotency_key=f"prop-{uuid.uuid4()}",
        )
        session.add(proposal)
        session.commit()
        return ToolResult(
            ok=True,
            proposal_id=proposal.id,
            output={
                "proposal_id": proposal.id,
                "status": proposal.status,
                "note": "Proposal created. No payment is recorded until a human confirms it.",
            },
        )

    return ToolResult(ok=False, error=f"unknown tool {name!r}")


def run_tool_calls(tool_calls: list[dict], session: Session) -> AgentResult:
    """Execute a batch of model-requested tool calls and summarise."""
    result = AgentResult(reply="")
    for call in tool_calls:
        name, args = call.get("name", ""), call.get("args", {}) or {}
        res = execute_tool(name, args, session)
        result.tool_log.append({"tool": name, "args": args, "ok": res.ok})
        if res.ok:
            if res.proposal_id:
                result.proposal_ids.append(res.proposal_id)
        else:
            result.errors.append(f"{name}: {res.error}")
    return result
