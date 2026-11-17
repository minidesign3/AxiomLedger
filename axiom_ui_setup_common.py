"""
axiom_ui_setup_common
=====================

Shared state for the Item + Property setup matrix view (phase D18): the
resolved-context dataclass built once per render, the filtered/paged rules view
model, the in-progress editor draft and the "re-apply rules to uploaded files /
ledger" helper used after every rule mutation.

Part of the decomposition of ``render_item_property_setup_matrix`` (formerly one
~737-line function in ``utils.py``); widget keys and call order are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from axiom_categorization import apply_rule_categorization
from axiom_ingestion import get_entity_uploaded_files, sync_entity_ledger_from_files
from axiom_ledger import get_entity_ledger, set_entity_ledger


@dataclass(frozen=True)
class MatrixContext:
    """Per-render context resolved once at the top of the setup matrix."""

    resolved_entity: str
    resolved_year: str
    key_prefix: str
    alphabetical_items: list[str]
    memorized_list: list[dict]
    real_props: list[str]
    all_props_option: str


@dataclass(frozen=True)
class RulesView:
    """Filter inputs plus the derived, sorted rule rows for the saved-rules card."""

    auto_label: str
    all_prop_dropdown_opts: list[str]
    all_item_dropdown_opts: list[str]
    active_edit_state_key: str
    active_edit_idx: object
    copy_mode_state_key: str
    is_copy_mode: bool
    indexed_rules: list[tuple[int, dict[str, str]]]
    filter_by_item: str
    search_rule_q: str
    page_size_choice: str
    view_mode: str


@dataclass(frozen=True)
class EditorDraft:
    """Values currently entered in the rule editor panel."""

    cur_prop_disp: str
    new_pat_val: str
    new_code_val: str
    new_prop_val: str


def reapply_rules_and_sync(ctx: MatrixContext) -> None:
    resolved_entity = ctx.resolved_entity
    resolved_year = ctx.resolved_year
    files_list = get_entity_uploaded_files(resolved_entity, resolved_year)
    for f_rec in files_list:
        f_df = f_rec.get("df")
        if isinstance(f_df, pd.DataFrame) and not f_df.empty:
            f_rec["df"] = apply_rule_categorization(
                f_df,
                overwrite_existing=True,
                entity=resolved_entity,
            )
    if files_list:
        sync_entity_ledger_from_files(resolved_entity, resolved_year)
    else:
        cur_ledger = get_entity_ledger(resolved_entity, resolved_year)
        if not cur_ledger.empty:
            updated_ledger = apply_rule_categorization(
                cur_ledger,
                overwrite_existing=True,
                entity=resolved_entity,
            )
            set_entity_ledger(updated_ledger, resolved_entity, resolved_year)
