"""
axiom_ui_loans_import
=====================

Screenshot-to-loan importer panel for the Property Loans block (phase D19).
Extracted verbatim from the nested ``_render_loan_screenshot_importer`` closure
in ``utils.py``; only the captured locals now arrive through
``LoanBlockContext``.
"""

from __future__ import annotations

import streamlit as st

from axiom_core import _parse_loan_date
from axiom_loan_crud import save_property_loan
from axiom_loan_ocr import extract_loan_details_from_screenshot_gemini
from axiom_ui_loans_common import (
    LOAN_FREQUENCY_OPTIONS,
    LOAN_INTEREST_TYPE_OPTIONS,
    LOAN_REPAYMENT_TYPE_OPTIONS,
    NZ_BANK_OPTIONS,
    LoanBlockContext,
)


def render_loan_screenshot_importer(ctx: LoanBlockContext) -> None:
    """Upload a bank loan screenshot, extract the fields and review before saving."""
    with st.container(border=True):
        st.markdown(
            '<div class="axiom-eyebrow">📷 SCREENSHOT IMPORT — GEMINI 4 FLASH (AUTO-FALLBACK TO 3.8 FLASH) · REVIEW &amp; EDIT BEFORE SAVING</div>',
            unsafe_allow_html=True,
        )
        ic1, ic2 = st.columns([1, 1.4])
        with ic1:
            _render_loan_ocr_upload(ctx)
        with ic2:
            _render_loan_ocr_review(ctx)


def _render_loan_ocr_upload(ctx: LoanBlockContext) -> None:
    """Property selector, screenshot uploader and the extract / close actions."""
    key_prefix = ctx.key_prefix
    props = ctx.props
    show_import_key = ctx.show_import_key
    ocr_result_key = ctx.ocr_result_key
    ocr_image_key = ctx.ocr_image_key
    ocr_target_key = ctx.ocr_target_key
    import_location_key = ctx.import_location_key

    target_prop = st.selectbox(
        "Property for this loan (leave on Auto-detect if unsure)",
        options=["Auto-detect from screenshot"] + props,
        key=ocr_target_key,
    )
    up = st.file_uploader(
        "Upload bank loan screenshot (Westpac / ANZ / ASB / BNZ / Kiwibank…)",
        type=["png", "jpg", "jpeg", "webp"],
        key=f"{key_prefix}_ocr_uploader",
    )
    if up is not None:
        st.image(up.getvalue(), caption=up.name, use_container_width=True)
        if st.button(
            "🔍 Extract Loan Details with Gemini",
            type="primary",
            use_container_width=True,
            key=f"{key_prefix}_btn_ocr_extract",
        ):
            try:
                with st.spinner("Gemini is reading the screenshot…"):
                    parsed, used_model, vault_path = extract_loan_details_from_screenshot_gemini(
                        up.getvalue(),
                        mime_type=up.type or "image/png",
                        filename=up.name,
                        target_property=None if target_prop.startswith("Auto") else target_prop,
                    )
                parsed["_vault_path"] = vault_path
                parsed["_used_model"] = used_model
                st.session_state[ocr_result_key] = parsed
                st.session_state[ocr_image_key] = up.getvalue()
                st.rerun()
            except Exception as exc:
                st.error(f"Screenshot extraction failed: {exc}")
    if st.button("✖ Close importer", key=f"{key_prefix}_btn_close_import"):
        st.session_state[show_import_key] = False
        st.session_state.pop(ocr_result_key, None)
        st.session_state.pop(import_location_key, None)
        st.rerun()


