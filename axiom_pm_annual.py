"""
axiom_pm_annual
===============

Parsers for property-manager *annual* statements that list one yearly total
per account per property (no individual transactions):

* **Reapit 'Financial Year Statement'** (e.g. Barfoot & Thompson) — columns
  ``INCLUDED GST | EXPENSE | INCOME`` under a per-property "Property Breakdown".
* **'Folio Summary'** (e.g. Ray White / PropertyMe-style) — columns
  ``Included Tax | Money Out | Money In`` with a ``Subtotal`` per property and
  a trailing ``Account Transactions`` block not tied to a property.

Whether an amount is money in or out is not recoverable from the text alone
("Council Rates $182.64 $1,400.29" vs "Water Usage Paid by Tenant $127.54
$977.65"), so pages are read as positioned words and each amount is assigned
to the column header whose right edge is nearest (amounts are right-aligned).

Rows are dated at the statement period end, money out negative / money in
positive, with the included GST recorded in the description.
"""

from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY
from axiom_pm_statements import Check, PropertyResolver, _default_property_resolver, attach_check

# A page is a list of lines; a line is a list of (text, x1) words in reading order.
Word = tuple[str, float]
Line = list[Word]

_AMOUNT_RE = re.compile(r"^-?\$[\d,]+\.\d{2}$")
_REAPIT_HEADER = "included gst expense income"
_FOLIO_HEADER = "included tax money out money in"
_GENERAL_PROPERTY = "General / Portfolio"


def line_text(line: Line) -> str:
    return " ".join(text for text, _ in line)


def words_to_lines(words: list[dict], tolerance: float = 2.0) -> list[Line]:
    """Group pdfplumber ``extract_words()`` dicts into lines by vertical position."""
    rows: list[tuple[float, list[dict]]] = []
    for word in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1][0] - word["top"]) <= tolerance:
            rows[-1][1].append(word)
        else:
            rows.append((word["top"], [word]))
    return [[(w["text"], float(w["x1"])) for w in sorted(ws, key=lambda w: w["x0"])] for _, ws in rows]


def detect_annual_layout(pages: list[list[Line]]) -> str | None:
    text = "\n".join(line_text(ln) for page in pages for ln in page).lower()
    if "financial year statement" in text and _REAPIT_HEADER in text:
        return "reapit_financial_year"
    if "folio summary" in text and _FOLIO_HEADER in text:
        return "folio_summary"
    return None


def _header_columns(line: Line) -> dict[str, float] | None:
    """Map 'tax' / 'out' / 'in' to the right edge (x1) of their header word."""
    low = [text.lower() for text, _ in line]
    joined = " ".join(low)
    if _REAPIT_HEADER in joined:
        names = {"gst": "tax", "expense": "out", "income": "in"}
        return {names[t]: x1 for (t, x1) in zip(low, (x for _, x in line), strict=True) if t in names}
    if _FOLIO_HEADER in joined:
        cols: dict[str, float] = {}
        for idx, (t, x1) in enumerate(zip(low, (x for _, x in line), strict=True)):
            if t == "tax":
                cols["tax"] = x1
            elif t in ("out", "in") and idx > 0 and low[idx - 1] == "money":
                cols[t] = x1
        return cols if len(cols) == 3 else None
    return None


def _split_amounts(line: Line, cols: dict[str, float]) -> tuple[str, dict[str, float]]:
    """Return (label, {column: amount}) assigning each amount to the nearest column."""
    label_parts: list[str] = []
    amounts: dict[str, float] = {}
    for text, x1 in line:
        if _AMOUNT_RE.match(text):
            col = min(cols, key=lambda c: abs(cols[c] - x1))
            amounts[col] = float(text.replace("$", "").replace(",", ""))
        elif text != "*":
            label_parts.append(text)
    return " ".join(label_parts).strip(), amounts


