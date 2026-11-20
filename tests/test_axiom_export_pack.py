"""Tests for duplicate protection, ledger/file sync, readiness checks and the Accountant Pack.

All data is synthetic (made-up files, payees and amounts).
"""

from __future__ import annotations

import io
from datetime import date

import pandas as pd
from openpyxl import load_workbook

import axiom_dedupe
from axiom_accountant_pack import build_pack_bytes, pack_filename
from axiom_core import STANDARD_COLUMNS
from axiom_dates import fy_bounds
from axiom_export_checks import build_export, is_non_pl_code
from axiom_ledger_sync import LEDGER_ONLY, ledger_with_sources, push_ledger_to_files

FY = fy_bounds("2026", "NZ")


def _df(rows):
    return pd.DataFrame(rows, columns=STANDARD_COLUMNS)


def _rec(name, bucket, rows, **extra):
    return {"name": name, "bucket": bucket, "df": _df(rows), **extra}


def _records():
    bank = _rec("bank.csv", "csv", [
        ["05/09/2025", 1000.0, "Tenant A rent", "1 Test", "Rent Income"],
        ["06/09/2025", -200.0, "Sample Council", "1 Test", "Rates"],
        ["07/09/2025", -500.0, "To savings", "General / Portfolio", "Funds Transfer"],
        ["07/09/2025", 500.0, "From cheque", "General / Portfolio", "Funds Transfer"],
    ], raw_bytes=b"bank-bytes")
    shot = _rec("shot.png", "screenshots", [
        ["6 Sep 2025", -200.0, "Council payment (screenshot)", "1 Test", "Rates"],  # repeats bank row
        ["2026-11-14", 0.0, "Account summary", "Unassigned", "Interest"],             # $0 placeholder
    ], raw_bytes=b"png-bytes")
    return [bank, shot]


def _ledger(records):
    return pd.concat([r["df"] for r in records], ignore_index=True)


# ---- dedupe -------------------------------------------------------------- #
def test_same_content_detection_and_groups():
    a = {"name": "a.csv", "raw_bytes": b"same"}
    b = {"name": "b.csv", "raw_bytes": b"same"}
    c = {"name": "c.csv", "raw_bytes": b"other"}
    assert axiom_dedupe.find_same_content_record(b"same", [c, a]) is a
    assert axiom_dedupe.find_same_content_record(b"new", [a, c]) is None
    assert axiom_dedupe.duplicate_content_groups([a, b, c]) == [["a.csv", "b.csv"]]


def test_vault_path_hashing(tmp_path):
    p = tmp_path / "x.csv"
    p.write_bytes(b"vault")
    rec = {"name": "x.csv", "vault_path": str(p)}
    assert axiom_dedupe.find_same_content_record(b"vault", [rec]) is rec
    assert rec["content_hash"] == axiom_dedupe.content_hash(b"vault")


# ---- ledger <-> files ----------------------------------------------------- #
def test_ledger_sources_and_ledger_only_rows():
    recs = _records()
    ledger = pd.concat([_ledger(recs), _df([["01/10/2025", -5.0, "Appended", "1 Test", "Bank Fee"]])], ignore_index=True)
    out, aligned = ledger_with_sources(ledger, recs)
    assert aligned
    assert out["Source File"].tolist() == ["bank.csv"] * 4 + ["shot.png"] * 2 + [LEDGER_ONLY]
    assert out["Source Type"].tolist()[:5] == ["Bank CSV"] * 4 + ["Screenshot"]


def test_push_ledger_edits_back_to_files():
    recs = _records()
    ledger = _ledger(recs)
    ledger.loc[1, "Account Code"] = "Council Rates"  # edit made on the Master sheet
    changed = push_ledger_to_files(ledger, recs)
    assert [r["name"] for r in changed] == ["bank.csv"]
    assert recs[0]["df"].loc[1, "Account Code"] == "Council Rates"
    # Shorter ledger can't be aligned -> no write-back
    assert push_ledger_to_files(ledger.iloc[:3], recs) == []


