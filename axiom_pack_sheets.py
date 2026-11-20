"""
axiom_pack_sheets
=================

Extra Accountant Pack sheets:

* **Per Property** — one block per house: each Account Code split into what
  went through the property manager vs the bank/other, the total, the net for
  the house, and the house's rent by manager and period (monthly or year).
* **Rent Reconciliation** — per manager: what the statement says it paid you
  vs what the bank received, difference and status; the month-by-month
  running comparison for monthly statements; every bank deposit matched to a
  manager; manager-looking deposits that matched nothing.

Writers take the pack's ``write_table(ws, df, start_row, money_cols, …)``
helper so formatting stays in one place.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
from openpyxl.styles import Font

from axiom_pm_payouts import manager_label
from axiom_pm_recon import ReconResult
from axiom_pm_rows import _RENT_LABEL_RE, pm_match_text

_BOLD = Font(bold=True)
_TITLE = Font(bold=True, size=12)
_MONEY_COLS = ("Via property manager", "Via bank / other", "Total", "Rent",
               "Statement paid to you", "Bank received", "Difference",
               "Running statement", "Running bank", "Running difference", "Amount")


def _rent_period(date_str: str, desc: str) -> str:
    if "annual total" in desc.lower():
        return "Year total"
    parts = str(date_str).split("/")
    return f"{parts[1]}/{parts[2]}" if len(parts) == 3 else str(date_str)


def property_blocks(rows: pd.DataFrame) -> list[tuple[str, pd.DataFrame, float, pd.DataFrame]]:
    """``[(property, code_table, net, rent_table), …]`` for P&L rows."""
    pl = rows[rows["Type"] == "P&L"]
    blocks = []
    for prop in sorted(pl["Property"].astype(str).unique()):
        g = pl[pl["Property"] == prop]
        via_pm = g["Source Type"] == "PM Statement"
        table = (
            pd.DataFrame({"Account Code": g["Account Code"],
                          "Via property manager": g["Amount"].where(via_pm, 0.0),
                          "Via bank / other": g["Amount"].where(~via_pm, 0.0)})
            .groupby("Account Code").sum()
        )
        table["Total"] = table.sum(axis=1)
        table = table.round(2).sort_values("Total", ascending=False).reset_index()
        is_rent = via_pm & g["Description"].map(lambda d: bool(_RENT_LABEL_RE.search(pm_match_text(d))))
        rent = g[is_rent & (g["Amount"] > 0)]
        rent_table = (
            pd.DataFrame({"Manager": rent["Source File"].map(manager_label),
                          "Period": [_rent_period(d, s) for d, s in zip(rent["Date"], rent["Description"], strict=True)],
                          "Rent": rent["Amount"]})
            .groupby(["Manager", "Period"], sort=False)["Rent"].sum().round(2).reset_index()
        )
        blocks.append((prop, table, round(float(g["Amount"].sum()), 2), rent_table))
    return blocks


def write_per_property(ws, rows: pd.DataFrame, write_table: Callable) -> None:
    ws.cell(row=1, column=1, value="Per-property income & expenses (P&L items only)").font = _TITLE
    ws.cell(row=2, column=1, value="Rent is taken from the property-manager statements (gross, per house).")
    row = 4
    for prop, table, net, rent_table in property_blocks(rows):
        ws.cell(row=row, column=1, value=prop).font = _TITLE
        row = write_table(ws, table, row + 1, money_cols=_MONEY_COLS) - 1
        ws.cell(row=row, column=1, value=f"Net for {prop}").font = _BOLD
        cell = ws.cell(row=row, column=4, value=net)
        cell.font, cell.number_format = _BOLD, "#,##0.00;[Red]-#,##0.00"
        row += 2
        if not rent_table.empty:
            ws.cell(row=row, column=1, value=f"Rent for {prop} by manager and period").font = _BOLD
            row = write_table(ws, rent_table, row + 1, money_cols=_MONEY_COLS)
        row += 1


def recon_summary(recon: ReconResult | None) -> pd.DataFrame:
    if recon is None or not recon.managers:
        return pd.DataFrame(columns=["Manager", "Basis", "Statement paid to you", "Bank received", "Difference", "Status"])
    return pd.DataFrame([{
        "Manager": m.manager,
        "Basis": m.basis,
        "Statement paid to you": m.statement_total,
        "Bank received": m.bank_total,
        "Difference": m.difference,
        "Status": "✅ Matches" if m.ok else "⚠️ Difference — check",
        "Bank payee keywords": ", ".join(m.keywords),
        "Statements": "; ".join(m.files),
    } for m in recon.managers])


def _deposit_table(df: pd.DataFrame, manager: str | None = None) -> pd.DataFrame:
    cols = ["Date", "Amount", "Description", "Source File"]
    out = df[cols].copy() if not df.empty else pd.DataFrame(columns=cols)
    if manager is not None:
        out.insert(0, "Manager", manager)
    return out


def write_rent_reconciliation(ws, recon: ReconResult | None, write_table: Callable) -> None:
    ws.cell(row=1, column=1, value="Rent reconciliation — property-manager statements vs bank").font = _TITLE
    ws.cell(row=2, column=1, value="Rent in the P&L comes from the statements. These bank deposits are the managers' NET "
                                   "payouts to you and are NOT in the Transactions/Summary sheets.")
    row = write_table(ws, recon_summary(recon), 4, money_cols=_MONEY_COLS)
    if recon is None:
        return
    for m in recon.managers:
        if m.monthly is not None and not m.monthly.empty:
            ws.cell(row=row, column=1, value=f"{m.manager} — month by month (running totals)").font = _BOLD
            row = write_table(ws, m.monthly, row + 1, money_cols=_MONEY_COLS)
    matched = [_deposit_table(m.deposits, m.manager) for m in recon.managers if not m.deposits.empty]
    ws.cell(row=row, column=1, value="Bank deposits matched to a manager").font = _BOLD
    row = write_table(ws, pd.concat(matched, ignore_index=True) if matched else _deposit_table(pd.DataFrame(), ""),
                      row + 1, money_cols=_MONEY_COLS)
    ws.cell(row=row, column=1, value="Manager-looking deposits that matched no manager (check payee keywords)").font = _BOLD
    write_table(ws, _deposit_table(recon.unmatched), row + 1, money_cols=_MONEY_COLS)
