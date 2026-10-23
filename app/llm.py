"""LLM clients.

Two interchangeable clients:

* MockLLMClient - deterministic, no API key, used by the tests and by
  default. It parses the user's message with simple rules so the whole
  agent flow works offline.
* GeminiClient - real function calling through the Gemini API. Used only
  when GEMINI_API_KEY is set and AGENT_MODE=gemini. The google-genai
  package is imported lazily so the app and tests never require it.
"""
import os
import re

from sqlalchemy.orm import Session

from .agent import TOOL_SPECS, AgentResult, execute_tool


class MockLLMClient:
    """Rule-based stand-in for a model with function calling.

    Understands a small command language:
      * 'pay <INVOICE-ID>'            -> propose paying the full outstanding
      * 'pay <INVOICE-ID> <amount>'   -> propose paying that amount
      * '<INVOICE-ID>'                -> get_invoice
      * anything else                 -> list_unpaid_invoices
    """

    name = "mock"

    def run(self, message: str, session: Session) -> AgentResult:
        result = AgentResult(reply="")
        m = re.search(r"(INV-\d+)", message.upper())
        wants_pay = "pay" in message.lower()

        if m and wants_pay:
            inv_id = m.group(1)
            look = execute_tool("get_invoice", {"invoice_id": inv_id}, session)
            result.tool_log.append({"tool": "get_invoice", "args": {"invoice_id": inv_id}, "ok": look.ok})
            if not look.ok:
                result.errors.append(look.error)
                result.reply = look.error
                return result
            outstanding = look.output["outstanding_cents"]
            if outstanding <= 0:
                msg = f"invoice {inv_id} is already fully paid"
                result.errors.append(msg)
                result.reply = msg
                return result
            amount_m = re.search(r"(\d+(?:\.\d{1,2})?)", message.replace(inv_id, ""))
            amount = amount_m.group(1) if amount_m else f"{outstanding / 100:.2f}"
            res = execute_tool(
                "propose_payment_record",
                {
                    "invoice_id": inv_id,
                    "amount": amount,
                    "currency": look.output["currency"],
                    "reason": message[:200],
                },
                session,
            )
            result.tool_log.append({"tool": "propose_payment_record", "args": {"invoice_id": inv_id, "amount": amount}, "ok": res.ok})
            if res.ok:
                result.proposal_ids.append(res.proposal_id)
                result.reply = (
                    f"I drafted a payment proposal for {inv_id} "
                    f"({amount} {look.output['currency']}). "
                    f"Confirm proposal {res.proposal_id} to record it - nothing is saved yet."
                )
            else:
                result.errors.append(res.error)
                result.reply = f"I couldn't draft that payment: {res.error}"
            return result

        if m:
            res = execute_tool("get_invoice", {"invoice_id": m.group(1)}, session)
            result.tool_log.append({"tool": "get_invoice", "args": {"invoice_id": m.group(1)}, "ok": res.ok})
            if res.ok:
                o = res.output
                result.reply = (
                    f"{o['invoice_id']} from {o['vendor']}: "
                    f"{o['outstanding_cents'] / 100:.2f} {o['currency']} outstanding "
                    f"({o['status']})."
                )
            else:
                result.errors.append(res.error)
                result.reply = res.error
            return result

        res = execute_tool("list_unpaid_invoices", {}, session)
        result.tool_log.append({"tool": "list_unpaid_invoices", "args": {}, "ok": res.ok})
        if res.ok:
            lines = [
                f"{o['invoice_id']}: {o['outstanding_cents'] / 100:.2f} {o['currency']} - {o['vendor']}"
                for o in res.output
            ]
            result.reply = "Unpaid invoices:\n" + ("\n".join(lines) if lines else "none")
        else:
            result.errors.append(res.error)
            result.reply = res.error
        return result


class GeminiClient:
    """Gemini function-calling client. Requires GEMINI_API_KEY.

    Uses the free-tier-friendly model 'gemini-2.0-flash'. Only tool
    calling is used; the model never writes to the database directly.
    """

    name = "gemini"
    MODEL = "gemini-2.0-flash"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")

    def run(self, message: str, session: Session, max_turns: int = 6) -> AgentResult:
        from google import genai  # lazy import: optional dependency
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        declarations = [
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters_json_schema=t["parameters"],
            )
            for t in TOOL_SPECS
        ]
        tool = types.Tool(function_declarations=declarations)
        system = (
            "You are a careful finance assistant over a small synthetic invoice "
            "ledger. You may look up invoices and draft payment proposals with "
            "the propose_payment_record tool. Proposals are NOT payments: a "
            "human confirms them separately. Never invent invoice IDs or "
            "amounts; read them with tools first. If anything is ambiguous, "
            "ask instead of guessing."
        )
        contents = [types.Content(role="user", parts=[types.Part(text=message)])]
        result = AgentResult(reply="")

        for _ in range(max_turns):
            resp = client.models.generate_content(
                model=self.MODEL,
                contents=contents,
                config=types.GenerateContentConfig(tools=[tool], system_instruction=system),
            )
            part_calls = [
                p.function_call
                for p in resp.candidates[0].content.parts
                if getattr(p, "function_call", None)
            ]
            if not part_calls:
                result.reply = resp.text or ""
                return result

            contents.append(resp.candidates[0].content)
            response_parts = []
            for fc in part_calls:
                args = dict(fc.args or {})
                res = execute_tool(fc.name, args, session)
                result.tool_log.append({"tool": fc.name, "args": args, "ok": res.ok})
                if res.ok and res.proposal_id:
                    result.proposal_ids.append(res.proposal_id)
                if not res.ok:
                    result.errors.append(f"{fc.name}: {res.error}")
                payload = {"result": res.output} if res.ok else {"error": res.error}
                response_parts.append(
                    types.Part.from_function_response(name=fc.name, response=payload)
                )
            contents.append(types.Content(role="user", parts=response_parts))

        result.reply = "Stopped after the maximum number of tool-calling turns."
        return result


def get_llm_client():
    mode = os.environ.get("AGENT_MODE", "mock").lower()
    if mode == "gemini":
        return GeminiClient()
    return MockLLMClient()
