"""
axiom_ui_setup_editor
=====================

The rule edit / copy panel of the Item + Property setup matrix (phase D18).
Split from the original ~226-line inline block into field rendering plus one
small function per action button; widget keys and call order are unchanged.
"""

from __future__ import annotations

import streamlit as st

from axiom_core import UNASSIGNED_PROPERTY
from axiom_properties import is_portfolio_wide_code
from axiom_rules import replace_memorized_rules, save_memorized_rule
from axiom_ui_setup_common import EditorDraft, MatrixContext, RulesView, reapply_rules_and_sync


def render_rule_editor_panel(ctx: MatrixContext, view: RulesView) -> None:
    """Full-width edit / copy panel shown above the rules list for the active rule."""
    resolved_entity = ctx.resolved_entity
    memorized_list = ctx.memorized_list
    real_props = ctx.real_props
    auto_label = view.auto_label
    all_prop_dropdown_opts = view.all_prop_dropdown_opts
    all_item_dropdown_opts = view.all_item_dropdown_opts
    active_edit_idx = view.active_edit_idx
    is_copy_mode = view.is_copy_mode
    if active_edit_idx is not None and 0 <= int(active_edit_idx) < len(memorized_list):
        target_rule = memorized_list[int(active_edit_idx)]
        cur_pat = str(target_rule.get("pattern", "")).strip()
        cur_code = str(target_rule.get("account_code", "")).strip() or "Uncategorized"
        cur_prop_raw = str(target_rule.get("property", "")).strip()
        if is_portfolio_wide_code(cur_code, resolved_entity):
            cur_prop_disp = "General / Portfolio"
        elif not cur_prop_raw or cur_prop_raw == UNASSIGNED_PROPERTY:
            cur_prop_disp = auto_label
        else:
            cur_prop_disp = cur_prop_raw

        if cur_code not in all_item_dropdown_opts:
            all_item_dropdown_opts.append(cur_code)
        if cur_prop_disp not in all_prop_dropdown_opts:
            all_prop_dropdown_opts.append(cur_prop_disp)

        # In Copy mode, pre-select the next property not yet linked to this pattern so the user can save right away
        default_prop_select_idx = all_prop_dropdown_opts.index(cur_prop_disp)
        if is_copy_mode and real_props and not is_portfolio_wide_code(cur_code, resolved_entity):
            existing_props_for_pat = {
                str(r.get("property", "")).strip().lower()
                for r in memorized_list
                if str(r.get("pattern", "")).strip().lower() == cur_pat.lower()
            }
            for candidate_p in real_props:
                if candidate_p.lower() not in existing_props_for_pat and candidate_p in all_prop_dropdown_opts:
                    default_prop_select_idx = all_prop_dropdown_opts.index(candidate_p)
                    break

        with st.container(border=True):
            if is_copy_mode:
                st.markdown(
                    f'<div class="axiom-eyebrow" style="color:#10B981;">📋 COPYING RULE #{int(active_edit_idx) + 1} // "{cur_pat}" (KEEPING ORIGINAL: {cur_prop_disp})</div>',
                    unsafe_allow_html=True,
                )
                st.caption(
                    "Pick the **Property** you want to copy this rule to and click **📋 Save as New Copy** (your original rule for "
                    f"`{cur_prop_disp}` stays saved)."
                )
            else:
                st.markdown(
                    f'<div class="axiom-eyebrow" style="color:#60A5FA;">✏️ EDITING RULE #{int(active_edit_idx) + 1} // "{cur_pat}" ({cur_prop_disp})</div>',
                    unsafe_allow_html=True,
                )
            draft_fields = _render_editor_fields(
                ctx, view, cur_pat, cur_code, default_prop_select_idx
            )
            draft = EditorDraft(cur_prop_disp, *draft_fields)
            _render_editor_buttons(ctx, view, draft)


