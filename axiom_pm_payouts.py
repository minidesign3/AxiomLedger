"""
axiom_pm_payouts
================

What each property-manager statement says was **paid to the owner**, so it can
be checked against the deposits that actually reached the bank.

* CSI-style **Year End Summary** (Charlton, Ray White Karaka): the monthly
  ``Payments`` column per property → one payout per property per month.
* **Folio Summary** (Ray White) and Reapit **Financial Year Statement**
  (Barfoot): yearly only → money in − money out per property (these are
  exactly the imported rows, already checked against the statement totals).
* **Expense Report**: no payouts (it lists invoices only).

A payout is a dict ``{"month": "YYYY-MM" | None, "property": str, "amount": float}``
(``month=None`` = whole-year figure).
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pandas as pd

import axiom_pm_statements as pm

MONTHLY = "Monthly"
ANNUAL = "Annual"

_CACHE: dict[str, tuple[str | None, list[dict]]] = {}


def payouts_from_summary_pages(pages: list[str], resolver: Callable[[str], str]) -> list[dict]:
    """Monthly owner payouts from a CSI 'Year End Summary' (page texts)."""
    payouts: list[dict] = []
    for page in pages:
        lines = [ln.strip() for ln in page.splitlines() if ln.strip()]
        address = pm._summary_page_address(lines)
        prop = resolver(address) if address else pm.UNASSIGNED_PROPERTY
        for line in lines:
            match = pm._SUMMARY_ROW_RE.match(line)
            if not match or match.group(2).lower() not in pm._MONTHS:
                continue
            values = pm._split_values(match.group(3))
            if values["Payments"] == 0.0:
                continue
            month = f"{match.group(1)}-{pm._MONTHS[match.group(2).lower()]:02d}"
            payouts.append({"month": month, "property": prop, "amount": round(values["Payments"], 2)})
    return payouts


def payouts_from_rows(rows: pd.DataFrame) -> list[dict]:
    """Yearly owner payout per property = net of the statement's imported rows."""
    if rows is None or rows.empty:
        return []
    net = rows.groupby("Property")["Amount"].sum().round(2)
    return [{"month": None, "property": str(p), "amount": float(a)} for p, a in net.items() if a != 0.0]


def payouts_from_pdf(pdf_bytes: bytes, resolver: Callable[[str], str] | None = None) -> tuple[str | None, list[dict]]:
    """``(basis, payouts)`` for one PDF; basis is MONTHLY, ANNUAL or None (no payouts)."""
    import pdfplumber

    from axiom_dedupe import content_hash

    key = content_hash(pdf_bytes)
    if resolver is None and key in _CACHE:
        return _CACHE[key]
    resolve = resolver or pm._default_property_resolver
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [(page.extract_text() or "") for page in pdf.pages]
    layout = pm.detect_pm_layout(pages)
    if layout == "year_end_summary":
        result: tuple[str | None, list[dict]] = (MONTHLY, payouts_from_summary_pages(pages, resolve))
    elif layout == "expense_report":
        result = (None, [])
    else:
        parsed = pm.parse_pm_statement_pdf(pdf_bytes)
        result = (ANNUAL, payouts_from_rows(parsed)) if parsed is not None and not parsed.empty else (None, [])
    if resolver is None:
        _CACHE[key] = result
    return result


def manager_label(file_name: str) -> str:
    """Manager name from a statement file name: 'Charlton - Financial Summary…' -> 'Charlton'."""
    stem = Path(str(file_name)).stem
    return stem.split(" - ")[0].strip() if " - " in stem else stem.strip()


def statement_payouts(records: list[dict[str, object]], read_bytes: Callable[[dict], bytes | None]) -> dict[str, dict]:
    """Per manager: ``{"basis": …, "payouts": [...], "files": [...]}`` from all PDF records."""
    out: dict[str, dict] = {}
    for rec in records:
        if str(rec.get("bucket")) != "pdf":
            continue
        data = read_bytes(rec)
        if not data:
            continue
        try:
            basis, payouts = payouts_from_pdf(data)
        except Exception:
            continue
        if not basis or not payouts:
            continue
        label = manager_label(str(rec.get("name", "")))
        entry = out.setdefault(label, {"basis": basis, "payouts": [], "files": []})
        if basis == MONTHLY:
            entry["basis"] = MONTHLY
        entry["payouts"].extend(payouts)
        entry["files"].append(str(rec.get("name", "")))
    return out
