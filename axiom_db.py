"""
axiom_db
========

Streamlit-free SQLite persistence layer extracted from the monolithic
``utils.py`` god-module.

This module owns the physical storage primitives (connection, schema/migrations)
and the pure, round-trippable persistence functions for the master ledger and
uploaded-file records. Nothing here imports ``streamlit`` or touches
``st.session_state``, so it is deterministic and unit-testable against a
temporary SQLite file. ``utils.py`` imports and re-exports these symbols, so all
existing ``from utils import ...`` call sites keep working unchanged.

Only the single, canonical persistence layer lives here; the legacy duplicate
``_db_*`` helpers have been removed.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd

from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY

# --- Storage locations -------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent / "data"
SQLITE_DB_PATH = DATA_DIR / "axiom_ledger.db"
VAULT_ROOT_DIR = DATA_DIR / "vault"

# Serialises all writes; SQLite handles concurrent readers via WAL.
_DB_LOCK = threading.Lock()
# Set once the SQLite schema (tables + migrations) has been ensured this process,
# so _ensure_sqlite_schema() short-circuits on every subsequent DB operation.
_SCHEMA_READY = threading.Event()

# One pooled connection per resolved DB path, reused for the life of the process
# instead of opening/closing a fresh `sqlite3.connect()` on every call (P2).
# Keyed by path (not a bare singleton) so tests that point SQLITE_DB_PATH at a
# fresh temp file always get an isolated connection — never a stale one left
# over from a previous test/path. Guarded by its own lock (distinct from
# _DB_LOCK, which callers typically already hold) to avoid self-deadlock.
_CONN_CACHE: dict[str, sqlite3.Connection] = {}
_CONN_CACHE_LOCK = threading.Lock()


def _get_sqlite_conn() -> sqlite3.Connection:
    """Return a cached, thread-safe SQLite connection to `data/axiom_ledger.db` (WAL journaling).

    The connection is created once per unique resolved `SQLITE_DB_PATH` and reused
    on every subsequent call (pooled), rather than reconnecting + re-issuing the
    WAL/synchronous PRAGMAs on every DB operation.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    VAULT_ROOT_DIR.mkdir(parents=True, exist_ok=True)
    db_path_str = str(SQLITE_DB_PATH)
    with _CONN_CACHE_LOCK:
        conn = _CONN_CACHE.get(db_path_str)
        if conn is None:
            conn = sqlite3.connect(db_path_str, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            _CONN_CACHE[db_path_str] = conn
        return conn


def _ensure_sqlite_schema() -> None:
    """Create all SQLite tables (`kv_settings`, `uploaded_files`, `file_transactions`, `master_ledgers`) if not present.

    Runs once per process: subsequent calls short-circuit via the _SCHEMA_READY
    guard instead of re-issuing executescript + PRAGMA migrations on every DB op.
    """
    if _SCHEMA_READY.is_set():
        return
    with _DB_LOCK:
        if _SCHEMA_READY.is_set():
            return
        with _get_sqlite_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS uploaded_files (
                    file_id TEXT PRIMARY KEY,
                    entity TEXT NOT NULL,
                    tax_year TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    name TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    vault_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT '',
                    needs_ocr INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    sort_order INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_upfiles_entity_year
                    ON uploaded_files(entity, tax_year, sort_order);

                CREATE TABLE IF NOT EXISTS file_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    tx_date TEXT NOT NULL DEFAULT '',
                    amount REAL NOT NULL DEFAULT 0.0,
                    description TEXT NOT NULL DEFAULT '',
                    property TEXT NOT NULL DEFAULT 'Unassigned',
                    account_code TEXT NOT NULL DEFAULT 'Uncategorized'
                );
                CREATE INDEX IF NOT EXISTS idx_ftx_file_id
                    ON file_transactions(file_id, row_index);

                CREATE TABLE IF NOT EXISTS master_ledgers (
                    entity TEXT NOT NULL,
                    tax_year TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    tx_date TEXT NOT NULL DEFAULT '',
                    amount REAL NOT NULL DEFAULT 0.0,
                    description TEXT NOT NULL DEFAULT '',
                    property TEXT NOT NULL DEFAULT 'Unassigned',
                    account_code TEXT NOT NULL DEFAULT 'Uncategorized',
                    PRIMARY KEY (entity, tax_year, row_index)
                );

                CREATE TABLE IF NOT EXISTS property_loans (
                    loan_id TEXT PRIMARY KEY,
                    entity TEXT NOT NULL,
                    property_name TEXT NOT NULL,
                    loan_label TEXT NOT NULL DEFAULT 'Main Facility',
                    bank_name TEXT NOT NULL DEFAULT '',
                    account_holder TEXT NOT NULL DEFAULT '',
                    account_product_type TEXT NOT NULL DEFAULT '',
                    account_number TEXT NOT NULL DEFAULT '',
                    repayment_account TEXT NOT NULL DEFAULT '',
                    drawdown_date TEXT NOT NULL DEFAULT '',
                    original_drawdown_amount REAL NOT NULL DEFAULT 0.0,
                    loan_limit REAL NOT NULL DEFAULT 0.0,
                    current_balance REAL NOT NULL DEFAULT 0.0,
                    available_redraw REAL NOT NULL DEFAULT 0.0,
                    interest_type TEXT NOT NULL DEFAULT 'Fixed',
                    repayment_type TEXT NOT NULL DEFAULT 'Interest Only',
                    interest_rate_pct REAL NOT NULL DEFAULT 0.0,
                    fixed_rate_review_date TEXT NOT NULL DEFAULT '',
                    interest_only_end_date TEXT NOT NULL DEFAULT '',
                    loan_term_end_date TEXT NOT NULL DEFAULT '',
                    interest_accrued REAL NOT NULL DEFAULT 0.0,
                    next_repayment_date TEXT NOT NULL DEFAULT '',
                    repayment_frequency TEXT NOT NULL DEFAULT 'Monthly',
                    regular_repayment_amount REAL NOT NULL DEFAULT 0.0,
                    next_payment_is_estimated INTEGER NOT NULL DEFAULT 0,
                    original_term_text TEXT NOT NULL DEFAULT '',
                    remaining_term_text TEXT NOT NULL DEFAULT '',
                    broker_or_banker TEXT NOT NULL DEFAULT '',
                    use_for_coding INTEGER NOT NULL DEFAULT 1,
                    coding_trigger_keywords TEXT NOT NULL DEFAULT '',
                    interest_account_code TEXT NOT NULL DEFAULT 'Loan Interest',
                    principal_account_code TEXT NOT NULL DEFAULT 'Principal Loan',
                    email_reminder_enabled INTEGER NOT NULL DEFAULT 1,
                    reminder_months_before INTEGER NOT NULL DEFAULT 2,
                    remind_on_fixed_expiry INTEGER NOT NULL DEFAULT 1,
                    remind_on_io_expiry INTEGER NOT NULL DEFAULT 1,
                    remind_on_term_expiry INTEGER NOT NULL DEFAULT 1,
                    screenshot_vault_path TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_proploans_entity_prop
                    ON property_loans(entity, property_name);

                CREATE TABLE IF NOT EXISTS property_loan_rate_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    loan_id TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    property_name TEXT NOT NULL,
                    effective_date TEXT NOT NULL DEFAULT '',
                    interest_type TEXT NOT NULL DEFAULT '',
                    repayment_type TEXT NOT NULL DEFAULT '',
                    interest_rate_pct REAL NOT NULL DEFAULT 0.0,
                    review_or_expiry_date TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    logged_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_loanratehist_loan
                    ON property_loan_rate_history(loan_id, id DESC);

                CREATE TABLE IF NOT EXISTS loan_reminder_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    loan_id TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    property_name TEXT NOT NULL,
                    milestone_type TEXT NOT NULL,
                    expiry_date TEXT NOT NULL,
                    recipient_email TEXT NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    sent_at TEXT NOT NULL,
                    trigger_source TEXT NOT NULL DEFAULT 'auto',
                    status TEXT NOT NULL DEFAULT 'sent',
                    detail TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_loanremlog_lookup
                    ON loan_reminder_log(loan_id, milestone_type, expiry_date, status);
                """
            )
            existing_loan_cols = {
                str(r["name"])
                for r in conn.execute("PRAGMA table_info(property_loans)").fetchall()
            }
            loan_migrations = [
                ("account_holder", "TEXT NOT NULL DEFAULT ''"),
                ("account_product_type", "TEXT NOT NULL DEFAULT ''"),
                ("loan_limit", "REAL NOT NULL DEFAULT 0.0"),
                ("available_redraw", "REAL NOT NULL DEFAULT 0.0"),
                ("next_payment_is_estimated", "INTEGER NOT NULL DEFAULT 0"),
                ("original_term_text", "TEXT NOT NULL DEFAULT ''"),
                ("remaining_term_text", "TEXT NOT NULL DEFAULT ''"),
                ("screenshot_vault_path", "TEXT NOT NULL DEFAULT ''"),
            ]
            for col_name, col_def in loan_migrations:
                if col_name not in existing_loan_cols:
                    conn.execute(f"ALTER TABLE property_loans ADD COLUMN {col_name} {col_def}")
        _SCHEMA_READY.set()


def _save_master_ledger_to_sqlite(entity: str, tax_year: str, df: pd.DataFrame) -> None:
    """Persist the master ledger DataFrame for `(entity, tax_year)` into SQLite."""
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                "DELETE FROM master_ledgers WHERE entity = ? AND tax_year = ?",
                (entity, tax_year),
            )
            if isinstance(df, pd.DataFrame) and not df.empty:
                clean_df = df.reindex(columns=STANDARD_COLUMNS).copy()
                rows_to_insert = []
                for idx, r in clean_df.reset_index(drop=True).iterrows():
                    amt_val = 0.0
                    try:
                        amt_val = float(r.get("Amount", 0.0) or 0.0)
                    except Exception:
                        amt_val = 0.0
                    rows_to_insert.append(
                        (
                            entity,
                            tax_year,
                            int(idx),
                            str(r.get("Date", "") or "").strip(),
                            amt_val,
                            str(r.get("Description", "") or "").strip(),
                            str(r.get("Property", UNASSIGNED_PROPERTY) or UNASSIGNED_PROPERTY).strip(),
                            str(r.get("Account Code", "Uncategorized") or "Uncategorized").strip(),
                        )
                    )
                conn.executemany(
                    """
                    INSERT INTO master_ledgers (
                        entity, tax_year, row_index, tx_date, amount, description, property, account_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_to_insert,
                )


def _load_master_ledger_from_sqlite(entity: str, tax_year: str) -> pd.DataFrame:
    """Load the master ledger DataFrame for `(entity, tax_year)` from SQLite."""
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            rows = conn.execute(
                """
                SELECT tx_date, amount, description, property, account_code
                FROM master_ledgers
                WHERE entity = ? AND tax_year = ?
                ORDER BY row_index ASC
                """,
                (entity, tax_year),
            ).fetchall()
            if not rows:
                return pd.DataFrame(columns=STANDARD_COLUMNS)
            return pd.DataFrame(
                [
                    {
                        "Date": str(r["tx_date"] or ""),
                        "Amount": float(r["amount"] or 0.0),
                        "Description": str(r["description"] or ""),
                        "Property": str(r["property"] or UNASSIGNED_PROPERTY),
                        "Account Code": str(r["account_code"] or "Uncategorized"),
                    }
                    for r in rows
                ]
            )[STANDARD_COLUMNS]


def _save_file_record_to_sqlite(
    entity: str,
    tax_year: str,
    record: dict[str, object],
    sort_order: int = 0,
) -> None:
    """Persist an uploaded file metadata record and all its transaction rows into SQLite.

    `sort_order` controls display ordering in `_load_uploaded_files_from_sqlite`
    (ascending, then rowid as a tiebreaker).
    """
    _ensure_sqlite_schema()
    file_id = str(record.get("id", ""))
    if not file_id:
        return
    fname = str(record.get("name", "Uploaded_File"))
    sig = str(record.get("signature", ""))
    bucket = str(record.get("bucket", "csv"))
    vault_path = str(record.get("vault_path", ""))
    mime_type = str(record.get("mime_type", ""))
    uploaded_at = str(record.get("uploaded_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    needs_ocr = 1 if record.get("needs_ocr") else 0
    note = str(record.get("note", ""))
    df: pd.DataFrame = record.get("df", pd.DataFrame(columns=STANDARD_COLUMNS))

    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO uploaded_files (
                    file_id, entity, tax_year, name, signature, bucket,
                    vault_path, mime_type, uploaded_at, needs_ocr, note, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    entity = excluded.entity,
                    tax_year = excluded.tax_year,
                    name = excluded.name,
                    signature = excluded.signature,
                    bucket = excluded.bucket,
                    vault_path = excluded.vault_path,
                    mime_type = excluded.mime_type,
                    needs_ocr = excluded.needs_ocr,
                    note = excluded.note,
                    sort_order = excluded.sort_order
                """,
                (
                    file_id,
                    entity,
                    tax_year,
                    fname,
                    sig,
                    bucket,
                    vault_path,
                    mime_type,
                    uploaded_at,
                    needs_ocr,
                    note,
                    int(sort_order or 0),
                ),
            )
            conn.execute("DELETE FROM file_transactions WHERE file_id = ?", (file_id,))
            if isinstance(df, pd.DataFrame) and not df.empty:
                clean_df = df.reindex(columns=STANDARD_COLUMNS).copy()
                rows_to_insert = []
                for idx, r in clean_df.reset_index(drop=True).iterrows():
                    amt_val = 0.0
                    try:
                        amt_val = float(r.get("Amount", 0.0) or 0.0)
                    except Exception:
                        amt_val = 0.0
                    rows_to_insert.append(
                        (
                            file_id,
                            int(idx),
                            str(r.get("Date", "") or "").strip(),
                            amt_val,
                            str(r.get("Description", "") or "").strip(),
                            str(r.get("Property", UNASSIGNED_PROPERTY) or UNASSIGNED_PROPERTY).strip(),
                            str(r.get("Account Code", "Uncategorized") or "Uncategorized").strip(),
                        )
                    )
                conn.executemany(
                    """
                    INSERT INTO file_transactions (
                        file_id, row_index, tx_date, amount, description, property, account_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_to_insert,
                )


def _delete_file_record_from_sqlite(file_id: str, delete_vault_file: bool = True) -> None:
    """Remove an uploaded file and its transactions from SQLite (and optionally its vault file)."""
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            if delete_vault_file:
                row = conn.execute(
                    "SELECT vault_path FROM uploaded_files WHERE file_id = ?",
                    (file_id,),
                ).fetchone()
                if row and row["vault_path"]:
                    vpath = Path(str(row["vault_path"]))
                    if not vpath.is_absolute():
                        vpath = Path(__file__).resolve().parent / vpath
                    try:
                        if vpath.exists() and vpath.is_file():
                            vpath.unlink()
                        sidecar_md = Path(f"{vpath}.ocr.md")
                        if sidecar_md.exists() and sidecar_md.is_file():
                            sidecar_md.unlink()
                    except Exception:
                        pass
            conn.execute("DELETE FROM file_transactions WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM uploaded_files WHERE file_id = ?", (file_id,))
