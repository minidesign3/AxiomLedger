"""
axiom_ui_setup_matrix
=====================

Item + Property setup matrix (phase D18): the quick-add card and the public
``render_item_property_setup_matrix`` entry point. Formerly a single ~737-line
function in ``utils.py``; now an orchestrator over sub-renderers here and in
``axiom_ui_setup_rules`` / ``axiom_ui_setup_editor`` / ``axiom_ui_setup_common``.
Widget keys and the order of Streamlit calls are unchanged (verified against the
pre-refactor implementation with a recording fake of ``streamlit``).
"""

from __future__ import annotations

import streamlit as st

from axiom_account_codes import (
    add_entity_account_code,
    get_entity_account_codes,
    rename_entity_account_code,
)
from axiom_core import DEFAULT_CUSTOM_ACCOUNT_CODES, UNASSIGNED_PROPERTY
from axiom_properties import get_entity_properties, is_portfolio_wide_code, set_entity_code_scope
from axiom_rules import get_memorized_rules, save_memorized_rule
from axiom_state import get_active_entity, get_active_tax_year
from axiom_ui_setup_common import MatrixContext, reapply_rules_and_sync
from axiom_ui_setup_rules import render_saved_rules_card


def render_item_property_setup_matrix(
    entity: str | None = None,
    tax_year: str | None = None,
    key_prefix: str = "item_prop_matrix",
) -> None:
    """
    Clean, side-by-side 3-Column Item + Property Setup UI so the user can easily specify:
      Column 1: Bank Text / Payee / Loan / Policy / Tenant (e.g. '8020AON Stylecover', 'Loan 91-02', 'Smith Rent')
      Column 2: What is the Item? (e.g. 'Insurance', 'Loan Interest', 'Rent Income', 'Rates', 'Bodycorp')
      Column 3: Which Property? (e.g. 'Property 2 / 33 Blampied', '303 Weymouth', or 'General / Portfolio')
    Can be rendered directly inside the Ingestion Board drawer, Settings, or Dashboard Tab 01.
    """
    ctx = _build_matrix_context(entity, tax_year, key_prefix)
    _render_quick_setup_card(ctx)
    render_saved_rules_card(ctx)


def _build_matrix_context(
    entity: str | None, tax_year: str | None, key_prefix: str
) -> MatrixContext:
    """Resolve entity / year and the sorted item and property lists used by every section."""
    resolved_entity = get_active_entity(entity)
    resolved_year = get_active_tax_year(tax_year)
    properties_list = get_entity_properties(resolved_entity)
    account_codes_list = sorted(get_entity_account_codes(resolved_entity), key=lambda s: s.lower())
    memorized_list = get_memorized_rules(resolved_entity)

    per_prop_items = sorted([c for c in account_codes_list if not is_portfolio_wide_code(c, resolved_entity)], key=lambda s: s.lower())
    portfolio_items = sorted([c for c in account_codes_list if is_portfolio_wide_code(c, resolved_entity)], key=lambda s: s.lower())
    alphabetical_items = sorted(list(dict.fromkeys(per_prop_items + portfolio_items)), key=lambda s: s.lower())
    real_props = sorted([p for p in properties_list if p.lower() != "general / portfolio"], key=lambda s: s.lower())

    all_props_option = f"🏠 All Properties ({len(real_props)} Houses)"
    return MatrixContext(
        resolved_entity=resolved_entity,
        resolved_year=resolved_year,
        key_prefix=key_prefix,
        alphabetical_items=alphabetical_items,
        memorized_list=memorized_list,
        real_props=real_props,
        all_props_option=all_props_option,
    )


def _render_quick_setup_card(ctx: MatrixContext) -> None:
    """Quick-add card: item rename/add popover plus the item / property / pattern row."""
    resolved_entity = ctx.resolved_entity
    # Quick Add Row in compact sidebar-aware columns: What is the Item? | Which Property? | Bank Text / CSV Tokens
    with st.container(border=True):
        top_hdr_c1, top_hdr_c2 = st.columns([2.7, 1.3])
        with top_hdr_c1:
            st.markdown(
                f'<div class="axiom-eyebrow">QUICK SETUP // LINK ANY ITEM (INSURANCE, LOAN INTEREST, RENT) TO A SPECIFIC PROPERTY ({resolved_entity.upper()})</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "Select or edit **What is the Item** (e.g. `Insurance`, `Tower Insurance`, `Loan Interest`, `Rent Income`, `Rates`), "
                "pick **Which Property** it belongs to (or `Auto-detect House from CSV Line` so house names like `Damian`, `Queen`, `Blampied` on the CSV row pair up automatically), "
                "and enter the **Bank Statement Text / Payee / Policy Ref**."
            )
        with top_hdr_c2:
            _render_item_names_popover(ctx)

        _render_quick_add_row(ctx)


