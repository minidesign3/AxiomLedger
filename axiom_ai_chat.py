"""
axiom_ai_chat
=============

Ask Axiom's Gemini conversation loop (google-genai SDK).

* :func:`list_chat_models` — the Gemini models the API key can actually use
  (live ``models.list``, filtered to those supporting ``generateContent``), so
  new models appear without code changes.
* :func:`ask` — one user turn: sends the conversation + system instructions +
  read-only tool declarations; whenever Gemini calls a tool it is executed
  **locally** (``axiom_ai_tools.run_tool``: masked, size-capped) and the result
  returned, up to ``MAX_TOOL_ROUNDS``. Automatic function calling is disabled
  so every result can be masked and logged. Returns the answer text plus the
  audit list of exactly what was sent for this turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from axiom_ai_redact import mask_text
from axiom_ai_tools import AIContext, function_declarations, run_tool

MAX_TOOL_ROUNDS = 8
MAX_HISTORY_MESSAGES = 20

SYSTEM_PROMPT = """You are Ask Axiom, an expert assistant inside Axiom Ledger, a bookkeeping app for a
New Zealand residential-property company. You help the owner get a complete, reconciled, correctly coded
financial year ready to send to their accountant.

Context:
- Entity: {entity}; financial year {tax_year} ({period}); jurisdiction {jurisdiction}; amounts in NZD.
- Rent policy: rental income comes from the property-manager (PM) statements, gross and per house. Bank deposits
  from property managers are their NET owner payouts; they are excluded from the accountant's P&L and are only used
  to reconcile against each statement's "paid to owner" figures.
- The app produces an Accountant Pack (Summary, Per Property, Rent Reconciliation, Transactions, Checks, Excluded).
  Its readiness checklist lists blockers (must fix) and warnings.

How to work:
- Always use the tools to look at the real data before answering; never guess or invent figures.
- Quote amounts, dates and counts exactly as the tools return them. If something is not in the data, say so.
- Explain the likely cause of problems (e.g. a payout under another payee name, a statement period overlap, a
  missing statement, own-account transfers coded as income) and give concrete next steps in the app
  (which tab / button), most important first.
- Identifiers are masked (e.g. [#…477]); refer to them as shown.
- You can't change data. Suggest changes for the owner to make.
- Give general bookkeeping guidance, but leave tax rulings (deductibility, capital vs revenue, depreciation) to the
  accountant and say so when relevant.
- Be concise; use short lists and small tables.
"""


@dataclass
class AskResult:
    text: str
    sent: list[dict] = field(default_factory=list)  # audit: tool calls + masked results
    rounds: int = 0
    error: str = ""


def list_chat_models(client) -> list[str]:
    """Model ids (e.g. 'gemini-…') usable for chat with this key, newest-looking first."""
    names: list[str] = []
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or getattr(model, "supported_generation_methods", None) or []
        name = str(getattr(model, "name", "") or "")
        short = name.split("/", 1)[-1]
        if "generateContent" in actions and short.startswith("gemini") and "embedding" not in short:
            names.append(short)
    return sorted(set(names), reverse=True)


def _history_contents(types, history: list[dict], question: str) -> list:
    contents = []
    for msg in history[-MAX_HISTORY_MESSAGES:]:
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=mask_text(msg["text"]))]))
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=mask_text(question))]))
    return contents


def _config(types, ctx: AIContext):
    system = SYSTEM_PROMPT.format(entity=ctx.entity, tax_year=ctx.tax_year, period=ctx.period,
                                  jurisdiction=ctx.jurisdiction)
    return types.GenerateContentConfig(
        system_instruction=system,
        tools=[types.Tool(function_declarations=function_declarations())],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=0.2,
    )


def _response_text(response) -> str:
    try:
        return (response.text or "").strip()
    except Exception:
        return ""


def ask(client, model: str, ctx: AIContext, history: list[dict], question: str) -> AskResult:
    """Run one question through Gemini with locally-executed read-only tools."""
    from google.genai import types

    contents = _history_contents(types, history, question)
    config = _config(types, ctx)
    result = AskResult(text="")
    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
            result.text = f"⚠️ Gemini request failed: {result.error}"
            return result
        calls = list(getattr(response, "function_calls", None) or [])
        result.rounds = round_no
        if not calls:
            result.text = _response_text(response) or "(No answer returned.)"
            return result
        contents.append(response.candidates[0].content)
        parts = []
        for call in calls:
            args = dict(call.args or {})
            output = run_tool(ctx, call.name, args)
            result.sent.append({"tool": call.name, "args": args, "result": output})
            parts.append(types.Part.from_function_response(name=call.name, response={"result": output}))
        contents.append(types.Content(role="user", parts=parts))
    result.text = "⚠️ Stopped after too many data lookups — please ask a narrower question."
    return result
