"""
axiom_ui_components
===================

Small, shared Streamlit widgets and HTML snippets used across several pages:
the property editor, the NZ bank logo badges and loan summary card, the global
background-daemon status indicator, the AI connection pill and the entity edit
popover.

Extracted from ``utils.py`` (phase D17) with byte-identical bodies and
re-exported there for existing call sites.
"""

from __future__ import annotations

import re

import streamlit as st

from axiom_core import (
    _fmt_loan_date,
    _parse_loan_date,
    _subtract_months,
    infer_nz_bank_from_account,
)
from axiom_entities import delete_entity, rename_entity
from axiom_loans import _loan_rate_status_tuple
from axiom_properties import (
    add_entity_property,
    get_entity_properties,
    remove_entity_property,
    rename_entity_property,
)
from axiom_state import get_active_entity, get_entity_jurisdiction
from axiom_tasks import get_background_status_summary


def render_property_editor_widget(
    entity: str | None = None,
    key_prefix: str = "prop_mgr",
) -> None:
    """
    Reusable widget to view, rename (edit in-place), add, or remove Properties
    (e.g. Property 1, Property 2, etc.) for the specified entity.
    """
    resolved = get_active_entity(entity)
    properties_list = get_entity_properties(resolved)

    add_c1, add_c2 = st.columns([2.3, 1.0])
    with add_c1:
        new_prop_name = st.text_input(
            "Add New Property",
            placeholder="e.g. Property 6 - 102 Hobson St",
            key=f"_{key_prefix}_new_prop_{resolved}",
            label_visibility="collapsed",
        )
    with add_c2:
        if st.button(
            "+ Add Property",
            key=f"_{key_prefix}_btn_add_prop_{resolved}",
            type="primary",
            use_container_width=True,
        ):
            ok_p, msg_p = add_entity_property(new_prop_name, resolved)
            if ok_p:
                st.success(msg_p)
                st.rerun()
            else:
                st.error(msg_p)

    st.caption("Edit any property name below (e.g. rename `Property 1` to `14 Queen St`) and click **Save**:")
    for idx, p_name in enumerate(properties_list):
        p_c1, p_c2, p_c3 = st.columns([2.6, 0.85, 0.55])
        with p_c1:
            edited_val = st.text_input(
                f"Property {idx + 1}",
                value=p_name,
                key=f"_{key_prefix}_edit_prop_{idx}_{p_name}_{resolved}",
                label_visibility="collapsed",
            )
        with p_c2:
            if st.button(
                "Save",
                key=f"_{key_prefix}_save_prop_{idx}_{p_name}_{resolved}",
                disabled=(edited_val.strip() == p_name or not edited_val.strip()),
                use_container_width=True,
            ):
                ok_rn, msg_rn = rename_entity_property(p_name, edited_val, resolved)
                if ok_rn:
                    st.success(msg_rn)
                    st.rerun()
                else:
                    st.error(msg_rn)
        with p_c3:
            if st.button(
                "✕",
                key=f"_{key_prefix}_del_prop_{idx}_{p_name}_{resolved}",
                disabled=len(properties_list) <= 1,
                help=f"Remove '{p_name}'",
                use_container_width=True,
            ):
                ok_rm, _ = remove_entity_property(p_name, resolved)
                if ok_rm:
                    st.rerun()


