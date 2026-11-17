"""
axiom_ui_loans_dossiers
=======================

Portfolio overview table, per-property loan dossiers and the loan edit form for
the Property Loans block (phase D19). Moved out of ``render_property_loans_block``
with the widget order, keys and save payloads unchanged (verified against the
pre-refactor implementation with ``tests/ui_recorder.py``).
"""

from __future__ import annotations

from functools import partial
from typing import Any

import pandas as pd
import streamlit as st

from axiom_account_codes import get_account_code_options
from axiom_core import _fmt_loan_date, _parse_loan_date, infer_nz_bank_from_account
from axiom_loan_crud import (
    create_property_loan,
    delete_property_loan,
    get_loan_rate_history,
    has_reminder_been_sent,
    save_property_loan,
    send_loan_reminder_email,
)
from axiom_loans import _loan_dossier_summary_label, _loan_rate_status_tuple, loan_milestones
from axiom_ui_components import _render_loan_card_html
from axiom_ui_loans_common import (
    LOAN_FREQUENCY_OPTIONS,
    LOAN_INTEREST_TYPE_OPTIONS,
    LOAN_REPAYMENT_TYPE_OPTIONS,
    NZ_BANK_OPTIONS,
    LoanBlockContext,
)
from axiom_ui_loans_import import render_loan_screenshot_importer


def _open_import_for_property(
    show_key: str,
    target_key: str,
    loc_key: str,
    exp_key: str,
    prop: str,
) -> None:
    """on_click handler: open the screenshot importer for one property."""
    st.session_state[show_key] = True
    st.session_state[target_key] = prop
    st.session_state[loc_key] = prop
    st.session_state[exp_key] = prop


