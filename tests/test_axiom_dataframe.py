"""Tests for the ledger DataFrame normalisation / export module (phase D10).

The property-detection helper is stubbed for every test (except the explicit
hint-scanning cases) so the suite never touches the real memory store.
"""

from __future__ import annotations

import pandas as pd
import pytest

import axiom_dataframe
from axiom_core import (
    ENTITY_CATEGORIZATION_RULES,
    STANDARD_COLUMNS,
    UNASSIGNED_PROPERTY,
)


@pytest.fixture(autouse=True)
def _stub_property_detection(monkeypatch):
    monkeypatch.setattr(
        axiom_dataframe, "detect_property_from_text", lambda text: UNASSIGNED_PROPERTY
    )


def _raw(rows: list[list[object]]) -> pd.DataFrame:
    """Build the raw DataFrame shape produced by CSV/XLSX readers (integer columns)."""
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# _clean_amount_series
# --------------------------------------------------------------------------- #
def test_clean_amount_series_handles_currency_and_negatives():
    series = pd.Series([1250, -12.5, "$1,234.56", "(45.00)", "45.00-", "", None, "-", "abc"])
    cleaned = axiom_dataframe._clean_amount_series(series).tolist()
    assert cleaned[0] == 1250.0
    assert cleaned[1] == -12.5
    assert cleaned[2] == 1234.56
    assert cleaned[3] == -45.0
    assert cleaned[4] == -45.0
    assert cleaned[5:8] == [0.0, 0.0, 0.0]
    assert cleaned[8] == 0.0


def test_clean_amount_series_returns_floats_for_nan():
    cleaned = axiom_dataframe._clean_amount_series(pd.Series([float("nan")]))
    assert cleaned.tolist() == [0.0]


# --------------------------------------------------------------------------- #
# _dedupe_column_names
# --------------------------------------------------------------------------- #
def test_dedupe_column_names_fills_blanks_and_duplicates():
    names = axiom_dataframe._dedupe_column_names(
        ["Date", "Amount", float("nan"), "Amount", "None", "date"]
    )
    assert names == ["Date", "Amount", "Column_2", "Amount_1", "Column_4", "date"]


# --------------------------------------------------------------------------- #
# normalize_financial_dataframe
# --------------------------------------------------------------------------- #
def test_normalize_empty_frame_returns_standard_columns():
    out = axiom_dataframe.normalize_financial_dataframe(pd.DataFrame())
    assert list(out.columns) == STANDARD_COLUMNS
    assert out.empty


def test_normalize_detects_header_below_preamble_rows():
    raw = _raw(
        [
            ["Westpac Statement", None, None, None],
            ["Account 12-3456", None, None, None],
            ["Date", "Amount", "Payee", "Particulars"],
            ["2026-01-05", "-12.50", "Countdown", "Groceries"],
        ]
    )
    out = axiom_dataframe.normalize_financial_dataframe(raw)
    assert list(out.columns) == STANDARD_COLUMNS
    assert len(out) == 1
    assert out.loc[0, "Date"] == "2026-01-05"
    assert out.loc[0, "Amount"] == -12.5
    assert out.loc[0, "Description"] == "Countdown | Groceries"
    assert out.loc[0, "Account Code"] == ""


def test_normalize_combines_debit_and_credit_columns():
    raw = _raw(
        [
            ["Date", "Debit", "Credit", "Description"],
            ["2026-02-01", "100.00", None, "Rates"],
            ["2026-02-02", None, "250.00", "Rent Income"],
        ]
    )
    out = axiom_dataframe.normalize_financial_dataframe(raw)
    assert out["Amount"].tolist() == [-100.0, 250.0]


def test_normalize_uses_explicit_account_code_column():
    raw = _raw(
        [
            ["Date", "Amount", "Description", "Account Code"],
            ["2026-03-01", "10.00", "Power", "Utilities"],
        ]
    )
    out = axiom_dataframe.normalize_financial_dataframe(raw)
    assert out.loc[0, "Account Code"] == "Utilities"


def test_normalize_detects_code_column_holding_accountant_codes():
    known_code = next(iter(ENTITY_CATEGORIZATION_RULES["NZ"].values()))
    raw = _raw(
        [
            ["Date", "Amount", "Code", "Description"],
            ["2026-04-01", "10.00", known_code, "Something"],
            ["2026-04-02", "20.00", known_code.upper(), "Something else"],
        ]
    )
    out = axiom_dataframe.normalize_financial_dataframe(raw)
    assert out["Account Code"].tolist() == [known_code, known_code.upper()]
    # The accountant-code column must not leak into the description.
    assert "|" not in out.loc[0, "Description"]


def test_normalize_keeps_bank_reference_codes_out_of_account_code():
    raw = _raw(
        [
            ["Date", "Amount", "Code", "Description"],
            ["2026-04-01", "10.00", "TFR-9912", "Transfer"],
        ]
    )
    out = axiom_dataframe.normalize_financial_dataframe(raw)
    assert out.loc[0, "Account Code"] == ""
    assert out.loc[0, "Description"] == "TFR-9912 | Transfer"


