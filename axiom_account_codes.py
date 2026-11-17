"""
axiom_account_codes
===================

Entity Chart-of-Accounts CRUD, Property shorthand-alias storage and the
deterministic Property detection helpers, extracted from the monolithic
``utils.py`` god-module (decomposition phase D6).

``get_entity_account_codes`` seeds the NZ default code list (or the
jurisdiction keyword values for US entities) and keeps ``Rent Income`` /
``Rent Income Raywhite`` present; ``rename_entity_account_code`` migrates a
code across the store, memorized rules, in-memory uploaded files/ledgers and
the SQLite ``file_transactions`` / ``master_ledgers`` tables.

``detect_property_from_text`` maps a free-text CSV line (description, payee,
reference, ...) to a configured Property using explicit aliases plus the
derived house-name tokens, falling back to the BT Property Holdings loan
account triggers.

Every function body was moved byte-for-byte; ``utils.py`` imports and
re-exports them so all existing ``from utils import ...`` / ``utils.<name>``
call sites keep working unchanged.
"""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from axiom_core import (
    DEFAULT_CUSTOM_ACCOUNT_CODES,
    DEFAULT_PROPERTY_ALIASES,
    ENTITY_CATEGORIZATION_RULES,
    UNASSIGNED_PROPERTY,
    UNCATEGORIZED_MARKERS,
)
from axiom_db import _DB_LOCK, _ensure_sqlite_schema, _get_sqlite_conn
from axiom_loan_crud import match_loan_from_text
from axiom_memory import _load_memory_store, _save_memory_store
from axiom_properties import get_entity_properties
from axiom_state import (
    get_active_entity,
    get_active_tax_year,
    get_entity_jurisdiction,
    init_workspace_state,
)


def get_entity_account_codes(entity: str | None = None) -> list[str]:
    """
    Return the custom Chart of Account Codes configured for the entity.
    Includes custom codes from `Codes.xlsx` (e.g. Materials, Bodycorp, Rent Income, Bank Interest,
    James Drawings, Kathy Drawings, etc.) plus any codes learned from memorized rules.
    """
    resolved = get_active_entity(entity)
    store = _load_memory_store()
    codes_map = store.setdefault("entity_account_codes", {})
    if not isinstance(codes_map, dict):
        codes_map = {}
        store["entity_account_codes"] = codes_map
    if resolved not in codes_map or not isinstance(codes_map[resolved], list) or not codes_map[resolved]:
        jur = get_entity_jurisdiction(resolved)
        base_codes = list(DEFAULT_CUSTOM_ACCOUNT_CODES) if jur == "NZ" else sorted(set(get_entity_rules(resolved).values()))
        codes_map[resolved] = base_codes
        _save_memory_store(store)
    elif get_entity_jurisdiction(resolved) == "NZ":
        ent_codes = list(codes_map[resolved])
        changed = False
        for req_code in ("Rent Income", "Rent Income Raywhite"):
            if not any(c.lower() == req_code.lower() for c in ent_codes):
                ent_codes.append(req_code)
                changed = True
        if changed:
            ent_codes.sort(key=lambda s: s.lower())
            codes_map[resolved] = ent_codes
            _save_memory_store(store)
    return sorted(list(codes_map[resolved]), key=lambda s: s.lower())


def add_entity_account_code(code_name: str, entity: str | None = None) -> tuple[bool, str]:
    """Add a new Account Code to the entity's Chart of Accounts and persist it."""
    resolved = get_active_entity(entity)
    cleaned = (code_name or "").strip()
    if not cleaned or cleaned.lower() in UNCATEGORIZED_MARKERS:
        return False, "Account Code name cannot be empty."
    store = _load_memory_store()
    codes_map = store.setdefault("entity_account_codes", {})
    current = list(codes_map.get(resolved, get_entity_account_codes(resolved)))
    if any(c.lower() == cleaned.lower() for c in current):
        return False, f"Account Code '{cleaned}' already exists for {resolved}."
    current.append(cleaned)
    current.sort(key=lambda s: s.lower())
    codes_map[resolved] = current
    _save_memory_store(store)
    return True, f"Added Account Code '{cleaned}' to {resolved}."


def remove_entity_account_code(code_name: str, entity: str | None = None) -> tuple[bool, str]:
    """Remove an Account Code from the entity's custom Chart of Accounts."""
    resolved = get_active_entity(entity)
    target = (code_name or "").strip()
    store = _load_memory_store()
    codes_map = store.setdefault("entity_account_codes", {})
    current = list(codes_map.get(resolved, get_entity_account_codes(resolved)))
    filtered = [c for c in current if c.lower() != target.lower()]
    codes_map[resolved] = filtered
    _save_memory_store(store)
    return True, f"Removed Account Code '{target}'."


