"""
axiom_ledger
============

Entity + financial-year ledger IO: SQLite-backed uploaded-file loading and
auto-repair / auto-recovery of orphaned vault files, the per-entity ledger
cache (get / set / append) and the vault ZIP export.

``get_entity_ledger`` imports ``axiom_ingestion.get_entity_uploaded_files``
inside its body because ``axiom_ingestion`` imports this module at module
level (the same order-independent seam pattern as ``axiom_loan_crud``).
The traffic-light engine moved to ``axiom_traffic`` in the same phase to keep
every module under the 500-line cap. ``utils.py`` re-exports everything.
"""

from __future__ import annotations

import io
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from axiom_categorization import apply_rule_categorization
from axiom_core import (
    STANDARD_COLUMNS,
    UNASSIGNED_PROPERTY,
    UNCATEGORIZED_MARKERS,
    _safe_vault_segment,
)
from axiom_dataframe import (
    _clean_amount_series,
    export_xero_master_csv,
    normalize_financial_dataframe,
)
from axiom_db import (
    _DB_LOCK,
    VAULT_ROOT_DIR,
    _ensure_sqlite_schema,
    _get_sqlite_conn,
    _load_master_ledger_from_sqlite,
    _save_file_record_to_sqlite,
    _save_master_ledger_to_sqlite,
)
from axiom_parsing import (
    _read_bank_csv_bytes,
    extract_other_document_tables,
    extract_pdf_tables,
)
from axiom_properties import is_portfolio_wide_code
from axiom_state import get_active_entity, get_active_tax_year, init_workspace_state
from axiom_vault import get_vault_dir


