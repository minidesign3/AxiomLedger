"""Inbox — upload files and triage them on the 4-column file board (was ``app.py``)."""

import streamlit as st

from utils import (
    format_tax_year_period,
    get_active_entity,
    get_active_tax_year,
    render_entity_ingestion_pipeline,
)

active_entity = get_active_entity()
active_tax_year = get_active_tax_year()
period_label = format_tax_year_period(active_tax_year, active_entity)

st.title("Inbox")
st.caption(f"Upload statements and fix red alerts for {active_entity} — {period_label}.")

render_entity_ingestion_pipeline(
    active_entity=active_entity,
    active_tax_year=active_tax_year,
    key_prefix="front_board",
)
