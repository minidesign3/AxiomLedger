"""
axiom_ui_settings
=================

System & Organization Settings drawer (phase D21): the drawer shell + the
Organizations/Properties, Loans, Files and Item+Property tabs. AI, Email and
Operator-account tabs live in their own modules so every file stays under the
500-line cap.
"""

from __future__ import annotations

import re

import streamlit as st

from axiom_entities import add_new_entity, rename_entity
from axiom_ingestion import get_entity_uploaded_files
from axiom_ledger import get_entity_ledger
from axiom_memory import _save_memory_store
from axiom_state import (
    get_active_entity,
    get_active_tax_year,
    get_entity_jurisdiction,
    get_entity_options,
)
from axiom_ui_components import _render_entity_edit_popover, render_property_editor_widget
from axiom_ui_ingestion import render_entity_ingestion_pipeline
from axiom_ui_ingestion_upload import _render_bulk_upload_dropzone
from axiom_ui_loans import render_property_loans_block
from axiom_ui_settings_accounts import render_accounts_tab
from axiom_ui_settings_ai import render_ai_tab
from axiom_ui_settings_email import render_email_tab
from axiom_ui_setup_matrix import render_item_property_setup_matrix


def render_right_settings_drawer() -> None:
    """
    Render the structured, full-width System & Organization Settings panel when
    `st.session_state["show_settings_drawer"]` is True.
    Organized into three clean, scannable tabs:
      1. Organizations & Entities: Manage entities, create portfolios, and rename via far-right 📌 pin icon.
      2. AI & API Connections: Select active AI provider and configure/verify Gemini Cloud & Local Qwen endpoints.
      3. Operator Accounts & Security: View operator roster, update credentials, or provision/remove accounts.
    """
    if not st.session_state.get("show_settings_drawer", False):
        return

    with st.container(border=True):
        _render_settings_header()

        tab_orgs, tab_loans, tab_files, tab_item_prop, tab_ai, tab_accounts, tab_email = st.tabs(
            [
                "01 // Organizations & Properties",
                "02 // Property Loans & Bank Registry (BT Property Holdings)",
                "03 // Upload & 4-Column File Table",
                "04 // Item + Property Setup (Insurance, Loan Interest, Rent)",
                "05 // AI & API Connections",
                "06 // Operator Accounts & Security",
                "07 // Email (SMTP) & Reminders",
            ]
        )

        with tab_orgs:
            render_orgs_tab()
        with tab_loans:
            render_loans_tab()
        with tab_files:
            render_files_tab()
        with tab_item_prop:
            render_item_prop_tab()
        with tab_ai:
            render_ai_tab()
        with tab_email:
            render_email_tab()
        with tab_accounts:
            render_accounts_tab()


