"""
axiom_pm_statements
===================

Text-layout parsers for property-manager (PM) owner statements that have no
ruled tables, so ``pdfplumber.extract_tables()`` finds nothing in them
(e.g. Charlton Property Management exports).

Two layouts are recognised from the page text:

* **Property Expense Report** — one row per invoice::

      1137103 27/11/2025 INV00372 A-One Electrical ... -287.50
      Creditor: A-One Electrical Services Ltd )      <- wrapped continuation
      Appliance Repairs -287.50                       <- category subtotal
      Property Total -4,209.42

  Rows are grouped under a property address heading and followed by a PM
  category subtotal; subtotals are detected by matching the group sum, so a
  wrapped line that happens to end in a number is never mistaken for one.

* **Year End Summary** — monthly per-property rollups. Only Rent, Tenant
  Reimbursements, Fees and GST on fees are imported. ``Invoices`` duplicates
  the Expense Report and ``Payments`` / ``Intro. Funds`` are owner transfers,
  so those columns are skipped to avoid double counting.

Both parsers return a ``STANDARD_COLUMNS`` frame (already normalised), leaving
``Account Code`` blank so the entity's Memorized / Keyword rules assign codes.
"""

from __future__ import annotations

import calendar
import io
import re
from collections.abc import Callable
from datetime import date

import pandas as pd

from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY

_AMOUNT = r"-?\$?[\d,]+\.\d{2}"
_TXN_RE = re.compile(rf"^(\d{{5,}})\s+(\d{{1,2}}/\d{{1,2}}/\d{{4}})\s+(\S+)\s+(.*?)\s*({_AMOUNT})$")
_LABEL_AMOUNT_RE = re.compile(rf"^(.*\S)\s+({_AMOUNT})$")
_TOTAL_RE = re.compile(rf"^(Property|Report) Total\s+({_AMOUNT})$", re.IGNORECASE)
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_SUMMARY_ROW_RE = re.compile(r"^(\d{4})\s+([A-Za-z]{3})\s+((?:-?\$?-?[\d,]+\.\d{2}\s*){8})$")
_SUMMARY_TOTALS_RE = re.compile(r"^Totals\s+((?:-?\$?-?[\d,]+\.\d{2}\s*){8})$", re.IGNORECASE)
_SUMMARY_COLUMNS = ("Rent", "Intro. Funds", "T. Reimburse", "Fees", "GST", "Invoices", "Payments", "Net")
# Summary column -> (description label, sign applied to the owner's ledger)
_SUMMARY_IMPORT = {
    "Rent": ("Rent", 1.0),
    "T. Reimburse": ("Tenant Reimbursement", 1.0),
    "Fees": ("Management Fees", -1.0),
    "GST": ("GST on Management Fees", -1.0),
}
_EXPENSE_NOISE_PREFIXES = (
    "trans. date",
    "transactions between",
    "owner:",
    "property expense report",
)
_FOOTER_RE = re.compile(r"(page \d+ of \d+$)|(^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M)", re.IGNORECASE)

PropertyResolver = Callable[[str], str]
# (label, total printed on the statement, total of the rows we parsed)
Check = tuple[str, float, float]

PDF_NO_TABLE_NOTE = "Saved PDF to Vault. No structured table found automatically."
CHECK_ATTR = "pdf_check"
_LAYOUT_NAMES = {
    "expense_report": "PM Expense Report",
    "year_end_summary": "PM Year End Summary",
    "reapit_financial_year": "PM Financial Year Statement",
    "folio_summary": "PM Folio Summary",
    "generic_tables": "Unrecognised PDF layout",
}


def attach_check(df: pd.DataFrame, layout: str, checks: list[Check]) -> pd.DataFrame:
    """Record the layout and reconciliation checks on ``df.attrs`` for the caller's note."""
    failed = [c for c in checks if abs(c[1] - c[2]) >= 0.005]
    df.attrs[CHECK_ATTR] = {"layout": layout, "checks": len(checks), "failed": failed}
    return df


def describe_pdf_check(df: pd.DataFrame) -> str:
    """One-line file note: ✅ when the rows match the statement's printed totals, ⚠️ otherwise.

    Notes starting with '⚠️' are shown in red on the file board.
    """
    if df is None or df.empty:
        return PDF_NO_TABLE_NOTE
    info = df.attrs.get(CHECK_ATTR) or {"layout": "generic_tables", "checks": 0, "failed": []}
    name = _LAYOUT_NAMES.get(info["layout"], info["layout"])
    n_rows = len(df)
    if info["layout"] == "generic_tables":
        return f"⚠️ {name} · {n_rows} rows read by the generic table reader — verify against the original PDF."
    if info["failed"]:
        label, expected, actual = info["failed"][0]
        more = f" (+{len(info['failed']) - 1} more)" if len(info["failed"]) > 1 else ""
        return (
            f"⚠️ {name} · {n_rows} rows · totals DON'T match the statement — {label}: "
            f"statement {expected:,.2f} vs imported {actual:,.2f}{more}. Check against the original PDF."
        )
    if not info["checks"]:
        return f"⚠️ {name} · {n_rows} rows · no statement totals found to verify against — double-check."
    return f"✅ {name} · {n_rows} rows · matches the statement's own totals ({info['checks']} checks)"


