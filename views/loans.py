"""Loans — NZ property loan & mortgage registry (was ``pages/4_Property_Loans.py``)."""

from utils import render_property_loans_block

render_property_loans_block(
    key_prefix="loans_page",
    eyebrow_title="NZ PROPERTY LOAN & MORTGAGE REGISTRY — BT PROPERTY HOLDINGS LTD",
    show_page_title=True,
    show_overview_and_dossiers=True,
)