def _nz_bank_logo_svg(bank_name: str) -> str:
    """Return a compact, dark-theme-friendly inline SVG logo badge for NZ banks."""
    b = (bank_name or "").strip().lower()
    if not b or b == "bank not set":
        return ""
    if "westpac" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 112 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="Westpac NZ">'
            '<rect width="112" height="28" rx="5" fill="#DA1710"/>'
            '<path d="M7 8.5h3.2l2.1 7.3 2.4-6.6h2.6l2.4 6.6 2.1-7.3H25l-3.8 11h-2.9L16 13.2l-2.3 6.3h-2.9L7 8.5z" fill="#FFFFFF"/>'
            '<text x="29" y="18.5" font-family="Arial,Helvetica,sans-serif" font-size="13" '
            'font-weight="700" fill="#FFFFFF">Westpac</text>'
            '<text x="87" y="18.5" font-family="Arial,Helvetica,sans-serif" font-size="8.5" '
            'font-weight="700" fill="#FFFFFF">NZ</text>'
            "</svg>"
        )
    if "bnz" in b or "bank of new zealand" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 88 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="BNZ">'
            '<rect width="88" height="28" rx="5" fill="#002F6C"/>'
            '<polygon points="15,5 17.2,11.2 23.6,11.2 18.4,15 20.3,21.2 15,17.4 9.7,21.2 11.6,15 6.4,11.2 12.8,11.2" fill="#F2A900"/>'
            '<text x="30" y="19.5" font-family="Arial,Helvetica,sans-serif" font-size="15.5" '
            'font-weight="800" letter-spacing="0.5" fill="#FFFFFF">bnz</text>'
            "</svg>"
        )
    if "anz" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 88 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="ANZ NZ">'
            '<rect width="88" height="28" rx="5" fill="#007DBA"/>'
            '<circle cx="15" cy="9.5" r="2.2" fill="#FFFFFF"/>'
            '<path d="M9.5 19.5c0-3.2 2.4-5.6 5.5-5.6s5.5 2.4 5.5 5.6h-2.5c0-1.8-1.3-3.1-3-3.1s-3 1.3-3 3.1H9.5z" fill="#FFFFFF"/>'
            '<text x="27" y="19" font-family="Arial,Helvetica,sans-serif" font-size="14.5" '
            'font-weight="800" letter-spacing="0.8" fill="#FFFFFF">ANZ</text>'
            '<text x="64" y="19" font-family="Arial,Helvetica,sans-serif" font-size="8.5" '
            'font-weight="700" fill="#BFE8FF">NZ</text>'
            "</svg>"
        )
    if "asb" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 78 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="ASB">'
            '<rect width="78" height="28" rx="5" fill="#FFCC00"/>'
            '<rect x="7" y="21.5" width="64" height="2.2" rx="1" fill="#111111"/>'
            '<text x="39" y="18" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            'font-size="15" font-weight="900" letter-spacing="1.2" fill="#111111">ASB</text>'
            "</svg>"
        )
    if "kiwi" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 98 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="Kiwibank">'
            '<rect width="98" height="28" rx="5" fill="#1A1D21" stroke="#65B32E" stroke-width="1.2"/>'
            '<circle cx="14" cy="14" r="5.5" fill="#65B32E"/>'
            '<path d="M12.5 14.5l3-3 2.5 1.5-2.2 3z" fill="#111111"/>'
            '<text x="25" y="18.5" font-family="Arial,Helvetica,sans-serif" font-size="12.5" '
            'font-weight="700" fill="#FFFFFF">Kiwibank</text>'
            "</svg>"
        )
    if "tsb" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 68 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="TSB">'
            '<rect width="68" height="28" rx="5" fill="#D50032"/>'
            '<text x="34" y="19" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            'font-size="14" font-weight="800" letter-spacing="1" fill="#FFFFFF">TSB</text>'
            "</svg>"
        )
    if "sbs" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 68 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="SBS Bank">'
            '<rect width="68" height="28" rx="5" fill="#C8102E"/>'
            '<text x="34" y="19" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            'font-size="14" font-weight="800" letter-spacing="1" fill="#FFFFFF">SBS</text>'
            "</svg>"
        )
    if "heartland" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 94 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="Heartland Bank">'
            '<rect width="94" height="28" rx="5" fill="#005EB8"/>'
            '<text x="47" y="18.5" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            'font-size="12" font-weight="700" fill="#FFFFFF">Heartland</text>'
            "</svg>"
        )
    if "co-op" in b or "cooperative" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 78 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="Co-operative Bank">'
            '<rect width="78" height="28" rx="5" fill="#007A5E"/>'
            '<text x="39" y="18.5" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            'font-size="12.5" font-weight="700" fill="#FFFFFF">Co-op</text>'
            "</svg>"
        )
    return ""


