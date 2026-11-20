"""Rent reconciliation tests: PM statement owner payouts vs bank deposits (synthetic data only)."""

from __future__ import annotations

import io

import pandas as pd
from openpyxl import load_workbook

from axiom_accountant_pack import build_pack_bytes
from axiom_core import STANDARD_COLUMNS
from axiom_dates import fy_bounds
from axiom_export_checks import PAYOUT_REASON, build_export
from axiom_pack_sheets import property_blocks
from axiom_pm_payouts import (
    ANNUAL,
    MONTHLY,
    manager_label,
    payouts_from_rows,
    payouts_from_summary_pages,
)
from axiom_pm_recon import default_keywords, effective_payee_map, match_pm_deposits
from tests.test_axiom_pm_statements import _SUMMARY_P1

FY = fy_bounds("2026", "NZ")


def _df(rows):
    return pd.DataFrame(rows, columns=STANDARD_COLUMNS)


def test_monthly_payouts_from_summary_and_annual_from_rows():
    payouts = payouts_from_summary_pages([_SUMMARY_P1], resolver=lambda a: "1 Test")
    assert payouts == [
        {"month": "2025-09", "property": "1 Test", "amount": 859.50},
        {"month": "2026-02", "property": "1 Test", "amount": 1338.00},
    ]
    rows = _df([["31/03/2026", 5000.0, "PM Financial Year: Rent", "2 Oak", ""],
                ["31/03/2026", -400.0, "PM Financial Year: Management Fee", "2 Oak", ""]])
    assert payouts_from_rows(rows) == [{"month": None, "property": "2 Oak", "amount": 4600.0}]
    assert manager_label("Acme PM - Financial Summary 1 Apr 2026.pdf") == "Acme PM"


def test_payee_keywords_longest_match_wins():
    assert "scpml" in default_keywords("Ray White") and default_keywords("Ray White Karaka")[0] == "ray white karaka"
    pmap = effective_payee_map(["Ray White", "Ray White Karaka"], {"Acme": ["acme pm"]})
    assert pmap["Acme"] == ["acme pm"]
    rows = pd.DataFrame({"Description": ["RAY WHITE KARAKA payout", "Ray White payout", "Ray White refund", "Tenant"],
                         "Amount": [10.0, 20.0, -5.0, 30.0], "_bucket": ["csv", "csv", "csv", "csv"]})
    assert match_pm_deposits(rows, pmap).tolist() == ["Ray White Karaka", "Ray White", "", ""]


def _scenario(bank_rows):
    pm_rows = [
        ["30/09/2025", 1000.0, "PM Year End Summary: Rent | 2025 Sep", "1 Test", "Rent Income"],
        ["30/09/2025", -70.0, "PM Year End Summary: Management Fees | 2025 Sep", "1 Test", "Property Management"],
        ["28/02/2026", 2000.0, "PM Year End Summary: Rent | 2026 Feb", "1 Test", "Rent Income"],
    ]
    recs = [
        {"name": "Acme PM - Financial Summary.pdf", "bucket": "pdf", "df": _df(pm_rows), "note": "✅ ok"},
        {"name": "bank.csv", "bucket": "csv", "df": _df(bank_rows)},
    ]
    ledger = pd.concat([r["df"] for r in recs], ignore_index=True)
    statements = {"Acme PM": {"basis": MONTHLY, "files": ["Acme PM - Financial Summary.pdf"],
                              "payouts": [{"month": "2025-09", "property": "1 Test", "amount": 859.50},
                                          {"month": "2026-02", "property": "1 Test", "amount": 1338.00}]}}
    return build_export(ledger, recs, fy=FY, codes=["Rent Income", "Property Management", "Funds Transfer", "Interest"],
                        properties=["1 Test"], statements=statements, entity="Sample Holdings Ltd")


def test_matching_payouts_reconcile_and_stay_out_of_export():
    res = _scenario([
        ["03/10/2025", 859.50, "ACME PM owner payout", "1 Test", "Rent Income"],   # paid early next month
        ["02/03/2026", 1338.00, "Acme PM owner payout", "1 Test", "Uncategorized"],
    ])
    m = res.recon.managers[0]
    assert (m.statement_total, m.bank_total, m.ok) == (2197.50, 2197.50, True)
    assert m.monthly["Running difference"].iloc[-1] == 0.0
    # Bank payouts are excluded (even the uncoded one) and rent is NOT double counted
    assert set(res.excluded["Excluded Because"]) == {PAYOUT_REASON}
    assert not any(c.name.startswith(("Rent check", "Rent counted twice", "Uncoded")) for c in res.checks)
    assert res.ready
    assert res.rows["Source Type"].eq("PM Statement").sum() == 3


def test_short_payment_is_a_blocker_and_unmatched_and_own_account_warn():
    res = _scenario([
        ["03/10/2025", 859.50, "Acme PM owner payout", "1 Test", "Rent Income"],
        ["05/03/2026", 900.00, "Sample Realty Ltd payout", "1 Test", "Rent Income"],   # PM-like, unknown payee
        ["06/03/2026", 600.00, "Sample Holdings Ltd transfer", "1 Test", "Interest"],  # own account
    ])
    checks = {c.name: c for c in res.checks}
    assert checks["Rent check: Acme PM"].level == "blocker"
    assert "difference -$1,338.00" in checks["Rent check: Acme PM"].detail
    assert checks["Unmatched manager-like deposits"].count == 1
    assert checks["Own-account transfers coded as income/expense"].level == "warning"
    assert "Rent counted twice" in checks  # the unmatched rent deposit is still in the P&L


def test_per_property_blocks_and_pack_sheets():
    res = _scenario([["03/10/2025", 859.50, "Acme PM owner payout", "1 Test", "Rent Income"],
                     ["02/03/2026", 1338.00, "Acme PM owner payout", "1 Test", "Rent Income"]])
    (prop, table, net, rent), = property_blocks(res.rows)
    assert prop == "1 Test" and net == 2930.0
    assert table.set_index("Account Code").loc["Rent Income", "Via property manager"] == 3000.0
    assert rent.to_dict("records") == [{"Manager": "Acme PM", "Period": "09/2025", "Rent": 1000.0},
                                       {"Manager": "Acme PM", "Period": "02/2026", "Rent": 2000.0}]
    wb = load_workbook(io.BytesIO(build_pack_bytes(res, "Sample Holdings Ltd", "2026")))
    recon_vals = [c.value for row in wb["Rent Reconciliation"].iter_rows() for c in row]
    assert "Acme PM" in recon_vals and "✅ Matches" in recon_vals and 2197.5 in recon_vals
    prop_vals = [c.value for row in wb["Per Property"].iter_rows() for c in row]
    assert "Net for 1 Test" in prop_vals and 2930.0 in prop_vals


def test_annual_basis_has_no_monthly_table():
    recs = [{"name": "bank.csv", "bucket": "csv", "df": _df([["01/12/2025", 4600.0, "Acme PM", "1 Test", "Rent Income"]])}]
    statements = {"Acme PM": {"basis": ANNUAL, "files": ["x.pdf"], "payouts": [{"month": None, "property": "1 Test",
                                                                                  "amount": 4600.0}]}}
    res = build_export(recs[0]["df"], recs, fy=FY, statements=statements)
    assert res.recon.managers[0].ok and res.recon.managers[0].monthly is None
