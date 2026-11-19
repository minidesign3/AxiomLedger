"""Review & Ledger — red/yellow/green review, master sheet, accountant export, properties
and item rules (was ``pages/1_Dashboard.py``)."""

import streamlit as st

from axiom_ui_dashboard_master import render_dashboard_accountant_tab, render_dashboard_master_tab
from axiom_ui_dashboard_setup import render_dashboard_properties_tab, render_dashboard_setup_tab
from utils import (
    format_tax_year_period,
    get_active_entity,
    get_active_tax_year,
    get_entity_account_codes,
    get_entity_ledger,
    get_entity_properties,
    get_file_red_counts,
    get_memorized_rules,
)

active_entity = get_active_entity()
active_tax_year = get_active_tax_year()
period_label = format_tax_year_period(active_tax_year, active_entity)

st.title("Review & Ledger")

ledger_df = get_entity_ledger(active_entity, active_tax_year)
stats = get_file_red_counts(ledger_df, entity=active_entity)
net_amount = float(ledger_df["Amount"].sum()) if not ledger_df.empty else 0.0
memorized_list = get_memorized_rules(active_entity)
account_codes_list = get_entity_account_codes(active_entity)
properties_list = get_entity_properties(active_entity)

m1, m2, m3, m4 = st.columns(4)
with m1:
    st.metric(
        label=f"FY {active_tax_year} net balance",
        value=f"${net_amount:,.2f}",
        delta=f"{stats['total']:,} rows ({period_label})",
        delta_color="off",
    )
with m2:
    st.metric(
        label="🟢 Sure",
        value=f"{stats['green']:,}",
        delta=f"{len(account_codes_list)} items · {len(memorized_list)} rules",
        delta_color="off",
    )
with m3:
    st.metric(
        label="🟡 Double-check",
        value=f"{stats['yellow']:,}",
        delta="Partial match / keyword guess",
        delta_color="off",
    )
with m4:
    st.metric(
        label="🔴 Unsure / missing",
        value=f"{stats['red']:,}",
        delta=f"{stats['unknown_code']} unknown item · {stats['needs_property']} missing property",
        delta_color="off",
    )

tab_master, tab_accountant, tab_properties, tab_codes = st.tabs(
    [
        f"Master ledger & red alerts ({stats['total']:,})",
        "Accountant summary & export",
        f"Properties & address matching ({len(properties_list)})",
        f"Rules & items ({len(account_codes_list)} · {len(memorized_list)})",
    ]
)

with tab_master:
    render_dashboard_master_tab()

with tab_accountant:
    render_dashboard_accountant_tab()

with tab_properties:
    render_dashboard_properties_tab()

with tab_codes:
    render_dashboard_setup_tab()
