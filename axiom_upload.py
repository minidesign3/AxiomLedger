"""
axiom_upload
============

Upload pipeline: persisting an uploaded object to the vault + SQLite (and
appending its rows to the ledger), OCR-on-upload for screenshots and importing
a parsed coded workbook into the entity's account codes, properties and ledger.

Extracted from ``utils.py`` (phase D15b) with byte-identical bodies and
re-exported there for existing call sites.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from axiom_account_codes import add_entity_account_code
from axiom_categorization import apply_rule_categorization
from axiom_core import (
    GEMINI_OCR_MODEL,
    STANDARD_COLUMNS,
    UNASSIGNED_PROPERTY,
    classify_upload_bucket,
)
from axiom_dataframe import normalize_financial_dataframe
from axiom_db import _delete_file_record_from_sqlite, _save_file_record_to_sqlite
from axiom_ingestion import get_entity_uploaded_files, sync_entity_ledger_from_files
from axiom_ledger import append_entity_ledger, get_entity_ledger, set_entity_ledger
from axiom_ocr import extract_screenshot_structured_gemini, save_screenshot_ocr_markdown
from axiom_parsing import (
    _read_bank_csv_bytes,
    extract_other_document_tables,
    extract_pdf_tables,
)
from axiom_properties import add_entity_property
from axiom_rules import save_memorized_rule
from axiom_state import get_active_entity, get_active_tax_year
from axiom_vault import _save_raw_file_to_vault
from axiom_workbook import parse_coded_workbook_sheets


def run_ocr_on_screenshot_record(
    file_rec: dict[str, object],
    entity: str | None = None,
    tax_year: str | None = None,
) -> tuple[bool, str, int]:
    """
    Run Gemini Vision OCR on an existing screenshot record in `files_list`,
    update its DataFrame in memory + SQLite, and sync the entity master ledger.
    Returns `(ok, message, extracted_row_count)`.
    """
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    fname = str(file_rec.get("name", "Screenshot.png"))
    api_key = st.session_state.get("google_genai_api_key", "").strip()
    if not api_key:
        return False, "No Gemini API Key configured in sidebar settings.", 0

    raw_bytes = file_rec.get("raw_bytes")
    if not isinstance(raw_bytes, (bytes, bytearray)):
        vpath = str(file_rec.get("vault_path", "") or "")
        if vpath:
            vp = Path(vpath)
            if not vp.is_absolute():
                vp = Path(__file__).resolve().parent / vp
            if vp.exists() and vp.is_file():
                raw_bytes = vp.read_bytes()
                file_rec["raw_bytes"] = raw_bytes
    if not isinstance(raw_bytes, (bytes, bytearray)):
        return False, f"Could not load image bytes for '{fname}'.", 0

    mime_type = str(file_rec.get("mime_type") or "") or (
        "image/png" if fname.lower().endswith(".png") else "image/jpeg"
    )
    try:
        ocr_res = extract_screenshot_structured_gemini(
            image_bytes=bytes(raw_bytes),
            mime_type=mime_type,
            api_key=api_key,
            run_dynamic_ai_fallback=True,
        )
        ocr_df = ocr_res.get("df")
        if not isinstance(ocr_df, pd.DataFrame):
            ocr_df = pd.DataFrame(columns=STANDARD_COLUMNS)

        if not ocr_df.empty:
            norm_df = (
                ocr_df.copy()
                if list(ocr_df.columns) == STANDARD_COLUMNS
                else normalize_financial_dataframe(ocr_df)
            )
            cat_df = apply_rule_categorization(norm_df, entity=target_entity)
            file_rec["df"] = cat_df
            file_rec["needs_ocr"] = False
            md_path = save_screenshot_ocr_markdown(file_rec, target_entity, target_year, ocr_meta=ocr_res)
            file_rec["ocr_md_path"] = md_path
            file_rec["note"] = f"OCR complete ({len(cat_df)} rows) · Saved .ocr.md"
            _save_file_record_to_sqlite(target_entity, target_year, file_rec)
            sync_entity_ledger_from_files(target_entity, target_year)
            return True, f"Extracted {len(cat_df)} transaction(s) from '{fname}' and saved .ocr.md.", len(cat_df)
        else:
            file_rec["needs_ocr"] = False
            summary_note = str(ocr_res.get("notes") or "").strip()
            file_rec["note"] = (
                f"{summary_note} · Saved .ocr.md"
                if summary_note
                else "Account/Loan summary screenshot · Saved .ocr.md"
            )
            md_path = save_screenshot_ocr_markdown(file_rec, target_entity, target_year, ocr_meta=ocr_res)
            file_rec["ocr_md_path"] = md_path
            _save_file_record_to_sqlite(target_entity, target_year, file_rec)
            sync_entity_ledger_from_files(target_entity, target_year)
            return True, f"Scanned '{fname}' and saved .ocr.md summary.", 0
    except Exception as exc:
        file_rec["needs_ocr"] = True
        file_rec["note"] = f"OCR error: {str(exc)[:80]}"
        _save_file_record_to_sqlite(target_entity, target_year, file_rec)
        return False, f"OCR failed on '{fname}': {exc}", 0

def ingest_uploaded_file_object(
    uploaded_file,
    entity: str | None = None,
    tax_year: str | None = None,
    forced_bucket: str | None = None,
    run_ocr_immediately: bool = True,
) -> tuple[bool, str, dict[str, object] | None]:
    """
    Parse an uploaded file (CSV, PDF, Screenshot, or Other [Excel/ODS/ODT]),
    save the original file into `data/vault/<Entity>/<Year>/<Bucket>/`,
    normalize its columns, apply Memorized Rules + Keyword Rules, store it in SQLite
    and the 4-column file board for `(entity, tax_year)`, and sync the master ledger.
    """
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    fname = getattr(uploaded_file, "name", "Uploaded_File")
    fbytes = uploaded_file.getvalue()
    bucket = forced_bucket or classify_upload_bucket(fname)

    files_list = get_entity_uploaded_files(target_entity, target_year)
    # Check if already ingested with identical name + byte size in SQLite / session
    file_sig = f"{fname}::{len(fbytes)}"
    for existing in files_list:
        if existing.get("signature") == file_sig or str(existing.get("name", "")).strip() == fname:
            existing_df = existing.get("df")
            # If previously failed CSV parse (0 rows), allow re-parsing now
            if (
                bucket == "csv"
                and (not isinstance(existing_df, pd.DataFrame) or existing_df.empty)
                and "parse warning" in str(existing.get("note", "")).lower()
            ):
                files_list.remove(existing)
                _delete_file_record_from_sqlite(str(existing.get("id", "")), delete_vault_file=False)
                break
            if bucket == "screenshots" and run_ocr_immediately and existing.get("needs_ocr"):
                run_ocr_on_screenshot_record(existing, entity=target_entity, tax_year=target_year)
                return True, f"Updated OCR for '{fname}'.", existing
            return True, f"'{fname}' is already saved in the {bucket.upper()} vault.", existing

    # Save raw file bytes to organized local File Vault on disk (`data/vault/<Company>/<Year>/<Bucket>/<Filename>`)
    vault_path = _save_raw_file_to_vault(target_entity, target_year, bucket, fname, fbytes)

    raw_df = pd.DataFrame()
    parse_note = ""
    needs_ocr = False
    ocr_meta: dict[str, object] | None = None

    try:
        if bucket == "csv":
            raw_df = _read_bank_csv_bytes(fbytes)
        elif bucket == "pdf":
            raw_df = extract_pdf_tables(fbytes)
            if raw_df.empty:
                parse_note = "Saved PDF to Vault. No structured table found automatically."
        elif bucket == "screenshots":
            has_api_key = bool(st.session_state.get("google_genai_api_key", "").strip())
            mime_type = getattr(uploaded_file, "type", None) or (
                "image/png" if fname.lower().endswith(".png") else "image/jpeg"
            )
            if run_ocr_immediately and has_api_key:
                try:
                    ocr_meta = extract_screenshot_structured_gemini(
                        image_bytes=fbytes,
                        mime_type=mime_type,
                        api_key=st.session_state.get("google_genai_api_key", ""),
                        run_dynamic_ai_fallback=True,
                    )
                    maybe_df = ocr_meta.get("df")
                    raw_df = maybe_df if isinstance(maybe_df, pd.DataFrame) else pd.DataFrame()
                    if raw_df.empty:
                        summary_note = str(ocr_meta.get("notes") or "").strip()
                        parse_note = (
                            f"{summary_note} · Saved .ocr.md"
                            if summary_note
                            else "Account/Loan summary screenshot · Saved .ocr.md"
                        )
                    else:
                        parse_note = f"Auto-OCR ({GEMINI_OCR_MODEL}) · Saved .ocr.md"
                except Exception as ocr_exc:
                    needs_ocr = True
                    parse_note = f"OCR pending ({str(ocr_exc)[:60]}). Click '🔍 Run OCR' or open file."
            else:
                needs_ocr = True
                parse_note = (
                    "Saved to Vault — click '🔍 Run OCR on Screenshots' above or open file to extract rows."
                    if has_api_key
                    else "Saved to Vault — open file to run OCR or code receipt row."
                )
        else:
            raw_df = extract_other_document_tables(fbytes, fname)
    except Exception as exc:
        parse_note = f"Saved to Vault (parse warning: {exc})"

    if not raw_df.empty:
        # If already standardized (e.g. from OCR), keep columns, else normalize
        if list(raw_df.columns) == STANDARD_COLUMNS:
            norm_df = raw_df.copy()
        else:
            norm_df = normalize_financial_dataframe(raw_df)
        categorized_df = apply_rule_categorization(norm_df, entity=target_entity)
    else:
        if bucket == "screenshots":
            # Provide a ready-to-edit 1-row template for a receipt screenshot so user can code it immediately
            categorized_df = pd.DataFrame(
                [
                    {
                        "Date": datetime.now().strftime("%Y-%m-%d"),
                        "Amount": 0.0,
                        "Description": Path(fname).stem.replace("_", " ").replace("-", " "),
                        "Property": UNASSIGNED_PROPERTY,
                        "Account Code": "Uncategorized",
                    }
                ]
            )[STANDARD_COLUMNS]
            categorized_df = apply_rule_categorization(categorized_df, entity=target_entity)
        else:
            categorized_df = pd.DataFrame(columns=STANDARD_COLUMNS)

    file_id = f"file_{int(time.time() * 1000)}_{len(files_list) + 1}"
    record: dict[str, object] = {
        "id": file_id,
        "signature": file_sig,
        "name": fname,
        "bucket": bucket,
        "vault_path": vault_path,
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "df": categorized_df,
        "raw_bytes": fbytes if bucket == "screenshots" else None,
        "mime_type": getattr(uploaded_file, "type", "image/png") if bucket == "screenshots" else "",
        "needs_ocr": needs_ocr,
        "note": parse_note,
    }
    if bucket == "screenshots" and ocr_meta is not None:
        md_path = save_screenshot_ocr_markdown(record, target_entity, target_year, ocr_meta=ocr_meta)
        record["ocr_md_path"] = md_path
    files_list.append(record)
    _save_file_record_to_sqlite(target_entity, target_year, record)
    sync_entity_ledger_from_files(target_entity, target_year)
    return True, f"Saved '{fname}' to Vault & SQLite ({len(categorized_df):,} rows).", record

def import_codes_workbook_into_entity(
    file_bytes: bytes,
    filename: str = "Codes.xlsx",
    entity: str | None = None,
    tax_year: str | None = None,
    import_bank_transactions: bool = False,
) -> dict[str, object]:
    """
    One-click importer for `Codes.xlsx` (or any multi-sheet coded workbook):
    1. Registers all discovered Account Codes into the entity's Chart of Accounts.
    2. Registers all discovered Properties (and their bank shorthand aliases) into the entity's Properties list.
    3. Saves all decoupled Payee -> Account Code rules into Memorized Rules.
    4. Re-applies the updated Account Code & Property rules across all currently loaded files/ledgers.
    5. Optionally imports the coded bank transaction sheets (`Westpac - BT Prop`, `ASB BT Prop`) into the active year ledger.
    """
    resolved_entity = get_active_entity(entity)
    resolved_year = get_active_tax_year(tax_year)
    parsed = parse_coded_workbook_sheets(file_bytes, filename=filename, entity=resolved_entity)

    added_codes = 0
    for code_name in parsed["account_codes"]:
        ok, _ = add_entity_account_code(code_name, entity=resolved_entity)
        if ok:
            added_codes += 1

    added_props = 0
    for prop_name in parsed["properties"]:
        ok, _ = add_entity_property(prop_name, entity=resolved_entity)
        if ok:
            added_props += 1

    saved_rules = 0
    for rule in parsed["payee_code_rules"]:
        ok, _ = save_memorized_rule(
            pattern=str(rule["pattern"]),
            property_name=str(rule["property"]),
            account_code=str(rule["account_code"]),
            entity=resolved_entity,
            source=f"Imported ({filename})",
        )
        if ok:
            saved_rules += 1

    # Re-apply rules to existing files and active ledger
    files_list = get_entity_uploaded_files(resolved_entity, resolved_year)
    for f_rec in files_list:
        f_df = f_rec.get("df")
        if isinstance(f_df, pd.DataFrame) and not f_df.empty:
            f_rec["df"] = apply_rule_categorization(f_df, overwrite_existing=False, entity=resolved_entity)
    if files_list:
        sync_entity_ledger_from_files(resolved_entity, resolved_year)
    else:
        curr_ledger = get_entity_ledger(resolved_entity, resolved_year)
        if not curr_ledger.empty:
            set_entity_ledger(
                apply_rule_categorization(curr_ledger, overwrite_existing=False, entity=resolved_entity),
                resolved_entity,
                resolved_year,
            )

    imported_tx_count = 0
    if import_bank_transactions:
        bank_df: pd.DataFrame = parsed["combined_bank_df"]
        if isinstance(bank_df, pd.DataFrame) and not bank_df.empty:
            append_entity_ledger(bank_df, entity=resolved_entity, tax_year=resolved_year)
            imported_tx_count = len(bank_df)

    return {
        "total_codes": len(parsed["account_codes"]),
        "added_codes": added_codes,
        "total_properties": len(parsed["properties"]),
        "added_properties": added_props,
        "saved_rules": saved_rules,
        "imported_tx_count": imported_tx_count,
        "parsed": parsed,
    }
