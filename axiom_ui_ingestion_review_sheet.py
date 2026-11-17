"""
axiom_ui_ingestion_review_sheet
===============================

The interactive traffic-light sheet of the full-page reviewer (phase D20): filter
bar, data editor and the save/memorise handler.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from axiom_categorization import apply_ai_fallback_categorization, apply_rule_categorization
from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY, is_uncategorized
from axiom_ingestion import sync_entity_ledger_from_files
from axiom_ledger import is_property_unassigned
from axiom_properties import is_portfolio_wide_code
from axiom_rules import save_memorized_rule
from axiom_traffic import evaluate_row_traffic_light
from axiom_ui_ingestion_common import ReviewContext


def _render_review_sheet(
    ctx: ReviewContext,
    counts: dict,
    clean_prop_options: list[str],
    account_code_options: list[str],
) -> None:
    """Filter bar, editable transaction grid and the save button."""
    file_id = ctx.file_id
    df = ctx.df
    file_rec = ctx.file_rec
    active_entity = ctx.active_entity
    active_tax_year = ctx.active_tax_year
    key_prefix = ctx.key_prefix

    # -------------------------------------------------------------------------
    # EXCEL-STYLE INTERACTIVE SHEET WITH 3-TIER TRAFFIC LIGHTS & FILTER TOGGLE
    # -------------------------------------------------------------------------
    flt_c1, flt_c2, flt_c3 = st.columns([2.5, 1.15, 1.05])
    with flt_c1:
        filter_mode = st.radio(
            "Filter Rows",
            options=[
                f"All Rows ({counts['total']})",
                f"🔴+🟡 Needs Check ({counts['red_total']})",
                f"🔴 Red Only ({counts['red']})",
                f"🟡 Yellow Only ({counts['yellow']})",
                f"🟢 Green 100% Sure ({counts['green']})",
            ],
            horizontal=True,
            key=f"{key_prefix}_filter_radio_{file_id}",
            label_visibility="collapsed",
        )
    with flt_c2:
        auto_memorize = st.checkbox(
            "Auto-Memorize Confirmed Rows (Turn 🟢 Green)",
            value=True,
            key=f"{key_prefix}_auto_mem_chk_{file_id}",
        )
    with flt_c3:
        if counts["red_total"] > 0:
            if st.button(
                "🤖 Auto-Guess Red Rows with AI",
                key=f"{key_prefix}_ai_guess_file_{file_id}",
                use_container_width=True,
            ):
                try:
                    with st.spinner("Running AI categorization on unknown rows..."):
                        updated_df, _ = apply_ai_fallback_categorization(df, entity=active_entity)
                        file_rec["df"] = updated_df
                        sync_entity_ledger_from_files(active_entity, active_tax_year)
                    st.rerun()
                except Exception as exc:
                    st.error(f"AI Categorization Error: {exc}")

    display_df = df.copy()
    row_tiers: list[str] = []
    row_badges: list[str] = []
    for _, r in display_df.iterrows():
        t_val, b_val = evaluate_row_traffic_light(
            property_val=r.get("Property", ""),
            account_code=r.get("Account Code", ""),
            description=r.get("Description", ""),
            entity=active_entity,
        )
        row_tiers.append(t_val)
        row_badges.append(b_val)
    display_df["Status"] = row_badges
    display_df["_tier"] = row_tiers

    # Reorder so Item (Account Code) and Property are side-by-side right next to Amount
    editor_cols = ["Status", "Date", "Description", "Amount", "Account Code", "Property"]

    if filter_mode.startswith("🔴+🟡"):
        view_df = display_df[display_df["_tier"].isin(["red", "yellow"])][editor_cols]
    elif filter_mode.startswith("🔴 Red"):
        view_df = display_df[display_df["_tier"] == "red"][editor_cols]
    elif filter_mode.startswith("🟡"):
        view_df = display_df[display_df["_tier"] == "yellow"][editor_cols]
    elif filter_mode.startswith("🟢"):
        view_df = display_df[display_df["_tier"] == "green"][editor_cols]
    else:
        view_df = display_df[editor_cols]

    # Ensure all current values in view_df exist in dropdown options
    for p_val in view_df["Property"].dropna().unique():
        s_p = str(p_val).strip()
        if s_p and s_p not in clean_prop_options:
            clean_prop_options.append(s_p)
    for c_val in view_df["Account Code"].dropna().unique():
        s_c = str(c_val).strip()
        if s_c and s_c not in account_code_options:
            account_code_options.append(s_c)

    edited_view = st.data_editor(
        view_df,
        use_container_width=True,
        hide_index=False,
        num_rows="dynamic" if filter_mode.startswith("All") else "fixed",
        key=f"{key_prefix}_sheet_editor_{file_id}_{filter_mode[:6]}",
        column_config={
            "Status": st.column_config.TextColumn(
                "Traffic Light Certainty",
                disabled=True,
                width="medium",
                help="🟢 Green = 100% Sure (Memorized Rule + House Matched). 🟡 Yellow = Partially Sure / Please Double-Check. 🔴 Red = Unsure / Missing.",
            ),
            "Date": st.column_config.TextColumn("Date", width="small"),
            "Description": st.column_config.TextColumn("Full CSV Line Text (Payee | Particulars | Code | Reference)", width="large"),
            "Amount": st.column_config.NumberColumn("Amount ($)", format="$%.2f", width="small"),
            "Account Code": st.column_config.SelectboxColumn(
                "1. What is the Item?",
                options=account_code_options,
                required=True,
                width="medium",
                help="Select the Item (e.g. Insurance, Loan Interest, Rent Income, Rates, Bodycorp, Accountant).",
            ),
            "Property": st.column_config.SelectboxColumn(
                "2. Which Property?",
                options=clean_prop_options,
                required=True,
                width="medium",
                help="Select which Property this item belongs to (e.g., 9 Damian, 33 Blampied, 303 Weymouth, or General / Portfolio).",
            ),
        },
    )

    if st.button(
        "💾 Save & Confirm Checked Rows (Memorize as 🟢 100% Sure)",
        key=f"{key_prefix}_save_sheet_btn_{file_id}",
        type="primary",
    ):
        _save_confirmed_rows(ctx, edited_view, filter_mode, auto_memorize)


def _save_confirmed_rows(
    ctx: ReviewContext,
    edited_view: pd.DataFrame,
    filter_mode: str,
    auto_memorize: bool,
) -> None:
    """Apply the edited rows, memorise what became confident and re-run rules."""
    file_name = ctx.file_name
    df = ctx.df
    file_rec = ctx.file_rec
    active_entity = ctx.active_entity
    active_tax_year = ctx.active_tax_year
    if filter_mode.startswith("All"):
        new_full_df = edited_view[STANDARD_COLUMNS].copy().reset_index(drop=True)
    else:
        new_full_df = df.copy()
        for idx in edited_view.index:
            if idx in new_full_df.index:
                for col in STANDARD_COLUMNS:
                    new_full_df.loc[idx, col] = edited_view.loc[idx, col]

    # Auto-fill General / Portfolio for any row assigned a Portfolio-Wide Item
    for idx_r in new_full_df.index:
        c_val = str(new_full_df.loc[idx_r, "Account Code"]).strip()
        p_val = str(new_full_df.loc[idx_r, "Property"]).strip()
        if is_portfolio_wide_code(c_val, active_entity) and p_val in ("", UNASSIGNED_PROPERTY):
            new_full_df.loc[idx_r, "Property"] = "General / Portfolio"

    learned_now = 0
    if auto_memorize:
        for _, r in new_full_df.iterrows():
            desc_v = str(r.get("Description", "")).strip()
            prop_v = str(r.get("Property", "")).strip()
            code_v = str(r.get("Account Code", "")).strip()
            if desc_v and not is_uncategorized(code_v) and not is_property_unassigned(prop_v, code_v):
                save_memorized_rule(
                    pattern=desc_v,
                    property_name=prop_v,
                    account_code=code_v,
                    entity=active_entity,
                    source=f"Confirmed in {file_name}",
                )
                learned_now += 1

    # Re-apply memorized rules across all uploaded files in case a newly memorized rule resolves other rows
    file_rec["df"] = apply_rule_categorization(new_full_df, overwrite_existing=False, entity=active_entity)
    sync_entity_ledger_from_files(active_entity, active_tax_year)
    st.success(f"Saved '{file_name}' and memorized {learned_now} confirmed rule(s) as 🟢 100% Sure.")
    st.rerun()