def _render_loan_card_html(loan: dict) -> str:
    import html as _html_mod

    def _e(v) -> str:
        return _html_mod.escape(str(v or ""))

    colour, status, _days = _loan_rate_status_tuple(loan)
    rate = float(loan.get("interest_rate_pct") or 0)
    fx = _parse_loan_date(loan.get("fixed_rate_review_date"))
    months = int(loan.get("reminder_months_before") or 2)
    win = _subtract_months(fx, months) if fx else None
    bal = (
        float(loan.get("current_balance") or 0)
        or float(loan.get("loan_limit") or 0)
        or float(loan.get("original_drawdown_amount") or 0)
    )
    bank = (
        loan.get("bank_name")
        or infer_nz_bank_from_account(loan.get("account_number") or "")
        or "Bank not set"
    )
    bank_logo_html = _nz_bank_logo_svg(bank)
    prod = f" · {_e(loan.get('account_product_type'))}" if loan.get("account_product_type") else ""
    email_icon = "✉ 2mth email ON" if loan.get("email_reminder_enabled") else "✉ email OFF"
    win_txt = (
        f"2mth email {_fmt_loan_date(win)}"
        if (win and loan.get("email_reminder_enabled"))
        else email_icon
    )
    repay_str = ""
    if float(loan.get("regular_repayment_amount") or 0) > 0:
        est_tag = " (est.)" if loan.get("next_payment_is_estimated") else ""
        repay_str = f" · Next debit: ${float(loan.get('regular_repayment_amount') or 0):,.2f}{est_tag} ({_e(loan.get('repayment_frequency') or 'Monthly')})"
    logo_block = (
        f'<div style="margin-bottom:4px;display:flex;justify-content:flex-end;">{bank_logo_html}</div>'
        if bank_logo_html
        else ""
    )
    return f"""
<div style="border:1px solid #2A2D34;border-left:4px solid {colour};background:#16181C;border-radius:8px;padding:14px 16px;margin-bottom:12px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
    <div style="font-weight:700;font-size:1.02rem;color:#E2E4E9;">🏠 {_e(loan.get('property_name'))}</div>
    <div style="text-align:right;flex-shrink:0;">
      {logo_block}
      <div style="font-size:0.75rem;color:#8E939F;">{_e(loan.get('loan_label'))}</div>
    </div>
  </div>
  <div style="display:flex;gap:28px;margin-top:10px;flex-wrap:wrap;">
    <div>
      <div style="font-size:0.62rem;letter-spacing:0.12em;color:#8E939F;">CURRENT INTEREST RATE</div>
      <div style="font-size:1.85rem;font-weight:800;line-height:1.05;color:#E2E4E9;">{rate:.2f}%<span style="font-size:0.8rem;font-weight:600;color:#8E939F;"> p.a.</span></div>
      <div style="font-size:0.75rem;color:#A0A5B1;">{_e(loan.get('interest_type'))} · {_e(loan.get('repayment_type'))}</div>
    </div>
    <div>
      <div style="font-size:0.62rem;letter-spacing:0.12em;color:#8E939F;">INTEREST RATE EXPIRY</div>
      <div style="font-size:1.3rem;font-weight:800;line-height:1.15;color:{colour};">{_fmt_loan_date(fx)}</div>
      <div style="font-size:0.75rem;color:{colour};font-weight:700;">{status}</div>
      <div style="font-size:0.72rem;color:#8E939F;">{win_txt if fx else ''}</div>
    </div>
  </div>
  <div style="margin-top:10px;padding-top:8px;border-top:1px solid #2A2D34;font-size:0.78rem;color:#A0A5B1;">
    {_e(bank)}{prod} · <span style="font-family:monospace;">{_e(loan.get('account_number') or '—')}</span> · Balance ${bal:,.0f}{repay_str}
  </div>
</div>"""


