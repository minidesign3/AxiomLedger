"""
axiom_ui_dashboard_master
==========================

Dashboard Tab 03 (Master Excel Sheet & Red Alerts) and Tab 04 (Accountant
Summary & Pivot Export).

Extracted from ``pages/1_Dashboard.py`` so the page shell stays under the
500-line cap. Each ``render_dashboard_*_tab`` function re-fetches the active
entity/tax year and ledger itself, matching the pattern already used by the
other ``axiom_ui_*`` tab modules. Tab 03 is further split into private
``_render_master_*`` helpers to keep every function under the complexity cap.
"""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from axiom_account_codes import add_entity_account_code, get_account_code_options
from axiom_categorization import apply_ai_fallback_categorization, apply_rule_categorization
from axiom_core import (
    DEFAULT_LOCAL_LLM_MODEL,
    STANDARD_COLUMNS,
    UNASSIGNED_PROPERTY,
    is_uncategorized,
)
from axiom_dataframe import export_xero_master_csv
from axiom_ingestion import get_entity_uploaded_files, get_file_red_counts
from axiom_ledger import get_entity_ledger, is_property_unassigned, set_entity_ledger
from axiom_ledger_sync import save_ledger_everywhere
from axiom_pm_rows import detect_pm_double_count
from axiom_properties import get_entity_properties, is_portfolio_wide_code
from axiom_rules import save_memorized_rule
from axiom_state import (
    format_tax_year_period,
    get_active_entity,
    get_active_tax_year,
    get_entity_jurisdiction,
)
from axiom_traffic import compute_traffic_light_columns
from axiom_ui_export_pack import render_accountant_export_panel


def render_dashboard_master_tab() -> None:
    """Tab 03: Master Excel sheet, traffic-light filters and inline editing."""
    active_entity = get_active_entity()
    active_tax_year = get_active_tax_year()
    period_label = format_tax_year_period(active_tax_year, active_entity)

    ledger_df = get_entity_ledger(active_entity, active_tax_year)
    stats = get_file_red_counts(ledger_df, entity=active_entity)
    properties_list = get_entity_properties(active_entity)

    _render_master_controls(active_entity, active_tax_year, ledger_df, stats)

    double_count_warning = detect_pm_double_count(ledger_df)
    if double_count_warning:
        st.error(double_count_warning)

    if ledger_df.empty:
        st.info(
            f"No transactions loaded for {active_entity} ({period_label}) yet. "
            "Use the 4-Column File Upload Board or open the `Codes.xlsx` importer on Tab 01."
        )
    else:
        _render_master_editor(active_entity, active_tax_year, ledger_df, stats, properties_list)


def _render_master_controls(
    active_entity: str,
    active_tax_year: str,
    ledger_df: pd.DataFrame,
    stats: dict,
) -> None:
    """The four action columns above the Master Excel Sheet (rules/AI run, export, clear)."""
    local_llm_url = st.session_state.get("local_llm_url", "http://localhost:11434/v1")
    active_cat_model = st.session_state.get("categorization_model", "Local Qwen 9B (Air-gapped)")
    ai_model_name = st.session_state.get("local_llm_model", DEFAULT_LOCAL_LLM_MODEL)

    ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([1.4, 1.6, 1.2, 1.0])

    with ctrl_col1:
        run_rules_clicked = st.button(
            "01 // Re-Apply Item & Property Rules",
            use_container_width=True,
            disabled=ledger_df.empty,
        )

    with ctrl_col2:
        run_ai_clicked = st.button(
            f"02 // AI Guess Unknown Items ({active_cat_model})",
            use_container_width=True,
            disabled=ledger_df.empty or stats["unknown_code"] == 0,
        )

    with ctrl_col3:
        safe_entity_slug = re.sub(r"[^a-zA-Z0-9]+", "_", active_entity).strip("_").lower()
        safe_year_slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(active_tax_year)).strip("_")
        export_filename = f"axiom_master_{safe_entity_slug}_{safe_year_slug}.csv"
        csv_bytes = export_xero_master_csv(ledger_df, jurisdiction=get_entity_jurisdiction(active_entity))

        st.download_button(
            label="⬇ Export Master CSV",
            data=csv_bytes,
            file_name=export_filename,
            mime="text/csv",
            use_container_width=True,
            disabled=ledger_df.empty,
        )

    with ctrl_col4:
        if st.button(
            "Clear Year Ledger",
            use_container_width=True,
            disabled=ledger_df.empty,
        ):
            set_entity_ledger(pd.DataFrame(columns=STANDARD_COLUMNS), active_entity, active_tax_year)
            get_entity_uploaded_files(active_entity, active_tax_year).clear()
            st.rerun()

    if run_rules_clicked:
        updated_df = apply_rule_categorization(ledger_df, entity=active_entity)
        # Write to the file records too, so a later file-level re-sync can't undo it
        save_ledger_everywhere(updated_df, active_entity, active_tax_year)
        st.success(f"Applied Item & Property Rules to {active_entity} ({active_tax_year}).")
        st.rerun()

    if run_ai_clicked:
        with st.spinner(f"Routing unknown descriptions to {active_cat_model}..."):
            try:
                updated_df, resolved_num = apply_ai_fallback_categorization(
                    ledger_df,
                    local_llm_url=local_llm_url,
                    model_name=ai_model_name.strip() or DEFAULT_LOCAL_LLM_MODEL,
                    selected_model=active_cat_model,
                    api_key=st.session_state.get("google_genai_api_key", ""),
                    entity=active_entity,
                )
                save_ledger_everywhere(updated_df, active_entity, active_tax_year)
                st.success(f"AI Categorized {resolved_num:,} row(s).")
                st.rerun()
            except Exception as exc:
                st.error(f"AI Categorization Error: {exc}")


