"""Ask Axiom tests: masking, key storage, read-only tools, conversation store and the Gemini
loop with a FAKE client (no network, synthetic data only)."""

from __future__ import annotations

import os
import stat
import threading
from types import SimpleNamespace

import pandas as pd
import pytest

import axiom_ai_store
import axiom_db
import axiom_secrets
from axiom_ai_chat import ask, list_chat_models
from axiom_ai_redact import HIDDEN_INCOMING, hide_incoming_description, mask_text, mask_value
from axiom_ai_tools import AIContext, function_declarations, run_tool
from axiom_core import STANDARD_COLUMNS
from axiom_dates import fy_bounds
from axiom_export_checks import build_export


# ---- masking ------------------------------------------------------------- #
def test_mask_identifiers_keeps_amounts_and_dates():
    text = ("Westpac 12-3456-7890123-00 ref 2025041101 card 4111 1111 1111 1234 IRD 123-456-789 "
            "a@b.co.nz ph 021 555 1234 paid $1,234.56 on 05/09/2025 acct 0618477")
    out = mask_text(text)
    assert "7890123" not in out and "[#…300]" in out
    assert "[#…101]" in out and "[#…234]" in out and "[IRD no.]" in out
    assert "[email]" in out and "[phone]" in out
    assert "$1,234.56" in out and "05/09/2025" in out and "[#…477]" in out
    assert mask_value({"a": ["x 0618477"], "b": 5}) == {"a": ["x [#…477]"], "b": 5}


def test_hide_incoming_only_hides_tenant_like_deposits():
    assert hide_incoming_description("J Smith rent", 500.0, "Bank CSV") == HIDDEN_INCOMING
    assert hide_incoming_description("Charlton Pro payout", 500.0, "Bank CSV") == "Charlton Pro payout"
    assert hide_incoming_description("ANZ loan advance", 9.0, "Bank CSV") == "ANZ loan advance"
    assert hide_incoming_description("Sample Holdings Ltd", 9.0, "Bank CSV", "Sample Holdings Ltd") == "Sample Holdings Ltd"
    assert hide_incoming_description("J Smith", -50.0, "Bank CSV") == "J Smith"
    assert hide_incoming_description("J Smith", 50.0, "PM Statement") == "J Smith"


# ---- key storage --------------------------------------------------------- #
def test_key_file_is_private_and_migrates_out_of_store():
    axiom_secrets.save_gemini_key("test-key-123")
    path = axiom_secrets.secrets_dir() / "gemini_api_key"
    assert axiom_secrets.load_gemini_key() == "test-key-123"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    store = {"google_genai_api_key": "old-key"}
    assert axiom_secrets.migrate_key_out_of_store(store) and store["google_genai_api_key"] == ""
    assert axiom_secrets.load_gemini_key() == "test-key-123"  # existing file wins
    axiom_secrets.save_gemini_key("")
    assert axiom_secrets.load_gemini_key() == ""


def test_env_var_overrides_file(monkeypatch):
    axiom_secrets.save_gemini_key("file-key")
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert axiom_secrets.load_gemini_key() == "env-key"


# ---- tools --------------------------------------------------------------- #
def _ctx(hide=True) -> AIContext:
    rows = [
        ["05/09/2025", 1000.0, "PM Year End Summary: Rent | 2025 Sep", "1 Test", "Rent Income"],
        ["06/09/2025", -200.0, "Sample Council 0618477", "1 Test", "Rates"],
        ["07/09/2025", 300.0, "J Smith", "1 Test", "Uncategorized"],
    ]
    recs = [{"name": "Acme - Summary.pdf", "bucket": "pdf", "df": pd.DataFrame(rows[:1], columns=STANDARD_COLUMNS)},
            {"name": "bank.csv", "bucket": "csv", "df": pd.DataFrame(rows[1:], columns=STANDARD_COLUMNS)}]
    ledger = pd.concat([r["df"] for r in recs], ignore_index=True)
    res = build_export(ledger, recs, fy=fy_bounds("2026"), codes=["Rent Income", "Rates"], properties=["1 Test"])
    return AIContext(entity="Sample Ltd", tax_year="2026", period="1 Apr 2025 – 31 Mar 2026", jurisdiction="NZ",
                     result=res, records=recs, rules=[{"pattern": "Sample Council", "account_code": "Rates",
                                                       "property": "1 Test"}],
                     codes=["Rent Income", "Rates"], properties=["1 Test"], hide_incoming=hide)


