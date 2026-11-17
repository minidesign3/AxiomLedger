"""
axiom_memory
============

Memory-store persistence extracted from the monolithic ``utils.py`` god-module.

Owns the single source of persisted reference data (entity options,
jurisdictions, financial years, properties, memorized coding rules and AI
settings): a ``kv_settings`` row in SQLite mirrored to the legacy
``.axiom_memory.json`` fallback. The read path is wrapped in ``st.cache_data``
so the SQLite + JSON round-trip happens once per process; every write path
invalidates that cache.

``utils.py`` imports and re-exports these symbols, so all existing
``from utils import ...`` call sites keep working unchanged. No function here
calls back into ``utils``, so there is no circular import.
"""

from __future__ import annotations

import json

import streamlit as st

from axiom_core import (
    CATEGORIZATION_MODEL_OPTIONS,
    DEFAULT_ENTITY_JURISDICTIONS,
    DEFAULT_LOCAL_LLM_MODEL,
    DEFAULT_LOCAL_LLM_URL,
    DEFAULT_PROPERTIES,
    ENTITY_OPTIONS,
    MEMORY_STORE_PATH,
    TAX_YEAR_OPTIONS,
)
from axiom_db import _DB_LOCK, _ensure_sqlite_schema, _get_sqlite_conn


@st.cache_data(show_spinner=False)
def _load_memory_store_from_storage() -> dict[str, object]:
    """
    Read persisted entity options, jurisdictions, properties, and memorized coding
    rules from SQLite (`data/axiom_ledger.db`) with automatic synchronization to
    `.axiom_memory.json`, normalizing defaults.

    Cached: this used to run a full SQLite read + default-normalization + SQLite
    write-back on *every* `_load_memory_store()` call (P3). `st.cache_data` makes
    the DB/JSON round-trip happen once per process and returns a fresh deep copy
    on each call thereafter (safe for callers that mutate their local `store`).
    Any write path that changes the underlying data MUST call
    `_invalidate_memory_store_cache()` (done inside `_save_memory_store`) so the
    next read reflects the change instead of serving stale cached reference data.
    """
    _ensure_sqlite_schema()
    loaded: dict[str, object] | None = None

    # 1. Check SQLite kv_settings first
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            row = conn.execute(
                "SELECT value_json FROM kv_settings WHERE key = 'axiom_memory_store'"
            ).fetchone()
            if row and row["value_json"]:
                try:
                    parsed = json.loads(str(row["value_json"]))
                    if isinstance(parsed, dict):
                        loaded = parsed
                except Exception:
                    loaded = None

    # 2. Fallback / migrate from .axiom_memory.json if SQLite didn't have it yet
    if loaded is None and MEMORY_STORE_PATH.exists():
        try:
            disk_json = json.loads(MEMORY_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(disk_json, dict):
                loaded = disk_json
        except Exception:
            loaded = None

    if isinstance(loaded, dict):
        loaded.setdefault("entity_options", list(ENTITY_OPTIONS))
        loaded.setdefault("entity_jurisdictions", dict(DEFAULT_ENTITY_JURISDICTIONS))
        saved_years = [str(y) for y in (loaded.get("tax_year_options") or TAX_YEAR_OPTIONS) if str(y) not in ("2024", "2025")]
        for yr in TAX_YEAR_OPTIONS:
            if yr not in saved_years:
                saved_years.append(yr)
        saved_years.sort()
        loaded["tax_year_options"] = saved_years
        if str(loaded.get("active_tax_year") or "") in ("2024", "2025", "") or str(loaded.get("active_tax_year")) not in saved_years:
            loaded["active_tax_year"] = "2026"
        loaded.setdefault("entity_properties", {})
        loaded.setdefault("memorized_rules", {})
        loaded.setdefault("google_genai_api_key", "")
        loaded.setdefault("categorization_model", CATEGORIZATION_MODEL_OPTIONS[0])
        loaded.setdefault("local_llm_url", DEFAULT_LOCAL_LLM_URL)
        loaded.setdefault("local_llm_model", DEFAULT_LOCAL_LLM_MODEL)
        loaded.setdefault("gemini_connection_status", None)
        saved_opts = [
            str(x)
            for x in (loaded.get("entity_options") or ENTITY_OPTIONS)
            if str(x).strip() and not str(x).strip().startswith("__")
        ]
        if not saved_opts:
            saved_opts = list(ENTITY_OPTIONS)
        loaded["entity_options"] = saved_opts
        # Ensure SQLite and JSON are both in sync
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_settings (key, value_json) VALUES ('axiom_memory_store', ?)",
                    (json.dumps(loaded),),
                )
        return loaded

    default_store: dict[str, object] = {
        "entity_options": list(ENTITY_OPTIONS),
        "entity_jurisdictions": dict(DEFAULT_ENTITY_JURISDICTIONS),
        "tax_year_options": list(TAX_YEAR_OPTIONS),
        "entity_properties": {
            ent: list(DEFAULT_PROPERTIES) for ent in ENTITY_OPTIONS
        },
        "memorized_rules": {
            ent: [] for ent in ENTITY_OPTIONS
        },
        "google_genai_api_key": "",
        "categorization_model": CATEGORIZATION_MODEL_OPTIONS[0],
        "local_llm_url": DEFAULT_LOCAL_LLM_URL,
        "local_llm_model": DEFAULT_LOCAL_LLM_MODEL,
        "gemini_connection_status": None,
    }
    _save_memory_store_unconditional(default_store)
    return default_store