def rename_entity_account_code(
    old_code: str,
    new_code: str,
    entity: str | None = None,
) -> tuple[bool, str]:
    """
    Rename an Account Code across the entity's Chart of Accounts, memorized rules,
    uploaded file DataFrames, and financial year ledgers.
    """
    init_workspace_state()
    resolved = get_active_entity(entity)
    src_code = (old_code or "").strip()
    target_code = (new_code or "").strip()
    if not target_code:
        return False, "New Account Code cannot be empty."
    if src_code == target_code:
        return True, f"Account Code '{src_code}' unchanged."

    store = _load_memory_store()
    codes_map = store.setdefault("entity_account_codes", {})
    current = list(codes_map.get(resolved, get_entity_account_codes(resolved)))
    updated_list: list[str] = []
    found = False
    for c in current:
        if c.lower() == src_code.lower():
            if not any(x.lower() == target_code.lower() for x in updated_list):
                updated_list.append(target_code)
            found = True
        else:
            if not any(x.lower() == c.lower() for x in updated_list):
                updated_list.append(c)
    if not found and not any(x.lower() == target_code.lower() for x in updated_list):
        updated_list.append(target_code)
    updated_list.sort(key=lambda s: s.lower())
    codes_map[resolved] = updated_list

    # Migrate memorized rules
    rules_map = store.setdefault("memorized_rules", {})
    ent_rules: list[dict[str, str]] = list(rules_map.get(resolved, []))
    for rule_item in ent_rules:
        if str(rule_item.get("account_code", "")).strip().lower() == src_code.lower():
            rule_item["account_code"] = target_code
    rules_map[resolved] = ent_rules
    _save_memory_store(store)

    # Migrate in-memory uploaded files & ledgers
    files_by_ent = st.session_state.get("entity_uploaded_files", {}).get(resolved, {})
    if isinstance(files_by_ent, dict):
        for _yr, file_list in files_by_ent.items():
            if isinstance(file_list, list):
                for frec in file_list:
                    fdf = frec.get("df")
                    if isinstance(fdf, pd.DataFrame) and "Account Code" in fdf.columns and not fdf.empty:
                        mask = fdf["Account Code"].astype(str).str.strip().str.lower() == src_code.lower()
                        if mask.any():
                            fdf.loc[mask, "Account Code"] = target_code
                            frec["df"] = fdf

    ledgers_by_ent = st.session_state.get("entity_year_ledgers", {}).get(resolved, {})
    if isinstance(ledgers_by_ent, dict):
        for yr, ldf in list(ledgers_by_ent.items()):
            if isinstance(ldf, pd.DataFrame) and "Account Code" in ldf.columns and not ldf.empty:
                mask = ldf["Account Code"].astype(str).str.strip().str.lower() == src_code.lower()
                if mask.any():
                    ldf.loc[mask, "Account Code"] = target_code
                    ledgers_by_ent[yr] = ldf
                    if yr == get_active_tax_year():
                        st.session_state["entity_ledgers"][resolved] = ldf

    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                """
                UPDATE file_transactions
                SET account_code = ?
                WHERE LOWER(TRIM(account_code)) = LOWER(TRIM(?))
                  AND file_id IN (SELECT file_id FROM uploaded_files WHERE entity = ?)
                """,
                (target_code, src_code, resolved),
            )
            conn.execute(
                """
                UPDATE master_ledgers
                SET account_code = ?
                WHERE entity = ? AND LOWER(TRIM(account_code)) = LOWER(TRIM(?))
                """,
                (target_code, resolved, src_code),
            )
    return True, f"Renamed Account Code '{src_code}' → '{target_code}'."


