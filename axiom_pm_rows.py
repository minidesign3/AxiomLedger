"""
axiom_pm_rows
=============

Helpers for ledger rows imported from property-manager (PM) statements.

PM rows have a Description whose first ``|`` segment is the PM's own category,
e.g. ``PM: Water Care Services | Water Care Services 02 Sep 2025 | Ref … | PM Txn …``
or ``PM Folio Summary: Council Rates | GST incl $182.64 | Annual total``.

* Rules are matched against that category segment only, so bank-learned rules
  and generic keywords can't misfire on PM detail text (e.g. a ``City`` rule
  hitting "City Centre", or ``tenancy`` hitting "Tenancy Portion").
* A confirmed PM row teaches one rule per category (no property: the statement
  supplies the property), so one confirm codes every row of that category,
  across properties and years.
* :func:`detect_pm_double_count` warns when PM gross rent and bank rows coded as
  rent income coexist in one ledger (managers deposit *net* rent to the bank).

Pure functions only — no Streamlit or store access.
"""

from __future__ import annotations

import re

import pandas as pd

_PM_ROW_RE = re.compile(r"^PM(?: [A-Za-z][A-Za-z ]*)?: \S")
_SEGMENT_RE = re.compile(r"\s*\|\s*")
_RENT_LABEL_RE = re.compile(r"\brent(al)?\b", re.IGNORECASE)


def is_pm_row(description: object) -> bool:
    """True if a Description was produced by a PM statement parser."""
    if description is None or (isinstance(description, float) and pd.isna(description)):
        return False
    return bool(_PM_ROW_RE.match(str(description).strip()))


def pm_match_text(description: object) -> str:
    """Text the rules engine should match: the PM category for PM rows, else the full line."""
    text = "" if description is None or (isinstance(description, float) and pd.isna(description)) else str(description)
    if not is_pm_row(text):
        return text
    return _SEGMENT_RE.split(text.strip(), maxsplit=1)[0]


def pm_rule_pattern(pattern: str) -> tuple[str, bool]:
    """Return ``(pattern_to_memorize, is_pm_row)`` — the category segment for PM rows."""
    if is_pm_row(pattern):
        return pm_match_text(pattern), True
    return pattern, False


def detect_pm_double_count(df: pd.DataFrame) -> str | None:
    """Warn if PM gross rent and bank rows coded as rent income are both in the ledger."""
    if not isinstance(df, pd.DataFrame) or df.empty or "Description" not in df.columns:
        return None
    desc = df["Description"].fillna("").astype(str)
    amount = pd.to_numeric(df["Amount"], errors="coerce").fillna(0.0)
    code = df["Account Code"].fillna("").astype(str) if "Account Code" in df.columns else pd.Series("", index=df.index)
    pm_mask = desc.map(is_pm_row)
    pm_rent = pm_mask & (amount > 0) & desc.map(lambda d: bool(_RENT_LABEL_RE.search(pm_match_text(d))))
    bank_rent = ~pm_mask & (amount > 0) & code.str.contains(r"\brent", case=False, regex=True)
    if not pm_rent.any() or not bank_rent.any():
        return None
    return (
        f"⚠️ Possible double-counted rent: property-manager statements show ${amount[pm_rent].sum():,.2f} "
        f"gross rent AND {int(bank_rent.sum())} bank row(s) are coded as rent income "
        f"(${amount[bank_rent].sum():,.2f}). Managers deposit NET rent, so if those bank rows are manager "
        "payouts, code them as a transfer (or leave the PM rent lines out). Confirm with your accountant."
    )