def _invalidate_memory_store_cache() -> None:
    """Clear the cached memory-store read so the next `_load_memory_store()` call re-reads from SQLite/JSON."""
    _load_memory_store_from_storage.clear()


def _load_memory_store() -> dict[str, object]:
    """
    Load persisted entity options, jurisdictions, properties, and memorized coding rules
    from SQLite (`data/axiom_ledger.db`) with automatic synchronization to `.axiom_memory.json`.
    """
    store = _load_memory_store_from_storage()
    st.session_state["_axiom_memory_store"] = store
    return store


def _save_memory_store_unconditional(target: dict[str, object]) -> None:
    """Write `target` to SQLite `kv_settings` + `.axiom_memory.json` as-is (no session_state merge, no cache invalidation).

    Internal helper used only while bootstrapping a brand-new store from inside
    `_load_memory_store_from_storage()` itself; invalidating the cache there would
    be pointless (the cache entry doesn't exist yet) and session_state isn't
    necessarily initialized yet either.
    """
    serialized = json.dumps(target, indent=2)
    try:
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_settings (key, value_json) VALUES ('axiom_memory_store', ?)",
                    (serialized,),
                )
    except Exception:
        pass
    try:
        MEMORY_STORE_PATH.write_text(serialized, encoding="utf-8")
    except Exception:
        pass


def _save_memory_store(store: dict[str, object] | None = None) -> None:
    """Save entity options, jurisdictions, properties, AI keys/settings, and memorized rules to SQLite (`data/axiom_ledger.db`) and `.axiom_memory.json`."""
    _ensure_sqlite_schema()
    target = store if store is not None else st.session_state.get("_axiom_memory_store", {})
    if "entity_options" in st.session_state:
        target["entity_options"] = list(st.session_state["entity_options"])
    if "entity_jurisdictions" in st.session_state:
        target["entity_jurisdictions"] = dict(st.session_state["entity_jurisdictions"])
    if "tax_year_options" in st.session_state:
        target["tax_year_options"] = list(st.session_state["tax_year_options"])
    if "active_entity" in st.session_state:
        target["active_entity"] = str(st.session_state["active_entity"])
    if "active_tax_year" in st.session_state:
        target["active_tax_year"] = str(st.session_state["active_tax_year"])
    if "google_genai_api_key" in st.session_state:
        target["google_genai_api_key"] = str(st.session_state["google_genai_api_key"] or "").strip()
    if "categorization_model" in st.session_state:
        target["categorization_model"] = str(st.session_state["categorization_model"])
    if "local_llm_url" in st.session_state:
        target["local_llm_url"] = str(st.session_state["local_llm_url"] or DEFAULT_LOCAL_LLM_URL).strip()
    if "local_llm_model" in st.session_state:
        target["local_llm_model"] = str(st.session_state["local_llm_model"] or DEFAULT_LOCAL_LLM_MODEL).strip()
    if "gemini_connection_status" in st.session_state and isinstance(st.session_state["gemini_connection_status"], dict):
        target["gemini_connection_status"] = dict(st.session_state["gemini_connection_status"])
    st.session_state["_axiom_memory_store"] = target
    _save_memory_store_unconditional(target)
    # Any write invalidates the cached read so the next _load_memory_store() call
    # (this session or any other) sees the change instead of stale cached data.
    _invalidate_memory_store_cache()

