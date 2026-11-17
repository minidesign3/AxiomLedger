"""
axiom_ui_chrome
===============

Workspace chrome (phase D22): the top workspace header (entity + year pills,
AI status badges, settings toggle) and the left-sidebar workspace switcher and
session/sign-out panel.
"""

from __future__ import annotations

import streamlit as st
import streamlit_authenticator as stauth

from axiom_auth import (
    get_authenticator,
    sign_out_current_session,
    sync_and_touch_auth_session,
)
from axiom_core import CATEGORIZATION_MODEL_OPTIONS
from axiom_entities import add_new_tax_year
from axiom_ledger import get_entity_ledger
from axiom_loan_crud import run_loan_reminder_check_once_per_session
from axiom_memory import _save_memory_store
from axiom_state import (
    format_tax_year_period,
    get_active_entity,
    get_active_tax_year,
    get_entity_jurisdiction,
    get_entity_options,
    get_tax_year_options,
    init_workspace_state,
)
from axiom_ui_components import _render_connection_badge, render_global_status_indicator
from axiom_ui_settings import render_right_settings_drawer
from axiom_ui_styles import apply_custom_styles


def render_workspace_header(show_status_indicator: bool = False) -> None:
    """
    Render the Xero/QuickBooks-style Top Workspace Toolbar:
    1. Left: Active Entity Banner, Jurisdiction Tag (NZ IRD vs US IRS Schedule E), and Financial Year Context
    2. Center/Bottom Bar: Single-Year Financial Year Pill Bar (`2026`, `2027`, `2028`, `2029`, `+ Add Year`)
    3. Right: Live AI Connection Status Badges + `⚙ AI & System Settings` button (opens right-hand drawer)
       and optional Background Thread Status Indicator.
    If Settings is open (`show_settings_drawer` is True), exclusively render the Settings view on the
    right-hand main canvas and halt any further page rendering so nothing else is displayed.
    """
    init_workspace_state()

    # Exclusive Settings View: when Settings is open, show ONLY Settings on the right-hand side
    if st.session_state.get("show_settings_drawer", False):
        render_right_settings_drawer()
        st.stop()

    active_entity = get_active_entity()
    active_tax_year = get_active_tax_year()
    jurisdiction = get_entity_jurisdiction(active_entity)
    jurisdiction_badge = "USA IRS" if jurisdiction == "US" else "NZ IRD"
    period_str = format_tax_year_period(active_tax_year, active_entity)

    active_model = st.session_state.get("categorization_model", CATEGORIZATION_MODEL_OPTIONS[0])
    gem_status = st.session_state.get("gemini_connection_status", {})
    loc_status = st.session_state.get("local_ai_connection_status", {})

    # Top Row: Workspace Context Banner | AI Status Badges & Settings Button | Background Telemetry
    if show_status_indicator:
        banner_col, ai_col, bg_col = st.columns([2.2, 1.15, 0.95])
    else:
        banner_col, ai_col = st.columns([2.7, 1.3])
        bg_col = None

    with banner_col:
        st.markdown(
            f"""
            <div class="axiom-workspace-banner">
                <div style="display: flex; align-items: center; justify-content: space-between;">
                    <span class="axiom-eyebrow">ORGANIZATION WORKSPACE // TAX PERIOD: {period_str.upper()}</span>
                    <span style="
                        font-size: 0.68rem;
                        font-weight: 600;
                        letter-spacing: 0.1em;
                        color: #E2E4E9;
                        background-color: #24272E;
                        border: 1px solid #3A3F4B;
                        border-radius: 3px;
                        padding: 0.1rem 0.5rem;
                    ">{jurisdiction_badge}</span>
                </div>
                <h2>{active_entity} &mdash; {active_tax_year} ({period_str})</h2>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with ai_col:
        badge_c1, badge_c2 = st.columns(2)
        with badge_c1:
            gem_html = _render_connection_badge(
                "GEMINI",
                gem_status,
                is_selected=(active_model == "Gemini 3.8 Flash (Cloud)"),
            )
            st.markdown(gem_html, unsafe_allow_html=True)
        with badge_c2:
            loc_html = _render_connection_badge(
                "LOCAL AI",
                loc_status,
                is_selected=(active_model == "Local Qwen 9B (Air-gapped)"),
            )
            st.markdown(loc_html, unsafe_allow_html=True)

        drawer_open = st.session_state.get("show_settings_drawer", False)
        settings_btn_label = "⚙ Hide Settings" if drawer_open else "⚙ Settings (Orgs, AI & Accounts)"
        if st.button(
            settings_btn_label,
            key="_top_toolbar_settings_btn",
            type="primary" if drawer_open else "secondary",
            use_container_width=True,
        ):
            st.session_state["show_settings_drawer"] = not drawer_open
            st.rerun()

    if bg_col is not None:
        with bg_col:
            render_global_status_indicator()

    # Single-Year Financial Year Pill Bar (2026 | 2027 | 2028 | 2029 | + Add Year)
    years = get_tax_year_options()
    year_bar_cols = st.columns([0.9] + [0.65 for _ in years] + [1.1, 1.8])
    with year_bar_cols[0]:
        st.markdown(
            '<div style="padding-top: 0.48rem;" class="axiom-eyebrow">FINANCIAL YEAR:</div>',
            unsafe_allow_html=True,
        )

    for idx, yr in enumerate(years):
        with year_bar_cols[idx + 1]:
            is_current_yr = str(yr) == str(active_tax_year)
            btn_label = f"● {yr}" if is_current_yr else str(yr)
            if st.button(
                btn_label,
                key=f"_fy_pill_btn_{yr}",
                type="primary" if is_current_yr else "secondary",
                use_container_width=True,
            ):
                st.session_state["active_tax_year"] = str(yr)
                st.rerun()

    with year_bar_cols[len(years) + 1]:
        with st.popover("+ Add Year", use_container_width=True):
            st.markdown('<div class="axiom-eyebrow">ADD FINANCIAL YEAR</div>', unsafe_allow_html=True)
            suggested_next = str(max(int(y) for y in years if y.isdigit()) + 1) if years else "2028"
            new_yr_input = st.text_input(
                "4-Digit Year",
                value=suggested_next,
                max_chars=4,
                key="_popover_new_tax_year_input",
            )
            if st.button("Add & Switch Year", key="_btn_confirm_add_year", use_container_width=True):
                ok, msg = add_new_tax_year(new_yr_input)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)


def render_sidebar_workspace_controls() -> None:
    """Backwards-compatible alias for workspace sidebar controls."""
    init_workspace_state()


def render_sidebar_session(authenticator: stauth.Authenticate) -> None:
    """
    Render the Xero/QuickBooks-style Organization Switcher in the left sidebar:
    - Dedicated one-click Entity Workspace cards/buttons (no clumsy selectbox dropdown)
    - '+ Add New Entity' inline creator with Tax Jurisdiction selection (NZ IRD vs US IRS Schedule E)
    - Quick Financial Year summary & Settings shortcut
    - Operator metadata & Sign Out button
    """
    init_workspace_state()

    # If the user clicked a different page in the left-hand Streamlit navigation menu,
    # automatically close the Settings view so the selected page is shown.
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        ctx = get_script_run_ctx()
        current_page = getattr(ctx, "main_script_path", "") or getattr(ctx, "page_script_hash", "")
    except Exception:
        current_page = ""

    last_page = st.session_state.get("_last_rendered_page")
    if current_page and last_page and current_page != last_page:
        st.session_state["show_settings_drawer"] = False
    if current_page:
        st.session_state["_last_rendered_page"] = current_page

    active_entity = get_active_entity()
    active_tax_year = get_active_tax_year()
    drawer_open = st.session_state.get("show_settings_drawer", False)

    with st.sidebar:
        st.markdown('<div class="axiom-eyebrow">AXIOM LEDGER // ORGANIZATIONS</div>', unsafe_allow_html=True)
        st.caption("Select an organization (edit names in ⚙ Settings):")

        for idx, ent in enumerate(get_entity_options()):
            is_active = (ent == active_entity) and not drawer_open
            jur = get_entity_jurisdiction(ent)
            jur_tag = "USA IRS" if jur == "US" else "NZ IRD"
            ent_df = get_entity_ledger(ent, active_tax_year)
            row_count = len(ent_df)
            prefix = "●" if is_active else "○"
            btn_text = f"{prefix} {ent} [{jur_tag}] ({row_count})"
            if st.button(
                btn_text,
                key=f"_sidebar_org_btn_{idx}_{ent}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state["active_entity"] = ent
                st.session_state["entity"] = ent
                st.session_state["show_settings_drawer"] = False
                _save_memory_store()
                st.rerun()

        st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="axiom-eyebrow">SYSTEM CONFIGURATION</div>', unsafe_allow_html=True)

        if st.button(
            "⚙ Settings (Orgs, AI & Security)" if not drawer_open else "⚙ Close Settings",
            key="_sidebar_open_settings_btn",
            type="primary" if drawer_open else "secondary",
            use_container_width=True,
        ):
            st.session_state["show_settings_drawer"] = not drawer_open
            st.rerun()

        st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="axiom-eyebrow">AXIOM LEDGER // CORE</div>', unsafe_allow_html=True)
        st.markdown(
            f"**Operator:** `{st.session_state.get('name', 'System Administrator')}`  \n"
            f"**Clearance:** `LEVEL-1 (ADMIN)`  \n"
            f"**Auto-Logout:** `60m Inactivity`"
        )
        if st.button("Sign Out", key="_sidebar_explicit_signout_btn", use_container_width=True):
            sign_out_current_session(authenticator)
            st.switch_page("app.py")


def require_auth() -> stauth.Authenticate:
    """
    Enforce authentication across all pages with a 60-minute sliding inactivity timeout.
    Redirects to the main login gateway (`app.py`) if the user is not authenticated.
    Renders persistent sidebar controls when authenticated.
    """
    authenticator = get_authenticator()
    auth_status = sync_and_touch_auth_session(authenticator)

    apply_custom_styles(authenticated=bool(auth_status))
    if auth_status:
        try:
            run_loan_reminder_check_once_per_session()
        except Exception:
            pass

    if not auth_status:
        st.switch_page("app.py")
        st.stop()

    render_sidebar_session(authenticator)
    return authenticator