def _period_end(pages: list[list[Line]]) -> str:
    text = "\n".join(line_text(ln) for page in pages for ln in page)
    match = re.search(r"To:\s*(\d{1,2}/\d{1,2}/\d{4}|\d{1,2} [A-Za-z]+ \d{4})", text)
    if not match:
        return ""
    for fmt in ("%d/%m/%Y", "%d %B %Y"):
        try:
            return datetime.strptime(match.group(1), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _make_row(label: str, amounts: dict[str, float], address: str, prop: str, when: str, source: str) -> dict | None:
    if "out" in amounts and amounts["out"] != 0.0:
        amount = -abs(amounts["out"])
    elif "in" in amounts and amounts["in"] != 0.0:
        amount = abs(amounts["in"])
    else:
        return None
    parts = [f"{source}: {label}"]
    if amounts.get("tax"):
        parts.append(f"GST incl ${amounts['tax']:,.2f}")
    parts.append("Annual total")
    # Address intentionally omitted (Property column has it; address words misfired rules).
    return {"Date": when, "Amount": amount, "Description": " | ".join(parts), "Property": prop, "Account Code": ""}


def parse_reapit_financial_year(
    pages: list[list[Line]],
    resolve_property: PropertyResolver | None = None,
) -> pd.DataFrame:
    """Reapit statement: import only the per-property 'Property Breakdown' item rows.

    Every printed 'Income - Expenses = Net Position' line is checked: inside a property
    breakdown against that property's rows, elsewhere ('Total For All Properties')
    against all imported rows.
    """
    state: dict = {
        "resolver": resolve_property or _default_property_resolver,
        "when": _period_end(pages),
        # State persists across pages: a breakdown may continue onto the next page.
        "in_breakdown": False,
        "expect_address": False,
        "address": "",
        "prop": UNASSIGNED_PROPERTY,
        "cols": None,
        "net_scope": None,  # 'property' | 'all' while waiting for the Net Position amounts
        "all_scope": False,
        "prop_start": 0,
        "deferred": [],  # (label, expected_in, expected_out) checked once all rows are parsed
    }
    rows: list[dict] = []
    checks: list[Check] = []
    for page in pages:
        for line in page:
            _reapit_line(state, line, rows, checks)
    for label, exp_in, exp_out in state["deferred"]:
        checks.extend(_in_out_checks(label, exp_in, exp_out, rows))
    return attach_check(pd.DataFrame(rows, columns=STANDARD_COLUMNS), "reapit_financial_year", checks)


def _in_out_checks(label: str, exp_in: float, exp_out: float, rows: list[dict]) -> list[Check]:
    got_in = sum(r["Amount"] for r in rows if r["Amount"] > 0)
    got_out = -sum(r["Amount"] for r in rows if r["Amount"] < 0)
    return [(f"{label} money in", exp_in, got_in), (f"{label} money out", exp_out, got_out)]


def _reapit_net_position(state: dict, line: Line, rows: list[dict], checks: list[Check]) -> None:
    values = [float(t.replace("$", "").replace(",", "")) for t, _ in line if _AMOUNT_RE.match(t)]
    if len(values) < 2:
        return
    if state["net_scope"] == "property":
        label = f"Net Position {state['address']}".strip()
        checks.extend(_in_out_checks(label, values[0], values[1], rows[state["prop_start"] :]))
        state["prop_start"] = len(rows)
    else:
        state["deferred"].append(("Total For All Properties", values[0], values[1]))
    state["net_scope"] = None


def _reapit_line(state: dict, line: Line, rows: list[dict], checks: list[Check]) -> None:
    """Advance the Reapit state machine by one line."""
    text = line_text(line).strip()
    low = text.lower()
    if state["net_scope"]:
        _reapit_net_position(state, line, rows, checks)
    elif low.startswith("property breakdown"):
        state["in_breakdown"], state["all_scope"] = True, False
    elif low.startswith("total for all properties"):
        state["all_scope"], state["cols"] = True, None
    elif low.startswith("income expenses net position"):
        in_property = state["in_breakdown"] and not state["all_scope"]
        state["net_scope"], state["cols"] = ("property" if in_property else "all"), None
    elif state["in_breakdown"] and re.match(r"^#\d+ of \d+ propert", low):
        state["expect_address"], state["all_scope"] = True, False
    elif state["in_breakdown"] and state["expect_address"]:
        state["address"], state["prop"], state["expect_address"] = text, state["resolver"](text), False
    elif state["in_breakdown"] and _header_columns(line):
        state["cols"] = _header_columns(line)
    elif state["in_breakdown"] and state["cols"] and low != "item":
        label, amounts = _split_amounts(line, state["cols"])
        row = _make_row(label, amounts, state["address"], state["prop"], state["when"], "PM Financial Year")
        if row:
            rows.append(row)


def _folio_line(state: dict, line: Line, resolver: PropertyResolver, rows: list[dict], checks: list[Check]) -> None:
    """Advance the Folio Summary state machine by one line.

    Printed Subtotals are checked against the rows since the previous subtotal and the
    final 'Total' against all rows.
    """
    text = line_text(line).strip()
    low = text.lower()
    header = _header_columns(line)
    if header:
        state["cols"] = header
        state["expect_heading"] = state["address"] == ""
        return
    cols = state["cols"]
    if not cols:
        return
    label, amounts = _split_amounts(line, cols)
    if low.startswith("total"):
        if "out" in amounts and "in" in amounts:
            checks.extend(_in_out_checks("Total", amounts["in"], amounts["out"], rows))
        state["cols"] = None
        return
    # The 'Subtotal' label is rendered slightly off the amounts' baseline, so it can
    # arrive as its own line and the amounts as an unlabelled line: both are subtotals.
    if low.startswith("subtotal") or (amounts and not label and not state["pending_label"]):
        if amounts:
            block_label = f"Subtotal {state['address'] or 'Account Transactions'}"
            block = rows[state["block_start"] :]
            checks.extend(_in_out_checks(block_label, amounts.get("in", 0.0), amounts.get("out", 0.0), block))
            state["block_start"] = len(rows)
        state["expect_heading"] = True
        return
    if not amounts:
        if state["expect_heading"]:
            general = low == "account transactions"
            state["address"] = "" if general else text
            state["prop"] = _GENERAL_PROPERTY if general else resolver(text)
            state["expect_heading"] = False
        else:
            state["pending_label"] = f"{state['pending_label']} {text}".strip()
        return
    full_label = f"{state['pending_label']} {label}".strip()
    state["pending_label"] = ""
    row = _make_row(full_label, amounts, state["address"], state["prop"], state["when"], "PM Folio Summary")
    if row:
        rows.append(row)


def parse_folio_summary(
    pages: list[list[Line]],
    resolve_property: PropertyResolver | None = None,
) -> pd.DataFrame:
    """Folio Summary: per-property account totals; 'Account Transactions' -> General / Portfolio."""
    resolver = resolve_property or _default_property_resolver
    state: dict = {
        "cols": None,
        "address": "",
        "prop": UNASSIGNED_PROPERTY,
        "expect_heading": True,
        "pending_label": "",
        "when": _period_end(pages),
        "block_start": 0,
    }
    rows: list[dict] = []
    checks: list[Check] = []
    for page in pages:
        for line in page:
            _folio_line(state, line, resolver, rows, checks)
    return attach_check(pd.DataFrame(rows, columns=STANDARD_COLUMNS), "folio_summary", checks)


def parse_annual_statement_lines(
    pages: list[list[Line]],
    resolve_property: PropertyResolver | None = None,
) -> pd.DataFrame | None:
    layout = detect_annual_layout(pages)
    if layout == "reapit_financial_year":
        return parse_reapit_financial_year(pages, resolve_property)
    if layout == "folio_summary":
        return parse_folio_summary(pages, resolve_property)
    return None