def _to_float(raw: str) -> float:
    return float(raw.replace("$", "").replace(",", ""))


def _iso_date(dmy: str) -> str:
    day, month, year = (int(p) for p in dmy.split("/"))
    return date(year, month, day).isoformat()


def _default_property_resolver(address: str) -> str:
    """Map a PM address heading to a configured entity property (or Unassigned).

    Match on the street part first: suburbs can collide with another property's
    name (e.g. '9 Damien Way, Weymouth' vs the '303 Weymouth' property).
    """
    try:
        from axiom_account_codes import detect_property_from_text

        street = address.split(",")[0].strip()
        found = detect_property_from_text(street) if street else UNASSIGNED_PROPERTY
        return found if found != UNASSIGNED_PROPERTY else detect_property_from_text(address)
    except Exception:
        return UNASSIGNED_PROPERTY


def detect_pm_layout(pages: list[str]) -> str | None:
    """Return ``'expense_report'``, ``'year_end_summary'`` or ``None`` for unknown text."""
    head = "\n".join(pages[:1]).lower()
    if "property expense report" in head and "trans. date" in head:
        return "expense_report"
    if "year end summary" in head and "year / month" in head:
        return "year_end_summary"
    return None


def _is_noise_line(line: str, pm_name: str) -> bool:
    low = line.lower()
    if pm_name and low == pm_name.lower():
        return True
    return low.startswith(_EXPENSE_NOISE_PREFIXES) or bool(_FOOTER_RE.search(line))


def _flush_group(group: list[dict], category: str, out: list[dict]) -> None:
    for txn in group:
        txn["category"] = category
        out.append(txn)
    group.clear()


def _parse_expense_lines(lines: list[str]) -> tuple[list[dict], list[Check]]:
    """State machine over cleaned lines -> transaction dicts and reconciliation checks.

    Every printed 'Property Total' / 'Report Total' becomes a check against the sum of
    the rows parsed for that block, so a missed or misread line is reported.
    """
    out: list[dict] = []
    group: list[dict] = []
    checks: list[Check] = []
    address = ""
    expect_heading = True
    prop_start = 0

    for line in lines:
        total_m = _TOTAL_RE.match(line)
        if total_m:
            _flush_group(group, "", out)
            expected = _to_float(total_m.group(2))
            if total_m.group(1).lower() == "report":
                checks.append(("Report Total", expected, sum(t["amount"] for t in out)))
            else:
                block = out[prop_start:]
                checks.append((f"Property Total {address}".strip(), expected, sum(t["amount"] for t in block)))
                prop_start = len(out)
            expect_heading = True
            continue
        txn_m = _TXN_RE.match(line)
        if txn_m:
            txn_id, dmy, ref, desc, amt = txn_m.groups()
            group.append(
                {"id": txn_id, "date": _iso_date(dmy), "ref": ref, "desc": [desc], "amount": _to_float(amt), "address": address}
            )
            expect_heading = False
            continue
        label_m = _LABEL_AMOUNT_RE.match(line)
        if label_m and group and abs(sum(t["amount"] for t in group) - _to_float(label_m.group(2))) < 0.005:
            _flush_group(group, label_m.group(1).strip(), out)
            continue
        if expect_heading and not group:
            address = line.strip()
            expect_heading = False
            continue
        if group:
            group[-1]["desc"].append(line.strip())
    _flush_group(group, "", out)
    return out, checks


def _expense_rows(txns: list[dict], resolve_property: PropertyResolver) -> list[dict]:
    rows: list[dict] = []
    for txn in txns:
        desc = " ".join(part for part in txn["desc"] if part).strip()
        # PM category first: rules match/learn on it (see axiom_pm_rows). The address is
        # left out on purpose — the Property column holds it, and address words
        # ("City Centre") made bank-learned rules misfire.
        head = f"PM: {txn['category']}" if txn["category"] else f"PM Expense: {desc}"
        parts = [head, desc, f"Ref {txn['ref']}", f"PM Txn {txn['id']}"]
        rows.append(
            {
                "Date": txn["date"],
                "Amount": txn["amount"],
                "Description": " | ".join(parts),
                "Property": resolve_property(txn["address"]) if txn["address"] else UNASSIGNED_PROPERTY,
                "Account Code": "",
            }
        )
    return rows