def _render_editor_fields(
    ctx: MatrixContext,
    view: RulesView,
    cur_pat: str,
    cur_code: str,
    default_prop_select_idx: int,
) -> tuple[str, str, str]:
    """Render the pattern / item / property inputs and return their current values."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    all_prop_dropdown_opts = view.all_prop_dropdown_opts
    all_item_dropdown_opts = view.all_item_dropdown_opts
    active_edit_idx = view.active_edit_idx
    is_copy_mode = view.is_copy_mode
    ed_c1, ed_c2, ed_c3 = st.columns([1.35, 1.3, 1.15])
    with ed_c1:
        new_pat_val = st.text_input(
            "Bank Statement Text / CSV Match Pattern",
            value=cur_pat,
            help="Tip: Use a vendor name (e.g. 'Tower') or combine vendor + house with '+' (e.g. 'Tower + Damian') to match across the entire CSV line.",
            key=f"_{key_prefix}_edit_pat_{resolved_entity}_{active_edit_idx}_{int(is_copy_mode)}",
        )
    with ed_c2:
        sel_edit_code = st.selectbox(
            "1. What is the Item? (Pick from list)",
            options=all_item_dropdown_opts,
            index=all_item_dropdown_opts.index(cur_code),
            key=f"_{key_prefix}_edit_code_{resolved_entity}_{active_edit_idx}_{int(is_copy_mode)}",
        )
        typed_edit_code = st.text_input(
            "Or Edit / Type Item Name (e.g. 'Tower Insurance')",
            value=sel_edit_code,
            help="Edit the Item name directly here (e.g. change 'Insurance' to 'Tower Insurance').",
            key=f"_{key_prefix}_edit_custom_code_{resolved_entity}_{active_edit_idx}_{int(is_copy_mode)}_{sel_edit_code}",
        )
        new_code_val = (typed_edit_code or "").strip() or sel_edit_code
    with ed_c3:
        new_prop_val = st.selectbox(
            "2. Which Property?",
            options=all_prop_dropdown_opts,
            index=default_prop_select_idx,
            key=f"_{key_prefix}_edit_prop_{resolved_entity}_{active_edit_idx}_{int(is_copy_mode)}",
        )
    return new_pat_val, new_code_val, new_prop_val


def _render_editor_buttons(ctx: MatrixContext, view: RulesView, draft: EditorDraft) -> None:
    """Render the Save / Copy / All-properties / Delete / Cancel action row."""
    btn_c1, btn_c2, btn_c3, btn_c4, btn_c5 = st.columns([1.05, 1.15, 1.15, 0.8, 0.65])
    with btn_c1:
        _editor_save_button(ctx, view, draft)
    with btn_c2:
        _editor_copy_button(ctx, view, draft)
    with btn_c3:
        _editor_all_props_button(ctx, view, draft)
    with btn_c4:
        _editor_delete_button(ctx, view)
    with btn_c5:
        _editor_cancel_button(ctx, view)


def _editor_save_button(ctx: MatrixContext, view: RulesView, draft: EditorDraft) -> None:
    """Primary action: save the edit (or save as a new copy in copy mode)."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    memorized_list = ctx.memorized_list
    auto_label = view.auto_label
    active_edit_state_key = view.active_edit_state_key
    active_edit_idx = view.active_edit_idx
    copy_mode_state_key = view.copy_mode_state_key
    is_copy_mode = view.is_copy_mode
    new_pat_val = draft.new_pat_val
    new_code_val = draft.new_code_val
    new_prop_val = draft.new_prop_val
    if st.button(
        "📋 Save as New Copy" if is_copy_mode else "💾 Save Changes",
        key=f"_{key_prefix}_save_edit_row_{resolved_entity}_{active_edit_idx}",
        type="primary",
        use_container_width=True,
    ):
        cleaned_pat = (new_pat_val or "").strip()
        if not cleaned_pat:
            st.error("Bank Statement Text cannot be empty.")
        else:
            resolved_save_prop = (
                "General / Portfolio"
                if is_portfolio_wide_code(new_code_val, resolved_entity)
                else (UNASSIGNED_PROPERTY if new_prop_val == auto_label else new_prop_val)
            )
            if is_copy_mode:
                ok_c, msg_c = save_memorized_rule(
                    pattern=cleaned_pat,
                    property_name=resolved_save_prop,
                    account_code=new_code_val,
                    entity=resolved_entity,
                    source="Copied Rule",
                )
                if ok_c:
                    st.session_state[active_edit_state_key] = None
                    st.session_state[copy_mode_state_key] = False
                    reapply_rules_and_sync(ctx)
                    st.success(f"Copied rule '{cleaned_pat}' → Item: {new_code_val} | Property: {new_prop_val}")
                    st.rerun()
            else:
                updated_list = list(memorized_list)
                updated_list[int(active_edit_idx)] = {
                    "pattern": cleaned_pat,
                    "account_code": new_code_val,
                    "property": resolved_save_prop,
                    "source": "Edited Rule",
                }
                ok_u, msg_u = replace_memorized_rules(updated_list, entity=resolved_entity)
                if ok_u:
                    st.session_state[active_edit_state_key] = None
                    st.session_state[copy_mode_state_key] = False
                    reapply_rules_and_sync(ctx)
                    st.success(f"Updated rule '{cleaned_pat}' → Item: {new_code_val} | Property: {new_prop_val}")
                    st.rerun()


