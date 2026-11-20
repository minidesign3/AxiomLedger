"""Reconciliation-check tests: parsed rows vs the statement's own printed totals.

Uses the synthetic page fixtures from the PM parser tests (made-up data only)
and deliberately damages them to prove mismatches surface as ⚠️ notes.
"""

from __future__ import annotations

import pandas as pd

import axiom_pm_annual as annual
import axiom_pm_statements as pm
from axiom_core import STANDARD_COLUMNS
from tests.test_axiom_pm_annual import _folio_pages, _label, _reapit_pages
from tests.test_axiom_pm_annual import _resolver as annual_resolver
from tests.test_axiom_pm_statements import _EXPENSE_P1, _EXPENSE_P2, _SUMMARY_P1, _resolver


def _note_for(df: pd.DataFrame) -> str:
    return pm.describe_pdf_check(df)


def test_clean_statements_reconcile():
    expense = pm.parse_expense_report_pages([_EXPENSE_P1, _EXPENSE_P2], resolve_property=_resolver)
    summary = pm.parse_year_end_summary_pages([_SUMMARY_P1], resolve_property=_resolver)
    folio = annual.parse_annual_statement_lines(_folio_pages(), resolve_property=annual_resolver)
    reapit = annual.parse_annual_statement_lines(_reapit_pages(), resolve_property=annual_resolver)
    for df in (expense, summary, folio, reapit):
        assert _note_for(df).startswith("✅"), _note_for(df)
    assert expense.attrs[pm.CHECK_ATTR]["checks"] == 3  # 2 Property Totals + Report Total
    assert summary.attrs[pm.CHECK_ATTR]["checks"] == 8  # one per Totals column


def test_expense_report_missed_transaction_is_flagged():
    # Line no longer matches the transaction pattern (date mangled) -> row is lost
    damaged = _EXPENSE_P2.replace("1000006 22/01/2026", "1000006 22-01-2026")
    df = pm.parse_expense_report_pages([_EXPENSE_P1, damaged], resolve_property=_resolver)
    note = _note_for(df)
    assert note.startswith("⚠️") and "DON'T match" in note
    assert "Property Total 2 Example Road" in note
    assert "statement -1,234.56 vs imported 0.00" in note
    assert "(+1 more)" in note  # Report Total fails too


def test_summary_unparsed_month_is_flagged():
    damaged = _SUMMARY_P1.replace("2026 Feb 2,000.00", "2026 Feb 2,000.0O")
    note = _note_for(pm.parse_year_end_summary_pages([damaged], resolve_property=_resolver))
    assert note.startswith("⚠️") and "Totals Rent" in note


def test_folio_wrong_column_is_flagged():
    pages = _folio_pages()
    # Shift the Council Rates amount into the Money In column
    pages[0][7] = _label("Council Rates") + [("$26.09", 415.0), ("$200.00", 566.0)]
    note = _note_for(annual.parse_annual_statement_lines(pages, resolve_property=annual_resolver))
    assert note.startswith("⚠️") and "Subtotal 1 Test Street" in note


def test_reapit_missing_item_is_flagged():
    pages = _reapit_pages()
    pages[1] = [ln for ln in pages[1] if annual.line_text(ln) != "Management Fee $52.17 $400.00"]
    note = _note_for(annual.parse_annual_statement_lines(pages, resolve_property=annual_resolver))
    assert note.startswith("⚠️") and "money out" in note


def test_no_totals_found_is_unverified_warning():
    page = """Acme Property Management
Property Expense Report
Trans. Date Ref. Description Amount
1 Test Street, Sampletown
1000010 01/05/2025 X-1 Vendor A -30.00
"""
    note = _note_for(pm.parse_expense_report_pages([page], resolve_property=_resolver))
    assert note.startswith("⚠️") and "no statement totals found" in note


def test_generic_and_empty_notes():
    assert _note_for(pd.DataFrame()) == pm.PDF_NO_TABLE_NOTE
    generic = pm.attach_check(pd.DataFrame({"a": [1, 2]}), "generic_tables", [])
    assert _note_for(generic).startswith("⚠️ Unrecognised PDF layout · 2 rows")
    # Frames without attrs (e.g. older code paths) are treated as unrecognised
    assert _note_for(pd.DataFrame({"a": [1]})).startswith("⚠️ Unrecognised")


def test_board_status_marks_warning_red_and_success_green():
    from axiom_ui_ingestion_board import _file_status_style

    df = pd.DataFrame([["2025-01-01", -1.0, "x", "1 Test", "Rates"]], columns=STANDARD_COLUMNS)
    stats = {"red": 0, "yellow": 0, "green": 1, "total": 1}
    colour, line = _file_status_style("pdf", {"note": "⚠️ totals DON'T match"}, df, stats)
    assert colour == "#B91C1C" and line.startswith("⚠️")
    _, line_ok = _file_status_style("pdf", {"note": "✅ PM Expense Report · 1 rows"}, df, stats)
    assert line_ok.endswith("✅ Totals match statement")


def test_backfill_sets_note_without_touching_rows(monkeypatch, tmp_path):
    import axiom_ingestion

    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    stored = pd.DataFrame([["2025-01-01", -1.0, "edited", "1 Test", "Rates"]], columns=STANDARD_COLUMNS)
    fresh_same = pm.attach_check(stored.copy(), "expense_report", [("Report Total", -1.0, -1.0)])
    saved: list[dict] = []
    monkeypatch.setattr(axiom_ingestion, "extract_pdf_tables", lambda b: fresh_same)
    monkeypatch.setattr(axiom_ingestion, "_save_file_record_to_sqlite", lambda e, y, r: saved.append(dict(r)))

    rec = {"bucket": "pdf", "note": "", "df": stored, "vault_path": str(pdf)}
    axiom_ingestion._backfill_pdf_check_notes("E", "2026", [rec])
    assert rec["note"].startswith("✅") and rec["df"] is stored and len(saved) == 1

    # Stored rows from an older parser (count differs) -> stale warning, rows untouched
    rec2 = {"bucket": "pdf", "note": "", "df": stored.iloc[:0].copy(), "vault_path": str(pdf)}
    axiom_ingestion._backfill_pdf_check_notes("E", "2026", [rec2])  # empty df -> skipped
    assert rec2["note"] == ""
    two_rows = pd.concat([stored, stored], ignore_index=True)
    rec3 = {"bucket": "pdf", "note": "", "df": two_rows, "vault_path": str(pdf)}
    axiom_ingestion._backfill_pdf_check_notes("E", "2026", [rec3])
    assert rec3["note"].startswith("⚠️ Saved rows (2) differ") and rec3["df"] is two_rows

    # Records that already have a note are never re-read
    rec4 = {"bucket": "pdf", "note": "✅ done", "df": stored, "vault_path": str(pdf)}
    axiom_ingestion._backfill_pdf_check_notes("E", "2026", [rec4])
    assert rec4["note"] == "✅ done" and len(saved) == 2