def test_normalize_drops_blank_rows_and_repeated_header_rows():
    raw = _raw(
        [
            ["Date", "Amount", "Description"],
            ["2026-05-01", "11.00", "One"],
            [None, None, None],
            ["Date", "0", "Description"],
            ["2026-05-02", "22.00", "Two"],
        ]
    )
    out = axiom_dataframe.normalize_financial_dataframe(raw)
    assert out["Description"].tolist() == ["One", "Two"]


def test_normalize_prefers_explicit_property_column(monkeypatch):
    monkeypatch.setattr(
        axiom_dataframe,
        "detect_property_from_text",
        lambda text: "33 Blampied" if "blampied" in text.lower() else UNASSIGNED_PROPERTY,
    )
    raw = _raw(
        [
            ["Date", "Amount", "Property", "Description"],
            ["2026-06-01", "10.00", "33 Blampied Rd", "Rates"],
            ["2026-06-02", "20.00", "26 Frances", "Insurance"],
        ]
    )
    out = axiom_dataframe.normalize_financial_dataframe(raw)
    assert out["Property"].tolist() == ["33 Blampied", "26 Frances"]


def test_normalize_scans_all_text_columns_for_property_hint(monkeypatch):
    monkeypatch.setattr(
        axiom_dataframe,
        "detect_property_from_text",
        lambda text: "Damian" if "damian" in text.lower() else UNASSIGNED_PROPERTY,
    )
    raw = _raw(
        [
            ["Date", "Amount", "Particulars", "Memo"],
            ["2026-07-01", "10.00", "Power", "Damian flat"],
            ["2026-07-02", "20.00", "Water", "Main house"],
        ]
    )
    out = axiom_dataframe.normalize_financial_dataframe(raw)
    assert out["Property"].tolist() == ["Damian", UNASSIGNED_PROPERTY]


def test_normalize_does_not_call_property_detection_for_balance_columns(monkeypatch):
    seen: list[str] = []

    def _detect(text: str) -> str:
        seen.append(text)
        return UNASSIGNED_PROPERTY

    monkeypatch.setattr(axiom_dataframe, "detect_property_from_text", _detect)
    raw = _raw(
        [
            ["Date", "Amount", "Balance", "Description"],
            ["2026-08-01", "10.00", "9999.99", "Power"],
        ]
    )
    axiom_dataframe.normalize_financial_dataframe(raw)
    assert all("9999.99" not in text for text in seen)


# --------------------------------------------------------------------------- #
# standardize_ledger_dataframe
# --------------------------------------------------------------------------- #
def test_standardize_applies_rule_categorization(monkeypatch):
    calls: list[pd.DataFrame] = []

    def _fake_rules(df: pd.DataFrame) -> pd.DataFrame:
        calls.append(df.copy())
        out = df.copy()
        out["Account Code"] = "Rates"
        return out

    monkeypatch.setattr(axiom_dataframe, "apply_rule_categorization", _fake_rules)
    raw = _raw([["Date", "Amount", "Description"], ["2026-09-01", "10.00", "Council"]])

    out = axiom_dataframe.standardize_ledger_dataframe(raw)

    assert len(calls) == 1
    assert list(calls[0].columns) == STANDARD_COLUMNS
    assert out.loc[0, "Account Code"] == "Rates"


def test_standardize_applies_manual_column_mapping(monkeypatch):
    monkeypatch.setattr(
        axiom_dataframe, "apply_rule_categorization", lambda df: df.assign(**{"Account Code": "X"})
    )
    raw = pd.DataFrame(
        {
            "When": ["2026-10-01"],
            "Value": ["30.00"],
            "Narrative": ["Insurance"],
        }
    )
    out = axiom_dataframe.standardize_ledger_dataframe(
        raw, {"Date": "When", "Amount": "Value", "Description": "Narrative"}
    )
    assert out.loc[0, "Date"] == "2026-10-01"
    assert out.loc[0, "Amount"] == 30.0
    assert out.loc[0, "Description"] == "Insurance"


def test_standardize_empty_frame_returns_standard_columns():
    out = axiom_dataframe.standardize_ledger_dataframe(pd.DataFrame())
    assert list(out.columns) == STANDARD_COLUMNS and out.empty


# --------------------------------------------------------------------------- #
# export_xero_master_csv
# --------------------------------------------------------------------------- #
def test_export_empty_frame_writes_header_only():
    payload = axiom_dataframe.export_xero_master_csv(pd.DataFrame())
    text = payload.decode("utf-8")
    assert text.splitlines()[0].split(",") == STANDARD_COLUMNS
    assert len(text.strip().splitlines()) == 1


def test_export_formats_amounts_and_fills_defaults():
    df = pd.DataFrame(
        [
            {
                "Date": "2026-01-01",
                "Amount": "$1,250",
                "Description": " Rent ",
                "Property": None,
                "Account Code": "",
            },
            {
                "Date": "2026-01-02",
                "Amount": "(45.5)",
                "Description": "Power",
                "Property": "Damian",
                "Account Code": "Utilities",
            },
        ]
    )
    text = axiom_dataframe.export_xero_master_csv(df).decode("utf-8")
    lines = text.strip().splitlines()
    assert lines[0].split(",") == STANDARD_COLUMNS
    assert lines[1] == f"2026-01-01,1250.00,Rent,{UNASSIGNED_PROPERTY},Uncategorized"
    assert lines[2] == "2026-01-02,-45.50,Power,Damian,Utilities"