def render_global_status_indicator() -> None:
    """
    Reusable UI component that reads the status of background threads and renders
    a compact, technical charcoal/metallic Status Indicator card.
    """
    summary = get_background_status_summary()
    overall = str(summary["overall"])

    badge_styles = {
        "Running": ("#D5D8E0", "#2A2E37", "#8E939F", "▪ RUNNING"),
        "Paused": ("#B8BCC6", "#23262D", "#6C727F", "▪ PAUSED"),
        "Error": ("#E2E4E9", "#2D2628", "#8A6B70", "▪ ERROR"),
        "Online": ("#8E939F", "#1A1C20", "#3A3F4B", "▪ ONLINE"),
    }
    text_color, bg_color, border_color, pill_text = badge_styles.get(
        overall, badge_styles["Online"]
    )

    st.markdown(
        f"""
        <div style="
            background-color: #16181C;
            border: 1px solid #2A2D34;
            border-radius: 4px;
            padding: 0.65rem 0.95rem;
            display: flex;
            flex-direction: column;
            justify-content: center;
        ">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.2rem;">
                <span class="axiom-eyebrow" style="margin: 0;">BACKGROUND DAEMONS</span>
                <span style="
                    font-size: 0.66rem;
                    font-weight: 600;
                    letter-spacing: 0.1em;
                    color: {text_color};
                    background-color: {bg_color};
                    border: 1px solid {border_color};
                    border-radius: 3px;
                    padding: 0.08rem 0.4rem;
                ">{pill_text}</span>
            </div>
            <div style="font-size: 0.8rem; font-weight: 600; color: #E2E4E9; letter-spacing: -0.01em;">
                {summary['headline']}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_connection_badge(title: str, status_dict: dict[str, object], is_selected: bool) -> str:
    """Return HTML snippet for a live AI connection status pill in the top toolbar."""
    connected = bool(status_dict.get("connected", False))
    label = str(status_dict.get("label", "UNVERIFIED"))
    dot = "●" if connected else "○"
    border_col = "#A0A5B1" if connected else "#3A3F4B"
    bg_col = "#22262E" if is_selected else "#16181C"
    text_col = "#F3F4F6" if connected else "#8E939F"
    active_tag = " [ACTIVE]" if is_selected else ""

    return (
        f'<div style="background-color: {bg_col}; border: 1px solid {border_col}; '
        f'border-radius: 3px; padding: 0.35rem 0.65rem; display: flex; align-items: center; '
        f'justify-content: space-between; margin-bottom: 0.35rem;">'
        f'<span style="font-size: 0.7rem; font-weight: 600; letter-spacing: 0.08em; color: #A0A5B1;">'
        f'{title}{active_tag}</span>'
        f'<span style="font-size: 0.7rem; font-weight: 600; color: {text_col}; font-family: monospace;">'
        f'{dot} {label}</span>'
        f'</div>'
    )


def _render_entity_edit_popover(entity_name: str, key_prefix: str) -> None:
    """
    Render a compact far-right pin/pen edit button that opens an inline popover
    allowing the operator to edit the entity name and tax jurisdiction.
    """
    safe_slug = re.sub(r"[^a-zA-Z0-9]+", "_", entity_name).strip("_")
    current_jur = get_entity_jurisdiction(entity_name)
    with st.popover("📌", help=f"Edit '{entity_name}' name or jurisdiction", use_container_width=True):
        st.markdown('<div class="axiom-eyebrow">EDIT ORGANIZATION</div>', unsafe_allow_html=True)
        edited_name = st.text_input(
            "Entity / Portfolio Name",
            value=entity_name,
            key=f"_{key_prefix}_edit_ent_name_{safe_slug}",
        )
        jur_options = ["NZ IRD", "USA IRS"]
        edited_jur_label = st.selectbox(
            "Tax Jurisdiction",
            options=jur_options,
            index=1 if current_jur == "US" else 0,
            key=f"_{key_prefix}_edit_ent_jur_{safe_slug}",
        )
        if st.button(
            "Save Entity Changes",
            key=f"_{key_prefix}_btn_save_ent_{safe_slug}",
            type="primary",
            use_container_width=True,
        ):
            new_jur_code = "US" if edited_jur_label.startswith("US") else "NZ"
            ok, msg = rename_entity(entity_name, edited_name, new_jur_code)
            st.session_state["_entity_edit_flash"] = (ok, msg)
            st.rerun()
        if st.button(
            "🗑 Remove Organization",
            key=f"_{key_prefix}_btn_del_ent_{safe_slug}",
            use_container_width=True,
        ):
            ok_del, msg_del = delete_entity(entity_name)
            st.session_state["_entity_edit_flash"] = (ok_del, msg_del)
            st.rerun()
