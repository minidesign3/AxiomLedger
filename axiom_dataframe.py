"""
axiom_dataframe
===============

Streamlit-free ledger DataFrame normalisation and export: the smart
header/column mapper (``normalize_financial_dataframe``), the rule
categorization pass (``standardize_ledger_dataframe``), currency cleaning,
column de-duping and the Xero-ready master CSV export.

Extracted from ``utils.py`` (phase D10) with byte-identical bodies and
re-exported there for existing call sites.
"""

from __future__ import annotations

import re

import pandas as pd

from axiom_account_codes import detect_property_from_text
from axiom_categorization import apply_rule_categorization
from axiom_core import (
    DEFAULT_CUSTOM_ACCOUNT_CODES,
    ENTITY_CATEGORIZATION_RULES,
    STANDARD_COLUMNS,
    UNASSIGNED_PROPERTY,
)


def export_xero_master_csv(df: pd.DataFrame, jurisdiction: str | None = None) -> bytes:
    """
    Format the standardized DataFrame as a Xero-ready CSV:
    - UTF-8 encoded
    - Strictly no commas in amounts (plain decimal numbers, e.g. 1250.50 or -450.00)
    - With ``jurisdiction``: every Date in one format (NZ dd/mm/yyyy, US mm/dd/yyyy)
    """
    if jurisdiction and df is not None and not df.empty:
        from axiom_dates import normalize_date

        df = df.copy()
        df["Date"] = [normalize_date(v, jurisdiction)[0] for v in df["Date"]]
    if df is None or df.empty:
        empty_df = pd.DataFrame(columns=STANDARD_COLUMNS)
        return empty_df.to_csv(index=False, encoding="utf-8").encode("utf-8")

    export_df = df.reindex(columns=STANDARD_COLUMNS).copy()
    numeric_amounts = _clean_amount_series(export_df["Amount"])
    export_df["Amount"] = numeric_amounts.map(lambda x: f"{float(x):.2f}")
    export_df["Date"] = export_df["Date"].fillna("").astype(str).str.strip()
    export_df["Description"] = export_df["Description"].fillna("").astype(str).str.strip()
    export_df["Property"] = (
        export_df["Property"]
        .fillna(UNASSIGNED_PROPERTY)
        .astype(str)
        .str.strip()
        .replace("", UNASSIGNED_PROPERTY)
    )
    export_df["Account Code"] = (
        export_df["Account Code"]
        .fillna("Uncategorized")
        .astype(str)
        .str.strip()
        .replace("", "Uncategorized")
    )
    return export_df.to_csv(index=False, encoding="utf-8").encode("utf-8")

def _clean_amount_series(series: pd.Series) -> pd.Series:
    """Normalize currency/amount strings (including parentheses negatives) into floats."""
    def _parse_val(val):
        if pd.isna(val):
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        text = str(val).strip()
        if not text:
            return 0.0
        is_negative = False
        if text.startswith("(") and text.endswith(")"):
            is_negative = True
            text = text[1:-1]
        elif text.endswith("-"):
            is_negative = True
            text = text[:-1]
        cleaned = re.sub(r"[^0-9.\-]", "", text)
        if cleaned in ("", "-", ".", "-."):
            return 0.0
        try:
            num = float(cleaned)
            return -abs(num) if is_negative else num
        except ValueError:
            return 0.0

    return series.apply(_parse_val)

def _dedupe_column_names(raw_cols: list[object]) -> list[str]:
    """Convert raw header cell values into stripped, deduplicated string column names."""
    deduped: list[str] = []
    seen: dict[str, int] = {}
    for idx, val in enumerate(raw_cols):
        name = "" if pd.isna(val) else str(val).strip()
        if not name or name.lower() in ("nan", "none"):
            name = f"Column_{idx}"
        if name in seen:
            seen[name] += 1
            deduped.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            deduped.append(name)
    return deduped