def _render_item_names_popover(ctx: MatrixContext) -> None:
    """Popover for renaming an existing item or adding a new one."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    alphabetical_items = ctx.alphabetical_items
    with st.popover("✏️ Edit / Rename or Add Item Names", use_container_width=True):
        st.markdown("**Rename an Existing Item**")
        all_existing_items = alphabetical_items
        if all_existing_items:
            ren_src_item = st.selectbox(
                "Pick Item to Rename",
                options=all_existing_items,
                key=f"_{key_prefix}_ren_src_{resolved_entity}",
            )
            ren_dst_item = st.text_input(
                "New Item Name",
                value=ren_src_item,
                placeholder="e.g. Tower Insurance",
                key=f"_{key_prefix}_ren_dst_{resolved_entity}_{ren_src_item}",
            )
            if st.button(
                "💾 Rename Item Everywhere",
                key=f"_{key_prefix}_ren_btn_{resolved_entity}",
                type="primary",
                use_container_width=True,
            ):
                ok_rn, msg_rn = rename_entity_account_code(
                    old_code=ren_src_item,
                    new_code=ren_dst_item,
                    entity=resolved_entity,
                )
                if ok_rn:
                    reapply_rules_and_sync(ctx)
                    st.success(msg_rn)
                    st.rerun()
                else:
                    st.error(msg_rn)
        st.markdown("---")
        st.markdown("**Add a New Item**")
        new_custom_item_name = st.text_input(
            "New Item Name",
            placeholder="e.g. Tower Insurance, Lawn Mowing...",
            key=f"_{key_prefix}_add_new_item_txt_{resolved_entity}",
        )
        new_custom_is_port = st.checkbox(
            "Portfolio-Wide (General / Company Overhead)",
            value=False,
            key=f"_{key_prefix}_add_new_item_port_{resolved_entity}",
        )
        if st.button(
            "＋ Add Item to List",
            key=f"_{key_prefix}_add_new_item_btn_{resolved_entity}",
            use_container_width=True,
        ):
            ok_add_c, msg_add_c = add_entity_account_code(new_custom_item_name, entity=resolved_entity)
            if ok_add_c:
                set_entity_code_scope(new_custom_item_name, is_portfolio_wide=new_custom_is_port, entity=resolved_entity)
                st.success(msg_add_c)
                st.rerun()
            else:
                st.error(msg_add_c)


def _render_quick_add_row(ctx: MatrixContext) -> None:
    """Item / Property / Bank-text row with the Save button."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    alphabetical_items = ctx.alphabetical_items
    real_props = ctx.real_props
    all_props_option = ctx.all_props_option
    qa_c1, qa_c2, qa_c3, qa_c4 = st.columns([1.25, 1.2, 1.45, 0.65])
    with qa_c1:
        ordered_items = alphabetical_items
        base_item_opts = ordered_items if ordered_items else sorted(list(DEFAULT_CUSTOM_ACCOUNT_CODES), key=lambda s: s.lower())
        default_item_idx = base_item_opts.index("Insurance") if "Insurance" in base_item_opts else 0
        selected_qa_item = st.selectbox(
            "1. What is the Item? (Pick or Edit below)",
            options=base_item_opts,
            index=default_item_idx,
            key=f"_{key_prefix}_qa_item_{resolved_entity}",
        )
        custom_qa_item = st.text_input(
            "Edit / Type Custom Item Name (optional)",
            value=selected_qa_item,
            help="Change the Item name right here (e.g. change 'Insurance' to 'Tower Insurance') before clicking Save.",
            key=f"_{key_prefix}_qa_custom_item_{resolved_entity}_{selected_qa_item}",
        )
        picked_item = (custom_qa_item or "").strip() or selected_qa_item
    is_port_item = is_portfolio_wide_code(picked_item, resolved_entity)
    with qa_c2:
        if is_port_item:
            prop_choices = ["General / Portfolio"]
            picked_prop = st.selectbox(
                "2. Which Property?",
                options=prop_choices,
                disabled=True,
                help="This item is Portfolio-Wide (company overhead), so it automatically assigns to General / Portfolio.",
                key=f"_{key_prefix}_qa_prop_port_{resolved_entity}",
            )
        else:
            prop_choices = (
                [UNASSIGNED_PROPERTY]
                + real_props
                + ([all_props_option] if real_props else [])
                + ["General / Portfolio"]
            )
            picked_prop = st.selectbox(
                "2. Which Property?",
                options=prop_choices,
                format_func=lambda x: "Auto-detect House from CSV Line" if x == UNASSIGNED_PROPERTY else x,
                key=f"_{key_prefix}_qa_prop_{resolved_entity}",
            )
    with qa_c3:
        picked_pattern = st.text_input(
            "3. Bank Text / Payee (or Vendor + House, e.g. 'Tower + Damian')",
            placeholder="e.g. Tower, 6851Tower Insurance, or Tower + Damian...",
            key=f"_{key_prefix}_qa_pat_{resolved_entity}",
        )
    with qa_c4:
        st.markdown("<div style='height: 1.65rem;'></div>", unsafe_allow_html=True)
        if st.button(
            "＋ Save",
            key=f"_{key_prefix}_qa_btn_{resolved_entity}",
            type="primary",
            use_container_width=True,
        ):
            clean_qa_pat = (picked_pattern or "").strip()
            if not clean_qa_pat:
                st.error("Description pattern cannot be empty.")
            else:
                if picked_item != selected_qa_item:
                    add_entity_account_code(picked_item, entity=resolved_entity)
                if not is_port_item and picked_prop == all_props_option and real_props:
                    saved_cnt = 0
                    for p_house in real_props:
                        ok_each, _ = save_memorized_rule(
                            pattern=clean_qa_pat,
                            property_name=p_house,
                            account_code=picked_item,
                            entity=resolved_entity,
                            source="Item + Property Setup",
                        )
                        if ok_each:
                            saved_cnt += 1
                    reapply_rules_and_sync(ctx)
                    st.success(
                        f"Saved '{clean_qa_pat}' → {picked_item} across all {saved_cnt} properties ({', '.join(real_props)})!"
                    )
                    st.rerun()
                else:
                    ok_s, msg_s = save_memorized_rule(
                        pattern=clean_qa_pat,
                        property_name="General / Portfolio" if is_port_item else picked_prop,
                        account_code=picked_item,
                        entity=resolved_entity,
                        source="Item + Property Setup",
                    )
                    if ok_s:
                        reapply_rules_and_sync(ctx)
                        st.success(msg_s)
                        st.rerun()
                    else:
                        st.error(msg_s)

