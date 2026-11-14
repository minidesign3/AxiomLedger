import importlib
from pathlib import Path
import re
import pandas as pd
import streamlit as st
import utils

importlib.reload(utils)
from utils import (
    DEFAULT_LOCAL_LLM_MODEL,
    STANDARD_COLUMNS,
    UNASSIGNED_PROPERTY,
    add_entity_account_code,
    apply_ai_fallback_categorization,
    apply_rule_categorization,
    evaluate_row_traffic_light,
    export_xero_master_csv,
    format_tax_year_period,
    get_account_code_options,
    get_active_entity,
    get_active_tax_year,
    get_entity_account_codes,
    get_entity_jurisdiction,
    get_entity_ledger,
    get_entity_properties,
    get_entity_rules,
    get_entity_uploaded_files,
    get_file_red_counts,
    get_memorized_rules,
    get_property_aliases,
    get_row_status_badge,
    import_codes_workbook_into_entity,
    is_portfolio_wide_code,
    is_property_unassigned,
    is_row_red_or_unsure,
    is_uncategorized,
    parse_coded_workbook_sheets,
    render_item_property_setup_matrix,
    render_property_editor_widget,
    render_workspace_header,
    require_auth,
    save_memorized_rule,
    save_property_aliases,
    set_entity_ledger,
)

st.set_page_config(
    page_title="Axiom Ledger | Item + Property Setup & Master Ledger",
    page_icon="▪",
    layout="wide",
    initial_sidebar_state="expanded",
)

require_auth()
render_workspace_header(show_status_indicator=True)

active_entity = get_active_entity()
active_tax_year = get_active_tax_year()
local_llm_url = st.session_state.get("local_llm_url", "http://localhost:11434/v1")

active_rules = get_entity_rules(active_entity)
active_jurisdiction = get_entity_jurisdiction(active_entity)
jurisdiction_label = "USA IRS" if active_jurisdiction == "US" else "NZ IRD"
period_label = format_tax_year_period(active_tax_year, active_entity)

top_c1, top_c2 = st.columns([2.8, 1.2])
with top_c1:
    st.markdown(
        f'<div class="axiom-eyebrow">ITEM + PROPERTY SETUP &amp; MASTER LEDGER // {active_entity.upper()} &mdash; {period_label.upper()}</div>',
        unsafe_allow_html=True,
    )
    st.title(f"{active_entity} — {active_tax_year} ({period_label})")
with top_c2:
    if st.button(
        "← Back to 4-Column File Upload Board",
        key="_dash_back_to_upload_board",
        use_container_width=True,
    ):
        st.switch_page("app.py")

st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)

ledger_df = get_entity_ledger(active_entity, active_tax_year)
stats = get_file_red_counts(ledger_df, entity=active_entity)
net_amount = float(ledger_df["Amount"].sum()) if not ledger_df.empty else 0.0
memorized_list = get_memorized_rules(active_entity)
properties_list = get_entity_properties(active_entity)
account_codes_list = get_entity_account_codes(active_entity)
property_aliases_map = get_property_aliases(active_entity)

m1, m2, m3, m4 = st.columns(4)
with m1:
    st.metric(
        label=f"FY {active_tax_year} NET BALANCE",
        value=f"${net_amount:,.2f}",
        delta=f"{stats['total']:,} TOTAL ROWS ({period_label})",
    )
with m2:
    st.metric(
        label="🟢 GREEN (100% SURE)",
        value=f"{stats['green']:,}",
        delta=f"{len(account_codes_list)} Items · {len(memorized_list)} Rules",
    )
with m3:
    st.metric(
        label="🟡 YELLOW (DOUBLE-CHECK)",
        value=f"{stats['yellow']:,}",
        delta="Partial Match / Keyword Guess",
    )
with m4:
    st.metric(
        label="🔴 RED (UNSURE / MISSING)",
        value=f"{stats['red']:,}",
        delta=f"{stats['unknown_code']} Unknown Item · {stats['needs_property']} Missing Prop",
    )

st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)

tab_codes, tab_properties, tab_master, tab_accountant = st.tabs(
    [
        f"01 // Item + Property Setup ({len(account_codes_list)} Items · {len(memorized_list)} Rules)",
        f"02 // Properties & Address Matching ({len(properties_list)} Properties)",
        f"03 // Master Excel Sheet & Red Alerts ({stats['total']:,} Rows)",
        "04 // Accountant Summary & Pivot Export",
    ]
)