def normalize_financial_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 4.6 Smart Column Mapper:
    Takes a raw pandas DataFrame (from CSV or PDF extraction) and standardizes it
    to strictly four columns: ['Date', 'Amount', 'Description', 'Account Code'].

    1. Header Detection & Junk Removal:
       - Scans the first 20 rows of the DataFrame to find the 'True Header' row
         (a row containing strings like 'date', 'amount', or 'debit' ignoring case),
         unless the current DataFrame columns already represent the True Header.
       - Drops all rows above the True Header and resets the columns to those values.
    2. Column Mapping Logic:
       - Date: First column named 'date', 'posting date', 'transaction date', or 'process date'
         (case-insensitive) -> renamed to 'Date'.
       - Amount: Single 'amount' column -> renamed to 'Amount'. If separate 'debit' and 'credit'
         columns exist, combines them into 'Amount' (Credit - Debit where income is positive and
         expenses are negative) and drops the original debit/credit columns.
       - Description: Finds all existing columns from
         ['payee', 'particulars', 'code', 'reference', 'description', 'details'] (case-insensitive)
         and concatenates their non-empty string values into 'Description', separated by a space.
       - Account Code: Adds an empty 'Account Code' column if it doesn't exist.
       - Drops all other unmapped columns so the final DataFrame is strictly:
         ['Date', 'Amount', 'Description', 'Account Code'].
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    work_df = df.copy()
    header_keywords = ("date", "amount", "debit")

    def _header_score(values: list[object]) -> int:
        tokens = [
            "" if pd.isna(v) else str(v).strip().lower()
            for v in values
        ]
        meaningful_tokens = [
            t for t in tokens
            if t and not t.startswith("unnamed:") and not re.match(r"^column_\d+$", t) and not t.isdigit()
        ]
        if not meaningful_tokens:
            return 0
        joined = " ".join(meaningful_tokens)
        if not any(kw in joined for kw in header_keywords):
            return 0
        known_header_terms = {
            "date",
            "posting date",
            "transaction date",
            "process date",
            "amount",
            "debit",
            "credit",
            "payee",
            "particulars",
            "code",
            "reference",
            "description",
            "details",
            "account code",
            "balance",
        }
        exact_matches = sum(1 for t in meaningful_tokens if t in known_header_terms)
        kw_matches = sum(1 for kw in header_keywords if kw in joined)
        return exact_matches * 3 + kw_matches

    # Scan the first 20 rows for the True Header row (if a row in the first 20 rows scores higher
    # than the initial DataFrame column labels, drop all rows above the True Header and reset columns).
    cols_score = _header_score(list(work_df.columns))
    scan_limit = min(20, len(work_df))
    true_header_pos: int | None = None
    best_row_score = cols_score

    for pos in range(scan_limit):
        row_values = list(work_df.iloc[pos].values)
        r_score = _header_score(row_values)
        if r_score > best_row_score:
            best_row_score = r_score
            true_header_pos = pos
            break

    if true_header_pos is not None:
        header_values = list(work_df.iloc[true_header_pos].values)
        work_df = work_df.iloc[true_header_pos + 1 :].reset_index(drop=True)
        work_df.columns = _dedupe_column_names(header_values)
    else:
        work_df.columns = _dedupe_column_names(list(work_df.columns))

    if work_df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    # Map lowercase stripped column name -> list of actual column names (preserving order)
    col_lookup: dict[str, list[str]] = {}
    for col in work_df.columns:
        key = str(col).strip().lower()
        col_lookup.setdefault(key, []).append(col)

    def _find_first_col(candidates: list[str]) -> str | None:
        # 1. Exact case-insensitive match in candidate priority order
        for cand in candidates:
            if cand in col_lookup and col_lookup[cand]:
                return col_lookup[cand][0]
        # 2. Substring fallback in column order
        for col in work_df.columns:
            col_lower = str(col).strip().lower()
            if any(cand in col_lower for cand in candidates):
                return col
        return None

    # 1. Date Mapping
    date_candidates = ["date", "posting date", "transaction date", "process date"]
    date_src_col = _find_first_col(date_candidates)
    if date_src_col is not None:
        if date_src_col != "Date":
            work_df = work_df.rename(columns={date_src_col: "Date"})
        date_series = work_df["Date"].fillna("").astype(str).str.strip()
        date_series = date_series.replace({"nan": "", "None": "", "NaT": ""})
    else:
        date_series = pd.Series([""] * len(work_df), index=work_df.index, dtype="object")

    # 2. Amount Mapping (Single Amount vs Separate Debit & Credit)
    debit_col = _find_first_col(["debit", "debits", "debit amount", "withdrawal", "withdrawals", "money out", "dr"])
    credit_col = _find_first_col(["credit", "credits", "credit amount", "deposit", "deposits", "money in", "cr"])
    amount_col = _find_first_col(["amount", "transaction amount", "net amount", "amt", "value", "total"])

    if debit_col is not None and credit_col is not None and debit_col != credit_col:
        debit_vals = _clean_amount_series(work_df[debit_col]).abs()
        credit_vals = _clean_amount_series(work_df[credit_col]).abs()
        amount_series = credit_vals - debit_vals
        cols_to_drop = [c for c in (debit_col, credit_col) if c in work_df.columns]
        work_df = work_df.drop(columns=cols_to_drop, errors="ignore")
    elif amount_col is not None:
        if amount_col != "Amount":
            work_df = work_df.rename(columns={amount_col: "Amount"})
        amount_series = _clean_amount_series(work_df["Amount"])
    elif debit_col is not None:
        debit_vals = _clean_amount_series(work_df[debit_col]).abs()
        amount_series = -debit_vals
        work_df = work_df.drop(columns=[debit_col], errors="ignore")
    elif credit_col is not None:
        credit_vals = _clean_amount_series(work_df[credit_col]).abs()
        amount_series = credit_vals
        work_df = work_df.drop(columns=[credit_col], errors="ignore")
    else:
        amount_series = pd.Series([0.0] * len(work_df), index=work_df.index, dtype="float64")

    # 3. Account Code Mapping:
    # Check explicit 'Account Code' / 'Category' column first.
    # Also check if a column named 'Code' (or 'Code_1') actually holds Accountant Codes
    # (as in `Codes.xlsx` -> `Westpac - BT Prop` Col A and `ASB BT Prop` Col D), rather than bank transaction codes.
    acct_src_col = None
    for col in work_df.columns:
        if str(col).strip().lower() in ("account code", "account_code", "acct code", "gl code", "category", "code_category"):
            acct_src_col = col
            break

    known_acct_codes_lower = {
        c.lower()
        for c in (
            list(DEFAULT_CUSTOM_ACCOUNT_CODES)
            + list(ENTITY_CATEGORIZATION_RULES.get("NZ", {}).values())
            + list(ENTITY_CATEGORIZATION_RULES.get("US", {}).values())
        )
    }
    if acct_src_col is None:
        for col in work_df.columns:
            c_low = str(col).strip().lower()
            if c_low == "code" or c_low.startswith("code_"):
                non_empty_vals = [
                    str(v).strip()
                    for v in work_df[col].dropna().tolist()
                    if str(v).strip() and str(v).strip().lower() not in ("nan", "none", "null")
                ]
                if non_empty_vals:
                    matches = sum(1 for v in non_empty_vals if v.lower() in known_acct_codes_lower)
                    if matches / len(non_empty_vals) >= 0.35:
                        acct_src_col = col
                        break

    if acct_src_col is not None:
        acct_series = (
            work_df[acct_src_col]
            .fillna("")
            .astype(str)
            .str.strip()
            .replace({"nan": "", "None": "", "null": ""})
        )
    else:
        acct_series = pd.Series([""] * len(work_df), index=work_df.index, dtype="object")

    # 4. Description Mapping: concatenate Payee / Other Party + Particulars + Reference + Analysis Code + Memo
    # PLUS any other text columns across the entire CSV row (excluding Date, Amount, Balance, and Account Code)
    # so that if a house name (e.g. 'Damian') or policy/vendor text appears in ANY column of the CSV line,
    # it is preserved and available to the matching engine.
    desc_target_names = [
        "other party",
        "payee",
        "particulars",
        "code",
        "reference",
        "analysis code",
        "description",
        "details",
        "memo",
        "notes",
        "tran type",
        "narrative",
        "merchant",
        "transaction details",
        "serial",
        "orig ref",
        "bank",
    ]
    matched_desc_cols: list[str] = []

    for target_name in desc_target_names:
        for col in work_df.columns:
            if col == acct_src_col:
                continue
            c_low = str(col).strip().lower()
            if col not in matched_desc_cols and (c_low == target_name or c_low.startswith(f"{target_name}_")):
                matched_desc_cols.append(col)

    # Also include any remaining non-date/non-amount/non-balance text columns across the CSV line
    excluded_desc_cols = {
        "Date",
        "Amount",
        date_src_col,
        amount_col,
        debit_col,
        credit_col,
        acct_src_col,
    }
    for col in work_df.columns:
        if col in matched_desc_cols or col in excluded_desc_cols:
            continue
        c_low = str(col).strip().lower()
        if any(skip_k in c_low for skip_k in ("balance", "running bal", "date", "amount", "debit", "credit")):
            continue
        matched_desc_cols.append(col)

    if matched_desc_cols:
        def _concat_desc_row(row: pd.Series) -> str:
            parts: list[str] = []
            for c in matched_desc_cols:
                val = row.get(c, "")
                if pd.isna(val):
                    continue
                s_val = str(val).strip()
                if s_val and s_val.lower() not in ("nan", "none", "null", "#name?"):
                    if s_val not in parts:
                        parts.append(s_val)
            return " | ".join(parts) if len(parts) > 1 else (" ".join(parts))

        description_series = work_df[matched_desc_cols].apply(_concat_desc_row, axis=1)
    else:
        description_series = pd.Series([""] * len(work_df), index=work_df.index, dtype="object")

    # 5. Property Mapping:
    # First check explicit Property / Address column; then scan EVERY text column across the entire CSV line
    # via `detect_property_from_text()` so row-level property notes (e.g. 'Damian', '26 Frances', '33 Blampied', '16D 363Queen')
    # anywhere on the line are automatically assigned to the Property column!
    prop_src_col = None
    for col in work_df.columns:
        if str(col).strip().lower() in ("property", "property name", "address", "property address", "unit", "building", "asset"):
            prop_src_col = col
            break

    prop_hint_cols = [
        col
        for col in work_df.columns
        if col not in excluded_desc_cols
        and not any(skip_k in str(col).strip().lower() for skip_k in ("balance", "running bal", "date", "amount", "debit", "credit"))
    ]

    def _resolve_row_property(idx_val) -> str:
        if prop_src_col is not None:
            raw_p = work_df.loc[idx_val, prop_src_col]
            if not pd.isna(raw_p):
                s_p = str(raw_p).strip()
                if s_p and s_p.lower() not in ("nan", "none", "null", "unassigned"):
                    det = detect_property_from_text(s_p)
                    return det if det != UNASSIGNED_PROPERTY else s_p
        # Scan the entire CSV row across all text/reference columns
        combined_hints = " | ".join(
            str(work_df.loc[idx_val, c] or "").strip()
            for c in prop_hint_cols
            if not pd.isna(work_df.loc[idx_val, c]) and str(work_df.loc[idx_val, c] or "").strip()
        )
        detected = detect_property_from_text(combined_hints)
        if detected != UNASSIGNED_PROPERTY:
            return detected
        return UNASSIGNED_PROPERTY

    prop_series = pd.Series([_resolve_row_property(i) for i in work_df.index], index=work_df.index, dtype="object")

    # Build strict 5-column DataFrame and drop all other unmapped columns
    normalized = pd.DataFrame(
        {
            "Date": date_series,
            "Amount": amount_series.astype(float),
            "Description": description_series,
            "Property": prop_series,
            "Account Code": acct_series,
        },
        index=work_df.index,
    )[STANDARD_COLUMNS]

    # Filter out completely empty rows or repeated mid-document table header rows
    def _is_valid_transaction_row(row: pd.Series) -> bool:
        d_val = str(row["Date"]).strip()
        desc_val = str(row["Description"]).strip()
        amt_val = float(row["Amount"]) if not pd.isna(row["Amount"]) else 0.0
        if d_val == "" and desc_val == "" and amt_val == 0.0:
            return False
        if d_val.lower() in ("date", "posting date", "transaction date", "process date") and amt_val == 0.0:
            return False
        return True

    valid_mask = normalized.apply(_is_valid_transaction_row, axis=1)
    normalized = normalized[valid_mask].reset_index(drop=True)
    return normalized[STANDARD_COLUMNS]

def standardize_ledger_dataframe(
    raw_df: pd.DataFrame,
    column_mapping: dict[str, str | None] | None = None,
) -> pd.DataFrame:
    """
    Standardize an arbitrary DataFrame to strictly:
    ['Date', 'Amount', 'Description', 'Account Code']
    using normalize_financial_dataframe() (with optional manual column override support)
    and automatically apply CATEGORIZATION_RULES to populate 'Account Code'.
    """
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    has_custom_override = bool(
        column_mapping and any(v is not None for v in column_mapping.values())
    )
    if not has_custom_override:
        normalized = normalize_financial_dataframe(raw_df)
        return apply_rule_categorization(normalized[STANDARD_COLUMNS])

    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    rename_overrides: dict[str, str] = {}
    for target_col, src_col in (column_mapping or {}).items():
        if src_col and src_col in df.columns and src_col != target_col:
            rename_overrides[src_col] = target_col
    if rename_overrides:
        df = df.rename(columns=rename_overrides)

    normalized = normalize_financial_dataframe(df)
    return apply_rule_categorization(normalized[STANDARD_COLUMNS])
