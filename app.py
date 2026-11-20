"""Axiom Ledger router: login gate, global chrome, and ``st.navigation``.

Page bodies live in ``views/`` and are registered in ``axiom_nav``. The router
renders the sidebar + slim workspace header once per run; if Settings is open,
``render_workspace_header`` shows only Settings and stops before the page runs.
"""

import streamlit as st

from axiom_nav import build_navigation_pages, page_identity
from utils import (
    apply_custom_styles,
    get_authenticator,
    init_workspace_state,
    is_loan_target_entity,
    render_sidebar_session,
    render_workspace_header,
    run_loan_reminder_check_once_per_session,
    sync_and_touch_auth_session,
)

st.set_page_config(
    page_title="Axiom Ledger",
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

try:
    run_loan_reminder_check_once_per_session()
except Exception:
    pass

# Navigation is resolved first so the sidebar page list stays visible even while
# Settings is open; the selected page only runs after the chrome is rendered.
# Workspace state is initialised first so the Loans page is only registered
# for the loan-target entity (switching org reruns and rebuilds this list).
init_workspace_state()
current_page = st.navigation(
    build_navigation_pages(show_loans=is_loan_target_entity()),
    position="sidebar",
)
render_sidebar_session(authenticator, current_page=page_identity(current_page))
render_workspace_header(show_status_indicator=True)
current_page.run()