def _render_settings_header() -> None:
    head_c1, head_c2 = st.columns([4.5, 1.0])
    with head_c1:
        st.markdown(
            """
            <div class="axiom-settings-drawer" style="margin-bottom: 0.25rem;">
                <div class="axiom-eyebrow">SYSTEM CONFIGURATION &amp; ORGANIZATION CONTROL PANEL</div>
                <h3 style="margin: 0; font-size: 1.1rem;">Organizations, AI Providers &amp; Operator Security</h3>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with head_c2:
        st.markdown("<div style='height: 0.35rem;'></div>", unsafe_allow_html=True)
        if st.button("✕ Close Settings", key="_btn_close_settings_drawer", use_container_width=True):
            st.session_state["show_settings_drawer"] = False
            st.rerun()

    ent_flash = st.session_state.pop("_entity_edit_flash", None)
    if isinstance(ent_flash, tuple) and len(ent_flash) == 2:
        f_ok, f_msg = ent_flash
        if f_ok:
            st.success(f_msg)
        else:
            st.error(f_msg)


def render_orgs_tab() -> None:
    active_entity = get_active_entity()
    active_tax_year = get_active_tax_year()
    all_entities = get_entity_options()

    org_list_col, org_create_col = st.columns([1.65, 1.15])

    with org_list_col:
        st.markdown(
            '<div class="axiom-eyebrow">CONFIGURED ORGANIZATIONS // EDIT NAME OR JURISDICTION &amp; CLICK SAVE</div>',
            unsafe_allow_html=True,
        )
        for idx, ent_name in enumerate(all_entities):
            is_active = ent_name == active_entity
            ent_jur = get_entity_jurisdiction(ent_name)
            ent_df = get_entity_ledger(ent_name, active_tax_year)
            row_cnt = len(ent_df)
            safe_slug = re.sub(r"[^a-zA-Z0-9]+", "_", ent_name).strip("_")

            with st.container(border=True):
                c_name, c_jur, c_save, c_switch, c_pin = st.columns([2.2, 1.15, 0.85, 0.9, 0.5])
                with c_name:
                    edited_org_name = st.text_input(
                        f"Organization {idx + 1}",
                        value=ent_name,
                        key=f"_settings_inline_org_name_{idx}_{safe_slug}",
                        label_visibility="collapsed",
                    )
                with c_jur:
                    edited_org_jur = st.selectbox(
                        f"Jurisdiction {idx + 1}",
                        options=["NZ IRD", "USA IRS"],
                        index=1 if ent_jur == "US" else 0,
                        key=f"_settings_inline_org_jur_{idx}_{safe_slug}",
                        label_visibility="collapsed",
                    )
                new_jur_code = "US" if edited_org_jur.startswith("US") else "NZ"
                has_changes = (edited_org_name.strip() != ent_name) or (new_jur_code != ent_jur)
                with c_save:
                    if st.button(
                        "Save",
                        key=f"_settings_inline_org_save_{idx}_{safe_slug}",
                        type="primary" if has_changes else "secondary",
                        disabled=(not edited_org_name.strip() or not has_changes),
                        use_container_width=True,
                    ):
                        ok_rn, msg_rn = rename_entity(ent_name, edited_org_name, new_jur_code)
                        st.session_state["_entity_edit_flash"] = (ok_rn, msg_rn)
                        st.rerun()
                with c_switch:
                    if st.button(
                        f"● Active ({row_cnt})" if is_active else f"Switch ({row_cnt})",
                        key=f"_settings_switch_ent_{idx}_{ent_name}",
                        type="primary" if is_active else "secondary",
                        disabled=is_active,
                        use_container_width=True,
                    ):
                        st.session_state["active_entity"] = ent_name
                        st.session_state["entity"] = ent_name
                        _save_memory_store()
                        st.rerun()
                with c_pin:
                    _render_entity_edit_popover(ent_name, key_prefix=f"settings_org_{idx}")

    with org_create_col:
        st.markdown(
            '<div class="axiom-eyebrow">REGISTER NEW ORGANIZATION</div>',
            unsafe_allow_html=True,
        )
        with st.form(key="_settings_form_create_entity", clear_on_submit=True):
            new_org_name = st.text_input(
                "Organization / Portfolio Name",
                placeholder="e.g. Family Trust Portfolio",
            )
            new_org_jur = st.selectbox(
                "Tax Jurisdiction",
                options=["NZ IRD", "USA IRS"],
            )
            if st.form_submit_button("+ Create Organization", use_container_width=True):
                jur_code = "US" if new_org_jur.startswith("US") else "NZ"
                ok_new, msg_new = add_new_entity(new_org_name, jur_code)
                st.session_state["_entity_edit_flash"] = (ok_new, msg_new)
                st.rerun()

    st.markdown('<div class="axiom-divider" style="margin: 0.75rem 0;"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="axiom-eyebrow">EDIT PROPERTIES FOR ACTIVE ORGANIZATION // {active_entity.upper()}</div>',
        unsafe_allow_html=True,
    )
    render_property_editor_widget(entity=active_entity, key_prefix="settings_prop_mgr")


def render_loans_tab() -> None:
    render_property_loans_block(
        key_prefix="settings_loans_tab",
        eyebrow_title="SETTINGS // PROPERTY LOANS & BANK REGISTRY — PROPERTIES & WHAT EACH LOAN IS (BT PROPERTY HOLDINGS LTD)",
        show_page_title=False,
    )


def render_files_tab() -> None:
    active_entity_f = get_active_entity()
    active_tax_year_f = get_active_tax_year()
    files_list_f = get_entity_uploaded_files(active_entity_f, active_tax_year_f)
    _render_bulk_upload_dropzone(
        active_entity=active_entity_f,
        active_tax_year=active_tax_year_f,
        files_list=files_list_f,
        key_prefix="settings_bulk_up",
        eyebrow_label="UPLOAD ALL FILES AT ONCE &mdash; AUTO-SAVED TO SQLITE &amp; DISK VAULT",
    )
    st.markdown("<div style='height: 0.35rem;'></div>", unsafe_allow_html=True)
    render_entity_ingestion_pipeline(
        active_entity=active_entity_f,
        active_tax_year=active_tax_year_f,
        key_prefix="settings_file_board",
        include_loans_block=False,
    )


def render_item_prop_tab() -> None:
    render_item_property_setup_matrix(
        entity=get_active_entity(),
        tax_year=get_active_tax_year(),
        key_prefix="settings_matrix",
    )

