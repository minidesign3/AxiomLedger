"""
axiom_entities
==============

Entity (organization) lifecycle and financial-year management, extracted from
the monolithic ``utils.py`` god-module (decomposition phase D5).

Owns creating / renaming / deleting entities across session state, the local
File Vault and SQLite (uploaded files, master ledgers, property loans, loan
rate history and the reminder log), plus adding a financial year to the
workspace year bar. Property-list CRUD and Per-Property vs Portfolio-Wide code
scoping live in ``axiom_properties``.

Every function here was moved byte-for-byte. ``utils.py`` imports and
re-exports these symbols, so all existing ``from utils import ...`` call sites
keep working unchanged.
"""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from axiom_core import DEFAULT_PROPERTIES, STANDARD_COLUMNS, _safe_vault_segment
from axiom_db import _DB_LOCK, VAULT_ROOT_DIR, _ensure_sqlite_schema, _get_sqlite_conn
from axiom_memory import _load_memory_store, _save_memory_store
from axiom_state import (
    get_active_tax_year,
    get_entity_jurisdiction,
    get_entity_options,
    get_tax_year_options,
    init_workspace_state,
)


def add_new_entity(entity_name: str, jurisdiction: str = "NZ") -> tuple[bool, str]:
    """
    Dynamically register a new Entity (Organization) with its tax jurisdiction ('NZ' or 'US')
    and initialize empty ledgers for all financial years.
    """
    init_workspace_state()
    cleaned_name = (entity_name or "").strip()
    if not cleaned_name:
        return False, "Entity name cannot be empty."

    options = get_entity_options()
    if any(existing.lower() == cleaned_name.lower() for existing in options):
        return False, f"Entity '{cleaned_name}' already exists."

    norm_jur = "US" if str(jurisdiction).upper().startswith("US") else "NZ"
    st.session_state["entity_options"].append(cleaned_name)
    st.session_state["entity_jurisdictions"][cleaned_name] = norm_jur

    st.session_state["entity_ledgers"][cleaned_name] = pd.DataFrame(columns=STANDARD_COLUMNS)
    if cleaned_name not in st.session_state["entity_year_ledgers"]:
        st.session_state["entity_year_ledgers"][cleaned_name] = {}
    for yr in get_tax_year_options():
        st.session_state["entity_year_ledgers"][cleaned_name][yr] = pd.DataFrame(columns=STANDARD_COLUMNS)

    st.session_state["active_entity"] = cleaned_name
    st.session_state["entity"] = cleaned_name

    # Persist new organization list, jurisdiction, and default properties to .axiom_memory.json
    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    if isinstance(props_map, dict) and cleaned_name not in props_map:
        props_map[cleaned_name] = list(DEFAULT_PROPERTIES)
    rules_map = store.setdefault("memorized_rules", {})
    if isinstance(rules_map, dict) and cleaned_name not in rules_map:
        rules_map[cleaned_name] = []
    _save_memory_store(store)

    return True, f"Entity '{cleaned_name}' ({norm_jur}) created and activated."


