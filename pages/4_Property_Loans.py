import importlib
import streamlit as st
import utils

importlib.reload(utils)
from utils import (
    render_property_loans_block,
    render_workspace_header,
    require_auth,
)

st.set_page_config(
    page_title="Axiom Ledger | Property Loans & Bank Registry",
    page_icon="▪",
    layout="wide",
    initial_sidebar_state="expanded",
)
require_auth()
render_workspace_header(show_status_indicator=True)

render_property_loans_block(
    key_prefix="loans_page",
    eyebrow_title="MODULE 04 // NZ PROPERTY LOAN & MORTGAGE REGISTRY — BT PROPERTY HOLDINGS LTD",
    show_page_title=True,
    show_overview_and_dossiers=True,
)
