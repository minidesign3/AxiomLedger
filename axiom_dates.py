"""
axiom_dates
===========

One date format for exports. Every importer stores the Date column as the raw
string its source used (``05/09/25``, ``2025/09/05``, ``05/09/2025``,
``2025-09-05``, ``9 Mar 2026``, ``Sat 9 Apr`` …); mixing them in one CSV makes
Excel and the accountant misread rows.

* :func:`fy_bounds` — the financial year window (NZ 1 Apr–31 Mar, US calendar year).
* :func:`parse_any_date` — parse a single value, jurisdiction aware (NZ day-first,
  US month-first). Dates without a year are placed inside the financial year
  and reported as ``year_inferred``.
* :func:`normalize_date` — parse + format (NZ ``dd/mm/yyyy``, US ``mm/dd/yyyy``) and
  return an issue code: ``""`` | ``blank`` | ``unparsed`` | ``year_inferred`` | ``outside_fy``.

Pure functions only.
"""

from __future__ import annotations

import re
from datetime import date, datetime

ISSUE_BLANK = "blank"
ISSUE_UNPARSED = "unparsed"
ISSUE_YEAR_INFERRED = "year_inferred"
ISSUE_OUTSIDE_FY = "outside_fy"

_ISO_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$")
_NUMERIC_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})$")
_TEXT_FORMATS = ("%d %b %Y", "%d %B %Y", "%a %d %b %Y", "%A %d %B %Y", "%b %d %Y", "%B %d %Y", "%d-%b-%Y", "%d-%b-%y")
_NO_YEAR_FORMATS = ("%a %d %b", "%A %d %B", "%d %b", "%d %B", "%b %d", "%B %d")


def fy_bounds(tax_year: object, jurisdiction: str = "NZ") -> tuple[date, date]:
    """Financial year window: NZ 2026 -> 1 Apr 2025..31 Mar 2026; US 2026 -> calendar 2026."""
    year = int(str(tax_year).strip()[:4])
    if jurisdiction == "US":
        return date(year, 1, 1), date(year, 12, 31)
    return date(year - 1, 4, 1), date(year, 3, 31)


def date_format_for(jurisdiction: str = "NZ") -> str:
    return "%m/%d/%Y" if jurisdiction == "US" else "%d/%m/%Y"


def _numeric(text: str, jurisdiction: str) -> date | None:
    match = _NUMERIC_RE.match(text)
    if not match:
        return None
    a, b, yr = int(match.group(1)), int(match.group(2)), match.group(3)
    year = int(yr) + 2000 if len(yr) == 2 else int(yr)
    day, month = (b, a) if jurisdiction == "US" else (a, b)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(",", " ")).strip()


def _infer_year(parsed: datetime, fy: tuple[date, date] | None) -> date:
    if fy is None:
        return date(date.today().year, parsed.month, parsed.day)
    start, end = fy
    for year in (start.year, end.year):
        try:
            candidate = date(year, parsed.month, parsed.day)
        except ValueError:
            continue
        if start <= candidate <= end:
            return candidate
    return date(end.year, parsed.month, parsed.day)


def parse_any_date(
    value: object,
    jurisdiction: str = "NZ",
    fy: tuple[date, date] | None = None,
) -> tuple[date | None, str]:
    """Return ``(date_or_None, issue)`` for one raw Date value."""
    if isinstance(value, datetime):
        return value.date(), ""
    if isinstance(value, date):
        return value, ""
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none", "null"):
        return None, ISSUE_BLANK
    iso = _ISO_RE.match(text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))), ""
        except ValueError:
            return None, ISSUE_UNPARSED
    numeric = _numeric(text, jurisdiction)
    if numeric:
        return numeric, ""
    cleaned = _clean_text(text)
    for fmt in _TEXT_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date(), ""
        except ValueError:
            continue
    for fmt in _NO_YEAR_FORMATS:
        try:
            # Parse with a leap-year placeholder so '29 Feb' never fails here.
            parsed = datetime.strptime(f"2000 {cleaned}", f"%Y {fmt}")
        except ValueError:
            continue
        return _infer_year(parsed, fy), ISSUE_YEAR_INFERRED
    return None, ISSUE_UNPARSED


def normalize_date(
    value: object,
    jurisdiction: str = "NZ",
    fy: tuple[date, date] | None = None,
) -> tuple[str, str]:
    """Return ``(formatted_date, issue)``; unparsed/blank values keep their raw text."""
    parsed, issue = parse_any_date(value, jurisdiction, fy)
    if parsed is None:
        return ("" if value is None else str(value).strip()), issue
    if not issue and fy is not None and not (fy[0] <= parsed <= fy[1]):
        issue = ISSUE_OUTSIDE_FY
    return parsed.strftime(date_format_for(jurisdiction)), issue