# =============================================================================
# TAB 1: CLEAN 3-COLUMN ITEM + PROPERTY SETUP MATRIX
# =============================================================================
with tab_codes:
    render_item_property_setup_matrix(
        entity=active_entity,
        tax_year=active_tax_year,
        key_prefix="dash_matrix",
    )

    st.markdown('<div class="axiom-divider" style="margin: 0.85rem 0;"></div>', unsafe_allow_html=True)

    with st.expander("📂 Optional: Import from `Codes.xlsx` or Other Coded Spreadsheets", expanded=False):
        default_codes_xlsx = Path("/home/ccjp/Desktop/Accounting CSV Parser/Codes.xlsx")
        if default_codes_xlsx.exists():
            with st.container(border=True):
                st.markdown(
                    f'<div style="font-size:0.82rem;color:#E2E4E9;margin-bottom:0.4rem;">'
                    f'📂 Detected workbook: <code>{default_codes_xlsx}</code>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                inc_tx_col, btn_col1, btn_col2 = st.columns([1.4, 1.3, 1.3])
                with inc_tx_col:
                    import_tx_toggle = st.checkbox(
                        f"Also load bank rows into {active_tax_year} ledger",
                        value=True,
                        key=f"_chk_import_tx_context_{active_entity}",
                    )
                with btn_col1:
                    if st.button(
                        "🔍 Preview Breakdown",
                        key=f"_btn_preview_context_xlsx_{active_entity}",
                        use_container_width=True,
                    ):
                        st.session_state[f"_preview_codes_xlsx_{active_entity}"] = parse_coded_workbook_sheets(
                            default_codes_xlsx.read_bytes(),
                            filename=default_codes_xlsx.name,
                            entity=active_entity,
                        )
                with btn_col2:
                    if st.button(
                        "⚡ Import Items, Rules & Properties Now",
                        key=f"_btn_import_context_xlsx_{active_entity}",
                        type="primary",
                        use_container_width=True,
                    ):
                        summary_res = import_codes_workbook_into_entity(
                            default_codes_xlsx.read_bytes(),
                            filename=default_codes_xlsx.name,
                            entity=active_entity,
                            tax_year=active_tax_year,
                            import_bank_transactions=import_tx_toggle,
                        )
                        st.session_state[f"_preview_codes_xlsx_{active_entity}"] = summary_res["parsed"]
                        st.success(
                            f"Imported {summary_res['total_codes']} Items, "
                            f"{summary_res['total_properties']} Properties, and "
                            f"{summary_res['saved_rules']} rules!"
                        )
                        st.rerun()

        train_files = st.file_uploader(
            "Or Upload Any Other Coded Workbook / CSV (.xlsx, .xls, .ods, .odt, .csv)",
            type=["xlsx", "xls", "ods", "odt", "csv"],
            accept_multiple_files=True,
            key=f"_train_memory_uploader_{active_entity}",
        )
        if train_files:
            up_inc_tx = st.checkbox(
                f"Also append bank transaction rows into {active_tax_year} Master Ledger",
                value=False,
                key=f"_chk_upload_inc_tx_{active_entity}",
            )
            if st.button(
                f"🧠 Extract & Memorize Rules from {len(train_files)} File(s)",
                key=f"_btn_run_train_memory_{active_entity}",
                type="primary",
                use_container_width=True,
            ):
                total_rules = 0
                total_codes = 0
                for tf in train_files:
                    res = import_codes_workbook_into_entity(
                        tf.getvalue(),
                        filename=tf.name,
                        entity=active_entity,
                        tax_year=active_tax_year,
                        import_bank_transactions=up_inc_tx,
                    )
                    total_rules += int(res["saved_rules"])
                    total_codes += int(res["total_codes"])
                st.success(
                    f"Extracted {total_codes} Items and memorized {total_rules} rules for {active_entity}!"
                )
                st.rerun()

# =============================================================================
# TAB 2: PROPERTIES & ADDRESS / SHORTHAND MATCHING (DEDICATED PROPERTY TAB)
# =============================================================================
with tab_properties:
    st.markdown(
        """
        <div style="background-color: #16181C; border: 1px solid #2A2D34; border-radius: 4px; padding: 0.75rem 1rem; margin-bottom: 0.9rem;">
            <span class="axiom-eyebrow" style="margin:0;">HOW PROPERTY DETECTION WORKS ACROSS BANK COLUMNS</span>
            <div style="font-size: 0.84rem; color: #D0D4DC; margin-top: 0.25rem;">
                In bank exports like <code>Westpac - BT Prop</code> and <code>ASB BT Prop</code>, the property is often written in a separate column (<code>Analysis Code</code>, <code>Reference</code>, <code>Particulars</code>, or <code>Memo</code>) using shorthand like <code>16D 363Queen</code>, <code>Apt 1604</code>, <code>33Blam</code>, <code>14Ral</code>, or <code>9 Damien</code>.<br/>
                Configure your properties on the left and their <b>Bank Column Matching Keywords</b> on the right so every row automatically links to the right property.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    prop_left_col, prop_right_col = st.columns([1.2, 1.8])

    with prop_left_col:
        with st.container(border=True):
            st.markdown(
                f'<div class="axiom-eyebrow">01 // PROPERTIES FOR {active_entity.upper()} ({len(properties_list)})</div>',
                unsafe_allow_html=True,
            )
            render_property_editor_widget(entity=active_entity, key_prefix="dash_prop_tab")

    with prop_right_col:
        with st.container(border=True):
            st.markdown(
                '<div class="axiom-eyebrow">02 // BANK COLUMN SHORTHAND &amp; ALIAS KEYWORDS PER PROPERTY</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "When any of these comma-separated keywords appear in `Analysis Code`, `Reference`, `Particulars`, `Memo`, "
                "or an EOY statement header, Axiom Ledger assigns that row to the corresponding Property."
            )

            for p_idx, prop_name in enumerate(properties_list):
                curr_aliases = property_aliases_map.get(prop_name, [prop_name.lower()])
                aliases_str = ", ".join(curr_aliases)
                al_c1, al_c2, al_c3 = st.columns([1.15, 2.15, 0.65])
                with al_c1:
                    st.markdown(
                        f"<div style='padding-top:0.42rem;font-weight:600;color:#F3F4F6;font-size:0.85rem;'>▪ {prop_name}</div>",
                        unsafe_allow_html=True,
                    )
                with al_c2:
                    edited_aliases = st.text_input(
                        f"Aliases for {prop_name}",
                        value=aliases_str,
                        key=f"_prop_alias_in_{active_entity}_{p_idx}",
                        label_visibility="collapsed",
                    )
                with al_c3:
                    if st.button(
                        "Save",
                        key=f"_btn_save_alias_{active_entity}_{p_idx}",
                        use_container_width=True,
                    ):
                        ok_al, msg_al = save_property_aliases(
                            prop_name, edited_aliases, entity=active_entity
                        )
                        if ok_al:
                            st.success(msg_al)
                            st.rerun()

# =============================================================================
# TAB 3: MASTER EXCEL SHEET & RED ALERTS (COMBINED ACROSS ALL UPLOADED FILES)
# =============================================================================
with tab_master:
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
        csv_bytes = export_xero_master_csv(ledger_df)

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
        set_entity_ledger(updated_df, active_entity, active_tax_year)
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
                set_entity_ledger(updated_df, active_entity, active_tax_year)
                st.success(f"AI Categorized {resolved_num:,} row(s).")
                st.rerun()
            except Exception as exc:
                st.error(f"AI Categorization Error: {exc}")

    if ledger_df.empty:
        st.info(
            f"No transactions loaded for {active_entity} ({period_label}) yet. "
            "Use the 4-Column File Upload Board or open the `Codes.xlsx` importer on Tab 01."
        )
    else:
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

        work_master = ledger_df.copy()
        work_master["Property"] = (
            work_master["Property"].fillna(UNASSIGNED_PROPERTY).astype(str).str.strip().replace("", UNASSIGNED_PROPERTY)
        )
        work_master["Account Code"] = (
            work_master["Account Code"].fillna("Uncategorized").astype(str).str.strip().replace("", "Uncategorized")
        )
        tiers_list: list[str] = []
        badges_list: list[str] = []
        for _, r in work_master.iterrows():
            t_val, b_val = evaluate_row_traffic_light(
                property_val=r.get("Property", ""),
                account_code=r.get("Account Code", ""),
                description=r.get("Description", ""),
                entity=active_entity,
            )
            tiers_list.append(t_val)
            badges_list.append(b_val)
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
            full_updated = work_master[STANDARD_COLUMNS].copy()
            for idx in edited_master.index:
                if idx in full_updated.index:
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
            set_entity_ledger(full_updated, active_entity, active_tax_year)
            st.success("Saved Master Ledger and memorized your confirmed Item + Property rules as 🟢 100% Sure.")
            st.rerun()

# =============================================================================
# TAB 4: ACCOUNTANT SUMMARY BY ACCOUNT CODE & PROPERTY (MATCHING PIVOT TABLE)
# =============================================================================
with tab_accountant:
    if ledger_df.empty:
        st.info(f"No transactions recorded yet for {active_entity} in {active_tax_year} ({period_label}).")
    else:
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
