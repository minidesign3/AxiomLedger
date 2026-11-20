"""Tests for the column-position-aware annual PM statement parsers.

Pages are synthetic lists of (text, x1) words that only mimic the layout of a
Reapit 'Financial Year Statement' and a 'Folio Summary' (made-up PM, owner,
addresses and amounts). No real statement files are read.
"""

from __future__ import annotations

import axiom_pm_annual as annual
from axiom_core import STANDARD_COLUMNS

# Folio columns (right edges): Tax 415, Money Out 491, Money In 566
_T, _O, _I = 415.0, 491.0, 566.0


def _line(*words: tuple[str, float]) -> list[tuple[str, float]]:
    return list(words)


def _label(text: str, x: float = 60.0) -> list[tuple[str, float]]:
    return [(w, x + i) for i, w in enumerate(text.split())]


def _resolver(address: str) -> str:
    if address.startswith("1 Test"):
        return "1 Test"
    if address.startswith("2 Example"):
        return "2 Example"
    return "Unassigned"


def _folio_pages() -> list[list[list[tuple[str, float]]]]:
    header = _line(("Account", 80), ("Included", 380), ("Tax", _T), ("Money", 470), ("Out", _O), ("Money", 550), ("In", _I))
    p1 = [
        _label("Sample PM Ltd"),
        _label("Folio Summary"),
        _label("From: 1/04/2025") + [("To:", 300), ("31/03/2026", 340)],
        _line(("Money", 170), ("In", 183), ("Money", 300), ("Out", 318), ("Balance", 450)),
        _line(("$1,180.00", 191), ("$330.00", 320), ("$850.00", 451)),  # summary box: ignored
        header,
        _label("1 Test Street, Sampletown"),
        _label("Council Rates") + [("$26.09", _T), ("$200.00", _O)],
        _label("Rental Income Received") + [("$1,000.00", _I)],
        _label("Water Usage Paid by Tenant") + [("$10.43", _T), ("$80.00", _I)],
        [("$200.00", _O), ("$1,080.00", _I)],  # subtotal amounts on their own baseline
        _label("Subtotal"),
        _label("2 Example Road, Sampletown"),
        _label("Very Long Compliance Assessment,"),  # wrapped label, no amounts
        _label("Healthy Homes") + [("$13.04", _T), ("$100.00", _O)],
    ]
    p2 = [
        header,  # repeated on page 2; property continues
        _label("Management Fee") + [("$3.91", _T), ("$30.00", _O)],
        [("$130.00", _O), ("$0.00", _I)],
        _label("Subtotal"),
        _label("Account Transactions"),
        _label("Tenant Contribution") + [("$13.04", _T), ("$100.00", _I)],
        [("$0.00", _O), ("$100.00", _I)],
        _label("Subtotal"),
        _label("Total") + [("$330.00", _O), ("$1,180.00", _I)],
        _label("Total Tax on Money Out:") + [("$43.04", 158)],
    ]
    return [p1, p2]


def test_folio_detect_and_totals():
    pages = _folio_pages()
    assert annual.detect_annual_layout(pages) == "folio_summary"
    df = annual.parse_annual_statement_lines(pages, resolve_property=_resolver)
    assert list(df.columns) == STANDARD_COLUMNS
    assert len(df) == 6  # subtotals / totals / summary box are not rows
    assert round(df.loc[df.Amount < 0, "Amount"].sum(), 2) == -330.00
    assert round(df.loc[df.Amount > 0, "Amount"].sum(), 2) == 1180.00
    assert set(df["Date"]) == {"2026-03-31"}
    assert (df["Account Code"] == "").all()


def test_folio_columns_properties_and_wrapped_labels():
    df = annual.parse_annual_statement_lines(_folio_pages(), resolve_property=_resolver)
    by_label = {d.split(" | ")[0]: (a, p) for d, a, p in zip(df.Description, df.Amount, df.Property, strict=True)}
    # Same text shape, different column -> opposite sign
    assert by_label["PM Folio Summary: Council Rates"] == (-200.00, "1 Test")
    assert by_label["PM Folio Summary: Water Usage Paid by Tenant"] == (80.00, "1 Test")
    assert by_label["PM Folio Summary: Very Long Compliance Assessment, Healthy Homes"] == (-100.00, "2 Example")
    # Page-2 continuation stays on the same property
    assert by_label["PM Folio Summary: Management Fee"] == (-30.00, "2 Example")
    assert by_label["PM Folio Summary: Tenant Contribution"] == (100.00, "General / Portfolio")
    rates = df[df.Description.str.contains("Council Rates")].Description.iloc[0]
    assert "GST incl $26.09" in rates
    # Address is kept out of the description (Property column holds it)
    assert not df.Description.str.contains("Sampletown").any()


def _reapit_pages() -> list[list[list[tuple[str, float]]]]:
    header = _line(("INCLUDED", 767), ("GST", 808), ("EXPENSE", 969), ("INCOME", 1130))
    items = [
        header,
        _label("ITEM"),
        _label("Rent") + [("*", 120), ("$5,000.00", 1130)],
        _label("Appliance Repairs") + [("$13.04", 808), ("$100.00", 969)],
        _label("Management Fee") + [("$52.17", 808), ("$400.00", 969)],
    ]
    p1 = [
        _label("Financial Year Statement"),
        _label("From: 1 April 2025"),
        _label("To: 31 March 2026"),
        _label("Total For All Properties"),
        *items,  # portfolio totals duplicate the breakdown: must be skipped
    ]
    p2 = [
        _label("Property Breakdown"),
        _label("#1 of 1 properties"),
        _label("1 Test Street, Sampletown 1010"),
        _label("Sample Owner Ltd"),
        *items,
        _label("Income Expenses Net Position"),
        [("$5,000.00", 436), ("$500.00", 673), ("$4,500.00", 919)],
        _label("Total For All Properties"),
        _label("Income Expenses Net Position"),
        [("$5,000.00", 436), ("$500.00", 673), ("$4,500.00", 919)],
    ]
    return [p1, p2]


def test_reapit_imports_only_property_breakdown():
    pages = _reapit_pages()
    assert annual.detect_annual_layout(pages) == "reapit_financial_year"
    df = annual.parse_annual_statement_lines(pages, resolve_property=_resolver)
    assert len(df) == 3
    assert (df["Property"] == "1 Test").all()
    assert set(df["Date"]) == {"2026-03-31"}
    assert df["Amount"].tolist() == [5000.00, -100.00, -400.00]
    assert df.Description.iloc[0].startswith("PM Financial Year: Rent |")
    assert "GST incl $52.17" in df.Description.iloc[2]


def test_unknown_layout_returns_none():
    assert annual.parse_annual_statement_lines([[_label("Bank statement")]]) is None


def test_words_to_lines_groups_by_top_and_orders_by_x():
    words = [
        {"text": "$5.00", "x0": 400, "x1": 430, "top": 100.6},
        {"text": "Fee", "x0": 50, "x1": 70, "top": 100.0},
        {"text": "Next", "x0": 50, "x1": 80, "top": 120.0},
    ]
    lines = annual.words_to_lines(words)
    assert [annual.line_text(ln) for ln in lines] == ["Fee $5.00", "Next"]
    assert lines[0][1] == ("$5.00", 430.0)
