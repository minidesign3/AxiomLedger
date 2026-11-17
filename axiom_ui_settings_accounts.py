"""
axiom_ui_settings_accounts
==========================

Settings drawer tab 06 — registered operator roster, credential updates,
operator provisioning and account removal.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from axiom_auth import (
    add_operator_account,
    delete_operator_account,
    get_all_user_accounts,
    update_operator_credentials,
)


def render_accounts_tab() -> None:
    all_accounts = get_all_user_accounts()
    account_usernames = list(all_accounts.keys())
    active_operator = st.session_state.get("username") or (account_usernames[0] if account_usernames else "admin")
    if active_operator not in account_usernames and account_usernames:
        active_operator = account_usernames[0]

    acc_flash = st.session_state.pop("_settings_account_flash", None)
    if isinstance(acc_flash, tuple) and len(acc_flash) == 2:
        flash_ok, flash_msg = acc_flash
        if flash_ok:
            st.success(flash_msg)
        else:
            st.error(flash_msg)

    # Top Row: Clean Registered Operator Roster
    roster_hdr_col, remove_ctrl_col = st.columns([2.2, 1.4])
    with roster_hdr_col:
        st.markdown(
            '<div class="axiom-eyebrow">REGISTERED OPERATOR DIRECTORY</div>',
            unsafe_allow_html=True,
        )
        roster_rows = [
            {
                "Username": uname,
                "Display Name": info.get("name", uname),
                "Email": info.get("email", f"{uname}@axiomledger.local"),
                "Role": info.get("role", "Administrator"),
                "Session": "● SIGNED IN" if uname == st.session_state.get("username") else "PROVISIONED",
            }
            for uname, info in all_accounts.items()
        ]
        st.dataframe(pd.DataFrame(roster_rows), use_container_width=True, hide_index=True)

    with remove_ctrl_col:
        st.markdown(
            '<div class="axiom-eyebrow">ACCOUNT SELECTION &amp; REMOVAL</div>',
            unsafe_allow_html=True,
        )
        selected_target_user = st.selectbox(
            "Account to Modify Below",
            options=account_usernames,
            index=account_usernames.index(active_operator) if active_operator in account_usernames else 0,
            key="_settings_select_account_to_modify",
        )
        target_info = all_accounts.get(selected_target_user, {})

        removable_users = [
            u for u in account_usernames if u != st.session_state.get("username") and len(account_usernames) > 1
        ]
        if removable_users:
            del_c1, del_c2 = st.columns([1.8, 1.2])
            with del_c1:
                user_to_remove = st.selectbox(
                    "Remove Operator",
                    options=removable_users,
                    key="_settings_remove_account_select",
                    label_visibility="collapsed",
                )
            with del_c2:
                if st.button("Remove", key="_btn_remove_operator_account", use_container_width=True):
                    ok_del, msg_del = delete_operator_account(user_to_remove)
                    st.session_state["_settings_account_flash"] = (ok_del, msg_del)
                    st.rerun()

    st.markdown('<div class="axiom-divider" style="margin: 0.5rem 0;"></div>', unsafe_allow_html=True)

    upd_col, add_col = st.columns(2)

    # -----------------------------------------------------------------
    # LEFT COLUMN: UPDATE CREDENTIALS FOR SELECTED ACCOUNT
    # -----------------------------------------------------------------
    with upd_col:
        st.markdown(
            f'<div class="axiom-eyebrow">01 // UPDATE CREDENTIALS FOR ({selected_target_user.upper()})</div>',
            unsafe_allow_html=True,
        )
        with st.form(key="_form_update_operator_credentials", clear_on_submit=False):
            u_c1, u_c2 = st.columns(2)
            with u_c1:
                edit_username = st.text_input(
                    "Username",
                    value=selected_target_user,
                    help="Letters, numbers, underscores, dots, or hyphens.",
                )
            with u_c2:
                edit_display_name = st.text_input(
                    "Display Name",
                    value=target_info.get("name", selected_target_user),
                )
            edit_email = st.text_input(
                "Email Address",
                value=target_info.get("email", f"{selected_target_user}@axiomledger.local"),
            )
            edit_current_pw = st.text_input(
                "Current Password (Required to Authorize)",
                type="password",
                placeholder="Enter current password",
            )
            pw_c1, pw_c2 = st.columns(2)
            with pw_c1:
                edit_new_pw = st.text_input(
                    "New Password (Optional)",
                    type="password",
                    placeholder="Min 8 chars",
                )
            with pw_c2:
                edit_confirm_pw = st.text_input(
                    "Confirm New Password",
                    type="password",
                    placeholder="Repeat new password",
                )

            if st.form_submit_button("Save Credentials", use_container_width=True):
                ok_upd, msg_upd = update_operator_credentials(
                    current_username=selected_target_user,
                    new_username=edit_username,
                    new_display_name=edit_display_name,
                    new_email=edit_email,
                    current_password=edit_current_pw,
                    new_password=edit_new_pw,
                    confirm_password=edit_confirm_pw,
                )
                st.session_state["_settings_account_flash"] = (ok_upd, msg_upd)
                st.rerun()

    # -----------------------------------------------------------------
    # RIGHT COLUMN: PROVISION NEW OPERATOR ACCOUNT
    # -----------------------------------------------------------------
    with add_col:
        st.markdown(
            '<div class="axiom-eyebrow">02 // PROVISION ADDITIONAL OPERATOR</div>',
            unsafe_allow_html=True,
        )
        with st.form(key="_form_add_operator_account", clear_on_submit=True):
            n_c1, n_c2 = st.columns(2)
            with n_c1:
                new_acc_user = st.text_input(
                    "New Username",
                    placeholder="e.g. accountant_nz",
                )
            with n_c2:
                new_acc_name = st.text_input(
                    "Display Name",
                    placeholder="e.g. Portfolio Accountant",
                )
            r_col1, r_col2 = st.columns(2)
            with r_col1:
                new_acc_email = st.text_input(
                    "Email",
                    placeholder="user@axiomledger.local",
                )
            with r_col2:
                new_acc_role = st.selectbox(
                    "Role",
                    options=["Administrator", "Accountant", "Auditor", "Operator"],
                )
            np_col1, np_col2 = st.columns(2)
            with np_col1:
                new_acc_pw = st.text_input(
                    "Password (Min 8 chars)",
                    type="password",
                    placeholder="••••••••••••",
                )
            with np_col2:
                new_acc_pw_confirm = st.text_input(
                    "Confirm Password",
                    type="password",
                    placeholder="••••••••••••",
                )

            if st.form_submit_button("+ Create Operator Account", use_container_width=True):
                ok_add, msg_add = add_operator_account(
                    username=new_acc_user,
                    display_name=new_acc_name,
                    email=new_acc_email,
                    password=new_acc_pw,
                    confirm_password=new_acc_pw_confirm,
                    role=new_acc_role,
                )
                st.session_state["_settings_account_flash"] = (ok_add, msg_add)
                st.rerun()

