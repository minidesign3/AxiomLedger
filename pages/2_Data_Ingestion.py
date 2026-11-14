import importlib
import streamlit as st
import utils

importlib.reload(utils)
from utils import (
    format_tax_year_period,
    get_active_entity,
    get_active_tax_year,
    render_entity_ingestion_pipeline,
    render_workspace_header,
    require_auth,
)

st.set_page_config(
    page_title="Axiom Ledger | Data Ingestion Board",
    page_icon="▪",
    layout="wide",
    initial_sidebar_state="expanded",
)

require_auth()
render_workspace_header(show_status_indicator=True)

active_entity = get_active_entity()
active_tax_year = get_active_tax_year()
period_label = format_tax_year_period(active_tax_year, active_entity)

hdr_c1, hdr_c2 = st.columns([2.8, 1.2])
with hdr_c1:
    st.markdown(
        f'<div class="axiom-eyebrow">DATA INGESTION // 4-COLUMN AUTO-SORTING BOARD &mdash; {active_entity.upper()} ({period_label.upper()})</div>',
        unsafe_allow_html=True,
    )
with hdr_c2:
    if st.button(
        "Open Item + Property Setup & Master Ledger →",
        key="_ing_page_goto_codes",
        use_container_width=True,
    ):
        st.switch_page("pages/1_Dashboard.py")

render_entity_ingestion_pipeline(
    active_entity=active_entity,
    active_tax_year=active_tax_year,
    key_prefix="ing_page",
)