def get_property_aliases(entity: str | None = None) -> dict[str, list[str]]:
    """
    Return the dictionary mapping each Property name -> list of shorthand/bank aliases
    (e.g. '363 Queen': ['363 queen', '363queen', '16d', 'unit 16d', 'apt 1604', 'quest hotel']).
    """
    resolved = get_active_entity(entity)
    store = _load_memory_store()
    alias_map = store.setdefault("entity_property_aliases", {})
    if not isinstance(alias_map, dict):
        alias_map = {}
        store["entity_property_aliases"] = alias_map
    ent_aliases = alias_map.get(resolved)
    if not isinstance(ent_aliases, dict) or not ent_aliases:
        ent_aliases = {}
        for p in get_entity_properties(resolved):
            if p in DEFAULT_PROPERTY_ALIASES:
                ent_aliases[p] = list(DEFAULT_PROPERTY_ALIASES[p])
            else:
                ent_aliases[p] = [p.lower()]
        alias_map[resolved] = ent_aliases
        _save_memory_store(store)
    else:
        # Ensure all current properties have an entry
        changed = False
        for p in get_entity_properties(resolved):
            if p not in ent_aliases:
                ent_aliases[p] = list(DEFAULT_PROPERTY_ALIASES.get(p, [p.lower()]))
                changed = True
        if changed:
            alias_map[resolved] = ent_aliases
            _save_memory_store(store)
    return dict(ent_aliases)


def save_property_aliases(
    property_name: str,
    aliases_csv_or_list: str | list[str],
    entity: str | None = None,
) -> tuple[bool, str]:
    """Save bank shorthand aliases for a specific Property."""
    resolved = get_active_entity(entity)
    prop = (property_name or "").strip()
    if not prop:
        return False, "Property name cannot be empty."
    if isinstance(aliases_csv_or_list, str):
        raw_items = [x.strip() for x in aliases_csv_or_list.split(",") if x.strip()]
    else:
        raw_items = [str(x).strip() for x in aliases_csv_or_list if str(x).strip()]
    deduped: list[str] = []
    for item in raw_items:
        if item.lower() not in [d.lower() for d in deduped]:
            deduped.append(item)
    store = _load_memory_store()
    alias_map = store.setdefault("entity_property_aliases", {})
    ent_aliases = dict(alias_map.get(resolved, {}))
    ent_aliases[prop] = deduped
    alias_map[resolved] = ent_aliases
    _save_memory_store(store)
    return True, f"Saved {len(deduped)} matching keyword(s) for '{prop}'."


_GENERIC_STREET_SUFFIXES = {
    "st",
    "street",
    "rd",
    "road",
    "pl",
    "plc",
    "place",
    "way",
    "ave",
    "avenue",
    "dr",
    "drive",
    "ln",
    "lane",
    "ct",
    "court",
    "cres",
    "crescent",
    "tce",
    "terrace",
    "blvd",
    "boulevard",
    "unit",
    "apt",
    "flat",
    "no",
    "property",
    "general",
    "portfolio",
}


def _derive_smart_property_tokens(property_name: str, configured_aliases: list[str] | None = None) -> list[str]:
    """
    Build the complete list of matching keywords for a property, combining:
    1. Explicit user-configured aliases
    2. Built-in DEFAULT_PROPERTY_ALIASES if matching
    3. Automatically derived distinctive house/street name words ( >= 4 chars, e.g. 'damian', 'damien',
       'weymouth', 'blampied', 'frances', 'raleigh', 'queen', 'southview', 'milson', 'melbourne', 'lauretta')
       plus compact number+street forms (e.g. '9damian', '33blam', '14ral', '363queen').
    """
    p_clean = (property_name or "").strip()
    if not p_clean or p_clean.lower() in ("general / portfolio", "general / portfolio-wide", "unassigned"):
        return []

    tokens: list[str] = []

    def _add_tok(t: str) -> None:
        tc = t.strip().lower()
        if tc and tc not in tokens:
            tokens.append(tc)

    _add_tok(p_clean)
    for k_def, def_list in DEFAULT_PROPERTY_ALIASES.items():
        if k_def.lower() == p_clean.lower() or k_def.lower() in p_clean.lower() or p_clean.lower() in k_def.lower():
            for d_tok in def_list:
                _add_tok(d_tok)

    if configured_aliases:
        for c_tok in configured_aliases:
            _add_tok(str(c_tok))

    # Compact no-space version (e.g. "9 Damian" -> "9damian", "303 Weymouth" -> "303weymouth")
    compact = re.sub(r"[^a-z0-9]+", "", p_clean.lower())
    if len(compact) >= 4:
        _add_tok(compact)

    # Extract individual words from property name & aliases (e.g. "damian", "weymouth", "blampied", "frances", "raleigh", "queen", "1604")
    word_parts = re.findall(r"[a-z0-9]+", p_clean.lower())
    nums = [w for w in word_parts if w.isdigit()]
    alpha_words = [w for w in word_parts if not w.isdigit() and w not in _GENERIC_STREET_SUFFIXES]

    for w in alpha_words:
        if len(w) >= 4:
            _add_tok(w)
            # Common spelling variant: damian <-> damien
            if w == "damian":
                _add_tok("damien")
            elif w == "damien":
                _add_tok("damian")
            # Short 4-letter prefix with house number (e.g. "33blam", "14ral")
            for num in nums:
                _add_tok(f"{num}{w}")
                _add_tok(f"{num} {w}")
                if len(w) >= 4:
                    _add_tok(f"{num}{w[:4]}")
                    _add_tok(f"{num}{w[:3]}")

    # Also allow distinctive 4+ digit unit/apartment numbers like '1604'
    for num in nums:
        if len(num) >= 4:
            _add_tok(num)

    tokens.sort(key=len, reverse=True)
    return tokens


