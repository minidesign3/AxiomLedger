"""
axiom_ui_status
===============

Compact AI status chip for the workspace header card (plan.md §11–§12).

The chip shows only the active provider name ("Gemini" / "Local AI" /
"Rules only") with a small CSS status dot; the state is encoded in the wrapping
container key (``ws_status_ok`` / ``_idle`` / ``_error``) so ``assets/styles.css``
can colour the dot without inline styles. Detail lives in the tooltip and in a
tidy three-row list inside the popover.
"""

from __future__ import annotations

import html

import streamlit as st

from axiom_core import CATEGORIZATION_MODEL_OPTIONS
from axiom_nav import debug_pages_enabled
from axiom_tasks import get_background_status_summary

GEMINI_MODEL = "Gemini 3.8 Flash (Cloud)"
LOCAL_MODEL = "Local Qwen 9B (Air-gapped)"

STATE_OK = "ok"
STATE_IDLE = "idle"
STATE_ERROR = "error"


def provider_state(status: dict[str, object]) -> str:
    """Classify one provider: connected → ok, explicit failure → error, else idle."""
    if bool(status.get("connected", False)):
        return STATE_OK
    if "ERROR" in str(status.get("label", "")).upper():
        return STATE_ERROR
    return STATE_IDLE


def summarize_status(
    active_model: str,
    gem_status: dict[str, object],
    loc_status: dict[str, object],
    background_overall: str,
) -> tuple[str, str, str]:
    """Return ``(state, chip_label, tooltip)`` for the header status chip."""
    if active_model == GEMINI_MODEL:
        provider, state = "Gemini", provider_state(gem_status)
    elif active_model == LOCAL_MODEL:
        provider, state = "Local AI", provider_state(loc_status)
    else:
        provider, state = "Rules only", STATE_OK

    if background_overall == "Error":
        return STATE_ERROR, provider, "A background job reported an error — click for details"
    if state == STATE_ERROR:
        return STATE_ERROR, provider, f"{provider} connection failed — click for details"
    if state == STATE_IDLE:
        return STATE_IDLE, provider, f"{provider} is not connected — click for details"
    return STATE_OK, provider, f"{provider} connected — click for details"


def _status_row(name: str, state: str, detail: str, *, active: bool = False) -> str:
    active_tag = '<span class="axiom-status-active">active</span>' if active else ""
    return (
        f'<div class="axiom-status-row axiom-status-{state}">'
        f'<span class="axiom-status-dot"></span>'
        f'<span class="axiom-status-name">{html.escape(name)}{active_tag}</span>'
        f'<span class="axiom-status-detail">{html.escape(detail)}</span>'
        f"</div>"
    )


def _background_row(summary: dict[str, object]) -> str:
    overall = str(summary.get("overall", "Online"))
    state = {"Error": STATE_ERROR, "Online": STATE_IDLE}.get(overall, STATE_OK)
    return _status_row("Background jobs", state, str(summary.get("headline", overall)).capitalize())


def _show_background_row(summary: dict[str, object]) -> bool:
    return debug_pages_enabled() or str(summary.get("overall", "Online")) != "Online"


def render_status_popover(show_background: bool = True) -> None:
    """Render the header status chip and its popover (providers + background jobs)."""
    active_model = st.session_state.get("categorization_model", CATEGORIZATION_MODEL_OPTIONS[0])
    gem_status = st.session_state.get("gemini_connection_status", {})
    loc_status = st.session_state.get("local_ai_connection_status", {})
    summary = get_background_status_summary() if show_background else {"overall": "Online"}

    state, label, tooltip = summarize_status(active_model, gem_status, loc_status, str(summary["overall"]))
    with st.container(key=f"ws_status_{state}"):
        with st.popover(label, help=tooltip, use_container_width=False):
            rows = [
                _status_row(
                    "Gemini",
                    provider_state(gem_status),
                    str(gem_status.get("label", "Unverified")).capitalize(),
                    active=active_model == GEMINI_MODEL,
                ),
                _status_row(
                    "Local AI",
                    provider_state(loc_status),
                    str(loc_status.get("label", "Unverified")).capitalize(),
                    active=active_model == LOCAL_MODEL,
                ),
            ]
            if show_background and _show_background_row(summary):
                rows.append(_background_row(summary))
            st.markdown(
                '<div class="axiom-status-list">' + "".join(rows) + "</div>",
                unsafe_allow_html=True,
            )
            if st.button("Open AI settings", key="_status_open_settings_btn", use_container_width=True):
                st.session_state["show_settings_drawer"] = True
                st.rerun()