def test_tools_return_masked_results():
    ctx = _ctx()
    ov = run_tool(ctx, "get_overview", {})
    assert ov["rows_for_accountant"] == 3 and ov["ready_to_send"] is False
    found = run_tool(ctx, "search_transactions", {"text": "council"})
    assert found["matched_rows"] == 1 and found["rows"][0]["Description"] == "Sample Council [#…477]"
    unc = run_tool(ctx, "search_transactions", {"uncoded_only": True})
    assert unc["rows"][0]["Description"] == HIDDEN_INCOMING
    assert run_tool(_ctx(hide=False), "search_transactions", {"uncoded_only": True})["rows"][0]["Description"] == "J Smith"
    dated = run_tool(ctx, "search_transactions", {"date_from": "06/09/2025", "max_amount": 0})
    assert dated["matched_rows"] == 1
    summ = run_tool(ctx, "summarize", {"group_by": ["account_code"]})
    assert {g["Account Code"]: g["total"] for g in summ["groups"]} == {"Rates": -200.0, "Rent Income": 1000.0,
                                                                       "Uncategorized": 300.0}
    assert any(c["name"] == "Uncoded rows" for c in run_tool(ctx, "get_readiness_checks", {})["checks"])
    assert run_tool(ctx, "get_property_summary", {"property": "1 test"})["properties"][0]["net"] == 1100.0
    assert run_tool(ctx, "list_rules", {"text": "council"})["matched_rules"] == 1
    assert run_tool(ctx, "nope", {})["error"].startswith("Unknown tool")
    assert {d["name"] for d in function_declarations()} >= {"search_transactions", "get_rent_reconciliation"}


# ---- conversation store -------------------------------------------------- #
@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    cache: dict = {}
    monkeypatch.setattr(axiom_db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(axiom_db, "VAULT_ROOT_DIR", tmp_path / "vault")
    monkeypatch.setattr(axiom_db, "SQLITE_DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(axiom_db, "_CONN_CACHE", cache)
    monkeypatch.setattr(axiom_db, "_SCHEMA_READY", threading.Event())
    yield
    for conn in cache.values():
        conn.close()


def test_conversation_store_round_trip(temp_db):
    cid = axiom_ai_store.create_conversation("E", "2026", "Rent question", "gemini-x")
    axiom_ai_store.add_message(cid, "user", "hi")
    axiom_ai_store.add_message(cid, "assistant", "hello", [{"tool": "get_overview", "result": {"a": 1}}], "gemini-x")
    msgs = axiom_ai_store.get_messages(cid)
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["sent"][0]["tool"] == "get_overview"
    axiom_ai_store.rename_conversation(cid, "Charlton gap")
    assert axiom_ai_store.list_conversations("E", "2026")[0]["title"] == "Charlton gap"
    assert axiom_ai_store.list_conversations("E", "2025") == []
    axiom_ai_store.delete_conversation(cid)
    assert axiom_ai_store.list_conversations("E", "2026") == [] and axiom_ai_store.get_messages(cid) == []


# ---- Gemini loop (fake client) -------------------------------------------- #
class _FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        return self.responses.pop(0)

    def list(self):
        return [SimpleNamespace(name="models/gemini-test-pro", supported_actions=["generateContent"]),
                SimpleNamespace(name="models/gemini-embedding-1", supported_actions=["embedContent"]),
                SimpleNamespace(name="models/gemini-test-flash", supported_actions=["generateContent", "countTokens"])]


def _call_response(name, args):
    call = SimpleNamespace(name=name, args=args)
    from google.genai import types

    content = types.Content(role="model", parts=[types.Part.from_function_call(name=name, args=args)])
    return SimpleNamespace(function_calls=[call], candidates=[SimpleNamespace(content=content)], text=None)


def test_ask_runs_tools_locally_and_logs_what_was_sent():
    models = _FakeModels([_call_response("search_transactions", {"text": "council"}),
                          SimpleNamespace(function_calls=None, text="Rates were $200.00.")])
    client = SimpleNamespace(models=models)
    history = [{"role": "user", "text": "earlier acct 0618477"}, {"role": "assistant", "text": "ok"}]
    res = ask(client, "gemini-test-pro", _ctx(), history, "What did I pay the council?")
    assert res.text == "Rates were $200.00." and res.rounds == 2
    assert res.sent[0]["tool"] == "search_transactions"
    assert res.sent[0]["result"]["rows"][0]["Description"] == "Sample Council [#…477]"
    first = models.calls[0]
    assert first["config"].automatic_function_calling.disable is True
    assert "0618477" not in first["contents"][0].parts[0].text  # history masked too
    assert len(models.calls[1]["contents"]) == len(first["contents"]) + 2  # model call + tool response


def test_ask_reports_api_errors_and_lists_models():
    class Boom:
        def generate_content(self, **kw):
            raise RuntimeError("quota exceeded\nmore")

    res = ask(SimpleNamespace(models=Boom()), "m", _ctx(), [], "q")
    assert res.error.startswith("RuntimeError: quota exceeded") and res.text.startswith("⚠️")
    assert list_chat_models(SimpleNamespace(models=_FakeModels([]))) == ["gemini-test-pro", "gemini-test-flash"]
