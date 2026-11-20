"""
axiom_accountant_pack
=====================

One Excel file for the accountant (openpyxl):

* **Summary** — entity, financial year, readiness status, then P&L totals per
  Account Code × Property (with totals), then Not-P&L items (transfers,
  drawings, principal, tax payments …) by code.
* **Per Property** / **Rent Reconciliation** — see ``axiom_pack_sheets``.
* **Transactions** — every exported row: Date (one format), Amount,
  Description, Property, Account Code, Type, Source File, Source Type.
* **Checks** — the readiness checklist (blockers / warnings / info).
* **Excluded** — rows left out of the export and why.

:func:`build_pack_bytes` is pure (takes an :class:`ExportResult`);
:func:`build_accountant_pack` is the app wrapper.
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from axiom_dates import parse_any_date
from axiom_export_checks import ExportResult
from axiom_pack_sheets import write_per_property, write_rent_reconciliation

_BOLD = Font(bold=True)
_HEAD_FILL = PatternFill("solid", fgColor="DDE3EA")
_LEVEL_FILL = {
    "blocker": PatternFill("solid", fgColor="F8D7DA"),
    "warning": PatternFill("solid", fgColor="FFF3CD"),
    "info": PatternFill("solid", fgColor="E2E8F0"),
}
_MONEY = '#,##0.00;[Red]-#,##0.00'


def _write_table(
    ws,
    df: pd.DataFrame,
    start_row: int = 1,
    money_cols: tuple[str, ...] = (),
    jurisdiction: str | None = None,
) -> int:
    """Write a header + rows; returns the next free row.

    With ``jurisdiction`` set, a ``Date`` column is written as real Excel dates
    (dd/mm/yyyy NZ, mm/dd/yyyy US) so the accountant can sort/filter them.
    """
    cols = [str(c) for c in df.columns]
    date_fmt = "mm/dd/yyyy" if jurisdiction == "US" else "dd/mm/yyyy"
    for j, name in enumerate(cols, start=1):
        cell = ws.cell(row=start_row, column=j, value=name)
        cell.font, cell.fill = _BOLD, _HEAD_FILL
    for i, values in enumerate(df.itertuples(index=False), start=start_row + 1):
        for j, value in enumerate(values, start=1):
            if jurisdiction and cols[j - 1] == "Date":
                parsed, _ = parse_any_date(value, jurisdiction)
                value = parsed if parsed is not None else value
            cell = ws.cell(row=i, column=j, value=None if pd.isna(value) else value)
            if cols[j - 1] in money_cols:
                cell.number_format = _MONEY
            elif jurisdiction and cols[j - 1] == "Date" and parsed is not None:
                cell.number_format = date_fmt
    return start_row + len(df) + 2


def _autosize(ws, max_width: int = 60) -> None:
    for col_cells in ws.columns:
        width = max((len(str(c.value)) for c in col_cells if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(max(10, width + 2), max_width)


def _pl_pivot(rows: pd.DataFrame) -> pd.DataFrame:
    pl = rows[rows["Type"] == "P&L"]
    if pl.empty:
        return pd.DataFrame(columns=["Account Code"])
    pivot = pl.pivot_table(index="Account Code", columns="Property", values="Amount", aggfunc="sum",
                           fill_value=0.0, margins=True, margins_name="Total")
    return pivot.round(2).reset_index()


def _write_summary(ws, result: ExportResult, entity: str, tax_year: str, period: str) -> None:
    status = "READY — no blocking issues" if result.ready else f"NOT READY — {len(result.blockers)} blocking issue(s), see Checks"
    header = [("Entity", entity), ("Financial year", f"{tax_year} ({period})"), ("Status", status),
              ("Transactions", len(result.rows)), ("Generated", datetime.now().strftime("%d/%m/%Y %H:%M"))]
    for i, (k, v) in enumerate(header, start=1):
        ws.cell(row=i, column=1, value=k).font = _BOLD
        ws.cell(row=i, column=2, value=v)
    row = len(header) + 2
    ws.cell(row=row, column=1, value="Income & expenses (P&L) by Account Code and Property").font = _BOLD
    pivot = _pl_pivot(result.rows)
    row = _write_table(ws, pivot, row + 1, money_cols=tuple(c for c in pivot.columns if c != "Account Code"))
    ws.cell(row=row, column=1, value="Not P&L (transfers, drawings, principal, tax payments …)").font = _BOLD
    non_pl = result.rows[result.rows["Type"] == "Not P&L"]
    by_code = (non_pl.groupby("Account Code")["Amount"].agg(["count", "sum"]).round(2).reset_index()
               .rename(columns={"count": "Transactions", "sum": "Total"}))
    _write_table(ws, by_code, row + 1, money_cols=("Total",))
    _autosize(ws)


def build_pack_bytes(
    result: ExportResult,
    entity: str,
    tax_year: str,
    period: str = "",
    jurisdiction: str = "NZ",
) -> bytes:
    """Render the four-sheet workbook for ``result``."""
    wb = Workbook()
    _write_summary(wb.active, result, entity, tax_year, period)
    wb.active.title = "Summary"
    ws_tx = wb.create_sheet("Transactions")
    _write_table(ws_tx, result.rows, money_cols=("Amount",), jurisdiction=jurisdiction)
    ws_tx.freeze_panes = "A2"
    _autosize(ws_tx)
    ws_checks = wb.create_sheet("Checks")
    checks_df = pd.DataFrame([{"Level": c.level.upper(), "Check": c.name, "Rows": c.count, "Detail": c.detail}
                              for c in result.checks] or [{"Level": "OK", "Check": "All checks passed", "Rows": 0, "Detail": ""}])
    _write_table(ws_checks, checks_df)
    for i, level in enumerate(checks_df["Level"], start=2):
        fill = _LEVEL_FILL.get(str(level).lower())
        if fill:
            for j in range(1, 5):
                ws_checks.cell(row=i, column=j).fill = fill
    _autosize(ws_checks, max_width=110)
    ws_prop = wb.create_sheet("Per Property", 1)
    write_per_property(ws_prop, result.rows, _write_table)
    _autosize(ws_prop)
    ws_recon = wb.create_sheet("Rent Reconciliation", 2)
    write_rent_reconciliation(ws_recon, result.recon, _write_table)
    _autosize(ws_recon, max_width=80)
    ws_ex = wb.create_sheet("Excluded")
    _write_table(ws_ex, result.excluded, money_cols=("Amount",), jurisdiction=jurisdiction)
    _autosize(ws_ex)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def pack_filename(entity: str, tax_year: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in entity).strip("_")
    return f"{safe}_FY{tax_year}_Accountant_Pack.xlsx"


def build_accountant_pack(entity: str, tax_year: str, include_overlaps: bool = False) -> tuple[bytes, ExportResult]:
    """App wrapper: prepare the export and render the workbook."""
    from axiom_export_checks import prepare_export
    from axiom_state import format_tax_year_period, get_entity_jurisdiction

    result = prepare_export(entity, tax_year, include_overlaps=include_overlaps)
    period = format_tax_year_period(tax_year, entity)
    return build_pack_bytes(result, entity, tax_year, period, get_entity_jurisdiction(entity)), result
