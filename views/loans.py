"""Loans — NZ property loan & mortgage registry (was ``pages/4_Property_Loans.py``)."""

import streamlit as st

from axiom_nav import INBOX_PAGE
from utils import get_loan_target_entity, is_loan_target_entity, render_property_loans_block

if not is_loan_target_entity():
    # The router only registers this page for the loan-target entity; this guard
    # covers stale deep links so another entity never shows BT's loan registry.
    st.info(f"Property loans are only tracked for {get_loan_target_entity()}. This entity has no loans.")
    if st.button("Back to Inbox", key="loans_page_back_to_inbox", icon=":material/inbox:"):
        st.switch_page(INBOX_PAGE)
    st.stop()

render_property_loans_block(
    key_prefix="loans_page",
    eyebrow_title="NZ PROPERTY LOAN & MORTGAGE REGISTRY — BT PROPERTY HOLDINGS LTD",
    show_page_title=True,
    show_overview_and_dossiers=True,
)