def _load_uploaded_files_from_sqlite(entity: str, tax_year: str) -> list[dict[str, object]]:
    """Load all persisted uploaded file records and their DataFrames for `(entity, tax_year)` from SQLite."""
    _ensure_sqlite_schema()
    loaded: list[dict[str, object]] = []
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            file_rows = conn.execute(
                """
                SELECT file_id, name, signature, bucket, vault_path, mime_type,
                       uploaded_at, needs_ocr, note
                FROM uploaded_files
                WHERE entity = ? AND tax_year = ?
                ORDER BY sort_order ASC, rowid ASC
                """,
                (entity, tax_year),
            ).fetchall()

            for frow in file_rows:
                fid = str(frow["file_id"])
                tx_rows = conn.execute(
                    """
                    SELECT tx_date, amount, description, property, account_code
                    FROM file_transactions
                    WHERE file_id = ?
                    ORDER BY row_index ASC
                    """,
                    (fid,),
                ).fetchall()

                if tx_rows:
                    df = pd.DataFrame(
                        [
                            {
                                "Date": str(tr["tx_date"] or ""),
                                "Amount": float(tr["amount"] or 0.0),
                                "Description": str(tr["description"] or ""),
                                "Property": str(tr["property"] or UNASSIGNED_PROPERTY),
                                "Account Code": str(tr["account_code"] or "Uncategorized"),
                            }
                            for tr in tx_rows
                        ]
                    )[STANDARD_COLUMNS]
                else:
                    df = pd.DataFrame(columns=STANDARD_COLUMNS)

                vpath_str = str(frow["vault_path"] or "")
                raw_bytes = None
                if str(frow["bucket"]) == "screenshots" and vpath_str:
                    vp = Path(vpath_str)
                    if not vp.is_absolute():
                        vp = Path(__file__).resolve().parent / vp
                    if vp.exists() and vp.is_file():
                        try:
                            raw_bytes = vp.read_bytes()
                        except Exception:
                            raw_bytes = None

                loaded.append(
                    {
                        "id": fid,
                        "signature": str(frow["signature"] or ""),
                        "name": str(frow["name"] or "Uploaded_File"),
                        "bucket": str(frow["bucket"] or "csv"),
                        "vault_path": vpath_str,
                        "uploaded_at": str(frow["uploaded_at"] or ""),
                        "df": df,
                        "raw_bytes": raw_bytes,
                        "mime_type": str(frow["mime_type"] or ""),
                        "needs_ocr": bool(frow["needs_ocr"]),
                        "note": str(frow["note"] or ""),
                    }
                )

    # Auto-repair any 0-row CSV records that previously failed due to bank preamble headers (e.g., ASB CSVs)
    # and auto-recover any orphaned files sitting in `data/vault/<Entity>/<Year>/<Bucket>/` on disk.
    repaired_or_recovered = False
    for rec in loaded:
        if (
            rec.get("bucket") == "csv"
            and isinstance(rec.get("df"), pd.DataFrame)
            and rec["df"].empty
            and "parse warning" in str(rec.get("note", "")).lower()
            and rec.get("vault_path")
        ):
            vp = Path(str(rec["vault_path"]))
            if not vp.is_absolute():
                vp = Path(__file__).resolve().parent / vp
            if vp.exists() and vp.is_file():
                try:
                    raw_df = _read_bank_csv_bytes(vp.read_bytes())
                    if not raw_df.empty:
                        norm_df = (
                            raw_df.copy()
                            if list(raw_df.columns) == STANDARD_COLUMNS
                            else normalize_financial_dataframe(raw_df)
                        )
                        if not norm_df.empty:
                            rec["df"] = apply_rule_categorization(norm_df, entity=entity)
                            rec["note"] = ""
                            _save_file_record_to_sqlite(entity, tax_year, rec)
                            repaired_or_recovered = True
                except Exception:
                    pass

    # Scan `data/vault/<Entity>/<Year>/<Bucket>/` for any orphaned files written to disk
    # whose SQLite insert was interrupted (e.g. during a previous multi-screenshot upload)
    known_names_by_bucket = {
        (str(r.get("bucket", "")).lower(), str(r.get("name", "")).strip())
        for r in loaded
    }
    for b_key in ("csv", "pdf", "screenshots", "other"):
        b_dir = get_vault_dir(entity, tax_year, b_key)
        if b_dir.exists() and b_dir.is_dir():
            for disk_file in sorted(b_dir.iterdir()):
                if not disk_file.is_file() or disk_file.name.startswith(".") or disk_file.name.lower().endswith(".ocr.md"):
                    continue
                if (b_key, disk_file.name) in known_names_by_bucket:
                    continue
                try:
                    fbytes = disk_file.read_bytes()
                    fsig = f"{disk_file.name}::{len(fbytes)}"
                    try:
                        rel_vpath = str(disk_file.relative_to(Path(__file__).resolve().parent))
                    except ValueError:
                        rel_vpath = str(disk_file)
                    rec_df = pd.DataFrame(columns=STANDARD_COLUMNS)
                    rec_note = ""
                    rec_needs_ocr = False
                    if b_key == "csv":
                        raw_c = _read_bank_csv_bytes(fbytes)
                        if not raw_c.empty:
                            norm_c = normalize_financial_dataframe(raw_c)
                            rec_df = apply_rule_categorization(norm_c, entity=entity)
                    elif b_key == "pdf":
                        raw_p = extract_pdf_tables(fbytes)
                        if not raw_p.empty:
                            norm_p = normalize_financial_dataframe(raw_p)
                            rec_df = apply_rule_categorization(norm_p, entity=entity)
                        else:
                            rec_note = "Saved PDF to Vault. No structured table found automatically."
                    elif b_key == "screenshots":
                        rec_needs_ocr = True
                        rec_note = "Saved in Vault — click '🔍 Run OCR on Screenshots' or open file to extract."
                        rec_df = pd.DataFrame(
                            [
                                {
                                    "Date": datetime.fromtimestamp(disk_file.stat().st_mtime).strftime("%Y-%m-%d"),
                                    "Amount": 0.0,
                                    "Description": disk_file.stem.replace("_", " ").replace("-", " "),
                                    "Property": UNASSIGNED_PROPERTY,
                                    "Account Code": "Uncategorized",
                                }
                            ]
                        )[STANDARD_COLUMNS]
                        rec_df = apply_rule_categorization(rec_df, entity=entity)
                    else:
                        raw_o = extract_other_document_tables(fbytes, disk_file.name)
                        if not raw_o.empty:
                            norm_o = normalize_financial_dataframe(raw_o)
                            rec_df = apply_rule_categorization(norm_o, entity=entity)

                    new_fid = f"file_{int(time.time() * 1000)}_{len(loaded) + 1}"
                    mime_str = (
                        "image/png"
                        if disk_file.suffix.lower() == ".png"
                        else ("image/jpeg" if b_key == "screenshots" else "")
                    )
                    recovered_rec: dict[str, object] = {
                        "id": new_fid,
                        "signature": fsig,
                        "name": disk_file.name,
                        "bucket": b_key,
                        "vault_path": rel_vpath,
                        "uploaded_at": datetime.fromtimestamp(disk_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "df": rec_df,
                        "raw_bytes": fbytes if b_key == "screenshots" else None,
                        "mime_type": mime_str,
                        "needs_ocr": rec_needs_ocr,
                        "note": rec_note,
                    }
                    loaded.append(recovered_rec)
                    _save_file_record_to_sqlite(entity, tax_year, recovered_rec)
                    known_names_by_bucket.add((b_key, disk_file.name))
                    repaired_or_recovered = True
                except Exception:
                    pass

    if repaired_or_recovered:
        frames = [
            r["df"].reindex(columns=STANDARD_COLUMNS).copy()
            for r in loaded
            if isinstance(r.get("df"), pd.DataFrame) and not r["df"].empty
        ]
        if frames:
            _save_master_ledger_to_sqlite(entity, tax_year, pd.concat(frames, ignore_index=True))

    return loaded

def export_entity_vault_zip(entity: str | None = None, tax_year: str | None = None) -> bytes:
    """
    Create an in-memory ZIP archive containing:
    1) All original uploaded files from `data/vault/<Entity>/<Year>/` (CSVs, PDFs, Screenshots, Excel/ODT)
    2) The coded Master Ledger CSV (`<Entity>_<Year>_Master_Coded_Ledger.csv`)
    """
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    ent_dir = _safe_vault_segment(target_entity)
    yr_dir = _safe_vault_segment(target_year)
    vault_folder = VAULT_ROOT_DIR / ent_dir / yr_dir

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        master_df = get_entity_ledger(target_entity, target_year)
        master_csv_bytes = export_xero_master_csv(master_df)
        zf.writestr(f"{ent_dir}_{yr_dir}_Master_Coded_Ledger.csv", master_csv_bytes)

        if vault_folder.exists():
            for file_path in sorted(vault_folder.rglob("*")):
                if file_path.is_file():
                    arcname = str(file_path.relative_to(vault_folder))
                    try:
                        zf.writestr(f"Original_Files/{arcname}", file_path.read_bytes())
                    except Exception:
                        pass
    return buf.getvalue()

def get_entity_ledger(entity: str | None = None, tax_year: str | None = None) -> pd.DataFrame:
    """
    Retrieve the standardized ledger DataFrame isolated by both Entity AND Financial Year.
    Loads from SQLite (`data/axiom_ledger.db`) on first access in a session so all coded
    transactions survive browser refreshes and restarts.
    """
    init_workspace_state()
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)

    st.session_state["entity_year_ledgers"].setdefault(target_entity, {})
    st.session_state.setdefault("_sqlite_loaded_ledgers", set())
    load_key = f"{target_entity}::{target_year}"

    if load_key not in st.session_state["_sqlite_loaded_ledgers"]:
        db_df = _load_master_ledger_from_sqlite(target_entity, target_year)
        if db_df.empty:
            # If master_ledgers table is empty for (entity, year) but uploaded_files exist in SQLite, rebuild from files
            # Local import: axiom_ingestion imports this module at module level.
            from axiom_ingestion import get_entity_uploaded_files

            files_list = get_entity_uploaded_files(target_entity, target_year)
            frames = [
                f["df"].reindex(columns=STANDARD_COLUMNS).copy()
                for f in files_list
                if isinstance(f.get("df"), pd.DataFrame) and not f["df"].empty
            ]
            if frames:
                db_df = pd.concat(frames, ignore_index=True)
                _save_master_ledger_to_sqlite(target_entity, target_year, db_df)
        st.session_state["entity_year_ledgers"][target_entity][target_year] = db_df
        st.session_state["_sqlite_loaded_ledgers"].add(load_key)
    elif target_year not in st.session_state["entity_year_ledgers"][target_entity]:
        st.session_state["entity_year_ledgers"][target_entity][target_year] = _load_master_ledger_from_sqlite(
            target_entity, target_year
        )

    df = st.session_state["entity_year_ledgers"][target_entity][target_year]
    st.session_state["entity_ledgers"][target_entity] = df
    return df

