"""
axiom_state
===========

Workspace/session state (Streamlit) extracted from the monolithic ``utils.py``
god-module.

Holds the *resolution* layer for the active entity / financial year /
jurisdiction, the human-readable tax-year period label, and the one-shot
workspace bootstrap (``init_workspace_state``). Reference data itself lives in
``axiom_memory``; this module only reads it.

``utils.py`` imports and re-exports these symbols, so all existing
``from utils import ...`` call sites keep working unchanged.
"""

from __future__ import annotations

import threading
from datetime import datetime

import pandas as pd
import streamlit as st

from axiom_core import (
    BACKGROUND_TASK_CATALOG,
    CATEGORIZATION_MODEL_OPTIONS,
    DEFAULT_ENTITY_JURISDICTIONS,
    DEFAULT_LOCAL_LLM_MODEL,
    DEFAULT_LOCAL_LLM_URL,
    ENTITY_OPTIONS,
    STANDARD_COLUMNS,
    TAX_YEAR_OPTIONS,
)
from axiom_memory import _load_memory_store


def get_entity_options() -> list[str]:
    """Return the dynamic list of configured entities (organizations), persisted in .axiom_memory.json."""
    store = _load_memory_store()
    saved_opts = store.get("entity_options")
    if isinstance(saved_opts, list) and saved_opts:
        st.session_state["entity_options"] = [str(x) for x in saved_opts if str(x).strip()]
    elif "entity_options" not in st.session_state:
        st.session_state["entity_options"] = list(ENTITY_OPTIONS)
    return st.session_state["entity_options"]


def get_tax_year_options() -> list[str]:
    """Return the dynamic list of single financial years (e.g. 2026, 2027, 2028, 2029)."""
    if "tax_year_options" not in st.session_state:
        st.session_state["tax_year_options"] = list(TAX_YEAR_OPTIONS)
    return st.session_state["tax_year_options"]


def get_active_entity(entity: str | None = None) -> str:
    """Resolve the active entity from explicit argument or st.session_state.entity / active_entity."""
    if entity:
        return entity
    options = get_entity_options()
    default_entity = options[0] if options else ENTITY_OPTIONS[0]
    return (
        st.session_state.get("entity")
        or st.session_state.get("active_entity")
        or default_entity
    )


def get_active_tax_year(tax_year: str | None = None) -> str:
    """Resolve the active financial year from explicit argument or st.session_state.active_tax_year."""
    if tax_year:
        return str(tax_year)
    years = get_tax_year_options()
    default_year = "2026" if "2026" in years else (years[0] if years else "2026")
    return str(st.session_state.get("active_tax_year") or default_year)


def get_entity_jurisdiction(entity: str | None = None) -> str:
    """Return 'US' or 'NZ' for the given entity (supports dynamically added entities)."""
    resolved_entity = get_active_entity(entity)
    jurisdictions = st.session_state.get("entity_jurisdictions", DEFAULT_ENTITY_JURISDICTIONS)
    if resolved_entity in jurisdictions:
        return jurisdictions[resolved_entity]
    return "NZ"


def format_tax_year_period(tax_year: str | None = None, entity: str | None = None) -> str:
    """
    Format the explicit financial year date window according to jurisdiction:
    - NZ (IRD): 1 Apr (Y-1) – 31 Mar Y (e.g., 2026 -> 1 Apr 2025 – 31 Mar 2026)
    - US (IRS): 1 Jan Y – 31 Dec Y (e.g., 2026 -> 1 Jan 2026 – 31 Dec 2026)
    """
    yr_str = str(get_active_tax_year(tax_year)).strip()
    jur = get_entity_jurisdiction(entity)
    try:
        yr_int = int(yr_str[:4])
        if jur == "US":
            return f"1 Jan {yr_int} – 31 Dec {yr_int}"
        return f"1 Apr {yr_int - 1} – 31 Mar {yr_int}"
    except Exception:
        return f"FY {yr_str}"


