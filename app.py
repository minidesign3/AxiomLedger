import importlib
import streamlit as st
import utils

importlib.reload(utils)
from utils import (
    apply_custom_styles,
    format_tax_year_period,
    get_active_entity,
    get_active_tax_year,
    get_authenticator,
    get_entity_jurisdiction,
    render_entity_ingestion_pipeline,
    render_sidebar_session,
    render_workspace_header,
    sync_and_touch_auth_session,
)

st.set_page_config(
    page_title="Axiom Ledger | Upload & File Board",
    page_icon="▪",
    layout="wide",
    initial_sidebar_state="expanded",
)

authenticator = get_authenticator()
auth_status = sync_and_touch_auth_session(authenticator)

# Apply strict dark charcoal/graphite aesthetic and lock sidebar until authenticated
apply_custom_styles(authenticated=bool(auth_status))

if not auth_status:
    _, center_col, _ = st.columns([1.2, 1.6, 1.2])
    with center_col:
        st.markdown(
            """
            <div style="margin-top: 3.5rem; margin-bottom: 1.5rem;">
                <div class="axiom-eyebrow">INSTITUTIONAL PROPERTY ACCOUNTING</div>
                <h1 style="margin: 0; font-size: 1.85rem; letter-spacing: -0.03em;">AXIOM LEDGER</h1>
                <p style="margin-top: 0.35rem; color: #8E939F; font-size: 0.88rem;">
                    Local Encrypted Financial Environment &mdash; Authorized Personnel Only
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.session_state.get("_session_expired_notice"):
            st.warning("Session locked after 60 minutes of inactivity. Please sign in again.")

        # Support both newer (0.3.x/0.4.x) and older (0.2.x) streamlit-authenticator APIs
        try:
            authenticator.login(location="main")
        except TypeError:
            name, authentication_status, username = authenticator.login("Security Gateway", "main")
            st.session_state["name"] = name
            st.session_state["authentication_status"] = authentication_status
            st.session_state["username"] = username

        auth_status = st.session_state.get("authentication_status")

        if auth_status is False:
            st.error("Authentication failed: Invalid operator credentials.")

    if not auth_status:
        st.stop()

    # Persist the newly authenticated session immediately and update CSS without aborting the cookie write
    sync_and_touch_auth_session(authenticator)
    apply_custom_styles(authenticated=True)

# Authenticated state: render persistent sidebar & workspace toolbar
# Note: Company/Entity editing and adding is strictly inside ⚙ Settings only.
render_sidebar_session(authenticator)
render_workspace_header(show_status_indicator=True)

active_entity = get_active_entity()
active_tax_year = get_active_tax_year()
active_jur = get_entity_jurisdiction(active_entity)
period_label = format_tax_year_period(active_tax_year, active_entity)

# Quick Top Navigation Bar between Front Upload Board and Codes/Master Ledger
top_nav1, top_nav2 = st.columns([2.8, 1.2])
with top_nav1:
    st.markdown(
        f'<div class="axiom-eyebrow">UPLOAD &amp; 4-COLUMN FILE BOARD // {active_entity.upper()} &mdash; {period_label.upper()}</div>',
        unsafe_allow_html=True,
    )
with top_nav2:
    if st.button(
        "Open Item + Property Setup & Master Ledger →",
        key="_front_goto_codes_dashboard",
        use_container_width=True,
    ):
        st.switch_page("pages/1_Dashboard.py")

render_entity_ingestion_pipeline(
    active_entity=active_entity,
    active_tax_year=active_tax_year,
    key_prefix="front_board",
)