def rename_entity(
    old_name: str,
    new_name: str,
    new_jurisdiction: str | None = None,
) -> tuple[bool, str]:
    """
    Rename an existing Entity (Organization) across all session state stores AND
    persistent disk storage (.axiom_memory.json), preserving its position, properties,
    memorized rules, uploaded files, financial year ledgers, and jurisdiction mapping.
    """
    init_workspace_state()
    src_name = (old_name or "").strip()
    target_name = (new_name or "").strip()

    options = get_entity_options()
    if src_name not in options:
        return False, f"Entity '{src_name}' was not found."

    if not target_name:
        return False, "New entity name cannot be empty."

    # Check case-insensitive collision with any OTHER entity
    for existing in options:
        if existing != src_name and existing.lower() == target_name.lower():
            return False, f"An entity named '{existing}' already exists."

    resolved_jur = (
        ("US" if str(new_jurisdiction).upper().startswith("US") else "NZ")
        if new_jurisdiction is not None
        else get_entity_jurisdiction(src_name)
    )

    if target_name == src_name:
        st.session_state["entity_jurisdictions"][src_name] = resolved_jur
        _save_memory_store()
        return True, f"Entity '{src_name}' updated ({resolved_jur})."

    # Replace in-place within entity_options list to preserve visual ordering
    idx = options.index(src_name)
    st.session_state["entity_options"][idx] = target_name

    # Migrate jurisdiction mapping
    st.session_state["entity_jurisdictions"].pop(src_name, None)
    st.session_state["entity_jurisdictions"][target_name] = resolved_jur

    # Migrate isolated financial year ledgers & active ledger cache
    year_ledgers = st.session_state["entity_year_ledgers"].pop(src_name, {})
    for yr in get_tax_year_options():
        if yr not in year_ledgers:
            year_ledgers[yr] = pd.DataFrame(columns=STANDARD_COLUMNS)
    st.session_state["entity_year_ledgers"][target_name] = year_ledgers

    active_yr = get_active_tax_year()
    legacy_df = st.session_state["entity_ledgers"].pop(
        src_name,
        year_ledgers.get(active_yr, pd.DataFrame(columns=STANDARD_COLUMNS)),
    )
    st.session_state["entity_ledgers"][target_name] = year_ledgers.get(active_yr, legacy_df)

    # Migrate uploaded files store across all years
    for files_state_key in ("entity_uploaded_files", "entity_year_files"):
        if files_state_key in st.session_state and isinstance(st.session_state[files_state_key], dict):
            old_files = st.session_state[files_state_key].pop(src_name, None)
            if old_files is not None:
                st.session_state[files_state_key][target_name] = old_files

    # Migrate vault folder on disk if present (`data/vault/<src_name>` -> `data/vault/<target_name>`)
    old_vault_dir = VAULT_ROOT_DIR / _safe_vault_segment(src_name)
    new_vault_dir = VAULT_ROOT_DIR / _safe_vault_segment(target_name)
    if old_vault_dir.exists() and old_vault_dir != new_vault_dir:
        try:
            if not new_vault_dir.exists():
                old_vault_dir.rename(new_vault_dir)
        except Exception:
            pass

    # Migrate SQLite uploaded_files and master_ledgers rows to target_name
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                "UPDATE uploaded_files SET entity = ? WHERE entity = ?",
                (target_name, src_name),
            )
            conn.execute(
                "UPDATE master_ledgers SET entity = ? WHERE entity = ?",
                (target_name, src_name),
            )
            conn.execute(
                "UPDATE property_loans SET entity = ? WHERE entity = ?",
                (target_name, src_name),
            )
            conn.execute(
                "UPDATE property_loan_rate_history SET entity = ? WHERE entity = ?",
                (target_name, src_name),
            )
            conn.execute(
                "UPDATE loan_reminder_log SET entity = ? WHERE entity = ?",
                (target_name, src_name),
            )
            # Update vault_path strings if vault directory was renamed
            rows = conn.execute(
                "SELECT file_id, vault_path FROM uploaded_files WHERE entity = ?",
                (target_name,),
            ).fetchall()
            old_seg = f"/{_safe_vault_segment(src_name)}/"
            new_seg = f"/{_safe_vault_segment(target_name)}/"
            for r in rows:
                vp = str(r["vault_path"] or "")
                if old_seg in vp:
                    conn.execute(
                        "UPDATE uploaded_files SET vault_path = ? WHERE file_id = ?",
                        (vp.replace(old_seg, new_seg, 1), str(r["file_id"])),
                    )

    # Keep active_entity & entity synchronized if the active entity was renamed
    if st.session_state.get("active_entity") == src_name or st.session_state.get("entity") == src_name:
        st.session_state["active_entity"] = target_name
        st.session_state["entity"] = target_name

    # Migrate entity_properties and memorized_rules in .axiom_memory.json and save to disk
    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    if isinstance(props_map, dict):
        old_props = props_map.pop(src_name, None)
        if old_props is not None:
            props_map[target_name] = old_props
        elif target_name not in props_map:
            props_map[target_name] = list(DEFAULT_PROPERTIES)

    rules_map = store.setdefault("memorized_rules", {})
    if isinstance(rules_map, dict):
        old_rules = rules_map.pop(src_name, None)
        if old_rules is not None:
            rules_map[target_name] = old_rules
        elif target_name not in rules_map:
            rules_map[target_name] = []

    _save_memory_store(store)

    return True, f"Renamed '{src_name}' to '{target_name}' ({resolved_jur})."


