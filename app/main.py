"""FastAPI application: synthetic invoice ledger + finance agent demo.

Built as a small, explainable companion to the payment-webhook-ledger
project: the same integrity ideas (validation, idempotency) applied to
an LLM agent that can propose - but never directly record - payments.
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .agent import AgentResult
from .db import init_db, make_engine, make_session_factory
from .llm import get_llm_client
from .models import Invoice, PaymentProposal, PaymentRecord
from .schemas import ChatRequest, InvoiceOut, PaymentOut, ProposalOut
from .seed import seed


def create_app(database_url: str | None = None) -> FastAPI:
    engine = make_engine(database_url)
    factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        with factory() as session:
            seed(session)
        yield

    app = FastAPI(
        title="Finance Agent Demo (synthetic data)",
        description=(
            "An LLM agent that can read synthetic invoices and propose "
            "payment records. Proposals are only written after explicit "
            "human confirmation, with idempotency keys guarding duplicates."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    def get_session():
        with factory() as session:
            yield session

    # ---------- invoices (read) ----------

    @app.get("/invoices", response_model=list[InvoiceOut])
    def list_invoices(session: Session = Depends(get_session)):
        return session.query(Invoice).order_by(Invoice.id).all()

    @app.get("/invoices/{invoice_id}", response_model=InvoiceOut)
    def get_invoice(invoice_id: str, session: Session = Depends(get_session)):
        inv = session.get(Invoice, invoice_id)
        if inv is None:
            raise HTTPException(404, f"invoice {invoice_id!r} not found")
        return inv

    # ---------- agent ----------

    @app.post("/agent/chat")
    def agent_chat(req: ChatRequest, session: Session = Depends(get_session)):
        client = get_llm_client()
        result: AgentResult = client.run(req.message, session)
        return {
            "reply": result.reply,
            "agent_mode": client.name,
            "proposals": result.proposal_ids,
            "tool_log": result.tool_log,
            "errors": result.errors,
        }

    # ---------- proposals (human-in-the-loop) ----------

    @app.get("/proposals", response_model=list[ProposalOut])
    def list_proposals(session: Session = Depends(get_session)):
        return session.query(PaymentProposal).order_by(PaymentProposal.created_at).all()

    @app.post("/proposals/{proposal_id}/confirm")
    def confirm_proposal(proposal_id: str, session: Session = Depends(get_session)):
        proposal = session.get(PaymentProposal, proposal_id)
        if proposal is None:
            raise HTTPException(404, f"proposal {proposal_id!r} not found")
        if proposal.status == "REJECTED":
            raise HTTPException(409, "proposal was rejected and cannot be confirmed")

        # Lock the invoice row FIRST: concurrent confirms - of this proposal
        # or of a different one on the same invoice - serialize here, so the
        # duplicate check and the balance re-check below each run exactly
        # once per invoice at a time. (PostgreSQL; a no-op on SQLite.)
        inv = (
            session.query(Invoice)
            .filter(Invoice.id == proposal.invoice_id)
            .with_for_update()
            .one()
        )

        existing = (
            session.query(PaymentRecord)
            .filter_by(idempotency_key=proposal.idempotency_key)
            .one_or_none()
        )
        if existing is not None:
            # Already confirmed (or a duplicate delivery of this confirm):
            # return the original record, do not create a second one.
            return {"payment": PaymentOut.model_validate(existing), "duplicate": True}

        paid = sum(p.amount_cents for p in inv.payments)
        if proposal.amount_cents > inv.amount_cents - paid:
            raise HTTPException(
                409,
                "outstanding balance changed; proposal no longer fits. "
                "Reject it and ask the agent for a fresh proposal.",
            )

        record = PaymentRecord(
            invoice_id=proposal.invoice_id,
            amount_cents=proposal.amount_cents,
            currency=proposal.currency,
            idempotency_key=proposal.idempotency_key,
        )
        session.add(record)
        try:
            session.flush()
        except IntegrityError:
            # Lost a race with a concurrent confirm of the same proposal.
            session.rollback()
            existing = (
                session.query(PaymentRecord)
                .filter_by(idempotency_key=proposal.idempotency_key)
                .one()
            )
            return {"payment": PaymentOut.model_validate(existing), "duplicate": True}

        proposal.status = "CONFIRMED"
        if paid + record.amount_cents >= inv.amount_cents:
            inv.status = "PAID"
        session.commit()
        return {"payment": PaymentOut.model_validate(record), "duplicate": False}

    @app.post("/proposals/{proposal_id}/reject", response_model=ProposalOut)
    def reject_proposal(proposal_id: str, session: Session = Depends(get_session)):
        proposal = session.get(PaymentProposal, proposal_id)
        if proposal is None:
            raise HTTPException(404, f"proposal {proposal_id!r} not found")
        if proposal.status == "CONFIRMED":
            raise HTTPException(409, "proposal was already confirmed")
        proposal.status = "REJECTED"
        session.commit()
        return proposal

    # ---------- payments (read) ----------

    @app.get("/payments", response_model=list[PaymentOut])
    def list_payments(session: Session = Depends(get_session)):
        return session.query(PaymentRecord).order_by(PaymentRecord.created_at).all()

    return app


app = create_app()