def _clean_lines(pages: list[str], pm_name: str) -> list[str]:
    lines: list[str] = []
    for page in pages:
        for raw in page.splitlines():
            line = raw.strip()
            if line and not _is_noise_line(line, pm_name):
                lines.append(line)
    return lines


def parse_expense_report_pages(
    pages: list[str],
    resolve_property: PropertyResolver | None = None,
) -> pd.DataFrame:
    """Parse a PM 'Property Expense Report' (page texts) into STANDARD_COLUMNS rows."""
    pm_name = pages[0].strip().splitlines()[0].strip() if pages and pages[0].strip() else ""
    txns, checks = _parse_expense_lines(_clean_lines(pages, pm_name))
    rows = _expense_rows(txns, resolve_property or _default_property_resolver)
    return attach_check(pd.DataFrame(rows, columns=STANDARD_COLUMNS), "expense_report", checks)


def _summary_page_address(lines: list[str]) -> str:
    for idx, line in enumerate(lines):
        if line.strip().lower() == "property" and idx + 1 < len(lines):
            return lines[idx + 1].strip()
    return ""


def _month_end(year: int, month: int) -> str:
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def parse_year_end_summary_pages(
    pages: list[str],
    resolve_property: PropertyResolver | None = None,
) -> pd.DataFrame:
    """Parse a PM 'Year End Summary' into monthly Rent / Reimbursement / Fees / GST rows.

    Each page's printed 'Totals' line is checked column-by-column against the sum of
    the month rows parsed, so a skipped or misread month is reported.
    """
    resolver = resolve_property or _default_property_resolver
    rows: list[dict] = []
    checks: list[Check] = []
    for page in pages:
        _parse_summary_page(page, resolver, rows, checks)
    return attach_check(pd.DataFrame(rows, columns=STANDARD_COLUMNS), "year_end_summary", checks)


def _split_values(raw: str) -> dict[str, float]:
    return dict(zip(_SUMMARY_COLUMNS, (_to_float(v) for v in raw.split()), strict=True))


def _parse_summary_page(page: str, resolver: PropertyResolver, rows: list[dict], checks: list[Check]) -> None:
    lines = [ln.strip() for ln in page.splitlines() if ln.strip()]
    address = _summary_page_address(lines)
    prop = resolver(address) if address else UNASSIGNED_PROPERTY
    col_sums = dict.fromkeys(_SUMMARY_COLUMNS, 0.0)
    for line in lines:
        totals = _SUMMARY_TOTALS_RE.match(line)
        if totals:
            for col, expected in _split_values(totals.group(1)).items():
                checks.append((f"Totals {col} {address}".strip(), expected, col_sums[col]))
            continue
        match = _SUMMARY_ROW_RE.match(line)
        if not match or match.group(2).lower() not in _MONTHS:
            continue
        values = _split_values(match.group(3))
        for col, val in values.items():
            col_sums[col] += val
        when = _month_end(int(match.group(1)), _MONTHS[match.group(2).lower()])
        period = f"{match.group(1)} {match.group(2).title()}"
        for col, (label, sign) in _SUMMARY_IMPORT.items():
            if values[col] == 0.0:
                continue
            rows.append(
                {
                    "Date": when,
                    "Amount": round(sign * values[col], 2),
                    "Description": f"PM Year End Summary: {label} | {period}",
                    "Property": prop,
                    "Account Code": "",
                }
            )


def parse_pm_statement_pages(
    pages: list[str],
    resolve_property: PropertyResolver | None = None,
) -> pd.DataFrame | None:
    """Dispatch on detected layout; ``None`` means 'not a known PM statement'."""
    layout = detect_pm_layout(pages)
    if layout == "expense_report":
        return parse_expense_report_pages(pages, resolve_property)
    if layout == "year_end_summary":
        return parse_year_end_summary_pages(pages, resolve_property)
    return None


def parse_pm_statement_pdf(pdf_bytes: bytes) -> pd.DataFrame | None:
    """Open a PDF with pdfplumber and parse it if it is a known PM statement layout.

    Text layouts (CSI expense report / year end summary) are tried first, then the
    column-position-aware annual layouts in ``axiom_pm_annual``.
    """
    import pdfplumber

    # Function-body import: axiom_pm_annual imports this module at top level.
    from axiom_pm_annual import parse_annual_statement_lines, words_to_lines

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [(page.extract_text() or "") for page in pdf.pages]
        parsed = parse_pm_statement_pages(pages)
        if parsed is not None:
            return parsed
        line_pages = [words_to_lines(page.extract_words()) for page in pdf.pages]
    return parse_annual_statement_lines(line_pages)