def set_entity_ledger(
    df: pd.DataFrame,
    entity: str | None = None,
    tax_year: str | None = None,
) -> pd.DataFrame:
    """Save a standardized DataFrame to st.session_state AND SQLite (`data/axiom_ledger.db`) isolated by Entity and Financial Year."""
    init_workspace_state()
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)

    standardized = df.reindex(columns=STANDARD_COLUMNS).copy()
    standardized["Amount"] = _clean_amount_series(standardized["Amount"])
    clean_df = standardized.reset_index(drop=True)

    st.session_state["entity_year_ledgers"].setdefault(target_entity, {})
    st.session_state["entity_year_ledgers"][target_entity][target_year] = clean_df
    st.session_state["entity_ledgers"][target_entity] = clean_df
    st.session_state.setdefault("_sqlite_loaded_ledgers", set()).add(f"{target_entity}::{target_year}")
    _save_master_ledger_to_sqlite(target_entity, target_year, clean_df)
    return clean_df

def append_entity_ledger(
    df: pd.DataFrame,
    entity: str | None = None,
    tax_year: str | None = None,
) -> pd.DataFrame:
    """Append a standardized DataFrame to the existing DataFrame for the active Entity and Financial Year and persist to SQLite."""
    init_workspace_state()
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)

    existing = get_entity_ledger(target_entity, target_year)
    incoming = df.reindex(columns=STANDARD_COLUMNS).copy()
    incoming["Amount"] = _clean_amount_series(incoming["Amount"])
    if existing.empty:
        combined = incoming.copy()
    else:
        combined = pd.concat([existing, incoming], ignore_index=True)

    return set_entity_ledger(combined, target_entity, target_year)

def is_property_unassigned(property_val: object, account_code: object = "") -> bool:
    """
    Return True if the Property column is empty/Unassigned AND the Account Code is not
    a portfolio-wide category (like Legal and Accounting or IRD Tax Payment).
    """
    if is_portfolio_wide_code(account_code):
        return False
    if pd.isna(property_val):
        return True
    cleaned = str(property_val).strip().lower()
    return cleaned in UNCATEGORIZED_MARKERS or cleaned in ("unassigned", "?", "select property", "unknown")