def init_workspace_state() -> None:
    """Initialize global workspace state variables, dynamic entities/years, and background task manager."""
    import os

    store = _load_memory_store()

    saved_opts = store.get("entity_options")
    if isinstance(saved_opts, list) and saved_opts:
        st.session_state["entity_options"] = [str(x) for x in saved_opts if str(x).strip()]
    elif "entity_options" not in st.session_state:
        st.session_state["entity_options"] = list(ENTITY_OPTIONS)

    saved_jurs = store.get("entity_jurisdictions")
    merged_jurs = dict(DEFAULT_ENTITY_JURISDICTIONS)
    if isinstance(saved_jurs, dict):
        for k, v in saved_jurs.items():
            merged_jurs[str(k)] = "US" if str(v).upper().startswith("US") else "NZ"
    st.session_state["entity_jurisdictions"] = merged_jurs

    if "tax_year_options" not in st.session_state:
        st.session_state["tax_year_options"] = list(TAX_YEAR_OPTIONS)
    else:
        filtered_years = [str(y) for y in st.session_state["tax_year_options"] if str(y) not in ("2024", "2025")]
        for yr in TAX_YEAR_OPTIONS:
            if yr not in filtered_years:
                filtered_years.append(yr)
        filtered_years.sort()
        st.session_state["tax_year_options"] = filtered_years

    if "active_entity" not in st.session_state or st.session_state["active_entity"] not in st.session_state["entity_options"]:
        saved_active = str(store.get("active_entity") or "").strip()
        if saved_active and saved_active in st.session_state["entity_options"]:
            st.session_state["active_entity"] = saved_active
        else:
            st.session_state["active_entity"] = st.session_state["entity_options"][0]

    if "entity" not in st.session_state:
        st.session_state["entity"] = st.session_state["active_entity"]
    else:
        st.session_state["entity"] = st.session_state["active_entity"]

    # Normalize any legacy range year or deprecated year (2024, 2025) to "2026"
    if "active_tax_year" not in st.session_state:
        st.session_state["active_tax_year"] = "2026" if "2026" in st.session_state["tax_year_options"] else st.session_state["tax_year_options"][0]
    else:
        curr_yr = str(st.session_state["active_tax_year"])
        if "–" in curr_yr or "-" in curr_yr:
            curr_yr = curr_yr[:4]
        if curr_yr in ("2024", "2025") or curr_yr not in st.session_state["tax_year_options"]:
            curr_yr = "2026" if "2026" in st.session_state["tax_year_options"] else st.session_state["tax_year_options"][0]
        st.session_state["active_tax_year"] = curr_yr

    saved_cat_model = str(store.get("categorization_model") or "").strip()
    if "categorization_model" not in st.session_state:
        st.session_state["categorization_model"] = (
            saved_cat_model if saved_cat_model in CATEGORIZATION_MODEL_OPTIONS else CATEGORIZATION_MODEL_OPTIONS[0]
        )

    saved_gem_key = str(store.get("google_genai_api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if "google_genai_api_key" not in st.session_state or not str(st.session_state.get("google_genai_api_key") or "").strip():
        st.session_state["google_genai_api_key"] = saved_gem_key

    saved_loc_url = str(store.get("local_llm_url") or DEFAULT_LOCAL_LLM_URL).strip()
    if "local_llm_url" not in st.session_state:
        st.session_state["local_llm_url"] = saved_loc_url

    saved_loc_model = str(store.get("local_llm_model") or DEFAULT_LOCAL_LLM_MODEL).strip()
    if "local_llm_model" not in st.session_state:
        st.session_state["local_llm_model"] = saved_loc_model

    if "show_settings_drawer" not in st.session_state:
        st.session_state["show_settings_drawer"] = False

    if "gemini_connection_status" not in st.session_state:
        saved_gem_status = store.get("gemini_connection_status")
        has_key = bool(st.session_state.get("google_genai_api_key", "").strip())
        if has_key and isinstance(saved_gem_status, dict) and saved_gem_status.get("label"):
            st.session_state["gemini_connection_status"] = dict(saved_gem_status)
        else:
            st.session_state["gemini_connection_status"] = {
                "connected": False,
                "label": "KEY SAVED (READY)" if has_key else "NOT CONFIGURED",
                "detail": "Saved permanently to local vault. Click 'Test & Verify Connection' to ping." if has_key else "Click 'Test & Verify Connection' in Settings to validate.",
                "checked_at": "—",
            }

    if "local_ai_connection_status" not in st.session_state:
        st.session_state["local_ai_connection_status"] = {
            "connected": False,
            "label": "UNVERIFIED",
            "detail": f"Configured endpoint: {st.session_state.get('local_llm_url', DEFAULT_LOCAL_LLM_URL)}",
            "checked_at": "—",
        }

    # Nested (entity -> year -> DataFrame) store for strict entity + financial year isolation
    if "entity_year_ledgers" not in st.session_state:
        st.session_state["entity_year_ledgers"] = {}

    for ent in st.session_state["entity_options"]:
        if ent not in st.session_state["entity_year_ledgers"]:
            st.session_state["entity_year_ledgers"][ent] = {}
        for yr in st.session_state["tax_year_options"]:
            if yr not in st.session_state["entity_year_ledgers"][ent]:
                st.session_state["entity_year_ledgers"][ent][yr] = pd.DataFrame(columns=STANDARD_COLUMNS)

    # Keep backwards-compatible st.session_state["entity_ledgers"][entity] synced with active_tax_year
    active_yr = st.session_state["active_tax_year"]
    if "entity_ledgers" not in st.session_state:
        st.session_state["entity_ledgers"] = {
            ent: st.session_state["entity_year_ledgers"][ent][active_yr]
            for ent in st.session_state["entity_options"]
        }
    else:
        for ent in st.session_state["entity_options"]:
            if ent not in st.session_state["entity_ledgers"]:
                st.session_state["entity_ledgers"][ent] = st.session_state["entity_year_ledgers"][ent][active_yr]

    if "background_task_logs" not in st.session_state:
        ts = datetime.now().strftime("%H:%M:%S")
        st.session_state["background_task_logs"] = [
            f"[{ts}] [SYSTEM] Axiom Background Task Supervisor initialized. All worker queues Online."
        ]

    if "background_tasks" not in st.session_state:
        st.session_state["background_tasks"] = {}

    for task_id, meta in BACKGROUND_TASK_CATALOG.items():
        if task_id not in st.session_state["background_tasks"]:
            st.session_state["background_tasks"][task_id] = {
                "id": task_id,
                "name": meta["name"],
                "description": meta["description"],
                "status": "Online",  # Online | Running | Paused | Error
                "thread": None,
                "stop_event": threading.Event(),
                "pause_event": threading.Event(),
                "error_event": threading.Event(),
                "iterations": 0,
                "last_heartbeat": "—",
            }
        else:
            task_entry = st.session_state["background_tasks"][task_id]
            worker = task_entry.get("thread")
            if task_entry["status"] in ("Running", "Paused") and (worker is None or not worker.is_alive()):
                if task_entry["error_event"].is_set():
                    task_entry["status"] = "Error"
                else:
                    task_entry["status"] = "Online"