def _render_loan_ocr_review(ctx: LoanBlockContext) -> None:
    """Review-and-save form for the extracted OCR fields."""
    key_prefix = ctx.key_prefix
    props = ctx.props
    loans = ctx.loans
    cfg = ctx.cfg
    show_import_key = ctx.show_import_key
    ocr_result_key = ctx.ocr_result_key
    ocr_image_key = ctx.ocr_image_key
    import_location_key = ctx.import_location_key

    ocr = st.session_state.get(ocr_result_key)
    if ocr:
        st.success(
            f"Extracted with **{ocr.get('_used_model', 'Gemini')}** — check every field below, fix any errors, then save. Screenshot saved to `{ocr.get('_vault_path', '')}`"
        )
        if st.session_state.get(ocr_image_key):
            st.image(
                st.session_state[ocr_image_key],
                caption="Imported screenshot (for comparison)",
                use_container_width=True,
            )
        existing_match = None
        for _l in loans:
            if ocr.get("account_number") and _l.get("account_number") == ocr.get("account_number"):
                existing_match = _l
                break
        if existing_match is None and ocr.get("property_name"):
            for _l in loans:
                if (
                    _l.get("property_name") == ocr.get("property_name")
                    and not (_l.get("account_number") or "").strip()
                    and float(_l.get("current_balance") or 0) <= 0
                ):
                    existing_match = _l
                    break
        if existing_match:
            st.info(
                f"This account number matches existing loan **{existing_match.get('loan_label')}** for **{existing_match.get('property_name')}** — saving will UPDATE it (old record replaced)."
            )
        with st.form(key=f"{key_prefix}_ocr_review_form"):
            r1, r2, r3 = st.columns(3)
            with r1:
                o_prop = st.selectbox(
                    "Property ⭐",
                    options=props,
                    index=props.index(ocr.get("property_name"))
                    if ocr.get("property_name") in props
                    else 0,
                )
                o_label = st.text_input(
                    "Loan Label / Nickname",
                    value=ocr.get("loan_label") or "Main Facility",
                )
                banks = list(NZ_BANK_OPTIONS)
                cur = ocr.get("bank_name") or ""
                if cur and cur not in banks:
                    banks = [cur] + banks
                o_bank = st.selectbox(
                    "Bank / Lender",
                    options=[""] + banks,
                    index=([""] + banks).index(cur) if cur in ([""] + banks) else 0,
                )
                o_holder = st.text_input(
                    "Account Holder",
                    value=ocr.get("account_holder") or "BT Property Holdings Limited",
                )
                o_prod = st.text_input(
                    "Account / Product Type",
                    value=ocr.get("account_product_type") or "",
                )
                o_acct = st.text_input(
                    "Loan Account Number ⭐",
                    value=ocr.get("account_number") or "",
                )
                o_repay = st.text_input(
                    "Repayment / Debit Interest Account",
                    value=ocr.get("repayment_account") or "",
                )
            with r2:
                o_drawn = st.number_input(
                    "Original Drawdown Amount ($)",
                    value=float(ocr.get("original_drawdown_amount") or 0),
                    step=1000.0,
                    format="%.2f",
                )
                o_limit = st.number_input(
                    "Loan Limit ($)",
                    value=float(ocr.get("loan_limit") or 0),
                    step=1000.0,
                    format="%.2f",
                )
                o_bal = st.number_input(
                    "Current Balance ($)",
                    value=float(ocr.get("current_balance") or 0),
                    step=1000.0,
                    format="%.2f",
                )
                o_redraw = st.number_input(
                    "Available Redraw ($)",
                    value=float(ocr.get("available_redraw") or 0),
                    step=100.0,
                    format="%.2f",
                )
                o_accrued = st.number_input(
                    "Interest Accrued ($)",
                    value=float(ocr.get("interest_accrued") or 0),
                    step=10.0,
                    format="%.2f",
                )
                o_itype = st.selectbox(
                    "Interest Type",
                    options=LOAN_INTEREST_TYPE_OPTIONS,
                    index=LOAN_INTEREST_TYPE_OPTIONS.index(ocr.get("interest_type"))
                    if ocr.get("interest_type") in LOAN_INTEREST_TYPE_OPTIONS
                    else 0,
                )
                o_rate = st.number_input(
                    "⭐ Current Interest Rate (% p.a.)",
                    value=float(ocr.get("interest_rate_pct") or 0),
                    step=0.01,
                    format="%.4f",
                )
                o_rtype = st.selectbox(
                    "Repayment Type",
                    options=LOAN_REPAYMENT_TYPE_OPTIONS,
                    index=LOAN_REPAYMENT_TYPE_OPTIONS.index(ocr.get("repayment_type"))
                    if ocr.get("repayment_type") in LOAN_REPAYMENT_TYPE_OPTIONS
                    else 0,
                )
            with r3:
                o_dd = st.date_input(
                    "Drawdown Date",
                    value=_parse_loan_date(ocr.get("drawdown_date")),
                    format="DD/MM/YYYY",
                )
                o_fx = st.date_input(
                    "⭐ Interest Rate Expiry / Fixed Review Date",
                    value=_parse_loan_date(ocr.get("fixed_rate_review_date")),
                    format="DD/MM/YYYY",
                )
                o_io = st.date_input(
                    "Interest-Only Expiry Date",
                    value=_parse_loan_date(ocr.get("interest_only_end_date")),
                    format="DD/MM/YYYY",
                )
                o_term = st.date_input(
                    "Final Term / Maturity Date",
                    value=_parse_loan_date(ocr.get("loan_term_end_date")),
                    format="DD/MM/YYYY",
                )
                o_next = st.date_input(
                    "Next Payment / Debit Interest Date",
                    value=_parse_loan_date(ocr.get("next_repayment_date")),
                    format="DD/MM/YYYY",
                )
                o_freq = st.selectbox(
                    "Debit / Repayment Frequency",
                    options=LOAN_FREQUENCY_OPTIONS,
                    index=LOAN_FREQUENCY_OPTIONS.index(ocr.get("repayment_frequency"))
                    if ocr.get("repayment_frequency") in LOAN_FREQUENCY_OPTIONS
                    else 0,
                )
                o_amt = st.number_input(
                    "Next Payment / Debit Interest Amount ($)",
                    value=float(ocr.get("regular_repayment_amount") or 0),
                    step=10.0,
                    format="%.2f",
                )
                o_est = st.checkbox(
                    "Next payment amount is estimated",
                    value=bool(ocr.get("next_payment_is_estimated")),
                )
            o_orig_term = st.text_input(
                "Original Term (e.g. 30 years 0 months)",
                value=ocr.get("original_term_text") or "",
            )
            o_rem_term = st.text_input(
                "Remaining Term (e.g. 28 years 7 months)",
                value=ocr.get("remaining_term_text") or "",
            )
            o_notes = st.text_area("Notes", value=ocr.get("notes") or "", height=60)
            o_log = st.checkbox("Log rate into Rate History", value=True)
            if st.form_submit_button(
                "💾 Save to Property Dossier",
                type="primary",
                use_container_width=True,
            ):
                payload = dict(existing_match) if existing_match else {}
                payload.update(
                    {
                        "property_name": o_prop,
                        "loan_label": o_label.strip() or "Main Facility",
                        "bank_name": o_bank,
                        "account_holder": o_holder.strip(),
                        "account_product_type": o_prod.strip(),
                        "account_number": o_acct.strip(),
                        "repayment_account": o_repay.strip(),
                        "drawdown_date": o_dd.isoformat() if o_dd else "",
                        "original_drawdown_amount": o_drawn,
                        "loan_limit": o_limit,
                        "current_balance": o_bal,
                        "available_redraw": o_redraw,
                        "interest_accrued": o_accrued,
                        "interest_type": o_itype,
                        "interest_rate_pct": o_rate,
                        "repayment_type": o_rtype,
                        "fixed_rate_review_date": o_fx.isoformat() if o_fx else "",
                        "interest_only_end_date": o_io.isoformat() if o_io else "",
                        "loan_term_end_date": o_term.isoformat() if o_term else "",
                        "next_repayment_date": o_next.isoformat() if o_next else "",
                        "repayment_frequency": o_freq,
                        "regular_repayment_amount": o_amt,
                        "next_payment_is_estimated": o_est,
                        "original_term_text": o_orig_term.strip(),
                        "remaining_term_text": o_rem_term.strip(),
                        "notes": o_notes.strip(),
                        "screenshot_vault_path": ocr.get("_vault_path") or "",
                        "use_for_coding": True,
                        "email_reminder_enabled": True,
                    }
                )
                if not existing_match:
                    payload.update(
                        {
                            "interest_account_code": "Loan Interest",
                            "principal_account_code": "Principal Loan",
                            "reminder_months_before": int(cfg.get("default_reminder_months") or 2),
                        }
                    )
                ok, msg, _lid = save_property_loan(
                    payload,
                    log_rate_history=o_log,
                    rate_note=f"Screenshot import ({ocr.get('_used_model', 'Gemini')})",
                )
                st.session_state["_loan_flash"] = (ok, msg)
                st.session_state.pop(ocr_result_key, None)
                st.session_state.pop(import_location_key, None)
                st.session_state[show_import_key] = False
                st.rerun()
    else:
        st.info(
            "Upload a screenshot on the left, then click **🔍 Extract Loan Details with Gemini**. The extracted fields will appear here for you to review and edit before saving."
        )
