"""
axiom_loans
===========

Pure loan domain calculations extracted from the monolithic ``utils.py``.

Nothing in this module imports ``streamlit`` or touches the database — every
function here is a deterministic transformation of a plain loan `dict` (as
produced by the `property_loans` SQLite table / `_loan_row_to_dict`). This is
the loan-domain counterpart to ``axiom_core`` (generic pure helpers): row
normalization, expiry-milestone computation, rate-status badges, and the
one-line dossier summary used by the property setup matrix.

The Streamlit-coupled DB CRUD (`get_property_loans`, `save_property_loan`, ...),
the HTML card renderer (`_render_loan_card_html`), and the email/reminder
orchestration remain in `utils.py` (and `axiom_email.py`) for now — those are
candidates for a later, carefully-scoped slice. ``utils.py`` imports and
re-exports these symbols, so existing ``from utils import ...`` call sites keep
working unchanged.
"""

from __future__ import annotations

import re
from datetime import date

from axiom_core import (
    _fmt_loan_date,
    _parse_loan_date,
    _subtract_months,
    infer_nz_bank_from_account,
)


def _loan_row_to_dict(row) -> dict:
    """Normalize a raw `property_loans` SQL row into a dict with consistent bool/float/str types."""
    d = dict(row)
    for k in (
        "use_for_coding",
        "email_reminder_enabled",
        "remind_on_fixed_expiry",
        "remind_on_io_expiry",
        "remind_on_term_expiry",
        "next_payment_is_estimated",
    ):
        d[k] = bool(d.get(k))
    for k in (
        "original_drawdown_amount",
        "loan_limit",
        "current_balance",
        "available_redraw",
        "interest_rate_pct",
        "interest_accrued",
        "regular_repayment_amount",
    ):
        try:
            d[k] = abs(float(d.get(k) or 0.0))
        except Exception:
            d[k] = 0.0
    for k in (
        "account_holder",
        "account_product_type",
        "original_term_text",
        "remaining_term_text",
        "screenshot_vault_path",
    ):
        d[k] = str(d.get(k) or "")
    try:
        d["reminder_months_before"] = int(d.get("reminder_months_before") or 2)
    except Exception:
        d["reminder_months_before"] = 2
    return d


def get_loan_trigger_tokens(loan: dict, shared_customer_bases: set[str] | None = None) -> list[str]:
    """Derive the set of description-matching tokens (account-number variants + keywords) for a loan."""
    toks: list[str] = []

    def _add(t):
        t = str(t or "").strip().lower()
        if t and len(t) >= 4 and t not in toks:
            toks.append(t)

    acct = str(loan.get("account_number") or "").strip()
    if acct:
        _add(acct)
        _add(re.sub(r"[^a-z0-9]+", "", acct))
        _add(re.sub(r"[^a-z0-9]+", " ", acct))
        parts = [p for p in re.split(r"[-\s]+", acct) if p]
        if len(parts) == 4:
            # Standard NZ bank account BB-BBBB-AAAAAAA-SSS -> add customer+suffix (0618477-092, 618477-092)
            cust, sfx = parts[2], parts[3]
            _add(f"{cust}-{sfx}")
            _add(f"{cust}{sfx}")
            cust_trim = cust.lstrip("0")
            if len(cust_trim) >= 5:
                _add(f"{cust_trim}-{sfx}")
                _add(f"{cust_trim}{sfx}")
            if len(cust) >= 6 and (not shared_customer_bases or cust.lower() not in shared_customer_bases):
                _add(cust)
        elif len(parts) >= 2:
            # 2-part loan account e.g. 86131912-1003
            base = parts[0]
            if len(base) >= 5 and (not shared_customer_bases or base.lower() not in shared_customer_bases):
                _add(base)
    for kw in re.split(r"[,\n;]+", str(loan.get("coding_trigger_keywords") or "")):
        _add(kw)
    toks.sort(key=len, reverse=True)
    return toks


def loan_milestones(loan: dict) -> list[dict]:
    """Compute the enabled expiry milestones (fixed-rate, interest-only, final term) for a loan, with reminder windows."""
    out = []
    specs = [
        ("fixed_rate_expiry", "Fixed Rate Review / Expiry", loan.get("fixed_rate_review_date"), loan.get("remind_on_fixed_expiry")),
        ("interest_only_end", "Interest-Only End", loan.get("interest_only_end_date"), loan.get("remind_on_io_expiry")),
        ("loan_term_end", "Final Loan Term End", loan.get("loan_term_end_date"), loan.get("remind_on_term_expiry")),
    ]
    for mtype, label, raw, enabled in specs:
        d = _parse_loan_date(raw)
        if d:
            months = int(loan.get("reminder_months_before") or 2)
            out.append(
                {
                    "milestone_type": mtype,
                    "label": label,
                    "expiry_date": d,
                    "reminder_enabled_for_milestone": bool(enabled),
                    "window_start": _subtract_months(d, months),
                    "days_remaining": (d - date.today()).days,
                }
            )
    return out


def _loan_rate_status_tuple(loan: dict) -> tuple[str, str, int | None]:
    """Return (css_colour, status_label, days_to_fixed_expiry) for a loan dict."""
    fx = _parse_loan_date(loan.get("fixed_rate_review_date"))
    if fx is None:
        return "#8E939F", "No expiry set", None
    days = (fx - date.today()).days
    if days < 0:
        return "#F87171", f"🔴 Expired {-days}d ago", days
    if days <= 60:
        return "#FBBF24", f"🟡 Refix window · {days}d left", days
    if days <= 120:
        return "#FBBF24", f"🟡 {days} days left", days
    return "#34D399", f"🟢 {days} days left", days


def _loan_dossier_summary_label(loan: dict | None, prop: str, count: int) -> str:
    """Build the one-line loan summary used by the property setup matrix's loan dossier popover."""
    if loan is None:
        return f"🏠 {prop} — no loan recorded"
    _colour, status, _days = _loan_rate_status_tuple(loan)
    label = f" +{count - 1} more" if count > 1 else ""
    bank = loan.get("bank_name") or infer_nz_bank_from_account(loan.get("account_number") or "") or "—"
    prod = f" ({loan.get('account_product_type')})" if loan.get("account_product_type") else ""
    acct = f" · Acct {loan.get('account_number')}" if loan.get("account_number") else ""
    bal = (
        float(loan.get("current_balance") or 0)
        or float(loan.get("loan_limit") or 0)
        or float(loan.get("original_drawdown_amount") or 0)
    )
    bal_str = f" · ${bal:,.0f}" if bal > 0 else ""
    return (
        f"🏠 {prop} — {bank}{prod}{acct}{bal_str} · "
        f"{float(loan.get('interest_rate_pct') or 0):.2f}% p.a. ({loan.get('interest_type') or 'Fixed'} / {loan.get('repayment_type') or 'Interest Only'}) · "
        f"Rate expires {_fmt_loan_date(loan.get('fixed_rate_review_date'))} ({status}){label}"
    )
