"""Tests for the coded-workbook parser (phase D12).

Workbooks are supplied as in-memory ``{sheet_name: DataFrame}`` maps (the same
shape ``pd.read_excel(..., header=None)`` returns), and property detection is
stubbed so nothing touches the real memory store.
"""

from __future__ import annotations

import pandas as pd
import pytest

import axiom_workbook
from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY

_PROPERTY_HINTS = {
    "damian": "9 Damian",
    "blampied": "33 Blampied",
    "frances": "26 Frances",
}


def _detect(text: object, entity: str | None = None) -> str:
    lowered = str(text).lower()
    for token, prop in _PROPERTY_HINTS.items():
        if token in lowered:
            return prop
    return UNASSIGNED_PROPERTY


@pytest.fixture(autouse=True)
def _stub_detection(monkeypatch):
    monkeypatch.setattr(axiom_workbook, "detect_property_from_text", _detect)
    monkeypatch.setattr(axiom_workbook, "get_active_entity", lambda entity=None: "Test Entity")


def _workbook(monkeypatch, sheets: dict[str, pd.DataFrame], filename: str = "Codes.xlsx"):
    monkeypatch.setattr(axiom_workbook.pd, "read_excel", lambda *a, **k: sheets)
    return axiom_workbook.parse_coded_workbook_sheets(b"ignored", filename=filename)


