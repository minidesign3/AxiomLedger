"""
axiom_properties
================

Entity Property-list CRUD, in-place Property renaming/migration and
Per-Property vs Portfolio-Wide account-code scoping, extracted from the
monolithic ``utils.py`` god-module (decomposition phase D5).

``rename_entity_property`` renames a Property in place and migrates every
memorized rule, uploaded-file DataFrame, current-year ledger and the SQLite
``file_transactions`` / ``master_ledgers`` / ``property_loans`` /
``property_loan_rate_history`` / ``loan_reminder_log`` rows that referenced the
old name. ``PORTFOLIO_WIDE_CATEGORIES`` moved here with its only consumer,
``is_portfolio_wide_code``.

Entity lifecycle (create/rename/delete entity, add financial year) lives in
``axiom_entities``. Every function here was moved byte-for-byte; ``utils.py``
imports and re-exports them so all existing ``from utils import ...`` call
sites keep working unchanged.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from axiom_core import DEFAULT_PROPERTIES
from axiom_db import _DB_LOCK, _ensure_sqlite_schema, _get_sqlite_conn
from axiom_memory import _load_memory_store, _save_memory_store
from axiom_state import get_active_entity, get_active_tax_year, init_workspace_state

# Per-Property items (Rates, Insurance, Loan Interest, Rent Income, ...) are
# tracked against one property; everything in this set is company overhead.
# is_portfolio_wide_code() below is the only consumer.
PORTFOLIO_WIDE_CATEGORIES = {
    "legal and accounting",
    "legal and professional",
    "ird tax payment",
    "ird payment",
    "accounting",
    "accountant",
    "cpa",
    "companies office",
    "james drawings",
    "kathy drawings",
    "owner drawing",
    "owner contribution",
    "website",
    "mobile",
    "bank fee",
    "bank fee refund",
    "bank cash contribution",
    "lawyer",
    "court order",
    "raywhite settlement",
}


def get_entity_properties(entity: str | None = None) -> list[str]:
    """Return the configured list of Properties (e.g., Property 1..5, General / Portfolio) for an entity."""
    resolved = get_active_entity(entity)
    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    if not isinstance(props_map, dict):
        props_map = {}
        store["entity_properties"] = props_map
    if resolved not in props_map or not props_map[resolved]:
        props_map[resolved] = list(DEFAULT_PROPERTIES)
        _save_memory_store(store)
    return list(props_map[resolved])


def add_entity_property(property_name: str, entity: str | None = None) -> tuple[bool, str]:
    """Add a new Property to the active entity's property list and persist it."""
    resolved = get_active_entity(entity)
    cleaned = (property_name or "").strip()
    if not cleaned:
        return False, "Property name cannot be empty."
    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    current = list(props_map.get(resolved, DEFAULT_PROPERTIES))
    if any(p.lower() == cleaned.lower() for p in current):
        return False, f"Property '{cleaned}' already exists for {resolved}."
    current.append(cleaned)
    props_map[resolved] = current
    _save_memory_store(store)
    return True, f"Added property '{cleaned}' to {resolved}."


def remove_entity_property(property_name: str, entity: str | None = None) -> tuple[bool, str]:
    """Remove a Property from the active entity's property list."""
    resolved = get_active_entity(entity)
    target = (property_name or "").strip()
    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    current = list(props_map.get(resolved, DEFAULT_PROPERTIES))
    if target not in current:
        return False, f"Property '{target}' not found."
    if len(current) <= 1:
        return False, "At least one property must remain configured."
    current.remove(target)
    props_map[resolved] = current
    _save_memory_store(store)
    return True, f"Removed property '{target}' from {resolved}."


