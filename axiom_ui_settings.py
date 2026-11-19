"""
axiom_ui_settings
=================

System & Organization Settings drawer (phase D21): the drawer shell + the
Organizations/Properties tab. AI, Email and Operator-account tabs live in their
own modules so every file stays under the 500-line cap. Loans, the upload board
and Item + Property setup were removed from Settings (plan.md §11) because they
have their own pages (Loans, Inbox, Review & Ledger).
"""

from __future__ import annotations

import re

import streamlit as st

from axiom_entities import add_new_entity, rename_entity
from axiom_ledger import get_entity_ledger
from axiom_memory import _save_memory_store
from axiom_state import (
    get_active_entity,
    get_active_tax_year,
    get_entity_jurisdiction,
    get_entity_options,
)
from axiom_ui_components import _render_entity_edit_popover, render_property_editor_widget
from axiom_ui_settings_accounts import render_accounts_tab
from axiom_ui_settings_ai import render_ai_tab
from axiom_ui_settings_email import render_email_tab


def render_right_settings_drawer() -> None:
    """
    Render the structured, full-width System & Organization Settings panel when
    `st.session_state["show_settings_drawer"]` is True.
    Organized into four tabs:
      1. Organizations & properties: Manage entities, create portfolios, and rename via far-right 📌 pin icon.
      2. AI & connections: Select active AI provider and configure/verify Gemini Cloud & Local Qwen endpoints.
      3. Accounts & security: View operator roster, update credentials, or provision/remove accounts.
      4. Email & reminders: SMTP configuration and loan-expiry reminder emails.
    """
    if not st.session_state.get("show_settings_drawer", False):
        return

    with st.container(border=True):
        _render_settings_header()

        # Loans, Upload board and Item + Property setup live on their own pages
        # (Loans / Inbox / Review & Ledger), so Settings only holds configuration.
        tab_orgs, tab_ai, tab_accounts, tab_email = st.tabs(
            [
                "Organizations & properties",
                "AI & connections",
                "Accounts & security",
                "Email & reminders",
            ]
        )

        with tab_orgs:
            render_orgs_tab()
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
                <div class="axiom-eyebrow">SYSTEM CONFIGURATION</div>
                <h3 style="margin: 0; font-size: 1.1rem;">Settings</h3>
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
