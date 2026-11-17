"""
axiom_workbook
==============

Smart multi-sheet coded-workbook parser: ``Codes.xlsx``-style workbooks with
bank transaction sheets, property/setup sheets, pivot/summary sheets and the
payee-level account-code / property statistics that seed memorized rules.

Extracted from ``utils.py`` (phase D12) with a byte-identical body and
re-exported there for existing call sites.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd

from axiom_account_codes import detect_property_from_text
from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY
from axiom_dataframe import _clean_amount_series, _dedupe_column_names
from axiom_parsing import parse_xlsx_without_openpyxl
from axiom_properties import PORTFOLIO_WIDE_CATEGORIES
from axiom_state import get_active_entity


def parse_coded_workbook_sheets(
    file_bytes: bytes,
    filename: str = "Codes.xlsx",
    entity: str | None = None,
) -> dict[str, object]:
    """
    Smart Multi-Sheet Coded Workbook Parser (built specifically for `Codes.xlsx` and similar workbooks):
    Inspects every sheet in the workbook and extracts:
    1. `account_codes`: Distinct Account Codes found across all sheets (e.g. 'Materials', 'Insurance',
       'Bodycorp', 'Rent Income', 'Bank Interest', 'Rates', 'Watercare', 'James Drawings', etc.)
    2. `properties`: Distinct Properties detected across sheets & EOY statements
       (e.g. '303 Weymouth', '33 Blampied', '26 Frances', '9 Damian', '14 Raleigh', '363 Queen')
    3. `payee_code_rules`: Clean, decoupled `Payee / Pattern -> Account Code` rules (plus default Property
       ONLY when a Payee is 100% dedicated to a single property or General/Portfolio, while leaving multi-property
       contractors/insurers flexible so row-level `Analysis Code`/`Reference` determines the Property!)
    4. `sheet_summaries`: Per-sheet normalized transactions (`Date`, `Amount`, `Description`, `Property`, `Account Code`, `Payee`, `Property Hint`).
    """
    resolved_entity = get_active_entity(entity)
    ext = Path(filename or "").suffix.lower()
    raw_sheets: dict[str, pd.DataFrame] = {}

    if ext == ".xlsx":
        try:
            raw_sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None)
        except Exception:
            raw_sheets = parse_xlsx_without_openpyxl(file_bytes)
    elif ext in (".xls", ".ods"):
        try:
            raw_sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None)
        except Exception:
            raw_sheets = {}
    else:
        try:
            df_single = pd.read_csv(io.BytesIO(file_bytes), header=None)
            raw_sheets = {Path(filename).stem: df_single}
        except Exception:
            raw_sheets = {}

    discovered_codes: set[str] = set()
    discovered_properties: set[str] = set()
    # Map (payee_pattern_lower) -> {"pattern": str, "codes": Counter, "props": Counter, "sheet": str}
    from collections import Counter
    payee_stats: dict[str, dict[str, object]] = {}
    sheet_records: list[dict[str, object]] = []
    all_normalized_frames: list[pd.DataFrame] = []

    noise_codes = {
        "",
        "code",
        "account code",
        "category",
        "row labels",
        "column labels",
        "grand total",
        "total",
        "values",
        "nan",
        "none",
        "#name?",
    }

    for sheet_name, raw_df in raw_sheets.items():
        if raw_df is None or raw_df.empty:
            continue
        sh_lower = sheet_name.strip().lower()

        # Check if this is an EOY Property Manager Statement sheet (e.g. EOY Raywhite, EOY Barfoot, EOY Charlton)
        is_eoy_sheet = "eoy" in sh_lower or any(
            k in sh_lower for k in ("raywhite", "barfoot", "charlton")
        )
        if is_eoy_sheet:
            for _, r in raw_df.iterrows():
                c0 = "" if pd.isna(r.iloc[0]) else str(r.iloc[0]).strip()
                if c0:
                    det_p = detect_property_from_text(c0, entity=resolved_entity)
                    if det_p != UNASSIGNED_PROPERTY and "/" not in det_p:
                        discovered_properties.add(det_p)

        # Check if this is a Pivot Table sheet (like Sheet 1 'Pivot Table' in Codes.xlsx)
        first_cells_text = " ".join(
            str(v).strip().lower()
            for v in raw_df.iloc[:6, :4].values.flatten()
            if not pd.isna(v)
        )
        if ("row labels" in first_cells_text or "sum of amount" in first_cells_text or "pivot" in sh_lower) and not is_eoy_sheet:
            # Extract Account Codes and Property headers from the Pivot Table
            for _, r in raw_df.iterrows():
                c0 = "" if pd.isna(r.iloc[0]) else str(r.iloc[0]).strip()
                if not c0 or c0.lower() in noise_codes:
                    continue
                det_p = detect_property_from_text(c0, entity=resolved_entity)
                if det_p != UNASSIGNED_PROPERTY and ("/" not in det_p):
                    discovered_properties.add(det_p)
            sheet_records.append(
                {
                    "sheet_name": sheet_name,
                    "sheet_type": "Pivot / Summary Table",
                    "rows": 0,
                    "df": pd.DataFrame(columns=STANDARD_COLUMNS),
                }
            )
            continue

        # Find header row within first 20 rows
        header_row_idx = None
        for r_idx in range(min(20, len(raw_df))):
            row_vals = [
                "" if pd.isna(v) else str(v).strip().lower()
                for v in raw_df.iloc[r_idx].tolist()
            ]
            has_fin_col = any(
                any(k in v for k in ("amount", "debit", "credit", "other party", "payee", "details", "description", "item", "rent", "income"))
                for v in row_vals
            )
            has_date_or_code = any(
                any(k in v for k in ("code", "account code", "category", "date", "year / month", "income"))
                for v in row_vals
            )
            if has_fin_col and has_date_or_code:
                header_row_idx = r_idx
                break

        if header_row_idx is None:
            sheet_records.append(
                {
                    "sheet_name": sheet_name,
                    "sheet_type": "EOY Property Statement" if is_eoy_sheet else "Summary Sheet",
                    "rows": int(len(raw_df)),
                    "df": pd.DataFrame(columns=STANDARD_COLUMNS),
                }
            )
            continue

        headers = _dedupe_column_names(raw_df.iloc[header_row_idx].tolist())
        body_df = raw_df.iloc[header_row_idx + 1 :].copy()
        body_df.columns = headers
        body_df = body_df.reset_index(drop=True)

        # Locate columns in this sheet
        acct_col = None
        for c in headers:
            if c.strip().lower() == "code":
                acct_col = c
                break
        if acct_col is None:
            for c in headers:
                if c.strip().lower() in ("account code", "category"):
                    acct_col = c
                    break

        payee_col = None
        for cand in ("other party", "payee", "description", "details", "item"):
            for c in headers:
                if c.strip().lower() == cand:
                    payee_col = c
                    break
            if payee_col:
                break

        date_col = None
        for cand in ("date", "transaction date", "posting date"):
            for c in headers:
                if c.strip().lower() == cand:
                    date_col = c
                    break
            if date_col:
                break

        amt_col = None
        debit_col = None
        credit_col = None
        for c in headers:
            cl = c.strip().lower()
            if cl in ("amount", "total"):
                amt_col = c
            elif cl.startswith("debit") or cl in ("withdrawal", "withdrawals"):
                debit_col = c
            elif cl.startswith("credit") or cl in ("deposit", "deposits"):
                credit_col = c

        prop_hint_cols = [
            c
            for c in headers
            if c != acct_col
            and any(
                k in c.strip().lower()
                for k in ("analysis code", "reference", "particulars", "memo", "notes", "code_1")
            )
        ]

        # Detect if the entire sheet is dedicated to one property (e.g., ASB BT Prop -> 363 Queen)
        sheet_default_prop = UNASSIGNED_PROPERTY
        if "asb" in sh_lower:
            # Check if ASB sheet rows mention Apt 1604 / 363 Queen
            sample_blob = " ".join(
                str(v) for v in body_df.values.flatten() if not pd.isna(v)
            )
            det_sh_prop = detect_property_from_text(sample_blob, entity=resolved_entity)
            if det_sh_prop != UNASSIGNED_PROPERTY and "/" not in det_sh_prop:
                sheet_default_prop = det_sh_prop

        current_section_prop = sheet_default_prop
        if is_eoy_sheet:
            for pre_idx in range(min(15, len(raw_df))):
                pre_val = "" if pd.isna(raw_df.iloc[pre_idx, 0]) else str(raw_df.iloc[pre_idx, 0]).strip()
                if pre_val:
                    pre_prop = detect_property_from_text(pre_val, entity=resolved_entity)
                    if pre_prop != UNASSIGNED_PROPERTY and "/" not in pre_prop:
                        if current_section_prop == UNASSIGNED_PROPERTY:
                            current_section_prop = pre_prop
                        discovered_properties.add(pre_prop)
        sheet_tx_rows: list[dict[str, object]] = []

        for _, row in body_df.iterrows():
            # In EOY sheets, section headers appear in Col 0 (e.g. '303 Weymouth Road, Weymouth, Auckland')
            first_col_val = "" if pd.isna(row.iloc[0]) else str(row.iloc[0]).strip()
            if is_eoy_sheet and first_col_val:
                sec_prop = detect_property_from_text(first_col_val, entity=resolved_entity)
                if sec_prop != UNASSIGNED_PROPERTY and "/" not in sec_prop:
                    current_section_prop = sec_prop
                    discovered_properties.add(sec_prop)

            raw_code = "" if (acct_col is None or pd.isna(row.get(acct_col))) else str(row.get(acct_col)).strip()
            if raw_code.lower() in noise_codes:
                raw_code = ""

            # Parse amount
            row_amt = 0.0
            if debit_col and credit_col:
                d_v = _clean_amount_series(pd.Series([row.get(debit_col)])).iloc[0]
                c_v = _clean_amount_series(pd.Series([row.get(credit_col)])).iloc[0]
                row_amt = abs(float(c_v)) - abs(float(d_v))
            elif amt_col:
                row_amt = float(_clean_amount_series(pd.Series([row.get(amt_col)])).iloc[0])
            elif debit_col:
                row_amt = -abs(float(_clean_amount_series(pd.Series([row.get(debit_col)])).iloc[0]))
            elif credit_col:
                row_amt = abs(float(_clean_amount_series(pd.Series([row.get(credit_col)])).iloc[0]))

            raw_date = "" if (date_col is None or pd.isna(row.get(date_col))) else str(row.get(date_col)).strip()
            if raw_date and re.fullmatch(r"\d{5}(?:\.0)?", raw_date):
                try:
                    raw_date = (
                        pd.to_datetime(float(raw_date), unit="D", origin="1899-12-30")
                        .strftime("%Y-%m-%d")
                    )
                except Exception:
                    pass
            elif raw_date and " 00:00:00" in raw_date:
                raw_date = raw_date.replace(" 00:00:00", "").strip()
            raw_payee = "" if (payee_col is None or pd.isna(row.get(payee_col))) else str(row.get(payee_col)).strip()

            hint_parts = [
                str(row.get(hc)).strip()
                for hc in prop_hint_cols
                if not pd.isna(row.get(hc))
                and str(row.get(hc)).strip()
                and str(row.get(hc)).strip().lower() not in ("nan", "none", "#name?")
            ]
            hint_str = " | ".join(dict.fromkeys(hint_parts))

            # Skip blank or non-transaction rows
            if not raw_code and row_amt == 0.0 and not raw_payee:
                continue
            if raw_payee.lower() in ("total", "subtotal", "opening balance", "closing balance") and not raw_code:
                continue

            if raw_code and not raw_code.replace(".", "").replace("-", "").isdigit():
                discovered_codes.add(raw_code)

            # Determine row Property:
            # 1) Check hint columns (Analysis Code, Reference, Particulars, Memo)
            # 2) Check Payee text
            # 3) Fallback to current EOY section property or sheet default property
            # 4) Fallback to 'General / Portfolio' if Account Code is portfolio-wide
            row_prop = detect_property_from_text(f"{hint_str} {raw_payee}", entity=resolved_entity)
            if row_prop == UNASSIGNED_PROPERTY and current_section_prop != UNASSIGNED_PROPERTY:
                row_prop = current_section_prop
            if row_prop == UNASSIGNED_PROPERTY and raw_code.lower() in PORTFOLIO_WIDE_CATEGORIES:
                row_prop = "General / Portfolio"

            if row_prop != UNASSIGNED_PROPERTY and "/" not in row_prop:
                discovered_properties.add(row_prop)

            # Build clean description combining Payee + Property/Bank Hints
            desc_components = [p for p in (raw_payee, hint_str) if p]
            full_desc = " | ".join(desc_components) if desc_components else raw_code

            if raw_date and raw_date.replace(".", "").isdigit():
                # Convert Excel serial date (e.g., 45385 -> 2024-04-03) into readable ISO date
                try:
                    serial_val = float(raw_date)
                    if 35000 <= serial_val <= 60000:
                        dt_obj = pd.to_datetime(serial_val, unit="D", origin="1899-12-30")
                        raw_date = dt_obj.strftime("%Y-%m-%d")
                except Exception:
                    pass

            sheet_tx_rows.append(
                {
                    "Date": raw_date,
                    "Amount": float(row_amt),
                    "Description": full_desc,
                    "Property": row_prop if row_prop else UNASSIGNED_PROPERTY,
                    "Account Code": raw_code if raw_code else "Uncategorized",
                }
            )

            # Record Payee -> Account Code rule candidate
            # Use `raw_payee` as primary pattern so payee rules aren't polluted by one-off invoice numbers
            pattern_candidate = raw_payee if raw_payee else hint_str
            if (
                pattern_candidate
                and len(pattern_candidate) >= 3
                and not pattern_candidate.replace(".", "").replace("-", "").isdigit()
                and raw_code
            ):
                pat_key = pattern_candidate.lower()
                entry = payee_stats.setdefault(
                    pat_key,
                    {
                        "pattern": pattern_candidate,
                        "codes": Counter(),
                        "props": Counter(),
                        "sheet": sheet_name,
                    },
                )
                entry["codes"][raw_code] += 1
                if row_prop and row_prop != UNASSIGNED_PROPERTY:
                    entry["props"][row_prop] += 1

        sh_df = pd.DataFrame(sheet_tx_rows, columns=STANDARD_COLUMNS) if sheet_tx_rows else pd.DataFrame(columns=STANDARD_COLUMNS)
        if not sh_df.empty and not is_eoy_sheet:
            all_normalized_frames.append(sh_df)

        sheet_records.append(
            {
                "sheet_name": sheet_name,
                "sheet_type": "EOY Property Statement" if is_eoy_sheet else "Coded Bank Ledger",
                "rows": len(sh_df) if not sh_df.empty else len(body_df),
                "df": sh_df,
            }
        )

    # Consolidate Payee -> Account Code rules
    # Crucial insight: If a Payee (like '8020AON Stylecover' or '5781Kim Painter Main') is used across
    # MULTIPLE properties (via Analysis Code / Reference), its rule Property is set to `UNASSIGNED_PROPERTY`
    # ("Auto-detect from Property Column") so it never forces the wrong property onto another property's row!
    extracted_rules: list[dict[str, object]] = []
    for _pat_k, info in payee_stats.items():
        codes_counter: Counter = info["codes"]
        props_counter: Counter = info["props"]
        best_code, code_count = codes_counter.most_common(1)[0]
        total_occurrences = sum(codes_counter.values())

        distinct_real_props = [
            p for p in props_counter.keys() if p not in (UNASSIGNED_PROPERTY, "General / Portfolio")
        ]
        if best_code.lower() in PORTFOLIO_WIDE_CATEGORIES:
            rule_prop = "General / Portfolio"
            prop_mode = "Portfolio-Wide"
        elif len(distinct_real_props) == 1 and "/" not in distinct_real_props[0]:
            rule_prop = distinct_real_props[0]
            prop_mode = "Single Property"
        elif len(distinct_real_props) > 1:
            # Multi-property vendor/contractor/insurer! Keep property decoupled so row-level Analysis Code decides
            rule_prop = UNASSIGNED_PROPERTY
            prop_mode = f"Multi-Property ({len(distinct_real_props)} props — row column decides)"
        else:
            rule_prop = UNASSIGNED_PROPERTY
            prop_mode = "Auto-detect from row"

        extracted_rules.append(
            {
                "pattern": str(info["pattern"]),
                "account_code": best_code,
                "property": rule_prop,
                "property_mode": prop_mode,
                "occurrences": total_occurrences,
                "sheet": str(info["sheet"]),
            }
        )

    extracted_rules.sort(key=lambda x: (-int(x["occurrences"]), str(x["pattern"]).lower()))
    combined_bank_df = (
        pd.concat(all_normalized_frames, ignore_index=True)
        if all_normalized_frames
        else pd.DataFrame(columns=STANDARD_COLUMNS)
    )

    return {
        "account_codes": sorted(discovered_codes, key=lambda s: s.lower()),
        "properties": sorted(discovered_properties, key=lambda s: s.lower()),
        "payee_code_rules": extracted_rules,
        "sheet_summaries": sheet_records,
        "combined_bank_df": combined_bank_df,
    }