# ---- readiness checks ----------------------------------------------------- #
def test_clean_export_is_ready_and_excludes_overlaps_and_placeholders():
    recs = _records()
    res = build_export(_ledger(recs), recs, fy=FY, codes=["Rent Income", "Rates", "Funds Transfer", "Interest"],
                       properties=["1 Test"])
    assert res.ready, [c.detail for c in res.blockers]
    assert len(res.rows) == 4
    assert set(res.excluded["Excluded Because"].str[:10]) == {"Screenshot", "$0 placeho"}
    assert res.rows["Date"].tolist() == ["05/09/2025", "06/09/2025", "07/09/2025", "07/09/2025"]
    assert res.rows["Type"].tolist() == ["P&L", "P&L", "Not P&L", "Not P&L"]
    assert list(res.rows.columns)[-3:] == ["Type", "Source File", "Source Type"]
    # Opt-in keeps the overlapping screenshot row
    res2 = build_export(_ledger(recs), recs, fy=FY, include_overlaps=True)
    assert len(res2.rows) == 5


def test_blockers_and_warnings():
    recs = _records()
    recs[0]["df"].loc[1, "Account Code"] = "Uncategorized"  # keep the bank rent row coded as rent
    recs[0]["df"].loc[3, "Date"] = ""
    recs[0]["df"].loc[2, "Amount"] = -600.0          # transfers no longer net to zero
    recs.append(_rec("copy.csv", "csv", [["10/10/2025", -1.0, "x", "14 A / 15 B", "Repairs"]], raw_bytes=b"bank-bytes"))
    recs.append(_rec("pm.pdf", "pdf", [["31/03/2026", 50.0, "PM Folio Summary: Rent | Annual total", "1 Test", ""]],
                     note="⚠️ PM Folio Summary · totals DON'T match"))
    res = build_export(_ledger(recs), recs, fy=FY, codes=["Rent Income", "Rates", "Funds Transfer"], properties=["1 Test"])
    names = {c.name for c in res.blockers}
    assert {"Uncoded rows", "Dates: blank", "Same file imported twice", "File warning: pm.pdf", "Rent counted twice"} <= names
    warn = {c.name: c.detail for c in res.checks if c.level == "warning"}
    assert "Transfers don't net to zero" in warn
    assert "Repairs" in warn["Codes not on the accountant's list"]
    assert "14 A / 15 B" in warn["Split-property rows"]
    assert not res.ready


def test_mixed_sign_and_non_pl_classification():
    recs = [_rec("b.csv", "csv", [["05/09/2025", 2600.0, "a", "1 Test", "Interest"],
                                   ["06/09/2025", -3000.0, "b", "1 Test", "Interest"]])]
    res = build_export(_ledger(recs), recs, fy=FY, codes=["Interest"], properties=["1 Test"])
    assert any(c.name == "Mixed signs: Interest" for c in res.checks)
    assert is_non_pl_code("James Drawings") and is_non_pl_code("Principal Loan") and is_non_pl_code("IRD Payment")
    assert not is_non_pl_code("Loan Interest") and not is_non_pl_code("Rates")


def test_ledger_shorter_than_files_is_blocker():
    recs = _records()
    res = build_export(_ledger(recs).iloc[:2], recs, fy=FY)
    assert any(c.name == "Ledger out of sync" for c in res.blockers)


# ---- accountant pack ----------------------------------------------------- #
def test_pack_workbook_sheets_dates_and_summary():
    recs = _records()
    res = build_export(_ledger(recs), recs, fy=FY, codes=["Rent Income", "Rates", "Funds Transfer"], properties=["1 Test"])
    wb = load_workbook(io.BytesIO(build_pack_bytes(res, "Sample Ltd", "2026", "1 Apr 2025 – 31 Mar 2026")))
    assert wb.sheetnames == ["Summary", "Per Property", "Rent Reconciliation", "Transactions", "Checks", "Excluded"]
    tx = wb["Transactions"]
    assert [c.value for c in tx[1]] == ["Date", "Amount", "Description", "Property", "Account Code", "Type",
                                       "Source File", "Source Type"]
    assert tx["A2"].value.date() == date(2025, 9, 5) and tx["A2"].number_format == "dd/mm/yyyy"
    summary = {r[0].value: r[1].value for r in wb["Summary"].iter_rows(max_row=5)}
    assert summary["Status"].startswith("READY")
    values = [[c.value for c in r] for r in wb["Summary"].iter_rows()]
    flat = [v for row in values for v in row]
    assert "Rent Income" in flat and "Funds Transfer" in flat and 800.0 in flat  # P&L total 1000 - 200
    assert wb["Excluded"].max_row == 3
    assert pack_filename("Sample Ltd", "2026") == "Sample_Ltd_FY2026_Accountant_Pack.xlsx"
