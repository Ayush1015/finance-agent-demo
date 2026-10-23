# Finance Agent Demo

A small FastAPI + PostgreSQL demo of an AI agent working over a synthetic
invoice ledger. The agent can look up invoices and **propose** payment
records, but a payment is only written after explicit human confirmation.
All data is synthetic - no real payments, vendors or customer data.

Companion project to
[payment-webhook-ledger](https://github.com/Ayush1015/payment-webhook-ledger):
the same integrity patterns (input validation, idempotency keys, safe
retries) applied to an LLM tool-calling loop.

## Why it is built this way

LLM output is untrusted input. This project treats it that way:

* **The model never writes payments.** Its only "write" tool creates a
  PENDING proposal. A separate `POST /proposals/{id}/confirm` call - the
  human in the loop - is the only path to a `PaymentRecord`.
* **Validation happens in code, not in the prompt.** Tool arguments are
  checked with pydantic (positive amounts, max 2 decimal places, supported
  currencies, outstanding-balance limits). Malformed model output becomes
  an error message, never a database write.
* **Retries and races are handled.** Every payment carries a unique
  idempotency key enforced by a PostgreSQL unique constraint; confirming
  the same proposal twice (even concurrently) returns the original record.
  The confirm endpoint also takes a row lock on the invoice, so two
  different proposals racing on one invoice cannot both pass the balance
  check. Both cases have concurrency tests.
* **Confirmation re-validates.** Balances are re-checked at confirm time,
  so a stale proposal cannot overspend an invoice.

## Quickstart

```bash
docker compose up -d db          # PostgreSQL 16 on localhost:5432
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

The app seeds five synthetic invoices (`INV-1001` to `INV-1005`) on startup.

Try it:

```bash
# Ask the agent what is unpaid
curl -X POST localhost:8000/agent/chat \
  -H 'Content-Type: application/json' -d '{"message": "what is still unpaid?"}'

# Ask it to pay an invoice - this only creates a PENDING proposal
curl -X POST localhost:8000/agent/chat \
  -H 'Content-Type: application/json' -d '{"message": "pay INV-1001"}'

# A human confirms the proposal -> exactly one PaymentRecord
curl -X POST localhost:8000/proposals/<proposal_id>/confirm

# Confirming again returns the same record with "duplicate": true
curl -X POST localhost:8000/proposals/<proposal_id>/confirm
```

Interactive API docs: `http://localhost:8000/docs`

## Agent modes

* `AGENT_MODE=mock` (default): a deterministic, offline stand-in for the
  model. No API key needed; this is what the tests run.
* `AGENT_MODE=gemini`: real function calling through the Gemini API
  (`gemini-2.0-flash`). Set `GEMINI_API_KEY` - a free-tier key from
  [Google AI Studio](https://aistudio.google.com/apikey) is enough. The
  same guardrails apply either way: the model can propose, only a human
  confirms.

## API surface

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/invoices`, `/invoices/{id}` | Read synthetic invoices |
| POST | `/agent/chat` | Talk to the agent (read tools + proposals) |
| GET | `/proposals` | List payment proposals |
| POST | `/proposals/{id}/confirm` | Human approval; writes the payment (idempotent) |
| POST | `/proposals/{id}/reject` | Reject a pending proposal |
| GET | `/payments` | List confirmed payment records |

## Testing

```bash
pip install -r requirements-dev.txt
pytest                 # SQLite fallback, no setup needed
TEST_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/finance_demo pytest
```

24 tests cover the agent flow, idempotent confirms, duplicate delivery,
concurrent-confirmation races, balance limits, and rejection of malformed
model output (bad amounts, wrong currencies, unknown invoices, unknown
tools). The two race tests run against PostgreSQL only and are skipped on
SQLite. CI runs the suite against real PostgreSQL 16 on every push.

## Scope and limitations

This is a local learning demo: the API has **no authentication** (anyone
who can reach the confirm endpoint can confirm), all data is synthetic,
and the mock agent is a rule-based stand-in. Do not deploy it as a public
writable service. The Gemini client is implemented but runs only when you
supply your own API key.

## Stack

FastAPI, SQLAlchemy 2, PostgreSQL, pydantic v2, pytest. Optional Gemini
function calling via `google-genai`.

## Provenance

AI-assisted project: designed and generated with an AI assistant, then
reviewed, tested and verified by hand. Built as a learning/demo project
to show how LLM tool calling can sit safely on top of payment-style
integrity patterns.