def _master_tab_filters(
    active_entity: str,
    active_tax_year: str,
    ledger_df: pd.DataFrame,
    stats: dict,
    properties_list: list[str],
) -> tuple[str, str, str]:
    """The three filter widgets above the Master Excel Sheet data editor."""
    f_col1, f_col2, f_col3 = st.columns([2.4, 1.1, 1.1])
    with f_col1:
        master_filter = st.radio(
            "Master Filter",
            options=[
                f"All Rows ({stats['total']})",
                f"🔴+🟡 Needs Check ({stats['red_total']})",
                f"🔴 Red Only ({stats['red']})",
                f"🟡 Yellow Only ({stats['yellow']})",
                f"🟢 Green 100% Sure ({stats['green']})",
            ],
            horizontal=True,
            key=f"_master_filter_{active_entity}_{active_tax_year}",
            label_visibility="collapsed",
        )
    with f_col2:
        code_filter_opts = ["All Items (Categories)"] + sorted(
            {str(x).strip() for x in ledger_df["Account Code"].dropna().unique() if str(x).strip()}
        )
        code_filter = st.selectbox(
            "Filter by Item",
            options=code_filter_opts,
            key=f"_master_code_filter_{active_entity}_{active_tax_year}",
            label_visibility="collapsed",
        )
    with f_col3:
        prop_filter_opts = ["All Properties"] + list(
            dict.fromkeys([UNASSIGNED_PROPERTY] + properties_list + list(ledger_df["Property"].dropna().unique()))
        )
        prop_filter = st.selectbox(
            "Filter by Property",
            options=prop_filter_opts,
            key=f"_master_prop_filter_{active_entity}_{active_tax_year}",
            label_visibility="collapsed",
        )
    return master_filter, code_filter, prop_filter


