"""
axiom_ui_settings_ai
====================

Settings drawer tab 05 — AI & API connections (Gemini cloud + local
air-gapped Ollama/Qwen endpoints).
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from axiom_core import (
    CATEGORIZATION_MODEL_OPTIONS,
    DEFAULT_LOCAL_LLM_MODEL,
    DEFAULT_LOCAL_LLM_URL,
)
from axiom_llm import verify_gemini_connection, verify_local_llm_connection
from axiom_memory import _save_memory_store


def render_ai_tab() -> None:
    st.markdown(
        '<div class="axiom-eyebrow">DEFAULT CATEGORIZATION &amp; FALLBACK ROUTING ENGINE</div>',
        unsafe_allow_html=True,
    )
    selected_cat_model = st.radio(
        "Select Active AI Categorization Engine",
        options=CATEGORIZATION_MODEL_OPTIONS,
        index=CATEGORIZATION_MODEL_OPTIONS.index(st.session_state["categorization_model"])
        if st.session_state["categorization_model"] in CATEGORIZATION_MODEL_OPTIONS
        else 0,
        horizontal=True,
        label_visibility="collapsed",
        key="_settings_drawer_cat_model_radio",
    )
    if selected_cat_model != st.session_state.get("categorization_model"):
        st.session_state["categorization_model"] = selected_cat_model
        _save_memory_store()

    st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)

    gem_col, loc_col = st.columns(2)

    # -----------------------------------------------------------------
    # PROVIDER 1: GOOGLE GEMINI API (CLOUD)
    # -----------------------------------------------------------------
    with gem_col:
        gem_status = st.session_state.get("gemini_connection_status", {})
        gem_connected = bool(gem_status.get("connected", False))
        gem_badge_border = "#A0A5B1" if gem_connected else "#3A3F4B"
        gem_dot = "● CONNECTED" if gem_connected else f"○ {gem_status.get('label', 'DISCONNECTED')}"

        st.markdown(
            f"""
            <div class="axiom-panel" style="padding: 0.9rem 1.1rem; margin-bottom: 0.65rem; border-color: {gem_badge_border};">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span class="axiom-eyebrow" style="margin: 0;">01 // GOOGLE GEMINI API (CLOUD)</span>
                    <span style="font-size: 0.72rem; font-family: monospace; color: {'#F3F4F6' if gem_connected else '#8E939F'}; font-weight: 600;">
                        {gem_dot}
                    </span>
                </div>
                <div style="font-size: 0.8rem; color: #8E939F; margin-top: 0.25rem;">
                    {gem_status.get('detail', 'Used for Gemini 3.8 Flash Categorization & Multimodal OCR.')}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        gem_flash = st.session_state.pop("_gemini_key_flash", None)
        if isinstance(gem_flash, tuple) and len(gem_flash) == 2:
            if gem_flash[0]:
                st.success(gem_flash[1])
            else:
                st.error(gem_flash[1])

        new_gem_key = st.text_input(
            "Google Gemini API Key (Saved Permanently)",
            value=st.session_state.get("google_genai_api_key", ""),
            type="password",
            placeholder="Paste AIza... Gemini API Key",
            key="_drawer_gemini_api_key_input",
        )
        if new_gem_key != st.session_state.get("google_genai_api_key", ""):
            st.session_state["google_genai_api_key"] = new_gem_key.strip()
            st.session_state["gemini_connection_status"] = {
                "connected": False,
                "label": "KEY SAVED (READY)" if new_gem_key.strip() else "NOT CONFIGURED",
                "detail": "Saved permanently to local database. Click 'Test & Verify' to ping." if new_gem_key.strip() else "Enter a Google Gemini API Key to connect.",
                "checked_at": datetime.now().strftime("%H:%M:%S"),
            }
            _save_memory_store()

        gem_btn_c1, gem_btn_c2 = st.columns(2)
        with gem_btn_c1:
            if st.button("Save Gemini API Key", key="_btn_save_gemini_key", type="primary", use_container_width=True):
                cleaned_gem_key = (new_gem_key or "").strip()
                st.session_state["google_genai_api_key"] = cleaned_gem_key
                st.session_state["gemini_connection_status"] = {
                    "connected": bool(st.session_state.get("gemini_connection_status", {}).get("connected")) and bool(cleaned_gem_key),
                    "label": "KEY SAVED (READY)" if cleaned_gem_key else "NOT CONFIGURED",
                    "detail": "Saved permanently to local SQLite database & vault." if cleaned_gem_key else "API key cleared.",
                    "checked_at": datetime.now().strftime("%H:%M:%S"),
                }
                _save_memory_store()
                st.session_state["_gemini_key_flash"] = (
                    True,
                    "Gemini API key saved permanently for all sessions." if cleaned_gem_key else "Gemini API key cleared.",
                )
                st.rerun()
        with gem_btn_c2:
            if st.button("Test & Verify Connection", key="_btn_verify_gemini", use_container_width=True):
                cleaned_gem_key = (new_gem_key or "").strip()
                st.session_state["google_genai_api_key"] = cleaned_gem_key
                _save_memory_store()
                with st.spinner("Saving key & pinging Google Gemini API..."):
                    res = verify_gemini_connection(cleaned_gem_key)
                st.session_state["_gemini_key_flash"] = (
                    bool(res["connected"]),
                    f"Saved & Connected: {res['detail']}" if res["connected"] else f"Key saved, but test failed: {res['detail']}",
                )
                st.rerun()

    # -----------------------------------------------------------------
    # PROVIDER 2: LOCAL AI (OLLAMA / QWEN AIR-GAPPED)
    # -----------------------------------------------------------------
    with loc_col:
        loc_status = st.session_state.get("local_ai_connection_status", {})
        loc_connected = bool(loc_status.get("connected", False))
        loc_badge_border = "#A0A5B1" if loc_connected else "#3A3F4B"
        loc_dot = "● CONNECTED" if loc_connected else f"○ {loc_status.get('label', 'OFFLINE')}"

        st.markdown(
            f"""
            <div class="axiom-panel" style="padding: 0.9rem 1.1rem; margin-bottom: 0.65rem; border-color: {loc_badge_border};">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span class="axiom-eyebrow" style="margin: 0;">02 // LOCAL AI (AIR-GAPPED)</span>
                    <span style="font-size: 0.72rem; font-family: monospace; color: {'#F3F4F6' if loc_connected else '#8E939F'}; font-weight: 600;">
                        {loc_dot}
                    </span>
                </div>
                <div style="font-size: 0.8rem; color: #8E939F; margin-top: 0.25rem;">
                    {loc_status.get('detail', 'Local Ollama / OpenAI-compatible server.')}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        u_col, m_col = st.columns([1.8, 1.2])
        with u_col:
            new_loc_url = st.text_input(
                "Local AI Endpoint URL",
                value=st.session_state.get("local_llm_url", DEFAULT_LOCAL_LLM_URL),
                placeholder="http://localhost:11434/v1",
                key="_drawer_local_llm_url_input",
            )
            if new_loc_url != st.session_state.get("local_llm_url"):
                st.session_state["local_llm_url"] = new_loc_url
                _save_memory_store()
        with m_col:
            new_loc_model = st.text_input(
                "Model Tag",
                value=st.session_state.get("local_llm_model", DEFAULT_LOCAL_LLM_MODEL),
                placeholder="qwen2.5:9b",
                key="_drawer_local_llm_model_input",
            )
            if new_loc_model != st.session_state.get("local_llm_model"):
                st.session_state["local_llm_model"] = new_loc_model
                _save_memory_store()

        if st.button("Test & Verify Local AI Connection", key="_btn_verify_local_ai", use_container_width=True):
            st.session_state["local_llm_url"] = new_loc_url
            _save_memory_store()
            with st.spinner(f"Testing connection to {new_loc_url}..."):
                res = verify_local_llm_connection(new_loc_url)
            if res["connected"]:
                st.success(f"Local AI Connected: {res['detail']}")
            else:
                st.error(res["detail"])
            st.rerun()

