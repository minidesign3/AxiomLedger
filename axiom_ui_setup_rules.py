"""
axiom_ui_setup_rules
====================

The "saved Item + Property rules" card of the setup matrix (phase D18): filter
bar, filtered/sorted view model, paged compact rows and the spreadsheet grid.
The edit/copy panel lives in ``axiom_ui_setup_editor``.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from axiom_core import UNASSIGNED_PROPERTY
from axiom_properties import is_portfolio_wide_code
from axiom_rules import replace_memorized_rules
from axiom_ui_setup_common import MatrixContext, RulesView, reapply_rules_and_sync
from axiom_ui_setup_editor import render_rule_editor_panel


def render_saved_rules_card(ctx: MatrixContext) -> None:
    """Saved Item + Property rules card: filters, editor panel and list / grid view."""
    # Sidebar-Responsive Compact Rules List with per-row ✏️ Edit Button + Optional Bulk Grid Mode
    with st.container(border=True):
        search_rule_q, filter_by_item, page_size_choice, view_mode = _render_rules_filter_bar(ctx)
        view = _build_rules_view(
            ctx, search_rule_q, filter_by_item, page_size_choice, view_mode
        )
        render_rule_editor_panel(ctx, view)
        if view_mode == "Compact Rows (✏️ Edit)":
            _render_rules_compact_rows(ctx, view)
        else:
            _render_rules_grid(ctx, view)


def _render_rules_filter_bar(ctx: MatrixContext) -> tuple[str, str, str, str]:
    """Render the header/search/filter/page-size/view-mode row and return the choices."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    alphabetical_items = ctx.alphabetical_items
    memorized_list = ctx.memorized_list
    hdr_c1, hdr_c2, hdr_c3, hdr_c4, hdr_c5 = st.columns([1.45, 0.9, 0.85, 0.72, 0.78])
    with hdr_c1:
        st.markdown(
            f'<div class="axiom-eyebrow">SAVED ITEM + PROPERTY RULES ({len(memorized_list)} CONFIGURED) &mdash; CLICK ✏️ EDIT OR 📋 COPY ON ANY ROW</div>',
            unsafe_allow_html=True,
        )
    with hdr_c2:
        search_rule_q = st.text_input(
            "Search Rules",
            placeholder="🔍 Filter text, item, house...",
            key=f"_{key_prefix}_search_q_{resolved_entity}",
            label_visibility="collapsed",
        )
    with hdr_c3:
        filter_item_opts = ["All Items"] + alphabetical_items
        filter_by_item = st.selectbox(
            "Filter Table by Item",
            options=filter_item_opts,
            key=f"_{key_prefix}_flt_item_{resolved_entity}",
            label_visibility="collapsed",
        )
    with hdr_c4:
        page_size_choice = st.selectbox(
            "Rows per page",
            options=["15 per page", "30 per page", "50 per page", "All"],
            index=0,
            key=f"_{key_prefix}_pg_size_{resolved_entity}",
            label_visibility="collapsed",
            help="Choose how many rules to show on the page at once (15, 30, 50, or All).",
        )
    with hdr_c5:
        view_mode = st.selectbox(
            "View Mode",
            options=["Compact Rows (✏️ Edit)", "Spreadsheet Grid"],
            key=f"_{key_prefix}_view_mode_{resolved_entity}",
            label_visibility="collapsed",
        )
    return search_rule_q, filter_by_item, page_size_choice, view_mode


