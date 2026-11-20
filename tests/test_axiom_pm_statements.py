"""Tests for the property-manager statement text parsers.

All page texts below are synthetic (made-up PM, owner, vendors and amounts)
that only mimic the line layout of a PM 'Property Expense Report' and
'Year End Summary'. No real statement files are read.
"""

from __future__ import annotations

import pytest

import axiom_pm_statements as pm
from axiom_core import STANDARD_COLUMNS

_EXPENSE_P1 = """Acme Property Management
Property Expense Report
Transactions Between 01/04/2025 and 31/03/2026
Trans. Date Ref. Description Amount
Owner: Sample Owner Ltd
1 Test Street, Sampletown
1000001 05/09/2025 INV-1 Widget Electrical Ltd -FIX OVEN (Invoice -100.00
Creditor: Widget Electrical Ltd )
Appliance Repairs -100.00
1000002 07/11/2025 R-77 Sample Council Rates Nov 25 -250.50
(Invoice Creditor: Sample Council)
1000003 03/02/2026 R-77 Sample Council Rates 3/4 -250.50
Council Rates -501.00
1000004 08/09/2025 W-9 Water Co 03 Sep 2025 -40.00
Tenancy Portion: $40.00 (Invoice Creditor: Water
Co)
Acme Property Management Page 1 of 2
01/04/2026 7:05:59 PM Property Expense Report
"""

_EXPENSE_P2 = """Trans. Date Ref. Description Amount
1000005 08/10/2025 W-9 Water Co 03 Oct 2025 -60.00
Tenancy Portion: $60.00 (Invoice Creditor: Water Co)
Water Services -100.00
Property Total -701.00
2 Example Road, Sampletown
1000006 22/01/2026 J-5 Handy Fix Ltd -1,234.56
GUTTER REPAIR (Invoice Creditor: Handy Fix Ltd)
Maintenance -1,234.56
Property Total -1,234.56
Report Total -1,935.56
Acme Property Management Page 2 of 2
01/04/2026 7:05:59 PM Property Expense Report
"""

_SUMMARY_P1 = """Year End Summary
From 1/04/2025 to 31/03/2026
Sample Owner Ltd
Property
1 Test Street, Sampletown
Year / Month Rent Intro. Funds T. Reimburse Fees GST Invoices Payments Net
2025 Sep 1,000.00 0.00 40.00 70.00 10.50 100.00 859.50 0.00
2026 Feb 2,000.00 0.00 0.00 140.00 21.00 501.00 1,338.00 -50.00
Totals $3,000.00 $0.00 $40.00 $210.00 $31.50 $601.00 $2,197.50 -$50.00
Invoice Analysis (Incl. GST) - Expenses Property Analysis (Fees)
Council Rates 501.00 Management Fees 210.00
Acme Property Management Page 1 of 1
"""


def _resolver(address: str) -> str:
    return {"1 Test Street, Sampletown": "1 Test", "2 Example Road, Sampletown": "2 Example"}.get(address, "Unassigned")


def test_detect_layout():
    assert pm.detect_pm_layout([_EXPENSE_P1]) == "expense_report"
    assert pm.detect_pm_layout([_SUMMARY_P1]) == "year_end_summary"
    assert pm.detect_pm_layout(["Bank statement\nDate Amount"]) is None
    assert pm.parse_pm_statement_pages(["random text"]) is None


def test_expense_report_rows_totals_and_properties():
    df = pm.parse_expense_report_pages([_EXPENSE_P1, _EXPENSE_P2], resolve_property=_resolver)
    assert list(df.columns) == STANDARD_COLUMNS
    assert len(df) == 6
    # Subtotal / Property Total / Report Total lines are not imported as rows
    assert round(df["Amount"].sum(), 2) == -1935.56
    assert df["Property"].tolist() == ["1 Test"] * 5 + ["2 Example"]
    assert df.loc[0, "Date"] == "2025-09-05"
    assert (df["Account Code"] == "").all()


def test_expense_report_wrapped_lines_and_category():
    df = pm.parse_expense_report_pages([_EXPENSE_P1, _EXPENSE_P2], resolve_property=_resolver)
    first = df.loc[0, "Description"]
    assert "FIX OVEN (Invoice Creditor: Widget Electrical Ltd )" in first
    assert "PM: Appliance Repairs" in first
    assert "Ref INV-1" in first and "PM Txn 1000001" in first
    # Group spanning a page break still gets the subtotal category
    water = df[df["Description"].str.contains("Water Co")]
    assert len(water) == 2
    assert water["Description"].str.contains("PM: Water Services").all()
    # Footer lines are never appended to a description
    assert not df["Description"].str.contains("Page 1 of 2").any()
    assert df.loc[5, "Amount"] == -1234.56


def test_continuation_ending_in_amount_is_not_a_subtotal():
    page = """Acme Property Management
Property Expense Report
Trans. Date Ref. Description Amount
1 Test Street, Sampletown
1000010 01/05/2025 X-1 Vendor A -30.00
partial credit applied 12.00
Repairs -30.00
Property Total -30.00
"""
    df = pm.parse_expense_report_pages([page], resolve_property=_resolver)
    assert len(df) == 1
    assert "partial credit applied 12.00" in df.loc[0, "Description"]
    assert "PM: Repairs" in df.loc[0, "Description"]


def test_year_end_summary_imports_rent_reimburse_fees_gst_only():
    df = pm.parse_year_end_summary_pages([_SUMMARY_P1], resolve_property=_resolver)
    assert list(df.columns) == STANDARD_COLUMNS
    # Sep: rent, reimburse, fees, gst (4) + Feb: rent, fees, gst (reimburse 0 skipped) = 7
    assert len(df) == 7
    assert set(df["Date"]) == {"2025-09-30", "2026-02-28"}
    assert (df["Property"] == "1 Test").all()
    assert round(df["Amount"].sum(), 2) == pytest.approx(3000.00 + 40.00 - 210.00 - 31.50)
    # Invoices / Payments / Net are never imported
    assert not df["Description"].str.contains("Invoices|Payments|Net").any()
    rent = df[df["Description"].str.contains(": Rent")]
    assert rent["Amount"].tolist() == [1000.00, 2000.00]


def test_default_resolver_matches_street_before_suburb(monkeypatch):
    import axiom_account_codes

    calls: list[str] = []

    def fake_detect(text, entity=None):
        calls.append(text)
        return "9 Elm" if text == "9 Elm Way" else "9 Elm / 5 Oak"

    monkeypatch.setattr(axiom_account_codes, "detect_property_from_text", fake_detect)
    assert pm._default_property_resolver("9 Elm Way , Oak, City") == "9 Elm"
    assert calls == ["9 Elm Way"]


def test_extract_pdf_tables_prefers_pm_parser(monkeypatch):
    import axiom_parsing

    sentinel = pm.parse_expense_report_pages([_EXPENSE_P1, _EXPENSE_P2], resolve_property=_resolver)
    monkeypatch.setattr(pm, "parse_pm_statement_pdf", lambda b: sentinel)
    out = axiom_parsing.extract_pdf_tables(b"%PDF-1.4")
    assert out is sentinel