def detect_property_from_text(
    text: object,
    entity: str | None = None,
) -> str:
    """
    Deterministically detect which Property a string (anywhere across a CSV line:
    Description, Payee, Analysis Code, Reference, Particulars, Memo, or EOY section header)
    refers to using configured Property names, Property Shorthand Aliases, and smart house-name tokens
    (e.g., 'Damian' -> '9 Damian', 'Blampied' -> '33 Blampied', 'Weymouth' -> '303 Weymouth').
    Supports multi-property split notes like '33Blam 14Ral' -> '33 Blampied / 14 Raleigh'.
    Returns UNASSIGNED_PROPERTY if no property alias matches.
    """
    if pd.isna(text):
        return UNASSIGNED_PROPERTY
    s = str(text).strip()
    if not s or s.lower() in ("nan", "none", "null", "#name?"):
        return UNASSIGNED_PROPERTY
    s_lower = s.lower()

    resolved = get_active_entity(entity)
    props = get_entity_properties(resolved)
    aliases_by_prop = get_property_aliases(resolved)

    # Check exact match to a configured property name first
    for p in props:
        if p.lower() == s_lower:
            return p

    # Collect all non-general properties whose aliases or house tokens appear in `s_lower`
    matched_props: list[str] = []
    for p in props:
        if p.lower() in ("general / portfolio", "general / portfolio-wide"):
            continue
        candidate_tokens = _derive_smart_property_tokens(p, aliases_by_prop.get(p, []))
        for tok_clean in candidate_tokens:
            if len(tok_clean) < 3:
                continue
            # For short 3-4 char tokens or purely numeric tokens, require word/token boundary so we don't false-match inside unrelated words
            if len(tok_clean) <= 4 or tok_clean.isdigit():
                pattern = rf"(?<![a-z0-9]){re.escape(tok_clean)}(?![a-z0-9])"
                if re.search(pattern, s_lower):
                    if p not in matched_props:
                        matched_props.append(p)
                    break
            else:
                if tok_clean in s_lower:
                    if p not in matched_props:
                        matched_props.append(p)
                    break

    if len(matched_props) == 1:
        return matched_props[0]
    if len(matched_props) > 1:
        return " / ".join(matched_props)

    # Loan account / trigger match (BT Property Holdings Ltd NZ loans only)
    try:
        _loan_hit = match_loan_from_text(s, entity=resolved)
        if _loan_hit:
            return str(_loan_hit.get("property_name") or UNASSIGNED_PROPERTY)
    except Exception:
        pass

    return UNASSIGNED_PROPERTY


def get_account_code_options(entity: str | None = None) -> list[str]:
    """Return sorted list of valid Account Codes for dropdown selection based on entity custom codes + jurisdiction + memorized codes."""
    # Local import: axiom_rules imports this module at module level (phase D6).
    from axiom_rules import get_memorized_rules

    resolved = get_active_entity(entity)
    rules = get_entity_rules(resolved)
    codes = set(rules.values())
    codes.update(get_entity_account_codes(resolved))
    codes.update(["Rental Income", "Rent Income", "Other Expense", "Owner Contribution", "Owner Drawing"])
    for mem in get_memorized_rules(resolved):
        c = str(mem.get("account_code", "")).strip()
        if c and c.lower() not in UNCATEGORIZED_MARKERS:
            codes.add(c)
    sorted_codes = sorted(codes, key=lambda s: s.lower())
    return ["Uncategorized"] + sorted_codes


def get_entity_rules(entity: str | None = None) -> dict[str, str]:
    """Return the keyword-matching rules dictionary for the specified or active entity's jurisdiction."""
    jurisdiction = get_entity_jurisdiction(entity)
    return ENTITY_CATEGORIZATION_RULES.get(jurisdiction, ENTITY_CATEGORIZATION_RULES["NZ"])
