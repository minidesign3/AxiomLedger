"""
axiom_ui_settings_email
=======================

Settings drawer tab 07 — SMTP email configuration, test sends, due loan
reminders and the recent reminder log.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from axiom_core import _fmt_loan_date
from axiom_email import (
    _smtp_recipients,
    get_smtp_config,
    save_smtp_config,
    send_email_via_smtp,
)
from axiom_loan_crud import (
    check_and_send_due_loan_reminders,
    get_due_loan_reminders,
    get_loan_reminder_log,
    get_loan_target_entity,
    is_loan_target_entity,
)
from axiom_nav import LOANS_PAGE


def render_email_tab() -> None:
    st.markdown('<div class="axiom-eyebrow">SMTP EMAIL // BT PROPERTY HOLDINGS LTD LOAN EXPIRY REMINDERS (2 MONTHS BEFORE EXPIRY)</div>', unsafe_allow_html=True)
    _smtp_flash = st.session_state.pop("_smtp_flash", None)
    if isinstance(_smtp_flash, tuple) and len(_smtp_flash) == 2:
        if _smtp_flash[0]:
            st.success(_smtp_flash[1])
        else:
            st.error(_smtp_flash[1])
    _cfg = get_smtp_config()
    with st.form(key="_form_smtp_config"):
        c1, c2 = st.columns(2)
        with c1:
            _host = st.text_input("SMTP Server Host", value=str(_cfg.get("smtp_host") or ""), placeholder="smtp.gmail.com / smtp.office365.com")
            _port = st.number_input("SMTP Port", min_value=1, max_value=65535, value=int(_cfg.get("smtp_port") or 587), step=1)
            _enc = st.selectbox("Encryption", options=["STARTTLS", "SSL", "None"], index=["STARTTLS", "SSL", "None"].index(str(_cfg.get("encryption") or "STARTTLS").upper() if str(_cfg.get("encryption") or "STARTTLS").upper() in ("STARTTLS", "SSL") else "STARTTLS"))
            _user = st.text_input("SMTP Username", value=str(_cfg.get("username") or ""))
            _pw = st.text_input("SMTP Password / App Password (leave blank to keep saved)", value="", type="password")
        with c2:
            _sender = st.text_input("Sender Email", value=str(_cfg.get("sender_email") or ""))
            _sname = st.text_input("Sender Name", value=str(_cfg.get("sender_name") or "Axiom Ledger"))
            _recips = st.text_area("Recipient Email(s) — comma separated (you receive loan reminders here)", value=str(_cfg.get("recipient_emails") or ""), height=70)
            _months = st.number_input("Default Reminder Lead Time (months before expiry)", min_value=0, max_value=12, value=int(_cfg.get("default_reminder_months") or 2), step=1)
            _auto = st.checkbox("Auto-send due reminders on app startup", value=bool(_cfg.get("auto_send_on_startup", True)))
        if st.form_submit_button("💾 Save SMTP Settings", use_container_width=True):
            _new = dict(_cfg)
            _new.update({"smtp_host": _host.strip(), "smtp_port": int(_port), "encryption": _enc, "username": _user.strip(), "sender_email": _sender.strip(), "sender_name": _sname.strip(), "recipient_emails": _recips.strip(), "default_reminder_months": int(_months), "auto_send_on_startup": bool(_auto)})
            if _pw:
                _new["password"] = _pw
            _ok, _msg = save_smtp_config(_new)
            st.session_state["_smtp_flash"] = (_ok, _msg)
            st.rerun()
    st.caption(f"Saved password: {'●●●●●● set' if str(_cfg.get('password') or '') else 'not set'}  ·  Recipients resolve to: {', '.join(_smtp_recipients(_cfg)) or '—'}")
    tc1, tc2, tc3 = st.columns(3)
    with tc1:
        if st.button("✉ Send Test Email", key="_btn_smtp_test", use_container_width=True):
            _ok, _msg = send_email_via_smtp("[Axiom Ledger] Test Email", "This is a test email from Axiom Ledger SMTP settings.\nIf you received this, loan expiry reminders will work.")
            st.session_state["_smtp_flash"] = (_ok, _msg)
            st.rerun()
    with tc2:
        if st.button("🔔 Check & Send Due Loan Reminders Now", key="_btn_smtp_check_due", use_container_width=True):
            _n, _msgs = check_and_send_due_loan_reminders(trigger_source="manual_settings")
            st.session_state["_smtp_flash"] = (_n > 0, f"Sent {_n} reminder(s). " + (" | ".join(_msgs) if _msgs else "No reminders due in the 2-month window."))
            st.rerun()
    with tc3:
        # Loans is only registered in st.navigation for the loan-target entity;
        # switch_page to an unregistered page raises, so disable it elsewhere.
        _loans_available = is_loan_target_entity()
        if st.button(
            "🏦 Open Property Loans Page →",
            key="_btn_goto_loans",
            use_container_width=True,
            disabled=not _loans_available,
            help=None if _loans_available else f"Switch to {get_loan_target_entity()} to manage property loans.",
        ):
            st.switch_page(LOANS_PAGE)
    _due = get_due_loan_reminders()
    if _due:
        st.warning(f"{len(_due)} loan milestone(s) currently inside the reminder window and not yet emailed:")
        for _d in _due:
            st.markdown(f"- **{_d['loan'].get('property_name')}** ({_d['loan'].get('loan_label')}) — {_d['label']} on **{_fmt_loan_date(_d['expiry_date'])}** ({_d['days_remaining']} days)")
    else:
        st.info("No loan milestones are currently inside the reminder window (or all due reminders have already been emailed).")
    _log = get_loan_reminder_log(limit=10)
    if _log:
        st.markdown('<div class="axiom-eyebrow">RECENT REMINDER EMAIL LOG</div>', unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(_log)[["sent_at", "property_name", "milestone_type", "expiry_date", "recipient_email", "status", "trigger_source"]], use_container_width=True, hide_index=True)

