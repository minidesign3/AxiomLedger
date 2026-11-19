"""
axiom_ui_dashboard_setup
========================

Dashboard Tab 01 (Item + Property Setup, with the ``Codes.xlsx`` / coded
workbook importer) and Tab 02 (Properties & Address/Alias Matching).

Extracted from ``pages/1_Dashboard.py`` so the page shell stays under the
500-line cap. Each ``render_dashboard_*_tab`` function re-fetches the active
entity/tax year itself, matching the pattern already used by the other
``axiom_ui_*`` tab modules (e.g. ``axiom_ui_settings.render_files_tab``).
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from axiom_account_codes import get_property_aliases, save_property_aliases
from axiom_properties import get_entity_properties
from axiom_state import get_active_entity, get_active_tax_year
from axiom_ui_components import render_property_editor_widget
from axiom_ui_setup_matrix import render_item_property_setup_matrix
from axiom_upload import import_codes_workbook_into_entity
from axiom_workbook import parse_coded_workbook_sheets


def render_dashboard_setup_tab() -> None:
    """Tab 01: Item + Property setup matrix plus the coded-workbook importer."""
    active_entity = get_active_entity()
    active_tax_year = get_active_tax_year()

    render_item_property_setup_matrix(
        entity=active_entity,
        tax_year=active_tax_year,
        key_prefix="dash_matrix",
    )

    st.markdown('<div class="axiom-divider" style="margin: 0.85rem 0;"></div>', unsafe_allow_html=True)

    with st.expander("📂 Optional: Import from `Codes.xlsx` or Other Coded Spreadsheets", expanded=False):
        default_codes_xlsx = Path(
            os.environ.get(
                "AXIOM_CODES_XLSX",
                "/home/ccjp/Desktop/Accounting CSV Parser/Codes.xlsx",
            )
        )
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


def render_dashboard_properties_tab() -> None:
    """Tab 02: Properties list, address matching and alias keywords."""
    active_entity = get_active_entity()
    properties_list = get_entity_properties(active_entity)
    property_aliases_map = get_property_aliases(active_entity)

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
