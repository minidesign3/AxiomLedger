"""
axiom_ingestion
===============

Ingestion orchestration without UI: vault/SQLite reconciliation, the uploaded
file list for an entity + financial year, rebuilding the ledger from stored
file records, memory training from coded rows and per-file red/unsure counts.

Extracted from ``utils.py`` (phase D15a) with byte-identical bodies and
re-exported there for existing call sites. The upload pipeline (persisting
uploads, OCR-on-upload, coded-workbook import) lives in ``axiom_upload``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from axiom_categorization import apply_rule_categorization
from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY, is_uncategorized
from axiom_dataframe import normalize_financial_dataframe
from axiom_db import _save_file_record_to_sqlite, _save_master_ledger_to_sqlite
from axiom_ledger import (
    _load_uploaded_files_from_sqlite,
    is_property_unassigned,
    set_entity_ledger,
)
from axiom_parsing import (
    _read_bank_csv_bytes,
    extract_other_document_tables,
    extract_pdf_tables,
)
from axiom_properties import get_entity_properties
from axiom_rules import save_memorized_rule
from axiom_state import get_active_entity, get_active_tax_year, init_workspace_state
from axiom_traffic import evaluate_row_traffic_light
from axiom_vault import get_vault_dir


def _reconcile_vault_and_broken_csvs(
    entity: str,
    tax_year: str,
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
    """
    1. Automatically repair any previously uploaded CSV record that had 0 rows due to bank metadata preamble lines.
    2. Automatically recover any orphaned files present in `data/vault/<Entity>/<Year>/<Bucket>/` on disk
       that were interrupted before being registered in SQLite `uploaded_files`.
    """
    changed = False
    existing_names = {str(r.get("name", "")).strip() for r in records}

    # 1. Repair 0-row CSVs that have a valid vault file on disk
    for rec in records:
        if str(rec.get("bucket", "")) == "csv":
            df_cur = rec.get("df")
            if not isinstance(df_cur, pd.DataFrame) or df_cur.empty:
                vpath = str(rec.get("vault_path", "") or "")
                vp = Path(vpath) if vpath else None
                if vp and not vp.is_absolute():
                    vp = Path(__file__).resolve().parent / vp
                if vp and vp.exists() and vp.is_file():
                    try:
                        raw_df = _read_bank_csv_bytes(vp.read_bytes())
                        if not raw_df.empty:
                            norm_df = (
                                raw_df.copy()
                                if list(raw_df.columns) == STANDARD_COLUMNS
                                else normalize_financial_dataframe(raw_df)
                            )
                            if not norm_df.empty:
                                cat_df = apply_rule_categorization(norm_df, entity=entity)
                                rec["df"] = cat_df
                                rec["note"] = ""
                                _save_file_record_to_sqlite(entity, tax_year, rec)
                                changed = True
                    except Exception:
                        pass

    # 2. Recover orphaned files in data/vault/<Entity>/<Year>/<Bucket>/
    for b_key in ("csv", "pdf", "screenshots", "other"):
        b_dir = get_vault_dir(entity, tax_year, b_key)
        if not b_dir.exists():
            continue
        for fpath in sorted(b_dir.iterdir()):
            if not fpath.is_file() or fpath.name.startswith(".") or fpath.name.lower().endswith(".ocr.md"):
                continue
            if fpath.name in existing_names:
                continue
            try:
                fbytes = fpath.read_bytes()
                fname = fpath.name
                raw_df = pd.DataFrame()
                parse_note = ""
                needs_ocr = False
                if b_key == "csv":
                    raw_df = _read_bank_csv_bytes(fbytes)
                elif b_key == "pdf":
                    raw_df = extract_pdf_tables(fbytes)
                elif b_key == "screenshots":
                    needs_ocr = True
                    parse_note = "Recovered from Vault — click '🔍 Run AI OCR' or open file."
                else:
                    raw_df = extract_other_document_tables(fbytes, fname)

                if not raw_df.empty:
                    norm_df = (
                        raw_df.copy()
                        if list(raw_df.columns) == STANDARD_COLUMNS
                        else normalize_financial_dataframe(raw_df)
                    )
                    cat_df = apply_rule_categorization(norm_df, entity=entity)
                elif b_key == "screenshots":
                    placeholder = pd.DataFrame(
                        [
                            {
                                "Date": pd.Timestamp.today().strftime("%Y-%m-%d"),
                                "Amount": 0.0,
                                "Description": f"Screenshot Receipt ({fname}) — Enter details or run OCR",
                                "Property": UNASSIGNED_PROPERTY,
                                "Account Code": "Uncategorized",
                            }
                        ]
                    )[STANDARD_COLUMNS]
                    cat_df = apply_rule_categorization(placeholder, entity=entity)
                else:
                    cat_df = pd.DataFrame(columns=STANDARD_COLUMNS)

                new_rec: dict[str, object] = {
                    "id": f"file_{len(records) + 1}_{abs(hash(f'{fname}::{len(fbytes)}')) % 100000}",
                    "signature": f"{fname}::{len(fbytes)}",
                    "name": fname,
                    "bucket": b_key,
                    "uploaded_at": pd.Timestamp.now().strftime("%H:%M:%S"),
                    "vault_path": str(fpath),
                    "df": cat_df,
                    "raw_bytes": fbytes if b_key == "screenshots" else None,
                    "mime_type": "image/png" if fname.lower().endswith(".png") else ("image/jpeg" if b_key == "screenshots" else ""),
                    "needs_ocr": needs_ocr,
                    "note": parse_note,
                }
                _save_file_record_to_sqlite(entity, tax_year, new_rec, sort_order=len(records))
                records.append(new_rec)
                existing_names.add(fname)
                changed = True
            except Exception:
                continue

    if changed:
        frames = [
            r["df"].reindex(columns=STANDARD_COLUMNS).copy()
            for r in records
            if isinstance(r.get("df"), pd.DataFrame) and not r["df"].empty
        ]
        combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=STANDARD_COLUMNS)
        _save_master_ledger_to_sqlite(entity, tax_year, combined)

    return records

def get_entity_uploaded_files(
    entity: str | None = None,
    tax_year: str | None = None,
) -> list[dict[str, object]]:
    """
    Return the list of uploaded file records for `(entity, tax_year)`.
    Automatically hydrates from SQLite (`data/axiom_ledger.db`) and the local File Vault
    (`data/vault/<Entity>/<Year>/<Bucket>/`) on first access in a session.
    """
    init_workspace_state()
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    st.session_state.setdefault("entity_uploaded_files", {})
    st.session_state["entity_uploaded_files"].setdefault(target_entity, {})
    st.session_state.setdefault("_sqlite_loaded_files", set())
    load_key = f"{target_entity}::{target_year}"

    if load_key not in st.session_state["_sqlite_loaded_files"]:
        db_files = _load_uploaded_files_from_sqlite(target_entity, target_year)
        db_files = _reconcile_vault_and_broken_csvs(target_entity, target_year, db_files)
        st.session_state["entity_uploaded_files"][target_entity][target_year] = db_files
        st.session_state["_sqlite_loaded_files"].add(load_key)
    elif target_year not in st.session_state["entity_uploaded_files"][target_entity]:
        db_files = _load_uploaded_files_from_sqlite(target_entity, target_year)
        db_files = _reconcile_vault_and_broken_csvs(target_entity, target_year, db_files)
        st.session_state["entity_uploaded_files"][target_entity][target_year] = db_files
    return st.session_state["entity_uploaded_files"][target_entity][target_year]

def sync_entity_ledger_from_files(
    entity: str | None = None,
    tax_year: str | None = None,
) -> pd.DataFrame:
    """
    Persist each file record's DataFrame to SQLite (`uploaded_files` + `file_transactions`)
    and rebuild the master entity ledger for `(entity, tax_year)` from all uploaded files.
    """
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    files_list = get_entity_uploaded_files(target_entity, target_year)
    frames: list[pd.DataFrame] = []
    for f_rec in files_list:
        _save_file_record_to_sqlite(target_entity, target_year, f_rec)
        f_df = f_rec.get("df")
        if isinstance(f_df, pd.DataFrame) and not f_df.empty:
            frames.append(f_df.reindex(columns=STANDARD_COLUMNS).copy())
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        return set_entity_ledger(combined, target_entity, target_year)
    empty_df = pd.DataFrame(columns=STANDARD_COLUMNS)
    return set_entity_ledger(empty_df, target_entity, target_year)

def train_memory_from_coded_dataframe(
    df: pd.DataFrame,
    entity: str | None = None,
    source_label: str = "2026 Coded Accounts",
) -> tuple[int, int]:
    """
    Scan a pre-coded DataFrame (such as last year's 2026 accountant spreadsheet)
    and memorize every row that has a valid Description and a coded Account Code (and/or Property).
    Returns `(learned_count, new_properties_count)`.
    """
    resolved_entity = get_active_entity(entity)
    norm_df = normalize_financial_dataframe(df)
    if norm_df.empty:
        return 0, 0

    initial_props = set(p.lower() for p in get_entity_properties(resolved_entity))
    learned = 0
    for _, row in norm_df.iterrows():
        desc = str(row.get("Description", "")).strip()
        prop = str(row.get("Property", "")).strip() or UNASSIGNED_PROPERTY
        code = str(row.get("Account Code", "")).strip()
        if not desc:
            continue
        if is_uncategorized(code) and is_property_unassigned(prop, code):
            continue
        ok, _ = save_memorized_rule(
            pattern=desc,
            property_name=prop,
            account_code=code if not is_uncategorized(code) else "Uncategorized",
            entity=resolved_entity,
            source=source_label,
        )
        if ok:
            learned += 1

    final_props = set(p.lower() for p in get_entity_properties(resolved_entity))
    new_props_added = max(0, len(final_props) - len(initial_props))
    return learned, new_props_added

def get_file_red_counts(df: pd.DataFrame, entity: str | None = None) -> dict[str, int]:
    """
    Compute 3-tier Traffic Light counts for a ledger or uploaded file DataFrame:
    - total: total rows
    - green: 🟢 100% Sure (both Property and Account Code assigned & matched to a confirmed rule)
    - yellow: 🟡 Partially Sure / Needs Double-Check (either Property or Item found, or keyword guess not yet memorized)
    - red: 🔴 Not Sure at All (neither Item nor Property known)
    - certain: alias for `green` (for backward compatibility)
    - needs_property: rows missing Property
    - unknown_code: rows missing Account Code (Item)
    - red_total: total rows needing attention or double-check (`red + yellow`)
    """
    if df is None or df.empty:
        return {
            "total": 0,
            "green": 0,
            "yellow": 0,
            "red": 0,
            "certain": 0,
            "needs_property": 0,
            "unknown_code": 0,
            "red_total": 0,
        }

    resolved_entity = get_active_entity(entity)
    total = len(df)
    green_cnt = 0
    yellow_cnt = 0
    red_cnt = 0
    needs_prop = 0
    unknown_code = 0

    for _, row in df.iterrows():
        prop = row.get("Property", UNASSIGNED_PROPERTY)
        code = row.get("Account Code", "Uncategorized")
        desc = row.get("Description", "")
        miss_code = is_uncategorized(code)
        miss_prop = is_property_unassigned(prop, code)
        if miss_code:
            unknown_code += 1
        elif miss_prop:
            needs_prop += 1

        tier, _ = evaluate_row_traffic_light(
            property_val=prop,
            account_code=code,
            description=desc,
            entity=resolved_entity,
        )
        if tier == "green":
            green_cnt += 1
        elif tier == "yellow":
            yellow_cnt += 1
        else:
            red_cnt += 1

    return {
        "total": total,
        "green": green_cnt,
        "yellow": yellow_cnt,
        "red": red_cnt,
        "certain": green_cnt,
        "needs_property": needs_prop,
        "unknown_code": unknown_code,
        "red_total": red_cnt + yellow_cnt,
    }