def render_loan_overview_table(ctx: LoanBlockContext) -> None:
    """One row per loan across every property, inside an expander."""
    loans = ctx.loans
    props = ctx.props

    with st.expander("PORTFOLIO OVERVIEW — EVERY NZ PROPERTY, WHAT EACH LOAN IS, BANK, ACCOUNT, RATE & EXPIRY", expanded=False):
        rows = []
        for p in props:
            pls = sorted(
                [x for x in loans if x.get("property_name") == p],
                key=lambda z: (
                    0 if (float(z.get("interest_rate_pct") or 0) > 0 or z.get("account_number")) else 1,
                    str(z.get("loan_label") or ""),
                ),
            )
            if not pls:
                rows.append(
                    {
                        "Property": p,
                        "What Loan Is": "— no loan recorded —",
                        "Bank": "—",
                        "Product": "",
                        "Loan Account": "",
                        "Balance": "",
                        "Rate % p.a.": "",
                        "Rate Expiry": "",
                        "Days": "",
                        "Status": "⚪ No loan",
                        "☑ Email": "",
                    }
                )
            for x in pls:
                _colour, status, days = _loan_rate_status_tuple(x)
                bal_val = (
                    float(x.get("current_balance") or 0)
                    or float(x.get("loan_limit") or 0)
                    or float(x.get("original_drawdown_amount") or 0)
                )
                rows.append(
                    {
                        "Property": p,
                        "What Loan Is": f"{x.get('loan_label') or 'Main Facility'} ({x.get('interest_type') or 'Fixed'} · {x.get('repayment_type') or 'Interest Only'})",
                        "Bank": x.get("bank_name") or infer_nz_bank_from_account(x.get("account_number") or "") or "—",
                        "Product": x.get("account_product_type") or "",
                        "Loan Account": x.get("account_number") or "—",
                        "Balance": f"${bal_val:,.0f}" if bal_val > 0 else "—",
                        "Rate % p.a.": f"{x.get('interest_type')} {float(x.get('interest_rate_pct') or 0):.2f}%"
                        if float(x.get("interest_rate_pct") or 0) > 0
                        else "—",
                        "Rate Expiry": _fmt_loan_date(x.get("fixed_rate_review_date")),
                        "Days": days if days is not None else "—",
                        "Status": status,
                        "☑ Email": "☑" if x.get("email_reminder_enabled") else "☐",
                    }
                )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_loan_dossiers(ctx: LoanBlockContext) -> None:
    """Per-property dossier expanders with the editable loan forms."""
    key_prefix = ctx.key_prefix
    loans = ctx.loans
    props = ctx.props
    show_import_key = ctx.show_import_key
    ocr_result_key = ctx.ocr_result_key
    ocr_target_key = ctx.ocr_target_key
    import_location_key = ctx.import_location_key
    expanded_prop_key = ctx.expanded_prop_key

    with st.expander(
        "PROPERTY LOAN DOSSIERS — CLICK ANY PROPERTY TO VIEW OR EDIT ITS LOAN DETAILS & RATE HISTORY",
        expanded=bool(st.session_state.get(expanded_prop_key)),
    ):
        code_options = get_account_code_options(ctx.target)
        for prop_idx, prop in enumerate(props):
            pls = sorted(
                [x for x in loans if x.get("property_name") == prop],
                key=lambda z: (
                    0 if (float(z.get("interest_rate_pct") or 0) > 0 or z.get("account_number")) else 1,
                    str(z.get("loan_label") or ""),
                ),
            )
            first = pls[0] if pls else None
            with st.expander(
                _loan_dossier_summary_label(first, prop, len(pls)),
                expanded=(st.session_state.get(expanded_prop_key) == prop),
            ):
                bc1, bc2 = st.columns([1, 3])
                with bc1:
                    st.button(
                        "📷 Import screenshot for this property",
                        key=f"{key_prefix}_shot_{prop_idx}_{prop}",
                        use_container_width=True,
                    on_click=partial(_open_import_for_property, show_import_key, ocr_target_key, import_location_key, expanded_prop_key, prop),
                    )
                with bc2:
                    if st.button(
                        "+ Add Loan / Tranche for this property",
                        key=f"{key_prefix}_addloan_{prop_idx}_{prop}",
                    ):
                        ok, msg, _lid = create_property_loan(
                            prop, f"Facility {len(pls) + 1}" if pls else "Main Facility"
                        )
                        st.session_state["_loan_flash"] = (ok, msg)
                        st.session_state[expanded_prop_key] = prop
                        st.rerun()
                if (
                    st.session_state.get(show_import_key)
                    or st.session_state.get(ocr_result_key)
                ) and st.session_state.get(import_location_key) == prop:
                    render_loan_screenshot_importer(ctx)
                for loan in pls:
                    lid = loan["loan_id"]
                    st.markdown(_render_loan_card_html(loan), unsafe_allow_html=True)
                    _render_loan_edit_form(ctx, loan, code_options)
                    for ms in loan_milestones(loan):
                        sent = has_reminder_been_sent(lid, ms["milestone_type"], ms["expiry_date"])
                        st.caption(
                            f"{ms['label']}: **{_fmt_loan_date(ms['expiry_date'])}** ({ms['days_remaining']} days) · reminder window opens {_fmt_loan_date(ms['window_start'])} · {'✉ emailed' if sent else 'not emailed yet'}"
                        )
                        if st.button(
                            f"✉ Send Reminder Email Now — {ms['label']}",
                            key=f"{key_prefix}_send_{lid}_{ms['milestone_type']}",
                        ):
                            ok, msg = send_loan_reminder_email(
                                lid, ms["milestone_type"], trigger_source=f"manual_{key_prefix}"
                            )
                            st.session_state["_loan_flash"] = (ok, msg)
                            st.rerun()
                    hist = get_loan_rate_history(lid)
                    if hist:
                        st.markdown(
                            '<div class="axiom-eyebrow">INTEREST RATE HISTORY (ANNUAL REFIXES)</div>',
                            unsafe_allow_html=True,
                        )
                        st.dataframe(
                            pd.DataFrame(hist)[
                                [
                                    "effective_date",
                                    "interest_type",
                                    "repayment_type",
                                    "interest_rate_pct",
                                    "review_or_expiry_date",
                                    "note",
                                    "logged_at",
                                ]
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )
                    if st.button("🗑 Delete this loan", key=f"{key_prefix}_del_{lid}"):
                        ok, msg = delete_property_loan(lid)
                        st.session_state["_loan_flash"] = (ok, msg)
                        st.rerun()


def _render_loan_edit_form(
    ctx: LoanBlockContext,
    loan: dict[str, Any],
    code_options: list[str],
) -> None:
    """The editable loan form for one dossier row."""
    key_prefix = ctx.key_prefix
    lid = loan["loan_id"]
    with st.form(key=f"{key_prefix}_loanform_{lid}"):
        c1, c2, c3 = st.columns(3)
        with c1:
            label = st.text_input(
                "Loan Label / Nickname",
                value=loan.get("loan_label") or "Main Facility",
            )
            banks = list(NZ_BANK_OPTIONS)
            cur_bank = loan.get("bank_name") or ""
            if cur_bank and cur_bank not in banks:
                banks = [cur_bank] + banks
            bank = st.selectbox(
                "Bank / Lender",
                options=[""] + banks,
                index=([""] + banks).index(cur_bank) if cur_bank in ([""] + banks) else 0,
            )
            holder = st.text_input(
                "Account Holder",
                value=loan.get("account_holder") or "BT Property Holdings Limited",
            )
            prod = st.text_input(
                "Account / Product Type (Choices, Home Loan…)",
                value=loan.get("account_product_type") or "",
            )
            acct = st.text_input(
                "Loan Account Number",
                value=loan.get("account_number") or "",
                help="Used as a coding trigger — statements showing this number auto-assign this property.",
            )
            repay_acct = st.text_input(
                "Repayment / Debit Interest Account",
                value=loan.get("repayment_account") or "",
            )
            broker = st.text_input(
                "Broker / Banker Contact",
                value=loan.get("broker_or_banker") or "",
            )
        with c2:
            drawn = st.number_input(
                "Original Drawdown Amount ($)",
                value=float(loan.get("original_drawdown_amount") or 0),
                step=1000.0,
                format="%.2f",
            )
            limit_amt = st.number_input(
                "Loan Limit ($)",
                value=float(loan.get("loan_limit") or 0),
                step=1000.0,
                format="%.2f",
            )
            balance = st.number_input(
                "Current Balance ($)",
                value=float(loan.get("current_balance") or 0),
                step=1000.0,
                format="%.2f",
            )
            redraw = st.number_input(
                "Available Redraw ($)",
                value=float(loan.get("available_redraw") or 0),
                step=100.0,
                format="%.2f",
            )
            accrued = st.number_input(
                "Interest Accrued ($)",
                value=float(loan.get("interest_accrued") or 0),
                step=10.0,
                format="%.2f",
            )
            itype = st.selectbox(
                "Interest Type",
                options=LOAN_INTEREST_TYPE_OPTIONS,
                index=LOAN_INTEREST_TYPE_OPTIONS.index(loan.get("interest_type"))
                if loan.get("interest_type") in LOAN_INTEREST_TYPE_OPTIONS
                else 0,
            )
            rate = st.number_input(
                "⭐ Current Interest Rate (% p.a.)",
                value=float(loan.get("interest_rate_pct") or 0),
                step=0.01,
                format="%.4f",
            )
            rtype = st.selectbox(
                "Repayment Type",
                options=LOAN_REPAYMENT_TYPE_OPTIONS,
                index=LOAN_REPAYMENT_TYPE_OPTIONS.index(loan.get("repayment_type"))
                if loan.get("repayment_type") in LOAN_REPAYMENT_TYPE_OPTIONS
                else 0,
            )
        with c3:
            dd = st.date_input(
                "Drawdown Date",
                value=_parse_loan_date(loan.get("drawdown_date")),
                format="DD/MM/YYYY",
            )
            fx = st.date_input(
                "⭐ Interest Rate Expiry / Fixed Review Date (2-month email based on this)",
                value=_parse_loan_date(loan.get("fixed_rate_review_date")),
                format="DD/MM/YYYY",
            )
            io_end = st.date_input(
                "Interest-Only Expiry Date",
                value=_parse_loan_date(loan.get("interest_only_end_date")),
                format="DD/MM/YYYY",
            )
            term_end = st.date_input(
                "Final Term / Maturity Date",
                value=_parse_loan_date(loan.get("loan_term_end_date")),
                format="DD/MM/YYYY",
            )
            nxt = st.date_input(
                "Next Payment / Debit Interest Date",
                value=_parse_loan_date(loan.get("next_repayment_date")),
                format="DD/MM/YYYY",
            )
            freq = st.selectbox(
                "Debit / Repayment Frequency",
                options=LOAN_FREQUENCY_OPTIONS,
                index=LOAN_FREQUENCY_OPTIONS.index(loan.get("repayment_frequency"))
                if loan.get("repayment_frequency") in LOAN_FREQUENCY_OPTIONS
                else 0,
            )
            regamt = st.number_input(
                "Next Payment / Debit Interest Amount ($)",
                value=float(loan.get("regular_repayment_amount") or 0),
                step=10.0,
                format="%.2f",
            )
            est = st.checkbox(
                "Next payment amount is estimated",
                value=bool(loan.get("next_payment_is_estimated")),
            )
        t1, t2 = st.columns(2)
        with t1:
            orig_term = st.text_input(
                "Original Term (e.g. 30 years 0 months)",
                value=loan.get("original_term_text") or "",
            )
        with t2:
            rem_term = st.text_input(
                "Remaining Term (e.g. 28 years 7 months)",
                value=loan.get("remaining_term_text") or "",
            )
        st.markdown("**Coding triggers & email reminder**")
        k1, k2 = st.columns(2)
        with k1:
            use_coding = st.checkbox(
                "☑ Use this loan for automatic transaction coding (account # / triggers assign this property)",
                value=bool(loan.get("use_for_coding")),
            )
            triggers = st.text_input(
                "Extra Coding Trigger Keywords (comma separated)",
                value=loan.get("coding_trigger_keywords") or "",
            )
            int_opts = list(
                dict.fromkeys(
                    code_options + ["Loan Interest", loan.get("interest_account_code") or "Loan Interest"]
                )
            )
            prin_opts = list(
                dict.fromkeys(
                    code_options
                    + ["Principal Loan", loan.get("principal_account_code") or "Principal Loan"]
                )
            )
            int_code = st.selectbox(
                "Interest Item Code",
                options=int_opts,
                index=int_opts.index(loan.get("interest_account_code"))
                if loan.get("interest_account_code") in int_opts
                else int_opts.index("Loan Interest"),
            )
            prin_code = st.selectbox(
                "Principal Item Code",
                options=prin_opts,
                index=prin_opts.index(loan.get("principal_account_code"))
                if loan.get("principal_account_code") in prin_opts
                else prin_opts.index("Principal Loan"),
            )
        with k2:
            email_on = st.checkbox(
                "☑ Email me 2 months before expiry",
                value=bool(loan.get("email_reminder_enabled")),
            )
            months = st.number_input(
                "Reminder lead (months before)",
                min_value=0,
                max_value=12,
                value=int(loan.get("reminder_months_before") or 2),
                step=1,
            )
            r_fx = st.checkbox(
                "Remind on Fixed Rate Expiry",
                value=bool(loan.get("remind_on_fixed_expiry")),
            )
            r_io = st.checkbox(
                "Remind on Interest-Only End",
                value=bool(loan.get("remind_on_io_expiry")),
            )
            r_term = st.checkbox(
                "Remind on Loan Term End",
                value=bool(loan.get("remind_on_term_expiry")),
            )
        if loan.get("screenshot_vault_path"):
            st.caption(f"📷 Source screenshot: `{loan.get('screenshot_vault_path')}`")
        notes = st.text_area("Notes", value=loan.get("notes") or "", height=60)
        log_hist = st.checkbox(
            "Log this save into Rate History (tick when refixing / rate changes)",
            value=False,
        )
        if st.form_submit_button(
            "💾 Save Loan Details",
            type="primary",
            use_container_width=True,
        ):
            payload = dict(loan)
            payload.update(
                {
                    "loan_label": label,
                    "bank_name": bank,
                    "account_holder": holder.strip(),
                    "account_product_type": prod.strip(),
                    "account_number": acct.strip(),
                    "repayment_account": repay_acct.strip(),
                    "broker_or_banker": broker.strip(),
                    "original_drawdown_amount": drawn,
                    "loan_limit": limit_amt,
                    "current_balance": balance,
                    "available_redraw": redraw,
                    "interest_accrued": accrued,
                    "interest_type": itype,
                    "interest_rate_pct": rate,
                    "repayment_type": rtype,
                    "drawdown_date": dd.isoformat() if dd else "",
                    "fixed_rate_review_date": fx.isoformat() if fx else "",
                    "interest_only_end_date": io_end.isoformat() if io_end else "",
                    "loan_term_end_date": term_end.isoformat() if term_end else "",
                    "next_repayment_date": nxt.isoformat() if nxt else "",
                    "repayment_frequency": freq,
                    "regular_repayment_amount": regamt,
                    "next_payment_is_estimated": est,
                    "original_term_text": orig_term.strip(),
                    "remaining_term_text": rem_term.strip(),
                    "use_for_coding": use_coding,
                    "coding_trigger_keywords": triggers.strip(),
                    "interest_account_code": int_code,
                    "principal_account_code": prin_code,
                    "email_reminder_enabled": email_on,
                    "reminder_months_before": int(months),
                    "remind_on_fixed_expiry": r_fx,
                    "remind_on_io_expiry": r_io,
                    "remind_on_term_expiry": r_term,
                    "notes": notes.strip(),
                }
            )
            ok, msg, _ = save_property_loan(
                payload,
                log_rate_history=log_hist,
                rate_note="Manual save from Property Loans dossier",
            )
            st.session_state["_loan_flash"] = (ok, msg)
            st.rerun()
