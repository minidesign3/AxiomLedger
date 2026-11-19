"""
axiom_ui_loans
==============

Property Loans & Bank Registry block (phase D19): orchestrator, header, summary
strip and the active-rate spotlight. The importer panel lives in
``axiom_ui_loans_import``; the overview, dossiers and edit form in
``axiom_ui_loans_dossiers``.
"""

from __future__ import annotations

from datetime import date
from functools import partial

import streamlit as st

from axiom_core import _parse_loan_date
from axiom_email import get_smtp_config
from axiom_loan_crud import (
    check_and_send_due_loan_reminders,
    get_due_loan_reminders,
    get_loan_target_entity,
    get_property_loans,
)
from axiom_loans import loan_milestones
from axiom_properties import get_entity_properties
from axiom_ui_components import _render_loan_card_html
from axiom_ui_loans_common import LoanBlockContext
from axiom_ui_loans_dossiers import render_loan_dossiers, render_loan_overview_table
from axiom_ui_loans_import import render_loan_screenshot_importer


def _open_import_top(show_key: str, loc_key: str) -> None:
    """on_click handler: open the screenshot importer above the summary strip."""
    st.session_state[show_key] = True
    st.session_state[loc_key] = "top"


def build_loan_block_context(key_prefix: str = "loans") -> LoanBlockContext:
    """Collect the target entity's loans, properties and SMTP config."""
    target = get_loan_target_entity()
    props = [
        p
        for p in get_entity_properties(target)
        if p.strip().lower() not in ("general / portfolio", "general / portfolio-wide")
    ]
    return LoanBlockContext(
        key_prefix=key_prefix,
        target=target,
        loans=get_property_loans(target),
        props=props,
        cfg=get_smtp_config(),
        show_import_key=f"_{key_prefix}_show_loan_import",
        ocr_result_key=f"_{key_prefix}_loan_ocr_result",
        ocr_image_key=f"_{key_prefix}_loan_ocr_image",
        ocr_target_key=f"_{key_prefix}_ocr_target_prop",
        import_location_key=f"_{key_prefix}_loan_import_loc",
        expanded_prop_key=f"_{key_prefix}_expanded_prop",
    )


def render_loan_header(
    ctx: LoanBlockContext,
    eyebrow_title: str,
    show_page_title: bool,
) -> None:
    """Eyebrow/title row plus the import-from-screenshot primary action."""
    hc1, hc2 = st.columns([3.2, 1.1])
    with hc1:
        st.markdown(f'<div class="axiom-eyebrow">{eyebrow_title}</div>', unsafe_allow_html=True)
        if show_page_title:
            st.title("Property Loans & Bank Registry")
        else:
            st.markdown(
                "<h3 style='margin: 0; font-size: 1.08rem;'>BT Property Holdings Ltd — Properties, Bank Accounts, Current Interest Rates &amp; Rate Expiry Dates</h3>",
                unsafe_allow_html=True,
            )
    with hc2:
        st.button(
            "📷 Import Loan from Screenshot",
            type="primary",
            use_container_width=True,
            key=f"{ctx.key_prefix}_btn_open_screenshot_import",
            on_click=partial(_open_import_top, ctx.show_import_key, ctx.import_location_key),
        )


