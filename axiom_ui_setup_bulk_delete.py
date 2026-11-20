"""
axiom_ui_setup_bulk_delete
==========================

Bulk selection + delete for the "saved Item + Property rules" card: duplicate
detection (keep one instance), the checkbox toolbar and the delete helper shared
with the per-row editor delete button.
"""

from __future__ import annotations

import streamlit as st

from axiom_rule_patterns import normalize_rule_pattern
from axiom_rules import replace_memorized_rules
from axiom_ui_setup_common import MatrixContext, RulesView, reapply_rules_and_sync


def rule_key(rule: dict[str, str]) -> tuple[str, str, str]:
    """Duplicate key: case-insensitive normalized (pattern, property, item)."""
    return (
        normalize_rule_pattern(rule.get("pattern", "")).lower(),
        str(rule.get("property", "")).strip().lower(),
        str(rule.get("account_code", "")).strip().lower(),
    )


def duplicate_extra_indices(rules: list[dict[str, str]]) -> set[int]:
    """Indices of every rule that is a 2nd+ copy of an earlier rule."""
    seen: set[tuple[str, str, str]] = set()
    extras: set[int] = set()
    for idx, rule in enumerate(rules):
        key = rule_key(rule)
        if key in seen:
            extras.add(idx)
        else:
            seen.add(key)
    return extras


def delete_rules_keep_one(
    rules: list[dict[str, str]], selected: set[int], keep_one: bool = True
) -> list[dict[str, str]]:
    """
    Remove the ``selected`` rule indices. With ``keep_one`` every selected rule that
    has duplicates keeps exactly one instance (the first) and all other duplicates
    are deleted; a selected rule with no duplicates is deleted outright.
    """
    valid = {i for i in selected if 0 <= i < len(rules)}
    if not keep_one:
        return [r for i, r in enumerate(rules) if i not in valid]
    groups: dict[tuple[str, str, str], list[int]] = {}
    selected_keys = {rule_key(rules[i]) for i in valid}
    for idx, rule in enumerate(rules):
        key = rule_key(rule)
        if key in selected_keys:
            groups.setdefault(key, []).append(idx)
    remove: set[int] = set()
    for idxs in groups.values():
        remove.update(idxs if len(idxs) == 1 else idxs[1:])
    return [r for i, r in enumerate(rules) if i not in remove]


def _sel_key(ctx: MatrixContext, idx: int) -> str:
    return f"_{ctx.key_prefix}_sel_{ctx.resolved_entity}_{idx}"


def row_checkbox(ctx: MatrixContext, orig_idx: int) -> None:
    """Render the selection checkbox for one rule row."""
    st.checkbox(
        "Select rule",
        key=_sel_key(ctx, orig_idx),
        label_visibility="collapsed",
    )


def _clear_selection(ctx: MatrixContext) -> None:
    prefix = f"_{ctx.key_prefix}_sel_{ctx.resolved_entity}_"
    for k in [k for k in st.session_state if str(k).startswith(prefix)]:
        del st.session_state[k]


def _set_selection(ctx: MatrixContext, indices: set[int], all_indices: list[int]) -> None:
    for idx in all_indices:
        st.session_state[_sel_key(ctx, idx)] = idx in indices


def render_bulk_toolbar(
    ctx: MatrixContext, view: RulesView, page_indices: list[int]
) -> None:
    """Select-all / select-duplicates / bulk delete controls above the rule rows."""
    all_indices = [i for i, _ in view.indexed_rules]
    selected = {i for i in all_indices if st.session_state.get(_sel_key(ctx, i), False)}
    extras = duplicate_extra_indices(ctx.memorized_list)
    kp, ent = ctx.key_prefix, ctx.resolved_entity
    c1, c2, c3, c4, c5 = st.columns([1, 1, 1.25, 0.8, 1.35])
    if c1.button("☑️ Select page", key=f"_{kp}_sel_page_{ent}", use_container_width=True):
        _set_selection(ctx, selected | set(page_indices), all_indices)
        st.rerun()
    if c2.button("☑️ Select all", key=f"_{kp}_sel_all_{ent}", use_container_width=True):
        _set_selection(ctx, set(all_indices), all_indices)
        st.rerun()
    if c3.button(
        f"♊ Select duplicates ({len(extras & set(all_indices))})",
        key=f"_{kp}_sel_dupes_{ent}",
        use_container_width=True,
        help="Tick every extra copy of a rule, leaving the first copy unticked.",
    ):
        _set_selection(ctx, extras, all_indices)
        st.rerun()
    if c4.button("Clear", key=f"_{kp}_sel_clear_{ent}", use_container_width=True):
        _clear_selection(ctx)
        st.rerun()
    pending_key = f"_{kp}_bulk_del_pending_{ent}"
    if c5.button(
        f"🗑️ Delete selected ({len(selected)})",
        key=f"_{kp}_bulk_del_btn_{ent}",
        type="primary",
        disabled=not selected,
        use_container_width=True,
    ):
        st.session_state[pending_key] = True
    if selected and st.session_state.get(pending_key):
        _render_bulk_confirm(ctx, selected, pending_key)


def _render_bulk_confirm(ctx: MatrixContext, selected: set[int], pending_key: str) -> None:
    """Confirmation panel with the keep-one-instance toggle."""
    kp, ent = ctx.key_prefix, ctx.resolved_entity
    keep_one = st.checkbox(
        "Keep one instance of each rule, delete all other duplicates",
        value=True,
        key=f"_{kp}_bulk_keep_one_{ent}",
    )
    new_rules = delete_rules_keep_one(ctx.memorized_list, selected, keep_one)
    removed = len(ctx.memorized_list) - len(new_rules)
    st.warning(f"This will remove {removed} rule(s) and leave {len(new_rules)}.")
    k1, k2 = st.columns(2)
    if k1.button("✅ Confirm delete", key=f"_{kp}_bulk_del_ok_{ent}", use_container_width=True):
        replace_memorized_rules(list(new_rules), entity=ent)
        st.session_state[pending_key] = False
        _clear_selection(ctx)
        reapply_rules_and_sync(ctx)
        st.rerun()
    if k2.button("Cancel", key=f"_{kp}_bulk_del_cancel_{ent}", use_container_width=True):
        st.session_state[pending_key] = False
        st.rerun()
