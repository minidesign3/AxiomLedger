"""
axiom_loan_crud
================

Property-loan SQLite CRUD, matching, and email-reminder orchestration
extracted from the monolithic ``utils.py``.

This module sits between the pure DB layer (``axiom_db``), pure loan
calculations (``axiom_loans``), the email service (``axiom_email``), and a
handful of Streamlit-session-coupled helpers that still live in ``utils.py``
(``get_active_entity``, ``get_entity_options``, ``get_entity_account_codes``,
``add_entity_account_code``).

To avoid a circular import (``utils.py`` imports this module at its own
import time, before those helpers are defined), those four are accessed via a
**local `import utils` inside each function body that needs one**, never at
this module's top level. A top-level `import utils` here would still break if
*this* module is imported before `utils.py` (e.g. directly, as some tests do).
A function-body-scoped `import utils` has no such ordering dependency: by the
time any function here is *called*, both modules have finished importing, so the
lookup always resolves regardless of which module was imported first.

``utils.py`` imports and re-exports every public symbol here, so existing
``from utils import ...`` call sites keep working unchanged.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime

import pandas as pd
import streamlit as st

from axiom_core import (
    _clean_loan_money,
    _clean_loan_rate,
    _loan_date_iso,
    infer_nz_bank_from_account,
)
from axiom_db import _DB_LOCK, _ensure_sqlite_schema, _get_sqlite_conn
from axiom_email import (
    _smtp_recipients,
    build_loan_reminder_email,
    get_smtp_config,
    send_email_via_smtp,
)
from axiom_loans import _loan_row_to_dict, get_loan_trigger_tokens, loan_milestones

LOAN_TARGET_ENTITY_DEFAULT = "BT Property Holdings Ltd"


def get_loan_target_entity() -> str:
    """Return the single entity that property loans are tracked against."""
    import utils

    try:
        opts = utils.get_entity_options()
    except Exception:
        opts = []
    if LOAN_TARGET_ENTITY_DEFAULT in opts:
        return LOAN_TARGET_ENTITY_DEFAULT
    return LOAN_TARGET_ENTITY_DEFAULT


def is_loan_target_entity(entity: str | None = None) -> bool:
    """Return True if `entity` (or the active entity) is the loan-tracking entity."""
    import utils

    try:
        resolved = utils.get_active_entity(entity)
    except Exception:
        resolved = str(entity or "")
    return resolved.strip().lower() == get_loan_target_entity().strip().lower()


def ensure_seeded_property_loans(entity: str | None = None) -> None:
    """Seed the two known BT Property Holdings loans (303 Weymouth, 33 Blampied) if not already present, and tidy up placeholder rows."""
    target = get_loan_target_entity()
    if entity and str(entity).strip().lower() != target.lower():
        return
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            now = datetime.now().isoformat(timespec="seconds")
            row_wey = conn.execute(
                "SELECT COUNT(*) c FROM property_loans WHERE loan_id=?",
                ("btph-303-weymouth-main",),
            ).fetchone()
            if not (row_wey and int(row_wey["c"] or 0) > 0):
                conn.execute(
                    """INSERT INTO property_loans (
                        loan_id,entity,property_name,loan_label,bank_name,account_holder,account_product_type,
                        account_number,repayment_account,drawdown_date,original_drawdown_amount,loan_limit,
                        current_balance,available_redraw,interest_type,repayment_type,interest_rate_pct,
                        fixed_rate_review_date,interest_only_end_date,loan_term_end_date,interest_accrued,
                        next_repayment_date,repayment_frequency,regular_repayment_amount,next_payment_is_estimated,
                        original_term_text,remaining_term_text,broker_or_banker,use_for_coding,coding_trigger_keywords,
                        interest_account_code,principal_account_code,email_reminder_enabled,reminder_months_before,
                        remind_on_fixed_expiry,remind_on_io_expiry,remind_on_term_expiry,screenshot_vault_path,notes,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "btph-303-weymouth-main", target, "303 Weymouth", "Main Facility", "", "BT Property Holdings Limited",
                        "Home Loan", "86131912-1003", "01-0286-0890538-00", "2025-04-17", 655000.0, 655000.0,
                        655000.0, 0.0, "Fixed", "Interest Only", 4.44,
                        "2027-04-17", "2030-04-17", "2041-11-02", 1195.15,
                        "2026-12-01", "Monthly", 0.0, 0,
                        "", "", "", 1, "",
                        "Loan Interest", "Principal Loan", 1, 2,
                        1, 1, 1, "", "Seeded from bank loan summary provided by operator.", now,
                    ),
                )
                conn.execute(
                    """INSERT INTO property_loan_rate_history (loan_id,entity,property_name,effective_date,interest_type,repayment_type,interest_rate_pct,review_or_expiry_date,note,logged_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    ("btph-303-weymouth-main", target, "303 Weymouth", "2025-04-17", "Fixed", "Interest Only", 4.44, "2027-04-17", "Original drawdown — fixed at 4.44% p.a.", now),
                )

            row_blam = conn.execute(
                "SELECT COUNT(*) c FROM property_loans WHERE loan_id=?",
                ("btph-33-blampied-main",),
            ).fetchone()
            if not (row_blam and int(row_blam["c"] or 0) > 0):
                conn.execute(
                    """INSERT INTO property_loans (
                        loan_id,entity,property_name,loan_label,bank_name,account_holder,account_product_type,
                        account_number,repayment_account,drawdown_date,original_drawdown_amount,loan_limit,
                        current_balance,available_redraw,interest_type,repayment_type,interest_rate_pct,
                        fixed_rate_review_date,interest_only_end_date,loan_term_end_date,interest_accrued,
                        next_repayment_date,repayment_frequency,regular_repayment_amount,next_payment_is_estimated,
                        original_term_text,remaining_term_text,broker_or_banker,use_for_coding,coding_trigger_keywords,
                        interest_account_code,principal_account_code,email_reminder_enabled,reminder_months_before,
                        remind_on_fixed_expiry,remind_on_io_expiry,remind_on_term_expiry,screenshot_vault_path,notes,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "btph-33-blampied-main", target, "33 Blampied", "33 Blampied Loan", "Westpac", "BT Property Holdings Limited",
                        "Choices", "03-0399-0618477-092", "", "", 313000.0, 313000.0,
                        313000.0, 0.0, "Fixed", "Interest Only", 4.49,
                        "2027-05-22", "2030-05-22", "2055-05-22", 0.0,
                        "2026-11-23", "Monthly", 1155.09, 1,
                        "30 years 0 months", "28 years 7 months", "", 1, "",
                        "Loan Interest", "Principal Loan", 1, 2,
                        1, 1, 1, "", "Seeded from Westpac Choices loan details provided by operator.", now,
                    ),
                )
                conn.execute(
                    """INSERT INTO property_loan_rate_history (loan_id,entity,property_name,effective_date,interest_type,repayment_type,interest_rate_pct,review_or_expiry_date,note,logged_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    ("btph-33-blampied-main", target, "33 Blampied", date.today().isoformat(), "Fixed", "Interest Only", 4.49, "2027-05-22", "Westpac Choices — fixed at 4.49% p.a. until 22 May 2027", now),
                )

            # Backfill 303 Weymouth balance/limit if 0 and remove accidental empty placeholder duplicate on 33 Blampied
            conn.execute(
                """UPDATE property_loans
                   SET current_balance = CASE WHEN current_balance <= 0 THEN original_drawdown_amount ELSE current_balance END,
                       loan_limit = CASE WHEN loan_limit <= 0 THEN original_drawdown_amount ELSE loan_limit END,
                       account_holder = CASE WHEN COALESCE(account_holder, '') = '' THEN 'BT Property Holdings Limited' ELSE account_holder END,
                       account_product_type = CASE WHEN COALESCE(account_product_type, '') = '' THEN 'Home Loan' ELSE account_product_type END
                   WHERE loan_id = 'btph-303-weymouth-main'"""
            )
            conn.execute(
                """DELETE FROM property_loans
                   WHERE property_name = '33 Blampied'
                     AND loan_id != 'btph-33-blampied-main'
                     AND COALESCE(account_number, '') = ''
                     AND COALESCE(interest_rate_pct, 0) = 0"""
            )
            conn.execute(
                """DELETE FROM property_loans
                   WHERE property_name = '14 Raleigh'
                     AND loan_label = 'Main Facility'
                     AND COALESCE(account_number, '') = ''
                     AND COALESCE(current_balance, 0) = 0"""
            )
            # Also clean up any placeholder 'Main Facility' row with no account_number and 0 balance
            # when the same property already has a real loan with an account_number
            conn.execute(
                """DELETE FROM property_loans
                   WHERE loan_label = 'Main Facility'
                     AND COALESCE(account_number, '') = ''
                     AND COALESCE(current_balance, 0) = 0
                     AND property_name IN (
                         SELECT property_name FROM property_loans
                         WHERE COALESCE(account_number, '') != ''
                            OR COALESCE(current_balance, 0) > 0
                     )"""
            )


def get_property_loans(entity: str | None = None, property_name: str | None = None) -> list[dict]:
    """Return all loans for the loan-tracking entity (optionally filtered by property), seeding defaults first."""
    target = get_loan_target_entity()
    resolved = target
    if entity:
        import utils

        try:
            resolved = utils.get_active_entity(entity)
        except Exception:
            resolved = str(entity)
        if resolved.strip().lower() != target.lower():
            return []
    ensure_seeded_property_loans(target)
    _ensure_sqlite_schema()
    q = "SELECT * FROM property_loans WHERE entity=? "
    args: list = [target]
    if property_name:
        q += "AND property_name=? "
        args.append(str(property_name).strip())
    q += "ORDER BY property_name COLLATE NOCASE, loan_label COLLATE NOCASE"
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            rows = conn.execute(q, args).fetchall()
    return [_loan_row_to_dict(r) for r in rows]


def get_property_loan_by_id(loan_id: str) -> dict | None:
    """Return a single loan by its `loan_id`, or None if not found."""
    if not loan_id:
        return None
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            r = conn.execute("SELECT * FROM property_loans WHERE loan_id=?", (str(loan_id),)).fetchone()
    return _loan_row_to_dict(r) if r else None


def save_property_loan(loan: dict, log_rate_history: bool = False, rate_note: str = "") -> tuple[bool, str, str]:
    """Insert or update a loan record (upsert by `loan_id`), logging a rate-history entry if the rate/terms changed."""
    target = get_loan_target_entity()
    prop = str(loan.get("property_name") or "").strip()
    if not prop:
        return False, "Property is required.", ""
    loan_id = str(loan.get("loan_id") or "").strip() or f"loan-{uuid.uuid4().hex[:12]}"
    existing = get_property_loan_by_id(loan_id)
    now = datetime.now().isoformat(timespec="seconds")
    acct_str = str(loan.get("account_number") or "").strip()
    bank_str = str(loan.get("bank_name") or "").strip() or infer_nz_bank_from_account(acct_str)
    vals = {
        "loan_id": loan_id,
        "entity": target,
        "property_name": prop,
        "loan_label": str(loan.get("loan_label") or "Main Facility").strip() or "Main Facility",
        "bank_name": bank_str,
        "account_holder": str(loan.get("account_holder") or "").strip(),
        "account_product_type": str(loan.get("account_product_type") or "").strip(),
        "account_number": acct_str,
        "repayment_account": str(loan.get("repayment_account") or "").strip(),
        "drawdown_date": _loan_date_iso(loan.get("drawdown_date")),
        "original_drawdown_amount": _clean_loan_money(loan.get("original_drawdown_amount")),
        "loan_limit": _clean_loan_money(loan.get("loan_limit")),
        "current_balance": _clean_loan_money(loan.get("current_balance")),
        "available_redraw": _clean_loan_money(loan.get("available_redraw")),
        "interest_type": str(loan.get("interest_type") or "Fixed").strip(),
        "repayment_type": str(loan.get("repayment_type") or "Interest Only").strip(),
        "interest_rate_pct": _clean_loan_rate(loan.get("interest_rate_pct")),
        "fixed_rate_review_date": _loan_date_iso(loan.get("fixed_rate_review_date")),
        "interest_only_end_date": _loan_date_iso(loan.get("interest_only_end_date")),
        "loan_term_end_date": _loan_date_iso(loan.get("loan_term_end_date")),
        "interest_accrued": _clean_loan_money(loan.get("interest_accrued")),
        "next_repayment_date": _loan_date_iso(loan.get("next_repayment_date")),
        "repayment_frequency": str(loan.get("repayment_frequency") or "Monthly").strip(),
        "regular_repayment_amount": _clean_loan_money(loan.get("regular_repayment_amount")),
        "next_payment_is_estimated": 1 if loan.get("next_payment_is_estimated", False) else 0,
        "original_term_text": str(loan.get("original_term_text") or "").strip(),
        "remaining_term_text": str(loan.get("remaining_term_text") or "").strip(),
        "broker_or_banker": str(loan.get("broker_or_banker") or "").strip(),
        "use_for_coding": 1 if loan.get("use_for_coding", True) else 0,
        "coding_trigger_keywords": str(loan.get("coding_trigger_keywords") or "").strip(),
        "interest_account_code": str(loan.get("interest_account_code") or "Loan Interest").strip() or "Loan Interest",
        "principal_account_code": str(loan.get("principal_account_code") or "Principal Loan").strip() or "Principal Loan",
        "email_reminder_enabled": 1 if loan.get("email_reminder_enabled", True) else 0,
        "reminder_months_before": int(loan.get("reminder_months_before") or 2),
        "remind_on_fixed_expiry": 1 if loan.get("remind_on_fixed_expiry", True) else 0,
        "remind_on_io_expiry": 1 if loan.get("remind_on_io_expiry", True) else 0,
        "remind_on_term_expiry": 1 if loan.get("remind_on_term_expiry", True) else 0,
        "screenshot_vault_path": str(loan.get("screenshot_vault_path") or (existing or {}).get("screenshot_vault_path") or "").strip(),
        "notes": str(loan.get("notes") or "").strip(),
        "updated_at": now,
    }
    _ensure_sqlite_schema()
    cols = list(vals.keys())
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(f"INSERT OR REPLACE INTO property_loans ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [vals[c] for c in cols])
    # Persist chosen codes so dropdowns include them
    import utils

    for code in (vals["interest_account_code"], vals["principal_account_code"]):
        try:
            if code and code not in utils.get_entity_account_codes(target):
                utils.add_entity_account_code(code, target)
        except Exception:
            pass
    rate_changed = False
    if existing:
        try:
            rate_changed = abs(float(existing.get("interest_rate_pct") or 0) - vals["interest_rate_pct"]) > 1e-9 or str(existing.get("fixed_rate_review_date") or "") != vals["fixed_rate_review_date"] or str(existing.get("interest_type") or "") != vals["interest_type"]
        except Exception:
            rate_changed = False
    if log_rate_history or rate_changed:
        log_loan_rate_history({**vals, "entity": target}, note=rate_note or ("Rate rollover / adjustment" if existing else "Initial rate recorded"))
    return True, f"Saved loan '{vals['loan_label']}' for {prop}.", loan_id


def delete_property_loan(loan_id: str) -> tuple[bool, str]:
    """Delete a loan and its rate history."""
    if not loan_id:
        return False, "Missing loan."
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute("DELETE FROM property_loans WHERE loan_id=?", (str(loan_id),))
            conn.execute("DELETE FROM property_loan_rate_history WHERE loan_id=?", (str(loan_id),))
    return True, "Loan deleted."


def create_property_loan(property_name: str, loan_label: str = "Main Facility") -> tuple[bool, str, str]:
    """Create a new, mostly-empty loan placeholder for a property with sensible defaults."""
    return save_property_loan(
        {
            "property_name": property_name,
            "loan_label": loan_label or "Main Facility",
            "interest_account_code": "Loan Interest",
            "principal_account_code": "Principal Loan",
            "email_reminder_enabled": True,
            "use_for_coding": True,
            "reminder_months_before": int(get_smtp_config().get("default_reminder_months") or 2),
        }
    )


def log_loan_rate_history(loan: dict, note: str = "", effective_date=None) -> None:
    """Append a rate-history entry for a loan (used whenever the rate/terms change)."""
    eff = _loan_date_iso(effective_date) or _loan_date_iso(loan.get("drawdown_date")) or date.today().isoformat()
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                "INSERT INTO property_loan_rate_history (loan_id,entity,property_name,effective_date,interest_type,repayment_type,interest_rate_pct,review_or_expiry_date,note,logged_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(loan.get("loan_id") or ""),
                    get_loan_target_entity(),
                    str(loan.get("property_name") or ""),
                    eff,
                    str(loan.get("interest_type") or ""),
                    str(loan.get("repayment_type") or ""),
                    float(loan.get("interest_rate_pct") or 0),
                    _loan_date_iso(loan.get("fixed_rate_review_date")),
                    str(note or ""),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )


def get_loan_rate_history(loan_id: str) -> list[dict]:
    """Return the full rate-history log for a loan, most recent first."""
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            rows = conn.execute("SELECT * FROM property_loan_rate_history WHERE loan_id=? ORDER BY id DESC", (str(loan_id),)).fetchall()
    return [dict(r) for r in rows]


def match_loan_from_text(text: object, entity: str | None = None) -> dict | None:
    """Find the best-matching loan (for auto-categorization) whose trigger tokens appear in `text`."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    s = str(text).lower()
    if not s.strip():
        return None
    loans = [l for l in get_property_loans(entity) if l.get("use_for_coding")]
    if not loans:
        return None
    # Detect customer numbers shared across DIFFERENT properties so a shared customer number alone never misroutes
    base_props: dict[str, set[str]] = {}
    for l in loans:
        parts = [p.lower() for p in re.split(r"[-\s]+", str(l.get("account_number") or "").strip()) if p]
        cand = parts[2] if len(parts) == 4 else (parts[0] if len(parts) >= 2 else "")
        if cand:
            base_props.setdefault(cand, set()).add(str(l.get("property_name") or ""))
    shared_bases = {b for b, ps in base_props.items() if len(ps) > 1}

    best, best_len = None, 0
    for loan in loans:
        for tok in get_loan_trigger_tokens(loan, shared_customer_bases=shared_bases):
            if len(tok) < 4:
                continue
            hit = (re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", s) is not None) if len(tok) <= 7 or tok.isdigit() else (tok in s)
            if hit and len(tok) > best_len:
                best, best_len = loan, len(tok)
    return best


def has_reminder_been_sent(loan_id: str, milestone_type: str, expiry_date: date) -> bool:
    """Return True if a 'sent' reminder log entry already exists for this loan/milestone/expiry combination."""
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            r = conn.execute(
                "SELECT COUNT(*) c FROM loan_reminder_log WHERE loan_id=? AND milestone_type=? AND expiry_date=? AND status='sent'",
                (str(loan_id), milestone_type, expiry_date.isoformat()),
            ).fetchone()
    return bool(r and int(r["c"] or 0) > 0)


def get_loan_reminder_log(limit: int = 50) -> list[dict]:
    """Return the most recent loan-reminder-email log entries."""
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            rows = conn.execute("SELECT * FROM loan_reminder_log ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def get_due_loan_reminders(reference_date: date | None = None) -> list[dict]:
    """Return every (loan, milestone) pair whose reminder window has opened and hasn't been sent yet."""
    today = reference_date or date.today()
    due = []
    for loan in get_property_loans():
        if not loan.get("email_reminder_enabled"):
            continue
        for ms in loan_milestones(loan):
            if not ms["reminder_enabled_for_milestone"]:
                continue
            if ms["window_start"] <= today and not has_reminder_been_sent(loan["loan_id"], ms["milestone_type"], ms["expiry_date"]):
                due.append({"loan": loan, **ms})
    return due


def send_loan_reminder_email(loan_id: str, milestone_type: str | None = None, trigger_source: str = "manual") -> tuple[bool, str]:
    """Build and send the reminder email(s) for a loan's due milestone(s), logging each attempt."""
    loan = get_property_loan_by_id(loan_id)
    if not loan:
        return False, "Loan not found."
    milestones = loan_milestones(loan)
    if milestone_type:
        milestones = [m for m in milestones if m["milestone_type"] == milestone_type]
    if not milestones:
        return False, "No expiry date set for that milestone."
    cfg = get_smtp_config()
    recips = _smtp_recipients(cfg)
    sent, msgs = 0, []
    for ms in milestones:
        subject, body = build_loan_reminder_email(loan, ms)
        ok, msg = send_email_via_smtp(subject, body, recips, cfg)
        _ensure_sqlite_schema()
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                conn.execute(
                    "INSERT INTO loan_reminder_log (loan_id,entity,property_name,milestone_type,expiry_date,recipient_email,subject,sent_at,trigger_source,status,detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        loan_id,
                        get_loan_target_entity(),
                        loan.get("property_name"),
                        ms["milestone_type"],
                        ms["expiry_date"].isoformat(),
                        ", ".join(recips),
                        subject,
                        datetime.now().isoformat(timespec="seconds"),
                        trigger_source,
                        "sent" if ok else "failed",
                        msg,
                    ),
                )
        msgs.append(msg)
        sent += 1 if ok else 0
    return (sent > 0), " ".join(msgs) if msgs else "No reminder sent."


def check_and_send_due_loan_reminders(trigger_source: str = "auto") -> tuple[int, list[str]]:
    """Send reminder emails for every due, not-yet-sent (loan, milestone) pair."""
    due = get_due_loan_reminders()
    sent, msgs = 0, []
    seen = set()
    for item in due:
        lid = item["loan"]["loan_id"]
        key = (lid, item["milestone_type"])
        if key in seen:
            continue
        seen.add(key)
        ok, msg = send_loan_reminder_email(lid, item["milestone_type"], trigger_source=trigger_source)
        msgs.append(f"{item['loan'].get('property_name')} / {item['label']}: {msg}")
        sent += 1 if ok else 0
    return sent, msgs


def run_loan_reminder_check_once_per_session() -> None:
    """Fire the due-reminder check at most once per Streamlit session, if auto-send-on-startup is enabled."""
    try:
        if st.session_state.get("_loan_reminder_checked"):
            return
        st.session_state["_loan_reminder_checked"] = True
        cfg = get_smtp_config()
        if not cfg.get("auto_send_on_startup") or not str(cfg.get("smtp_host") or "").strip():
            return
        check_and_send_due_loan_reminders(trigger_source="startup")
    except Exception:
        pass