def render_loan_summary_strip(ctx: LoanBlockContext) -> None:
    """Portfolio metrics, due-reminder warning and the send-all action."""
    key_prefix = ctx.key_prefix
    loans = ctx.loans
    props = ctx.props

    total_drawn = sum(float(x.get("original_drawdown_amount") or 0) for x in loans)
    total_bal = sum(
        float(x.get("current_balance") or 0)
        or float(x.get("loan_limit") or 0)
        or float(x.get("original_drawdown_amount") or 0)
        for x in loans
    )
    wavg = (
        sum(
            float(x.get("interest_rate_pct") or 0)
            * (
                float(x.get("current_balance") or 0)
                or float(x.get("loan_limit") or 0)
                or float(x.get("original_drawdown_amount") or 0)
            )
            for x in loans
        )
        / total_bal
        if total_bal
        else 0.0
    )
    due = get_due_loan_reminders()
    upcoming = []
    for x in loans:
        for ms in loan_milestones(x):
            if 0 <= ms["days_remaining"] <= 60:
                upcoming.append(ms)
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Properties / Loans", f"{len(props)} / {len(loans)}")
    m2.metric("Original Drawdown", f"${total_drawn:,.0f}")
    m3.metric("Balance (recorded)", f"${total_bal:,.0f}")
    m4.metric("Weighted Avg Rate", f"{wavg:.2f}% p.a.")
    m5.metric("Expiries ≤60 days / Due emails", f"{len(upcoming)} / {len(due)}")

    if due:
        st.warning(
            f"🔔 {len(due)} reminder(s) inside the 2-month window and not yet emailed. Configure SMTP in ⚙ Settings → 05 // Email & Loan Reminders, then:"
        )
        if st.button("✉ Send All Due Reminders Now", type="primary", key=f"{key_prefix}_send_all_due"):
            n, msgs = check_and_send_due_loan_reminders(trigger_source=f"manual_{key_prefix}")
            st.session_state["_loan_flash"] = (n > 0, f"Sent {n}. " + " | ".join(msgs))
            st.rerun()


def render_loan_spotlight(ctx: LoanBlockContext) -> None:
    """The active-rate cards, sorted by rate-expiry date."""
    loans = ctx.loans

    st.markdown(
        '<div class="axiom-eyebrow">⭐ ACTIVE LOANS — CURRENT INTEREST RATE &amp; INTEREST RATE EXPIRY</div>',
        unsafe_allow_html=True,
    )
    active_loans = [
        x
        for x in loans
        if float(x.get("interest_rate_pct") or 0) > 0 or x.get("fixed_rate_review_date") or x.get("account_number")
    ]
    if active_loans:
        cols = st.columns(2)
        for idx, loan in enumerate(
            sorted(
                active_loans,
                key=lambda z: (_parse_loan_date(z.get("fixed_rate_review_date")) or date.max),
            )
        ):
            with cols[idx % 2]:
                st.markdown(_render_loan_card_html(loan), unsafe_allow_html=True)
    else:
        st.caption("No active loan rates recorded yet — use 📷 Import Loan from Screenshot above.")


def render_property_loans_block(
    key_prefix: str = "loans",
    eyebrow_title: str = "STEP 2 // PROPERTY LOANS & BANK REGISTRY — PROPERTIES & WHAT EACH LOAN IS (BT PROPERTY HOLDINGS LTD)",
    show_page_title: bool = False,
    show_overview_and_dossiers: bool = False,
) -> None:
    """
    Reusable Property Loans & Bank Registry block for BT Property Holdings Ltd.
    Renders on:
      - Front Page (Step 2 / Second Block for BT Property Holdings Ltd)
      - Settings Drawer (⚙ Settings -> Property Loans & Reminders)
      - Dedicated Loans page (views/loans.py)
    """
    ctx = build_loan_block_context(key_prefix)

    with st.container(border=True):
        render_loan_header(ctx, eyebrow_title, show_page_title)

        flash = st.session_state.pop("_loan_flash", None)
        if isinstance(flash, tuple) and len(flash) == 2:
            if flash[0]:
                st.success(flash[1])
            else:
                st.error(flash[1])

        if (
            st.session_state.get(ctx.show_import_key)
            or st.session_state.get(ctx.ocr_result_key)
        ) and st.session_state.get(ctx.import_location_key, "top") == "top":
            render_loan_screenshot_importer(ctx)

        render_loan_summary_strip(ctx)
        render_loan_spotlight(ctx)

        if not show_overview_and_dossiers:
            return

        render_loan_overview_table(ctx)

        st.markdown('<div class="axiom-divider" style="margin: 0.6rem 0;"></div>', unsafe_allow_html=True)

        render_loan_dossiers(ctx)