def _master_tab_view(
    ledger_df: pd.DataFrame,
    active_entity: str,
    master_filter: str,
    code_filter: str,
    prop_filter: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Builds the traffic-lit working frame and the filtered view shown in the editor."""
    work_master = ledger_df.copy()
    work_master["Property"] = (
        work_master["Property"].fillna(UNASSIGNED_PROPERTY).astype(str).str.strip().replace("", UNASSIGNED_PROPERTY)
    )
    work_master["Account Code"] = (
        work_master["Account Code"].fillna("Uncategorized").astype(str).str.strip().replace("", "Uncategorized")
    )
    tiers_list, badges_list = compute_traffic_light_columns(work_master, entity=active_entity)
    work_master["Status"] = badges_list
    work_master["_tier"] = tiers_list

    view_master = work_master[["Status", "Date", "Description", "Amount", "Account Code", "Property"]]
    if master_filter.startswith("🔴+🟡"):
        view_master = view_master[work_master["_tier"].isin(["red", "yellow"])]
    elif master_filter.startswith("🔴 Red"):
        view_master = view_master[work_master["_tier"] == "red"]
    elif master_filter.startswith("🟡"):
        view_master = view_master[work_master["_tier"] == "yellow"]
    elif master_filter.startswith("🟢"):
        view_master = view_master[work_master["_tier"] == "green"]

    if code_filter != "All Items (Categories)":
        view_master = view_master[view_master["Account Code"] == code_filter]
    if prop_filter != "All Properties":
        view_master = view_master[view_master["Property"] == prop_filter]

    return work_master, view_master


def _save_master_tab_edits(
    work_master: pd.DataFrame,
    edited_master: pd.DataFrame,
    active_entity: str,
    active_tax_year: str,
) -> None:
    """Writes data-editor edits back to the ledger and memorizes confirmed rows."""
    full_updated = work_master[STANDARD_COLUMNS].copy()
    for idx in edited_master.index:
        if idx not in full_updated.index:
            continue
        for col in STANDARD_COLUMNS:
            full_updated.loc[idx, col] = edited_master.loc[idx, col]
        desc_v = str(full_updated.loc[idx, "Description"]).strip()
        prop_v = str(full_updated.loc[idx, "Property"]).strip()
        code_v = str(full_updated.loc[idx, "Account Code"]).strip()
        if is_portfolio_wide_code(code_v, active_entity) and prop_v in ("", UNASSIGNED_PROPERTY):
            prop_v = "General / Portfolio"
            full_updated.loc[idx, "Property"] = prop_v
        if desc_v and not is_uncategorized(code_v) and not is_property_unassigned(prop_v, code_v):
            add_entity_account_code(code_v, entity=active_entity)
            save_memorized_rule(
                pattern=desc_v,
                property_name=prop_v,
                account_code=code_v,
                entity=active_entity,
                source="Master Ledger Confirmed",
            )
    # Master-sheet edits used to update only the ledger and were lost on the next file save
    save_ledger_everywhere(full_updated, active_entity, active_tax_year)
    st.success("Saved Master Ledger and memorized your confirmed Item + Property rules as 🟢 100% Sure.")
    st.rerun()


def _render_master_editor(
    active_entity: str,
    active_tax_year: str,
    ledger_df: pd.DataFrame,
    stats: dict,
    properties_list: list[str],
) -> None:
    """Filters, traffic-light data editor and save/memorize button for Tab 03."""
    master_filter, code_filter, prop_filter = _master_tab_filters(
        active_entity, active_tax_year, ledger_df, stats, properties_list
    )
    work_master, view_master = _master_tab_view(
        ledger_df, active_entity, master_filter, code_filter, prop_filter
    )

    all_prop_opts = list(dict.fromkeys([UNASSIGNED_PROPERTY] + properties_list + list(work_master["Property"].unique())))
    all_code_opts = list(dict.fromkeys(get_account_code_options(active_entity) + list(work_master["Account Code"].unique())))

    edited_master = st.data_editor(
        view_master,
        use_container_width=True,
        hide_index=False,
        num_rows="fixed",
        key=f"_master_editor_{active_entity}_{active_tax_year}_{master_filter[:6]}_{code_filter}_{prop_filter}",
        column_config={
            "Status": st.column_config.TextColumn(
                "Traffic Light Certainty",
                disabled=True,
                width="medium",
                help="🟢 Green = 100% Sure. 🟡 Yellow = Partially Sure / Double-Check. 🔴 Red = Missing / Unsure.",
            ),
            "Date": st.column_config.TextColumn("Date", width="small"),
            "Description": st.column_config.TextColumn("Full CSV Line Text (Payee | Particulars | Code | Reference)", width="large"),
            "Amount": st.column_config.NumberColumn("Amount ($)", format="$%.2f", width="small"),
            "Account Code": st.column_config.SelectboxColumn("1. What is the Item?", options=all_code_opts, width="medium"),
            "Property": st.column_config.SelectboxColumn("2. Which Property?", options=all_prop_opts, width="medium"),
        },
    )

    if st.button(
        "💾 Save & Confirm Master Ledger Edits (Memorize as 🟢 100% Sure)",
        key=f"_save_master_btn_{active_entity}_{active_tax_year}",
        type="primary",
    ):
        _save_master_tab_edits(work_master, edited_master, active_entity, active_tax_year)


def render_dashboard_accountant_tab() -> None:
    """Tab 04: Accountant summary by Item/Property and the combined pivot table."""
    active_entity = get_active_entity()
    active_tax_year = get_active_tax_year()
    period_label = format_tax_year_period(active_tax_year, active_entity)
    ledger_df = get_entity_ledger(active_entity, active_tax_year)

    if ledger_df.empty:
        st.info(f"No transactions recorded yet for {active_entity} in {active_tax_year} ({period_label}).")
    else:
        # Readiness checklist (includes the PM double-count check) + one-file Accountant Pack
        render_accountant_export_panel(active_entity, active_tax_year)
        st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)
        sum_c1, sum_c2 = st.columns(2)
        with sum_c1:
            st.markdown(
                f'<div class="axiom-eyebrow">SUMMARY BY ITEM (ACCOUNT CODE) // {active_entity.upper()}</div>',
                unsafe_allow_html=True,
            )
            by_code = (
                ledger_df.groupby("Account Code", dropna=False)
                .agg(Transactions=("Amount", "count"), Sum_of_Amount=("Amount", "sum"))
                .reset_index()
                .sort_values(by="Account Code")
            )
            st.dataframe(by_code, use_container_width=True, hide_index=True)

        with sum_c2:
            st.markdown(
                f'<div class="axiom-eyebrow">SUMMARY BY PROPERTY // {active_entity.upper()}</div>',
                unsafe_allow_html=True,
            )
            by_prop = (
                ledger_df.groupby("Property", dropna=False)
                .agg(Transactions=("Amount", "count"), Sum_of_Amount=("Amount", "sum"))
                .reset_index()
                .sort_values(by="Property")
            )
            st.dataframe(by_prop, use_container_width=True, hide_index=True)

        st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="axiom-eyebrow">PIVOT TABLE: ITEMS (ROWS) VS PROPERTIES (COLUMNS)</div>',
            unsafe_allow_html=True,
        )
        pivot_df = ledger_df.pivot_table(
            index="Account Code",
            columns="Property",
            values="Amount",
            aggfunc="sum",
            fill_value=0.0,
            margins=True,
            margins_name="Grand Total",
        )
        st.dataframe(pivot_df.style.format("${:,.2f}"), use_container_width=True)
