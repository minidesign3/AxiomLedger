"""
axiom_llm
=========

LLM connectivity verification (Gemini cloud API + local OpenAI-compatible
endpoint) extracted from the monolithic ``utils.py``.

Both functions here do real network I/O (a lightweight "list models" ping),
and update ``st.session_state`` with the result. This is the first slice of
the LLM categorization engine to be extracted; the deeper categorization
pipeline (``guess_ird_category_*``, ``categorize_description_dynamic``,
``apply_rule_categorization``, ``apply_ai_fallback_categorization``) remains
in ``utils.py`` for a future slice — it pulls in ``get_entity_jurisdiction``,
``match_memorized_or_rule``, ``match_rule_category``, and the long
jurisdiction system-prompt constants, which is considerably more surface area.

``verify_gemini_connection`` persists the verified API key via
``_save_memory_store``, which lives in ``utils.py`` (session-state-coupled).
To avoid a circular import, it is accessed via a **local** ``import utils``
inside the function body — never at this module's top level (see
``axiom_loan_crud.py``'s module docstring for why that matters).

``utils.py`` imports and re-exports both functions, so existing
``from utils import ...`` call sites keep working unchanged.
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime

import streamlit as st

try:
    from google import genai
except ImportError:
    genai = None  # type: ignore[assignment]

from axiom_core import DEFAULT_LOCAL_LLM_URL, GEMINI_OCR_MODEL


def verify_gemini_connection(api_key: str | None = None) -> dict[str, object]:
    """
    Actively test and verify connection to Google Gemini API using the provided API key.
    Updates st.session_state['gemini_connection_status'].
    """
    resolved_key = (api_key if api_key is not None else st.session_state.get("google_genai_api_key", "")).strip()
    if not resolved_key:
        status = {
            "connected": False,
            "label": "NO API KEY",
            "detail": "Enter a Google Gemini API Key to connect.",
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }
        st.session_state["gemini_connection_status"] = status
        return status

    start_ts = time.perf_counter()
    try:
        client = genai.Client(api_key=resolved_key)
        # Lightweight verification ping to confirm key validity
        models_pager = client.models.list()
        _ = next(iter(models_pager), None)
        latency_ms = int((time.perf_counter() - start_ts) * 1000)
        status = {
            "connected": True,
            "label": f"CONNECTED ({latency_ms}ms)",
            "detail": f"Verified Google GenAI endpoint ({GEMINI_OCR_MODEL} ready)",
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as exc:
        err_msg = str(exc).splitlines()[0][:90]
        status = {
            "connected": False,
            "label": "AUTH / CONNECTION ERROR",
            "detail": f"Gemini check failed: {err_msg}",
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }

    st.session_state["google_genai_api_key"] = resolved_key
    st.session_state["gemini_connection_status"] = status

    import utils

    utils._save_memory_store()
    return status


def verify_local_llm_connection(local_llm_url: str | None = None) -> dict[str, object]:
    """
    Actively test and verify connection to the Local AI (Ollama / OpenAI-compatible) server.
    Updates st.session_state['local_ai_connection_status'].
    """
    base_url = (
        local_llm_url
        if local_llm_url is not None
        else st.session_state.get("local_llm_url", DEFAULT_LOCAL_LLM_URL)
    ).strip()

    if not base_url:
        status = {
            "connected": False,
            "label": "NO ENDPOINT URL",
            "detail": "Configure a Local AI URL (e.g. http://localhost:11434/v1).",
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }
        st.session_state["local_ai_connection_status"] = status
        return status

    models_url = base_url.rstrip("/") + "/models"
    start_ts = time.perf_counter()
    try:
        req = urllib.request.Request(models_url, method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            latency_ms = int((time.perf_counter() - start_ts) * 1000)
            payload = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
            model_list = payload.get("data", []) if isinstance(payload, dict) else []
            model_count = len(model_list) if isinstance(model_list, list) else 0
            status = {
                "connected": True,
                "label": f"CONNECTED ({latency_ms}ms)",
                "detail": f"Reachable at {base_url} ({model_count} model(s) online)",
                "checked_at": datetime.now().strftime("%H:%M:%S"),
            }
    except Exception as exc:
        err_short = str(exc).splitlines()[0][:80]
        status = {
            "connected": False,
            "label": "OFFLINE / UNREACHABLE",
            "detail": f"{base_url} unreachable ({err_short})",
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }

    st.session_state["local_ai_connection_status"] = status
    return status