def _sheet(rows: list[list[object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_parse_coded_workbook_returns_all_sections(monkeypatch):
    result = _workbook(
        monkeypatch,
        {
            "Ledger": _sheet(
                [
                    ["Coded Bank Ledger", None, None, None, None],
                    ["Date", "Other Party", "Amount", "Code", "Analysis Code"],
                    ["2026-01-05", "Countdown", -10.0, "Materials", "9 Damian"],
                    ["2026-01-06", "Raywhite", 250.0, "Rent Income", "33 Blampied"],
                ]
            )
        },
    )

    assert set(result) == {
        "account_codes",
        "properties",
        "payee_code_rules",
        "sheet_summaries",
        "combined_bank_df",
    }
    assert result["account_codes"] == ["Materials", "Rent Income"]
    assert result["properties"] == ["33 Blampied", "9 Damian"]

    combined = result["combined_bank_df"]
    assert list(combined.columns) == STANDARD_COLUMNS
    assert combined["Amount"].tolist() == [-10.0, 250.0]
    assert combined["Property"].tolist() == ["9 Damian", "33 Blampied"]
    assert combined["Description"].tolist() == [
        "Countdown | 9 Damian",
        "Raywhite | 33 Blampied",
    ]


def test_parse_coded_workbook_builds_single_property_payee_rules(monkeypatch):
    result = _workbook(
        monkeypatch,
        {
            "Ledger": _sheet(
                [
                    ["Date", "Other Party", "Amount", "Code", "Analysis Code"],
                    ["2026-01-05", "Countdown", -10.0, "Materials", "9 Damian"],
                    ["2026-02-05", "Countdown", -12.0, "Materials", "9 Damian"],
                ]
            )
        },
    )

    rules = result["payee_code_rules"]
    assert len(rules) == 1
    assert rules[0]["pattern"] == "Countdown"
    assert rules[0]["account_code"] == "Materials"
    assert rules[0]["property"] == "9 Damian"
    assert rules[0]["property_mode"] == "Single Property"
    assert rules[0]["occurrences"] == 2


def test_parse_coded_workbook_keeps_multi_property_payee_decoupled(monkeypatch):
    result = _workbook(
        monkeypatch,
        {
            "Ledger": _sheet(
                [
                    ["Date", "Other Party", "Amount", "Code", "Analysis Code"],
                    ["2026-01-05", "Kim Painter", -10.0, "Repairs", "9 Damian"],
                    ["2026-02-05", "Kim Painter", -12.0, "Repairs", "26 Frances"],
                ]
            )
        },
    )

    rule = result["payee_code_rules"][0]
    assert rule["property"] == UNASSIGNED_PROPERTY
    assert rule["property_mode"].startswith("Multi-Property (2 props")


def test_parse_coded_workbook_marks_portfolio_wide_codes(monkeypatch):
    result = _workbook(
        monkeypatch,
        {
            "Ledger": _sheet(
                [
                    ["Date", "Other Party", "Amount", "Code"],
                    ["2026-01-05", "Bank Fee", -2.5, "Bank Fee"],
                ]
            )
        },
    )

    rule = result["payee_code_rules"][0]
    assert rule["property"] == "General / Portfolio"
    assert rule["property_mode"] == "Portfolio-Wide"
    assert result["combined_bank_df"].loc[0, "Property"] == "General / Portfolio"


def test_parse_coded_workbook_ignores_noise_codes_and_total_rows(monkeypatch):
    result = _workbook(
        monkeypatch,
        {
            "Ledger": _sheet(
                [
                    ["Date", "Other Party", "Amount", "Code"],
                    ["2026-01-05", "Countdown", -10.0, "Materials"],
                    ["2026-01-06", "Total", 999.0, "Total"],
                ]
            )
        },
    )

    combined = result["combined_bank_df"]
    assert len(combined) == 1
    assert combined.loc[0, "Account Code"] == "Materials"
    assert "total" not in [c.lower() for c in result["account_codes"]]


def test_parse_coded_workbook_combines_debit_and_credit(monkeypatch):
    result = _workbook(
        monkeypatch,
        {
            "Ledger": _sheet(
                [
                    ["Date", "Other Party", "Debit", "Credit", "Code"],
                    ["2026-01-05", "Power Co", 45.0, None, "Utilities"],
                    ["2026-01-06", "Raywhite", None, 500.0, "Rent Income"],
                ]
            )
        },
    )

    assert result["combined_bank_df"]["Amount"].tolist() == [-45.0, 500.0]


def test_parse_coded_workbook_converts_excel_serial_dates(monkeypatch):
    result = _workbook(
        monkeypatch,
        {
            "Ledger": _sheet(
                [
                    ["Date", "Other Party", "Amount", "Code"],
                    [45385, "Countdown", -10.0, "Materials"],
                ]
            )
        },
    )

    assert result["combined_bank_df"].loc[0, "Date"] == "2024-04-03"


def test_parse_coded_workbook_pivot_sheet_only_harvests_properties(monkeypatch):
    result = _workbook(
        monkeypatch,
        {
            "Pivot Table": _sheet(
                [
                    ["Row Labels", "Sum of Amount"],
                    ["9 Damian Road", 100.0],
                    ["26 Frances Street", 50.0],
                ]
            )
        },
    )

    summary = result["sheet_summaries"][0]
    assert summary["sheet_type"] == "Pivot / Summary Table"
    assert summary["rows"] == 0
    assert result["properties"] == ["26 Frances", "9 Damian"]
    assert result["combined_bank_df"].empty


def test_parse_coded_workbook_eoy_sheet_inherits_section_property(monkeypatch):
    result = _workbook(
        monkeypatch,
        {
            "EOY Raywhite": _sheet(
                [
                    ["EOY Statement — 33 Blampied Road"],
                    [None, None, None],
                    ["Date", "Details", "Amount"],
                    ["33 Blampied Road, Auckland", None, None],
                    ["2026-03-31", "Rent", 500.0],
                    ["2026-04-01", "Rates", -100.0],
                ]
            )
        },
    )

    summary = result["sheet_summaries"][0]
    assert summary["sheet_type"] == "EOY Property Statement"
    assert summary["rows"] == 2
    eoy_df = summary["df"]
    assert eoy_df["Property"].tolist() == ["33 Blampied", "33 Blampied"]
    # EOY statements are reference material, not imported bank transactions.
    assert result["combined_bank_df"].empty


def test_parse_coded_workbook_reads_csv_fallback(monkeypatch):
    result = axiom_workbook.parse_coded_workbook_sheets(
        b"Date,Other Party,Amount,Code\n2026-01-05,Shop,-5.0,Materials\n",
        filename="ledger.csv",
    )
    combined = result["combined_bank_df"]
    assert len(combined) == 1
    assert combined.loc[0, "Account Code"] == "Materials"


def test_parse_coded_workbook_skips_empty_and_headerless_sheets(monkeypatch):
    result = _workbook(
        monkeypatch,
        {
            "Empty": pd.DataFrame(),
            "Notes": _sheet([["Just some notes"], ["nothing tabular here"]]),
        },
    )

    assert result["combined_bank_df"].empty
    assert [s["sheet_type"] for s in result["sheet_summaries"]] == ["Summary Sheet"]
