"""
axiom_email
===========

SMTP / email notification service extracted from the monolithic ``utils.py``.

Nothing in this module imports ``streamlit`` or touches ``st.session_state``.
Configuration (``get_smtp_config`` / ``save_smtp_config``) is persisted via the
same shared SQLite primitives as the rest of the app (``axiom_db``); everything
else (``_smtp_recipients``, ``build_loan_reminder_email``) is a pure function of
its arguments. ``send_email_via_smtp`` is the only function that performs real
network I/O. ``utils.py`` imports and re-exports these symbols, so existing
``from utils import ...`` call sites keep working unchanged.
"""

from __future__ import annotations

import json
import re
import smtplib
import ssl
from email.message import EmailMessage

from axiom_core import _fmt_loan_date
from axiom_db import _DB_LOCK, _ensure_sqlite_schema, _get_sqlite_conn

DEFAULT_SMTP_CONFIG: dict[str, object] = {
    "smtp_host": "",
    "smtp_port": 587,
    "encryption": "STARTTLS",
    "username": "",
    "password": "",
    "sender_email": "",
    "sender_name": "Axiom Ledger",
    "recipient_emails": "",
    "auto_send_on_startup": True,
    "default_reminder_months": 2,
}


def get_smtp_config() -> dict:
    """Load the persisted SMTP configuration from SQLite `kv_settings`, merged over defaults."""
    defaults = dict(DEFAULT_SMTP_CONFIG)
    try:
        _ensure_sqlite_schema()
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                r = conn.execute(
                    "SELECT value_json FROM kv_settings WHERE key='axiom_smtp_config'"
                ).fetchone()
        if r:
            cfg = json.loads(r["value_json"] or "{}")
            if isinstance(cfg, dict):
                defaults.update(cfg)
    except Exception:
        pass
    try:
        defaults["smtp_port"] = int(defaults.get("smtp_port") or 587)
    except Exception:
        defaults["smtp_port"] = 587
    return defaults


def save_smtp_config(cfg: dict) -> tuple[bool, str]:
    """Persist the SMTP configuration to SQLite `kv_settings`."""
    cur = get_smtp_config()
    cur.update({k: cfg.get(k, cur.get(k)) for k in cur.keys()})
    # Don't overwrite password with masked/empty placeholder from UI
    if cfg.get("_keep_password"):
        pass
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_settings (key,value_json) VALUES ('axiom_smtp_config',?)",
                (json.dumps(cur),),
            )
    return True, "SMTP settings saved."


def _smtp_recipients(cfg: dict | None = None) -> list[str]:
    """Parse the configured recipient list (comma/semicolon/newline separated) into clean email addresses."""
    cfg = cfg or get_smtp_config()
    raw = str(cfg.get("recipient_emails") or cfg.get("sender_email") or "")
    return [x.strip() for x in re.split(r"[,\n;]+", raw) if x.strip() and "@" in x]


def send_email_via_smtp(
    subject: str,
    body: str,
    recipients: list[str] | None = None,
    cfg: dict | None = None,
) -> tuple[bool, str]:
    """Send a plain-text email via the configured SMTP server (SSL or STARTTLS)."""
    cfg = cfg or get_smtp_config()
    host = str(cfg.get("smtp_host") or "").strip()
    sender = str(cfg.get("sender_email") or cfg.get("username") or "").strip()
    recips = recipients or _smtp_recipients(cfg)
    if not host:
        return False, "SMTP host is not configured in Settings → Email (SMTP) & Loan Reminders."
    if not sender:
        return False, "Sender email is not configured."
    if not recips:
        return False, "No recipient email configured."
    msg = EmailMessage()
    msg["From"] = f"{cfg.get('sender_name') or 'Axiom Ledger'} <{sender}>" if cfg.get("sender_name") else sender
    msg["To"] = ", ".join(recips)
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(cfg.get("smtp_port") or 587)
    enc = str(cfg.get("encryption") or "STARTTLS").upper()
    try:
        if enc in ("SSL", "SMTPS"):
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
                if cfg.get("username"):
                    s.login(str(cfg["username"]), str(cfg.get("password") or ""))
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                if enc in ("STARTTLS", "TLS"):
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                if cfg.get("username"):
                    s.login(str(cfg["username"]), str(cfg.get("password") or ""))
                s.send_message(msg)
    except Exception as exc:
        return False, f"SMTP send failed: {exc}"
    return True, f"Email sent to {', '.join(recips)}."


def build_loan_reminder_email(loan: dict, milestone: dict) -> tuple[str, str]:
    """Build the (subject, body) for a loan-expiry reminder email from a loan record + milestone dict."""
    exp = milestone["expiry_date"]
    subject = f"[Axiom Ledger] Loan reminder: {loan.get('property_name')} — {milestone['label']} on {_fmt_loan_date(exp)}"
    bal = float(loan.get("current_balance") or 0) or float(loan.get("loan_limit") or 0) or float(loan.get("original_drawdown_amount") or 0)
    lim = float(loan.get("loan_limit") or 0)
    est_tag = " (estimated)" if loan.get("next_payment_is_estimated") else ""
    prod = f" ({loan.get('account_product_type')})" if str(loan.get("account_product_type") or "").strip() else ""
    body = f"""Axiom Ledger — Loan Expiry Reminder (2-month advance)

Property: {loan.get('property_name')}
Account Holder: {loan.get('account_holder') or 'BT Property Holdings Limited'}
Loan: {loan.get('loan_label')}{prod}  |  Bank/Lender: {loan.get('bank_name') or '—'}
Loan Account Number: {loan.get('account_number') or '—'}
Repayment / Debit Interest Account: {loan.get('repayment_account') or '—'}

Milestone: {milestone['label']}
Expiry / Review Date: {_fmt_loan_date(exp)}  ({milestone['days_remaining']} days from today)
Reminder window opened: {_fmt_loan_date(milestone['window_start'])}

Current Terms
- Current Interest Rate: {float(loan.get('interest_rate_pct') or 0):.2f}% p.a. ({loan.get('interest_type')})
- Interest Rate Expiry / Review Date: {_fmt_loan_date(loan.get('fixed_rate_review_date'))}
- Repayment Type: {loan.get('repayment_type')}  |  Frequency: {loan.get('repayment_frequency')}
- Next Payment / Debit Interest: ${float(loan.get('regular_repayment_amount') or 0):,.2f}{est_tag} on {_fmt_loan_date(loan.get('next_repayment_date'))}
- Current Balance: ${bal:,.2f}  |  Loan Limit: ${lim:,.2f}  |  Original Drawdown: ${float(loan.get('original_drawdown_amount') or 0):,.2f}
- Drawdown Date: {_fmt_loan_date(loan.get('drawdown_date'))}
- Interest-Only Expiry Date: {_fmt_loan_date(loan.get('interest_only_end_date'))}
- Final Loan Term / Maturity Date: {_fmt_loan_date(loan.get('loan_term_end_date'))}
- Original Term: {loan.get('original_term_text') or '—'}  |  Remaining Term: {loan.get('remaining_term_text') or '—'}
- Broker / Banker: {loan.get('broker_or_banker') or '—'}

Action: contact your bank/broker now to refix or arrange a new loan before this date.
Notes: {loan.get('notes') or '—'}
"""
    return subject, body