def _build_rules_view(
    ctx: MatrixContext,
    search_rule_q: str,
    filter_by_item: str,
    page_size_choice: str,
    view_mode: str,
) -> RulesView:
    """Build dropdown option lists and the filtered, sorted rule rows."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    alphabetical_items = ctx.alphabetical_items
    memorized_list = ctx.memorized_list
    real_props = ctx.real_props
    auto_label = "Auto-detect House from CSV Line"
    all_prop_dropdown_opts = list(dict.fromkeys([auto_label] + real_props + ["General / Portfolio"]))
    all_item_dropdown_opts = sorted(list(dict.fromkeys(alphabetical_items + ["Uncategorized"])), key=lambda s: s.lower())

    active_edit_state_key = f"_{key_prefix}_active_edit_idx_{resolved_entity}"
    active_edit_idx = st.session_state.get(active_edit_state_key, None)
    copy_mode_state_key = f"_{key_prefix}_copy_mode_{resolved_entity}"
    is_copy_mode = bool(st.session_state.get(copy_mode_state_key, False))

    # Build indexed list so edits/deletes target the exact rule in `memorized_list`
    indexed_rules: list[tuple[int, dict[str, str]]] = []
    for idx_r, r in enumerate(memorized_list):
        r_code = str(r.get("account_code", "")).strip() or "Uncategorized"
        r_prop = str(r.get("property", "")).strip()
        r_pat = str(r.get("pattern", "")).strip()
        r_src = str(r.get("source", "Saved")).strip()
        if is_portfolio_wide_code(r_code, resolved_entity):
            disp_prop = "General / Portfolio"
        elif not r_prop or r_prop == UNASSIGNED_PROPERTY:
            disp_prop = auto_label
        else:
            disp_prop = r_prop
            if disp_prop not in all_prop_dropdown_opts:
                all_prop_dropdown_opts.append(disp_prop)
        if r_code not in all_item_dropdown_opts:
            all_item_dropdown_opts.append(r_code)

        if filter_by_item != "All Items" and r_code != filter_by_item:
            continue
        if search_rule_q.strip():
            q_low = search_rule_q.strip().lower()
            if (
                q_low not in r_pat.lower()
                and q_low not in r_code.lower()
                and q_low not in disp_prop.lower()
            ):
                continue

        indexed_rules.append(
            (
                idx_r,
                {
                    "Item": r_code,
                    "Property": disp_prop,
                    "Pattern": r_pat,
                    "Source": r_src,
                },
            )
        )
    all_item_dropdown_opts.sort(key=lambda s: s.lower())
    indexed_rules.sort(
        key=lambda pair: (
            pair[1]["Item"].lower(),
            pair[1]["Pattern"].lower(),
            pair[1]["Property"].lower(),
        )
    )
    return RulesView(
        auto_label=auto_label,
        all_prop_dropdown_opts=all_prop_dropdown_opts,
        all_item_dropdown_opts=all_item_dropdown_opts,
        active_edit_state_key=active_edit_state_key,
        active_edit_idx=active_edit_idx,
        copy_mode_state_key=copy_mode_state_key,
        is_copy_mode=is_copy_mode,
        indexed_rules=indexed_rules,
        filter_by_item=filter_by_item,
        search_rule_q=search_rule_q,
        page_size_choice=page_size_choice,
        view_mode=view_mode,
    )


def _render_rules_compact_rows(ctx: MatrixContext, view: RulesView) -> None:
    """Paged, compact row list with per-row Edit / Copy buttons."""
    indexed_rules = view.indexed_rules
    if not indexed_rules:
        st.caption("No saved rules match your current filter.")
    else:
        cur_page, page_size = _render_rules_pagination(ctx, view)

        # Compact Column Header Row
        th_c1, th_c2, th_c3, th_c4, th_c5 = st.columns([1.35, 0.88, 1.07, 0.42, 0.42])
        th_c1.markdown("<span style='color:#8E939F;font-size:0.73rem;font-weight:600;'>BANK TEXT / CSV MATCH</span>", unsafe_allow_html=True)
        th_c2.markdown("<span style='color:#8E939F;font-size:0.73rem;font-weight:600;'>1. ITEM (CODE)</span>", unsafe_allow_html=True)
        th_c3.markdown("<span style='color:#8E939F;font-size:0.73rem;font-weight:600;'>2. PROPERTY</span>", unsafe_allow_html=True)
        th_c4.markdown("<span style='color:#8E939F;font-size:0.73rem;font-weight:600;'>EDIT</span>", unsafe_allow_html=True)
        th_c5.markdown("<span style='color:#8E939F;font-size:0.73rem;font-weight:600;'>COPY</span>", unsafe_allow_html=True)

        page_slice = indexed_rules[(cur_page - 1) * page_size : cur_page * page_size]
        for orig_idx, r_info in page_slice:
            _render_rule_row(ctx, view, orig_idx, r_info)


def _render_rules_pagination(ctx: MatrixContext, view: RulesView) -> tuple[int, int]:
    """Render the page caption / prev / next buttons; return (current page, page size)."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    indexed_rules = view.indexed_rules
    page_size_choice = view.page_size_choice
    total_filtered = len(indexed_rules)
    if page_size_choice == "30 per page":
        page_size = 30
    elif page_size_choice == "50 per page":
        page_size = 50
    elif page_size_choice == "All":
        page_size = max(1, total_filtered)
    else:
        page_size = 15

    total_pages = max(1, (total_filtered + page_size - 1) // page_size)
    page_state_key = f"_{key_prefix}_page_{resolved_entity}"
    cur_page = min(max(1, int(st.session_state.get(page_state_key, 1))), total_pages)

    if total_pages > 1:
        pg_c1, pg_c2, pg_c3 = st.columns([1.6, 1.2, 1.2])
        with pg_c1:
            st.caption(
                f"Showing rules **{(cur_page - 1) * page_size + 1}–{min(cur_page * page_size, total_filtered)}** of **{total_filtered}** ({page_size_choice})"
            )
        with pg_c2:
            if st.button(
                f"◀ Previous {page_size}",
                disabled=(cur_page <= 1),
                key=f"_{key_prefix}_prev_pg_{resolved_entity}",
                use_container_width=True,
            ):
                st.session_state[page_state_key] = cur_page - 1
                st.rerun()
        with pg_c3:
            if st.button(
                f"Next {page_size} ▶",
                disabled=(cur_page >= total_pages),
                key=f"_{key_prefix}_next_pg_{resolved_entity}",
                use_container_width=True,
            ):
                st.session_state[page_state_key] = cur_page + 1
                st.rerun()
    else:
        st.caption(f"Showing all **{total_filtered}** saved rule(s) on one page.")
    return cur_page, page_size


def _render_rule_row(
    ctx: MatrixContext, view: RulesView, orig_idx: int, r_info: dict[str, str]
) -> None:
    """Render one compact rule row with its Edit and Copy buttons."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    auto_label = view.auto_label
    active_edit_state_key = view.active_edit_state_key
    active_edit_idx = view.active_edit_idx
    copy_mode_state_key = view.copy_mode_state_key
    r_c1, r_c2, r_c3, r_c4, r_c5 = st.columns([1.35, 0.88, 1.07, 0.42, 0.42])
    is_editing_this = active_edit_idx is not None and int(active_edit_idx) == orig_idx
    pat_text = r_info["Pattern"]
    item_text = r_info["Item"]
    prop_text = r_info["Property"]
    prop_badge_color = "#60A5FA" if prop_text == auto_label else "#10B981"
    row_bg = "#1E2638" if is_editing_this else "#16181C"

    with r_c1:
        st.markdown(
            f"<div style='background:{row_bg};border:1px solid #2A2D34;border-radius:4px;padding:0.36rem 0.55rem;"
            f"font-family:JetBrains Mono,monospace;font-size:0.78rem;color:#F3F4F6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' "
            f"title='{pat_text}'>{pat_text}</div>",
            unsafe_allow_html=True,
        )
    with r_c2:
        st.markdown(
            f"<div style='background:{row_bg};border:1px solid #2A2D34;border-radius:4px;padding:0.36rem 0.55rem;"
            f"font-size:0.79rem;font-weight:600;color:#E5E7EB;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' "
            f"title='{item_text}'>{item_text}</div>",
            unsafe_allow_html=True,
        )
    with r_c3:
        short_prop = "🔍 Auto House from CSV" if prop_text == auto_label else prop_text
        st.markdown(
            f"<div style='background:{row_bg};border:1px solid #2A2D34;border-radius:4px;padding:0.36rem 0.55rem;"
            f"font-size:0.78rem;color:{prop_badge_color};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' "
            f"title='{prop_text}'>{short_prop}</div>",
            unsafe_allow_html=True,
        )
    with r_c4:
        if st.button(
            "✏️ Edit",
            key=f"_{key_prefix}_row_edit_btn_{resolved_entity}_{orig_idx}",
            help="Edit this rule in place",
            use_container_width=True,
        ):
            st.session_state[active_edit_state_key] = orig_idx
            st.session_state[copy_mode_state_key] = False
            st.rerun()
    with r_c5:
        if st.button(
            "📋 Copy",
            key=f"_{key_prefix}_row_copy_btn_{resolved_entity}_{orig_idx}",
            help="Copy this rule so you can assign it to another property while keeping this one",
            use_container_width=True,
        ):
            st.session_state[active_edit_state_key] = orig_idx
            st.session_state[copy_mode_state_key] = True
            st.rerun()


def _render_rules_grid(ctx: MatrixContext, view: RulesView) -> None:
    """Spreadsheet-style editable grid fallback with a bulk save button."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    memorized_list = ctx.memorized_list
    auto_label = view.auto_label
    all_prop_dropdown_opts = view.all_prop_dropdown_opts
    indexed_rules = view.indexed_rules
    filter_by_item = view.filter_by_item
    # Compact Spreadsheet Grid fallback with shrunk column widths
    table_rows: list[dict[str, str]] = [
        {
            "Bank Text": r_info["Pattern"],
            "Item": r_info["Item"],
            "Property": r_info["Property"],
        }
        for _, r_info in indexed_rules
    ]
    view_rules_df = (
        pd.DataFrame(table_rows)
        if table_rows
        else pd.DataFrame(columns=["Bank Text", "Item", "Property"])
    )

    edited_rules_df = st.data_editor(
        view_rules_df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key=f"_{key_prefix}_editor_{resolved_entity}_{filter_by_item}",
        column_config={
            "Bank Text": st.column_config.TextColumn(
                "Bank Text / CSV Match",
                required=True,
                width="medium",
                help="Text or 'Vendor + House' tokens on the CSV row.",
            ),
            "Item": st.column_config.TextColumn(
                "1. Item (Type or Edit Name)",
                required=True,
                width="small",
                help="You can edit or type any Item name here (e.g. 'Insurance' or 'Tower Insurance').",
            ),
            "Property": st.column_config.SelectboxColumn(
                "2. Property",
                options=all_prop_dropdown_opts,
                required=True,
                width="small",
            ),
        },
    )

    if st.button(
        "💾 Save Grid Changes & Re-Apply to All Uploaded Files",
        key=f"_{key_prefix}_save_table_btn_{resolved_entity}",
        type="primary",
    ):
        edited_indices = {orig_idx for orig_idx, _ in indexed_rules}
        kept_unfiltered = [
            r for idx_r, r in enumerate(memorized_list) if idx_r not in edited_indices
        ]
        new_payload: list[dict[str, object]] = []
        for _, row_item in edited_rules_df.iterrows():
            pat_v = str(row_item.get("Bank Text", "") or "").strip()
            item_v = str(row_item.get("Item", "") or "").strip()
            prop_v = str(row_item.get("Property", "") or "").strip()
            if pat_v:
                new_payload.append(
                    {
                        "pattern": pat_v,
                        "account_code": item_v,
                        "property": UNASSIGNED_PROPERTY if prop_v == auto_label else prop_v,
                        "source": "Setup Table",
                    }
                )
        new_payload.extend(kept_unfiltered)
        ok_rep, msg_rep = replace_memorized_rules(new_payload, entity=resolved_entity)
        if ok_rep:
            reapply_rules_and_sync(ctx)
            st.success(msg_rep)
            st.rerun()