def delete_entity(entity_name: str) -> tuple[bool, str]:
    """
    Delete an Entity (Organization) from session state, SQLite, and .axiom_memory.json.
    Refuses to delete the last remaining organization.
    """
    init_workspace_state()
    target = (entity_name or "").strip()
    options = list(get_entity_options())
    if target not in options:
        return False, f"Entity '{target}' was not found."
    if len(options) <= 1:
        return False, "Cannot delete the last remaining organization."

    options = [x for x in options if x != target]
    st.session_state["entity_options"] = options
    st.session_state.get("entity_jurisdictions", {}).pop(target, None)
    st.session_state.get("entity_year_ledgers", {}).pop(target, None)
    st.session_state.get("entity_ledgers", {}).pop(target, None)
    for files_state_key in ("entity_uploaded_files", "entity_year_files"):
        if files_state_key in st.session_state and isinstance(st.session_state[files_state_key], dict):
            st.session_state[files_state_key].pop(target, None)

    if st.session_state.get("active_entity") == target or st.session_state.get("entity") == target:
        st.session_state["active_entity"] = options[0]
        st.session_state["entity"] = options[0]

    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute("DELETE FROM uploaded_files WHERE entity = ?", (target,))
            conn.execute("DELETE FROM master_ledgers WHERE entity = ?", (target,))
            conn.execute("DELETE FROM property_loans WHERE entity = ?", (target,))
            conn.execute("DELETE FROM property_loan_rate_history WHERE entity = ?", (target,))
            conn.execute("DELETE FROM loan_reminder_log WHERE entity = ?", (target,))

    store = _load_memory_store()
    store["entity_options"] = list(options)
    store["active_entity"] = str(st.session_state["active_entity"])
    for map_key in (
        "entity_jurisdictions",
        "entity_properties",
        "memorized_rules",
        "entity_property_aliases",
        "entity_account_codes",
        "entity_code_scopes",
    ):
        sub = store.get(map_key)
        if isinstance(sub, dict):
            sub.pop(target, None)
    _save_memory_store(store)
    return True, f"Organization '{target}' removed."


def add_new_tax_year(year_value: str) -> tuple[bool, str]:
    """
    Dynamically add a single financial year (e.g. '2028') to the workspace year bar
    and initialize isolated DataFrames for all entities.
    """
    init_workspace_state()
    cleaned_year = (year_value or "").strip()
    if not re.match(r"^\d{4}$", cleaned_year):
        return False, "Financial year must be a 4-digit year (e.g., 2028)."

    years = get_tax_year_options()
    if cleaned_year in years:
        st.session_state["active_tax_year"] = cleaned_year
        return True, f"Switched to existing financial year {cleaned_year}."

    years.append(cleaned_year)
    years.sort()
    st.session_state["tax_year_options"] = years

    for ent in get_entity_options():
        st.session_state["entity_year_ledgers"].setdefault(ent, {})
        st.session_state["entity_year_ledgers"][ent][cleaned_year] = pd.DataFrame(columns=STANDARD_COLUMNS)

    st.session_state["active_tax_year"] = cleaned_year
    return True, f"Financial year {cleaned_year} added and selected."
