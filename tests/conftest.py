"""
Shared pytest fixtures.

`st.session_state` is **process-global** in bare (non-Streamlit) test runs — the
same underlying store is shared by every test in the session. Production code
correctly reads and writes it (e.g. `_save_memory_store()` merges
`st.session_state` over the store it is given; `axiom_llm.verify_gemini_connection()`
records the API key there), so a test that exercises one of those paths would
otherwise leak values into later, unrelated tests.

This autouse fixture snapshots the session-state keys that app code reads back
into the memory store, and restores them after every test — so each test starts
from a clean slate regardless of execution order or which other tests ran first.
"""

from __future__ import annotations

import threading

import pytest

import axiom_account_codes
import axiom_core
import axiom_db
import axiom_entities
import axiom_properties
import axiom_rules
import axiom_state
import utils

# Keys that `_save_memory_store()` merges from st.session_state into the store,
# plus the store itself. Any test touching memory-store / LLM-settings paths can
# leave these mutated; we snapshot and restore them around every test.
_SESSION_KEYS = (
    "_axiom_memory_store",
    "entity_options",
    "entity_jurisdictions",
    "tax_year_options",
    "active_entity",
    "active_tax_year",
    "google_genai_api_key",
    "categorization_model",
    "local_llm_url",
    "local_llm_model",
    "gemini_connection_status",
)

_MISSING = object()


@pytest.fixture(autouse=True)
def _isolate_secrets_dir(monkeypatch, tmp_path):
    """Never let tests read or write the user's real Gemini key file (axiom_secrets)."""
    monkeypatch.setenv("AXIOM_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _isolate_streamlit_session_state():
    """Snapshot/restore the memory-store-related st.session_state keys around each test."""
    try:
        import streamlit as st

        # Probe that session_state behaves like a mapping in this (bare) mode.
        st.session_state.get("_axiom_probe", None)
    except Exception:
        yield
        return

    saved = {key: st.session_state.get(key, _MISSING) for key in _SESSION_KEYS}
    try:
        yield
    finally:
        for key, original in saved.items():
            try:
                if original is _MISSING:
                    st.session_state.pop(key, None)
                else:
                    st.session_state[key] = original
            except Exception:
                pass


@pytest.fixture()
def rules_env(monkeypatch):
    """Isolated in-memory store + deterministic stubs for every external lookup
    used by ``axiom_rules`` (no disk, SQLite or real user data)."""
    store = {
        "memorized_rules": {},
        "entity_account_codes": {},
        "entity_properties": {},
    }
    saved: list[dict] = []
    codes = ["Insurance", "Loan Interest", "Rates"]
    entity_rules: dict[str, str] = {}
    portfolio_codes: set[str] = set()
    detected: dict[str, str] = {}

    monkeypatch.setattr(axiom_rules, "get_active_entity", lambda entity=None: entity or "Test Entity")
    monkeypatch.setattr(axiom_rules, "_load_memory_store", lambda: store)
    monkeypatch.setattr(axiom_rules, "_save_memory_store", lambda s: saved.append(s))
    # Since phase D6 the rule engine imports these helpers directly from
    # axiom_account_codes / axiom_properties, so patch them on axiom_rules.
    monkeypatch.setattr(axiom_rules, "get_entity_account_codes", lambda entity=None: list(codes))
    monkeypatch.setattr(
        axiom_rules,
        "detect_property_from_text",
        lambda text, entity=None: detected.get((text or "").strip().lower(), axiom_core.UNASSIGNED_PROPERTY),
    )
    monkeypatch.setattr(axiom_rules, "get_entity_rules", lambda entity=None: dict(entity_rules))
    monkeypatch.setattr(axiom_rules, "is_portfolio_wide_code", lambda code, entity=None: code in portfolio_codes)
    monkeypatch.setattr(
        utils, "is_property_unassigned", lambda prop, code=None: prop == axiom_core.UNASSIGNED_PROPERTY
    )
    monkeypatch.setattr(axiom_rules, "match_loan_from_text", lambda text, entity=None: None)

    return {
        "store": store,
        "saved": saved,
        "codes": codes,
        "entity_rules": entity_rules,
        "portfolio_codes": portfolio_codes,
        "detected": detected,
    }


# Session-state keys the entity/property CRUD fixtures write that the autouse
# snapshot above does not cover.
_ENTITY_SESSION_KEYS = (
    "entity",
    "entity_ledgers",
    "entity_year_ledgers",
    "entity_uploaded_files",
    "entity_year_files",
)


@pytest.fixture()
def entities_env(tmp_path, monkeypatch):
    """Isolated temp SQLite DB + File Vault and in-memory memory store for the
    entity/property CRUD modules (``axiom_entities`` / ``axiom_properties``).

    No real ``data/`` dirs, no real ``.axiom_memory.json`` and no real session
    data are touched: the store is a plain dict, writes are recorded rather than
    persisted, and ``st.session_state`` starts from a known two-entity workspace.
    """
    import streamlit as st

    data_dir = tmp_path / "data"
    vault_root = data_dir / "vault"
    conn_cache: dict = {}
    monkeypatch.setattr(axiom_db, "DATA_DIR", data_dir)
    monkeypatch.setattr(axiom_db, "SQLITE_DB_PATH", data_dir / "axiom_ledger.db")
    monkeypatch.setattr(axiom_db, "VAULT_ROOT_DIR", vault_root)
    monkeypatch.setattr(axiom_db, "_SCHEMA_READY", threading.Event())
    monkeypatch.setattr(axiom_db, "_CONN_CACHE", conn_cache)
    monkeypatch.setattr(axiom_entities, "VAULT_ROOT_DIR", vault_root)

    store: dict = {
        "entity_options": ["Alpha", "Beta"],
        "entity_jurisdictions": {},
        "entity_properties": {},
        "memorized_rules": {},
        "entity_code_scopes": {},
        "entity_account_codes": {},
        "entity_property_aliases": {},
    }
    saved: list = []
    for module in (axiom_account_codes, axiom_entities, axiom_properties):
        monkeypatch.setattr(module, "_load_memory_store", lambda: store)
        monkeypatch.setattr(module, "_save_memory_store", lambda s=None: saved.append(s))
    monkeypatch.setattr(axiom_state, "_load_memory_store", lambda: store)

    saved_state = {key: st.session_state.get(key, _MISSING) for key in _ENTITY_SESSION_KEYS}
    st.session_state["entity_options"] = ["Alpha", "Beta"]
    st.session_state["entity_jurisdictions"] = {"Alpha": "NZ", "Beta": "US"}
    st.session_state["tax_year_options"] = list(axiom_core.TAX_YEAR_OPTIONS)
    st.session_state["active_entity"] = "Alpha"
    st.session_state["entity"] = "Alpha"
    st.session_state["active_tax_year"] = "2026"
    st.session_state["entity_ledgers"] = {}
    st.session_state["entity_year_ledgers"] = {}
    st.session_state["entity_uploaded_files"] = {}
    st.session_state["entity_year_files"] = {}

    try:
        yield {
            "store": store,
            "saved": saved,
            "vault_root": vault_root,
        }
    finally:
        for conn in list(conn_cache.values()):
            try:
                conn.close()
            except Exception:
                pass
        for key, original in saved_state.items():
            try:
                if original is _MISSING:
                    st.session_state.pop(key, None)
                else:
                    st.session_state[key] = original
            except Exception:
                pass
