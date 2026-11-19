"""
axiom_ui_chrome
===============

Workspace chrome (phase D22, slimmed in plan.md §11): the one-line workspace
header (entity · jurisdiction · financial year + a single "● AI ▾" status
popover) and the left sidebar (organization switcher, financial-year selector,
the only Settings button, and a compact operator / sign-out footer).

Page navigation itself is ``st.navigation`` in ``app.py`` (see ``axiom_nav``).
"""

from __future__ import annotations

import html

import streamlit as st
import streamlit_authenticator as stauth

from axiom_auth import (
    get_authenticator,
    sign_out_current_session,
    sync_and_touch_auth_session,
)
from axiom_entities import add_new_tax_year
from axiom_ledger import get_entity_ledger
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
from axiom_ui_settings import render_right_settings_drawer
from axiom_ui_status import render_status_popover
from axiom_ui_styles import apply_custom_styles

YEAR_SELECT_KEY = "_sidebar_fy_select"


def render_workspace_header(show_status_indicator: bool = False) -> None:
    """
    Render the slim workspace header: ``Entity · NZ IRD · FY (period)`` on the left
    and one "● AI ▾" status popover (AI providers + background jobs) on the right.
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
    jurisdiction_badge = "USA IRS" if get_entity_jurisdiction(active_entity) == "US" else "NZ IRD"
    period_str = format_tax_year_period(active_tax_year, active_entity)

    # One header card (styled via `.st-key-ws_header` in assets/styles.css) that
    # holds both the workspace title and the compact AI status chip.
    with st.container(key="ws_header"):
        banner_col, status_col = st.columns([4.0, 1.0], vertical_alignment="center")
        with banner_col:
            st.markdown(
                f"""
                <div class="axiom-header-title">
                    <span class="axiom-eyebrow">{jurisdiction_badge} &middot; TAX PERIOD {period_str.upper()}</span>
                    <h2>{active_entity} &mdash; FY {active_tax_year}</h2>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with status_col:
            render_status_popover(show_background=show_status_indicator)


def render_sidebar_workspace_controls() -> None:
    """Backwards-compatible alias for workspace sidebar controls."""
    init_workspace_state()


def _close_settings_on_page_change(current_page: str | None) -> None:
    """Close Settings when the operator picks a different page in the navigation."""
    if not current_page:
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx

            ctx = get_script_run_ctx()
            current_page = getattr(ctx, "page_script_hash", "") or getattr(ctx, "main_script_path", "")
        except Exception:
            current_page = ""

    last_page = st.session_state.get("_last_rendered_page")
    if current_page and last_page and current_page != last_page:
        st.session_state["show_settings_drawer"] = False
    if current_page:
        st.session_state["_last_rendered_page"] = current_page


def _on_year_selected() -> None:
    st.session_state["active_tax_year"] = str(st.session_state[YEAR_SELECT_KEY])


def _render_year_selector(active_tax_year: str) -> None:
    years = [str(y) for y in get_tax_year_options()]
    if str(active_tax_year) not in years:
        years.append(str(active_tax_year))
    # Keep the widget in sync when the year changes elsewhere (e.g. "+" add year).
    st.session_state[YEAR_SELECT_KEY] = str(active_tax_year)

    _sb_label("Financial year")
    year_col, add_col = st.columns([3.0, 1.0], vertical_alignment="bottom")
    with year_col:
        st.selectbox(
            "Financial year",
            options=years,
            key=YEAR_SELECT_KEY,
            on_change=_on_year_selected,
            label_visibility="collapsed",
        )
    with add_col:
        with st.popover("＋", help="Add a financial year", use_container_width=True):
            st.markdown('<div class="axiom-eyebrow">ADD FINANCIAL YEAR</div>', unsafe_allow_html=True)
            numeric = [int(y) for y in years if y.isdigit()]
            suggested_next = str(max(numeric) + 1) if numeric else "2028"
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


def _sb_label(text: str) -> None:
    """Small uppercase section label for the sidebar (styled by `.axiom-sb-label`)."""
    st.markdown(f'<div class="axiom-sb-label">{html.escape(text)}</div>', unsafe_allow_html=True)


def _org_tooltip(ent: str, is_active: bool, active_tax_year: str) -> str:
    jur = "USA IRS" if get_entity_jurisdiction(ent) == "US" else "NZ IRD"
    if is_active:
        # Only the active org's ledger is counted (avoids one query per org per rerun).
        row_count = len(get_entity_ledger(ent, active_tax_year))
        return f"{ent} · {jur} · {row_count:,} rows in FY {active_tax_year}"
    return f"{ent} · {jur} · click to switch"


def _render_org_switcher(active_entity: str, active_tax_year: str, drawer_open: bool) -> None:
    _sb_label("Organizations")
    # Quiet list rows styled like the page links (`.st-key-sb_orgs` in assets/styles.css).
    with st.container(key="sb_orgs"):
        for idx, ent in enumerate(get_entity_options()):
            is_active = (ent == active_entity) and not drawer_open
            if st.button(
                ent,
                key=f"_sidebar_org_btn_{idx}_{ent}",
                icon=":material/domain:",
                help=_org_tooltip(ent, is_active, active_tax_year),
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state["active_entity"] = ent
                st.session_state["entity"] = ent
                st.session_state["show_settings_drawer"] = False
                _save_memory_store()
                st.rerun()


def render_sidebar_session(authenticator: stauth.Authenticate, current_page: str | None = None) -> None:
    """
    Render the left sidebar below the ``st.navigation`` page list:
    - Organization switcher (one-click buttons; edit names in ⚙ Settings)
    - Financial-year selector with a "＋" add-year popover
    - The only ⚙ Settings toggle, then operator name & Sign out
    ``current_page`` is the router's selected page id; changing page closes Settings.
    """
    init_workspace_state()
    _close_settings_on_page_change(current_page)

    active_entity = get_active_entity()
    active_tax_year = get_active_tax_year()
    drawer_open = st.session_state.get("show_settings_drawer", False)

    with st.sidebar:
        _render_org_switcher(active_entity, active_tax_year, drawer_open)

        with st.container(key="sb_year"):
            _render_year_selector(active_tax_year)

        with st.container(key="sb_footer"):
            if st.button(
                "Settings" if not drawer_open else "Close settings",
                key="_sidebar_open_settings_btn",
                icon=":material/settings:" if not drawer_open else ":material/close:",
                type="primary" if drawer_open else "secondary",
                use_container_width=True,
            ):
                st.session_state["show_settings_drawer"] = not drawer_open
                st.rerun()

            operator = html.escape(str(st.session_state.get("name") or "Operator"))
            st.markdown(
                f'<div class="axiom-sb-user"><span class="axiom-sb-user-name">{operator}</span>'
                f'<span class="axiom-sb-user-meta">Auto-lock after 60 min idle</span></div>',
                unsafe_allow_html=True,
            )
            if st.button(
                "Sign out",
                key="_sidebar_explicit_signout_btn",
                icon=":material/logout:",
                use_container_width=True,
            ):
                sign_out_current_session(authenticator)
                st.rerun()


def require_auth() -> stauth.Authenticate:
    """
    Compatibility guard for code that runs outside the ``app.py`` router.
    The router performs login, sidebar and header rendering itself; this only
    refreshes the 60-minute sliding session and halts if the operator is signed out.
    """
    authenticator = get_authenticator()
    auth_status = sync_and_touch_auth_session(authenticator)

    apply_custom_styles(authenticated=bool(auth_status))
    if not auth_status:
        st.stop()
    return authenticator