def _editor_copy_button(ctx: MatrixContext, view: RulesView, draft: EditorDraft) -> None:
    """Secondary action: keep-both copy (edit mode) or overwrite-original (copy mode)."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    memorized_list = ctx.memorized_list
    auto_label = view.auto_label
    active_edit_state_key = view.active_edit_state_key
    active_edit_idx = view.active_edit_idx
    copy_mode_state_key = view.copy_mode_state_key
    is_copy_mode = view.is_copy_mode
    cur_prop_disp = draft.cur_prop_disp
    new_pat_val = draft.new_pat_val
    new_code_val = draft.new_code_val
    new_prop_val = draft.new_prop_val
    if not is_copy_mode:
        if st.button(
            "📋 Save as Copy (Keep Both)",
            key=f"_{key_prefix}_copy_edit_row_{resolved_entity}_{active_edit_idx}",
            help="Saves this Property as an additional rule while keeping the original rule intact.",
            use_container_width=True,
        ):
            cleaned_pat = (new_pat_val or "").strip()
            if not cleaned_pat:
                st.error("Bank Statement Text cannot be empty.")
            else:
                resolved_save_prop = (
                    "General / Portfolio"
                    if is_portfolio_wide_code(new_code_val, resolved_entity)
                    else (UNASSIGNED_PROPERTY if new_prop_val == auto_label else new_prop_val)
                )
                ok_cp, _ = save_memorized_rule(
                    pattern=cleaned_pat,
                    property_name=resolved_save_prop,
                    account_code=new_code_val,
                    entity=resolved_entity,
                    source="Copied Rule",
                )
                if ok_cp:
                    st.session_state[active_edit_state_key] = None
                    st.session_state[copy_mode_state_key] = False
                    reapply_rules_and_sync(ctx)
                    st.success(
                        f"Added copy '{cleaned_pat}' → {new_code_val} | {new_prop_val} (kept original {cur_prop_disp})!"
                    )
                    st.rerun()
    else:
        if st.button(
            "💾 Overwrite Original Instead",
            key=f"_{key_prefix}_overwrite_orig_row_{resolved_entity}_{active_edit_idx}",
            use_container_width=True,
        ):
            cleaned_pat = (new_pat_val or "").strip()
            if cleaned_pat:
                updated_list = list(memorized_list)
                resolved_save_prop = (
                    "General / Portfolio"
                    if is_portfolio_wide_code(new_code_val, resolved_entity)
                    else (UNASSIGNED_PROPERTY if new_prop_val == auto_label else new_prop_val)
                )
                updated_list[int(active_edit_idx)] = {
                    "pattern": cleaned_pat,
                    "account_code": new_code_val,
                    "property": resolved_save_prop,
                    "source": "Edited Rule",
                }
                replace_memorized_rules(updated_list, entity=resolved_entity)
                st.session_state[active_edit_state_key] = None
                st.session_state[copy_mode_state_key] = False
                reapply_rules_and_sync(ctx)
                st.rerun()


def _editor_all_props_button(
    ctx: MatrixContext, view: RulesView, draft: EditorDraft
) -> None:
    """Create the rule for every property of the entity."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    real_props = ctx.real_props
    active_edit_state_key = view.active_edit_state_key
    active_edit_idx = view.active_edit_idx
    copy_mode_state_key = view.copy_mode_state_key
    new_pat_val = draft.new_pat_val
    new_code_val = draft.new_code_val
    if st.button(
        f"🏠 Add for All ({len(real_props)}) Properties",
        key=f"_{key_prefix}_all_props_edit_row_{resolved_entity}_{active_edit_idx}",
        disabled=is_portfolio_wide_code(new_code_val, resolved_entity) or not real_props,
        help="Creates a saved rule for every property in this entity so all houses are saved in your list.",
        use_container_width=True,
    ):
        cleaned_pat = (new_pat_val or "").strip()
        if not cleaned_pat:
            st.error("Bank Statement Text cannot be empty.")
        else:
            for p_house in real_props:
                save_memorized_rule(
                    pattern=cleaned_pat,
                    property_name=p_house,
                    account_code=new_code_val,
                    entity=resolved_entity,
                    source="Copied to All Properties",
                )
            st.session_state[active_edit_state_key] = None
            st.session_state[copy_mode_state_key] = False
            reapply_rules_and_sync(ctx)
            st.success(
                f"Saved '{cleaned_pat}' → {new_code_val} for all {len(real_props)} properties!"
            )
            st.rerun()


def _editor_delete_button(ctx: MatrixContext, view: RulesView) -> None:
    """Delete the rule currently being edited."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    memorized_list = ctx.memorized_list
    active_edit_state_key = view.active_edit_state_key
    active_edit_idx = view.active_edit_idx
    copy_mode_state_key = view.copy_mode_state_key
    if st.button(
        "🗑️ Delete",
        key=f"_{key_prefix}_del_edit_row_{resolved_entity}_{active_edit_idx}",
        use_container_width=True,
    ):
        updated_list = [r for i_r, r in enumerate(memorized_list) if i_r != int(active_edit_idx)]
        replace_memorized_rules(updated_list, entity=resolved_entity)
        st.session_state[active_edit_state_key] = None
        st.session_state[copy_mode_state_key] = False
        reapply_rules_and_sync(ctx)
        st.rerun()


def _editor_cancel_button(ctx: MatrixContext, view: RulesView) -> None:
    """Leave edit / copy mode without saving."""
    resolved_entity = ctx.resolved_entity
    key_prefix = ctx.key_prefix
    active_edit_state_key = view.active_edit_state_key
    active_edit_idx = view.active_edit_idx
    copy_mode_state_key = view.copy_mode_state_key
    if st.button(
        "Cancel",
        key=f"_{key_prefix}_cancel_edit_row_{resolved_entity}_{active_edit_idx}",
        use_container_width=True,
    ):
        st.session_state[active_edit_state_key] = None
        st.session_state[copy_mode_state_key] = False
        st.rerun()