def rename_entity_property(
    old_property_name: str,
    new_property_name: str,
    entity: str | None = None,
) -> tuple[bool, str]:
    """
    Rename an existing Property (e.g. 'Property 1' -> '14 Queen Street') in-place
    for the specified entity, and automatically migrate all memorized rules,
    uploaded file DataFrames, and financial year ledgers that referenced the old name.
    """
    init_workspace_state()
    resolved = get_active_entity(entity)
    src_prop = (old_property_name or "").strip()
    target_prop = (new_property_name or "").strip()

    if not target_prop:
        return False, "Property name cannot be empty."

    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    current = list(props_map.get(resolved, DEFAULT_PROPERTIES))

    if src_prop not in current:
        return False, f"Property '{src_prop}' not found."

    if src_prop == target_prop:
        return True, f"Property '{src_prop}' unchanged."

    for existing in current:
        if existing != src_prop and existing.lower() == target_prop.lower():
            return False, f"A property named '{existing}' already exists for {resolved}."

    idx = current.index(src_prop)
    current[idx] = target_prop
    props_map[resolved] = current

    # Migrate memorized rules that point to src_prop
    rules_map = store.setdefault("memorized_rules", {})
    ent_rules: list[dict[str, str]] = list(rules_map.get(resolved, []))
    for rule_item in ent_rules:
        if str(rule_item.get("property", "")).strip().lower() == src_prop.lower():
            rule_item["property"] = target_prop
    rules_map[resolved] = ent_rules
    _save_memory_store(store)

    # Migrate uploaded file DataFrames across all years for this entity (in-memory & SQLite)
    files_by_ent = st.session_state.get("entity_uploaded_files", {}).get(resolved, {})
    if isinstance(files_by_ent, dict):
        for _yr, file_list in files_by_ent.items():
            if isinstance(file_list, list):
                for frec in file_list:
                    fdf = frec.get("df")
                    if isinstance(fdf, pd.DataFrame) and "Property" in fdf.columns and not fdf.empty:
                        mask = fdf["Property"].astype(str).str.strip().str.lower() == src_prop.lower()
                        if mask.any():
                            fdf.loc[mask, "Property"] = target_prop
                            frec["df"] = fdf

    # Migrate master ledgers across all years for this entity (in-memory & SQLite)
    ledgers_by_ent = st.session_state.get("entity_year_ledgers", {}).get(resolved, {})
    if isinstance(ledgers_by_ent, dict):
        for yr, ldf in list(ledgers_by_ent.items()):
            if isinstance(ldf, pd.DataFrame) and "Property" in ldf.columns and not ldf.empty:
                mask = ldf["Property"].astype(str).str.strip().str.lower() == src_prop.lower()
                if mask.any():
                    ldf.loc[mask, "Property"] = target_prop
                    ledgers_by_ent[yr] = ldf
                    if yr == get_active_tax_year():
                        st.session_state["entity_ledgers"][resolved] = ldf

    # Update SQLite file_transactions and master_ledgers for this entity
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                """
                UPDATE file_transactions
                SET property = ?
                WHERE LOWER(TRIM(property)) = LOWER(TRIM(?))
                  AND file_id IN (SELECT file_id FROM uploaded_files WHERE entity = ?)
                """,
                (target_prop, src_prop, resolved),
            )
            conn.execute(
                """
                UPDATE master_ledgers
                SET property = ?
                WHERE entity = ? AND LOWER(TRIM(property)) = LOWER(TRIM(?))
                """,
                (target_prop, resolved, src_prop),
            )
            conn.execute(
                """
                UPDATE property_loans
                SET property_name = ?
                WHERE entity = ? AND LOWER(TRIM(property_name)) = LOWER(TRIM(?))
                """,
                (target_prop, resolved, src_prop),
            )
            conn.execute(
                """
                UPDATE property_loan_rate_history
                SET property_name = ?
                WHERE entity = ? AND LOWER(TRIM(property_name)) = LOWER(TRIM(?))
                """,
                (target_prop, resolved, src_prop),
            )
            conn.execute(
                """
                UPDATE loan_reminder_log
                SET property_name = ?
                WHERE entity = ? AND LOWER(TRIM(property_name)) = LOWER(TRIM(?))
                """,
                (target_prop, resolved, src_prop),
            )

    return True, f"Renamed '{src_prop}' → '{target_prop}'."


def is_portfolio_wide_code(code_name: object, entity: str | None = None) -> bool:
    """
    Return True if the given Item / Account Code is a Portfolio-Wide (company overhead) item
    rather than a Per-Property item (like Insurance, Loan Interest, Rent Income, Rates, etc.).
    """
    if pd.isna(code_name):
        return False
    c_lower = str(code_name).strip().lower()
    if not c_lower:
        return False
    resolved = get_active_entity(entity)
    store = _load_memory_store()
    scopes_map = store.get("entity_code_scopes", {})
    if isinstance(scopes_map, dict):
        ent_scopes = scopes_map.get(resolved, {})
        if isinstance(ent_scopes, dict):
            for k, v in ent_scopes.items():
                if str(k).strip().lower() == c_lower:
                    return str(v).strip().lower() == "portfolio"
    return c_lower in PORTFOLIO_WIDE_CATEGORIES


def set_entity_code_scope(
    code_name: str,
    is_portfolio_wide: bool,
    entity: str | None = None,
) -> tuple[bool, str]:
    """Set whether an Account Code is Per-Property ('property') or Portfolio-Wide ('portfolio')."""
    resolved = get_active_entity(entity)
    cleaned = (code_name or "").strip()
    if not cleaned:
        return False, "Item name cannot be empty."
    store = _load_memory_store()
    scopes_map = store.setdefault("entity_code_scopes", {})
    if not isinstance(scopes_map, dict):
        scopes_map = {}
        store["entity_code_scopes"] = scopes_map
    ent_scopes = dict(scopes_map.get(resolved, {}))
    ent_scopes[cleaned] = "portfolio" if is_portfolio_wide else "property"
    scopes_map[resolved] = ent_scopes
    _save_memory_store(store)
    scope_lbl = "Portfolio-Wide" if is_portfolio_wide else "Per-Property"
    return True, f"Set '{cleaned}' to {scope_lbl}."
