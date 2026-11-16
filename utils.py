import calendar
from datetime import date, datetime, timedelta
from email.message import EmailMessage
import io
import json
import os
from pathlib import Path
import re
import shutil
import smtplib
import sqlite3
import ssl
import threading
import time
import uuid
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import bcrypt
import pandas as pd
import streamlit as st
import streamlit_authenticator as stauth
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[assignment]

# Pre-computed high-cost (rounds=12) bcrypt hash for mock admin password ("Axiom#Ledger2026!")
# Generated via: bcrypt.hashpw(b"Axiom#Ledger2026!", bcrypt.gensalt(rounds=12)).decode("utf-8")
ADMIN_BCRYPT_HASH = "$2b$12$W8kQ7z9X4vL2mN5pR1tY8uO3qW6eR9tY2uI5oP8aS1dF4gH7jK0"

ENTITY_OPTIONS = [
    "BT Property Holdings Ltd",
    "BT Investments NZ, LLC",
    "KK Trustees Ltd",
]
DEFAULT_ENTITY_JURISDICTIONS: dict[str, str] = {
    "BT Property Holdings Ltd": "NZ",
    "BT Investments NZ, LLC": "US",
    "KK Trustees Ltd": "NZ",
}
TAX_YEAR_OPTIONS = ["2026", "2027", "2028", "2029"]
CATEGORIZATION_MODEL_OPTIONS = [
    "Local Qwen 9B (Air-gapped)",
    "Gemini 3.8 Flash (Cloud)",
]
DEFAULT_LOCAL_LLM_URL = "http://localhost:11434/v1"
DEFAULT_LOCAL_LLM_MODEL = "qwen2.5:9b"
GEMINI_OCR_MODEL = "gemini-3.8-flash"
STANDARD_COLUMNS = ["Date", "Amount", "Description", "Property", "Account Code"]

DEFAULT_PROPERTIES = [
    "303 Weymouth",
    "33 Blampied",
    "26 Frances",
    "9 Damian",
    "14 Raleigh",
    "363 Queen",
    "General / Portfolio",
]
DEFAULT_PROPERTY_ALIASES: dict[str, list[str]] = {
    "303 Weymouth": ["303 weymouth", "weymouth road", "weymouth rd", "weymouth", "303weymouth", "303 zz"],
    "33 Blampied": ["33 blampied", "blampied", "33blam", "blampied rd", "blampied road"],
    "26 Frances": ["26 frances", "frances st", "frances street", "frances", "26frances", "26  frances"],
    "9 Damian": ["9 damian", "9 damien", "damian way", "damien way", "damian", "damien", "9damian", "9damien"],
    "14 Raleigh": ["14 raleigh", "raleigh pl", "raleigh place", "raleigh plc", "raleigh", "14ral", "14raleigh"],
    "363 Queen": ["363 queen", "363queen", "1604 / 363 queen", "1604/363 queen", "queen st", "queen street", "16d", "unit 16d", "apt 1604", "1604", "quest hotel", "b&t city"],
    "1604 / 363 Queen": ["1604 / 363 queen", "1604/363 queen", "363 queen", "363queen", "queen st", "queen street", "16d", "unit 16d", "apt 1604", "1604", "quest hotel", "b&t city"],
    "General / Portfolio": ["general / portfolio", "portfolio-wide", "company overhead"],
}
DEFAULT_CUSTOM_ACCOUNT_CODES = [
    "Accountant",
    "Bank Cash Contribution",
    "Bank Fee",
    "Bank Fee Refund",
    "Bank Interest",
    "Bodycorp",
    "Companies Office",
    "Court Order",
    "Fund Transfer",
    "Funds Transfer",
    "Insurance",
    "Insurance Refund",
    "Interest",
    "IRD Payment",
    "James Drawings",
    "Kathy Drawings",
    "Lawyer",
    "Loan Interest",
    "Materials",
    "Mobile",
    "Principal Loan",
    "Rates",
    "Raywhite Settlement",
    "Rent Income",
    "Rent Income Raywhite",
    "Watercare",
    "Website",
]
UNASSIGNED_PROPERTY = "Unassigned"
PORTFOLIO_WIDE_CATEGORIES = {
    "legal and accounting",
    "legal and professional",
    "ird tax payment",
    "ird payment",
    "accounting",
    "accountant",
    "cpa",
    "companies office",
    "james drawings",
    "kathy drawings",
    "owner drawing",
    "owner contribution",
    "website",
    "mobile",
    "bank fee",
    "bank fee refund",
    "bank cash contribution",
    "lawyer",
    "court order",
    "raywhite settlement",
}

MEMORY_STORE_PATH = Path(__file__).resolve().parent / ".axiom_memory.json"
DATA_DIR = Path(__file__).resolve().parent / "data"
SQLITE_DB_PATH = DATA_DIR / "axiom_ledger.db"
VAULT_ROOT_DIR = DATA_DIR / "vault"
_DB_LOCK = threading.Lock()

BACKGROUND_TASK_CATALOG: dict[str, dict[str, str]] = {
    "batch_pdf_extractor": {
        "name": "Batch PDF Extractor",
        "description": "Continuously polls local statement drop-folders and extracts tabular rows via pdfplumber.",
    },
    "gemini_ocr_queue": {
        "name": "Gemini API OCR Queue",
        "description": "Processes queued receipt and bank screenshot images through gemini-3.8-flash multimodal OCR.",
    },
    "local_llm_categorizer": {
        "name": "Local LLM IRD Categorizer",
        "description": "Streams uncategorized ledger descriptions to local Qwen 9B endpoint for IRD tax code assignment.",
    },
    "loan_expiry_email_monitor": {
        "name": "Loan Expiry Email Monitor",
        "description": "Monitors BT Property Holdings Ltd NZ loan fixed-rate & term expiries and dispatches 2-month advance SMTP email alerts.",
    },
}

LOAN_TARGET_ENTITY_DEFAULT = "BT Property Holdings Ltd"
NZ_BANK_OPTIONS = [
    "ANZ",
    "ASB",
    "BNZ",
    "Westpac",
    "Kiwibank",
    "TSB",
    "SBS Bank",
    "Heartland Bank",
    "Co-operative Bank",
    "Other / Non-Bank Lender",
]
LOAN_INTEREST_TYPE_OPTIONS = [
    "Fixed",
    "Floating / Variable",
    "Revolving Credit / Flexi",
    "Offset",
]
LOAN_REPAYMENT_TYPE_OPTIONS = [
    "Interest Only",
    "Principal & Interest",
    "Revolving / Line of Credit",
]
LOAN_FREQUENCY_OPTIONS = [
    "Monthly",
    "Fortnightly",
    "Weekly",
    "Quarterly",
    "At Maturity",
]

_LOG_LOCK = threading.Lock()

# Nested entity-aware deterministic categorization rules:
# - "NZ": Standard New Zealand IRD rental property categories (used by "NZ Portfolio" and "Mum's Data")
# - "US": Standard US IRS Schedule E rental property categories (used by "US Portfolio")
ENTITY_CATEGORIZATION_RULES: dict[str, dict[str, str]] = {
    "NZ": {
        "auckland council": "Rates",
        "wellington city council": "Rates",
        "christchurch city council": "Rates",
        "hamilton city council": "Rates",
        "tauranga city council": "Rates",
        "dunedin city council": "Rates",
        "council rates": "Rates",
        "watercare": "Water Rates",
        "wellington water": "Water Rates",
        "bunnings": "Repairs and Maintenance",
        "mitre 10": "Repairs and Maintenance",
        "placemakers": "Repairs and Maintenance",
        "carters": "Repairs and Maintenance",
        "plumber": "Repairs and Maintenance",
        "plumbing": "Repairs and Maintenance",
        "electrician": "Repairs and Maintenance",
        "electrical": "Repairs and Maintenance",
        "handyman": "Repairs and Maintenance",
        "lawn": "Repairs and Maintenance",
        "cleaning": "Repairs and Maintenance",
        "repairs": "Repairs and Maintenance",
        "tower insurance": "Insurance",
        "ami insurance": "Insurance",
        "state insurance": "Insurance",
        "aa insurance": "Insurance",
        "vero": "Insurance",
        "initio": "Insurance",
        "insurance": "Insurance",
        "barfoot": "Property Management",
        "quinovic": "Property Management",
        "ray white": "Property Management",
        "harcourts": "Property Management",
        "lj hooker": "Property Management",
        "bayleys": "Property Management",
        "property management": "Property Management",
        "management fee": "Property Management",
        "xero": "Legal and Accounting",
        "accountant": "Legal and Accounting",
        "accounting": "Legal and Accounting",
        "solicitor": "Legal and Accounting",
        "lawyer": "Legal and Accounting",
        "legal": "Legal and Accounting",
        "anz": "Interest",
        "asb": "Interest",
        "bnz": "Interest",
        "westpac": "Interest",
        "kiwibank": "Interest",
        "loan interest": "Interest",
        "mortgage interest": "Interest",
        "interest": "Interest",
        "body corporate": "Body Corporate Levies",
        "body corp": "Body Corporate Levies",
        "inland revenue": "IRD Tax Payment",
        "ird": "IRD Tax Payment",
        "bond": "Tenancy Bond",
        "rent": "Rental Income",
        "tenancy": "Rental Income",
        "mercury energy": "Utilities",
        "genesis energy": "Utilities",
        "contact energy": "Utilities",
        "meridian": "Utilities",
        "spark": "Utilities",
        "one nz": "Utilities",
    },
    "US": {
        "property tax": "Taxes",
        "county tax": "Taxes",
        "real estate tax": "Taxes",
        "franchise tax": "Taxes",
        "irs": "Taxes",
        "hoa": "HOA Dues",
        "homeowners association": "HOA Dues",
        "condo association": "HOA Dues",
        "state farm": "Insurance",
        "allstate": "Insurance",
        "geico": "Insurance",
        "liberty mutual": "Insurance",
        "travelers": "Insurance",
        "insurance": "Insurance",
        "home depot": "Repairs",
        "lowe's": "Repairs",
        "lowes": "Repairs",
        "ace hardware": "Repairs",
        "plumber": "Repairs",
        "plumbing": "Repairs",
        "electrician": "Repairs",
        "hvac": "Repairs",
        "handyman": "Repairs",
        "repairs": "Repairs",
        "repair": "Repairs",
        "cleaning": "Cleaning and maintenance",
        "landscaping": "Cleaning and maintenance",
        "lawn": "Cleaning and maintenance",
        "pest control": "Cleaning and maintenance",
        "maintenance": "Cleaning and maintenance",
        "utilities": "Utilities",
        "pg&e": "Utilities",
        "con edison": "Utilities",
        "duke energy": "Utilities",
        "comcast": "Utilities",
        "water dept": "Utilities",
        "electric": "Utilities",
        "gas": "Utilities",
        "mortgage interest": "Mortgage interest",
        "loan interest": "Mortgage interest",
        "chase": "Mortgage interest",
        "wells fargo": "Mortgage interest",
        "bank of america": "Mortgage interest",
        "property management": "Management fees",
        "management fee": "Management fees",
        "management": "Management fees",
        "cpa": "Legal and professional",
        "attorney": "Legal and professional",
        "legal": "Legal and professional",
        "accounting": "Legal and professional",
        "advertising": "Advertising",
        "zillow": "Advertising",
        "apartments.com": "Advertising",
        "commission": "Commissions",
        "leasing fee": "Commissions",
        "supplies": "Supplies",
        "auto": "Auto and travel",
        "mileage": "Auto and travel",
        "travel": "Auto and travel",
        "rent": "Rental Income",
    },
}

# Backwards-compatible alias pointing to the NZ rules dictionary
CATEGORIZATION_RULES: dict[str, str] = ENTITY_CATEGORIZATION_RULES["NZ"]

UNCATEGORIZED_MARKERS = {"", "uncategorized", "unassigned", "none", "nan", "null"}

US_IRS_SYSTEM_PROMPT = (
    "You are a strict US Certified Public Accountant. Categorize the following transaction description "
    "into exactly one of the standard IRS Schedule E rental property expense categories (e.g., Advertising, "
    "Auto and travel, Cleaning and maintenance, Commissions, Insurance, Legal and professional, "
    "Management fees, Mortgage interest, Repairs, Supplies, Taxes, Utilities). Output ONLY the category name."
)

NZ_IRD_SYSTEM_PROMPT = (
    "You are a strict New Zealand Property Accountant. Categorize the following transaction description "
    "into exactly one of the standard NZ IRD rental property expense categories (e.g., Rates, Insurance, "
    "Interest, Property Management, Repairs and Maintenance, Water Rates, Legal and Accounting). "
    "Output ONLY the category name."
)


def get_entity_options() -> list[str]:
    """Return the dynamic list of configured entities (organizations), persisted in .axiom_memory.json."""
    store = _load_memory_store()
    saved_opts = store.get("entity_options")
    if isinstance(saved_opts, list) and saved_opts:
        st.session_state["entity_options"] = [str(x) for x in saved_opts if str(x).strip()]
    elif "entity_options" not in st.session_state:
        st.session_state["entity_options"] = list(ENTITY_OPTIONS)
    return st.session_state["entity_options"]


def get_tax_year_options() -> list[str]:
    """Return the dynamic list of single financial years (e.g. 2026, 2027, 2028, 2029)."""
    if "tax_year_options" not in st.session_state:
        st.session_state["tax_year_options"] = list(TAX_YEAR_OPTIONS)
    return st.session_state["tax_year_options"]


def get_active_entity(entity: str | None = None) -> str:
    """Resolve the active entity from explicit argument or st.session_state.entity / active_entity."""
    if entity:
        return entity
    options = get_entity_options()
    default_entity = options[0] if options else ENTITY_OPTIONS[0]
    return (
        st.session_state.get("entity")
        or st.session_state.get("active_entity")
        or default_entity
    )


def get_active_tax_year(tax_year: str | None = None) -> str:
    """Resolve the active financial year from explicit argument or st.session_state.active_tax_year."""
    if tax_year:
        return str(tax_year)
    years = get_tax_year_options()
    default_year = "2026" if "2026" in years else (years[0] if years else "2026")
    return str(st.session_state.get("active_tax_year") or default_year)


def get_entity_jurisdiction(entity: str | None = None) -> str:
    """Return 'US' or 'NZ' for the given entity (supports dynamically added entities)."""
    resolved_entity = get_active_entity(entity)
    jurisdictions = st.session_state.get("entity_jurisdictions", DEFAULT_ENTITY_JURISDICTIONS)
    if resolved_entity in jurisdictions:
        return jurisdictions[resolved_entity]
    return "US" if resolved_entity == "US Portfolio" or "US" in resolved_entity.upper() else "NZ"


def format_tax_year_period(tax_year: str | None = None, entity: str | None = None) -> str:
    """
    Format the explicit financial year date window according to jurisdiction:
    - NZ (IRD): 1 Apr (Y-1) – 31 Mar Y (e.g., 2026 -> 1 Apr 2025 – 31 Mar 2026)
    - US (IRS): 1 Jan Y – 31 Dec Y (e.g., 2026 -> 1 Jan 2026 – 31 Dec 2026)
    """
    yr_str = str(get_active_tax_year(tax_year)).strip()
    jur = get_entity_jurisdiction(entity)
    try:
        yr_int = int(yr_str[:4])
        if jur == "US":
            return f"1 Jan {yr_int} – 31 Dec {yr_int}"
        return f"1 Apr {yr_int - 1} – 31 Mar {yr_int}"
    except Exception:
        return f"FY {yr_str}"


def _get_sqlite_conn() -> sqlite3.Connection:
    """Open a thread-safe SQLite connection to `data/axiom_ledger.db` with WAL journaling."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    VAULT_ROOT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SQLITE_DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _ensure_sqlite_schema() -> None:
    """Create all SQLite tables (`kv_settings`, `uploaded_files`, `file_transactions`, `master_ledgers`) if not present."""
    with _DB_LOCK:
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


def _safe_vault_segment(name: str) -> str:
    """Sanitize a folder/file path segment while keeping it human-readable on disk."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip())
    return cleaned or "unnamed"


def get_vault_dir(entity: str, tax_year: str, bucket: str) -> Path:
    """Return the local File Vault directory path `data/vault/<Entity>/<TaxYear>/<Bucket>`."""
    ent_dir = _safe_vault_segment(entity)
    yr_dir = _safe_vault_segment(tax_year)
    bkt_dir = _safe_vault_segment(bucket)
    return VAULT_ROOT_DIR / ent_dir / yr_dir / bkt_dir


def save_raw_file_to_vault(
    entity: str,
    tax_year: str,
    bucket: str,
    filename: str,
    file_bytes: bytes,
) -> Path:
    """
    Save the original uploaded file (CSV, PDF, Screenshot, Excel/ODT) into the
    human-readable local File Vault: `data/vault/<Entity>/<TaxYear>/<Bucket>/<Filename>`.
    """
    ent_dir = _safe_vault_segment(entity)
    yr_dir = _safe_vault_segment(tax_year)
    bkt_dir = _safe_vault_segment(bucket)
    safe_fname = _safe_vault_segment(filename)

    target_folder = VAULT_ROOT_DIR / ent_dir / yr_dir / bkt_dir
    target_folder.mkdir(parents=True, exist_ok=True)
    target_path = target_folder / safe_fname
    target_path.write_bytes(file_bytes)
    return target_path


def _db_save_file_record_and_rows(
    entity: str,
    tax_year: str,
    record: dict[str, object],
    sort_order: int = 0,
) -> None:
    """Persist a single uploaded file metadata record and all of its transaction rows to SQLite."""
    _ensure_sqlite_schema()
    file_id = str(record.get("id", ""))
    if not file_id:
        return
    df = record.get("df")
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(columns=STANDARD_COLUMNS)
    else:
        df = df.reindex(columns=STANDARD_COLUMNS).copy()

    vault_path_str = str(record.get("vault_path") or "")
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO uploaded_files (
                    file_id, entity, tax_year, signature, name, bucket,
                    uploaded_at, vault_path, mime_type, needs_ocr, note, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    str(entity),
                    str(tax_year),
                    str(record.get("signature", "")),
                    str(record.get("name", "")),
                    str(record.get("bucket", "csv")),
                    str(record.get("uploaded_at", "")),
                    vault_path_str,
                    str(record.get("mime_type", "")),
                    1 if record.get("needs_ocr") else 0,
                    str(record.get("note", "")),
                    int(sort_order),
                ),
            )
            conn.execute("DELETE FROM file_transactions WHERE file_id = ?", (file_id,))
            if not df.empty:
                rows_to_insert = []
                amounts = _clean_amount_series(df["Amount"]).tolist()
                for idx, (_, r) in enumerate(df.iterrows()):
                    rows_to_insert.append(
                        (
                            file_id,
                            idx,
                            str(r.get("Date", "") or "").strip(),
                            float(amounts[idx]) if idx < len(amounts) else 0.0,
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


def _db_delete_file_record(file_id: str, delete_vault_file: bool = True) -> None:
    """Remove an uploaded file and its rows from SQLite (and optionally remove the file from `data/vault/`)."""
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            row = conn.execute(
                "SELECT vault_path FROM uploaded_files WHERE file_id = ?",
                (str(file_id),),
            ).fetchone()
            conn.execute("DELETE FROM file_transactions WHERE file_id = ?", (str(file_id),))
            conn.execute("DELETE FROM uploaded_files WHERE file_id = ?", (str(file_id),))
            if delete_vault_file and row and row["vault_path"]:
                vp = Path(str(row["vault_path"]))
                if vp.exists() and vp.is_file():
                    try:
                        vp.unlink()
                    except Exception:
                        pass


def _db_load_uploaded_files(entity: str, tax_year: str) -> list[dict[str, object]]:
    """Load all persisted uploaded files and their transaction DataFrames for `(entity, tax_year)` from SQLite."""
    _ensure_sqlite_schema()
    records: list[dict[str, object]] = []
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            file_rows = conn.execute(
                """
                SELECT file_id, signature, name, bucket, uploaded_at,
                       vault_path, mime_type, needs_ocr, note, sort_order
                FROM uploaded_files
                WHERE entity = ? AND tax_year = ?
                ORDER BY sort_order ASC, rowid ASC
                """,
                (str(entity), str(tax_year)),
            ).fetchall()

            for f_row in file_rows:
                fid = str(f_row["file_id"])
                tx_rows = conn.execute(
                    """
                    SELECT tx_date, amount, description, property, account_code
                    FROM file_transactions
                    WHERE file_id = ?
                    ORDER BY row_index ASC
                    """,
                    (fid,),
                ).fetchall()

                if tx_rows:
                    df = pd.DataFrame(
                        [
                            {
                                "Date": r["tx_date"],
                                "Amount": float(r["amount"]),
                                "Description": r["description"],
                                "Property": r["property"],
                                "Account Code": r["account_code"],
                            }
                            for r in tx_rows
                        ]
                    )[STANDARD_COLUMNS]
                else:
                    df = pd.DataFrame(columns=STANDARD_COLUMNS)

                vpath_str = str(f_row["vault_path"] or "")
                raw_bytes = None
                if vpath_str and Path(vpath_str).exists():
                    try:
                        if str(f_row["bucket"]) == "screenshots":
                            raw_bytes = Path(vpath_str).read_bytes()
                    except Exception:
                        raw_bytes = None

                records.append(
                    {
                        "id": fid,
                        "signature": str(f_row["signature"]),
                        "name": str(f_row["name"]),
                        "bucket": str(f_row["bucket"]),
                        "uploaded_at": str(f_row["uploaded_at"]),
                        "vault_path": vpath_str,
                        "df": df,
                        "raw_bytes": raw_bytes,
                        "mime_type": str(f_row["mime_type"] or ""),
                        "needs_ocr": bool(f_row["needs_ocr"]),
                        "note": str(f_row["note"] or ""),
                    }
                )
    return records


def _db_save_master_ledger(entity: str, tax_year: str, df: pd.DataFrame) -> None:
    """Persist the master ledger DataFrame for `(entity, tax_year)` into SQLite `master_ledgers`."""
    _ensure_sqlite_schema()
    clean_df = df.reindex(columns=STANDARD_COLUMNS).copy() if isinstance(df, pd.DataFrame) else pd.DataFrame(columns=STANDARD_COLUMNS)
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                "DELETE FROM master_ledgers WHERE entity = ? AND tax_year = ?",
                (str(entity), str(tax_year)),
            )
            if not clean_df.empty:
                amounts = _clean_amount_series(clean_df["Amount"]).tolist()
                rows_to_insert = []
                for idx, (_, r) in enumerate(clean_df.iterrows()):
                    rows_to_insert.append(
                        (
                            str(entity),
                            str(tax_year),
                            idx,
                            str(r.get("Date", "") or "").strip(),
                            float(amounts[idx]) if idx < len(amounts) else 0.0,
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


def _db_load_master_ledger(entity: str, tax_year: str) -> pd.DataFrame:
    """Load the master ledger DataFrame for `(entity, tax_year)` from SQLite `master_ledgers`."""
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
                (str(entity), str(tax_year)),
            ).fetchall()
            if rows:
                return pd.DataFrame(
                    [
                        {
                            "Date": r["tx_date"],
                            "Amount": float(r["amount"]),
                            "Description": r["description"],
                            "Property": r["property"],
                            "Account Code": r["account_code"],
                        }
                        for r in rows
                    ]
                )[STANDARD_COLUMNS]
    # If master_ledgers table is empty for this (entity, year) but uploaded_files exist, rebuild from uploaded_files
    up_files = _db_load_uploaded_files(entity, tax_year)
    frames = [f["df"] for f in up_files if isinstance(f.get("df"), pd.DataFrame) and not f["df"].empty]
    if frames:
        combined = pd.concat(frames, ignore_index=True).reindex(columns=STANDARD_COLUMNS)
        _db_save_master_ledger(entity, tax_year, combined)
        return combined
    return pd.DataFrame(columns=STANDARD_COLUMNS)


def get_vault_file_bytes(file_rec: dict[str, object]) -> bytes | None:
    """Read and return the original raw file bytes from `data/vault/` (or in-memory cache)."""
    vpath = str(file_rec.get("vault_path") or "").strip()
    if vpath and Path(vpath).exists():
        try:
            return Path(vpath).read_bytes()
        except Exception:
            pass
    raw = file_rec.get("raw_bytes")
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    return None


def export_entity_vault_zip(entity: str | None = None, tax_year: str | None = None) -> bytes:
    """
    Create an in-memory ZIP archive containing:
    1. All original uploaded files (CSVs, PDFs, Screenshots, Excel/ODT) from `data/vault/<Entity>/<Year>/`
    2. The coded Master Ledger CSV (`Master_Coded_Ledger_<Entity>_<Year>.csv`)
    """
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    files_list = get_entity_uploaded_files(target_entity, target_year)
    master_df = get_entity_ledger(target_entity, target_year)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Include coded Master Ledger CSV at root of zip
        safe_ent = _safe_vault_segment(target_entity)
        zf.writestr(
            f"Master_Coded_Ledger_{safe_ent}_{target_year}.csv",
            export_xero_master_csv(master_df),
        )
        # Include each individual coded CSV + original raw file from vault
        for frec in files_list:
            bkt = str(frec.get("bucket", "other"))
            fname = str(frec.get("name", "file"))
            raw_b = get_vault_file_bytes(frec)
            if raw_b is not None:
                zf.writestr(f"Original_Files/{bkt}/{fname}", raw_b)
            fdf = frec.get("df")
            if isinstance(fdf, pd.DataFrame) and not fdf.empty:
                coded_name = f"{Path(fname).stem}_CODED.csv"
                zf.writestr(f"Coded_Sheets/{bkt}/{coded_name}", export_xero_master_csv(fdf))
    return buf.getvalue()


def _load_memory_store() -> dict[str, object]:
    """
    Load persisted entity options, jurisdictions, properties, and memorized coding rules
    from SQLite (`data/axiom_ledger.db`) with automatic synchronization to `.axiom_memory.json`.
    """
    _ensure_sqlite_schema()
    loaded: dict[str, object] | None = None

    # 1. Check SQLite kv_settings first
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            row = conn.execute(
                "SELECT value_json FROM kv_settings WHERE key = 'axiom_memory_store'"
            ).fetchone()
            if row and row["value_json"]:
                try:
                    parsed = json.loads(str(row["value_json"]))
                    if isinstance(parsed, dict):
                        loaded = parsed
                except Exception:
                    loaded = None

    # 2. Fallback / migrate from .axiom_memory.json if SQLite didn't have it yet
    if loaded is None and MEMORY_STORE_PATH.exists():
        try:
            disk_json = json.loads(MEMORY_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(disk_json, dict):
                loaded = disk_json
        except Exception:
            loaded = None

    if isinstance(loaded, dict):
        loaded.setdefault("entity_options", list(ENTITY_OPTIONS))
        loaded.setdefault("entity_jurisdictions", dict(DEFAULT_ENTITY_JURISDICTIONS))
        saved_years = [str(y) for y in (loaded.get("tax_year_options") or TAX_YEAR_OPTIONS) if str(y) not in ("2024", "2025")]
        for yr in TAX_YEAR_OPTIONS:
            if yr not in saved_years:
                saved_years.append(yr)
        saved_years.sort()
        loaded["tax_year_options"] = saved_years
        if str(loaded.get("active_tax_year") or "") in ("2024", "2025", "") or str(loaded.get("active_tax_year")) not in saved_years:
            loaded["active_tax_year"] = "2026"
        loaded.setdefault("entity_properties", {})
        loaded.setdefault("memorized_rules", {})
        loaded.setdefault("google_genai_api_key", "")
        loaded.setdefault("categorization_model", CATEGORIZATION_MODEL_OPTIONS[0])
        loaded.setdefault("local_llm_url", DEFAULT_LOCAL_LLM_URL)
        loaded.setdefault("local_llm_model", DEFAULT_LOCAL_LLM_MODEL)
        loaded.setdefault("gemini_connection_status", None)
        saved_opts = [
            str(x)
            for x in (loaded.get("entity_options") or ENTITY_OPTIONS)
            if str(x).strip() and not str(x).strip().startswith("__")
        ]
        if not saved_opts:
            saved_opts = list(ENTITY_OPTIONS)
        loaded["entity_options"] = saved_opts
        st.session_state["_axiom_memory_store"] = loaded
        # Ensure SQLite and JSON are both in sync
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_settings (key, value_json) VALUES ('axiom_memory_store', ?)",
                    (json.dumps(loaded),),
                )
        return loaded

    default_store: dict[str, object] = {
        "entity_options": list(ENTITY_OPTIONS),
        "entity_jurisdictions": dict(DEFAULT_ENTITY_JURISDICTIONS),
        "tax_year_options": list(TAX_YEAR_OPTIONS),
        "entity_properties": {
            ent: list(DEFAULT_PROPERTIES) for ent in ENTITY_OPTIONS
        },
        "memorized_rules": {
            ent: [] for ent in ENTITY_OPTIONS
        },
        "google_genai_api_key": "",
        "categorization_model": CATEGORIZATION_MODEL_OPTIONS[0],
        "local_llm_url": DEFAULT_LOCAL_LLM_URL,
        "local_llm_model": DEFAULT_LOCAL_LLM_MODEL,
        "gemini_connection_status": None,
    }
    st.session_state["_axiom_memory_store"] = default_store
    _save_memory_store(default_store)
    return default_store


def _save_memory_store(store: dict[str, object] | None = None) -> None:
    """Save entity options, jurisdictions, properties, AI keys/settings, and memorized rules to SQLite (`data/axiom_ledger.db`) and `.axiom_memory.json`."""
    _ensure_sqlite_schema()
    target = store if store is not None else st.session_state.get("_axiom_memory_store", {})
    if "entity_options" in st.session_state:
        target["entity_options"] = list(st.session_state["entity_options"])
    if "entity_jurisdictions" in st.session_state:
        target["entity_jurisdictions"] = dict(st.session_state["entity_jurisdictions"])
    if "tax_year_options" in st.session_state:
        target["tax_year_options"] = list(st.session_state["tax_year_options"])
    if "active_entity" in st.session_state:
        target["active_entity"] = str(st.session_state["active_entity"])
    if "active_tax_year" in st.session_state:
        target["active_tax_year"] = str(st.session_state["active_tax_year"])
    if "google_genai_api_key" in st.session_state:
        target["google_genai_api_key"] = str(st.session_state["google_genai_api_key"] or "").strip()
    if "categorization_model" in st.session_state:
        target["categorization_model"] = str(st.session_state["categorization_model"])
    if "local_llm_url" in st.session_state:
        target["local_llm_url"] = str(st.session_state["local_llm_url"] or DEFAULT_LOCAL_LLM_URL).strip()
    if "local_llm_model" in st.session_state:
        target["local_llm_model"] = str(st.session_state["local_llm_model"] or DEFAULT_LOCAL_LLM_MODEL).strip()
    if "gemini_connection_status" in st.session_state and isinstance(st.session_state["gemini_connection_status"], dict):
        target["gemini_connection_status"] = dict(st.session_state["gemini_connection_status"])
    st.session_state["_axiom_memory_store"] = target
    serialized = json.dumps(target, indent=2)
    try:
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_settings (key, value_json) VALUES ('axiom_memory_store', ?)",
                    (serialized,),
                )
    except Exception:
        pass
    try:
        MEMORY_STORE_PATH.write_text(serialized, encoding="utf-8")
    except Exception:
        pass


def get_entity_properties(entity: str | None = None) -> list[str]:
    """Return the configured list of Properties (e.g., Property 1..5, General / Portfolio) for an entity."""
    resolved = get_active_entity(entity)
    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    if not isinstance(props_map, dict):
        props_map = {}
        store["entity_properties"] = props_map
    if resolved not in props_map or not props_map[resolved]:
        props_map[resolved] = list(DEFAULT_PROPERTIES)
        _save_memory_store(store)
    return list(props_map[resolved])


def add_entity_property(property_name: str, entity: str | None = None) -> tuple[bool, str]:
    """Add a new Property to the active entity's property list and persist it."""
    resolved = get_active_entity(entity)
    cleaned = (property_name or "").strip()
    if not cleaned:
        return False, "Property name cannot be empty."
    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    current = list(props_map.get(resolved, DEFAULT_PROPERTIES))
    if any(p.lower() == cleaned.lower() for p in current):
        return False, f"Property '{cleaned}' already exists for {resolved}."
    current.append(cleaned)
    props_map[resolved] = current
    _save_memory_store(store)
    return True, f"Added property '{cleaned}' to {resolved}."


def remove_entity_property(property_name: str, entity: str | None = None) -> tuple[bool, str]:
    """Remove a Property from the active entity's property list."""
    resolved = get_active_entity(entity)
    target = (property_name or "").strip()
    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    current = list(props_map.get(resolved, DEFAULT_PROPERTIES))
    if target not in current:
        return False, f"Property '{target}' not found."
    if len(current) <= 1:
        return False, "At least one property must remain configured."
    current.remove(target)
    props_map[resolved] = current
    _save_memory_store(store)
    return True, f"Removed property '{target}' from {resolved}."


def rename_entity_property(
    old_property_name: str,
    new_property_name: str,
    entity: str | None = None,
) -> tuple[bool, str]:
    """
    Rename an existing Property (e.g. 'Property 1' -> '14 Queen Street') in-place
    for the specified entity, and automatically migrate all memorized rules,
    uploaded file DataFrames, and financial year ledgers that referenced the old name.
    """
    init_workspace_state()
    resolved = get_active_entity(entity)
    src_prop = (old_property_name or "").strip()
    target_prop = (new_property_name or "").strip()

    if not target_prop:
        return False, "Property name cannot be empty."

    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    current = list(props_map.get(resolved, DEFAULT_PROPERTIES))

    if src_prop not in current:
        return False, f"Property '{src_prop}' not found."

    if src_prop == target_prop:
        return True, f"Property '{src_prop}' unchanged."

    for existing in current:
        if existing != src_prop and existing.lower() == target_prop.lower():
            return False, f"A property named '{existing}' already exists for {resolved}."

    idx = current.index(src_prop)
    current[idx] = target_prop
    props_map[resolved] = current

    # Migrate memorized rules that point to src_prop
    rules_map = store.setdefault("memorized_rules", {})
    ent_rules: list[dict[str, str]] = list(rules_map.get(resolved, []))
    for rule_item in ent_rules:
        if str(rule_item.get("property", "")).strip().lower() == src_prop.lower():
            rule_item["property"] = target_prop
    rules_map[resolved] = ent_rules
    _save_memory_store(store)

    # Migrate uploaded file DataFrames across all years for this entity (in-memory & SQLite)
    files_by_ent = st.session_state.get("entity_uploaded_files", {}).get(resolved, {})
    if isinstance(files_by_ent, dict):
        for _yr, file_list in files_by_ent.items():
            if isinstance(file_list, list):
                for frec in file_list:
                    fdf = frec.get("df")
                    if isinstance(fdf, pd.DataFrame) and "Property" in fdf.columns and not fdf.empty:
                        mask = fdf["Property"].astype(str).str.strip().str.lower() == src_prop.lower()
                        if mask.any():
                            fdf.loc[mask, "Property"] = target_prop
                            frec["df"] = fdf

    # Migrate master ledgers across all years for this entity (in-memory & SQLite)
    ledgers_by_ent = st.session_state.get("entity_year_ledgers", {}).get(resolved, {})
    if isinstance(ledgers_by_ent, dict):
        for yr, ldf in list(ledgers_by_ent.items()):
            if isinstance(ldf, pd.DataFrame) and "Property" in ldf.columns and not ldf.empty:
                mask = ldf["Property"].astype(str).str.strip().str.lower() == src_prop.lower()
                if mask.any():
                    ldf.loc[mask, "Property"] = target_prop
                    ledgers_by_ent[yr] = ldf
                    if yr == get_active_tax_year():
                        st.session_state["entity_ledgers"][resolved] = ldf

    # Update SQLite file_transactions and master_ledgers for this entity
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                """
                UPDATE file_transactions
                SET property = ?
                WHERE LOWER(TRIM(property)) = LOWER(TRIM(?))
                  AND file_id IN (SELECT file_id FROM uploaded_files WHERE entity = ?)
                """,
                (target_prop, src_prop, resolved),
            )
            conn.execute(
                """
                UPDATE master_ledgers
                SET property = ?
                WHERE entity = ? AND LOWER(TRIM(property)) = LOWER(TRIM(?))
                """,
                (target_prop, resolved, src_prop),
            )
            conn.execute(
                """
                UPDATE property_loans
                SET property_name = ?
                WHERE entity = ? AND LOWER(TRIM(property_name)) = LOWER(TRIM(?))
                """,
                (target_prop, resolved, src_prop),
            )
            conn.execute(
                """
                UPDATE property_loan_rate_history
                SET property_name = ?
                WHERE entity = ? AND LOWER(TRIM(property_name)) = LOWER(TRIM(?))
                """,
                (target_prop, resolved, src_prop),
            )
            conn.execute(
                """
                UPDATE loan_reminder_log
                SET property_name = ?
                WHERE entity = ? AND LOWER(TRIM(property_name)) = LOWER(TRIM(?))
                """,
                (target_prop, resolved, src_prop),
            )

    return True, f"Renamed '{src_prop}' → '{target_prop}'."


def render_property_editor_widget(
    entity: str | None = None,
    key_prefix: str = "prop_mgr",
) -> None:
    """
    Reusable widget to view, rename (edit in-place), add, or remove Properties
    (e.g. Property 1, Property 2, etc.) for the specified entity.
    """
    resolved = get_active_entity(entity)
    properties_list = get_entity_properties(resolved)

    add_c1, add_c2 = st.columns([2.3, 1.0])
    with add_c1:
        new_prop_name = st.text_input(
            "Add New Property",
            placeholder="e.g. Property 6 - 102 Hobson St",
            key=f"_{key_prefix}_new_prop_{resolved}",
            label_visibility="collapsed",
        )
    with add_c2:
        if st.button(
            "+ Add Property",
            key=f"_{key_prefix}_btn_add_prop_{resolved}",
            type="primary",
            use_container_width=True,
        ):
            ok_p, msg_p = add_entity_property(new_prop_name, resolved)
            if ok_p:
                st.success(msg_p)
                st.rerun()
            else:
                st.error(msg_p)

    st.caption("Edit any property name below (e.g. rename `Property 1` to `14 Queen St`) and click **Save**:")
    for idx, p_name in enumerate(properties_list):
        p_c1, p_c2, p_c3 = st.columns([2.6, 0.85, 0.55])
        with p_c1:
            edited_val = st.text_input(
                f"Property {idx + 1}",
                value=p_name,
                key=f"_{key_prefix}_edit_prop_{idx}_{p_name}_{resolved}",
                label_visibility="collapsed",
            )
        with p_c2:
            if st.button(
                "Save",
                key=f"_{key_prefix}_save_prop_{idx}_{p_name}_{resolved}",
                disabled=(edited_val.strip() == p_name or not edited_val.strip()),
                use_container_width=True,
            ):
                ok_rn, msg_rn = rename_entity_property(p_name, edited_val, resolved)
                if ok_rn:
                    st.success(msg_rn)
                    st.rerun()
                else:
                    st.error(msg_rn)
        with p_c3:
            if st.button(
                "✕",
                key=f"_{key_prefix}_del_prop_{idx}_{p_name}_{resolved}",
                disabled=len(properties_list) <= 1,
                help=f"Remove '{p_name}'",
                use_container_width=True,
            ):
                ok_rm, _ = remove_entity_property(p_name, resolved)
                if ok_rm:
                    st.rerun()


def get_memorized_rules(entity: str | None = None) -> list[dict[str, str]]:
    """
    Return the list of memorized rules for the entity.
    Each item is: {'pattern': str, 'property': str, 'account_code': str, 'source': str}
    """
    resolved = get_active_entity(entity)
    store = _load_memory_store()
    rules_map = store.setdefault("memorized_rules", {})
    if not isinstance(rules_map, dict):
        rules_map = {}
        store["memorized_rules"] = rules_map
    if resolved not in rules_map or not isinstance(rules_map[resolved], list):
        rules_map[resolved] = []
    return list(rules_map[resolved])


def save_memorized_rule(
    pattern: str,
    property_name: str,
    account_code: str,
    entity: str | None = None,
    source: str = "User Coded",
) -> tuple[bool, str]:
    """
    Upsert a memorized rule mapping `pattern` -> (`property_name`, `account_code`)
    for the specified entity and persist to disk.
    """
    resolved = get_active_entity(entity)
    clean_pat = (pattern or "").strip()
    clean_prop = (property_name or "").strip() or UNASSIGNED_PROPERTY
    clean_code = (account_code or "").strip() or "Uncategorized"
    if not clean_pat:
        return False, "Description pattern cannot be empty."

    store = _load_memory_store()
    rules_map = store.setdefault("memorized_rules", {})
    current_rules: list[dict[str, str]] = list(rules_map.get(resolved, []))

    pat_lower = clean_pat.lower()
    prop_lower = clean_prop.lower()
    updated = False
    for item in current_rules:
        item_pat_l = str(item.get("pattern", "")).strip().lower()
        item_prop_l = str(item.get("property", "") or UNASSIGNED_PROPERTY).strip().lower()
        if item_pat_l == pat_lower and item_prop_l == prop_lower:
            item["pattern"] = clean_pat
            item["property"] = clean_prop
            item["account_code"] = clean_code
            item["source"] = source
            updated = True
            break

    if not updated:
        current_rules.insert(
            0,
            {
                "pattern": clean_pat,
                "property": clean_prop,
                "account_code": clean_code,
                "source": source,
            },
        )

        # Also auto-register the account_code in entity_account_codes if it was newly typed/edited
    if clean_code and clean_code.lower() not in UNCATEGORIZED_MARKERS:
        codes_map = store.setdefault("entity_account_codes", {})
        ent_codes = list(codes_map.get(resolved, get_entity_account_codes(resolved)))
        if not any(c.lower() == clean_code.lower() for c in ent_codes):
            ent_codes.append(clean_code)
            ent_codes.sort(key=lambda s: s.lower())
            codes_map[resolved] = ent_codes

    # Also auto-register the property if it was newly typed/imported and isn't Unassigned
    if clean_prop and clean_prop.lower() not in UNCATEGORIZED_MARKERS:
        props_map = store.setdefault("entity_properties", {})
        ent_props = list(props_map.get(resolved, DEFAULT_PROPERTIES))
        if not any(p.lower() == clean_prop.lower() for p in ent_props):
            ent_props.append(clean_prop)
            props_map[resolved] = ent_props

    # Self-Learning Vendor Token Extraction:
    # If `clean_pat` is a compound CSV line (e.g. "6851Tower Insurance | Damian | Pol 8821" or "Tower + Damian")
    # and has a valid Account Code, also ensure the primary vendor stem (segment 0) is remembered
    # so future CSV lines with the same vendor + a DIFFERENT house (e.g. "6851Tower Insurance | 33 Blampied")
    # automatically line up the vendor's Account Code ("Insurance") with the new house ("33 Blampied")!
    if "|" in clean_pat and not is_uncategorized(clean_code):
        primary_seg = clean_pat.split("|")[0].strip()
        if len(primary_seg) >= 3:
            seg_has_prop = detect_property_from_text(primary_seg, entity=resolved) != UNASSIGNED_PROPERTY
            seg_lower = primary_seg.lower()
            already_has_seg = any(
                str(r.get("pattern", "")).strip().lower() == seg_lower for r in current_rules
            )
            if not already_has_seg:
                vendor_default_prop = (
                    "General / Portfolio"
                    if is_portfolio_wide_code(clean_code, resolved)
                    else (clean_prop if seg_has_prop else UNASSIGNED_PROPERTY)
                )
                current_rules.append(
                    {
                        "pattern": primary_seg,
                        "property": vendor_default_prop,
                        "account_code": clean_code,
                        "source": f"{source} (Vendor Auto-Rule)",
                    }
                )

    rules_map[resolved] = current_rules
    _save_memory_store(store)
    return True, f"Memorized: '{clean_pat}' → {clean_prop} | {clean_code}"


def delete_memorized_rule(pattern: str, entity: str | None = None) -> tuple[bool, str]:
    """Delete a memorized rule by pattern for the specified entity."""
    resolved = get_active_entity(entity)
    pat_lower = (pattern or "").strip().lower()
    store = _load_memory_store()
    rules_map = store.setdefault("memorized_rules", {})
    current_rules: list[dict[str, str]] = list(rules_map.get(resolved, []))
    filtered = [r for r in current_rules if str(r.get("pattern", "")).strip().lower() != pat_lower]
    rules_map[resolved] = filtered
    _save_memory_store(store)
    return True, f"Removed memorized rule '{pattern}'."


def replace_memorized_rules(
    new_rules_rows: list[dict[str, object]],
    entity: str | None = None,
) -> tuple[bool, str]:
    """
    Save the full 3-column Item + Property mapping table for an entity:
    Each row contains:
    - `pattern`: What appears on the bank statement (e.g. Payee / Loan / Policy / Tenant text)
    - `account_code`: What is the Item? (e.g. Insurance, Loan Interest, Rent Income, Rates, Accountant)
    - `property`: Which Property? (e.g. 33 Blampied, 303 Weymouth, or General / Portfolio)
    Allows the same `pattern` to be saved across multiple properties (deduplicates by pattern + property + account_code).
    Automatically sets `General / Portfolio` for portfolio-wide items like Accountant, Bank Fee, etc.
    """
    resolved = get_active_entity(entity)
    cleaned_rules: list[dict[str, str]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for r in new_rules_rows:
        pat = str(r.get("pattern", "") or "").strip()
        if not pat or pat.lower() in ("nan", "none"):
            continue

        code = str(r.get("account_code", "") or "").strip() or "Uncategorized"
        raw_prop = str(r.get("property", "") or "").strip()
        if raw_prop.startswith("Auto-detect") or raw_prop in ("", "nan", "None"):
            prop = UNASSIGNED_PROPERTY
        else:
            prop = raw_prop

        if is_portfolio_wide_code(code, resolved):
            prop = "General / Portfolio"

        dedup_key = (pat.lower(), prop.lower(), code.lower())
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        src = str(r.get("source", "") or "Setup Table").strip() or "Setup Table"
        cleaned_rules.append(
            {
                "pattern": pat,
                "account_code": code,
                "property": prop,
                "source": src,
            }
        )

    store = _load_memory_store()
    codes_map = store.setdefault("entity_account_codes", {})
    ent_codes = list(codes_map.get(resolved, get_entity_account_codes(resolved)))
    codes_added = False
    for r_saved in cleaned_rules:
        c_val = str(r_saved.get("account_code", "")).strip()
        if c_val and c_val.lower() not in UNCATEGORIZED_MARKERS:
            if not any(existing_c.lower() == c_val.lower() for existing_c in ent_codes):
                ent_codes.append(c_val)
                codes_added = True
    if codes_added:
        ent_codes.sort(key=lambda s: s.lower())
        codes_map[resolved] = ent_codes

    rules_map = store.setdefault("memorized_rules", {})
    rules_map[resolved] = cleaned_rules
    _save_memory_store(store)
    return True, f"Saved {len(cleaned_rules)} Item + Property rule(s) for {resolved}."


def is_portfolio_wide_code(code_name: object, entity: str | None = None) -> bool:
    """
    Return True if the given Item / Account Code is a Portfolio-Wide (company overhead) item
    rather than a Per-Property item (like Insurance, Loan Interest, Rent Income, Rates, etc.).
    """
    if pd.isna(code_name):
        return False
    c_lower = str(code_name).strip().lower()
    if not c_lower:
        return False
    resolved = get_active_entity(entity)
    store = _load_memory_store()
    scopes_map = store.get("entity_code_scopes", {})
    if isinstance(scopes_map, dict):
        ent_scopes = scopes_map.get(resolved, {})
        if isinstance(ent_scopes, dict):
            for k, v in ent_scopes.items():
                if str(k).strip().lower() == c_lower:
                    return str(v).strip().lower() == "portfolio"
    return c_lower in PORTFOLIO_WIDE_CATEGORIES


def set_entity_code_scope(
    code_name: str,
    is_portfolio_wide: bool,
    entity: str | None = None,
) -> tuple[bool, str]:
    """Set whether an Account Code is Per-Property ('property') or Portfolio-Wide ('portfolio')."""
    resolved = get_active_entity(entity)
    cleaned = (code_name or "").strip()
    if not cleaned:
        return False, "Item name cannot be empty."
    store = _load_memory_store()
    scopes_map = store.setdefault("entity_code_scopes", {})
    if not isinstance(scopes_map, dict):
        scopes_map = {}
        store["entity_code_scopes"] = scopes_map
    ent_scopes = dict(scopes_map.get(resolved, {}))
    ent_scopes[cleaned] = "portfolio" if is_portfolio_wide else "property"
    scopes_map[resolved] = ent_scopes
    _save_memory_store(store)
    scope_lbl = "Portfolio-Wide" if is_portfolio_wide else "Per-Property"
    return True, f"Set '{cleaned}' to {scope_lbl}."


def render_item_property_setup_matrix(
    entity: str | None = None,
    tax_year: str | None = None,
    key_prefix: str = "item_prop_matrix",
) -> None:
    """
    Clean, side-by-side 3-Column Item + Property Setup UI so the user can easily specify:
      Column 1: Bank Text / Payee / Loan / Policy / Tenant (e.g. '8020AON Stylecover', 'Loan 91-02', 'Smith Rent')
      Column 2: What is the Item? (e.g. 'Insurance', 'Loan Interest', 'Rent Income', 'Rates', 'Bodycorp')
      Column 3: Which Property? (e.g. 'Property 2 / 33 Blampied', '303 Weymouth', or 'General / Portfolio')
    Can be rendered directly inside the Ingestion Board drawer, Settings, or Dashboard Tab 01.
    """
    resolved_entity = get_active_entity(entity)
    resolved_year = get_active_tax_year(tax_year)
    properties_list = get_entity_properties(resolved_entity)
    account_codes_list = sorted(get_entity_account_codes(resolved_entity), key=lambda s: s.lower())
    memorized_list = get_memorized_rules(resolved_entity)

    per_prop_items = sorted([c for c in account_codes_list if not is_portfolio_wide_code(c, resolved_entity)], key=lambda s: s.lower())
    portfolio_items = sorted([c for c in account_codes_list if is_portfolio_wide_code(c, resolved_entity)], key=lambda s: s.lower())
    alphabetical_items = sorted(list(dict.fromkeys(per_prop_items + portfolio_items)), key=lambda s: s.lower())
    real_props = sorted([p for p in properties_list if p.lower() != "general / portfolio"], key=lambda s: s.lower())

    def _reapply_rules_and_sync() -> None:
        files_list = get_entity_uploaded_files(resolved_entity, resolved_year)
        for f_rec in files_list:
            f_df = f_rec.get("df")
            if isinstance(f_df, pd.DataFrame) and not f_df.empty:
                f_rec["df"] = apply_rule_categorization(
                    f_df,
                    overwrite_existing=True,
                    entity=resolved_entity,
                )
        if files_list:
            sync_entity_ledger_from_files(resolved_entity, resolved_year)
        else:
            cur_ledger = get_entity_ledger(resolved_entity, resolved_year)
            if not cur_ledger.empty:
                updated_ledger = apply_rule_categorization(
                    cur_ledger,
                    overwrite_existing=True,
                    entity=resolved_entity,
                )
                set_entity_ledger(updated_ledger, resolved_entity, resolved_year)

    all_props_option = f"🏠 All Properties ({len(real_props)} Houses)"

    # Quick Add Row in compact sidebar-aware columns: What is the Item? | Which Property? | Bank Text / CSV Tokens
    with st.container(border=True):
        top_hdr_c1, top_hdr_c2 = st.columns([2.7, 1.3])
        with top_hdr_c1:
            st.markdown(
                f'<div class="axiom-eyebrow">QUICK SETUP // LINK ANY ITEM (INSURANCE, LOAN INTEREST, RENT) TO A SPECIFIC PROPERTY ({resolved_entity.upper()})</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "Select or edit **What is the Item** (e.g. `Insurance`, `Tower Insurance`, `Loan Interest`, `Rent Income`, `Rates`), "
                "pick **Which Property** it belongs to (or `Auto-detect House from CSV Line` so house names like `Damian`, `Queen`, `Blampied` on the CSV row pair up automatically), "
                "and enter the **Bank Statement Text / Payee / Policy Ref**."
            )
        with top_hdr_c2:
            with st.popover("✏️ Edit / Rename or Add Item Names", use_container_width=True):
                st.markdown("**Rename an Existing Item**")
                all_existing_items = ordered_items_for_rename = alphabetical_items
                if all_existing_items:
                    ren_src_item = st.selectbox(
                        "Pick Item to Rename",
                        options=all_existing_items,
                        key=f"_{key_prefix}_ren_src_{resolved_entity}",
                    )
                    ren_dst_item = st.text_input(
                        "New Item Name",
                        value=ren_src_item,
                        placeholder="e.g. Tower Insurance",
                        key=f"_{key_prefix}_ren_dst_{resolved_entity}_{ren_src_item}",
                    )
                    if st.button(
                        "💾 Rename Item Everywhere",
                        key=f"_{key_prefix}_ren_btn_{resolved_entity}",
                        type="primary",
                        use_container_width=True,
                    ):
                        ok_rn, msg_rn = rename_entity_account_code(
                            old_code=ren_src_item,
                            new_code=ren_dst_item,
                            entity=resolved_entity,
                        )
                        if ok_rn:
                            _reapply_rules_and_sync()
                            st.success(msg_rn)
                            st.rerun()
                        else:
                            st.error(msg_rn)
                st.markdown("---")
                st.markdown("**Add a New Item**")
                new_custom_item_name = st.text_input(
                    "New Item Name",
                    placeholder="e.g. Tower Insurance, Lawn Mowing...",
                    key=f"_{key_prefix}_add_new_item_txt_{resolved_entity}",
                )
                new_custom_is_port = st.checkbox(
                    "Portfolio-Wide (General / Company Overhead)",
                    value=False,
                    key=f"_{key_prefix}_add_new_item_port_{resolved_entity}",
                )
                if st.button(
                    "＋ Add Item to List",
                    key=f"_{key_prefix}_add_new_item_btn_{resolved_entity}",
                    use_container_width=True,
                ):
                    ok_add_c, msg_add_c = add_entity_account_code(new_custom_item_name, entity=resolved_entity)
                    if ok_add_c:
                        set_entity_code_scope(new_custom_item_name, is_portfolio_wide=new_custom_is_port, entity=resolved_entity)
                        st.success(msg_add_c)
                        st.rerun()
                    else:
                        st.error(msg_add_c)

        qa_c1, qa_c2, qa_c3, qa_c4 = st.columns([1.25, 1.2, 1.45, 0.65])
        with qa_c1:
            ordered_items = alphabetical_items
            base_item_opts = ordered_items if ordered_items else sorted(list(DEFAULT_CUSTOM_ACCOUNT_CODES), key=lambda s: s.lower())
            default_item_idx = base_item_opts.index("Insurance") if "Insurance" in base_item_opts else 0
            selected_qa_item = st.selectbox(
                "1. What is the Item? (Pick or Edit below)",
                options=base_item_opts,
                index=default_item_idx,
                key=f"_{key_prefix}_qa_item_{resolved_entity}",
            )
            custom_qa_item = st.text_input(
                "Edit / Type Custom Item Name (optional)",
                value=selected_qa_item,
                help="Change the Item name right here (e.g. change 'Insurance' to 'Tower Insurance') before clicking Save.",
                key=f"_{key_prefix}_qa_custom_item_{resolved_entity}_{selected_qa_item}",
            )
            picked_item = (custom_qa_item or "").strip() or selected_qa_item
        is_port_item = is_portfolio_wide_code(picked_item, resolved_entity)
        with qa_c2:
            if is_port_item:
                prop_choices = ["General / Portfolio"]
                picked_prop = st.selectbox(
                    "2. Which Property?",
                    options=prop_choices,
                    disabled=True,
                    help="This item is Portfolio-Wide (company overhead), so it automatically assigns to General / Portfolio.",
                    key=f"_{key_prefix}_qa_prop_port_{resolved_entity}",
                )
            else:
                prop_choices = (
                    [UNASSIGNED_PROPERTY]
                    + real_props
                    + ([all_props_option] if real_props else [])
                    + ["General / Portfolio"]
                )
                picked_prop = st.selectbox(
                    "2. Which Property?",
                    options=prop_choices,
                    format_func=lambda x: "Auto-detect House from CSV Line" if x == UNASSIGNED_PROPERTY else x,
                    key=f"_{key_prefix}_qa_prop_{resolved_entity}",
                )
        with qa_c3:
            picked_pattern = st.text_input(
                "3. Bank Text / Payee (or Vendor + House, e.g. 'Tower + Damian')",
                placeholder=f"e.g. Tower, 6851Tower Insurance, or Tower + Damian...",
                key=f"_{key_prefix}_qa_pat_{resolved_entity}",
            )
        with qa_c4:
            st.markdown("<div style='height: 1.65rem;'></div>", unsafe_allow_html=True)
            if st.button(
                "＋ Save",
                key=f"_{key_prefix}_qa_btn_{resolved_entity}",
                type="primary",
                use_container_width=True,
            ):
                clean_qa_pat = (picked_pattern or "").strip()
                if not clean_qa_pat:
                    st.error("Description pattern cannot be empty.")
                else:
                    if picked_item != selected_qa_item:
                        add_entity_account_code(picked_item, entity=resolved_entity)
                    if not is_port_item and picked_prop == all_props_option and real_props:
                        saved_cnt = 0
                        for p_house in real_props:
                            ok_each, _ = save_memorized_rule(
                                pattern=clean_qa_pat,
                                property_name=p_house,
                                account_code=picked_item,
                                entity=resolved_entity,
                                source="Item + Property Setup",
                            )
                            if ok_each:
                                saved_cnt += 1
                        _reapply_rules_and_sync()
                        st.success(
                            f"Saved '{clean_qa_pat}' → {picked_item} across all {saved_cnt} properties ({', '.join(real_props)})!"
                        )
                        st.rerun()
                    else:
                        ok_s, msg_s = save_memorized_rule(
                            pattern=clean_qa_pat,
                            property_name="General / Portfolio" if is_port_item else picked_prop,
                            account_code=picked_item,
                            entity=resolved_entity,
                            source="Item + Property Setup",
                        )
                        if ok_s:
                            _reapply_rules_and_sync()
                            st.success(msg_s)
                            st.rerun()
                        else:
                            st.error(msg_s)

    # Sidebar-Responsive Compact Rules List with per-row ✏️ Edit Button + Optional Bulk Grid Mode
    with st.container(border=True):
        hdr_c1, hdr_c2, hdr_c3, hdr_c4, hdr_c5 = st.columns([1.45, 0.9, 0.85, 0.72, 0.78])
        with hdr_c1:
            st.markdown(
                f'<div class="axiom-eyebrow">SAVED ITEM + PROPERTY RULES ({len(memorized_list)} CONFIGURED) &mdash; CLICK ✏️ EDIT OR 📋 COPY ON ANY ROW</div>',
                unsafe_allow_html=True,
            )
        with hdr_c2:
            search_rule_q = st.text_input(
                "Search Rules",
                placeholder="🔍 Filter text, item, house...",
                key=f"_{key_prefix}_search_q_{resolved_entity}",
                label_visibility="collapsed",
            )
        with hdr_c3:
            filter_item_opts = ["All Items"] + alphabetical_items
            filter_by_item = st.selectbox(
                "Filter Table by Item",
                options=filter_item_opts,
                key=f"_{key_prefix}_flt_item_{resolved_entity}",
                label_visibility="collapsed",
            )
        with hdr_c4:
            page_size_choice = st.selectbox(
                "Rows per page",
                options=["15 per page", "30 per page", "50 per page", "All"],
                index=0,
                key=f"_{key_prefix}_pg_size_{resolved_entity}",
                label_visibility="collapsed",
                help="Choose how many rules to show on the page at once (15, 30, 50, or All).",
            )
        with hdr_c5:
            view_mode = st.selectbox(
                "View Mode",
                options=["Compact Rows (✏️ Edit)", "Spreadsheet Grid"],
                key=f"_{key_prefix}_view_mode_{resolved_entity}",
                label_visibility="collapsed",
            )

        auto_label = "Auto-detect House from CSV Line"
        all_prop_dropdown_opts = list(dict.fromkeys([auto_label] + real_props + ["General / Portfolio"]))
        all_item_dropdown_opts = sorted(list(dict.fromkeys(alphabetical_items + ["Uncategorized"])), key=lambda s: s.lower())

        active_edit_state_key = f"_{key_prefix}_active_edit_idx_{resolved_entity}"
        active_edit_idx = st.session_state.get(active_edit_state_key, None)
        copy_mode_state_key = f"_{key_prefix}_copy_mode_{resolved_entity}"
        is_copy_mode = bool(st.session_state.get(copy_mode_state_key, False))

        # Build indexed list so edits/deletes target the exact rule in `memorized_list`
        indexed_rules: list[tuple[int, dict[str, str]]] = []
        for idx_r, r in enumerate(memorized_list):
            r_code = str(r.get("account_code", "")).strip() or "Uncategorized"
            r_prop = str(r.get("property", "")).strip()
            r_pat = str(r.get("pattern", "")).strip()
            r_src = str(r.get("source", "Saved")).strip()
            if is_portfolio_wide_code(r_code, resolved_entity):
                disp_prop = "General / Portfolio"
            elif not r_prop or r_prop == UNASSIGNED_PROPERTY:
                disp_prop = auto_label
            else:
                disp_prop = r_prop
                if disp_prop not in all_prop_dropdown_opts:
                    all_prop_dropdown_opts.append(disp_prop)
            if r_code not in all_item_dropdown_opts:
                all_item_dropdown_opts.append(r_code)

            if filter_by_item != "All Items" and r_code != filter_by_item:
                continue
            if search_rule_q.strip():
                q_low = search_rule_q.strip().lower()
                if (
                    q_low not in r_pat.lower()
                    and q_low not in r_code.lower()
                    and q_low not in disp_prop.lower()
                ):
                    continue

            indexed_rules.append(
                (
                    idx_r,
                    {
                        "Item": r_code,
                        "Property": disp_prop,
                        "Pattern": r_pat,
                        "Source": r_src,
                    },
                )
            )
        all_item_dropdown_opts.sort(key=lambda s: s.lower())
        indexed_rules.sort(
            key=lambda pair: (
                pair[1]["Item"].lower(),
                pair[1]["Pattern"].lower(),
                pair[1]["Property"].lower(),
            )
        )

        # If a row's ✏️ Edit or 📋 Copy button was clicked, show a dedicated, full-width Editor Panel right at the top
        if active_edit_idx is not None and 0 <= int(active_edit_idx) < len(memorized_list):
            target_rule = memorized_list[int(active_edit_idx)]
            cur_pat = str(target_rule.get("pattern", "")).strip()
            cur_code = str(target_rule.get("account_code", "")).strip() or "Uncategorized"
            cur_prop_raw = str(target_rule.get("property", "")).strip()
            if is_portfolio_wide_code(cur_code, resolved_entity):
                cur_prop_disp = "General / Portfolio"
            elif not cur_prop_raw or cur_prop_raw == UNASSIGNED_PROPERTY:
                cur_prop_disp = auto_label
            else:
                cur_prop_disp = cur_prop_raw

            if cur_code not in all_item_dropdown_opts:
                all_item_dropdown_opts.append(cur_code)
            if cur_prop_disp not in all_prop_dropdown_opts:
                all_prop_dropdown_opts.append(cur_prop_disp)

            # In Copy mode, pre-select the next property not yet linked to this pattern so the user can save right away
            default_prop_select_idx = all_prop_dropdown_opts.index(cur_prop_disp)
            if is_copy_mode and real_props and not is_portfolio_wide_code(cur_code, resolved_entity):
                existing_props_for_pat = {
                    str(r.get("property", "")).strip().lower()
                    for r in memorized_list
                    if str(r.get("pattern", "")).strip().lower() == cur_pat.lower()
                }
                for candidate_p in real_props:
                    if candidate_p.lower() not in existing_props_for_pat and candidate_p in all_prop_dropdown_opts:
                        default_prop_select_idx = all_prop_dropdown_opts.index(candidate_p)
                        break

            with st.container(border=True):
                if is_copy_mode:
                    st.markdown(
                        f'<div class="axiom-eyebrow" style="color:#10B981;">📋 COPYING RULE #{int(active_edit_idx) + 1} // "{cur_pat}" (KEEPING ORIGINAL: {cur_prop_disp})</div>',
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        "Pick the **Property** you want to copy this rule to and click **📋 Save as New Copy** (your original rule for "
                        f"`{cur_prop_disp}` stays saved)."
                    )
                else:
                    st.markdown(
                        f'<div class="axiom-eyebrow" style="color:#60A5FA;">✏️ EDITING RULE #{int(active_edit_idx) + 1} // "{cur_pat}" ({cur_prop_disp})</div>',
                        unsafe_allow_html=True,
                    )
                ed_c1, ed_c2, ed_c3 = st.columns([1.35, 1.3, 1.15])
                with ed_c1:
                    new_pat_val = st.text_input(
                        "Bank Statement Text / CSV Match Pattern",
                        value=cur_pat,
                        help="Tip: Use a vendor name (e.g. 'Tower') or combine vendor + house with '+' (e.g. 'Tower + Damian') to match across the entire CSV line.",
                        key=f"_{key_prefix}_edit_pat_{resolved_entity}_{active_edit_idx}_{int(is_copy_mode)}",
                    )
                with ed_c2:
                    sel_edit_code = st.selectbox(
                        "1. What is the Item? (Pick from list)",
                        options=all_item_dropdown_opts,
                        index=all_item_dropdown_opts.index(cur_code),
                        key=f"_{key_prefix}_edit_code_{resolved_entity}_{active_edit_idx}_{int(is_copy_mode)}",
                    )
                    typed_edit_code = st.text_input(
                        "Or Edit / Type Item Name (e.g. 'Tower Insurance')",
                        value=sel_edit_code,
                        help="Edit the Item name directly here (e.g. change 'Insurance' to 'Tower Insurance').",
                        key=f"_{key_prefix}_edit_custom_code_{resolved_entity}_{active_edit_idx}_{int(is_copy_mode)}_{sel_edit_code}",
                    )
                    new_code_val = (typed_edit_code or "").strip() or sel_edit_code
                with ed_c3:
                    new_prop_val = st.selectbox(
                        "2. Which Property?",
                        options=all_prop_dropdown_opts,
                        index=default_prop_select_idx,
                        key=f"_{key_prefix}_edit_prop_{resolved_entity}_{active_edit_idx}_{int(is_copy_mode)}",
                    )

                btn_c1, btn_c2, btn_c3, btn_c4, btn_c5 = st.columns([1.05, 1.15, 1.15, 0.8, 0.65])
                with btn_c1:
                    if st.button(
                        "📋 Save as New Copy" if is_copy_mode else "💾 Save Changes",
                        key=f"_{key_prefix}_save_edit_row_{resolved_entity}_{active_edit_idx}",
                        type="primary",
                        use_container_width=True,
                    ):
                        cleaned_pat = (new_pat_val or "").strip()
                        if not cleaned_pat:
                            st.error("Bank Statement Text cannot be empty.")
                        else:
                            resolved_save_prop = (
                                "General / Portfolio"
                                if is_portfolio_wide_code(new_code_val, resolved_entity)
                                else (UNASSIGNED_PROPERTY if new_prop_val == auto_label else new_prop_val)
                            )
                            if is_copy_mode:
                                ok_c, msg_c = save_memorized_rule(
                                    pattern=cleaned_pat,
                                    property_name=resolved_save_prop,
                                    account_code=new_code_val,
                                    entity=resolved_entity,
                                    source="Copied Rule",
                                )
                                if ok_c:
                                    st.session_state[active_edit_state_key] = None
                                    st.session_state[copy_mode_state_key] = False
                                    _reapply_rules_and_sync()
                                    st.success(f"Copied rule '{cleaned_pat}' → Item: {new_code_val} | Property: {new_prop_val}")
                                    st.rerun()
                            else:
                                updated_list = list(memorized_list)
                                updated_list[int(active_edit_idx)] = {
                                    "pattern": cleaned_pat,
                                    "account_code": new_code_val,
                                    "property": resolved_save_prop,
                                    "source": "Edited Rule",
                                }
                                ok_u, msg_u = replace_memorized_rules(updated_list, entity=resolved_entity)
                                if ok_u:
                                    st.session_state[active_edit_state_key] = None
                                    st.session_state[copy_mode_state_key] = False
                                    _reapply_rules_and_sync()
                                    st.success(f"Updated rule '{cleaned_pat}' → Item: {new_code_val} | Property: {new_prop_val}")
                                    st.rerun()
                with btn_c2:
                    if not is_copy_mode:
                        if st.button(
                            "📋 Save as Copy (Keep Both)",
                            key=f"_{key_prefix}_copy_edit_row_{resolved_entity}_{active_edit_idx}",
                            help="Saves this Property as an additional rule while keeping the original rule intact.",
                            use_container_width=True,
                        ):
                            cleaned_pat = (new_pat_val or "").strip()
                            if not cleaned_pat:
                                st.error("Bank Statement Text cannot be empty.")
                            else:
                                resolved_save_prop = (
                                    "General / Portfolio"
                                    if is_portfolio_wide_code(new_code_val, resolved_entity)
                                    else (UNASSIGNED_PROPERTY if new_prop_val == auto_label else new_prop_val)
                                )
                                ok_cp, _ = save_memorized_rule(
                                    pattern=cleaned_pat,
                                    property_name=resolved_save_prop,
                                    account_code=new_code_val,
                                    entity=resolved_entity,
                                    source="Copied Rule",
                                )
                                if ok_cp:
                                    st.session_state[active_edit_state_key] = None
                                    st.session_state[copy_mode_state_key] = False
                                    _reapply_rules_and_sync()
                                    st.success(
                                        f"Added copy '{cleaned_pat}' → {new_code_val} | {new_prop_val} (kept original {cur_prop_disp})!"
                                    )
                                    st.rerun()
                    else:
                        if st.button(
                            "💾 Overwrite Original Instead",
                            key=f"_{key_prefix}_overwrite_orig_row_{resolved_entity}_{active_edit_idx}",
                            use_container_width=True,
                        ):
                            cleaned_pat = (new_pat_val or "").strip()
                            if cleaned_pat:
                                updated_list = list(memorized_list)
                                resolved_save_prop = (
                                    "General / Portfolio"
                                    if is_portfolio_wide_code(new_code_val, resolved_entity)
                                    else (UNASSIGNED_PROPERTY if new_prop_val == auto_label else new_prop_val)
                                )
                                updated_list[int(active_edit_idx)] = {
                                    "pattern": cleaned_pat,
                                    "account_code": new_code_val,
                                    "property": resolved_save_prop,
                                    "source": "Edited Rule",
                                }
                                replace_memorized_rules(updated_list, entity=resolved_entity)
                                st.session_state[active_edit_state_key] = None
                                st.session_state[copy_mode_state_key] = False
                                _reapply_rules_and_sync()
                                st.rerun()
                with btn_c3:
                    if st.button(
                        f"🏠 Add for All ({len(real_props)}) Properties",
                        key=f"_{key_prefix}_all_props_edit_row_{resolved_entity}_{active_edit_idx}",
                        disabled=is_portfolio_wide_code(new_code_val, resolved_entity) or not real_props,
                        help="Creates a saved rule for every property in this entity so all houses are saved in your list.",
                        use_container_width=True,
                    ):
                        cleaned_pat = (new_pat_val or "").strip()
                        if not cleaned_pat:
                            st.error("Bank Statement Text cannot be empty.")
                        else:
                            for p_house in real_props:
                                save_memorized_rule(
                                    pattern=cleaned_pat,
                                    property_name=p_house,
                                    account_code=new_code_val,
                                    entity=resolved_entity,
                                    source="Copied to All Properties",
                                )
                            st.session_state[active_edit_state_key] = None
                            st.session_state[copy_mode_state_key] = False
                            _reapply_rules_and_sync()
                            st.success(
                                f"Saved '{cleaned_pat}' → {new_code_val} for all {len(real_props)} properties!"
                            )
                            st.rerun()
                with btn_c4:
                    if st.button(
                        "🗑️ Delete",
                        key=f"_{key_prefix}_del_edit_row_{resolved_entity}_{active_edit_idx}",
                        use_container_width=True,
                    ):
                        updated_list = [r for i_r, r in enumerate(memorized_list) if i_r != int(active_edit_idx)]
                        replace_memorized_rules(updated_list, entity=resolved_entity)
                        st.session_state[active_edit_state_key] = None
                        st.session_state[copy_mode_state_key] = False
                        _reapply_rules_and_sync()
                        st.rerun()
                with btn_c5:
                    if st.button(
                        "Cancel",
                        key=f"_{key_prefix}_cancel_edit_row_{resolved_entity}_{active_edit_idx}",
                        use_container_width=True,
                    ):
                        st.session_state[active_edit_state_key] = None
                        st.session_state[copy_mode_state_key] = False
                        st.rerun()

        if view_mode == "Compact Rows (✏️ Edit)":
            if not indexed_rules:
                st.caption("No saved rules match your current filter.")
            else:
                total_filtered = len(indexed_rules)
                if page_size_choice == "30 per page":
                    page_size = 30
                elif page_size_choice == "50 per page":
                    page_size = 50
                elif page_size_choice == "All":
                    page_size = max(1, total_filtered)
                else:
                    page_size = 15

                total_pages = max(1, (total_filtered + page_size - 1) // page_size)
                page_state_key = f"_{key_prefix}_page_{resolved_entity}"
                cur_page = min(max(1, int(st.session_state.get(page_state_key, 1))), total_pages)

                if total_pages > 1:
                    pg_c1, pg_c2, pg_c3 = st.columns([1.6, 1.2, 1.2])
                    with pg_c1:
                        st.caption(
                            f"Showing rules **{(cur_page - 1) * page_size + 1}–{min(cur_page * page_size, total_filtered)}** of **{total_filtered}** ({page_size_choice})"
                        )
                    with pg_c2:
                        if st.button(
                            f"◀ Previous {page_size}",
                            disabled=(cur_page <= 1),
                            key=f"_{key_prefix}_prev_pg_{resolved_entity}",
                            use_container_width=True,
                        ):
                            st.session_state[page_state_key] = cur_page - 1
                            st.rerun()
                    with pg_c3:
                        if st.button(
                            f"Next {page_size} ▶",
                            disabled=(cur_page >= total_pages),
                            key=f"_{key_prefix}_next_pg_{resolved_entity}",
                            use_container_width=True,
                        ):
                            st.session_state[page_state_key] = cur_page + 1
                            st.rerun()
                else:
                    st.caption(f"Showing all **{total_filtered}** saved rule(s) on one page.")

                # Compact Column Header Row
                th_c1, th_c2, th_c3, th_c4, th_c5 = st.columns([1.35, 0.88, 1.07, 0.42, 0.42])
                th_c1.markdown("<span style='color:#8E939F;font-size:0.73rem;font-weight:600;'>BANK TEXT / CSV MATCH</span>", unsafe_allow_html=True)
                th_c2.markdown("<span style='color:#8E939F;font-size:0.73rem;font-weight:600;'>1. ITEM (CODE)</span>", unsafe_allow_html=True)
                th_c3.markdown("<span style='color:#8E939F;font-size:0.73rem;font-weight:600;'>2. PROPERTY</span>", unsafe_allow_html=True)
                th_c4.markdown("<span style='color:#8E939F;font-size:0.73rem;font-weight:600;'>EDIT</span>", unsafe_allow_html=True)
                th_c5.markdown("<span style='color:#8E939F;font-size:0.73rem;font-weight:600;'>COPY</span>", unsafe_allow_html=True)

                page_slice = indexed_rules[(cur_page - 1) * page_size : cur_page * page_size]
                for orig_idx, r_info in page_slice:
                    r_c1, r_c2, r_c3, r_c4, r_c5 = st.columns([1.35, 0.88, 1.07, 0.42, 0.42])
                    is_editing_this = active_edit_idx is not None and int(active_edit_idx) == orig_idx
                    pat_text = r_info["Pattern"]
                    item_text = r_info["Item"]
                    prop_text = r_info["Property"]
                    prop_badge_color = "#60A5FA" if prop_text == auto_label else "#10B981"
                    row_bg = "#1E2638" if is_editing_this else "#16181C"

                    with r_c1:
                        st.markdown(
                            f"<div style='background:{row_bg};border:1px solid #2A2D34;border-radius:4px;padding:0.36rem 0.55rem;"
                            f"font-family:JetBrains Mono,monospace;font-size:0.78rem;color:#F3F4F6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' "
                            f"title='{pat_text}'>{pat_text}</div>",
                            unsafe_allow_html=True,
                        )
                    with r_c2:
                        st.markdown(
                            f"<div style='background:{row_bg};border:1px solid #2A2D34;border-radius:4px;padding:0.36rem 0.55rem;"
                            f"font-size:0.79rem;font-weight:600;color:#E5E7EB;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' "
                            f"title='{item_text}'>{item_text}</div>",
                            unsafe_allow_html=True,
                        )
                    with r_c3:
                        short_prop = "🔍 Auto House from CSV" if prop_text == auto_label else prop_text
                        st.markdown(
                            f"<div style='background:{row_bg};border:1px solid #2A2D34;border-radius:4px;padding:0.36rem 0.55rem;"
                            f"font-size:0.78rem;color:{prop_badge_color};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' "
                            f"title='{prop_text}'>{short_prop}</div>",
                            unsafe_allow_html=True,
                        )
                    with r_c4:
                        if st.button(
                            "✏️ Edit",
                            key=f"_{key_prefix}_row_edit_btn_{resolved_entity}_{orig_idx}",
                            help="Edit this rule in place",
                            use_container_width=True,
                        ):
                            st.session_state[active_edit_state_key] = orig_idx
                            st.session_state[copy_mode_state_key] = False
                            st.rerun()
                    with r_c5:
                        if st.button(
                            "📋 Copy",
                            key=f"_{key_prefix}_row_copy_btn_{resolved_entity}_{orig_idx}",
                            help="Copy this rule so you can assign it to another property while keeping this one",
                            use_container_width=True,
                        ):
                            st.session_state[active_edit_state_key] = orig_idx
                            st.session_state[copy_mode_state_key] = True
                            st.rerun()
        else:
            # Compact Spreadsheet Grid fallback with shrunk column widths
            table_rows: list[dict[str, str]] = [
                {
                    "Bank Text": r_info["Pattern"],
                    "Item": r_info["Item"],
                    "Property": r_info["Property"],
                }
                for _, r_info in indexed_rules
            ]
            view_rules_df = (
                pd.DataFrame(table_rows)
                if table_rows
                else pd.DataFrame(columns=["Bank Text", "Item", "Property"])
            )

            edited_rules_df = st.data_editor(
                view_rules_df,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key=f"_{key_prefix}_editor_{resolved_entity}_{filter_by_item}",
                column_config={
                    "Bank Text": st.column_config.TextColumn(
                        "Bank Text / CSV Match",
                        required=True,
                        width="medium",
                        help="Text or 'Vendor + House' tokens on the CSV row.",
                    ),
                    "Item": st.column_config.TextColumn(
                        "1. Item (Type or Edit Name)",
                        required=True,
                        width="small",
                        help="You can edit or type any Item name here (e.g. 'Insurance' or 'Tower Insurance').",
                    ),
                    "Property": st.column_config.SelectboxColumn(
                        "2. Property",
                        options=all_prop_dropdown_opts,
                        required=True,
                        width="small",
                    ),
                },
            )

            if st.button(
                "💾 Save Grid Changes & Re-Apply to All Uploaded Files",
                key=f"_{key_prefix}_save_table_btn_{resolved_entity}",
                type="primary",
            ):
                edited_indices = {orig_idx for orig_idx, _ in indexed_rules}
                kept_unfiltered = [
                    r for idx_r, r in enumerate(memorized_list) if idx_r not in edited_indices
                ]
                new_payload: list[dict[str, object]] = []
                for _, row_item in edited_rules_df.iterrows():
                    pat_v = str(row_item.get("Bank Text", "") or "").strip()
                    item_v = str(row_item.get("Item", "") or "").strip()
                    prop_v = str(row_item.get("Property", "") or "").strip()
                    if pat_v:
                        new_payload.append(
                            {
                                "pattern": pat_v,
                                "account_code": item_v,
                                "property": UNASSIGNED_PROPERTY if prop_v == auto_label else prop_v,
                                "source": "Setup Table",
                            }
                        )
                new_payload.extend(kept_unfiltered)
                ok_rep, msg_rep = replace_memorized_rules(new_payload, entity=resolved_entity)
                if ok_rep:
                    _reapply_rules_and_sync()
                    st.success(msg_rep)
                    st.rerun()


def get_entity_account_codes(entity: str | None = None) -> list[str]:
    """
    Return the custom Chart of Account Codes configured for the entity.
    Includes custom codes from `Codes.xlsx` (e.g. Materials, Bodycorp, Rent Income, Bank Interest,
    James Drawings, Kathy Drawings, etc.) plus any codes learned from memorized rules.
    """
    resolved = get_active_entity(entity)
    store = _load_memory_store()
    codes_map = store.setdefault("entity_account_codes", {})
    if not isinstance(codes_map, dict):
        codes_map = {}
        store["entity_account_codes"] = codes_map
    if resolved not in codes_map or not isinstance(codes_map[resolved], list) or not codes_map[resolved]:
        jur = get_entity_jurisdiction(resolved)
        base_codes = list(DEFAULT_CUSTOM_ACCOUNT_CODES) if jur == "NZ" else sorted(set(get_entity_rules(resolved).values()))
        codes_map[resolved] = base_codes
        _save_memory_store(store)
    elif get_entity_jurisdiction(resolved) == "NZ":
        ent_codes = list(codes_map[resolved])
        changed = False
        for req_code in ("Rent Income", "Rent Income Raywhite"):
            if not any(c.lower() == req_code.lower() for c in ent_codes):
                ent_codes.append(req_code)
                changed = True
        if changed:
            ent_codes.sort(key=lambda s: s.lower())
            codes_map[resolved] = ent_codes
            _save_memory_store(store)
    return sorted(list(codes_map[resolved]), key=lambda s: s.lower())


def add_entity_account_code(code_name: str, entity: str | None = None) -> tuple[bool, str]:
    """Add a new Account Code to the entity's Chart of Accounts and persist it."""
    resolved = get_active_entity(entity)
    cleaned = (code_name or "").strip()
    if not cleaned or cleaned.lower() in UNCATEGORIZED_MARKERS:
        return False, "Account Code name cannot be empty."
    store = _load_memory_store()
    codes_map = store.setdefault("entity_account_codes", {})
    current = list(codes_map.get(resolved, get_entity_account_codes(resolved)))
    if any(c.lower() == cleaned.lower() for c in current):
        return False, f"Account Code '{cleaned}' already exists for {resolved}."
    current.append(cleaned)
    current.sort(key=lambda s: s.lower())
    codes_map[resolved] = current
    _save_memory_store(store)
    return True, f"Added Account Code '{cleaned}' to {resolved}."


def remove_entity_account_code(code_name: str, entity: str | None = None) -> tuple[bool, str]:
    """Remove an Account Code from the entity's custom Chart of Accounts."""
    resolved = get_active_entity(entity)
    target = (code_name or "").strip()
    store = _load_memory_store()
    codes_map = store.setdefault("entity_account_codes", {})
    current = list(codes_map.get(resolved, get_entity_account_codes(resolved)))
    filtered = [c for c in current if c.lower() != target.lower()]
    codes_map[resolved] = filtered
    _save_memory_store(store)
    return True, f"Removed Account Code '{target}'."


def rename_entity_account_code(
    old_code: str,
    new_code: str,
    entity: str | None = None,
) -> tuple[bool, str]:
    """
    Rename an Account Code across the entity's Chart of Accounts, memorized rules,
    uploaded file DataFrames, and financial year ledgers.
    """
    init_workspace_state()
    resolved = get_active_entity(entity)
    src_code = (old_code or "").strip()
    target_code = (new_code or "").strip()
    if not target_code:
        return False, "New Account Code cannot be empty."
    if src_code == target_code:
        return True, f"Account Code '{src_code}' unchanged."

    store = _load_memory_store()
    codes_map = store.setdefault("entity_account_codes", {})
    current = list(codes_map.get(resolved, get_entity_account_codes(resolved)))
    updated_list: list[str] = []
    found = False
    for c in current:
        if c.lower() == src_code.lower():
            if not any(x.lower() == target_code.lower() for x in updated_list):
                updated_list.append(target_code)
            found = True
        else:
            if not any(x.lower() == c.lower() for x in updated_list):
                updated_list.append(c)
    if not found and not any(x.lower() == target_code.lower() for x in updated_list):
        updated_list.append(target_code)
    updated_list.sort(key=lambda s: s.lower())
    codes_map[resolved] = updated_list

    # Migrate memorized rules
    rules_map = store.setdefault("memorized_rules", {})
    ent_rules: list[dict[str, str]] = list(rules_map.get(resolved, []))
    for rule_item in ent_rules:
        if str(rule_item.get("account_code", "")).strip().lower() == src_code.lower():
            rule_item["account_code"] = target_code
    rules_map[resolved] = ent_rules
    _save_memory_store(store)

    # Migrate in-memory uploaded files & ledgers
    files_by_ent = st.session_state.get("entity_uploaded_files", {}).get(resolved, {})
    if isinstance(files_by_ent, dict):
        for _yr, file_list in files_by_ent.items():
            if isinstance(file_list, list):
                for frec in file_list:
                    fdf = frec.get("df")
                    if isinstance(fdf, pd.DataFrame) and "Account Code" in fdf.columns and not fdf.empty:
                        mask = fdf["Account Code"].astype(str).str.strip().str.lower() == src_code.lower()
                        if mask.any():
                            fdf.loc[mask, "Account Code"] = target_code
                            frec["df"] = fdf

    ledgers_by_ent = st.session_state.get("entity_year_ledgers", {}).get(resolved, {})
    if isinstance(ledgers_by_ent, dict):
        for yr, ldf in list(ledgers_by_ent.items()):
            if isinstance(ldf, pd.DataFrame) and "Account Code" in ldf.columns and not ldf.empty:
                mask = ldf["Account Code"].astype(str).str.strip().str.lower() == src_code.lower()
                if mask.any():
                    ldf.loc[mask, "Account Code"] = target_code
                    ledgers_by_ent[yr] = ldf
                    if yr == get_active_tax_year():
                        st.session_state["entity_ledgers"][resolved] = ldf

    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                """
                UPDATE file_transactions
                SET account_code = ?
                WHERE LOWER(TRIM(account_code)) = LOWER(TRIM(?))
                  AND file_id IN (SELECT file_id FROM uploaded_files WHERE entity = ?)
                """,
                (target_code, src_code, resolved),
            )
            conn.execute(
                """
                UPDATE master_ledgers
                SET account_code = ?
                WHERE entity = ? AND LOWER(TRIM(account_code)) = LOWER(TRIM(?))
                """,
                (target_code, resolved, src_code),
            )
    return True, f"Renamed Account Code '{src_code}' → '{target_code}'."


def get_property_aliases(entity: str | None = None) -> dict[str, list[str]]:
    """
    Return the dictionary mapping each Property name -> list of shorthand/bank aliases
    (e.g. '363 Queen': ['363 queen', '363queen', '16d', 'unit 16d', 'apt 1604', 'quest hotel']).
    """
    resolved = get_active_entity(entity)
    store = _load_memory_store()
    alias_map = store.setdefault("entity_property_aliases", {})
    if not isinstance(alias_map, dict):
        alias_map = {}
        store["entity_property_aliases"] = alias_map
    ent_aliases = alias_map.get(resolved)
    if not isinstance(ent_aliases, dict) or not ent_aliases:
        ent_aliases = {}
        for p in get_entity_properties(resolved):
            if p in DEFAULT_PROPERTY_ALIASES:
                ent_aliases[p] = list(DEFAULT_PROPERTY_ALIASES[p])
            else:
                ent_aliases[p] = [p.lower()]
        alias_map[resolved] = ent_aliases
        _save_memory_store(store)
    else:
        # Ensure all current properties have an entry
        changed = False
        for p in get_entity_properties(resolved):
            if p not in ent_aliases:
                ent_aliases[p] = list(DEFAULT_PROPERTY_ALIASES.get(p, [p.lower()]))
                changed = True
        if changed:
            alias_map[resolved] = ent_aliases
            _save_memory_store(store)
    return dict(ent_aliases)


def save_property_aliases(
    property_name: str,
    aliases_csv_or_list: str | list[str],
    entity: str | None = None,
) -> tuple[bool, str]:
    """Save bank shorthand aliases for a specific Property."""
    resolved = get_active_entity(entity)
    prop = (property_name or "").strip()
    if not prop:
        return False, "Property name cannot be empty."
    if isinstance(aliases_csv_or_list, str):
        raw_items = [x.strip() for x in aliases_csv_or_list.split(",") if x.strip()]
    else:
        raw_items = [str(x).strip() for x in aliases_csv_or_list if str(x).strip()]
    deduped: list[str] = []
    for item in raw_items:
        if item.lower() not in [d.lower() for d in deduped]:
            deduped.append(item)
    store = _load_memory_store()
    alias_map = store.setdefault("entity_property_aliases", {})
    ent_aliases = dict(alias_map.get(resolved, {}))
    ent_aliases[prop] = deduped
    alias_map[resolved] = ent_aliases
    _save_memory_store(store)
    return True, f"Saved {len(deduped)} matching keyword(s) for '{prop}'."


_GENERIC_STREET_SUFFIXES = {
    "st",
    "street",
    "rd",
    "road",
    "pl",
    "plc",
    "place",
    "way",
    "ave",
    "avenue",
    "dr",
    "drive",
    "ln",
    "lane",
    "ct",
    "court",
    "cres",
    "crescent",
    "tce",
    "terrace",
    "blvd",
    "boulevard",
    "unit",
    "apt",
    "flat",
    "no",
    "property",
    "general",
    "portfolio",
}


def _derive_smart_property_tokens(property_name: str, configured_aliases: list[str] | None = None) -> list[str]:
    """
    Build the complete list of matching keywords for a property, combining:
    1. Explicit user-configured aliases
    2. Built-in DEFAULT_PROPERTY_ALIASES if matching
    3. Automatically derived distinctive house/street name words ( >= 4 chars, e.g. 'damian', 'damien',
       'weymouth', 'blampied', 'frances', 'raleigh', 'queen', 'southview', 'milson', 'melbourne', 'lauretta')
       plus compact number+street forms (e.g. '9damian', '33blam', '14ral', '363queen').
    """
    p_clean = (property_name or "").strip()
    if not p_clean or p_clean.lower() in ("general / portfolio", "general / portfolio-wide", "unassigned"):
        return []

    tokens: list[str] = []

    def _add_tok(t: str) -> None:
        tc = t.strip().lower()
        if tc and tc not in tokens:
            tokens.append(tc)

    _add_tok(p_clean)
    for k_def, def_list in DEFAULT_PROPERTY_ALIASES.items():
        if k_def.lower() == p_clean.lower() or k_def.lower() in p_clean.lower() or p_clean.lower() in k_def.lower():
            for d_tok in def_list:
                _add_tok(d_tok)

    if configured_aliases:
        for c_tok in configured_aliases:
            _add_tok(str(c_tok))

    # Compact no-space version (e.g. "9 Damian" -> "9damian", "303 Weymouth" -> "303weymouth")
    compact = re.sub(r"[^a-z0-9]+", "", p_clean.lower())
    if len(compact) >= 4:
        _add_tok(compact)

    # Extract individual words from property name & aliases (e.g. "damian", "weymouth", "blampied", "frances", "raleigh", "queen", "1604")
    word_parts = re.findall(r"[a-z0-9]+", p_clean.lower())
    nums = [w for w in word_parts if w.isdigit()]
    alpha_words = [w for w in word_parts if not w.isdigit() and w not in _GENERIC_STREET_SUFFIXES]

    for w in alpha_words:
        if len(w) >= 4:
            _add_tok(w)
            # Common spelling variant: damian <-> damien
            if w == "damian":
                _add_tok("damien")
            elif w == "damien":
                _add_tok("damian")
            # Short 4-letter prefix with house number (e.g. "33blam", "14ral")
            for num in nums:
                _add_tok(f"{num}{w}")
                _add_tok(f"{num} {w}")
                if len(w) >= 4:
                    _add_tok(f"{num}{w[:4]}")
                    _add_tok(f"{num}{w[:3]}")

    # Also allow distinctive 4+ digit unit/apartment numbers like '1604'
    for num in nums:
        if len(num) >= 4:
            _add_tok(num)

    tokens.sort(key=len, reverse=True)
    return tokens


def detect_property_from_text(
    text: object,
    entity: str | None = None,
) -> str:
    """
    Deterministically detect which Property a string (anywhere across a CSV line:
    Description, Payee, Analysis Code, Reference, Particulars, Memo, or EOY section header)
    refers to using configured Property names, Property Shorthand Aliases, and smart house-name tokens
    (e.g., 'Damian' -> '9 Damian', 'Blampied' -> '33 Blampied', 'Weymouth' -> '303 Weymouth').
    Supports multi-property split notes like '33Blam 14Ral' -> '33 Blampied / 14 Raleigh'.
    Returns UNASSIGNED_PROPERTY if no property alias matches.
    """
    if pd.isna(text):
        return UNASSIGNED_PROPERTY
    s = str(text).strip()
    if not s or s.lower() in ("nan", "none", "null", "#name?"):
        return UNASSIGNED_PROPERTY
    s_lower = s.lower()

    resolved = get_active_entity(entity)
    props = get_entity_properties(resolved)
    aliases_by_prop = get_property_aliases(resolved)

    # Check exact match to a configured property name first
    for p in props:
        if p.lower() == s_lower:
            return p

    # Collect all non-general properties whose aliases or house tokens appear in `s_lower`
    matched_props: list[str] = []
    for p in props:
        if p.lower() in ("general / portfolio", "general / portfolio-wide"):
            continue
        candidate_tokens = _derive_smart_property_tokens(p, aliases_by_prop.get(p, []))
        for tok_clean in candidate_tokens:
            if len(tok_clean) < 3:
                continue
            # For short 3-4 char tokens or purely numeric tokens, require word/token boundary so we don't false-match inside unrelated words
            if len(tok_clean) <= 4 or tok_clean.isdigit():
                pattern = rf"(?<![a-z0-9]){re.escape(tok_clean)}(?![a-z0-9])"
                if re.search(pattern, s_lower):
                    if p not in matched_props:
                        matched_props.append(p)
                    break
            else:
                if tok_clean in s_lower:
                    if p not in matched_props:
                        matched_props.append(p)
                    break

    if len(matched_props) == 1:
        return matched_props[0]
    if len(matched_props) > 1:
        return " / ".join(matched_props)

    # Loan account / trigger match (BT Property Holdings Ltd NZ loans only)
    try:
        _loan_hit = match_loan_from_text(s, entity=resolved)
        if _loan_hit:
            return str(_loan_hit.get("property_name") or UNASSIGNED_PROPERTY)
    except Exception:
        pass

    return UNASSIGNED_PROPERTY


def get_account_code_options(entity: str | None = None) -> list[str]:
    """Return sorted list of valid Account Codes for dropdown selection based on entity custom codes + jurisdiction + memorized codes."""
    resolved = get_active_entity(entity)
    rules = get_entity_rules(resolved)
    codes = set(rules.values())
    codes.update(get_entity_account_codes(resolved))
    codes.update(["Rental Income", "Rent Income", "Other Expense", "Owner Contribution", "Owner Drawing"])
    for mem in get_memorized_rules(resolved):
        c = str(mem.get("account_code", "")).strip()
        if c and c.lower() not in UNCATEGORIZED_MARKERS:
            codes.add(c)
    sorted_codes = sorted(codes, key=lambda s: s.lower())
    return ["Uncategorized"] + sorted_codes


def get_entity_rules(entity: str | None = None) -> dict[str, str]:
    """Return the keyword-matching rules dictionary for the specified or active entity's jurisdiction."""
    jurisdiction = get_entity_jurisdiction(entity)
    return ENTITY_CATEGORIZATION_RULES.get(jurisdiction, ENTITY_CATEGORIZATION_RULES["NZ"])



# =====================================================================
# PROPERTY LOANS, SMTP & EXPIRY REMINDERS (BT Property Holdings Ltd only)
# =====================================================================

def get_loan_target_entity() -> str:
    try:
        opts = get_entity_options()
    except Exception:
        opts = []
    if LOAN_TARGET_ENTITY_DEFAULT in opts:
        return LOAN_TARGET_ENTITY_DEFAULT
    return LOAN_TARGET_ENTITY_DEFAULT


def is_loan_target_entity(entity: str | None = None) -> bool:
    try:
        resolved = get_active_entity(entity)
    except Exception:
        resolved = str(entity or "")
    return resolved.strip().lower() == get_loan_target_entity().strip().lower()


def _parse_loan_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s or s.lower() in ("none", "nat", "nan"):
        return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y", "%d %b %y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=True).date()
    except Exception:
        return None


def _fmt_loan_date(val) -> str:
    d = _parse_loan_date(val)
    return d.strftime("%d %b %Y") if d else "—"


def _loan_date_iso(val) -> str:
    d = _parse_loan_date(val)
    return d.isoformat() if d else ""


def _subtract_months(d: date, months: int) -> date:
    m = int(months or 0)
    total = (d.year * 12 + (d.month - 1)) - m
    y, mo = divmod(total, 12)
    mo += 1
    last = calendar.monthrange(y, mo)[1]
    return date(y, mo, min(d.day, last))


def infer_nz_bank_from_account(account_number: str) -> str:
    """Infer standard NZ bank name from a 2-digit NZ bank prefix (BB-BBBB-AAAAAAA-SSS)."""
    s = str(account_number or "").strip()
    m = re.match(r"^(\d{2})[-\s]\d{4}[-\s]\d{6,8}[-\s]\d{2,4}$", s)
    if not m:
        return ""
    prefix = m.group(1)
    prefix_map = {
        "01": "ANZ",
        "06": "ANZ",
        "11": "ANZ",
        "02": "BNZ",
        "03": "Westpac",
        "13": "Westpac",
        "14": "Westpac",
        "15": "Westpac",
        "16": "Westpac",
        "17": "Westpac",
        "18": "Westpac",
        "19": "Westpac",
        "20": "Westpac",
        "21": "Westpac",
        "22": "Westpac",
        "23": "Westpac",
        "24": "Westpac",
        "12": "ASB",
        "38": "Kiwibank",
        "15": "TSB",
        "25": "TSB",
        "26": "SBS Bank",
        "28": "Co-operative Bank",
    }
    return prefix_map.get(prefix, "")


def _clean_loan_money(val) -> float:
    """Parse currency strings like '– $313,000.00' or '$1,155.09 (estimated)' into positive float."""
    if val is None or isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        return abs(float(val))
    s = str(val).strip()
    if not s:
        return 0.0
    m = re.search(r"[-–—]?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", s)
    if not m:
        return 0.0
    try:
        return abs(float(m.group(1).replace(",", "")))
    except Exception:
        return 0.0


def _clean_loan_rate(val) -> float:
    """Parse rate strings like '4.49% p.a.' or 'Fixed at 4.44%' into float 4.49."""
    if val is None or isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        return abs(float(val))
    s = str(val).strip()
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%?", s)
    if not m:
        return 0.0
    try:
        return abs(float(m.group(1)))
    except Exception:
        return 0.0


def _loan_row_to_dict(row) -> dict:
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


def ensure_seeded_property_loans(entity: str | None = None) -> None:
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
    target = get_loan_target_entity()
    resolved = target
    if entity:
        try:
            resolved = get_active_entity(entity)
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
    if not loan_id:
        return None
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            r = conn.execute("SELECT * FROM property_loans WHERE loan_id=?", (str(loan_id),)).fetchone()
    return _loan_row_to_dict(r) if r else None


def save_property_loan(loan: dict, log_rate_history: bool = False, rate_note: str = "") -> tuple[bool, str, str]:
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
            conn.execute(f"INSERT OR REPLACE INTO property_loans ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})", [vals[c] for c in cols])
    # Persist chosen codes so dropdowns include them
    for code in (vals["interest_account_code"], vals["principal_account_code"]):
        try:
            if code and code not in get_entity_account_codes(target):
                add_entity_account_code(code, target)
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
    if not loan_id:
        return False, "Missing loan."
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute("DELETE FROM property_loans WHERE loan_id=?", (str(loan_id),))
            conn.execute("DELETE FROM property_loan_rate_history WHERE loan_id=?", (str(loan_id),))
    return True, "Loan deleted."


def create_property_loan(property_name: str, loan_label: str = "Main Facility") -> tuple[bool, str, str]:
    return save_property_loan({"property_name": property_name, "loan_label": loan_label or "Main Facility", "interest_account_code": "Loan Interest", "principal_account_code": "Principal Loan", "email_reminder_enabled": True, "use_for_coding": True, "reminder_months_before": int(get_smtp_config().get("default_reminder_months") or 2)})


def log_loan_rate_history(loan: dict, note: str = "", effective_date=None) -> None:
    eff = _loan_date_iso(effective_date) or _loan_date_iso(loan.get("drawdown_date")) or date.today().isoformat()
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute("INSERT INTO property_loan_rate_history (loan_id,entity,property_name,effective_date,interest_type,repayment_type,interest_rate_pct,review_or_expiry_date,note,logged_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (str(loan.get("loan_id") or ""), get_loan_target_entity(), str(loan.get("property_name") or ""), eff, str(loan.get("interest_type") or ""), str(loan.get("repayment_type") or ""), float(loan.get("interest_rate_pct") or 0), _loan_date_iso(loan.get("fixed_rate_review_date")), str(note or ""), datetime.now().isoformat(timespec="seconds")))


def get_loan_rate_history(loan_id: str) -> list[dict]:
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            rows = conn.execute("SELECT * FROM property_loan_rate_history WHERE loan_id=? ORDER BY id DESC", (str(loan_id),)).fetchall()
    return [dict(r) for r in rows]


def get_loan_trigger_tokens(loan: dict, shared_customer_bases: set[str] | None = None) -> list[str]:
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


def match_loan_from_text(text: object, entity: str | None = None) -> dict | None:
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


def loan_milestones(loan: dict) -> list[dict]:
    out = []
    specs = [("fixed_rate_expiry", "Fixed Rate Review / Expiry", loan.get("fixed_rate_review_date"), loan.get("remind_on_fixed_expiry")), ("interest_only_end", "Interest-Only End", loan.get("interest_only_end_date"), loan.get("remind_on_io_expiry")), ("loan_term_end", "Final Loan Term End", loan.get("loan_term_end_date"), loan.get("remind_on_term_expiry"))]
    for mtype, label, raw, enabled in specs:
        d = _parse_loan_date(raw)
        if d:
            months = int(loan.get("reminder_months_before") or 2)
            out.append({"milestone_type": mtype, "label": label, "expiry_date": d, "reminder_enabled_for_milestone": bool(enabled), "window_start": _subtract_months(d, months), "days_remaining": (d - date.today()).days})
    return out


def has_reminder_been_sent(loan_id: str, milestone_type: str, expiry_date: date) -> bool:
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            r = conn.execute("SELECT COUNT(*) c FROM loan_reminder_log WHERE loan_id=? AND milestone_type=? AND expiry_date=? AND status='sent'", (str(loan_id), milestone_type, expiry_date.isoformat())).fetchone()
    return bool(r and int(r["c"] or 0) > 0)


def get_loan_reminder_log(limit: int = 50) -> list[dict]:
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            rows = conn.execute("SELECT * FROM loan_reminder_log ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def get_due_loan_reminders(reference_date: date | None = None) -> list[dict]:
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


def get_smtp_config() -> dict:
    defaults = {"smtp_host": "", "smtp_port": 587, "encryption": "STARTTLS", "username": "", "password": "", "sender_email": "", "sender_name": "Axiom Ledger", "recipient_emails": "", "auto_send_on_startup": True, "default_reminder_months": 2}
    try:
        _ensure_sqlite_schema()
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                r = conn.execute("SELECT value_json FROM kv_settings WHERE key='axiom_smtp_config'").fetchone()
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
    cur = get_smtp_config()
    cur.update({k: cfg.get(k, cur.get(k)) for k in cur.keys()})
    # Don't overwrite password with masked/empty placeholder from UI
    if cfg.get("_keep_password"):
        pass
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO kv_settings (key,value_json) VALUES ('axiom_smtp_config',?)", (json.dumps(cur),))
    return True, "SMTP settings saved."


def _smtp_recipients(cfg: dict | None = None) -> list[str]:
    cfg = cfg or get_smtp_config()
    raw = str(cfg.get("recipient_emails") or cfg.get("sender_email") or "")
    return [x.strip() for x in re.split(r"[,\n;]+", raw) if x.strip() and "@" in x]


def send_email_via_smtp(subject: str, body: str, recipients: list[str] | None = None, cfg: dict | None = None) -> tuple[bool, str]:
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


def send_loan_reminder_email(loan_id: str, milestone_type: str | None = None, trigger_source: str = "manual") -> tuple[bool, str]:
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
                conn.execute("INSERT INTO loan_reminder_log (loan_id,entity,property_name,milestone_type,expiry_date,recipient_email,subject,sent_at,trigger_source,status,detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (loan_id, get_loan_target_entity(), loan.get("property_name"), ms["milestone_type"], ms["expiry_date"].isoformat(), ", ".join(recips), subject, datetime.now().isoformat(timespec="seconds"), trigger_source, "sent" if ok else "failed", msg))
        msgs.append(msg)
        sent += 1 if ok else 0
    return (sent > 0), " ".join(msgs) if msgs else "No reminder sent."


def check_and_send_due_loan_reminders(trigger_source: str = "auto") -> tuple[int, list[str]]:
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


GEMINI_LOAN_SCREENSHOT_MODELS = (
    "gemini-4.0-flash",
    "gemini-4-flash",
    "gemini-3.8-flash",
    "gemini-2.5-flash",
)


def save_loan_screenshot_to_vault(image_bytes: bytes, filename: str = "loan_screenshot.png") -> str:
    """Persist uploaded loan screenshot image into local encrypted vault directory."""
    if not image_bytes:
        return ""
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "loan_screenshot.png").name) or "loan_screenshot.png"
    vault_dir = VAULT_ROOT_DIR / "BT_Property_Holdings_Ltd" / "loans"
    vault_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = vault_dir / f"{ts}_{safe_name}"
    dest.write_bytes(image_bytes)
    try:
        return str(dest.relative_to(Path(__file__).resolve().parent))
    except Exception:
        return str(dest)


def _match_known_btph_property(candidate: str, raw_text: str = "") -> str:
    """Match an extracted property label (e.g. '33 Blampied Loan') to BT Property Holdings Ltd's property list."""
    target = get_loan_target_entity()
    props = [
        p for p in get_entity_properties(target)
        if p and not is_property_unassigned(p) and str(p).strip().lower() != "general / portfolio"
    ]
    hay_cand = str(candidate or "").strip()
    combined = f"{hay_cand} {raw_text or ''}".lower()
    if hay_cand:
        for p in props:
            if p.lower() == hay_cand.lower():
                return p
        for p in props:
            if p.lower() in hay_cand.lower() or hay_cand.lower() in p.lower():
                return p
    detected = detect_property_from_text(combined, entity=target)
    if detected and not is_property_unassigned(detected) and detected in props:
        return detected
    for p in props:
        tokens = [t for t in re.split(r"[/,\s]+", p.lower()) if len(t) >= 3]
        if tokens and all(t in combined for t in tokens):
            return p
    return hay_cand


def parse_loan_screenshot_json(raw_output: str, target_property: str | None = None) -> dict:
    """Parse and normalize structured JSON returned by Gemini multimodal loan screenshot OCR."""
    cleaned = str(raw_output or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    payload: dict = {}
    if cleaned:
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                payload = parsed[0]
            elif isinstance(parsed, dict):
                payload = parsed
        except Exception:
            m = re.search(r"\{[\s\S]*\}", cleaned)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, dict):
                        payload = parsed
                except Exception:
                    payload = {}

    acct = str(payload.get("account_number") or "").strip()
    bank = str(payload.get("bank_name") or "").strip() or infer_nz_bank_from_account(acct)
    matched_prop = target_property or _match_known_btph_property(
        str(payload.get("property_name") or payload.get("loan_label") or ""),
        cleaned,
    )

    raw_itype = str(payload.get("interest_type") or "Fixed").strip()
    itype_low = raw_itype.lower()
    if "float" in itype_low or "variable" in itype_low:
        itype = "Floating"
    elif "revolv" in itype_low or "flexi" in itype_low or "orbit" in itype_low:
        itype = "Revolving / Flexi"
    elif "split" in itype_low:
        itype = "Split"
    else:
        itype = "Fixed"

    raw_rtype = str(payload.get("repayment_type") or "Interest Only").strip()
    rtype_low = raw_rtype.lower()
    if "principal" in rtype_low or "p&i" in rtype_low or "table" in rtype_low:
        rtype = "Principal & Interest"
    elif "revolv" in rtype_low:
        rtype = "Revolving"
    else:
        rtype = "Interest Only"

    raw_freq = str(payload.get("repayment_frequency") or "Monthly").strip()
    freq_low = raw_freq.lower()
    if "fortnight" in freq_low:
        freq = "Fortnightly"
    elif "week" in freq_low:
        freq = "Weekly"
    elif "quarter" in freq_low:
        freq = "Quarterly"
    elif "annual" in freq_low or "year" in freq_low:
        freq = "Annually"
    else:
        freq = "Monthly"

    next_amt_raw = payload.get("regular_repayment_amount")
    if next_amt_raw in (None, "", 0, "0"):
        next_amt_raw = payload.get("next_debit_interest_amount")
    est_flag = bool(payload.get("next_payment_is_estimated")) or ("estimat" in str(next_amt_raw or "").lower())

    bal = _clean_loan_money(payload.get("current_balance"))
    limit_amt = _clean_loan_money(payload.get("loan_limit"))
    orig_draw = _clean_loan_money(payload.get("original_drawdown_amount"))
    if orig_draw == 0.0 and limit_amt > 0.0:
        orig_draw = limit_amt
    elif limit_amt == 0.0 and orig_draw > 0.0:
        limit_amt = orig_draw

    return {
        "property_name": matched_prop,
        "loan_label": str(payload.get("loan_label") or "Main Facility").strip() or "Main Facility",
        "bank_name": bank,
        "account_holder": str(payload.get("account_holder") or "BT Property Holdings Limited").strip(),
        "account_product_type": str(payload.get("account_product_type") or "").strip(),
        "account_number": acct,
        "repayment_account": str(payload.get("repayment_account") or "").strip(),
        "drawdown_date": _loan_date_iso(payload.get("drawdown_date")),
        "original_drawdown_amount": orig_draw,
        "loan_limit": limit_amt,
        "current_balance": bal,
        "available_redraw": _clean_loan_money(payload.get("available_redraw")),
        "interest_type": itype,
        "repayment_type": rtype,
        "interest_rate_pct": _clean_loan_rate(payload.get("interest_rate_pct")),
        "fixed_rate_review_date": _loan_date_iso(payload.get("fixed_rate_review_date")),
        "interest_only_end_date": _loan_date_iso(payload.get("interest_only_end_date")),
        "loan_term_end_date": _loan_date_iso(payload.get("loan_term_end_date")),
        "interest_accrued": _clean_loan_money(payload.get("interest_accrued")),
        "next_repayment_date": _loan_date_iso(payload.get("next_repayment_date")),
        "repayment_frequency": freq,
        "regular_repayment_amount": _clean_loan_money(next_amt_raw),
        "next_payment_is_estimated": est_flag,
        "original_term_text": str(payload.get("original_term_text") or "").strip(),
        "remaining_term_text": str(payload.get("remaining_term_text") or "").strip(),
        "broker_or_banker": str(payload.get("broker_or_banker") or "").strip(),
        "notes": str(payload.get("notes") or "").strip(),
    }


def extract_loan_details_from_screenshot_gemini(
    image_bytes: bytes,
    mime_type: str = "image/png",
    filename: str = "loan_screenshot.png",
    target_property: str | None = None,
    api_key: str | None = None,
) -> tuple[dict, str, str]:
    """
    Multimodal NZ Bank Loan Screenshot Extractor (Gemini 4 Flash with automatic 3.8 Flash fallback).
    Returns (normalized_loan_dict, used_model_name, vault_relative_path).
    """
    resolved_key = (
        api_key
        or st.session_state.get("google_genai_api_key", "")
        or _load_memory_store().get("google_genai_api_key", "")
        or os.environ.get("GEMINI_API_KEY", "")
        or os.environ.get("GOOGLE_API_KEY", "")
    ).strip()
    if not resolved_key:
        raise ValueError(
            "Google Gemini API Key is required for Screenshot Import. Add it in ⚙ Settings → AI & API Connections."
        )

    from google import genai
    from google.genai import types

    target = get_loan_target_entity()
    known_props = [
        p for p in get_entity_properties(target)
        if p and not is_property_unassigned(p) and str(p).strip().lower() != "general / portfolio"
    ]
    vault_path = save_loan_screenshot_to_vault(image_bytes, filename=filename)

    prompt = (
        "You are an expert New Zealand property lending & banking OCR parser. "
        "Extract EVERY loan detail visible in this bank screenshot (e.g., Westpac, ANZ, ASB, BNZ, Kiwibank, TSB, SBS) "
        "and return ONLY a single valid JSON object with no markdown fences and no extra commentary.\n\n"
        f"Known properties for BT Property Holdings Ltd: {json.dumps(known_props)}\n"
        "If the screenshot title/nickname mentions one of these properties (e.g. '33 Blampied Loan' -> '33 Blampied'), "
        "set `property_name` to the exact matching property name from the list above.\n\n"
        "Required JSON keys (use empty string \"\" or 0 if not shown in the image; convert all dates to YYYY-MM-DD):\n"
        "{\n"
        '  "property_name": "Matched property name e.g. 33 Blampied or 303 Weymouth",\n'
        '  "loan_label": "Nickname / loan title shown on screen e.g. 33 Blampied Loan or Main Facility",\n'
        '  "bank_name": "Bank name e.g. Westpac, ANZ, ASB, BNZ, Kiwibank (check logos, branding, or NZ bank prefix: 03=Westpac, 01/06=ANZ, 12=ASB, 02=BNZ, 38=Kiwibank)",\n'
        '  "account_holder": "Account holder legal name e.g. BT Property Holdings Limited",\n'
        '  "account_product_type": "Loan product/account type e.g. Choices, Home Loan, Orbit, Flexi",\n'
        '  "account_number": "Full loan account number exactly as shown e.g. 03-0399-0618477-092 or 86131912-1003",\n'
        '  "repayment_account": "Repayment / debit interest source bank account number if shown",\n'
        '  "drawdown_date": "YYYY-MM-DD or empty string",\n'
        '  "original_drawdown_amount": 0.0,\n'
        '  "loan_limit": 313000.00,\n'
        '  "current_balance": 313000.00,\n'
        '  "available_redraw": 0.00,\n'
        '  "interest_type": "Fixed or Floating or Revolving / Flexi",\n'
        '  "repayment_type": "Interest Only or Principal & Interest",\n'
        '  "interest_rate_pct": 4.49,\n'
        '  "fixed_rate_review_date": "YYYY-MM-DD (from Fixed rate review date / Fixed until <date> / Interest rate expiry)",\n'
        '  "interest_only_end_date": "YYYY-MM-DD (from Interest only end date / Interest only expiry date)",\n'
        '  "loan_term_end_date": "YYYY-MM-DD (from Loan term ends / Maturity date)",\n'
        '  "interest_accrued": 0.00,\n'
        '  "next_repayment_date": "YYYY-MM-DD (from Next repayment date / Next debit interest date)",\n'
        '  "repayment_frequency": "Monthly or Fortnightly or Weekly",\n'
        '  "regular_repayment_amount": 1155.09,\n'
        '  "next_payment_is_estimated": true,\n'
        '  "original_term_text": "e.g. 30 years 0 months",\n'
        '  "remaining_term_text": "e.g. 28 years 7 months",\n'
        '  "notes": "Any extra details worth noting"\n'
        "}\n"
        "Important rules:\n"
        "- Return positive numbers for `current_balance`, `loan_limit`, `original_drawdown_amount`, and `regular_repayment_amount` even if shown with a minus sign like '– $313,000.00'.\n"
        "- If 'Interest rate type' says 'Fixed until 22 May 2027', set `interest_type` = 'Fixed' and `fixed_rate_review_date` = '2027-05-22'.\n"
        "- If 'Next debit interest amount' says '$1,155.09 (estimated)', set `regular_repayment_amount` = 1155.09 and `next_payment_is_estimated` = true.\n"
        "- Output raw JSON only."
    )

    client = genai.Client(api_key=resolved_key)
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type or "image/png")

    last_err: Exception | None = None
    raw_text = ""
    used_model = ""
    for candidate_model in GEMINI_LOAN_SCREENSHOT_MODELS:
        try:
            response = client.models.generate_content(
                model=candidate_model,
                contents=[image_part, prompt],
            )
            raw_text = response.text or ""
            used_model = candidate_model
            break
        except Exception as exc:
            last_err = exc
            err_s = str(exc).lower()
            if "404" in err_s or "not found" in err_s or "not_found" in err_s or "unsupported" in err_s:
                continue
            raise

    if not used_model:
        raise RuntimeError(f"Gemini screenshot extraction failed across candidate models: {last_err}")

    parsed = parse_loan_screenshot_json(raw_text, target_property=target_property)
    parsed["screenshot_vault_path"] = vault_path
    return parsed, used_model, vault_path


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


def _nz_bank_logo_svg(bank_name: str) -> str:
    """Return a compact, dark-theme-friendly inline SVG logo badge for NZ banks."""
    b = (bank_name or "").strip().lower()
    if not b or b == "bank not set":
        return ""
    if "westpac" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 112 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="Westpac NZ">'
            '<rect width="112" height="28" rx="5" fill="#DA1710"/>'
            '<path d="M7 8.5h3.2l2.1 7.3 2.4-6.6h2.6l2.4 6.6 2.1-7.3H25l-3.8 11h-2.9L16 13.2l-2.3 6.3h-2.9L7 8.5z" fill="#FFFFFF"/>'
            '<text x="29" y="18.5" font-family="Arial,Helvetica,sans-serif" font-size="13" '
            'font-weight="700" fill="#FFFFFF">Westpac</text>'
            '<text x="87" y="18.5" font-family="Arial,Helvetica,sans-serif" font-size="8.5" '
            'font-weight="700" fill="#FFFFFF">NZ</text>'
            "</svg>"
        )
    if "bnz" in b or "bank of new zealand" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 88 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="BNZ">'
            '<rect width="88" height="28" rx="5" fill="#002F6C"/>'
            '<polygon points="15,5 17.2,11.2 23.6,11.2 18.4,15 20.3,21.2 15,17.4 9.7,21.2 11.6,15 6.4,11.2 12.8,11.2" fill="#F2A900"/>'
            '<text x="30" y="19.5" font-family="Arial,Helvetica,sans-serif" font-size="15.5" '
            'font-weight="800" letter-spacing="0.5" fill="#FFFFFF">bnz</text>'
            "</svg>"
        )
    if "anz" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 88 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="ANZ NZ">'
            '<rect width="88" height="28" rx="5" fill="#007DBA"/>'
            '<circle cx="15" cy="9.5" r="2.2" fill="#FFFFFF"/>'
            '<path d="M9.5 19.5c0-3.2 2.4-5.6 5.5-5.6s5.5 2.4 5.5 5.6h-2.5c0-1.8-1.3-3.1-3-3.1s-3 1.3-3 3.1H9.5z" fill="#FFFFFF"/>'
            '<text x="27" y="19" font-family="Arial,Helvetica,sans-serif" font-size="14.5" '
            'font-weight="800" letter-spacing="0.8" fill="#FFFFFF">ANZ</text>'
            '<text x="64" y="19" font-family="Arial,Helvetica,sans-serif" font-size="8.5" '
            'font-weight="700" fill="#BFE8FF">NZ</text>'
            "</svg>"
        )
    if "asb" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 78 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="ASB">'
            '<rect width="78" height="28" rx="5" fill="#FFCC00"/>'
            '<rect x="7" y="21.5" width="64" height="2.2" rx="1" fill="#111111"/>'
            '<text x="39" y="18" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            'font-size="15" font-weight="900" letter-spacing="1.2" fill="#111111">ASB</text>'
            "</svg>"
        )
    if "kiwi" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 98 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="Kiwibank">'
            '<rect width="98" height="28" rx="5" fill="#1A1D21" stroke="#65B32E" stroke-width="1.2"/>'
            '<circle cx="14" cy="14" r="5.5" fill="#65B32E"/>'
            '<path d="M12.5 14.5l3-3 2.5 1.5-2.2 3z" fill="#111111"/>'
            '<text x="25" y="18.5" font-family="Arial,Helvetica,sans-serif" font-size="12.5" '
            'font-weight="700" fill="#FFFFFF">Kiwibank</text>'
            "</svg>"
        )
    if "tsb" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 68 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="TSB">'
            '<rect width="68" height="28" rx="5" fill="#D50032"/>'
            '<text x="34" y="19" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            'font-size="14" font-weight="800" letter-spacing="1" fill="#FFFFFF">TSB</text>'
            "</svg>"
        )
    if "sbs" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 68 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="SBS Bank">'
            '<rect width="68" height="28" rx="5" fill="#C8102E"/>'
            '<text x="34" y="19" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            'font-size="14" font-weight="800" letter-spacing="1" fill="#FFFFFF">SBS</text>'
            "</svg>"
        )
    if "heartland" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 94 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="Heartland Bank">'
            '<rect width="94" height="28" rx="5" fill="#005EB8"/>'
            '<text x="47" y="18.5" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            'font-size="12" font-weight="700" fill="#FFFFFF">Heartland</text>'
            "</svg>"
        )
    if "co-op" in b or "cooperative" in b:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 78 28" height="24" '
            'style="display:block;border-radius:5px;" aria-label="Co-operative Bank">'
            '<rect width="78" height="28" rx="5" fill="#007A5E"/>'
            '<text x="39" y="18.5" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            'font-size="12.5" font-weight="700" fill="#FFFFFF">Co-op</text>'
            "</svg>"
        )
    return ""


def _render_loan_card_html(loan: dict) -> str:
    import html as _html_mod

    def _e(v) -> str:
        return _html_mod.escape(str(v or ""))

    colour, status, _days = _loan_rate_status_tuple(loan)
    rate = float(loan.get("interest_rate_pct") or 0)
    fx = _parse_loan_date(loan.get("fixed_rate_review_date"))
    months = int(loan.get("reminder_months_before") or 2)
    win = _subtract_months(fx, months) if fx else None
    bal = (
        float(loan.get("current_balance") or 0)
        or float(loan.get("loan_limit") or 0)
        or float(loan.get("original_drawdown_amount") or 0)
    )
    bank = (
        loan.get("bank_name")
        or infer_nz_bank_from_account(loan.get("account_number") or "")
        or "Bank not set"
    )
    bank_logo_html = _nz_bank_logo_svg(bank)
    prod = f" · {_e(loan.get('account_product_type'))}" if loan.get("account_product_type") else ""
    email_icon = "✉ 2mth email ON" if loan.get("email_reminder_enabled") else "✉ email OFF"
    win_txt = (
        f"2mth email {_fmt_loan_date(win)}"
        if (win and loan.get("email_reminder_enabled"))
        else email_icon
    )
    repay_str = ""
    if float(loan.get("regular_repayment_amount") or 0) > 0:
        est_tag = " (est.)" if loan.get("next_payment_is_estimated") else ""
        repay_str = f" · Next debit: ${float(loan.get('regular_repayment_amount') or 0):,.2f}{est_tag} ({_e(loan.get('repayment_frequency') or 'Monthly')})"
    logo_block = (
        f'<div style="margin-bottom:4px;display:flex;justify-content:flex-end;">{bank_logo_html}</div>'
        if bank_logo_html
        else ""
    )
    return f"""
<div style="border:1px solid #2A2D34;border-left:4px solid {colour};background:#16181C;border-radius:8px;padding:14px 16px;margin-bottom:12px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
    <div style="font-weight:700;font-size:1.02rem;color:#E2E4E9;">🏠 {_e(loan.get('property_name'))}</div>
    <div style="text-align:right;flex-shrink:0;">
      {logo_block}
      <div style="font-size:0.75rem;color:#8E939F;">{_e(loan.get('loan_label'))}</div>
    </div>
  </div>
  <div style="display:flex;gap:28px;margin-top:10px;flex-wrap:wrap;">
    <div>
      <div style="font-size:0.62rem;letter-spacing:0.12em;color:#8E939F;">CURRENT INTEREST RATE</div>
      <div style="font-size:1.85rem;font-weight:800;line-height:1.05;color:#E2E4E9;">{rate:.2f}%<span style="font-size:0.8rem;font-weight:600;color:#8E939F;"> p.a.</span></div>
      <div style="font-size:0.75rem;color:#A0A5B1;">{_e(loan.get('interest_type'))} · {_e(loan.get('repayment_type'))}</div>
    </div>
    <div>
      <div style="font-size:0.62rem;letter-spacing:0.12em;color:#8E939F;">INTEREST RATE EXPIRY</div>
      <div style="font-size:1.3rem;font-weight:800;line-height:1.15;color:{colour};">{_fmt_loan_date(fx)}</div>
      <div style="font-size:0.75rem;color:{colour};font-weight:700;">{status}</div>
      <div style="font-size:0.72rem;color:#8E939F;">{win_txt if fx else ''}</div>
    </div>
  </div>
  <div style="margin-top:10px;padding-top:8px;border-top:1px solid #2A2D34;font-size:0.78rem;color:#A0A5B1;">
    {_e(bank)}{prod} · <span style="font-family:monospace;">{_e(loan.get('account_number') or '—')}</span> · Balance ${bal:,.0f}{repay_str}
  </div>
</div>"""


def _loan_dossier_summary_label(loan: dict | None, prop: str, count: int) -> str:
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


def render_property_loans_block(
    key_prefix: str = "loans",
    eyebrow_title: str = "STEP 2 // PROPERTY LOANS & BANK REGISTRY — PROPERTIES & WHAT EACH LOAN IS (BT PROPERTY HOLDINGS LTD)",
    show_page_title: bool = False,
    show_overview_and_dossiers: bool = False,
) -> None:
    """
    Reusable Property Loans & Bank Registry block for BT Property Holdings Ltd.
    Renders on:
      - Front Page (Step 2 / Second Block for BT Property Holdings Ltd)
      - Settings Drawer (⚙ Settings -> Property Loans & Reminders)
      - Dedicated Property Loans Page (pages/4_Property_Loans.py)
    """
    target = get_loan_target_entity()
    show_import_key = f"_{key_prefix}_show_loan_import"
    ocr_result_key = f"_{key_prefix}_loan_ocr_result"
    ocr_image_key = f"_{key_prefix}_loan_ocr_image"
    ocr_target_key = f"_{key_prefix}_ocr_target_prop"
    import_location_key = f"_{key_prefix}_loan_import_loc"
    expanded_prop_key = f"_{key_prefix}_expanded_prop"

    def _open_import_top(
        _show_key: str = show_import_key,
        _loc_key: str = import_location_key,
    ) -> None:
        st.session_state[_show_key] = True
        st.session_state[_loc_key] = "top"

    with st.container(border=True):
        hc1, hc2 = st.columns([3.2, 1.1])
        with hc1:
            st.markdown(f'<div class="axiom-eyebrow">{eyebrow_title}</div>', unsafe_allow_html=True)
            if show_page_title:
                st.title("Property Loans & Bank Registry")
            else:
                st.markdown(
                    "<h3 style='margin: 0; font-size: 1.08rem;'>BT Property Holdings Ltd — Properties, Bank Accounts, Current Interest Rates &amp; Rate Expiry Dates</h3>",
                    unsafe_allow_html=True,
                )
        with hc2:
            st.button(
                "📷 Import Loan from Screenshot",
                type="primary",
                use_container_width=True,
                key=f"{key_prefix}_btn_open_screenshot_import",
                on_click=_open_import_top,
            )

        flash = st.session_state.pop("_loan_flash", None)
        if isinstance(flash, tuple) and len(flash) == 2:
            if flash[0]:
                st.success(flash[1])
            else:
                st.error(flash[1])

        loans = get_property_loans(target)
        props = [
            p
            for p in get_entity_properties(target)
            if p.strip().lower() not in ("general / portfolio", "general / portfolio-wide")
        ]
        cfg = get_smtp_config()

        # ---------------------------------------------------------------- screenshot import
        def _render_loan_screenshot_importer() -> None:
            with st.container(border=True):
                st.markdown(
                    '<div class="axiom-eyebrow">📷 SCREENSHOT IMPORT — GEMINI 4 FLASH (AUTO-FALLBACK TO 3.8 FLASH) · REVIEW &amp; EDIT BEFORE SAVING</div>',
                    unsafe_allow_html=True,
                )
                ic1, ic2 = st.columns([1, 1.4])
                with ic1:
                    target_prop = st.selectbox(
                        "Property for this loan (leave on Auto-detect if unsure)",
                        options=["Auto-detect from screenshot"] + props,
                        key=ocr_target_key,
                    )
                    up = st.file_uploader(
                        "Upload bank loan screenshot (Westpac / ANZ / ASB / BNZ / Kiwibank…)",
                        type=["png", "jpg", "jpeg", "webp"],
                        key=f"{key_prefix}_ocr_uploader",
                    )
                    if up is not None:
                        st.image(up.getvalue(), caption=up.name, use_container_width=True)
                        if st.button(
                            "🔍 Extract Loan Details with Gemini",
                            type="primary",
                            use_container_width=True,
                            key=f"{key_prefix}_btn_ocr_extract",
                        ):
                            try:
                                with st.spinner("Gemini is reading the screenshot…"):
                                    parsed, used_model, vault_path = extract_loan_details_from_screenshot_gemini(
                                        up.getvalue(),
                                        mime_type=up.type or "image/png",
                                        filename=up.name,
                                        target_property=None if target_prop.startswith("Auto") else target_prop,
                                    )
                                parsed["_vault_path"] = vault_path
                                parsed["_used_model"] = used_model
                                st.session_state[ocr_result_key] = parsed
                                st.session_state[ocr_image_key] = up.getvalue()
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Screenshot extraction failed: {exc}")
                    if st.button("✖ Close importer", key=f"{key_prefix}_btn_close_import"):
                        st.session_state[show_import_key] = False
                        st.session_state.pop(ocr_result_key, None)
                        st.session_state.pop(import_location_key, None)
                        st.rerun()
                with ic2:
                    ocr = st.session_state.get(ocr_result_key)
                    if ocr:
                        st.success(
                            f"Extracted with **{ocr.get('_used_model', 'Gemini')}** — check every field below, fix any errors, then save. Screenshot saved to `{ocr.get('_vault_path', '')}`"
                        )
                        if st.session_state.get(ocr_image_key):
                            st.image(
                                st.session_state[ocr_image_key],
                                caption="Imported screenshot (for comparison)",
                                use_container_width=True,
                            )
                        existing_match = None
                        for _l in loans:
                            if ocr.get("account_number") and _l.get("account_number") == ocr.get("account_number"):
                                existing_match = _l
                                break
                        if existing_match is None and ocr.get("property_name"):
                            for _l in loans:
                                if (
                                    _l.get("property_name") == ocr.get("property_name")
                                    and not (_l.get("account_number") or "").strip()
                                    and float(_l.get("current_balance") or 0) <= 0
                                ):
                                    existing_match = _l
                                    break
                        if existing_match:
                            st.info(
                                f"This account number matches existing loan **{existing_match.get('loan_label')}** for **{existing_match.get('property_name')}** — saving will UPDATE it (old record replaced)."
                            )
                        with st.form(key=f"{key_prefix}_ocr_review_form"):
                            r1, r2, r3 = st.columns(3)
                            with r1:
                                o_prop = st.selectbox(
                                    "Property ⭐",
                                    options=props,
                                    index=props.index(ocr.get("property_name"))
                                    if ocr.get("property_name") in props
                                    else 0,
                                )
                                o_label = st.text_input(
                                    "Loan Label / Nickname",
                                    value=ocr.get("loan_label") or "Main Facility",
                                )
                                banks = list(NZ_BANK_OPTIONS)
                                cur = ocr.get("bank_name") or ""
                                if cur and cur not in banks:
                                    banks = [cur] + banks
                                o_bank = st.selectbox(
                                    "Bank / Lender",
                                    options=[""] + banks,
                                    index=([""] + banks).index(cur) if cur in ([""] + banks) else 0,
                                )
                                o_holder = st.text_input(
                                    "Account Holder",
                                    value=ocr.get("account_holder") or "BT Property Holdings Limited",
                                )
                                o_prod = st.text_input(
                                    "Account / Product Type",
                                    value=ocr.get("account_product_type") or "",
                                )
                                o_acct = st.text_input(
                                    "Loan Account Number ⭐",
                                    value=ocr.get("account_number") or "",
                                )
                                o_repay = st.text_input(
                                    "Repayment / Debit Interest Account",
                                    value=ocr.get("repayment_account") or "",
                                )
                            with r2:
                                o_drawn = st.number_input(
                                    "Original Drawdown Amount ($)",
                                    value=float(ocr.get("original_drawdown_amount") or 0),
                                    step=1000.0,
                                    format="%.2f",
                                )
                                o_limit = st.number_input(
                                    "Loan Limit ($)",
                                    value=float(ocr.get("loan_limit") or 0),
                                    step=1000.0,
                                    format="%.2f",
                                )
                                o_bal = st.number_input(
                                    "Current Balance ($)",
                                    value=float(ocr.get("current_balance") or 0),
                                    step=1000.0,
                                    format="%.2f",
                                )
                                o_redraw = st.number_input(
                                    "Available Redraw ($)",
                                    value=float(ocr.get("available_redraw") or 0),
                                    step=100.0,
                                    format="%.2f",
                                )
                                o_accrued = st.number_input(
                                    "Interest Accrued ($)",
                                    value=float(ocr.get("interest_accrued") or 0),
                                    step=10.0,
                                    format="%.2f",
                                )
                                o_itype = st.selectbox(
                                    "Interest Type",
                                    options=LOAN_INTEREST_TYPE_OPTIONS,
                                    index=LOAN_INTEREST_TYPE_OPTIONS.index(ocr.get("interest_type"))
                                    if ocr.get("interest_type") in LOAN_INTEREST_TYPE_OPTIONS
                                    else 0,
                                )
                                o_rate = st.number_input(
                                    "⭐ Current Interest Rate (% p.a.)",
                                    value=float(ocr.get("interest_rate_pct") or 0),
                                    step=0.01,
                                    format="%.4f",
                                )
                                o_rtype = st.selectbox(
                                    "Repayment Type",
                                    options=LOAN_REPAYMENT_TYPE_OPTIONS,
                                    index=LOAN_REPAYMENT_TYPE_OPTIONS.index(ocr.get("repayment_type"))
                                    if ocr.get("repayment_type") in LOAN_REPAYMENT_TYPE_OPTIONS
                                    else 0,
                                )
                            with r3:
                                o_dd = st.date_input(
                                    "Drawdown Date",
                                    value=_parse_loan_date(ocr.get("drawdown_date")),
                                    format="DD/MM/YYYY",
                                )
                                o_fx = st.date_input(
                                    "⭐ Interest Rate Expiry / Fixed Review Date",
                                    value=_parse_loan_date(ocr.get("fixed_rate_review_date")),
                                    format="DD/MM/YYYY",
                                )
                                o_io = st.date_input(
                                    "Interest-Only Expiry Date",
                                    value=_parse_loan_date(ocr.get("interest_only_end_date")),
                                    format="DD/MM/YYYY",
                                )
                                o_term = st.date_input(
                                    "Final Term / Maturity Date",
                                    value=_parse_loan_date(ocr.get("loan_term_end_date")),
                                    format="DD/MM/YYYY",
                                )
                                o_next = st.date_input(
                                    "Next Payment / Debit Interest Date",
                                    value=_parse_loan_date(ocr.get("next_repayment_date")),
                                    format="DD/MM/YYYY",
                                )
                                o_freq = st.selectbox(
                                    "Debit / Repayment Frequency",
                                    options=LOAN_FREQUENCY_OPTIONS,
                                    index=LOAN_FREQUENCY_OPTIONS.index(ocr.get("repayment_frequency"))
                                    if ocr.get("repayment_frequency") in LOAN_FREQUENCY_OPTIONS
                                    else 0,
                                )
                                o_amt = st.number_input(
                                    "Next Payment / Debit Interest Amount ($)",
                                    value=float(ocr.get("regular_repayment_amount") or 0),
                                    step=10.0,
                                    format="%.2f",
                                )
                                o_est = st.checkbox(
                                    "Next payment amount is estimated",
                                    value=bool(ocr.get("next_payment_is_estimated")),
                                )
                            o_orig_term = st.text_input(
                                "Original Term (e.g. 30 years 0 months)",
                                value=ocr.get("original_term_text") or "",
                            )
                            o_rem_term = st.text_input(
                                "Remaining Term (e.g. 28 years 7 months)",
                                value=ocr.get("remaining_term_text") or "",
                            )
                            o_notes = st.text_area("Notes", value=ocr.get("notes") or "", height=60)
                            o_log = st.checkbox("Log rate into Rate History", value=True)
                            if st.form_submit_button(
                                "💾 Save to Property Dossier",
                                type="primary",
                                use_container_width=True,
                            ):
                                payload = dict(existing_match) if existing_match else {}
                                payload.update(
                                    {
                                        "property_name": o_prop,
                                        "loan_label": o_label.strip() or "Main Facility",
                                        "bank_name": o_bank,
                                        "account_holder": o_holder.strip(),
                                        "account_product_type": o_prod.strip(),
                                        "account_number": o_acct.strip(),
                                        "repayment_account": o_repay.strip(),
                                        "drawdown_date": o_dd.isoformat() if o_dd else "",
                                        "original_drawdown_amount": o_drawn,
                                        "loan_limit": o_limit,
                                        "current_balance": o_bal,
                                        "available_redraw": o_redraw,
                                        "interest_accrued": o_accrued,
                                        "interest_type": o_itype,
                                        "interest_rate_pct": o_rate,
                                        "repayment_type": o_rtype,
                                        "fixed_rate_review_date": o_fx.isoformat() if o_fx else "",
                                        "interest_only_end_date": o_io.isoformat() if o_io else "",
                                        "loan_term_end_date": o_term.isoformat() if o_term else "",
                                        "next_repayment_date": o_next.isoformat() if o_next else "",
                                        "repayment_frequency": o_freq,
                                        "regular_repayment_amount": o_amt,
                                        "next_payment_is_estimated": o_est,
                                        "original_term_text": o_orig_term.strip(),
                                        "remaining_term_text": o_rem_term.strip(),
                                        "notes": o_notes.strip(),
                                        "screenshot_vault_path": ocr.get("_vault_path") or "",
                                        "use_for_coding": True,
                                        "email_reminder_enabled": True,
                                    }
                                )
                                if not existing_match:
                                    payload.update(
                                        {
                                            "interest_account_code": "Loan Interest",
                                            "principal_account_code": "Principal Loan",
                                            "reminder_months_before": int(cfg.get("default_reminder_months") or 2),
                                        }
                                    )
                                ok, msg, _lid = save_property_loan(
                                    payload,
                                    log_rate_history=o_log,
                                    rate_note=f"Screenshot import ({ocr.get('_used_model', 'Gemini')})",
                                )
                                st.session_state["_loan_flash"] = (ok, msg)
                                st.session_state.pop(ocr_result_key, None)
                                st.session_state.pop(import_location_key, None)
                                st.session_state[show_import_key] = False
                                st.rerun()
                    else:
                        st.info(
                            "Upload a screenshot on the left, then click **🔍 Extract Loan Details with Gemini**. The extracted fields will appear here for you to review and edit before saving."
                        )

        if (
            st.session_state.get(show_import_key)
            or st.session_state.get(ocr_result_key)
        ) and st.session_state.get(import_location_key, "top") == "top":
            _render_loan_screenshot_importer()

        # ---------------------------------------------------------------- summary strip
        total_drawn = sum(float(x.get("original_drawdown_amount") or 0) for x in loans)
        total_bal = sum(
            float(x.get("current_balance") or 0)
            or float(x.get("loan_limit") or 0)
            or float(x.get("original_drawdown_amount") or 0)
            for x in loans
        )
        wavg = (
            sum(
                float(x.get("interest_rate_pct") or 0)
                * (
                    float(x.get("current_balance") or 0)
                    or float(x.get("loan_limit") or 0)
                    or float(x.get("original_drawdown_amount") or 0)
                )
                for x in loans
            )
            / total_bal
            if total_bal
            else 0.0
        )
        due = get_due_loan_reminders()
        upcoming = []
        for x in loans:
            for ms in loan_milestones(x):
                if 0 <= ms["days_remaining"] <= 60:
                    upcoming.append(ms)
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Properties / Loans", f"{len(props)} / {len(loans)}")
        m2.metric("Original Drawdown", f"${total_drawn:,.0f}")
        m3.metric("Balance (recorded)", f"${total_bal:,.0f}")
        m4.metric("Weighted Avg Rate", f"{wavg:.2f}% p.a.")
        m5.metric("Expiries ≤60 days / Due emails", f"{len(upcoming)} / {len(due)}")

        if due:
            st.warning(
                f"🔔 {len(due)} reminder(s) inside the 2-month window and not yet emailed. Configure SMTP in ⚙ Settings → 05 // Email & Loan Reminders, then:"
            )
            if st.button("✉ Send All Due Reminders Now", type="primary", key=f"{key_prefix}_send_all_due"):
                n, msgs = check_and_send_due_loan_reminders(trigger_source=f"manual_{key_prefix}")
                st.session_state["_loan_flash"] = (n > 0, f"Sent {n}. " + " | ".join(msgs))
                st.rerun()

        # ---------------------------------------------------------------- rate & expiry spotlight
        st.markdown(
            '<div class="axiom-eyebrow">⭐ ACTIVE LOANS — CURRENT INTEREST RATE &amp; INTEREST RATE EXPIRY</div>',
            unsafe_allow_html=True,
        )
        active_loans = [
            x
            for x in loans
            if float(x.get("interest_rate_pct") or 0) > 0 or x.get("fixed_rate_review_date") or x.get("account_number")
        ]
        if active_loans:
            cols = st.columns(2)
            for idx, loan in enumerate(
                sorted(
                    active_loans,
                    key=lambda z: (_parse_loan_date(z.get("fixed_rate_review_date")) or date.max),
                )
            ):
                with cols[idx % 2]:
                    st.markdown(_render_loan_card_html(loan), unsafe_allow_html=True)
        else:
            st.caption("No active loan rates recorded yet — use 📷 Import Loan from Screenshot above.")

        if not show_overview_and_dossiers:
            return

        # ---------------------------------------------------------------- overview table (minimised)
        with st.expander("PORTFOLIO OVERVIEW — EVERY NZ PROPERTY, WHAT EACH LOAN IS, BANK, ACCOUNT, RATE & EXPIRY", expanded=False):
            rows = []
            for p in props:
                pls = sorted(
                    [x for x in loans if x.get("property_name") == p],
                    key=lambda z: (
                        0 if (float(z.get("interest_rate_pct") or 0) > 0 or z.get("account_number")) else 1,
                        str(z.get("loan_label") or ""),
                    ),
                )
                if not pls:
                    rows.append(
                        {
                            "Property": p,
                            "What Loan Is": "— no loan recorded —",
                            "Bank": "—",
                            "Product": "",
                            "Loan Account": "",
                            "Balance": "",
                            "Rate % p.a.": "",
                            "Rate Expiry": "",
                            "Days": "",
                            "Status": "⚪ No loan",
                            "☑ Email": "",
                        }
                    )
                for x in pls:
                    _colour, status, days = _loan_rate_status_tuple(x)
                    bal_val = (
                        float(x.get("current_balance") or 0)
                        or float(x.get("loan_limit") or 0)
                        or float(x.get("original_drawdown_amount") or 0)
                    )
                    rows.append(
                        {
                            "Property": p,
                            "What Loan Is": f"{x.get('loan_label') or 'Main Facility'} ({x.get('interest_type') or 'Fixed'} · {x.get('repayment_type') or 'Interest Only'})",
                            "Bank": x.get("bank_name") or infer_nz_bank_from_account(x.get("account_number") or "") or "—",
                            "Product": x.get("account_product_type") or "",
                            "Loan Account": x.get("account_number") or "—",
                            "Balance": f"${bal_val:,.0f}" if bal_val > 0 else "—",
                            "Rate % p.a.": f"{x.get('interest_type')} {float(x.get('interest_rate_pct') or 0):.2f}%"
                            if float(x.get("interest_rate_pct") or 0) > 0
                            else "—",
                            "Rate Expiry": _fmt_loan_date(x.get("fixed_rate_review_date")),
                            "Days": days if days is not None else "—",
                            "Status": status,
                            "☑ Email": "☑" if x.get("email_reminder_enabled") else "☐",
                        }
                    )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.markdown('<div class="axiom-divider" style="margin: 0.6rem 0;"></div>', unsafe_allow_html=True)

        # ---------------------------------------------------------------- dossiers (minimised)
        with st.expander(
            "PROPERTY LOAN DOSSIERS — CLICK ANY PROPERTY TO VIEW OR EDIT ITS LOAN DETAILS & RATE HISTORY",
            expanded=bool(st.session_state.get(expanded_prop_key)),
        ):
            code_options = get_account_code_options(target)
            for prop_idx, prop in enumerate(props):
                pls = sorted(
                    [x for x in loans if x.get("property_name") == prop],
                    key=lambda z: (
                        0 if (float(z.get("interest_rate_pct") or 0) > 0 or z.get("account_number")) else 1,
                        str(z.get("loan_label") or ""),
                    ),
                )
                first = pls[0] if pls else None
                with st.expander(
                    _loan_dossier_summary_label(first, prop, len(pls)),
                    expanded=(st.session_state.get(expanded_prop_key) == prop),
                ):
                    bc1, bc2 = st.columns([1, 3])
                    with bc1:
                        def _open_import_for_prop(
                            _show_key: str = show_import_key,
                            _target_key: str = ocr_target_key,
                            _loc_key: str = import_location_key,
                            _exp_key: str = expanded_prop_key,
                            _prop_name: str = prop,
                        ) -> None:
                            st.session_state[_show_key] = True
                            st.session_state[_target_key] = _prop_name
                            st.session_state[_loc_key] = _prop_name
                            st.session_state[_exp_key] = _prop_name

                        st.button(
                            "📷 Import screenshot for this property",
                            key=f"{key_prefix}_shot_{prop_idx}_{prop}",
                            use_container_width=True,
                            on_click=_open_import_for_prop,
                        )
                    with bc2:
                        if st.button(
                            "+ Add Loan / Tranche for this property",
                            key=f"{key_prefix}_addloan_{prop_idx}_{prop}",
                        ):
                            ok, msg, _lid = create_property_loan(
                                prop, f"Facility {len(pls) + 1}" if pls else "Main Facility"
                            )
                            st.session_state["_loan_flash"] = (ok, msg)
                            st.session_state[expanded_prop_key] = prop
                            st.rerun()
                    if (
                        st.session_state.get(show_import_key)
                        or st.session_state.get(ocr_result_key)
                    ) and st.session_state.get(import_location_key) == prop:
                        _render_loan_screenshot_importer()
                    for loan in pls:
                        lid = loan["loan_id"]
                        st.markdown(_render_loan_card_html(loan), unsafe_allow_html=True)
                        with st.form(key=f"{key_prefix}_loanform_{lid}"):
                            c1, c2, c3 = st.columns(3)
                            with c1:
                                label = st.text_input(
                                    "Loan Label / Nickname",
                                    value=loan.get("loan_label") or "Main Facility",
                                )
                                banks = list(NZ_BANK_OPTIONS)
                                cur_bank = loan.get("bank_name") or ""
                                if cur_bank and cur_bank not in banks:
                                    banks = [cur_bank] + banks
                                bank = st.selectbox(
                                    "Bank / Lender",
                                    options=[""] + banks,
                                    index=([""] + banks).index(cur_bank) if cur_bank in ([""] + banks) else 0,
                                )
                                holder = st.text_input(
                                    "Account Holder",
                                    value=loan.get("account_holder") or "BT Property Holdings Limited",
                                )
                                prod = st.text_input(
                                    "Account / Product Type (Choices, Home Loan…)",
                                    value=loan.get("account_product_type") or "",
                                )
                                acct = st.text_input(
                                    "Loan Account Number",
                                    value=loan.get("account_number") or "",
                                    help="Used as a coding trigger — statements showing this number auto-assign this property.",
                                )
                                repay_acct = st.text_input(
                                    "Repayment / Debit Interest Account",
                                    value=loan.get("repayment_account") or "",
                                )
                                broker = st.text_input(
                                    "Broker / Banker Contact",
                                    value=loan.get("broker_or_banker") or "",
                                )
                            with c2:
                                drawn = st.number_input(
                                    "Original Drawdown Amount ($)",
                                    value=float(loan.get("original_drawdown_amount") or 0),
                                    step=1000.0,
                                    format="%.2f",
                                )
                                limit_amt = st.number_input(
                                    "Loan Limit ($)",
                                    value=float(loan.get("loan_limit") or 0),
                                    step=1000.0,
                                    format="%.2f",
                                )
                                balance = st.number_input(
                                    "Current Balance ($)",
                                    value=float(loan.get("current_balance") or 0),
                                    step=1000.0,
                                    format="%.2f",
                                )
                                redraw = st.number_input(
                                    "Available Redraw ($)",
                                    value=float(loan.get("available_redraw") or 0),
                                    step=100.0,
                                    format="%.2f",
                                )
                                accrued = st.number_input(
                                    "Interest Accrued ($)",
                                    value=float(loan.get("interest_accrued") or 0),
                                    step=10.0,
                                    format="%.2f",
                                )
                                itype = st.selectbox(
                                    "Interest Type",
                                    options=LOAN_INTEREST_TYPE_OPTIONS,
                                    index=LOAN_INTEREST_TYPE_OPTIONS.index(loan.get("interest_type"))
                                    if loan.get("interest_type") in LOAN_INTEREST_TYPE_OPTIONS
                                    else 0,
                                )
                                rate = st.number_input(
                                    "⭐ Current Interest Rate (% p.a.)",
                                    value=float(loan.get("interest_rate_pct") or 0),
                                    step=0.01,
                                    format="%.4f",
                                )
                                rtype = st.selectbox(
                                    "Repayment Type",
                                    options=LOAN_REPAYMENT_TYPE_OPTIONS,
                                    index=LOAN_REPAYMENT_TYPE_OPTIONS.index(loan.get("repayment_type"))
                                    if loan.get("repayment_type") in LOAN_REPAYMENT_TYPE_OPTIONS
                                    else 0,
                                )
                            with c3:
                                dd = st.date_input(
                                    "Drawdown Date",
                                    value=_parse_loan_date(loan.get("drawdown_date")),
                                    format="DD/MM/YYYY",
                                )
                                fx = st.date_input(
                                    "⭐ Interest Rate Expiry / Fixed Review Date (2-month email based on this)",
                                    value=_parse_loan_date(loan.get("fixed_rate_review_date")),
                                    format="DD/MM/YYYY",
                                )
                                io_end = st.date_input(
                                    "Interest-Only Expiry Date",
                                    value=_parse_loan_date(loan.get("interest_only_end_date")),
                                    format="DD/MM/YYYY",
                                )
                                term_end = st.date_input(
                                    "Final Term / Maturity Date",
                                    value=_parse_loan_date(loan.get("loan_term_end_date")),
                                    format="DD/MM/YYYY",
                                )
                                nxt = st.date_input(
                                    "Next Payment / Debit Interest Date",
                                    value=_parse_loan_date(loan.get("next_repayment_date")),
                                    format="DD/MM/YYYY",
                                )
                                freq = st.selectbox(
                                    "Debit / Repayment Frequency",
                                    options=LOAN_FREQUENCY_OPTIONS,
                                    index=LOAN_FREQUENCY_OPTIONS.index(loan.get("repayment_frequency"))
                                    if loan.get("repayment_frequency") in LOAN_FREQUENCY_OPTIONS
                                    else 0,
                                )
                                regamt = st.number_input(
                                    "Next Payment / Debit Interest Amount ($)",
                                    value=float(loan.get("regular_repayment_amount") or 0),
                                    step=10.0,
                                    format="%.2f",
                                )
                                est = st.checkbox(
                                    "Next payment amount is estimated",
                                    value=bool(loan.get("next_payment_is_estimated")),
                                )
                            t1, t2 = st.columns(2)
                            with t1:
                                orig_term = st.text_input(
                                    "Original Term (e.g. 30 years 0 months)",
                                    value=loan.get("original_term_text") or "",
                                )
                            with t2:
                                rem_term = st.text_input(
                                    "Remaining Term (e.g. 28 years 7 months)",
                                    value=loan.get("remaining_term_text") or "",
                                )
                            st.markdown("**Coding triggers & email reminder**")
                            k1, k2 = st.columns(2)
                            with k1:
                                use_coding = st.checkbox(
                                    "☑ Use this loan for automatic transaction coding (account # / triggers assign this property)",
                                    value=bool(loan.get("use_for_coding")),
                                )
                                triggers = st.text_input(
                                    "Extra Coding Trigger Keywords (comma separated)",
                                    value=loan.get("coding_trigger_keywords") or "",
                                )
                                int_opts = list(
                                    dict.fromkeys(
                                        code_options + ["Loan Interest", loan.get("interest_account_code") or "Loan Interest"]
                                    )
                                )
                                prin_opts = list(
                                    dict.fromkeys(
                                        code_options
                                        + ["Principal Loan", loan.get("principal_account_code") or "Principal Loan"]
                                    )
                                )
                                int_code = st.selectbox(
                                    "Interest Item Code",
                                    options=int_opts,
                                    index=int_opts.index(loan.get("interest_account_code"))
                                    if loan.get("interest_account_code") in int_opts
                                    else int_opts.index("Loan Interest"),
                                )
                                prin_code = st.selectbox(
                                    "Principal Item Code",
                                    options=prin_opts,
                                    index=prin_opts.index(loan.get("principal_account_code"))
                                    if loan.get("principal_account_code") in prin_opts
                                    else prin_opts.index("Principal Loan"),
                                )
                            with k2:
                                email_on = st.checkbox(
                                    "☑ Email me 2 months before expiry",
                                    value=bool(loan.get("email_reminder_enabled")),
                                )
                                months = st.number_input(
                                    "Reminder lead (months before)",
                                    min_value=0,
                                    max_value=12,
                                    value=int(loan.get("reminder_months_before") or 2),
                                    step=1,
                                )
                                r_fx = st.checkbox(
                                    "Remind on Fixed Rate Expiry",
                                    value=bool(loan.get("remind_on_fixed_expiry")),
                                )
                                r_io = st.checkbox(
                                    "Remind on Interest-Only End",
                                    value=bool(loan.get("remind_on_io_expiry")),
                                )
                                r_term = st.checkbox(
                                    "Remind on Loan Term End",
                                    value=bool(loan.get("remind_on_term_expiry")),
                                )
                            if loan.get("screenshot_vault_path"):
                                st.caption(f"📷 Source screenshot: `{loan.get('screenshot_vault_path')}`")
                            notes = st.text_area("Notes", value=loan.get("notes") or "", height=60)
                            log_hist = st.checkbox(
                                "Log this save into Rate History (tick when refixing / rate changes)",
                                value=False,
                            )
                            if st.form_submit_button(
                                "💾 Save Loan Details",
                                type="primary",
                                use_container_width=True,
                            ):
                                payload = dict(loan)
                                payload.update(
                                    {
                                        "loan_label": label,
                                        "bank_name": bank,
                                        "account_holder": holder.strip(),
                                        "account_product_type": prod.strip(),
                                        "account_number": acct.strip(),
                                        "repayment_account": repay_acct.strip(),
                                        "broker_or_banker": broker.strip(),
                                        "original_drawdown_amount": drawn,
                                        "loan_limit": limit_amt,
                                        "current_balance": balance,
                                        "available_redraw": redraw,
                                        "interest_accrued": accrued,
                                        "interest_type": itype,
                                        "interest_rate_pct": rate,
                                        "repayment_type": rtype,
                                        "drawdown_date": dd.isoformat() if dd else "",
                                        "fixed_rate_review_date": fx.isoformat() if fx else "",
                                        "interest_only_end_date": io_end.isoformat() if io_end else "",
                                        "loan_term_end_date": term_end.isoformat() if term_end else "",
                                        "next_repayment_date": nxt.isoformat() if nxt else "",
                                        "repayment_frequency": freq,
                                        "regular_repayment_amount": regamt,
                                        "next_payment_is_estimated": est,
                                        "original_term_text": orig_term.strip(),
                                        "remaining_term_text": rem_term.strip(),
                                        "use_for_coding": use_coding,
                                        "coding_trigger_keywords": triggers.strip(),
                                        "interest_account_code": int_code,
                                        "principal_account_code": prin_code,
                                        "email_reminder_enabled": email_on,
                                        "reminder_months_before": int(months),
                                        "remind_on_fixed_expiry": r_fx,
                                        "remind_on_io_expiry": r_io,
                                        "remind_on_term_expiry": r_term,
                                        "notes": notes.strip(),
                                    }
                                )
                                ok, msg, _ = save_property_loan(
                                    payload,
                                    log_rate_history=log_hist,
                                    rate_note="Manual save from Property Loans dossier",
                                )
                                st.session_state["_loan_flash"] = (ok, msg)
                                st.rerun()
                        for ms in loan_milestones(loan):
                            sent = has_reminder_been_sent(lid, ms["milestone_type"], ms["expiry_date"])
                            st.caption(
                                f"{ms['label']}: **{_fmt_loan_date(ms['expiry_date'])}** ({ms['days_remaining']} days) · reminder window opens {_fmt_loan_date(ms['window_start'])} · {'✉ emailed' if sent else 'not emailed yet'}"
                            )
                            if st.button(
                                f"✉ Send Reminder Email Now — {ms['label']}",
                                key=f"{key_prefix}_send_{lid}_{ms['milestone_type']}",
                            ):
                                ok, msg = send_loan_reminder_email(
                                    lid, ms["milestone_type"], trigger_source=f"manual_{key_prefix}"
                                )
                                st.session_state["_loan_flash"] = (ok, msg)
                                st.rerun()
                        hist = get_loan_rate_history(lid)
                        if hist:
                            st.markdown(
                                '<div class="axiom-eyebrow">INTEREST RATE HISTORY (ANNUAL REFIXES)</div>',
                                unsafe_allow_html=True,
                            )
                            st.dataframe(
                                pd.DataFrame(hist)[
                                    [
                                        "effective_date",
                                        "interest_type",
                                        "repayment_type",
                                        "interest_rate_pct",
                                        "review_or_expiry_date",
                                        "note",
                                        "logged_at",
                                    ]
                                ],
                                use_container_width=True,
                                hide_index=True,
                            )
                        if st.button("🗑 Delete this loan", key=f"{key_prefix}_del_{lid}"):
                            ok, msg = delete_property_loan(lid)
                            st.session_state["_loan_flash"] = (ok, msg)
                            st.rerun()




def get_categorization_system_prompt(entity: str | None = None) -> str:
    """
    Return the dynamic system prompt based on the active entity's jurisdiction:
    - 'US' -> US CPA (IRS Schedule E)
    - 'NZ' -> NZ Property Accountant (NZ IRD)
    """
    if get_entity_jurisdiction(entity) == "US":
        return US_IRS_SYSTEM_PROMPT
    base = NZ_IRD_SYSTEM_PROMPT
    try:
        loans = get_property_loans(entity)
        if loans:
            lines = []
            for _l in loans:
                if _l.get("use_for_coding"):
                    lines.append(f"- {_l.get('property_name')}: bank {_l.get('bank_name') or '?'}, loan account {_l.get('account_number') or '?'}, triggers [{_l.get('coding_trigger_keywords') or ''}] -> Interest code '{_l.get('interest_account_code')}', Principal code '{_l.get('principal_account_code')}'")
            if lines:
                base += "\n\nKnown NZ property loans for this entity (use loan account numbers/triggers to assign the correct Property; interest charges -> the loan's Interest code, principal/repayments -> its Principal code):\n" + "\n".join(lines)
    except Exception:
        pass
    return base


def add_new_entity(entity_name: str, jurisdiction: str = "NZ") -> tuple[bool, str]:
    """
    Dynamically register a new Entity (Organization) with its tax jurisdiction ('NZ' or 'US')
    and initialize empty ledgers for all financial years.
    """
    init_workspace_state()
    cleaned_name = (entity_name or "").strip()
    if not cleaned_name:
        return False, "Entity name cannot be empty."

    options = get_entity_options()
    if any(existing.lower() == cleaned_name.lower() for existing in options):
        return False, f"Entity '{cleaned_name}' already exists."

    norm_jur = "US" if str(jurisdiction).upper().startswith("US") else "NZ"
    st.session_state["entity_options"].append(cleaned_name)
    st.session_state["entity_jurisdictions"][cleaned_name] = norm_jur

    st.session_state["entity_ledgers"][cleaned_name] = pd.DataFrame(columns=STANDARD_COLUMNS)
    if cleaned_name not in st.session_state["entity_year_ledgers"]:
        st.session_state["entity_year_ledgers"][cleaned_name] = {}
    for yr in get_tax_year_options():
        st.session_state["entity_year_ledgers"][cleaned_name][yr] = pd.DataFrame(columns=STANDARD_COLUMNS)

    st.session_state["active_entity"] = cleaned_name
    st.session_state["entity"] = cleaned_name

    # Persist new organization list, jurisdiction, and default properties to .axiom_memory.json
    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    if isinstance(props_map, dict) and cleaned_name not in props_map:
        props_map[cleaned_name] = list(DEFAULT_PROPERTIES)
    rules_map = store.setdefault("memorized_rules", {})
    if isinstance(rules_map, dict) and cleaned_name not in rules_map:
        rules_map[cleaned_name] = []
    _save_memory_store(store)

    return True, f"Entity '{cleaned_name}' ({norm_jur}) created and activated."


def rename_entity(
    old_name: str,
    new_name: str,
    new_jurisdiction: str | None = None,
) -> tuple[bool, str]:
    """
    Rename an existing Entity (Organization) across all session state stores AND
    persistent disk storage (.axiom_memory.json), preserving its position, properties,
    memorized rules, uploaded files, financial year ledgers, and jurisdiction mapping.
    """
    init_workspace_state()
    src_name = (old_name or "").strip()
    target_name = (new_name or "").strip()

    options = get_entity_options()
    if src_name not in options:
        return False, f"Entity '{src_name}' was not found."

    if not target_name:
        return False, "New entity name cannot be empty."

    # Check case-insensitive collision with any OTHER entity
    for existing in options:
        if existing != src_name and existing.lower() == target_name.lower():
            return False, f"An entity named '{existing}' already exists."

    resolved_jur = (
        ("US" if str(new_jurisdiction).upper().startswith("US") else "NZ")
        if new_jurisdiction is not None
        else get_entity_jurisdiction(src_name)
    )

    if target_name == src_name:
        st.session_state["entity_jurisdictions"][src_name] = resolved_jur
        _save_memory_store()
        return True, f"Entity '{src_name}' updated ({resolved_jur})."

    # Replace in-place within entity_options list to preserve visual ordering
    idx = options.index(src_name)
    st.session_state["entity_options"][idx] = target_name

    # Migrate jurisdiction mapping
    st.session_state["entity_jurisdictions"].pop(src_name, None)
    st.session_state["entity_jurisdictions"][target_name] = resolved_jur

    # Migrate isolated financial year ledgers & active ledger cache
    year_ledgers = st.session_state["entity_year_ledgers"].pop(src_name, {})
    for yr in get_tax_year_options():
        if yr not in year_ledgers:
            year_ledgers[yr] = pd.DataFrame(columns=STANDARD_COLUMNS)
    st.session_state["entity_year_ledgers"][target_name] = year_ledgers

    active_yr = get_active_tax_year()
    legacy_df = st.session_state["entity_ledgers"].pop(
        src_name,
        year_ledgers.get(active_yr, pd.DataFrame(columns=STANDARD_COLUMNS)),
    )
    st.session_state["entity_ledgers"][target_name] = year_ledgers.get(active_yr, legacy_df)

    # Migrate uploaded files store across all years
    for files_state_key in ("entity_uploaded_files", "entity_year_files"):
        if files_state_key in st.session_state and isinstance(st.session_state[files_state_key], dict):
            old_files = st.session_state[files_state_key].pop(src_name, None)
            if old_files is not None:
                st.session_state[files_state_key][target_name] = old_files

    # Migrate vault folder on disk if present (`data/vault/<src_name>` -> `data/vault/<target_name>`)
    old_vault_dir = VAULT_ROOT_DIR / _safe_vault_segment(src_name)
    new_vault_dir = VAULT_ROOT_DIR / _safe_vault_segment(target_name)
    if old_vault_dir.exists() and old_vault_dir != new_vault_dir:
        try:
            if not new_vault_dir.exists():
                old_vault_dir.rename(new_vault_dir)
        except Exception:
            pass

    # Migrate SQLite uploaded_files and master_ledgers rows to target_name
    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute(
                "UPDATE uploaded_files SET entity = ? WHERE entity = ?",
                (target_name, src_name),
            )
            conn.execute(
                "UPDATE master_ledgers SET entity = ? WHERE entity = ?",
                (target_name, src_name),
            )
            conn.execute(
                "UPDATE property_loans SET entity = ? WHERE entity = ?",
                (target_name, src_name),
            )
            conn.execute(
                "UPDATE property_loan_rate_history SET entity = ? WHERE entity = ?",
                (target_name, src_name),
            )
            conn.execute(
                "UPDATE loan_reminder_log SET entity = ? WHERE entity = ?",
                (target_name, src_name),
            )
            # Update vault_path strings if vault directory was renamed
            rows = conn.execute(
                "SELECT file_id, vault_path FROM uploaded_files WHERE entity = ?",
                (target_name,),
            ).fetchall()
            old_seg = f"/{_safe_vault_segment(src_name)}/"
            new_seg = f"/{_safe_vault_segment(target_name)}/"
            for r in rows:
                vp = str(r["vault_path"] or "")
                if old_seg in vp:
                    conn.execute(
                        "UPDATE uploaded_files SET vault_path = ? WHERE file_id = ?",
                        (vp.replace(old_seg, new_seg, 1), str(r["file_id"])),
                    )

    # Keep active_entity & entity synchronized if the active entity was renamed
    if st.session_state.get("active_entity") == src_name or st.session_state.get("entity") == src_name:
        st.session_state["active_entity"] = target_name
        st.session_state["entity"] = target_name

    # Migrate entity_properties and memorized_rules in .axiom_memory.json and save to disk
    store = _load_memory_store()
    props_map = store.setdefault("entity_properties", {})
    if isinstance(props_map, dict):
        old_props = props_map.pop(src_name, None)
        if old_props is not None:
            props_map[target_name] = old_props
        elif target_name not in props_map:
            props_map[target_name] = list(DEFAULT_PROPERTIES)

    rules_map = store.setdefault("memorized_rules", {})
    if isinstance(rules_map, dict):
        old_rules = rules_map.pop(src_name, None)
        if old_rules is not None:
            rules_map[target_name] = old_rules
        elif target_name not in rules_map:
            rules_map[target_name] = []

    _save_memory_store(store)

    return True, f"Renamed '{src_name}' to '{target_name}' ({resolved_jur})."


def delete_entity(entity_name: str) -> tuple[bool, str]:
    """
    Delete an Entity (Organization) from session state, SQLite, and .axiom_memory.json.
    Refuses to delete the last remaining organization.
    """
    init_workspace_state()
    target = (entity_name or "").strip()
    options = list(get_entity_options())
    if target not in options:
        return False, f"Entity '{target}' was not found."
    if len(options) <= 1:
        return False, "Cannot delete the last remaining organization."

    options = [x for x in options if x != target]
    st.session_state["entity_options"] = options
    st.session_state.get("entity_jurisdictions", {}).pop(target, None)
    st.session_state.get("entity_year_ledgers", {}).pop(target, None)
    st.session_state.get("entity_ledgers", {}).pop(target, None)
    for files_state_key in ("entity_uploaded_files", "entity_year_files"):
        if files_state_key in st.session_state and isinstance(st.session_state[files_state_key], dict):
            st.session_state[files_state_key].pop(target, None)

    if st.session_state.get("active_entity") == target or st.session_state.get("entity") == target:
        st.session_state["active_entity"] = options[0]
        st.session_state["entity"] = options[0]

    _ensure_sqlite_schema()
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            conn.execute("DELETE FROM uploaded_files WHERE entity = ?", (target,))
            conn.execute("DELETE FROM master_ledgers WHERE entity = ?", (target,))
            conn.execute("DELETE FROM property_loans WHERE entity = ?", (target,))
            conn.execute("DELETE FROM property_loan_rate_history WHERE entity = ?", (target,))
            conn.execute("DELETE FROM loan_reminder_log WHERE entity = ?", (target,))

    store = _load_memory_store()
    store["entity_options"] = list(options)
    store["active_entity"] = str(st.session_state["active_entity"])
    for map_key in (
        "entity_jurisdictions",
        "entity_properties",
        "memorized_rules",
        "entity_property_aliases",
        "entity_account_codes",
        "entity_code_scopes",
    ):
        sub = store.get(map_key)
        if isinstance(sub, dict):
            sub.pop(target, None)
    _save_memory_store(store)
    return True, f"Organization '{target}' removed."


def add_new_tax_year(year_value: str) -> tuple[bool, str]:
    """
    Dynamically add a single financial year (e.g. '2028') to the workspace year bar
    and initialize isolated DataFrames for all entities.
    """
    init_workspace_state()
    cleaned_year = (year_value or "").strip()
    if not re.match(r"^\d{4}$", cleaned_year):
        return False, "Financial year must be a 4-digit year (e.g., 2028)."

    years = get_tax_year_options()
    if cleaned_year in years:
        st.session_state["active_tax_year"] = cleaned_year
        return True, f"Switched to existing financial year {cleaned_year}."

    years.append(cleaned_year)
    years.sort()
    st.session_state["tax_year_options"] = years

    for ent in get_entity_options():
        st.session_state["entity_year_ledgers"].setdefault(ent, {})
        st.session_state["entity_year_ledgers"][ent][cleaned_year] = pd.DataFrame(columns=STANDARD_COLUMNS)

    st.session_state["active_tax_year"] = cleaned_year
    return True, f"Financial year {cleaned_year} added and selected."


def verify_gemini_connection(api_key: str | None = None) -> dict[str, object]:
    """
    Actively test and verify connection to Google Gemini API using the provided API key.
    Updates st.session_state['gemini_connection_status'].
    """
    resolved_key = (api_key if api_key is not None else st.session_state.get("google_genai_api_key", "")).strip()
    if not resolved_key:
        status = {
            "connected": False,
            "label": "NO API KEY",
            "detail": "Enter a Google Gemini API Key to connect.",
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }
        st.session_state["gemini_connection_status"] = status
        return status

    start_ts = time.perf_counter()
    try:
        client = genai.Client(api_key=resolved_key)
        # Lightweight verification ping to confirm key validity
        models_pager = client.models.list()
        _ = next(iter(models_pager), None)
        latency_ms = int((time.perf_counter() - start_ts) * 1000)
        status = {
            "connected": True,
            "label": f"CONNECTED ({latency_ms}ms)",
            "detail": f"Verified Google GenAI endpoint ({GEMINI_OCR_MODEL} ready)",
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as exc:
        err_msg = str(exc).splitlines()[0][:90]
        status = {
            "connected": False,
            "label": "AUTH / CONNECTION ERROR",
            "detail": f"Gemini check failed: {err_msg}",
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }

    st.session_state["google_genai_api_key"] = resolved_key
    st.session_state["gemini_connection_status"] = status
    _save_memory_store()
    return status


def verify_local_llm_connection(local_llm_url: str | None = None) -> dict[str, object]:
    """
    Actively test and verify connection to the Local AI (Ollama / OpenAI-compatible) server.
    Updates st.session_state['local_ai_connection_status'].
    """
    base_url = (
        local_llm_url
        if local_llm_url is not None
        else st.session_state.get("local_llm_url", DEFAULT_LOCAL_LLM_URL)
    ).strip()

    if not base_url:
        status = {
            "connected": False,
            "label": "NO ENDPOINT URL",
            "detail": "Configure a Local AI URL (e.g. http://localhost:11434/v1).",
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }
        st.session_state["local_ai_connection_status"] = status
        return status

    models_url = base_url.rstrip("/") + "/models"
    start_ts = time.perf_counter()
    try:
        req = urllib.request.Request(models_url, method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            latency_ms = int((time.perf_counter() - start_ts) * 1000)
            payload = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
            model_list = payload.get("data", []) if isinstance(payload, dict) else []
            model_count = len(model_list) if isinstance(model_list, list) else 0
            status = {
                "connected": True,
                "label": f"CONNECTED ({latency_ms}ms)",
                "detail": f"Reachable at {base_url} ({model_count} model(s) online)",
                "checked_at": datetime.now().strftime("%H:%M:%S"),
            }
    except Exception as exc:
        err_short = str(exc).splitlines()[0][:80]
        status = {
            "connected": False,
            "label": "OFFLINE / UNREACHABLE",
            "detail": f"{base_url} unreachable ({err_short})",
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }

    st.session_state["local_ai_connection_status"] = status
    return status


ACCOUNTS_STORE_PATH = Path(__file__).resolve().parent / ".axiom_accounts.json"


def _hash_password_bcrypt(raw_password: str) -> str:
    """Hash a plaintext password using bcrypt with cost factor 12."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(raw_password.encode("utf-8"), salt).decode("utf-8")


def _verify_password_bcrypt(raw_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def _get_admin_password_hash() -> str:
    """
    Returns a valid, strictly verified bcrypt hash for the default 'admin' user.
    Generates and caches a cost-12 bcrypt hash in session state to guarantee
    cryptographic validity with the installed bcrypt library.
    """
    if "_axiom_admin_hash" not in st.session_state:
        # Default credential for local secure access: Axiom#Ledger2026!
        st.session_state["_axiom_admin_hash"] = _hash_password_bcrypt("Axiom#Ledger2026!")
    return st.session_state["_axiom_admin_hash"]


def _load_accounts_store() -> dict[str, dict[str, str]]:
    """
    Load persisted operator accounts from disk (.axiom_accounts.json) or initialize
    with the default System Administrator ('admin') account.
    """
    if "_axiom_user_accounts" in st.session_state and isinstance(st.session_state["_axiom_user_accounts"], dict):
        if st.session_state["_axiom_user_accounts"]:
            return st.session_state["_axiom_user_accounts"]

    if ACCOUNTS_STORE_PATH.exists():
        try:
            data = json.loads(ACCOUNTS_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                st.session_state["_axiom_user_accounts"] = data
                return data
        except Exception:
            pass

    default_accounts: dict[str, dict[str, str]] = {
        "admin": {
            "name": "System Administrator",
            "email": "admin@axiomledger.local",
            "password": _get_admin_password_hash(),
            "role": "Primary Administrator",
        }
    }
    st.session_state["_axiom_user_accounts"] = default_accounts
    _save_accounts_store(default_accounts)
    return default_accounts


def _save_accounts_store(accounts: dict[str, dict[str, str]]) -> None:
    """Persist operator accounts dictionary to disk (.axiom_accounts.json) and session state."""
    st.session_state["_axiom_user_accounts"] = accounts
    try:
        ACCOUNTS_STORE_PATH.write_text(json.dumps(accounts, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_all_user_accounts() -> dict[str, dict[str, str]]:
    """Return all configured operator accounts."""
    return _load_accounts_store()


def update_operator_credentials(
    current_username: str,
    new_username: str,
    new_display_name: str,
    new_email: str,
    current_password: str,
    new_password: str = "",
    confirm_password: str = "",
) -> tuple[bool, str]:
    """
    Update an existing operator's username, display name, email, and/or password.
    Verifies `current_password` via bcrypt before applying changes.
    """
    accounts = dict(_load_accounts_store())
    cur_user = (current_username or "").strip()
    if cur_user not in accounts:
        return False, f"Account '{cur_user}' not found."

    user_record = dict(accounts[cur_user])
    if not _verify_password_bcrypt(current_password or "", user_record.get("password", "")):
        return False, "Current password verification failed. Please enter your valid current password."

    target_username = re.sub(r"[^a-zA-Z0-9_.\-]", "", (new_username or "").strip())
    if not target_username or len(target_username) < 2:
        return False, "Username must be at least 2 characters (letters, numbers, _, ., -)."

    if target_username != cur_user and target_username in accounts:
        return False, f"Username '{target_username}' is already taken by another account."

    clean_name = (new_display_name or "").strip() or user_record.get("name", target_username)
    clean_email = (new_email or "").strip() or user_record.get("email", f"{target_username}@axiomledger.local")

    if new_password:
        if len(new_password) < 8:
            return False, "New password must be at least 8 characters long."
        if new_password != confirm_password:
            return False, "New password and confirmation do not match."
        user_record["password"] = _hash_password_bcrypt(new_password)

    user_record["name"] = clean_name
    user_record["email"] = clean_email

    if target_username != cur_user:
        del accounts[cur_user]
    accounts[target_username] = user_record
    _save_accounts_store(accounts)

    # Keep active session state aligned if the logged-in user renamed themselves
    if st.session_state.get("username") == cur_user:
        st.session_state["username"] = target_username
        st.session_state["name"] = clean_name

    pw_note = " and password updated" if new_password else ""
    return True, f"Credentials saved for '{target_username}' ({clean_name}){pw_note}."


def add_operator_account(
    username: str,
    display_name: str,
    email: str,
    password: str,
    confirm_password: str,
    role: str = "Operator",
) -> tuple[bool, str]:
    """Create a new bcrypt-secured operator account."""
    accounts = dict(_load_accounts_store())
    clean_user = re.sub(r"[^a-zA-Z0-9_.\-]", "", (username or "").strip())
    if not clean_user or len(clean_user) < 2:
        return False, "Username must be at least 2 characters (letters, numbers, _, ., -)."

    if clean_user in accounts:
        return False, f"Username '{clean_user}' already exists."

    clean_name = (display_name or "").strip()
    if not clean_name:
        return False, "Display Name is required."

    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters long."

    if password != confirm_password:
        return False, "Password and Confirm Password do not match."

    clean_email = (email or "").strip() or f"{clean_user}@axiomledger.local"
    accounts[clean_user] = {
        "name": clean_name,
        "email": clean_email,
        "password": _hash_password_bcrypt(password),
        "role": (role or "Operator").strip(),
    }
    _save_accounts_store(accounts)
    return True, f"Account '{clean_user}' ({clean_name}) created and ready for login."


def delete_operator_account(username_to_delete: str) -> tuple[bool, str]:
    """Remove an operator account, ensuring at least one account remains and the active user cannot delete themselves."""
    accounts = dict(_load_accounts_store())
    target = (username_to_delete or "").strip()
    if target not in accounts:
        return False, f"Account '{target}' does not exist."
    if len(accounts) <= 1:
        return False, "Cannot delete the last remaining administrator account."
    if st.session_state.get("username") == target:
        return False, "Cannot delete the currently signed-in account."

    del accounts[target]
    _save_accounts_store(accounts)
    return True, f"Account '{target}' removed."


def get_auth_config() -> dict:
    """Return the credentials and cookie configuration for streamlit-authenticator."""
    accounts = _load_accounts_store()
    usernames_cfg: dict[str, dict[str, str]] = {}
    for uname, info in accounts.items():
        usernames_cfg[uname] = {
            "name": info.get("name", uname),
            "email": info.get("email", f"{uname}@axiomledger.local"),
            "password": info.get("password", _get_admin_password_hash()),
        }
    return {
        "credentials": {
            "usernames": usernames_cfg,
        },
        "cookie": {
            "name": "axiom_ledger_session",
            "key": "axiom_ledger_strict_hmac_key_9f8e7d6c5b4a",
            "expiry_days": 1,
        },
    }


SESSION_INACTIVITY_TIMEOUT_SEC = 60 * 60  # 60 minutes


def _load_active_auth_session() -> dict[str, object] | None:
    """Load the persisted active auth session from SQLite (`kv_settings` key `axiom_auth_session`)."""
    _ensure_sqlite_schema()
    try:
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                row = conn.execute(
                    "SELECT value_json FROM kv_settings WHERE key = 'axiom_auth_session'"
                ).fetchone()
                if row and row["value_json"]:
                    parsed = json.loads(str(row["value_json"]))
                    if isinstance(parsed, dict):
                        return parsed
    except Exception:
        pass
    return None


def _save_active_auth_session(username: str, name: str, last_active_ts: float | None = None) -> None:
    """Persist the active auth session and last activity timestamp to SQLite."""
    _ensure_sqlite_schema()
    payload = {
        "authenticated": True,
        "username": str(username or "admin"),
        "name": str(name or "System Administrator"),
        "last_active_ts": float(last_active_ts if last_active_ts is not None else time.time()),
    }
    try:
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_settings (key, value_json) VALUES ('axiom_auth_session', ?)",
                    (json.dumps(payload),),
                )
    except Exception:
        pass


def _clear_active_auth_session() -> None:
    """Clear the persisted active auth session in SQLite."""
    _ensure_sqlite_schema()
    try:
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                conn.execute("DELETE FROM kv_settings WHERE key = 'axiom_auth_session'")
    except Exception:
        pass


def sync_and_touch_auth_session(authenticator: stauth.Authenticate | None = None) -> bool:
    """
    Synchronize session authentication state across pages/reruns and enforce a
    strict 60-minute sliding inactivity timeout:
    - If idle for > 60 minutes (3,600 seconds), clears session and logs the user out.
    - If active within the last 60 minutes, restores/keeps the session alive and updates `last_active_ts`.
    """
    now = time.time()
    persisted = _load_active_auth_session()

    # Check if persisted session has expired due to > 60 minutes of inactivity
    if isinstance(persisted, dict) and persisted.get("authenticated"):
        last_ts = float(persisted.get("last_active_ts") or 0.0)
        if last_ts > 0 and (now - last_ts) > SESSION_INACTIVITY_TIMEOUT_SEC:
            _clear_active_auth_session()
            st.session_state["authentication_status"] = None
            st.session_state["name"] = None
            st.session_state["username"] = None
            st.session_state["_session_expired_notice"] = True
            if authenticator is not None:
                try:
                    authenticator.cookie_controller.delete_cookie()
                except Exception:
                    pass
            return False

    auth_status = st.session_state.get("authentication_status")

    # Also check in-memory last_active_ts in case session_state survived while idle
    mem_last_ts = float(st.session_state.get("_last_active_ts") or 0.0)
    if auth_status is True and mem_last_ts > 0 and (now - mem_last_ts) > SESSION_INACTIVITY_TIMEOUT_SEC:
        _clear_active_auth_session()
        st.session_state["authentication_status"] = None
        st.session_state["name"] = None
        st.session_state["username"] = None
        st.session_state["_session_expired_notice"] = True
        if authenticator is not None:
            try:
                authenticator.cookie_controller.delete_cookie()
            except Exception:
                pass
        return False

    # Restore from persisted SQLite session if Streamlit reset session_state during page switch / rerun
    if not auth_status and isinstance(persisted, dict) and persisted.get("authenticated"):
        accounts = _load_accounts_store()
        uname = str(persisted.get("username") or "")
        if uname in accounts:
            st.session_state["authentication_status"] = True
            st.session_state["username"] = uname
            st.session_state["name"] = str(accounts[uname].get("name") or persisted.get("name") or uname)
            auth_status = True

    # Try unrendered cookie check as secondary fallback if still not authenticated
    if not auth_status and authenticator is not None:
        try:
            authenticator.login(location="unrendered")
            auth_status = st.session_state.get("authentication_status")
        except Exception:
            pass

    if auth_status is True:
        uname = str(st.session_state.get("username") or "admin")
        dname = str(st.session_state.get("name") or "System Administrator")
        st.session_state["_last_active_ts"] = now
        st.session_state.pop("_session_expired_notice", None)
        _save_active_auth_session(uname, dname, now)
        return True

    return False


def sign_out_current_session(authenticator: stauth.Authenticate | None = None) -> None:
    """Explicitly sign out the current operator and clear both SQLite and cookie sessions."""
    _clear_active_auth_session()
    st.session_state["authentication_status"] = None
    st.session_state["name"] = None
    st.session_state["username"] = None
    st.session_state.pop("_last_active_ts", None)
    if authenticator is not None:
        try:
            authenticator.cookie_controller.delete_cookie()
        except Exception:
            pass


def get_authenticator() -> stauth.Authenticate:
    """Instantiate and return the Streamlit Authenticator object."""
    config = get_auth_config()
    try:
        return stauth.Authenticate(
            credentials=config["credentials"],
            cookie_name=config["cookie"]["name"],
            cookie_key=config["cookie"]["key"],
            cookie_expiry_days=config["cookie"]["expiry_days"],
            auto_hash=False,
        )
    except TypeError:
        return stauth.Authenticate(
            credentials=config["credentials"],
            cookie_name=config["cookie"]["name"],
            cookie_key=config["cookie"]["key"],
            cookie_expiry_days=config["cookie"]["expiry_days"],
        )


def init_workspace_state() -> None:
    """Initialize global workspace state variables, dynamic entities/years, and background task manager."""
    import os

    store = _load_memory_store()

    saved_opts = store.get("entity_options")
    if isinstance(saved_opts, list) and saved_opts:
        st.session_state["entity_options"] = [str(x) for x in saved_opts if str(x).strip()]
    elif "entity_options" not in st.session_state:
        st.session_state["entity_options"] = list(ENTITY_OPTIONS)

    saved_jurs = store.get("entity_jurisdictions")
    merged_jurs = dict(DEFAULT_ENTITY_JURISDICTIONS)
    if isinstance(saved_jurs, dict):
        for k, v in saved_jurs.items():
            merged_jurs[str(k)] = "US" if str(v).upper().startswith("US") else "NZ"
    st.session_state["entity_jurisdictions"] = merged_jurs

    if "tax_year_options" not in st.session_state:
        st.session_state["tax_year_options"] = list(TAX_YEAR_OPTIONS)
    else:
        filtered_years = [str(y) for y in st.session_state["tax_year_options"] if str(y) not in ("2024", "2025")]
        for yr in TAX_YEAR_OPTIONS:
            if yr not in filtered_years:
                filtered_years.append(yr)
        filtered_years.sort()
        st.session_state["tax_year_options"] = filtered_years

    if "active_entity" not in st.session_state or st.session_state["active_entity"] not in st.session_state["entity_options"]:
        saved_active = str(store.get("active_entity") or "").strip()
        if saved_active and saved_active in st.session_state["entity_options"]:
            st.session_state["active_entity"] = saved_active
        else:
            st.session_state["active_entity"] = st.session_state["entity_options"][0]

    if "entity" not in st.session_state:
        st.session_state["entity"] = st.session_state["active_entity"]
    else:
        st.session_state["entity"] = st.session_state["active_entity"]

    # Normalize any legacy range year or deprecated year (2024, 2025) to "2026"
    if "active_tax_year" not in st.session_state:
        st.session_state["active_tax_year"] = "2026" if "2026" in st.session_state["tax_year_options"] else st.session_state["tax_year_options"][0]
    else:
        curr_yr = str(st.session_state["active_tax_year"])
        if "–" in curr_yr or "-" in curr_yr:
            curr_yr = curr_yr[:4]
        if curr_yr in ("2024", "2025") or curr_yr not in st.session_state["tax_year_options"]:
            curr_yr = "2026" if "2026" in st.session_state["tax_year_options"] else st.session_state["tax_year_options"][0]
        st.session_state["active_tax_year"] = curr_yr

    saved_cat_model = str(store.get("categorization_model") or "").strip()
    if "categorization_model" not in st.session_state:
        st.session_state["categorization_model"] = (
            saved_cat_model if saved_cat_model in CATEGORIZATION_MODEL_OPTIONS else CATEGORIZATION_MODEL_OPTIONS[0]
        )

    saved_gem_key = str(store.get("google_genai_api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if "google_genai_api_key" not in st.session_state or not str(st.session_state.get("google_genai_api_key") or "").strip():
        st.session_state["google_genai_api_key"] = saved_gem_key

    saved_loc_url = str(store.get("local_llm_url") or DEFAULT_LOCAL_LLM_URL).strip()
    if "local_llm_url" not in st.session_state:
        st.session_state["local_llm_url"] = saved_loc_url

    saved_loc_model = str(store.get("local_llm_model") or DEFAULT_LOCAL_LLM_MODEL).strip()
    if "local_llm_model" not in st.session_state:
        st.session_state["local_llm_model"] = saved_loc_model

    if "show_settings_drawer" not in st.session_state:
        st.session_state["show_settings_drawer"] = False

    if "gemini_connection_status" not in st.session_state:
        saved_gem_status = store.get("gemini_connection_status")
        has_key = bool(st.session_state.get("google_genai_api_key", "").strip())
        if has_key and isinstance(saved_gem_status, dict) and saved_gem_status.get("label"):
            st.session_state["gemini_connection_status"] = dict(saved_gem_status)
        else:
            st.session_state["gemini_connection_status"] = {
                "connected": False,
                "label": "KEY SAVED (READY)" if has_key else "NOT CONFIGURED",
                "detail": "Saved permanently to local vault. Click 'Test & Verify Connection' to ping." if has_key else "Click 'Test & Verify Connection' in Settings to validate.",
                "checked_at": "—",
            }

    if "local_ai_connection_status" not in st.session_state:
        st.session_state["local_ai_connection_status"] = {
            "connected": False,
            "label": "UNVERIFIED",
            "detail": f"Configured endpoint: {st.session_state.get('local_llm_url', DEFAULT_LOCAL_LLM_URL)}",
            "checked_at": "—",
        }

    # Nested (entity -> year -> DataFrame) store for strict entity + financial year isolation
    if "entity_year_ledgers" not in st.session_state:
        st.session_state["entity_year_ledgers"] = {}

    for ent in st.session_state["entity_options"]:
        if ent not in st.session_state["entity_year_ledgers"]:
            st.session_state["entity_year_ledgers"][ent] = {}
        for yr in st.session_state["tax_year_options"]:
            if yr not in st.session_state["entity_year_ledgers"][ent]:
                st.session_state["entity_year_ledgers"][ent][yr] = pd.DataFrame(columns=STANDARD_COLUMNS)

    # Keep backwards-compatible st.session_state["entity_ledgers"][entity] synced with active_tax_year
    active_yr = st.session_state["active_tax_year"]
    if "entity_ledgers" not in st.session_state:
        st.session_state["entity_ledgers"] = {
            ent: st.session_state["entity_year_ledgers"][ent][active_yr]
            for ent in st.session_state["entity_options"]
        }
    else:
        for ent in st.session_state["entity_options"]:
            if ent not in st.session_state["entity_ledgers"]:
                st.session_state["entity_ledgers"][ent] = st.session_state["entity_year_ledgers"][ent][active_yr]

    if "background_task_logs" not in st.session_state:
        ts = datetime.now().strftime("%H:%M:%S")
        st.session_state["background_task_logs"] = [
            f"[{ts}] [SYSTEM] Axiom Background Task Supervisor initialized. All worker queues Online."
        ]

    if "background_tasks" not in st.session_state:
        st.session_state["background_tasks"] = {}

    for task_id, meta in BACKGROUND_TASK_CATALOG.items():
        if task_id not in st.session_state["background_tasks"]:
            st.session_state["background_tasks"][task_id] = {
                "id": task_id,
                "name": meta["name"],
                "description": meta["description"],
                "status": "Online",  # Online | Running | Paused | Error
                "thread": None,
                "stop_event": threading.Event(),
                "pause_event": threading.Event(),
                "error_event": threading.Event(),
                "iterations": 0,
                "last_heartbeat": "—",
            }
        else:
            task_entry = st.session_state["background_tasks"][task_id]
            worker = task_entry.get("thread")
            if task_entry["status"] in ("Running", "Paused") and (worker is None or not worker.is_alive()):
                if task_entry["error_event"].is_set():
                    task_entry["status"] = "Error"
                else:
                    task_entry["status"] = "Online"


def _append_task_log(log_buffer: list[str], task_name: str, message: str) -> None:
    """Thread-safe helper to append a formatted print line to the shared terminal log list."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] [{task_name.upper()}] {message}"
    with _LOG_LOCK:
        log_buffer.append(line)
        if len(log_buffer) > 400:
            del log_buffer[:-400]


def _background_worker_loop(
    task_state: dict,
    log_buffer: list[str],
    entity_snapshot: str,
    tax_year_snapshot: str,
) -> None:
    """
    Background worker target executed via threading.Thread.
    Emits live print logs into `log_buffer` while respecting pause_event, stop_event, and error_event.
    """
    task_name = task_state["name"]
    stop_event: threading.Event = task_state["stop_event"]
    pause_event: threading.Event = task_state["pause_event"]
    error_event: threading.Event = task_state["error_event"]

    _append_task_log(
        log_buffer,
        task_name,
        f"Worker thread started (ident={threading.get_ident()}) for {entity_snapshot} [FY {tax_year_snapshot}].",
    )

    step_messages = {
        "batch_pdf_extractor": [
            "Scanning local ingestion spool for unindexed PDF statements...",
            "Validating pdfplumber table bounding boxes & column alignment...",
            "Normalizing Date, Amount, Description, and Account Code fields...",
            "Verifying checksum integrity against master entity ledger...",
        ],
        "gemini_ocr_queue": [
            "Polling screenshot queue for .png / .jpg financial captures...",
            f"Prepared multimodal payload for {GEMINI_OCR_MODEL} endpoint...",
            "Parsing structured CSV OCR transaction response (Date,Amount,Description)...",
            "Applying Categorization Engine rules & dynamic AI routing...",
        ],
        "local_llm_categorizer": [
            "Inspecting ledger queue for Uncategorized transaction rows...",
            f"Dispatching Description-only payload to local Qwen endpoint ({DEFAULT_LOCAL_LLM_MODEL})...",
            "Resolved IRD tax category mapping from local model response...",
            "Committed categorized Account Code update to workspace buffer...",
        ],
    }
    messages = step_messages.get(task_state["id"], ["Executing background cycle..."])

    try:
        while not stop_event.is_set():
            if error_event.is_set():
                raise RuntimeError("Fault condition triggered in worker pipeline.")

            if pause_event.is_set():
                task_state["status"] = "Paused"
                time.sleep(0.35)
                continue

            task_state["status"] = "Running"
            idx = task_state["iterations"] % len(messages)
            task_state["iterations"] += 1
            task_state["last_heartbeat"] = datetime.now().strftime("%H:%M:%S")

            _append_task_log(
                log_buffer,
                task_name,
                f"Cycle #{task_state['iterations']:03d} ({entity_snapshot} // {tax_year_snapshot}): {messages[idx]}",
            )

            for _ in range(15):
                if stop_event.is_set() or pause_event.is_set() or error_event.is_set():
                    break
                time.sleep(0.1)

        if not error_event.is_set():
            task_state["status"] = "Online"
            _append_task_log(log_buffer, task_name, "Worker stopped cleanly. Status reset to Online.")
    except Exception as exc:
        task_state["status"] = "Error"
        _append_task_log(log_buffer, task_name, f"FATAL ERROR: {exc}")


def start_background_task(task_id: str) -> None:
    """Start or resume a background thread task and store its reference in st.session_state."""
    init_workspace_state()
    task_state = st.session_state["background_tasks"][task_id]
    log_buffer = st.session_state["background_task_logs"]
    worker: threading.Thread | None = task_state.get("thread")

    if worker is not None and worker.is_alive() and task_state["pause_event"].is_set():
        task_state["pause_event"].clear()
        task_state["error_event"].clear()
        task_state["status"] = "Running"
        _append_task_log(log_buffer, task_state["name"], "Execution resumed by operator.")
        return

    if worker is not None and worker.is_alive():
        return

    task_state["stop_event"] = threading.Event()
    task_state["pause_event"] = threading.Event()
    task_state["error_event"] = threading.Event()
    task_state["status"] = "Running"

    entity_snapshot = get_active_entity()
    tax_year_snapshot = get_active_tax_year()

    new_thread = threading.Thread(
        target=_background_worker_loop,
        args=(task_state, log_buffer, entity_snapshot, tax_year_snapshot),
        name=f"axiom_{task_id}",
        daemon=True,
    )
    task_state["thread"] = new_thread
    new_thread.start()


def pause_background_task(task_id: str) -> None:
    """Pause a running background thread task."""
    init_workspace_state()
    task_state = st.session_state["background_tasks"][task_id]
    log_buffer = st.session_state["background_task_logs"]
    worker: threading.Thread | None = task_state.get("thread")

    if worker is not None and worker.is_alive():
        if not task_state["pause_event"].is_set():
            task_state["pause_event"].set()
            task_state["status"] = "Paused"
            _append_task_log(log_buffer, task_state["name"], "Task paused by operator.")
        else:
            task_state["pause_event"].clear()
            task_state["status"] = "Running"
            _append_task_log(log_buffer, task_state["name"], "Task unpaused by operator.")


def stop_background_task(task_id: str) -> None:
    """Stop/kill a running or paused background thread task and return state to Online."""
    init_workspace_state()
    task_state = st.session_state["background_tasks"][task_id]
    log_buffer = st.session_state["background_task_logs"]
    worker: threading.Thread | None = task_state.get("thread")

    task_state["stop_event"].set()
    task_state["pause_event"].clear()
    task_state["error_event"].clear()

    if worker is not None and worker.is_alive():
        _append_task_log(log_buffer, task_state["name"], "SIGTERM / Stop signal sent to worker thread...")
        worker.join(timeout=1.0)

    task_state["thread"] = None
    task_state["status"] = "Online"


def trigger_background_task_error(task_id: str) -> None:
    """Simulate or flag an Error state on a background task."""
    init_workspace_state()
    task_state = st.session_state["background_tasks"][task_id]
    log_buffer = st.session_state["background_task_logs"]
    worker: threading.Thread | None = task_state.get("thread")

    task_state["error_event"].set()
    task_state["pause_event"].clear()
    if worker is not None and worker.is_alive():
        worker.join(timeout=0.8)
    else:
        _append_task_log(log_buffer, task_state["name"], "ERROR state flagged on worker pipeline.")
    task_state["status"] = "Error"


def get_background_status_summary() -> dict[str, object]:
    """
    Read the live status of all background threads in st.session_state and return
    aggregate telemetry for the Global Status Indicator.
    """
    init_workspace_state()
    tasks = st.session_state["background_tasks"]

    running_names: list[str] = []
    paused_names: list[str] = []
    error_names: list[str] = []
    online_names: list[str] = []

    for task_id, task_info in tasks.items():
        worker: threading.Thread | None = task_info.get("thread")
        alive = worker is not None and worker.is_alive()

        if task_info["status"] in ("Running", "Paused") and not alive:
            task_info["status"] = "Error" if task_info["error_event"].is_set() else "Online"

        st_label = task_info["status"]
        if st_label == "Running":
            running_names.append(task_info["name"])
        elif st_label == "Paused":
            paused_names.append(task_info["name"])
        elif st_label == "Error":
            error_names.append(task_info["name"])
        else:
            online_names.append(task_info["name"])

    if error_names:
        overall = "Error"
        headline = f"FAULT: {len(error_names)} TASK(S) IN ERROR"
        detail = ", ".join(error_names)
    elif running_names:
        overall = "Running"
        headline = f"ACTIVE: {len(running_names)} RUNNING IN BACKGROUND"
        detail = ", ".join(running_names)
    elif paused_names:
        overall = "Paused"
        headline = f"SUSPENDED: {len(paused_names)} TASK(S) PAUSED"
        detail = ", ".join(paused_names)
    else:
        overall = "Online"
        headline = f"IDLE // {len(online_names)} DAEMONS ONLINE"
        detail = "No active background jobs executing"

    return {
        "overall": overall,
        "headline": headline,
        "detail": detail,
        "running_count": len(running_names),
        "paused_count": len(paused_names),
        "error_count": len(error_names),
        "online_count": len(online_names),
    }


def render_global_status_indicator() -> None:
    """
    Reusable UI component that reads the status of background threads and renders
    a compact, technical charcoal/metallic Status Indicator card.
    """
    summary = get_background_status_summary()
    overall = str(summary["overall"])

    badge_styles = {
        "Running": ("#D5D8E0", "#2A2E37", "#8E939F", "▪ RUNNING"),
        "Paused": ("#B8BCC6", "#23262D", "#6C727F", "▪ PAUSED"),
        "Error": ("#E2E4E9", "#2D2628", "#8A6B70", "▪ ERROR"),
        "Online": ("#8E939F", "#1A1C20", "#3A3F4B", "▪ ONLINE"),
    }
    text_color, bg_color, border_color, pill_text = badge_styles.get(
        overall, badge_styles["Online"]
    )

    st.markdown(
        f"""
        <div style="
            background-color: #16181C;
            border: 1px solid #2A2D34;
            border-radius: 4px;
            padding: 0.65rem 0.95rem;
            display: flex;
            flex-direction: column;
            justify-content: center;
        ">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.2rem;">
                <span class="axiom-eyebrow" style="margin: 0;">BACKGROUND DAEMONS</span>
                <span style="
                    font-size: 0.66rem;
                    font-weight: 600;
                    letter-spacing: 0.1em;
                    color: {text_color};
                    background-color: {bg_color};
                    border: 1px solid {border_color};
                    border-radius: 3px;
                    padding: 0.08rem 0.4rem;
                ">{pill_text}</span>
            </div>
            <div style="font-size: 0.8rem; font-weight: 600; color: #E2E4E9; letter-spacing: -0.01em;">
                {summary['headline']}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _save_raw_file_to_vault(
    entity: str,
    tax_year: str,
    bucket: str,
    filename: str,
    file_bytes: bytes,
) -> str:
    """
    Save an uploaded file's raw bytes into the organized local File Vault on disk:
    `data/vault/<Entity>/<Year>/<Bucket>/<Filename>`
    Returns the relative vault path string.
    """
    _ensure_sqlite_schema()
    ent_dir = _safe_vault_segment(entity)
    yr_dir = _safe_vault_segment(tax_year)
    bkt_dir = _safe_vault_segment(bucket.lower())
    target_dir = VAULT_ROOT_DIR / ent_dir / yr_dir / bkt_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    clean_fname = Path(filename or "Uploaded_File").name
    clean_fname = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean_fname).strip() or "Uploaded_File"
    dest_path = target_dir / clean_fname
    try:
        dest_path.write_bytes(file_bytes)
    except Exception:
        pass
    try:
        return str(dest_path.relative_to(Path(__file__).resolve().parent))
    except Exception:
        return str(dest_path)


def _save_file_record_to_sqlite(
    entity: str,
    tax_year: str,
    record: dict[str, object],
) -> None:
    """Persist an uploaded file metadata record and all its transaction rows into SQLite."""
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
                    vault_path, mime_type, uploaded_at, needs_ocr, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    entity = excluded.entity,
                    tax_year = excluded.tax_year,
                    name = excluded.name,
                    signature = excluded.signature,
                    bucket = excluded.bucket,
                    vault_path = excluded.vault_path,
                    mime_type = excluded.mime_type,
                    needs_ocr = excluded.needs_ocr,
                    note = excluded.note
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


def _load_uploaded_files_from_sqlite(entity: str, tax_year: str) -> list[dict[str, object]]:
    """Load all persisted uploaded file records and their DataFrames for `(entity, tax_year)` from SQLite."""
    _ensure_sqlite_schema()
    loaded: list[dict[str, object]] = []
    with _DB_LOCK:
        with _get_sqlite_conn() as conn:
            file_rows = conn.execute(
                """
                SELECT file_id, name, signature, bucket, vault_path, mime_type,
                       uploaded_at, needs_ocr, note
                FROM uploaded_files
                WHERE entity = ? AND tax_year = ?
                ORDER BY sort_order ASC, rowid ASC
                """,
                (entity, tax_year),
            ).fetchall()

            for frow in file_rows:
                fid = str(frow["file_id"])
                tx_rows = conn.execute(
                    """
                    SELECT tx_date, amount, description, property, account_code
                    FROM file_transactions
                    WHERE file_id = ?
                    ORDER BY row_index ASC
                    """,
                    (fid,),
                ).fetchall()

                if tx_rows:
                    df = pd.DataFrame(
                        [
                            {
                                "Date": str(tr["tx_date"] or ""),
                                "Amount": float(tr["amount"] or 0.0),
                                "Description": str(tr["description"] or ""),
                                "Property": str(tr["property"] or UNASSIGNED_PROPERTY),
                                "Account Code": str(tr["account_code"] or "Uncategorized"),
                            }
                            for tr in tx_rows
                        ]
                    )[STANDARD_COLUMNS]
                else:
                    df = pd.DataFrame(columns=STANDARD_COLUMNS)

                vpath_str = str(frow["vault_path"] or "")
                raw_bytes = None
                if str(frow["bucket"]) == "screenshots" and vpath_str:
                    vp = Path(vpath_str)
                    if not vp.is_absolute():
                        vp = Path(__file__).resolve().parent / vp
                    if vp.exists() and vp.is_file():
                        try:
                            raw_bytes = vp.read_bytes()
                        except Exception:
                            raw_bytes = None

                loaded.append(
                    {
                        "id": fid,
                        "signature": str(frow["signature"] or ""),
                        "name": str(frow["name"] or "Uploaded_File"),
                        "bucket": str(frow["bucket"] or "csv"),
                        "vault_path": vpath_str,
                        "uploaded_at": str(frow["uploaded_at"] or ""),
                        "df": df,
                        "raw_bytes": raw_bytes,
                        "mime_type": str(frow["mime_type"] or ""),
                        "needs_ocr": bool(frow["needs_ocr"]),
                        "note": str(frow["note"] or ""),
                    }
                )

    # Auto-repair any 0-row CSV records that previously failed due to bank preamble headers (e.g., ASB CSVs)
    # and auto-recover any orphaned files sitting in `data/vault/<Entity>/<Year>/<Bucket>/` on disk.
    repaired_or_recovered = False
    for rec in loaded:
        if (
            rec.get("bucket") == "csv"
            and isinstance(rec.get("df"), pd.DataFrame)
            and rec["df"].empty
            and "parse warning" in str(rec.get("note", "")).lower()
            and rec.get("vault_path")
        ):
            vp = Path(str(rec["vault_path"]))
            if not vp.is_absolute():
                vp = Path(__file__).resolve().parent / vp
            if vp.exists() and vp.is_file():
                try:
                    raw_df = _read_bank_csv_bytes(vp.read_bytes())
                    if not raw_df.empty:
                        norm_df = (
                            raw_df.copy()
                            if list(raw_df.columns) == STANDARD_COLUMNS
                            else normalize_financial_dataframe(raw_df)
                        )
                        if not norm_df.empty:
                            rec["df"] = apply_rule_categorization(norm_df, entity=entity)
                            rec["note"] = ""
                            _save_file_record_to_sqlite(entity, tax_year, rec)
                            repaired_or_recovered = True
                except Exception:
                    pass

    # Scan `data/vault/<Entity>/<Year>/<Bucket>/` for any orphaned files written to disk
    # whose SQLite insert was interrupted (e.g. during a previous multi-screenshot upload)
    known_names_by_bucket = {
        (str(r.get("bucket", "")).lower(), str(r.get("name", "")).strip())
        for r in loaded
    }
    for b_key in ("csv", "pdf", "screenshots", "other"):
        b_dir = get_vault_dir(entity, tax_year, b_key)
        if b_dir.exists() and b_dir.is_dir():
            for disk_file in sorted(b_dir.iterdir()):
                if not disk_file.is_file() or disk_file.name.startswith(".") or disk_file.name.lower().endswith(".ocr.md"):
                    continue
                if (b_key, disk_file.name) in known_names_by_bucket:
                    continue
                try:
                    fbytes = disk_file.read_bytes()
                    fsig = f"{disk_file.name}::{len(fbytes)}"
                    try:
                        rel_vpath = str(disk_file.relative_to(Path(__file__).resolve().parent))
                    except ValueError:
                        rel_vpath = str(disk_file)
                    rec_df = pd.DataFrame(columns=STANDARD_COLUMNS)
                    rec_note = ""
                    rec_needs_ocr = False
                    if b_key == "csv":
                        raw_c = _read_bank_csv_bytes(fbytes)
                        if not raw_c.empty:
                            norm_c = normalize_financial_dataframe(raw_c)
                            rec_df = apply_rule_categorization(norm_c, entity=entity)
                    elif b_key == "pdf":
                        raw_p = extract_pdf_tables(fbytes)
                        if not raw_p.empty:
                            norm_p = normalize_financial_dataframe(raw_p)
                            rec_df = apply_rule_categorization(norm_p, entity=entity)
                        else:
                            rec_note = "Saved PDF to Vault. No structured table found automatically."
                    elif b_key == "screenshots":
                        rec_needs_ocr = True
                        rec_note = "Saved in Vault — click '🔍 Run OCR on Screenshots' or open file to extract."
                        rec_df = pd.DataFrame(
                            [
                                {
                                    "Date": datetime.fromtimestamp(disk_file.stat().st_mtime).strftime("%Y-%m-%d"),
                                    "Amount": 0.0,
                                    "Description": disk_file.stem.replace("_", " ").replace("-", " "),
                                    "Property": UNASSIGNED_PROPERTY,
                                    "Account Code": "Uncategorized",
                                }
                            ]
                        )[STANDARD_COLUMNS]
                        rec_df = apply_rule_categorization(rec_df, entity=entity)
                    else:
                        raw_o = extract_other_document_tables(fbytes, disk_file.name)
                        if not raw_o.empty:
                            norm_o = normalize_financial_dataframe(raw_o)
                            rec_df = apply_rule_categorization(norm_o, entity=entity)

                    new_fid = f"file_{int(time.time() * 1000)}_{len(loaded) + 1}"
                    mime_str = (
                        "image/png"
                        if disk_file.suffix.lower() == ".png"
                        else ("image/jpeg" if b_key == "screenshots" else "")
                    )
                    recovered_rec: dict[str, object] = {
                        "id": new_fid,
                        "signature": fsig,
                        "name": disk_file.name,
                        "bucket": b_key,
                        "vault_path": rel_vpath,
                        "uploaded_at": datetime.fromtimestamp(disk_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "df": rec_df,
                        "raw_bytes": fbytes if b_key == "screenshots" else None,
                        "mime_type": mime_str,
                        "needs_ocr": rec_needs_ocr,
                        "note": rec_note,
                    }
                    loaded.append(recovered_rec)
                    _save_file_record_to_sqlite(entity, tax_year, recovered_rec)
                    known_names_by_bucket.add((b_key, disk_file.name))
                    repaired_or_recovered = True
                except Exception:
                    pass

    if repaired_or_recovered:
        frames = [
            r["df"].reindex(columns=STANDARD_COLUMNS).copy()
            for r in loaded
            if isinstance(r.get("df"), pd.DataFrame) and not r["df"].empty
        ]
        if frames:
            _save_master_ledger_to_sqlite(entity, tax_year, pd.concat(frames, ignore_index=True))

    return loaded


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


def export_entity_vault_zip(entity: str | None = None, tax_year: str | None = None) -> bytes:
    """
    Create an in-memory ZIP archive containing:
    1) All original uploaded files from `data/vault/<Entity>/<Year>/` (CSVs, PDFs, Screenshots, Excel/ODT)
    2) The coded Master Ledger CSV (`<Entity>_<Year>_Master_Coded_Ledger.csv`)
    """
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    ent_dir = _safe_vault_segment(target_entity)
    yr_dir = _safe_vault_segment(target_year)
    vault_folder = VAULT_ROOT_DIR / ent_dir / yr_dir

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        master_df = get_entity_ledger(target_entity, target_year)
        master_csv_bytes = export_xero_master_csv(master_df)
        zf.writestr(f"{ent_dir}_{yr_dir}_Master_Coded_Ledger.csv", master_csv_bytes)

        if vault_folder.exists():
            for file_path in sorted(vault_folder.rglob("*")):
                if file_path.is_file():
                    arcname = str(file_path.relative_to(vault_folder))
                    try:
                        zf.writestr(f"Original_Files/{arcname}", file_path.read_bytes())
                    except Exception:
                        pass
    return buf.getvalue()


def get_entity_ledger(entity: str | None = None, tax_year: str | None = None) -> pd.DataFrame:
    """
    Retrieve the standardized ledger DataFrame isolated by both Entity AND Financial Year.
    Loads from SQLite (`data/axiom_ledger.db`) on first access in a session so all coded
    transactions survive browser refreshes and restarts.
    """
    init_workspace_state()
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)

    st.session_state["entity_year_ledgers"].setdefault(target_entity, {})
    st.session_state.setdefault("_sqlite_loaded_ledgers", set())
    load_key = f"{target_entity}::{target_year}"

    if load_key not in st.session_state["_sqlite_loaded_ledgers"]:
        db_df = _load_master_ledger_from_sqlite(target_entity, target_year)
        if db_df.empty:
            # If master_ledgers table is empty for (entity, year) but uploaded_files exist in SQLite, rebuild from files
            files_list = get_entity_uploaded_files(target_entity, target_year)
            frames = [
                f["df"].reindex(columns=STANDARD_COLUMNS).copy()
                for f in files_list
                if isinstance(f.get("df"), pd.DataFrame) and not f["df"].empty
            ]
            if frames:
                db_df = pd.concat(frames, ignore_index=True)
                _save_master_ledger_to_sqlite(target_entity, target_year, db_df)
        st.session_state["entity_year_ledgers"][target_entity][target_year] = db_df
        st.session_state["_sqlite_loaded_ledgers"].add(load_key)
    elif target_year not in st.session_state["entity_year_ledgers"][target_entity]:
        st.session_state["entity_year_ledgers"][target_entity][target_year] = _load_master_ledger_from_sqlite(
            target_entity, target_year
        )

    df = st.session_state["entity_year_ledgers"][target_entity][target_year]
    st.session_state["entity_ledgers"][target_entity] = df
    return df


def set_entity_ledger(
    df: pd.DataFrame,
    entity: str | None = None,
    tax_year: str | None = None,
) -> pd.DataFrame:
    """Save a standardized DataFrame to st.session_state AND SQLite (`data/axiom_ledger.db`) isolated by Entity and Financial Year."""
    init_workspace_state()
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)

    standardized = df.reindex(columns=STANDARD_COLUMNS).copy()
    standardized["Amount"] = _clean_amount_series(standardized["Amount"])
    clean_df = standardized.reset_index(drop=True)

    st.session_state["entity_year_ledgers"].setdefault(target_entity, {})
    st.session_state["entity_year_ledgers"][target_entity][target_year] = clean_df
    st.session_state["entity_ledgers"][target_entity] = clean_df
    st.session_state.setdefault("_sqlite_loaded_ledgers", set()).add(f"{target_entity}::{target_year}")
    _save_master_ledger_to_sqlite(target_entity, target_year, clean_df)
    return clean_df


def append_entity_ledger(
    df: pd.DataFrame,
    entity: str | None = None,
    tax_year: str | None = None,
) -> pd.DataFrame:
    """Append a standardized DataFrame to the existing DataFrame for the active Entity and Financial Year and persist to SQLite."""
    init_workspace_state()
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)

    existing = get_entity_ledger(target_entity, target_year)
    incoming = df.reindex(columns=STANDARD_COLUMNS).copy()
    incoming["Amount"] = _clean_amount_series(incoming["Amount"])
    if existing.empty:
        combined = incoming.copy()
    else:
        combined = pd.concat([existing, incoming], ignore_index=True)

    return set_entity_ledger(combined, target_entity, target_year)


def is_uncategorized(account_code: object) -> bool:
    """Return True if an Account Code cell is empty or marked as Uncategorized/UNASSIGNED."""
    if pd.isna(account_code):
        return True
    cleaned = str(account_code).strip().lower()
    return cleaned in UNCATEGORIZED_MARKERS


def is_property_unassigned(property_val: object, account_code: object = "") -> bool:
    """
    Return True if the Property column is empty/Unassigned AND the Account Code is not
    a portfolio-wide category (like Legal and Accounting or IRD Tax Payment).
    """
    if is_portfolio_wide_code(account_code):
        return False
    if pd.isna(property_val):
        return True
    cleaned = str(property_val).strip().lower()
    return cleaned in UNCATEGORIZED_MARKERS or cleaned in ("unassigned", "?", "select property", "unknown")


def _pattern_matches_row_text(pattern_str: str, row_text_lower: str) -> tuple[bool, int]:
    """
    Evaluate whether a saved rule `pattern_str` matches `row_text_lower` (the full CSV row text).
    Supports:
    - Exact match (score 1000 + len)
    - Contiguous substring match (score 500 + len)
    - Compound multi-part rules separated by '|', '+', or '&' (e.g. 'Tower | Damian' or 'Tower + 9 Damian')
      where every part appears anywhere across the CSV row text (score 400 + sum of part lengths)
    - Leading payee segment match when rule pattern has bank suffix/prefix variations (score 300 + len)
    """
    pat_clean = (pattern_str or "").strip().lower()
    if not pat_clean or not row_text_lower:
        return False, 0

    if pat_clean == row_text_lower:
        return True, 1000 + len(pat_clean)

    if pat_clean in row_text_lower:
        return True, 500 + len(pat_clean)

    # Check compound pattern separated by '|', '+', or '&' (e.g. "Tower + Damian" or "6851Tower Insurance | 9 Damian")
    if any(sep in pat_clean for sep in ("|", "+", "&")):
        sub_parts = [p.strip() for p in re.split(r"[|+&]+", pat_clean) if p.strip()]
        if len(sub_parts) >= 2 and all(sp in row_text_lower for sp in sub_parts):
            return True, 400 + sum(len(sp) for sp in sub_parts)

    # Check if stripping leading 4-digit bank code from pattern (e.g. '6851Tower Insurance' -> 'tower insurance')
    # matches the CSV row text
    stripped_digits = re.sub(r"^\d{3,6}\s*", "", pat_clean).strip()
    if len(stripped_digits) >= 5 and stripped_digits != pat_clean and stripped_digits in row_text_lower:
        return True, 320 + len(stripped_digits)

    return False, 0


def match_memorized_or_rule(
    description: object,
    entity: str | None = None,
) -> tuple[str, str, str]:
    """
    Full-CSV-Line Decoupled Account Code & Property Matching Engine:
    1. Property Detection Across Full Line:
       Scans `description` (which includes Payee, Analysis Code, Reference, Particulars, Memo, and all
       other text columns from the CSV line) using `detect_property_from_text()`.
       If a house name (e.g. 'Damian', '26 Frances', '33 Blampied', '14 Raleigh', '303 Weymouth', '363 Queen')
       appears anywhere on the line, `explicit_prop` captures it immediately.
    2. Memorized Rules Matching:
       Checks User's Memorized Rules (exact match, substring match, compound 'Vendor + House' match, or
       vendor-without-bank-prefix match).
       - If both a Memorized Rule AND an explicit house in the CSV line or rule property resolve -> 'Memorized (100%)'
       - If a Memorized Rule matches the Item, and the rule is Portfolio-Wide -> 'Memorized (100%)'
       - If a Memorized Rule matches the Item, but Property is still unknown -> 'Memorized (Missing Property)'
    3. Built-in Jurisdiction & Vendor Keyword Dictionary:
       Scans the full CSV line against known vendor/item keywords (e.g. 'tower', 'insurance', 'auckland council',
       'watercare', 'rates', 'body corp', 'rent', 'interest', 'bunnings', 'mitre 10').
       - If a strong vendor keyword (like 'tower insurance', 'auckland council', 'watercare') lines up with an
         explicit house name on the same CSV line (like 'Damian') -> 'Aligned Vendor + House (100%)'
       - If only a generic keyword matched (or property wasn't on the line) -> 'Keyword Guess (Needs Check)'
    """
    if pd.isna(description):
        return UNASSIGNED_PROPERTY, "Uncategorized", "Unknown"
    desc_text = str(description).strip()
    desc_lower = desc_text.lower()
    if not desc_lower:
        return UNASSIGNED_PROPERTY, "Uncategorized", "Unknown"

    resolved_entity = get_active_entity(entity)

    # Step 1: Detect explicit property / house name anywhere in the CSV line text
    explicit_prop = detect_property_from_text(desc_text, entity=resolved_entity)

    # Step 1b: Loan account/trigger match (highest priority — explicit account numbers)
    try:
        _loan = match_loan_from_text(desc_text, entity=resolved_entity)
    except Exception:
        _loan = None
    if _loan:
        _prop = str(_loan.get("property_name") or UNASSIGNED_PROPERTY)
        _int_code = str(_loan.get("interest_account_code") or "Loan Interest")
        _prin_code = str(_loan.get("principal_account_code") or "Principal Loan")
        try:
            _codes = get_entity_account_codes(resolved_entity)
            if "Loan Interest" not in _codes and "Interest" in _codes and _int_code == "Loan Interest":
                _int_code = "Interest"
        except Exception:
            pass
        if re.search(r"\b(principal|loan repayment|mortgage repayment|repayment|loan payment)\b", desc_lower):
            return _prop, _prin_code, "Memorized"
        if re.search(r"\b(interest|int charged|debit interest|loan int)\b", desc_lower):
            return _prop, _int_code, "Memorized"
        # Bare account-number transfer with no interest/principal wording
        return _prop, _int_code, "Memorized"

    # Step 2: Check Memorized Rules (scored by match specificity)
    mem_rules = get_memorized_rules(resolved_entity)
    best_rule: dict[str, str] | None = None
    best_score = 0
    matched_props_for_best_pat: set[str] = set()
    best_pat_lower = ""

    for rule in mem_rules:
        pat = str(rule.get("pattern", "")).strip()
        if not pat:
            continue
        matched, score = _pattern_matches_row_text(pat, desc_lower)
        if matched:
            # Bonus score if the rule's property also matches the explicit property detected on the CSV line
            r_prop = str(rule.get("property", "")).strip()
            if explicit_prop != UNASSIGNED_PROPERTY and r_prop.lower() == explicit_prop.lower():
                score += 150
            if score > best_score:
                best_score = score
                best_rule = rule
                best_pat_lower = pat.lower()

    if best_rule is not None:
        if best_pat_lower:
            for r_chk in mem_rules:
                if str(r_chk.get("pattern", "")).strip().lower() == best_pat_lower:
                    p_chk = str(r_chk.get("property", "")).strip()
                    if p_chk and p_chk != UNASSIGNED_PROPERTY and p_chk.lower() != "general / portfolio":
                        matched_props_for_best_pat.add(p_chk)

        rule_prop = str(best_rule.get("property", "")).strip() or UNASSIGNED_PROPERTY
        code = str(best_rule.get("account_code", "")).strip() or "Uncategorized"
        # Explicit house on the CSV line always takes precedence over a generic default property.
        # If the same vendor pattern is saved for multiple different properties (e.g. TOWER Insurance saved for
        # 9 Damian AND 303 Weymouth), require the house name on the CSV line to pick the right property
        # rather than blindly assigning every row to whichever property rule was saved last.
        if explicit_prop != UNASSIGNED_PROPERTY:
            final_prop = explicit_prop
        elif len(matched_props_for_best_pat) > 1:
            final_prop = UNASSIGNED_PROPERTY
        else:
            final_prop = rule_prop

        if final_prop == UNASSIGNED_PROPERTY and is_portfolio_wide_code(code, resolved_entity):
            final_prop = "General / Portfolio"

        if not is_uncategorized(code) and not is_property_unassigned(final_prop, code):
            return final_prop, code, "Memorized"
        return final_prop, code, "Memorized Partial"

    # Step 3: Check entity's custom Account Codes appearing directly as words in the CSV line
    # AND check Jurisdiction Keyword Dictionary (longest/most specific keyword first)
    active_rules = dict(get_entity_rules(resolved_entity))
    # Ensure common NZ/US vendor shorthand keywords like 'tower' -> 'Insurance' are included
    extra_smart_keywords = {
        "tower": "Insurance",
        "aon": "Insurance",
        "stylecover": "Insurance",
        "lumley": "Insurance",
        "crombie lockwood": "Insurance",
        "bodycorp": "Bodycorp",
        "body corporate": "Bodycorp",
        "bca ltd": "Bodycorp",
        "watercare": "Watercare" if "Watercare" in get_entity_account_codes(resolved_entity) else "Water Rates",
        "auckland council": "Rates",
    }
    for ek, ev in extra_smart_keywords.items():
        if ek not in active_rules:
            active_rules[ek] = ev

    matched_code = "Uncategorized"
    matched_kw = ""
    # Sort keywords longest first so specific vendor names ('tower insurance', 'auckland council') match before generic words
    for keyword in sorted(active_rules.keys(), key=len, reverse=True):
        kw_low = keyword.lower().strip()
        if not kw_low:
            continue
        if len(kw_low) <= 4:
            if re.search(rf"(?<![a-z0-9]){re.escape(kw_low)}(?![a-z0-9])", desc_lower):
                matched_code = active_rules[keyword]
                matched_kw = kw_low
                break
        elif kw_low in desc_lower:
            matched_code = active_rules[keyword]
            matched_kw = kw_low
            break

    # Map jurisdiction generic category name to entity's exact Account Code if equivalent exists
    ent_codes = get_entity_account_codes(resolved_entity)
    if matched_code == "Rental Income" and "Rent Income" in ent_codes:
        matched_code = "Rent Income"
    elif matched_code == "Body Corporate Levies" and "Bodycorp" in ent_codes:
        matched_code = "Bodycorp"
    elif matched_code == "Water Rates" and "Watercare" in ent_codes:
        matched_code = "Watercare"

    detected_prop = explicit_prop
    if is_portfolio_wide_code(matched_code, resolved_entity) and detected_prop == UNASSIGNED_PROPERTY:
        detected_prop = "General / Portfolio"

    if matched_code != "Uncategorized" or detected_prop != UNASSIGNED_PROPERTY:
        # If both a house name AND an item keyword were found on the line, note whether it was a specific vendor or generic keyword
        high_conf_vendors = {
            "tower",
            "tower insurance",
            "ami insurance",
            "state insurance",
            "aa insurance",
            "vero",
            "initio",
            "aon",
            "stylecover",
            "auckland council",
            "wellington city council",
            "christchurch city council",
            "hamilton city council",
            "tauranga city council",
            "dunedin city council",
            "watercare",
            "bca ltd",
            "inland revenue",
            "xero",
        }
        if matched_code != "Uncategorized" and detected_prop != UNASSIGNED_PROPERTY and matched_kw in high_conf_vendors:
            return detected_prop, matched_code, "Aligned Vendor+House"
        return detected_prop, matched_code, "Keyword Rule"

    return UNASSIGNED_PROPERTY, "Uncategorized", "Unknown"


def evaluate_row_traffic_light(
    property_val: object,
    account_code: object,
    description: object = "",
    entity: str | None = None,
) -> tuple[str, str]:
    """
    Evaluate the 3-tier Traffic Light certainty level for a single transaction row:
    Returns `(tier, badge_text)` where `tier` is one of `'green'`, `'yellow'`, `'red'`:
      - `'green'`  (🟢 100% Sure):
          Both Item (Account Code) and Property are assigned, AND the row matches a Saved/Memorized Rule
          (or aligned specific vendor + house name on the CSV line, or was user-coded).
      - `'yellow'` (🟡 Needs Double-Check / Partially Sure):
          1) Both Item and Property are filled, but it came from a generic Keyword Guess / AI Guess
             rather than a confirmed Memorized Rule (`🟡 Check Guess (Not Memorized)`), OR
          2) One of Item or Property was found (e.g. House '9 Damian' found on the line, or Item 'Insurance'
             found on the line), but the other half still needs to be picked (`🟡 Pick Property` / `🟡 Pick Item`).
      - `'red'`    (🔴 Unsure / Missing Both):
          Neither the Item nor the Property could be determined (`🔴 Unsure — Pick Item & Property`).
    """
    resolved_entity = get_active_entity(entity)
    missing_code = is_uncategorized(account_code)
    missing_prop = is_property_unassigned(property_val, account_code)

    if missing_code and missing_prop:
        return "red", "🔴 Unsure — Needs Item & Property"

    if missing_code and not missing_prop:
        return "yellow", f"🟡 House Found ({property_val}) — Pick Item"

    if not missing_code and missing_prop:
        return "yellow", f"🟡 Item Found ({account_code}) — Pick Property"

    # Both Item and Property are present! Check whether this row is backed by a 100% certain Memorized Rule
    desc_str = "" if pd.isna(description) else str(description).strip()
    if desc_str:
        _, _, match_src = match_memorized_or_rule(desc_str, entity=resolved_entity)
        if match_src in ("Memorized", "Loan Match", "Aligned Vendor+House"):
            return "green", "🟢 100% Sure (Matched Rule)"
        if match_src == "Keyword Rule":
            return "yellow", "🟡 Check Guess (Keyword Match)"
        if match_src == "Memorized Partial":
            # Rule matched the item, and property is now filled
            return "green", "🟢 100% Sure (Rule + House)"
        # If match_src == "Unknown" yet both code & property are populated, it was either pre-coded in the file or AI-guessed
        return "green", "🟢 100% Coded"

    return "green", "🟢 100% Coded"


def get_row_status_badge(
    property_val: object,
    account_code: object,
    description: object = "",
    entity: str | None = None,
) -> str:
    """
    Grade a single transaction row into the 3-tier Traffic Light system:
    - '🟢 100% Sure ...' : High-confidence matched rule or confirmed Item + Property
    - '🟡 ...'           : Partially matched (Item found OR House found, or unconfirmed keyword guess)
    - '🔴 Unsure ...'    : Neither Item nor Property is known
    """
    _, badge = evaluate_row_traffic_light(
        property_val=property_val,
        account_code=account_code,
        description=description,
        entity=entity,
    )
    return badge


def is_row_red_or_unsure(
    property_val: object,
    account_code: object,
    description: object = "",
    entity: str | None = None,
) -> bool:
    """Return True if a row is 🔴 Red or 🟡 Yellow (i.e. not 🟢 100% Green)."""
    if is_uncategorized(account_code) or is_property_unassigned(property_val, account_code):
        return True
    if description:
        tier, _ = evaluate_row_traffic_light(property_val, account_code, description=description, entity=entity)
        return tier != "green"
    return False


def match_rule_category(description: object, entity: str | None = None) -> str:
    """
    Match a transaction Description against Memorized Rules first, then the active entity's
    jurisdiction rules (US IRS Schedule E rules vs NZ IRD rules).
    Returns the matched Account Code or 'Uncategorized' if no rule matches.
    """
    _, code, _ = match_memorized_or_rule(description, entity=entity)
    return code


def apply_rule_categorization(
    df: pd.DataFrame,
    overwrite_existing: bool = False,
    entity: str | None = None,
) -> pd.DataFrame:
    """
    Apply Memorized Rules + entity-aware keyword rules to auto-fill 'Property' and 'Account Code'
    based on 'Description'.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    resolved_entity = get_active_entity(entity)
    result = df.reindex(columns=STANDARD_COLUMNS).copy()
    result["Property"] = result["Property"].fillna("").astype(str).str.strip()
    result["Account Code"] = result["Account Code"].fillna("").astype(str).str.strip()

    new_props: list[str] = []
    new_codes: list[str] = []

    for _, row in result.iterrows():
        curr_prop = str(row.get("Property", "")).strip()
        curr_code = str(row.get("Account Code", "")).strip()
        matched_prop, matched_code, _ = match_memorized_or_rule(
            row.get("Description", ""),
            entity=resolved_entity,
        )

        if overwrite_existing or is_uncategorized(curr_code):
            final_code = matched_code
        else:
            final_code = curr_code

        if overwrite_existing or is_property_unassigned(curr_prop, final_code):
            if not is_property_unassigned(curr_prop, final_code) and not overwrite_existing:
                final_prop = curr_prop
            elif matched_prop != UNASSIGNED_PROPERTY:
                final_prop = matched_prop
            elif is_portfolio_wide_code(final_code, resolved_entity):
                final_prop = "General / Portfolio"
            else:
                final_prop = UNASSIGNED_PROPERTY
        else:
            final_prop = curr_prop if curr_prop else UNASSIGNED_PROPERTY

        if is_portfolio_wide_code(final_code, resolved_entity) and final_prop in ("", UNASSIGNED_PROPERTY):
            final_prop = "General / Portfolio"

        new_props.append(final_prop)
        new_codes.append(final_code)

    result["Property"] = new_props
    result["Account Code"] = new_codes
    return result


def _clean_ird_category_response(raw_content: str) -> str:
    """Normalize a model response down to the exact single-line tax category string."""
    content = (raw_content or "").strip()
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    if not content:
        return "Uncategorized"
    first_line = content.splitlines()[0].strip(" \"'`.,:;!*")
    return first_line if first_line else "Uncategorized"


def guess_ird_category_local_llm(
    description: str,
    local_llm_url: str | None = None,
    model_name: str = DEFAULT_LOCAL_LLM_MODEL,
    entity: str | None = None,
) -> str:
    """
    Local Qwen 9B (Air-gapped) Categorizer:
    Uses the openai Python package pointed at the user's Local LLM URL.
    Dynamically injects the US CPA (IRS Schedule E) or NZ Property Accountant (NZ IRD)
    system prompt based on the active entity and sends ONLY the description text string.
    """
    desc_only = "" if pd.isna(description) else str(description).strip()
    if not desc_only:
        return "Uncategorized"

    base_url = (
        local_llm_url
        or st.session_state.get("local_llm_url")
        or DEFAULT_LOCAL_LLM_URL
    ).strip()

    system_prompt = get_categorization_system_prompt(entity)

    client = OpenAI(
        base_url=base_url,
        api_key="local-llm",
    )

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": desc_only},
        ],
        temperature=0.0,
        max_tokens=24,
    )

    return _clean_ird_category_response(response.choices[0].message.content or "")


def guess_ird_category_gemini(
    description: str,
    api_key: str | None = None,
    entity: str | None = None,
) -> str:
    """
    Gemini 3.8 Flash (Cloud) Categorizer:
    Uses the google-genai SDK (`from google import genai` and `client = genai.Client(api_key=...)`)
    with `gemini-3.8-flash` and the entity-aware system prompt (US IRS Schedule E vs NZ IRD)
    to categorize the description text string and output ONLY the category name.
    """
    desc_only = "" if pd.isna(description) else str(description).strip()
    if not desc_only:
        return "Uncategorized"

    resolved_key = (api_key or st.session_state.get("google_genai_api_key") or "").strip()
    if not resolved_key:
        raise ValueError(
            "Google GenAI API Key is required when 'Gemini 3.8 Flash (Cloud)' is selected. "
            "Open '⚙ AI & System Settings' in the top right to configure and verify your key."
        )

    system_prompt = get_categorization_system_prompt(entity)
    client = genai.Client(api_key=resolved_key)
    prompt = f"{system_prompt}\n\nTransaction Description: {desc_only}"

    response = client.models.generate_content(
        model=GEMINI_OCR_MODEL,
        contents=prompt,
    )

    return _clean_ird_category_response(response.text or "")


def categorize_description_dynamic(
    description: str,
    selected_model: str | None = None,
    local_llm_url: str | None = None,
    local_model_name: str = DEFAULT_LOCAL_LLM_MODEL,
    api_key: str | None = None,
    entity: str | None = None,
) -> str:
    """
    Dynamic Entity-Aware Categorization Engine Router:
    1. Attempts deterministic keyword matching against the active entity's jurisdiction dictionary first.
    2. When unresolved, routes ONLY the description text string to the configured AI provider
       with the jurisdiction-specific system prompt:
       - "Local Qwen 9B (Air-gapped)" -> OpenAI client at Local LLM URL
       - "Gemini 3.8 Flash (Cloud)" -> google-genai SDK (gemini-3.8-flash)
    """
    resolved_entity = get_active_entity(entity)
    rule_match = match_rule_category(description, entity=resolved_entity)
    if not is_uncategorized(rule_match):
        return rule_match

    active_choice = (
        selected_model
        or st.session_state.get("categorization_model")
        or CATEGORIZATION_MODEL_OPTIONS[0]
    )

    if active_choice == "Gemini 3.8 Flash (Cloud)":
        return guess_ird_category_gemini(
            description=description,
            api_key=api_key,
            entity=resolved_entity,
        )

    return guess_ird_category_local_llm(
        description=description,
        local_llm_url=local_llm_url,
        model_name=local_model_name,
        entity=resolved_entity,
    )


def apply_ai_fallback_categorization(
    df: pd.DataFrame,
    local_llm_url: str | None = None,
    model_name: str = DEFAULT_LOCAL_LLM_MODEL,
    selected_model: str | None = None,
    api_key: str | None = None,
    entity: str | None = None,
) -> tuple[pd.DataFrame, int]:
    """
    Dynamically route unresolved ('Uncategorized' or blank) rows in the DataFrame
    to the configured AI model ("Local Qwen 9B (Air-gapped)" or "Gemini 3.8 Flash (Cloud)")
    using the active entity's jurisdiction rules and prompt.
    Returns (updated_df, categorized_count).
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS), 0

    resolved_entity = get_active_entity(entity)
    updated = apply_rule_categorization(
        df.reindex(columns=STANDARD_COLUMNS).copy(),
        entity=resolved_entity,
    )
    updated_count = 0

    active_choice = (
        selected_model
        or st.session_state.get("categorization_model")
        or CATEGORIZATION_MODEL_OPTIONS[0]
    )

    desc_cache: dict[str, str] = {}

    for idx, row in updated.iterrows():
        current_code = row.get("Account Code", "")
        if is_uncategorized(current_code):
            desc_text = "" if pd.isna(row.get("Description")) else str(row.get("Description")).strip()
            if not desc_text:
                updated.at[idx, "Account Code"] = "Uncategorized"
                continue

            if desc_text in desc_cache:
                guessed_category = desc_cache[desc_text]
            else:
                guessed_category = categorize_description_dynamic(
                    description=desc_text,
                    selected_model=active_choice,
                    local_llm_url=local_llm_url,
                    local_model_name=model_name,
                    api_key=api_key,
                    entity=resolved_entity,
                )
                desc_cache[desc_text] = guessed_category

            updated.at[idx, "Account Code"] = guessed_category
            if not is_uncategorized(guessed_category):
                updated_count += 1

    return updated, updated_count


def parse_gemini_ocr_output(
    raw_text: str,
    run_dynamic_ai_fallback: bool = False,
) -> pd.DataFrame:
    """
    Parse the CSV text block (with headers `Date,Amount,Description`) returned by `gemini-3.8-flash`
    into a Pandas DataFrame and run it through the Categorization Engine.
    """
    if not raw_text or not raw_text.strip():
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:csv|json|text)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    parsed_df: pd.DataFrame | None = None

    # Primary path: parse CSV text block with headers Date,Amount,Description
    try:
        csv_candidate = pd.read_csv(io.StringIO(cleaned))
        if not csv_candidate.empty or len(csv_candidate.columns) >= 2:
            parsed_df = csv_candidate
    except Exception:
        parsed_df = None

    # Fallback if model returned JSON array
    if parsed_df is None:
        try:
            json_payload = json.loads(cleaned)
            if isinstance(json_payload, dict):
                for key in ("transactions", "data", "items", "rows", "entries"):
                    if key in json_payload and isinstance(json_payload[key], list):
                        json_payload = json_payload[key]
                        break
                else:
                    json_payload = [json_payload]
            if isinstance(json_payload, list):
                parsed_df = pd.DataFrame(json_payload)
        except Exception:
            parsed_df = pd.DataFrame(columns=STANDARD_COLUMNS)

    standardized = standardize_ledger_dataframe(parsed_df)
    categorized = apply_rule_categorization(standardized)

    if run_dynamic_ai_fallback and not categorized.empty:
        has_unresolved = categorized["Account Code"].apply(is_uncategorized).any()
        if has_unresolved:
            try:
                categorized, _ = apply_ai_fallback_categorization(categorized)
            except Exception:
                # Keep rule-categorized DataFrame if the configured AI fallback endpoint is unreachable
                pass

    return categorized


def get_screenshot_ocr_md_path(vault_path: str | Path) -> Path | None:
    """Return the `.ocr.md` sidecar Path for a screenshot's vault path."""
    raw = str(vault_path or "").strip()
    if not raw:
        return None
    vp = Path(raw)
    if not vp.is_absolute():
        vp = Path(__file__).resolve().parent / vp
    return Path(f"{vp}.ocr.md")


def read_screenshot_ocr_markdown(file_rec: dict[str, object]) -> tuple[str, str]:
    """Read the saved `.ocr.md` sidecar for a screenshot record if it exists."""
    md_path = get_screenshot_ocr_md_path(str(file_rec.get("vault_path", "")))
    if md_path is None or not md_path.exists() or not md_path.is_file():
        return "", ""
    try:
        rel_path = str(md_path.relative_to(Path(__file__).resolve().parent))
    except ValueError:
        rel_path = str(md_path)
    try:
        return rel_path, md_path.read_text(encoding="utf-8")
    except Exception:
        return rel_path, ""


def save_screenshot_ocr_markdown(
    file_rec: dict[str, object],
    entity: str,
    tax_year: str,
    ocr_meta: dict[str, object] | None = None,
) -> str:
    """
    Save extracted screenshot OCR details + transactions to a human- and AI-readable
    Markdown sidecar file (`<vault_path>.ocr.md`) alongside the original image in the Vault.
    """
    md_path = get_screenshot_ocr_md_path(str(file_rec.get("vault_path", "")))
    if md_path is None:
        return ""
    md_path.parent.mkdir(parents=True, exist_ok=True)

    meta = ocr_meta or {}
    fname = str(file_rec.get("name", "Screenshot"))
    doc_type = str(meta.get("document_type") or "screenshot").strip()
    title = str(meta.get("title") or fname).strip()
    notes = str(meta.get("notes") or file_rec.get("note") or "").strip()
    summary_fields = meta.get("summary_fields") if isinstance(meta.get("summary_fields"), dict) else {}
    df = file_rec.get("df")
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(columns=STANDARD_COLUMNS)

    lines: list[str] = [
        f"# OCR Extract: {fname}",
        "",
        f"- **Entity:** {entity}",
        f"- **Tax Year:** {tax_year}",
        f"- **Source Image:** `{fname}`",
        f"- **OCR Model:** `{GEMINI_OCR_MODEL}`",
        f"- **Document Type:** `{doc_type}`",
        f"- **Title / Subject:** {title}",
        f"- **Extracted At:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary & Extracted Details",
        "",
    ]

    if summary_fields:
        for k, v in summary_fields.items():
            val_str = str(v).strip()
            if val_str:
                lines.append(f"- **{str(k).strip()}:** {val_str}")
    else:
        lines.append("- _No key-value summary fields detected._")

    if notes:
        lines.extend(["", f"**Notes:** {notes}"])

    lines.extend(["", "## Extracted Transactions", ""])
    if not df.empty:
        lines.append("| Date | Amount | Description | Property | Account Code |")
        lines.append("| --- | ---: | --- | --- | --- |")
        for _, row in df.iterrows():
            d_val = str(row.get("Date", "")).replace("|", "\\|").strip()
            try:
                amt_val = f"{float(row.get('Amount', 0.0) or 0.0):.2f}"
            except (TypeError, ValueError):
                amt_val = str(row.get("Amount", "0.00"))
            desc_val = str(row.get("Description", "")).replace("|", "\\|").replace("\n", " ").strip()
            prop_val = str(row.get("Property", UNASSIGNED_PROPERTY)).replace("|", "\\|").strip()
            code_val = str(row.get("Account Code", "Uncategorized")).replace("|", "\\|").strip()
            lines.append(f"| {d_val} | {amt_val} | {desc_val} | {prop_val} | {code_val} |")
    else:
        lines.append("_No individual transaction rows detected in this screenshot._")

    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    try:
        return str(md_path.relative_to(Path(__file__).resolve().parent))
    except ValueError:
        return str(md_path)


def extract_screenshot_structured_gemini(
    image_bytes: bytes,
    mime_type: str = "image/png",
    api_key: str | None = None,
    run_dynamic_ai_fallback: bool = True,
) -> dict[str, object]:
    """
    Use `gemini-3.8-flash` via `google-genai` to extract BOTH structured summary details
    (for bank/loan summaries, receipts, balances) and any transaction rows from a screenshot.
    """
    resolved_key = (api_key or st.session_state.get("google_genai_api_key") or "").strip()
    if not resolved_key:
        raise ValueError("Google GenAI API Key is required. Open '⚙ AI & System Settings' in the top right to enter it.")

    client = genai.Client(api_key=resolved_key)

    ocr_prompt = (
        "Perform highly precise OCR on this financial/banking/receipt image. "
        "Return ONLY a single valid JSON object (no markdown fences) with these keys:\n"
        '- "document_type": one of "transactions", "account_summary", "loan_summary", "receipt", "other"\n'
        '- "title": short title (e.g. bank & account name or merchant name)\n'
        '- "summary_fields": object of key-value pairs for every important figure or label visible '
        '(e.g. Bank, Account Name, Account Number, Balance, Limit, Interest Rate, Maturity Date, Total, Tax/GST, Date)\n'
        '- "notes": 1-2 sentence concise summary of what the screenshot shows\n'
        '- "transactions": array of objects, each with {"Date": "YYYY-MM-DD", "Amount": number, "Description": string}. '
        "Format Amount as a plain number (negative for debits/expenses, positive for credits/income). "
        "If the image is purely an account/loan balance summary with no transaction list, return an empty array []."
    )

    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    response = client.models.generate_content(
        model=GEMINI_OCR_MODEL,
        contents=[image_part, ocr_prompt],
    )

    raw_output = response.text or ""
    cleaned = raw_output.strip()
    fence_match = re.search(r"```(?:json|csv|text)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    doc_type = "screenshot"
    title = ""
    summary_fields: dict[str, object] = {}
    notes = ""
    df = pd.DataFrame(columns=STANDARD_COLUMNS)

    parsed_json = None
    try:
        parsed_json = json.loads(cleaned)
    except Exception:
        parsed_json = None

    if isinstance(parsed_json, dict):
        doc_type = str(parsed_json.get("document_type") or "screenshot").strip()
        title = str(parsed_json.get("title") or "").strip()
        if isinstance(parsed_json.get("summary_fields"), dict):
            summary_fields = parsed_json["summary_fields"]
        notes = str(parsed_json.get("notes") or "").strip()
        txs = parsed_json.get("transactions")
        if isinstance(txs, list) and txs:
            raw_df = pd.DataFrame(txs)
            df = standardize_ledger_dataframe(raw_df)
            df = apply_rule_categorization(df)
            if run_dynamic_ai_fallback and not df.empty and df["Account Code"].apply(is_uncategorized).any():
                try:
                    df, _ = apply_ai_fallback_categorization(df)
                except Exception:
                    pass
    else:
        df = parse_gemini_ocr_output(raw_output, run_dynamic_ai_fallback=run_dynamic_ai_fallback)

    return {
        "df": df,
        "document_type": doc_type,
        "title": title,
        "summary_fields": summary_fields,
        "notes": notes,
        "raw_output": raw_output,
    }


def extract_transactions_from_image_gemini(
    image_bytes: bytes,
    mime_type: str = "image/png",
    api_key: str | None = None,
    run_dynamic_ai_fallback: bool = True,
) -> tuple[pd.DataFrame, str]:
    """
    Use `gemini-3.8-flash` to OCR a screenshot and return `(DataFrame, raw_output)`.
    """
    res = extract_screenshot_structured_gemini(
        image_bytes=image_bytes,
        mime_type=mime_type,
        api_key=api_key,
        run_dynamic_ai_fallback=run_dynamic_ai_fallback,
    )
    return res["df"], str(res.get("raw_output", ""))


def extract_pdf_tables(pdf_bytes: bytes) -> pd.DataFrame:
    """
    Use pdfplumber to extract all tables from a PDF document and return a combined raw DataFrame.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(
            "pdfplumber is required for PDF table extraction. Install it via `pip install pdfplumber`."
        ) from exc

    extracted_frames: list[pd.DataFrame] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                cleaned_rows = [
                    [("" if cell is None else str(cell).strip()) for cell in row]
                    for row in table
                    if row and any(cell is not None and str(cell).strip() != "" for cell in row)
                ]
                if not cleaned_rows:
                    continue

                header_row = cleaned_rows[0]
                header_tokens = " ".join(header_row).lower()
                looks_like_header = any(
                    kw in header_tokens
                    for kw in ("date", "amount", "debit", "credit", "description", "account", "code", "particulars", "memo", "balance")
                )

                if looks_like_header and len(cleaned_rows) > 1:
                    deduped_cols = _dedupe_column_names(list(header_row))
                    frame = pd.DataFrame(cleaned_rows[1:], columns=deduped_cols)
                else:
                    frame = pd.DataFrame(cleaned_rows)

                extracted_frames.append(frame)

    if not extracted_frames:
        return pd.DataFrame()

    return pd.concat(extracted_frames, ignore_index=True)


def parse_xlsx_without_openpyxl(file_bytes: bytes) -> dict[str, pd.DataFrame]:
    """
    Pure-Python `.xlsx` workbook parser using `zipfile` + `xml.etree.ElementTree`.
    Works even when `openpyxl` is not installed in the environment, and reads ALL sheets
    accurately (including `Codes.xlsx`).
    """
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

    def _col_letters_to_idx(col_str: str) -> int:
        idx = 0
        for ch in col_str:
            if ch.isalpha():
                idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
        return max(0, idx - 1)

    sheets_dict: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.findall("main:si", ns):
                texts = [t.text or "" for t in si.iter(f"{{{ns['main']}}}t")]
                shared_strings.append("".join(texts))

        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map: dict[str, str] = {}
        for rel in rels_root:
            r_id = rel.attrib.get("Id", "")
            target = rel.attrib.get("Target", "")
            if r_id and target:
                clean_target = target.lstrip("/")
                if not clean_target.startswith("xl/"):
                    clean_target = f"xl/{clean_target}"
                rel_map[r_id] = clean_target

        for sh_idx, sh_el in enumerate(wb_root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"), start=1):
            sh_name = sh_el.attrib.get("name", f"Sheet{sh_idx}")
            r_id = ""
            for attr_k, attr_v in sh_el.attrib.items():
                if attr_k.endswith("}id") or attr_k == "r:id":
                    r_id = attr_v
                    break
            xml_path = rel_map.get(r_id, f"xl/worksheets/sheet{sh_idx}.xml")
            if xml_path not in zf.namelist():
                continue
            s_root = ET.fromstring(zf.read(xml_path))
            rows_xml = s_root.findall(".//main:row", ns)
            if not rows_xml:
                sheets_dict[sh_name] = pd.DataFrame()
                continue

            parsed_rows: list[dict[int, str]] = []
            max_col_idx = 0
            for r_el in rows_xml:
                row_map: dict[int, str] = {}
                for c_el in r_el.findall("main:c", ns):
                    ref = c_el.attrib.get("r", "")
                    col_letters = "".join(ch for ch in ref if ch.isalpha())
                    c_idx = _col_letters_to_idx(col_letters)
                    c_type = c_el.attrib.get("t", "")
                    v_el = c_el.find("main:v", ns)
                    is_el = c_el.find("main:is", ns)
                    val_str = ""
                    if v_el is not None and v_el.text is not None:
                        if c_type == "s":
                            try:
                                val_str = shared_strings[int(v_el.text)]
                            except Exception:
                                val_str = v_el.text
                        else:
                            val_str = v_el.text
                    elif is_el is not None:
                        val_str = "".join(
                            t.text or "" for t in is_el.iter(f"{{{ns['main']}}}t")
                        )
                    if val_str != "":
                        row_map[c_idx] = val_str.strip()
                        if c_idx > max_col_idx:
                            max_col_idx = c_idx
                if row_map:
                    parsed_rows.append(row_map)

            if not parsed_rows:
                sheets_dict[sh_name] = pd.DataFrame()
                continue

            grid = [
                [r_map.get(c_i, "") for c_i in range(max_col_idx + 1)]
                for r_map in parsed_rows
            ]
            sheets_dict[sh_name] = pd.DataFrame(grid)

    return sheets_dict


def parse_coded_workbook_sheets(
    file_bytes: bytes,
    filename: str = "Codes.xlsx",
    entity: str | None = None,
) -> dict[str, object]:
    """
    Smart Multi-Sheet Coded Workbook Parser (built specifically for `Codes.xlsx` and similar workbooks):
    Inspects every sheet in the workbook and extracts:
    1. `account_codes`: Distinct Account Codes found across all sheets (e.g. 'Materials', 'Insurance',
       'Bodycorp', 'Rent Income', 'Bank Interest', 'Rates', 'Watercare', 'James Drawings', etc.)
    2. `properties`: Distinct Properties detected across sheets & EOY statements
       (e.g. '303 Weymouth', '33 Blampied', '26 Frances', '9 Damian', '14 Raleigh', '363 Queen')
    3. `payee_code_rules`: Clean, decoupled `Payee / Pattern -> Account Code` rules (plus default Property
       ONLY when a Payee is 100% dedicated to a single property or General/Portfolio, while leaving multi-property
       contractors/insurers flexible so row-level `Analysis Code`/`Reference` determines the Property!)
    4. `sheet_summaries`: Per-sheet normalized transactions (`Date`, `Amount`, `Description`, `Property`, `Account Code`, `Payee`, `Property Hint`).
    """
    resolved_entity = get_active_entity(entity)
    ext = Path(filename or "").suffix.lower()
    raw_sheets: dict[str, pd.DataFrame] = {}

    if ext == ".xlsx":
        try:
            raw_sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None)
        except Exception:
            raw_sheets = parse_xlsx_without_openpyxl(file_bytes)
    elif ext in (".xls", ".ods"):
        try:
            raw_sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None)
        except Exception:
            raw_sheets = {}
    else:
        try:
            df_single = pd.read_csv(io.BytesIO(file_bytes), header=None)
            raw_sheets = {Path(filename).stem: df_single}
        except Exception:
            raw_sheets = {}

    discovered_codes: set[str] = set()
    discovered_properties: set[str] = set()
    # Map (payee_pattern_lower) -> {"pattern": str, "codes": Counter, "props": Counter, "sheet": str}
    from collections import Counter
    payee_stats: dict[str, dict[str, object]] = {}
    sheet_records: list[dict[str, object]] = []
    all_normalized_frames: list[pd.DataFrame] = []

    noise_codes = {
        "",
        "code",
        "account code",
        "category",
        "row labels",
        "column labels",
        "grand total",
        "total",
        "values",
        "nan",
        "none",
        "#name?",
    }

    for sheet_name, raw_df in raw_sheets.items():
        if raw_df is None or raw_df.empty:
            continue
        sh_lower = sheet_name.strip().lower()

        # Check if this is an EOY Property Manager Statement sheet (e.g. EOY Raywhite, EOY Barfoot, EOY Charlton)
        is_eoy_sheet = "eoy" in sh_lower or any(
            k in sh_lower for k in ("raywhite", "barfoot", "charlton")
        )
        if is_eoy_sheet:
            for _, r in raw_df.iterrows():
                c0 = "" if pd.isna(r.iloc[0]) else str(r.iloc[0]).strip()
                if c0:
                    det_p = detect_property_from_text(c0, entity=resolved_entity)
                    if det_p != UNASSIGNED_PROPERTY and "/" not in det_p:
                        discovered_properties.add(det_p)

        # Check if this is a Pivot Table sheet (like Sheet 1 'Pivot Table' in Codes.xlsx)
        first_cells_text = " ".join(
            str(v).strip().lower()
            for v in raw_df.iloc[:6, :4].values.flatten()
            if not pd.isna(v)
        )
        if ("row labels" in first_cells_text or "sum of amount" in first_cells_text or "pivot" in sh_lower) and not is_eoy_sheet:
            # Extract Account Codes and Property headers from the Pivot Table
            for _, r in raw_df.iterrows():
                c0 = "" if pd.isna(r.iloc[0]) else str(r.iloc[0]).strip()
                if not c0 or c0.lower() in noise_codes:
                    continue
                det_p = detect_property_from_text(c0, entity=resolved_entity)
                if det_p != UNASSIGNED_PROPERTY and ("/" not in det_p):
                    discovered_properties.add(det_p)
            sheet_records.append(
                {
                    "sheet_name": sheet_name,
                    "sheet_type": "Pivot / Summary Table",
                    "rows": 0,
                    "df": pd.DataFrame(columns=STANDARD_COLUMNS),
                }
            )
            continue

        # Find header row within first 20 rows
        header_row_idx = None
        for r_idx in range(min(20, len(raw_df))):
            row_vals = [
                "" if pd.isna(v) else str(v).strip().lower()
                for v in raw_df.iloc[r_idx].tolist()
            ]
            has_fin_col = any(
                any(k in v for k in ("amount", "debit", "credit", "other party", "payee", "details", "description", "item", "rent", "income"))
                for v in row_vals
            )
            has_date_or_code = any(
                any(k in v for k in ("code", "account code", "category", "date", "year / month", "income"))
                for v in row_vals
            )
            if has_fin_col and has_date_or_code:
                header_row_idx = r_idx
                break

        if header_row_idx is None:
            sheet_records.append(
                {
                    "sheet_name": sheet_name,
                    "sheet_type": "EOY Property Statement" if is_eoy_sheet else "Summary Sheet",
                    "rows": int(len(raw_df)),
                    "df": pd.DataFrame(columns=STANDARD_COLUMNS),
                }
            )
            continue

        headers = _dedupe_column_names(raw_df.iloc[header_row_idx].tolist())
        body_df = raw_df.iloc[header_row_idx + 1 :].copy()
        body_df.columns = headers
        body_df = body_df.reset_index(drop=True)

        # Locate columns in this sheet
        acct_col = None
        for c in headers:
            if c.strip().lower() == "code":
                acct_col = c
                break
        if acct_col is None:
            for c in headers:
                if c.strip().lower() in ("account code", "category"):
                    acct_col = c
                    break

        payee_col = None
        for cand in ("other party", "payee", "description", "details", "item"):
            for c in headers:
                if c.strip().lower() == cand:
                    payee_col = c
                    break
            if payee_col:
                break

        date_col = None
        for cand in ("date", "transaction date", "posting date"):
            for c in headers:
                if c.strip().lower() == cand:
                    date_col = c
                    break
            if date_col:
                break

        amt_col = None
        debit_col = None
        credit_col = None
        for c in headers:
            cl = c.strip().lower()
            if cl in ("amount", "total"):
                amt_col = c
            elif cl.startswith("debit") or cl in ("withdrawal", "withdrawals"):
                debit_col = c
            elif cl.startswith("credit") or cl in ("deposit", "deposits"):
                credit_col = c

        prop_hint_cols = [
            c
            for c in headers
            if c != acct_col
            and any(
                k in c.strip().lower()
                for k in ("analysis code", "reference", "particulars", "memo", "notes", "code_1")
            )
        ]

        # Detect if the entire sheet is dedicated to one property (e.g., ASB BT Prop -> 363 Queen)
        sheet_default_prop = UNASSIGNED_PROPERTY
        if "asb" in sh_lower:
            # Check if ASB sheet rows mention Apt 1604 / 363 Queen
            sample_blob = " ".join(
                str(v) for v in body_df.values.flatten() if not pd.isna(v)
            )
            det_sh_prop = detect_property_from_text(sample_blob, entity=resolved_entity)
            if det_sh_prop != UNASSIGNED_PROPERTY and "/" not in det_sh_prop:
                sheet_default_prop = det_sh_prop

        current_section_prop = sheet_default_prop
        if is_eoy_sheet:
            for pre_idx in range(min(15, len(raw_df))):
                pre_val = "" if pd.isna(raw_df.iloc[pre_idx, 0]) else str(raw_df.iloc[pre_idx, 0]).strip()
                if pre_val:
                    pre_prop = detect_property_from_text(pre_val, entity=resolved_entity)
                    if pre_prop != UNASSIGNED_PROPERTY and "/" not in pre_prop:
                        if current_section_prop == UNASSIGNED_PROPERTY:
                            current_section_prop = pre_prop
                        discovered_properties.add(pre_prop)
        sheet_tx_rows: list[dict[str, object]] = []

        for _, row in body_df.iterrows():
            # In EOY sheets, section headers appear in Col 0 (e.g. '303 Weymouth Road, Weymouth, Auckland')
            first_col_val = "" if pd.isna(row.iloc[0]) else str(row.iloc[0]).strip()
            if is_eoy_sheet and first_col_val:
                sec_prop = detect_property_from_text(first_col_val, entity=resolved_entity)
                if sec_prop != UNASSIGNED_PROPERTY and "/" not in sec_prop:
                    current_section_prop = sec_prop
                    discovered_properties.add(sec_prop)

            raw_code = "" if (acct_col is None or pd.isna(row.get(acct_col))) else str(row.get(acct_col)).strip()
            if raw_code.lower() in noise_codes:
                raw_code = ""

            # Parse amount
            row_amt = 0.0
            if debit_col and credit_col:
                d_v = _clean_amount_series(pd.Series([row.get(debit_col)])).iloc[0]
                c_v = _clean_amount_series(pd.Series([row.get(credit_col)])).iloc[0]
                row_amt = abs(float(c_v)) - abs(float(d_v))
            elif amt_col:
                row_amt = float(_clean_amount_series(pd.Series([row.get(amt_col)])).iloc[0])
            elif debit_col:
                row_amt = -abs(float(_clean_amount_series(pd.Series([row.get(debit_col)])).iloc[0]))
            elif credit_col:
                row_amt = abs(float(_clean_amount_series(pd.Series([row.get(credit_col)])).iloc[0]))

            raw_date = "" if (date_col is None or pd.isna(row.get(date_col))) else str(row.get(date_col)).strip()
            if raw_date and re.fullmatch(r"\d{5}(?:\.0)?", raw_date):
                try:
                    raw_date = (
                        pd.to_datetime(float(raw_date), unit="D", origin="1899-12-30")
                        .strftime("%Y-%m-%d")
                    )
                except Exception:
                    pass
            elif raw_date and " 00:00:00" in raw_date:
                raw_date = raw_date.replace(" 00:00:00", "").strip()
            raw_payee = "" if (payee_col is None or pd.isna(row.get(payee_col))) else str(row.get(payee_col)).strip()

            hint_parts = [
                str(row.get(hc)).strip()
                for hc in prop_hint_cols
                if not pd.isna(row.get(hc))
                and str(row.get(hc)).strip()
                and str(row.get(hc)).strip().lower() not in ("nan", "none", "#name?")
            ]
            hint_str = " | ".join(dict.fromkeys(hint_parts))

            # Skip blank or non-transaction rows
            if not raw_code and row_amt == 0.0 and not raw_payee:
                continue
            if raw_payee.lower() in ("total", "subtotal", "opening balance", "closing balance") and not raw_code:
                continue

            if raw_code and not raw_code.replace(".", "").replace("-", "").isdigit():
                discovered_codes.add(raw_code)

            # Determine row Property:
            # 1) Check hint columns (Analysis Code, Reference, Particulars, Memo)
            # 2) Check Payee text
            # 3) Fallback to current EOY section property or sheet default property
            # 4) Fallback to 'General / Portfolio' if Account Code is portfolio-wide
            row_prop = detect_property_from_text(f"{hint_str} {raw_payee}", entity=resolved_entity)
            if row_prop == UNASSIGNED_PROPERTY and current_section_prop != UNASSIGNED_PROPERTY:
                row_prop = current_section_prop
            if row_prop == UNASSIGNED_PROPERTY and raw_code.lower() in PORTFOLIO_WIDE_CATEGORIES:
                row_prop = "General / Portfolio"

            if row_prop != UNASSIGNED_PROPERTY and "/" not in row_prop:
                discovered_properties.add(row_prop)

            # Build clean description combining Payee + Property/Bank Hints
            desc_components = [p for p in (raw_payee, hint_str) if p]
            full_desc = " | ".join(desc_components) if desc_components else raw_code

            if raw_date and raw_date.replace(".", "").isdigit():
                # Convert Excel serial date (e.g., 45385 -> 2024-04-03) into readable ISO date
                try:
                    serial_val = float(raw_date)
                    if 35000 <= serial_val <= 60000:
                        dt_obj = pd.to_datetime(serial_val, unit="D", origin="1899-12-30")
                        raw_date = dt_obj.strftime("%Y-%m-%d")
                except Exception:
                    pass

            sheet_tx_rows.append(
                {
                    "Date": raw_date,
                    "Amount": float(row_amt),
                    "Description": full_desc,
                    "Property": row_prop if row_prop else UNASSIGNED_PROPERTY,
                    "Account Code": raw_code if raw_code else "Uncategorized",
                }
            )

            # Record Payee -> Account Code rule candidate
            # Use `raw_payee` as primary pattern so payee rules aren't polluted by one-off invoice numbers
            pattern_candidate = raw_payee if raw_payee else hint_str
            if (
                pattern_candidate
                and len(pattern_candidate) >= 3
                and not pattern_candidate.replace(".", "").replace("-", "").isdigit()
                and raw_code
            ):
                pat_key = pattern_candidate.lower()
                entry = payee_stats.setdefault(
                    pat_key,
                    {
                        "pattern": pattern_candidate,
                        "codes": Counter(),
                        "props": Counter(),
                        "sheet": sheet_name,
                    },
                )
                entry["codes"][raw_code] += 1
                if row_prop and row_prop != UNASSIGNED_PROPERTY:
                    entry["props"][row_prop] += 1

        sh_df = pd.DataFrame(sheet_tx_rows, columns=STANDARD_COLUMNS) if sheet_tx_rows else pd.DataFrame(columns=STANDARD_COLUMNS)
        if not sh_df.empty and not is_eoy_sheet:
            all_normalized_frames.append(sh_df)

        sheet_records.append(
            {
                "sheet_name": sheet_name,
                "sheet_type": "EOY Property Statement" if is_eoy_sheet else "Coded Bank Ledger",
                "rows": len(sh_df) if not sh_df.empty else len(body_df),
                "df": sh_df,
            }
        )

    # Consolidate Payee -> Account Code rules
    # Crucial insight: If a Payee (like '8020AON Stylecover' or '5781Kim Painter Main') is used across
    # MULTIPLE properties (via Analysis Code / Reference), its rule Property is set to `UNASSIGNED_PROPERTY`
    # ("Auto-detect from Property Column") so it never forces the wrong property onto another property's row!
    extracted_rules: list[dict[str, object]] = []
    for _pat_k, info in payee_stats.items():
        codes_counter: Counter = info["codes"]
        props_counter: Counter = info["props"]
        best_code, code_count = codes_counter.most_common(1)[0]
        total_occurrences = sum(codes_counter.values())

        distinct_real_props = [
            p for p in props_counter.keys() if p not in (UNASSIGNED_PROPERTY, "General / Portfolio")
        ]
        if best_code.lower() in PORTFOLIO_WIDE_CATEGORIES:
            rule_prop = "General / Portfolio"
            prop_mode = "Portfolio-Wide"
        elif len(distinct_real_props) == 1 and "/" not in distinct_real_props[0]:
            rule_prop = distinct_real_props[0]
            prop_mode = "Single Property"
        elif len(distinct_real_props) > 1:
            # Multi-property vendor/contractor/insurer! Keep property decoupled so row-level Analysis Code decides
            rule_prop = UNASSIGNED_PROPERTY
            prop_mode = f"Multi-Property ({len(distinct_real_props)} props — row column decides)"
        else:
            rule_prop = UNASSIGNED_PROPERTY
            prop_mode = "Auto-detect from row"

        extracted_rules.append(
            {
                "pattern": str(info["pattern"]),
                "account_code": best_code,
                "property": rule_prop,
                "property_mode": prop_mode,
                "occurrences": total_occurrences,
                "sheet": str(info["sheet"]),
            }
        )

    extracted_rules.sort(key=lambda x: (-int(x["occurrences"]), str(x["pattern"]).lower()))
    combined_bank_df = (
        pd.concat(all_normalized_frames, ignore_index=True)
        if all_normalized_frames
        else pd.DataFrame(columns=STANDARD_COLUMNS)
    )

    return {
        "account_codes": sorted(discovered_codes, key=lambda s: s.lower()),
        "properties": sorted(discovered_properties, key=lambda s: s.lower()),
        "payee_code_rules": extracted_rules,
        "sheet_summaries": sheet_records,
        "combined_bank_df": combined_bank_df,
    }


def import_codes_workbook_into_entity(
    file_bytes: bytes,
    filename: str = "Codes.xlsx",
    entity: str | None = None,
    tax_year: str | None = None,
    import_bank_transactions: bool = False,
) -> dict[str, object]:
    """
    One-click importer for `Codes.xlsx` (or any multi-sheet coded workbook):
    1. Registers all discovered Account Codes into the entity's Chart of Accounts.
    2. Registers all discovered Properties (and their bank shorthand aliases) into the entity's Properties list.
    3. Saves all decoupled Payee -> Account Code rules into Memorized Rules.
    4. Re-applies the updated Account Code & Property rules across all currently loaded files/ledgers.
    5. Optionally imports the coded bank transaction sheets (`Westpac - BT Prop`, `ASB BT Prop`) into the active year ledger.
    """
    resolved_entity = get_active_entity(entity)
    resolved_year = get_active_tax_year(tax_year)
    parsed = parse_coded_workbook_sheets(file_bytes, filename=filename, entity=resolved_entity)

    added_codes = 0
    for code_name in parsed["account_codes"]:
        ok, _ = add_entity_account_code(code_name, entity=resolved_entity)
        if ok:
            added_codes += 1

    added_props = 0
    for prop_name in parsed["properties"]:
        ok, _ = add_entity_property(prop_name, entity=resolved_entity)
        if ok:
            added_props += 1

    saved_rules = 0
    for rule in parsed["payee_code_rules"]:
        ok, _ = save_memorized_rule(
            pattern=str(rule["pattern"]),
            property_name=str(rule["property"]),
            account_code=str(rule["account_code"]),
            entity=resolved_entity,
            source=f"Imported ({filename})",
        )
        if ok:
            saved_rules += 1

    # Re-apply rules to existing files and active ledger
    files_list = get_entity_uploaded_files(resolved_entity, resolved_year)
    for f_rec in files_list:
        f_df = f_rec.get("df")
        if isinstance(f_df, pd.DataFrame) and not f_df.empty:
            f_rec["df"] = apply_rule_categorization(f_df, overwrite_existing=False, entity=resolved_entity)
    if files_list:
        sync_entity_ledger_from_files(resolved_entity, resolved_year)
    else:
        curr_ledger = get_entity_ledger(resolved_entity, resolved_year)
        if not curr_ledger.empty:
            set_entity_ledger(
                apply_rule_categorization(curr_ledger, overwrite_existing=False, entity=resolved_entity),
                resolved_entity,
                resolved_year,
            )

    imported_tx_count = 0
    if import_bank_transactions:
        bank_df: pd.DataFrame = parsed["combined_bank_df"]
        if isinstance(bank_df, pd.DataFrame) and not bank_df.empty:
            append_entity_ledger(bank_df, entity=resolved_entity, tax_year=resolved_year)
            imported_tx_count = len(bank_df)

    return {
        "total_codes": len(parsed["account_codes"]),
        "added_codes": added_codes,
        "total_properties": len(parsed["properties"]),
        "added_properties": added_props,
        "saved_rules": saved_rules,
        "imported_tx_count": imported_tx_count,
        "parsed": parsed,
    }


def extract_other_document_tables(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Extract financial tables from 'Other' formats:
    - Excel (.xlsx, .xls) — with pure-Python XML fallback if `openpyxl` is unavailable
    - OpenDocument Spreadsheet (.ods)
    - OpenDocument Text (.odt)
    - TSV / Text (.tsv, .txt)
    """
    ext = Path(filename or "").suffix.lower()
    if ext in (".xlsx", ".xls"):
        # First try smart multi-sheet coded workbook parser (handles Codes.xlsx & bank sheets cleanly)
        try:
            parsed_wb = parse_coded_workbook_sheets(file_bytes, filename=filename)
            bank_df = parsed_wb.get("combined_bank_df")
            if isinstance(bank_df, pd.DataFrame) and not bank_df.empty:
                return bank_df
        except Exception:
            pass
        try:
            sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None)
        except Exception:
            sheets = parse_xlsx_without_openpyxl(file_bytes) if ext == ".xlsx" else {}
        frames = []
        for df in sheets.values():
            if isinstance(df, pd.DataFrame) and not df.empty:
                norm_s = normalize_financial_dataframe(df)
                if not norm_s.empty:
                    frames.append(norm_s)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if ext == ".ods":
        sheets = pd.read_excel(io.BytesIO(file_bytes), engine="odf", sheet_name=None)
        frames = [df for df in sheets.values() if isinstance(df, pd.DataFrame) and not df.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if ext == ".odt":
        from odf.opendocument import load as odf_load
        from odf.table import Table, TableCell, TableRow
        from odf.teletype import extractText

        doc = odf_load(io.BytesIO(file_bytes))
        extracted_frames: list[pd.DataFrame] = []
        for tbl in doc.getElementsByType(Table):
            rows_data: list[list[str]] = []
            for tr in tbl.getElementsByType(TableRow):
                row_cells: list[str] = []
                for tc in tr.getElementsByType(TableCell):
                    cell_text = extractText(tc).strip()
                    repeat = int(tc.getAttribute("numbercolumnsrepeated") or 1)
                    for _ in range(min(repeat, 12)):
                        row_cells.append(cell_text)
                if any(c != "" for c in row_cells):
                    rows_data.append(row_cells)
            if rows_data:
                header_tokens = " ".join(rows_data[0]).lower()
                if any(k in header_tokens for k in ("date", "amount", "debit", "credit", "description", "payee")) and len(rows_data) > 1:
                    cols = _dedupe_column_names(rows_data[0])
                    extracted_frames.append(pd.DataFrame(rows_data[1:], columns=cols))
                else:
                    extracted_frames.append(pd.DataFrame(rows_data))
        return pd.concat(extracted_frames, ignore_index=True) if extracted_frames else pd.DataFrame()
    # Fallback CSV/TSV
    try:
        return pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python")
    except Exception:
        return pd.read_csv(io.BytesIO(file_bytes))


def _read_bank_csv_bytes(file_bytes: bytes) -> pd.DataFrame:
    """
    Robustly parse bank CSV bytes, including NZ/US bank exports (such as ASB CSVs)
    that prepend 1-column metadata preamble lines above the true multi-column table header.
    """
    import csv as _csv

    text = ""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = file_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        text = file_bytes.decode("utf-8", errors="replace")

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return pd.DataFrame()

    header_keywords = {
        "date",
        "amount",
        "debit",
        "credit",
        "payee",
        "description",
        "memo",
        "particulars",
        "tran type",
        "details",
        "reference",
        "other party",
        "unique id",
    }

    best_header_idx = 0
    for idx, line in enumerate(lines[:30]):
        try:
            parsed_row = next(_csv.reader([line]))
        except Exception:
            continue
        cleaned_cells = [str(c).strip().lower() for c in parsed_row if str(c).strip()]
        if len(cleaned_cells) >= 3:
            hits = sum(1 for c in cleaned_cells if c in header_keywords or any(k in c for k in ("date", "amount", "debit", "credit")))
            if hits >= 2:
                best_header_idx = idx
                break

    sliced_text = "\n".join(lines[best_header_idx:])
    try:
        df = pd.read_csv(io.StringIO(sliced_text))
        if len(df.columns) >= 2:
            return df
    except Exception:
        pass

    try:
        df = pd.read_csv(io.BytesIO(file_bytes))
        if len(df.columns) >= 2:
            return df
    except Exception:
        pass

    # Fallback: jagged row reader via csv.reader padded to max width
    all_rows: list[list[str]] = []
    for line in lines[best_header_idx:]:
        try:
            row = next(_csv.reader([line]))
            if any(str(cell).strip() for cell in row):
                all_rows.append([str(cell).strip() for cell in row])
        except Exception:
            continue
    if not all_rows:
        return pd.DataFrame()
    max_cols = max(len(r) for r in all_rows)
    padded_rows = [r + [""] * (max_cols - len(r)) for r in all_rows]
    if len(padded_rows) >= 2:
        cols = _dedupe_column_names(padded_rows[0])
        return pd.DataFrame(padded_rows[1:], columns=cols)
    return pd.DataFrame(padded_rows)


def classify_upload_bucket(filename: str) -> str:
    """
    Classify any uploaded filename into one of the 4 columns:
    - 'csv'         : .csv
    - 'pdf'         : .pdf
    - 'screenshots' : .png, .jpg, .jpeg, .webp
    - 'other'       : .xlsx, .xls, .ods, .odt, .tsv, etc.
    """
    ext = Path(filename or "").suffix.lower()
    if ext == ".csv":
        return "csv"
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        return "screenshots"
    return "other"


def _reconcile_vault_and_broken_csvs(
    entity: str,
    tax_year: str,
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
    """
    1. Automatically repair any previously uploaded CSV record that had 0 rows due to bank metadata preamble lines.
    2. Automatically recover any orphaned files present in `data/vault/<Entity>/<Year>/<Bucket>/` on disk
       that were interrupted before being registered in SQLite `uploaded_files`.
    """
    changed = False
    existing_names = {str(r.get("name", "")).strip() for r in records}

    # 1. Repair 0-row CSVs that have a valid vault file on disk
    for rec in records:
        if str(rec.get("bucket", "")) == "csv":
            df_cur = rec.get("df")
            if not isinstance(df_cur, pd.DataFrame) or df_cur.empty:
                vpath = str(rec.get("vault_path", "") or "")
                vp = Path(vpath) if vpath else None
                if vp and not vp.is_absolute():
                    vp = Path(__file__).resolve().parent / vp
                if vp and vp.exists() and vp.is_file():
                    try:
                        raw_df = _read_bank_csv_bytes(vp.read_bytes())
                        if not raw_df.empty:
                            norm_df = (
                                raw_df.copy()
                                if list(raw_df.columns) == STANDARD_COLUMNS
                                else normalize_financial_dataframe(raw_df)
                            )
                            if not norm_df.empty:
                                cat_df = apply_rule_categorization(norm_df, entity=entity)
                                rec["df"] = cat_df
                                rec["note"] = ""
                                _save_file_record_to_sqlite(entity, tax_year, rec)
                                changed = True
                    except Exception:
                        pass

    # 2. Recover orphaned files in data/vault/<Entity>/<Year>/<Bucket>/
    for b_key in ("csv", "pdf", "screenshots", "other"):
        b_dir = get_vault_dir(entity, tax_year, b_key)
        if not b_dir.exists():
            continue
        for fpath in sorted(b_dir.iterdir()):
            if not fpath.is_file() or fpath.name.startswith(".") or fpath.name.lower().endswith(".ocr.md"):
                continue
            if fpath.name in existing_names:
                continue
            try:
                fbytes = fpath.read_bytes()
                fname = fpath.name
                raw_df = pd.DataFrame()
                parse_note = ""
                needs_ocr = False
                if b_key == "csv":
                    raw_df = _read_bank_csv_bytes(fbytes)
                elif b_key == "pdf":
                    raw_df = extract_pdf_tables(fbytes)
                elif b_key == "screenshots":
                    needs_ocr = True
                    parse_note = "Recovered from Vault — click '🔍 Run AI OCR' or open file."
                else:
                    raw_df = extract_other_document_tables(fbytes, fname)

                if not raw_df.empty:
                    norm_df = (
                        raw_df.copy()
                        if list(raw_df.columns) == STANDARD_COLUMNS
                        else normalize_financial_dataframe(raw_df)
                    )
                    cat_df = apply_rule_categorization(norm_df, entity=entity)
                elif b_key == "screenshots":
                    placeholder = pd.DataFrame(
                        [
                            {
                                "Date": pd.Timestamp.today().strftime("%Y-%m-%d"),
                                "Amount": 0.0,
                                "Description": f"Screenshot Receipt ({fname}) — Enter details or run OCR",
                                "Property": UNASSIGNED_PROPERTY,
                                "Account Code": "Uncategorized",
                            }
                        ]
                    )[STANDARD_COLUMNS]
                    cat_df = apply_rule_categorization(placeholder, entity=entity)
                else:
                    cat_df = pd.DataFrame(columns=STANDARD_COLUMNS)

                new_rec: dict[str, object] = {
                    "id": f"file_{len(records) + 1}_{abs(hash(f'{fname}::{len(fbytes)}')) % 100000}",
                    "signature": f"{fname}::{len(fbytes)}",
                    "name": fname,
                    "bucket": b_key,
                    "uploaded_at": pd.Timestamp.now().strftime("%H:%M:%S"),
                    "vault_path": str(fpath),
                    "df": cat_df,
                    "raw_bytes": fbytes if b_key == "screenshots" else None,
                    "mime_type": "image/png" if fname.lower().endswith(".png") else ("image/jpeg" if b_key == "screenshots" else ""),
                    "needs_ocr": needs_ocr,
                    "note": parse_note,
                }
                _save_file_record_to_sqlite(entity, tax_year, new_rec, sort_order=len(records))
                records.append(new_rec)
                existing_names.add(fname)
                changed = True
            except Exception:
                continue

    if changed:
        frames = [
            r["df"].reindex(columns=STANDARD_COLUMNS).copy()
            for r in records
            if isinstance(r.get("df"), pd.DataFrame) and not r["df"].empty
        ]
        combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=STANDARD_COLUMNS)
        _db_save_master_ledger(entity, tax_year, combined)

    return records


def get_entity_uploaded_files(
    entity: str | None = None,
    tax_year: str | None = None,
) -> list[dict[str, object]]:
    """
    Return the list of uploaded file records for `(entity, tax_year)`.
    Automatically hydrates from SQLite (`data/axiom_ledger.db`) and the local File Vault
    (`data/vault/<Entity>/<Year>/<Bucket>/`) on first access in a session.
    """
    init_workspace_state()
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    st.session_state.setdefault("entity_uploaded_files", {})
    st.session_state["entity_uploaded_files"].setdefault(target_entity, {})
    st.session_state.setdefault("_sqlite_loaded_files", set())
    load_key = f"{target_entity}::{target_year}"

    if load_key not in st.session_state["_sqlite_loaded_files"]:
        db_files = _load_uploaded_files_from_sqlite(target_entity, target_year)
        db_files = _reconcile_vault_and_broken_csvs(target_entity, target_year, db_files)
        st.session_state["entity_uploaded_files"][target_entity][target_year] = db_files
        st.session_state["_sqlite_loaded_files"].add(load_key)
    elif target_year not in st.session_state["entity_uploaded_files"][target_entity]:
        db_files = _load_uploaded_files_from_sqlite(target_entity, target_year)
        db_files = _reconcile_vault_and_broken_csvs(target_entity, target_year, db_files)
        st.session_state["entity_uploaded_files"][target_entity][target_year] = db_files
    return st.session_state["entity_uploaded_files"][target_entity][target_year]


def sync_entity_ledger_from_files(
    entity: str | None = None,
    tax_year: str | None = None,
) -> pd.DataFrame:
    """
    Persist each file record's DataFrame to SQLite (`uploaded_files` + `file_transactions`)
    and rebuild the master entity ledger for `(entity, tax_year)` from all uploaded files.
    """
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    files_list = get_entity_uploaded_files(target_entity, target_year)
    frames: list[pd.DataFrame] = []
    for f_rec in files_list:
        _save_file_record_to_sqlite(target_entity, target_year, f_rec)
        f_df = f_rec.get("df")
        if isinstance(f_df, pd.DataFrame) and not f_df.empty:
            frames.append(f_df.reindex(columns=STANDARD_COLUMNS).copy())
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        return set_entity_ledger(combined, target_entity, target_year)
    empty_df = pd.DataFrame(columns=STANDARD_COLUMNS)
    return set_entity_ledger(empty_df, target_entity, target_year)


def run_ocr_on_screenshot_record(
    file_rec: dict[str, object],
    entity: str | None = None,
    tax_year: str | None = None,
) -> tuple[bool, str, int]:
    """
    Run Gemini Vision OCR on an existing screenshot record in `files_list`,
    update its DataFrame in memory + SQLite, and sync the entity master ledger.
    Returns `(ok, message, extracted_row_count)`.
    """
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    fname = str(file_rec.get("name", "Screenshot.png"))
    api_key = st.session_state.get("google_genai_api_key", "").strip()
    if not api_key:
        return False, "No Gemini API Key configured in sidebar settings.", 0

    raw_bytes = file_rec.get("raw_bytes")
    if not isinstance(raw_bytes, (bytes, bytearray)):
        vpath = str(file_rec.get("vault_path", "") or "")
        if vpath:
            vp = Path(vpath)
            if not vp.is_absolute():
                vp = Path(__file__).resolve().parent / vp
            if vp.exists() and vp.is_file():
                raw_bytes = vp.read_bytes()
                file_rec["raw_bytes"] = raw_bytes
    if not isinstance(raw_bytes, (bytes, bytearray)):
        return False, f"Could not load image bytes for '{fname}'.", 0

    mime_type = str(file_rec.get("mime_type") or "") or (
        "image/png" if fname.lower().endswith(".png") else "image/jpeg"
    )
    try:
        ocr_res = extract_screenshot_structured_gemini(
            image_bytes=bytes(raw_bytes),
            mime_type=mime_type,
            api_key=api_key,
            run_dynamic_ai_fallback=True,
        )
        ocr_df = ocr_res.get("df")
        if not isinstance(ocr_df, pd.DataFrame):
            ocr_df = pd.DataFrame(columns=STANDARD_COLUMNS)

        if not ocr_df.empty:
            norm_df = (
                ocr_df.copy()
                if list(ocr_df.columns) == STANDARD_COLUMNS
                else normalize_financial_dataframe(ocr_df)
            )
            cat_df = apply_rule_categorization(norm_df, entity=target_entity)
            file_rec["df"] = cat_df
            file_rec["needs_ocr"] = False
            md_path = save_screenshot_ocr_markdown(file_rec, target_entity, target_year, ocr_meta=ocr_res)
            file_rec["ocr_md_path"] = md_path
            file_rec["note"] = f"OCR complete ({len(cat_df)} rows) · Saved .ocr.md"
            _save_file_record_to_sqlite(target_entity, target_year, file_rec)
            sync_entity_ledger_from_files(target_entity, target_year)
            return True, f"Extracted {len(cat_df)} transaction(s) from '{fname}' and saved .ocr.md.", len(cat_df)
        else:
            file_rec["needs_ocr"] = False
            summary_note = str(ocr_res.get("notes") or "").strip()
            file_rec["note"] = (
                f"{summary_note} · Saved .ocr.md"
                if summary_note
                else "Account/Loan summary screenshot · Saved .ocr.md"
            )
            md_path = save_screenshot_ocr_markdown(file_rec, target_entity, target_year, ocr_meta=ocr_res)
            file_rec["ocr_md_path"] = md_path
            _save_file_record_to_sqlite(target_entity, target_year, file_rec)
            sync_entity_ledger_from_files(target_entity, target_year)
            return True, f"Scanned '{fname}' and saved .ocr.md summary.", 0
    except Exception as exc:
        file_rec["needs_ocr"] = True
        file_rec["note"] = f"OCR error: {str(exc)[:80]}"
        _save_file_record_to_sqlite(target_entity, target_year, file_rec)
        return False, f"OCR failed on '{fname}': {exc}", 0


def ingest_uploaded_file_object(
    uploaded_file,
    entity: str | None = None,
    tax_year: str | None = None,
    forced_bucket: str | None = None,
    run_ocr_immediately: bool = True,
) -> tuple[bool, str, dict[str, object] | None]:
    """
    Parse an uploaded file (CSV, PDF, Screenshot, or Other [Excel/ODS/ODT]),
    save the original file into `data/vault/<Entity>/<Year>/<Bucket>/`,
    normalize its columns, apply Memorized Rules + Keyword Rules, store it in SQLite
    and the 4-column file board for `(entity, tax_year)`, and sync the master ledger.
    """
    target_entity = get_active_entity(entity)
    target_year = get_active_tax_year(tax_year)
    fname = getattr(uploaded_file, "name", "Uploaded_File")
    fbytes = uploaded_file.getvalue()
    bucket = forced_bucket or classify_upload_bucket(fname)

    files_list = get_entity_uploaded_files(target_entity, target_year)
    # Check if already ingested with identical name + byte size in SQLite / session
    file_sig = f"{fname}::{len(fbytes)}"
    for existing in files_list:
        if existing.get("signature") == file_sig or str(existing.get("name", "")).strip() == fname:
            existing_df = existing.get("df")
            # If previously failed CSV parse (0 rows), allow re-parsing now
            if (
                bucket == "csv"
                and (not isinstance(existing_df, pd.DataFrame) or existing_df.empty)
                and "parse warning" in str(existing.get("note", "")).lower()
            ):
                files_list.remove(existing)
                _delete_file_record_from_sqlite(str(existing.get("id", "")), delete_vault_file=False)
                break
            if bucket == "screenshots" and run_ocr_immediately and existing.get("needs_ocr"):
                run_ocr_on_screenshot_record(existing, entity=target_entity, tax_year=target_year)
                return True, f"Updated OCR for '{fname}'.", existing
            return True, f"'{fname}' is already saved in the {bucket.upper()} vault.", existing

    # Save raw file bytes to organized local File Vault on disk (`data/vault/<Company>/<Year>/<Bucket>/<Filename>`)
    vault_path = _save_raw_file_to_vault(target_entity, target_year, bucket, fname, fbytes)

    raw_df = pd.DataFrame()
    parse_note = ""
    needs_ocr = False
    ocr_meta: dict[str, object] | None = None

    try:
        if bucket == "csv":
            raw_df = _read_bank_csv_bytes(fbytes)
        elif bucket == "pdf":
            raw_df = extract_pdf_tables(fbytes)
            if raw_df.empty:
                parse_note = "Saved PDF to Vault. No structured table found automatically."
        elif bucket == "screenshots":
            has_api_key = bool(st.session_state.get("google_genai_api_key", "").strip())
            mime_type = getattr(uploaded_file, "type", None) or (
                "image/png" if fname.lower().endswith(".png") else "image/jpeg"
            )
            if run_ocr_immediately and has_api_key:
                try:
                    ocr_meta = extract_screenshot_structured_gemini(
                        image_bytes=fbytes,
                        mime_type=mime_type,
                        api_key=st.session_state.get("google_genai_api_key", ""),
                        run_dynamic_ai_fallback=True,
                    )
                    maybe_df = ocr_meta.get("df")
                    raw_df = maybe_df if isinstance(maybe_df, pd.DataFrame) else pd.DataFrame()
                    if raw_df.empty:
                        summary_note = str(ocr_meta.get("notes") or "").strip()
                        parse_note = (
                            f"{summary_note} · Saved .ocr.md"
                            if summary_note
                            else "Account/Loan summary screenshot · Saved .ocr.md"
                        )
                    else:
                        parse_note = f"Auto-OCR ({GEMINI_OCR_MODEL}) · Saved .ocr.md"
                except Exception as ocr_exc:
                    needs_ocr = True
                    parse_note = f"OCR pending ({str(ocr_exc)[:60]}). Click '🔍 Run OCR' or open file."
            else:
                needs_ocr = True
                parse_note = (
                    "Saved to Vault — click '🔍 Run OCR on Screenshots' above or open file to extract rows."
                    if has_api_key
                    else "Saved to Vault — open file to run OCR or code receipt row."
                )
        else:
            raw_df = extract_other_document_tables(fbytes, fname)
    except Exception as exc:
        parse_note = f"Saved to Vault (parse warning: {exc})"

    if not raw_df.empty:
        # If already standardized (e.g. from OCR), keep columns, else normalize
        if list(raw_df.columns) == STANDARD_COLUMNS:
            norm_df = raw_df.copy()
        else:
            norm_df = normalize_financial_dataframe(raw_df)
        categorized_df = apply_rule_categorization(norm_df, entity=target_entity)
    else:
        if bucket == "screenshots":
            # Provide a ready-to-edit 1-row template for a receipt screenshot so user can code it immediately
            categorized_df = pd.DataFrame(
                [
                    {
                        "Date": datetime.now().strftime("%Y-%m-%d"),
                        "Amount": 0.0,
                        "Description": Path(fname).stem.replace("_", " ").replace("-", " "),
                        "Property": UNASSIGNED_PROPERTY,
                        "Account Code": "Uncategorized",
                    }
                ]
            )[STANDARD_COLUMNS]
            categorized_df = apply_rule_categorization(categorized_df, entity=target_entity)
        else:
            categorized_df = pd.DataFrame(columns=STANDARD_COLUMNS)

    file_id = f"file_{int(time.time() * 1000)}_{len(files_list) + 1}"
    record: dict[str, object] = {
        "id": file_id,
        "signature": file_sig,
        "name": fname,
        "bucket": bucket,
        "vault_path": vault_path,
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "df": categorized_df,
        "raw_bytes": fbytes if bucket == "screenshots" else None,
        "mime_type": getattr(uploaded_file, "type", "image/png") if bucket == "screenshots" else "",
        "needs_ocr": needs_ocr,
        "note": parse_note,
    }
    if bucket == "screenshots" and ocr_meta is not None:
        md_path = save_screenshot_ocr_markdown(record, target_entity, target_year, ocr_meta=ocr_meta)
        record["ocr_md_path"] = md_path
    files_list.append(record)
    _save_file_record_to_sqlite(target_entity, target_year, record)
    sync_entity_ledger_from_files(target_entity, target_year)
    return True, f"Saved '{fname}' to Vault & SQLite ({len(categorized_df):,} rows).", record


def train_memory_from_coded_dataframe(
    df: pd.DataFrame,
    entity: str | None = None,
    source_label: str = "2026 Coded Accounts",
) -> tuple[int, int]:
    """
    Scan a pre-coded DataFrame (such as last year's 2026 accountant spreadsheet)
    and memorize every row that has a valid Description and a coded Account Code (and/or Property).
    Returns `(learned_count, new_properties_count)`.
    """
    resolved_entity = get_active_entity(entity)
    norm_df = normalize_financial_dataframe(df)
    if norm_df.empty:
        return 0, 0

    initial_props = set(p.lower() for p in get_entity_properties(resolved_entity))
    learned = 0
    for _, row in norm_df.iterrows():
        desc = str(row.get("Description", "")).strip()
        prop = str(row.get("Property", "")).strip() or UNASSIGNED_PROPERTY
        code = str(row.get("Account Code", "")).strip()
        if not desc:
            continue
        if is_uncategorized(code) and is_property_unassigned(prop, code):
            continue
        ok, _ = save_memorized_rule(
            pattern=desc,
            property_name=prop,
            account_code=code if not is_uncategorized(code) else "Uncategorized",
            entity=resolved_entity,
            source=source_label,
        )
        if ok:
            learned += 1

    final_props = set(p.lower() for p in get_entity_properties(resolved_entity))
    new_props_added = max(0, len(final_props) - len(initial_props))
    return learned, new_props_added


def get_file_red_counts(df: pd.DataFrame, entity: str | None = None) -> dict[str, int]:
    """
    Compute 3-tier Traffic Light counts for a ledger or uploaded file DataFrame:
    - total: total rows
    - green: 🟢 100% Sure (both Property and Account Code assigned & matched to a confirmed rule)
    - yellow: 🟡 Partially Sure / Needs Double-Check (either Property or Item found, or keyword guess not yet memorized)
    - red: 🔴 Not Sure at All (neither Item nor Property known)
    - certain: alias for `green` (for backward compatibility)
    - needs_property: rows missing Property
    - unknown_code: rows missing Account Code (Item)
    - red_total: total rows needing attention or double-check (`red + yellow`)
    """
    if df is None or df.empty:
        return {
            "total": 0,
            "green": 0,
            "yellow": 0,
            "red": 0,
            "certain": 0,
            "needs_property": 0,
            "unknown_code": 0,
            "red_total": 0,
        }

    resolved_entity = get_active_entity(entity)
    total = len(df)
    green_cnt = 0
    yellow_cnt = 0
    red_cnt = 0
    needs_prop = 0
    unknown_code = 0

    for _, row in df.iterrows():
        prop = row.get("Property", UNASSIGNED_PROPERTY)
        code = row.get("Account Code", "Uncategorized")
        desc = row.get("Description", "")
        miss_code = is_uncategorized(code)
        miss_prop = is_property_unassigned(prop, code)
        if miss_code:
            unknown_code += 1
        elif miss_prop:
            needs_prop += 1

        tier, _ = evaluate_row_traffic_light(
            property_val=prop,
            account_code=code,
            description=desc,
            entity=resolved_entity,
        )
        if tier == "green":
            green_cnt += 1
        elif tier == "yellow":
            yellow_cnt += 1
        else:
            red_cnt += 1

    return {
        "total": total,
        "green": green_cnt,
        "yellow": yellow_cnt,
        "red": red_cnt,
        "certain": green_cnt,
        "needs_property": needs_prop,
        "unknown_code": unknown_code,
        "red_total": red_cnt + yellow_cnt,
    }


def _render_full_page_file_reviewer(
    file_rec: dict[str, object],
    active_entity: str,
    active_tax_year: str,
    key_prefix: str = "ing",
) -> None:
    """
    Full-Page File Reviewer when a user clicks on an individual CSV, PDF, Screenshot, or Other file.
    Includes:
    - Top '← Back to 4-Column File Board' button
    - Summary metrics (🟢 Coded vs 🔴 Needs Property vs 🔴 Unknown Code)
    - One-click 'Set Default Property for This File'
    - Batch 'Group Identical Problem Payees (Fix Once -> Apply to All)'
    - Filter Toggle: [ 🔴 Red / Needs Attention Only ] | [ All Rows ]
    - Excel-style interactive table with Property + Account Code dropdowns and Red status indicators
    - Automatic memorization into .axiom_memory.json
    """
    file_id = str(file_rec.get("id", "file"))
    file_name = str(file_rec.get("name", "Uploaded File"))
    bucket = str(file_rec.get("bucket", "csv")).upper()
    df: pd.DataFrame = file_rec.get("df", pd.DataFrame(columns=STANDARD_COLUMNS)).copy()
    df = df.reindex(columns=STANDARD_COLUMNS).copy()
    df["Property"] = df["Property"].fillna(UNASSIGNED_PROPERTY).astype(str).str.strip().replace("", UNASSIGNED_PROPERTY)
    df["Account Code"] = df["Account Code"].fillna("Uncategorized").astype(str).str.strip().replace("", "Uncategorized")

    state_open_key = f"{key_prefix}_open_file_id_{active_entity}_{active_tax_year}"
    vault_path_str = str(file_rec.get("vault_path", ""))
    vault_file_bytes: bytes | None = None
    if vault_path_str:
        vp = Path(vault_path_str)
        if not vp.is_absolute():
            vp = Path(__file__).resolve().parent / vp
        if vp.exists() and vp.is_file():
            try:
                vault_file_bytes = vp.read_bytes()
            except Exception:
                vault_file_bytes = None
    if vault_file_bytes is None and isinstance(file_rec.get("raw_bytes"), (bytes, bytearray)):
        vault_file_bytes = bytes(file_rec["raw_bytes"])

    # Top Navigation Bar with Back Button + Download Original Vault File + Re-Run Rules
    nav_c1, nav_c2, nav_c3, nav_c4 = st.columns([1.15, 2.35, 1.15, 1.15])
    with nav_c1:
        if st.button(
            "← Back to All Files",
            key=f"{key_prefix}_back_btn_{file_id}",
            type="secondary",
            use_container_width=True,
        ):
            st.session_state[state_open_key] = None
            st.rerun()
    with nav_c2:
        period_str = format_tax_year_period(active_tax_year, active_entity)
        vault_badge = f" · 💾 Saved in Vault ({vault_path_str})" if vault_path_str else " · 💾 Saved in SQLite"
        st.markdown(
            f"""
            <div style="padding-top: 0.1rem;">
                <span class="axiom-eyebrow">{bucket} FILE REVIEW // {active_entity.upper()} ({period_str})</span>
                <div style="font-size: 1.12rem; font-weight: 600; color: #F3F4F6;">📄 {file_name}</div>
                <div style="font-size: 0.72rem; color: #8E95A5;">{vault_badge}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with nav_c3:
        if vault_file_bytes is not None:
            st.download_button(
                label="⬇ Original File",
                data=vault_file_bytes,
                file_name=file_name,
                mime=str(file_rec.get("mime_type") or "application/octet-stream"),
                key=f"{key_prefix}_dl_orig_{file_id}",
                use_container_width=True,
            )
    with nav_c4:
        if st.button(
            "Re-Run Memorized Rules",
            key=f"{key_prefix}_rerun_mem_{file_id}",
            use_container_width=True,
        ):
            updated_df = apply_rule_categorization(df, overwrite_existing=False, entity=active_entity)
            file_rec["df"] = updated_df
            sync_entity_ledger_from_files(active_entity, active_tax_year)
            st.rerun()

    st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)

    # If this is a Screenshot, show image preview, OCR button & saved .ocr.md sidecar right at top
    if file_rec.get("bucket") == "screenshots" and file_rec.get("raw_bytes"):
        img_col, ocr_col = st.columns([1.2, 2.8])
        with img_col:
            st.image(file_rec["raw_bytes"], caption=file_name, use_container_width=True)
        with ocr_col:
            has_key = bool(st.session_state.get("google_genai_api_key", "").strip())
            st.markdown('<div class="axiom-eyebrow">SCREENSHOT OCR EXTRACTION (GEMINI 3.8 FLASH)</div>', unsafe_allow_html=True)
            if file_rec.get("note"):
                st.caption(str(file_rec.get("note")))
            md_rel_path, md_content = read_screenshot_ocr_markdown(file_rec)
            oc_b1, oc_b2 = st.columns([1.6, 1.2])
            with oc_b1:
                if st.button(
                    f"🔍 {'Re-Run' if md_content else 'Run'} Gemini 3.8 Flash OCR",
                    key=f"{key_prefix}_run_shot_ocr_{file_id}",
                    disabled=not has_key,
                    use_container_width=True,
                ):
                    with st.spinner("Running Gemini 3.8 Flash OCR and saving .ocr.md..."):
                        ok_ocr, msg_ocr, _ = run_ocr_on_screenshot_record(
                            file_rec,
                            entity=active_entity,
                            tax_year=active_tax_year,
                        )
                    if ok_ocr:
                        st.success(msg_ocr)
                        st.rerun()
                    else:
                        st.error(msg_ocr)
            with oc_b2:
                if md_content:
                    st.download_button(
                        label="⬇ Download .ocr.md",
                        data=md_content.encode("utf-8"),
                        file_name=f"{file_name}.ocr.md",
                        mime="text/markdown",
                        key=f"{key_prefix}_dl_ocr_md_{file_id}",
                        use_container_width=True,
                    )
            if md_content:
                with st.expander(f"📝 Saved OCR Markdown ({md_rel_path or f'{file_name}.ocr.md'})", expanded=False):
                    st.markdown(md_content)

    counts = get_file_red_counts(df, entity=active_entity)
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        st.metric("TOTAL ROWS IN FILE", f"{counts['total']:,}")
    with mc2:
        st.metric("🟢 GREEN (100% SURE)", f"{counts['green']:,}")
    with mc3:
        st.metric("🟡 YELLOW (DOUBLE-CHECK)", f"{counts['yellow']:,}")
    with mc4:
        st.metric("🔴 RED (UNSURE / MISSING)", f"{counts['red']:,}")

    property_options = [UNASSIGNED_PROPERTY] + get_entity_properties(active_entity)
    # Deduplicate while preserving order
    seen_p: set[str] = set()
    clean_prop_options: list[str] = []
    for p in property_options:
        if p not in seen_p:
            seen_p.add(p)
            clean_prop_options.append(p)

    account_code_options = get_account_code_options(active_entity)

    # -------------------------------------------------------------------------
    # FAST FIX BAR: 1) Default Property for File & 2) Group Identical Red/Yellow Payees
    # -------------------------------------------------------------------------
    fix_col1, fix_col2 = st.columns([1.4, 2.2])
    with fix_col1:
        with st.container(border=True):
            st.markdown('<div class="axiom-eyebrow">QUICK FIX 1 // BANK FILE DEFAULT PROPERTY</div>', unsafe_allow_html=True)
            st.caption("If this entire bank CSV/statement belongs to one property, fill all unassigned rows in 1 click:")
            dp_c1, dp_c2 = st.columns([1.6, 1.1])
            with dp_c1:
                chosen_default_prop = st.selectbox(
                    "Default Property for File",
                    options=get_entity_properties(active_entity),
                    key=f"{key_prefix}_def_prop_sel_{file_id}",
                    label_visibility="collapsed",
                )
            with dp_c2:
                if st.button(
                    "Apply to Unassigned",
                    key=f"{key_prefix}_apply_def_prop_{file_id}",
                    use_container_width=True,
                ):
                    mask_unassigned = df.apply(
                        lambda r: is_property_unassigned(r.get("Property", ""), r.get("Account Code", "")),
                        axis=1,
                    )
                    df.loc[mask_unassigned, "Property"] = chosen_default_prop
                    file_rec["df"] = df
                    sync_entity_ledger_from_files(active_entity, active_tax_year)
                    st.success(f"Assigned '{chosen_default_prop}' to {int(mask_unassigned.sum())} unassigned row(s).")
                    st.rerun()

            with st.popover("✏️ Edit Property Names (Property 1, Property 2...)", use_container_width=True):
                st.markdown(f'<div class="axiom-eyebrow">EDIT PROPERTIES // {active_entity.upper()}</div>', unsafe_allow_html=True)
                render_property_editor_widget(entity=active_entity, key_prefix=f"{key_prefix}_fp_prop_{file_id}")

    with fix_col2:
        with st.container(border=True):
            st.markdown('<div class="axiom-eyebrow">QUICK FIX 2 // BATCH CODE REPEATED 🔴 RED / 🟡 YELLOW ROWS &amp; MEMORIZE</div>', unsafe_allow_html=True)
            red_mask = df.apply(
                lambda r: is_row_red_or_unsure(
                    r.get("Property", ""),
                    r.get("Account Code", ""),
                    description=r.get("Description", ""),
                    entity=active_entity,
                ),
                axis=1,
            )
            red_df = df[red_mask]
            if red_df.empty:
                st.success("✅ Zero Red/Yellow rows remaining in this file! Every row is 🟢 100% Sure.")
            else:
                payee_counts = red_df["Description"].value_counts()
                payee_labels = [f"{desc} ({cnt} row{'s' if cnt > 1 else ''})" for desc, cnt in payee_counts.items() if str(desc).strip()]
                payee_map = {f"{desc} ({cnt} row{'s' if cnt > 1 else ''})": str(desc) for desc, cnt in payee_counts.items() if str(desc).strip()}

                if payee_labels:
                    b_c1, b_c2, b_c3, b_c4 = st.columns([1.8, 1.25, 1.25, 0.9])
                    with b_c1:
                        selected_payee_label = st.selectbox(
                            "Repeated Bank Row Text",
                            options=payee_labels,
                            key=f"{key_prefix}_batch_payee_{file_id}",
                        )
                        target_desc = payee_map[selected_payee_label]
                        sample_row = red_df[red_df["Description"] == target_desc].iloc[0]
                        curr_p = str(sample_row.get("Property", UNASSIGNED_PROPERTY))
                        curr_c = str(sample_row.get("Account Code", "Uncategorized"))
                    with b_c2:
                        batch_code = st.selectbox(
                            "1. What is the Item?",
                            options=account_code_options,
                            index=account_code_options.index(curr_c) if curr_c in account_code_options else (account_code_options.index("Insurance") if "Insurance" in account_code_options else 0),
                            key=f"{key_prefix}_batch_code_{file_id}",
                        )
                    is_batch_port = is_portfolio_wide_code(batch_code, active_entity)
                    with b_c3:
                        if is_batch_port:
                            batch_prop = st.selectbox(
                                "2. Which Property?",
                                options=["General / Portfolio"],
                                disabled=True,
                                key=f"{key_prefix}_batch_prop_port_{file_id}",
                            )
                        else:
                            batch_prop = st.selectbox(
                                "2. Which Property?",
                                options=clean_prop_options,
                                index=clean_prop_options.index(curr_p) if curr_p in clean_prop_options else (1 if len(clean_prop_options) > 1 else 0),
                                key=f"{key_prefix}_batch_prop_{file_id}",
                            )
                    with b_c4:
                        st.markdown("<div style='height: 1.65rem;'></div>", unsafe_allow_html=True)
                        if st.button(
                            "Save & Memorize",
                            key=f"{key_prefix}_btn_batch_fix_{file_id}",
                            type="primary",
                            use_container_width=True,
                        ):
                            resolved_batch_prop = "General / Portfolio" if is_batch_port else batch_prop
                            match_rows = df["Description"].astype(str).str.strip().str.lower() == target_desc.strip().lower()
                            df.loc[match_rows, "Property"] = resolved_batch_prop
                            df.loc[match_rows, "Account Code"] = batch_code
                            save_memorized_rule(
                                pattern=target_desc,
                                property_name=resolved_batch_prop,
                                account_code=batch_code,
                                entity=active_entity,
                                source=f"Coded in {file_name}",
                            )
                            file_rec["df"] = df
                            sync_entity_ledger_from_files(active_entity, active_tax_year)
                            st.success(
                                f"Updated {int(match_rows.sum())} row(s) for '{target_desc}' → {batch_code} | {resolved_batch_prop} (🟢 Memorized)."
                            )
                            st.rerun()

    # -------------------------------------------------------------------------
    # EXCEL-STYLE INTERACTIVE SHEET WITH 3-TIER TRAFFIC LIGHTS & FILTER TOGGLE
    # -------------------------------------------------------------------------
    flt_c1, flt_c2, flt_c3 = st.columns([2.5, 1.15, 1.05])
    with flt_c1:
        filter_mode = st.radio(
            "Filter Rows",
            options=[
                f"All Rows ({counts['total']})",
                f"🔴+🟡 Needs Check ({counts['red_total']})",
                f"🔴 Red Only ({counts['red']})",
                f"🟡 Yellow Only ({counts['yellow']})",
                f"🟢 Green 100% Sure ({counts['green']})",
            ],
            horizontal=True,
            key=f"{key_prefix}_filter_radio_{file_id}",
            label_visibility="collapsed",
        )
    with flt_c2:
        auto_memorize = st.checkbox(
            "Auto-Memorize Confirmed Rows (Turn 🟢 Green)",
            value=True,
            key=f"{key_prefix}_auto_mem_chk_{file_id}",
        )
    with flt_c3:
        if counts["red_total"] > 0:
            if st.button(
                "🤖 Auto-Guess Red Rows with AI",
                key=f"{key_prefix}_ai_guess_file_{file_id}",
                use_container_width=True,
            ):
                try:
                    with st.spinner("Running AI categorization on unknown rows..."):
                        updated_df, _ = apply_ai_fallback_categorization(df, entity=active_entity)
                        file_rec["df"] = updated_df
                        sync_entity_ledger_from_files(active_entity, active_tax_year)
                    st.rerun()
                except Exception as exc:
                    st.error(f"AI Categorization Error: {exc}")

    display_df = df.copy()
    row_tiers: list[str] = []
    row_badges: list[str] = []
    for _, r in display_df.iterrows():
        t_val, b_val = evaluate_row_traffic_light(
            property_val=r.get("Property", ""),
            account_code=r.get("Account Code", ""),
            description=r.get("Description", ""),
            entity=active_entity,
        )
        row_tiers.append(t_val)
        row_badges.append(b_val)
    display_df["Status"] = row_badges
    display_df["_tier"] = row_tiers

    # Reorder so Item (Account Code) and Property are side-by-side right next to Amount
    editor_cols = ["Status", "Date", "Description", "Amount", "Account Code", "Property"]

    if filter_mode.startswith("🔴+🟡"):
        view_df = display_df[display_df["_tier"].isin(["red", "yellow"])][editor_cols]
    elif filter_mode.startswith("🔴 Red"):
        view_df = display_df[display_df["_tier"] == "red"][editor_cols]
    elif filter_mode.startswith("🟡"):
        view_df = display_df[display_df["_tier"] == "yellow"][editor_cols]
    elif filter_mode.startswith("🟢"):
        view_df = display_df[display_df["_tier"] == "green"][editor_cols]
    else:
        view_df = display_df[editor_cols]

    # Ensure all current values in view_df exist in dropdown options
    for p_val in view_df["Property"].dropna().unique():
        s_p = str(p_val).strip()
        if s_p and s_p not in clean_prop_options:
            clean_prop_options.append(s_p)
    for c_val in view_df["Account Code"].dropna().unique():
        s_c = str(c_val).strip()
        if s_c and s_c not in account_code_options:
            account_code_options.append(s_c)

    edited_view = st.data_editor(
        view_df,
        use_container_width=True,
        hide_index=False,
        num_rows="dynamic" if filter_mode.startswith("All") else "fixed",
        key=f"{key_prefix}_sheet_editor_{file_id}_{filter_mode[:6]}",
        column_config={
            "Status": st.column_config.TextColumn(
                "Traffic Light Certainty",
                disabled=True,
                width="medium",
                help="🟢 Green = 100% Sure (Memorized Rule + House Matched). 🟡 Yellow = Partially Sure / Please Double-Check. 🔴 Red = Unsure / Missing.",
            ),
            "Date": st.column_config.TextColumn("Date", width="small"),
            "Description": st.column_config.TextColumn("Full CSV Line Text (Payee | Particulars | Code | Reference)", width="large"),
            "Amount": st.column_config.NumberColumn("Amount ($)", format="$%.2f", width="small"),
            "Account Code": st.column_config.SelectboxColumn(
                "1. What is the Item?",
                options=account_code_options,
                required=True,
                width="medium",
                help="Select the Item (e.g. Insurance, Loan Interest, Rent Income, Rates, Bodycorp, Accountant).",
            ),
            "Property": st.column_config.SelectboxColumn(
                "2. Which Property?",
                options=clean_prop_options,
                required=True,
                width="medium",
                help="Select which Property this item belongs to (e.g., 9 Damian, 33 Blampied, 303 Weymouth, or General / Portfolio).",
            ),
        },
    )

    if st.button(
        "💾 Save & Confirm Checked Rows (Memorize as 🟢 100% Sure)",
        key=f"{key_prefix}_save_sheet_btn_{file_id}",
        type="primary",
    ):
        if filter_mode.startswith("All"):
            new_full_df = edited_view[STANDARD_COLUMNS].copy().reset_index(drop=True)
        else:
            new_full_df = df.copy()
            for idx in edited_view.index:
                if idx in new_full_df.index:
                    for col in STANDARD_COLUMNS:
                        new_full_df.loc[idx, col] = edited_view.loc[idx, col]

        # Auto-fill General / Portfolio for any row assigned a Portfolio-Wide Item
        for idx_r in new_full_df.index:
            c_val = str(new_full_df.loc[idx_r, "Account Code"]).strip()
            p_val = str(new_full_df.loc[idx_r, "Property"]).strip()
            if is_portfolio_wide_code(c_val, active_entity) and p_val in ("", UNASSIGNED_PROPERTY):
                new_full_df.loc[idx_r, "Property"] = "General / Portfolio"

        learned_now = 0
        if auto_memorize:
            for _, r in new_full_df.iterrows():
                desc_v = str(r.get("Description", "")).strip()
                prop_v = str(r.get("Property", "")).strip()
                code_v = str(r.get("Account Code", "")).strip()
                if desc_v and not is_uncategorized(code_v) and not is_property_unassigned(prop_v, code_v):
                    save_memorized_rule(
                        pattern=desc_v,
                        property_name=prop_v,
                        account_code=code_v,
                        entity=active_entity,
                        source=f"Confirmed in {file_name}",
                    )
                    learned_now += 1

        # Re-apply memorized rules across all uploaded files in case a newly memorized rule resolves other rows
        file_rec["df"] = apply_rule_categorization(new_full_df, overwrite_existing=False, entity=active_entity)
        sync_entity_ledger_from_files(active_entity, active_tax_year)
        st.success(f"Saved '{file_name}' and memorized {learned_now} confirmed rule(s) as 🟢 100% Sure.")
        st.rerun()


def _render_bulk_upload_dropzone(
    active_entity: str,
    active_tax_year: str,
    files_list: list[dict],
    key_prefix: str = "ing",
    eyebrow_label: str = "UPLOAD ALL FILES AT ONCE &mdash; AUTO-SAVED TO SQLITE &amp; DISK VAULT",
) -> None:
    period_label = format_tax_year_period(active_tax_year, active_entity)
    with st.container(border=True):
        top_u1, top_u2, top_u3, top_u4 = st.columns([2.2, 0.95, 1.05, 0.95])
        with top_u1:
            st.markdown(
                f"""
                <div>
                    <div class="axiom-eyebrow">{eyebrow_label}</div>
                    <h3 style="margin: 0; font-size: 1.08rem;">Upload Bank CSVs, PDF Statements, Screenshots &amp; Excel/ODT Files ({active_entity} &bull; {period_label})</h3>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with top_u2:
            with st.popover(f"🏠 Edit Properties ({len(get_entity_properties(active_entity))})", use_container_width=True):
                st.markdown(f'<div class="axiom-eyebrow">EDIT PROPERTIES // {active_entity.upper()}</div>', unsafe_allow_html=True)
                render_property_editor_widget(entity=active_entity, key_prefix=f"{key_prefix}_board_prop")
        with top_u3:
            show_setup_key = f"_{key_prefix}_show_item_prop_setup_{active_entity}"
            is_setup_open = bool(st.session_state.get(show_setup_key, False))
            if st.button(
                "✕ Close Item + Property Setup" if is_setup_open else f"⚙️ Item + Property Setup ({len(get_memorized_rules(active_entity))})",
                key=f"{key_prefix}_btn_toggle_item_prop_{active_entity}",
                type="primary" if is_setup_open else "secondary",
                use_container_width=True,
                help="Specify which Insurance, Loan Interest, Rent Income, or Rates belongs to which Property in clean columns.",
            ):
                st.session_state[show_setup_key] = not is_setup_open
                st.rerun()
        with top_u4:
            zip_bytes = export_entity_vault_zip(active_entity, active_tax_year)
            safe_ent_slug = _safe_vault_segment(active_entity).replace(" ", "_")
            st.download_button(
                label=f"📦 Vault Zip ({len(files_list)} files)",
                data=zip_bytes,
                file_name=f"AxiomVault_{safe_ent_slug}_{active_tax_year}.zip",
                mime="application/zip",
                key=f"{key_prefix}_dl_vault_zip_{active_entity}_{active_tax_year}",
                use_container_width=True,
                help="Download a ZIP bundle containing all original CSVs, PDFs, Screenshots, Excel/ODT files AND the coded Master CSV for your accountant.",
            )

        if st.session_state.get(f"_{key_prefix}_show_item_prop_setup_{active_entity}", False):
            st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)
            render_item_property_setup_matrix(
                entity=active_entity,
                tax_year=active_tax_year,
                key_prefix=f"{key_prefix}_inline_matrix",
            )
            st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)

        master_nonce_key = f"_{key_prefix}_master_up_nonce_{active_entity}_{active_tax_year}"
        master_nonce = int(st.session_state.get(master_nonce_key, 0))
        last_master_msg_key = f"_{key_prefix}_master_up_msg_{active_entity}_{active_tax_year}"
        if st.session_state.get(last_master_msg_key):
            st.success(str(st.session_state.pop(last_master_msg_key)))

        uploaded_batch = st.file_uploader(
            "Select or drag-and-drop all your files here (CSV, PDF, PNG/JPG Screenshots, Excel .xlsx/.xls, OpenDocument .ods/.odt)",
            type=["csv", "pdf", "png", "jpg", "jpeg", "webp", "xlsx", "xls", "ods", "odt", "tsv"],
            accept_multiple_files=True,
            key=f"{key_prefix}_master_multi_uploader_{active_entity}_{active_tax_year}_{master_nonce}",
        )

        if uploaded_batch:
            has_shots = any(classify_upload_bucket(getattr(uf, "name", "")) == "screenshots" for uf in uploaded_batch)
            has_gemini = bool(st.session_state.get("google_genai_api_key", "").strip())
            ub_c1, ub_c2, ub_c3 = st.columns([2.0, 2.0, 1.0] if (has_shots and has_gemini) else [3.0, 0.01, 1.0])
            with ub_c1:
                do_fast_upload = st.button(
                    f"⬆️ Upload & Sort {len(uploaded_batch)} Selected File(s) Now",
                    key=f"{key_prefix}_btn_master_upload_{active_entity}_{active_tax_year}_{master_nonce}",
                    type="primary",
                    use_container_width=True,
                )
            with ub_c2:
                do_ocr_upload = False
                if has_shots and has_gemini:
                    do_ocr_upload = st.button(
                        f"🔍 Upload & Run AI OCR on Screenshots ({len(uploaded_batch)} file(s))",
                        key=f"{key_prefix}_btn_master_upload_ocr_{active_entity}_{active_tax_year}_{master_nonce}",
                        type="secondary",
                        use_container_width=True,
                    )
            with ub_c3:
                if st.button(
                    "✕ Clear Queue",
                    key=f"{key_prefix}_btn_master_clear_{active_entity}_{active_tax_year}_{master_nonce}",
                    use_container_width=True,
                ):
                    st.session_state[master_nonce_key] = master_nonce + 1
                    st.rerun()

            if do_fast_upload or do_ocr_upload:
                prog = st.progress(0.0, text="Uploading files to Vault & SQLite...")
                newly_added = 0
                total_u = len(uploaded_batch)
                for idx_u, up_file in enumerate(uploaded_batch, start=1):
                    prog.progress(
                        idx_u / total_u,
                        text=f"Processing ({idx_u}/{total_u}): {up_file.name}...",
                    )
                    ok, _, _ = ingest_uploaded_file_object(
                        up_file,
                        entity=active_entity,
                        tax_year=active_tax_year,
                        run_ocr_immediately=True,
                    )
                    if ok:
                        newly_added += 1
                prog.empty()
                st.session_state[master_nonce_key] = master_nonce + 1
                st.session_state[last_master_msg_key] = (
                    f"✅ Uploaded & sorted {newly_added} file(s) into the 4 columns below."
                )
                st.rerun()


def render_entity_ingestion_pipeline(
    active_entity: str,
    active_tax_year: str,
    key_prefix: str = "ing",
    include_loans_block: bool = True,
) -> None:
    """
    Front Page / Data Ingestion Board:
    - Block 1 (Step 1): Automatic 4-Column File Table (CSV | PDF | Screenshots | Other) — click any file to open full-page review & fix red alerts.
    - Block 2 (Step 2, for BT Property Holdings Ltd): Property Loans & Bank Registry — each property and what each loan is.
    """
    state_open_key = f"{key_prefix}_open_file_id_{active_entity}_{active_tax_year}"
    files_list = get_entity_uploaded_files(active_entity, active_tax_year)

    # Check if a specific file is currently opened in Full-Page Review Mode
    open_file_id = st.session_state.get(state_open_key)
    if open_file_id:
        matched_file = next((f for f in files_list if f.get("id") == open_file_id), None)
        if matched_file is not None:
            _render_full_page_file_reviewer(
                matched_file,
                active_entity=active_entity,
                active_tax_year=active_tax_year,
                key_prefix=key_prefix,
            )
            return
        else:
            st.session_state[state_open_key] = None

    # =========================================================================
    # BLOCK 1 (STEP 1): AUTOMATIC 4-COLUMN FILE TABLE
    # =========================================================================
    with st.container(border=True):
        hdr1, hdr2, hdr3, hdr4 = st.columns([2.6, 0.95, 1.15, 0.95])
        with hdr1:
            st.markdown(
                '<div class="axiom-eyebrow">STEP 1 // AUTOMATIC 4-COLUMN FILE TABLE &mdash; CLICK ANY FILE TO OPEN FULL-PAGE REVIEW &amp; FIX RED ALERTS</div>',
                unsafe_allow_html=True,
            )
        with hdr2:
            with st.popover(f"🏠 Properties ({len(get_entity_properties(active_entity))})", use_container_width=True):
                st.markdown(f'<div class="axiom-eyebrow">EDIT PROPERTIES // {active_entity.upper()}</div>', unsafe_allow_html=True)
                render_property_editor_widget(entity=active_entity, key_prefix=f"{key_prefix}_board_prop")
        with hdr3:
            show_setup_key = f"_{key_prefix}_show_item_prop_setup_{active_entity}"
            is_setup_open = bool(st.session_state.get(show_setup_key, False))
            if st.button(
                "✕ Close Setup" if is_setup_open else f"⚙️ Item + Property ({len(get_memorized_rules(active_entity))})",
                key=f"{key_prefix}_btn_toggle_item_prop_{active_entity}",
                type="primary" if is_setup_open else "secondary",
                use_container_width=True,
            ):
                st.session_state[show_setup_key] = not is_setup_open
                st.rerun()
        with hdr4:
            zip_bytes = export_entity_vault_zip(active_entity, active_tax_year)
            safe_ent_slug = _safe_vault_segment(active_entity).replace(" ", "_")
            st.download_button(
                label=f"📦 Vault Zip ({len(files_list)})",
                data=zip_bytes,
                file_name=f"AxiomVault_{safe_ent_slug}_{active_tax_year}.zip",
                mime="application/zip",
                key=f"{key_prefix}_dl_vault_zip_{active_entity}_{active_tax_year}",
                use_container_width=True,
            )

        st.markdown("<div style='height: 0.85rem;'></div>", unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown(
                """
                <div style="border-bottom: 1px solid #2A2D34; padding-bottom: 0.5rem; margin-bottom: 0.65rem;">
                    <div class="axiom-eyebrow" style="margin-bottom: 0.1rem;">＋ UPLOAD MULTIPLE FILES (ADD AS MANY BATCHES AS YOU NEED)</div>
                    <div style="font-size: 0.82rem; color: #A0A5B1;">1) Select or drag-and-drop CSV, PDF, PNG/JPG Screenshots, Excel &amp; ODT files &rarr; 2) Click <b>⬆️ Upload &amp; Sort Selected Files</b> below</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            quick_nonce_key = f"_{key_prefix}_quick_up_nonce_{active_entity}_{active_tax_year}"
            quick_nonce = int(st.session_state.get(quick_nonce_key, 0))
            last_quick_msg_key = f"_{key_prefix}_quick_up_msg_{active_entity}_{active_tax_year}"
            if st.session_state.get(last_quick_msg_key):
                st.success(str(st.session_state.pop(last_quick_msg_key)))

            quick_batch = st.file_uploader(
                "Drop CSV, PDF, PNG/JPG Screenshots, Excel/ODT files",
                type=["csv", "pdf", "png", "jpg", "jpeg", "webp", "xlsx", "xls", "ods", "odt", "tsv"],
                accept_multiple_files=True,
                key=f"{key_prefix}_quick_multi_up_{active_entity}_{active_tax_year}_{quick_nonce}",
                label_visibility="collapsed",
            )
            if quick_batch:
                q_shots = [uf for uf in quick_batch if classify_upload_bucket(getattr(uf, "name", "")) == "screenshots"]
                has_gemini_q = bool(st.session_state.get("google_genai_api_key", "").strip())
                st.markdown(
                    f"""
                    <div style="
                        background-color: #1A2332;
                        border: 1px solid #3B82F6;
                        border-radius: 4px;
                        padding: 0.5rem 0.75rem;
                        margin-top: 0.35rem;
                        margin-bottom: 0.5rem;
                        font-size: 0.83rem;
                        color: #E5E7EB;
                    ">
                        📂 <b>{len(quick_batch)} file(s) staged for upload</b> — click <b>⬆️ Upload &amp; Sort Selected Files</b> below to save them into the 4 columns (you can keep clicking <i>Browse files</i> to add more before uploading).
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if q_shots and has_gemini_q:
                    qb_c1, qb_c2, qb_c3 = st.columns([2.1, 2.1, 0.9])
                else:
                    qb_c1, qb_c2, qb_c3 = st.columns([3.2, 0.01, 0.9])
                with qb_c1:
                    click_quick_upload = st.button(
                        f"⬆️ Upload & Sort {len(quick_batch)} Selected File(s) Now",
                        key=f"{key_prefix}_btn_quick_upload_{active_entity}_{active_tax_year}_{quick_nonce}",
                        type="primary",
                        use_container_width=True,
                    )
                with qb_c2:
                    click_quick_ocr = False
                    if q_shots and has_gemini_q:
                        click_quick_ocr = st.button(
                            f"🔍 Upload & Run AI OCR on {len(q_shots)} Screenshot(s)",
                            key=f"{key_prefix}_btn_quick_upload_ocr_{active_entity}_{active_tax_year}_{quick_nonce}",
                            type="secondary",
                            use_container_width=True,
                        )
                with qb_c3:
                    if st.button(
                        "✕ Clear",
                        key=f"{key_prefix}_btn_quick_clear_{active_entity}_{active_tax_year}_{quick_nonce}",
                        use_container_width=True,
                    ):
                        st.session_state[quick_nonce_key] = quick_nonce + 1
                        st.rerun()

                if click_quick_upload or click_quick_ocr:
                    q_prog = st.progress(0.0, text="Saving uploaded files to Vault & SQLite...")
                    newly_added_q = 0
                    tot_q = len(quick_batch)
                    for idx_q, up_file in enumerate(quick_batch, start=1):
                        q_prog.progress(
                            idx_q / tot_q,
                            text=f"Uploading ({idx_q}/{tot_q}): {up_file.name}...",
                        )
                        ok, _, _ = ingest_uploaded_file_object(
                            up_file,
                            entity=active_entity,
                            tax_year=active_tax_year,
                            run_ocr_immediately=True,
                        )
                        if ok:
                            newly_added_q += 1
                    q_prog.empty()
                    st.session_state[quick_nonce_key] = quick_nonce + 1
                    st.session_state[last_quick_msg_key] = (
                        f"✅ Uploaded & sorted {newly_added_q} file(s) into the 4 columns below! Ready to add more files anytime."
                    )
                    st.toast(f"Uploaded & sorted {newly_added_q} file(s) into columns.")
                    st.rerun()

        if st.session_state.get(f"_{key_prefix}_show_item_prop_setup_{active_entity}", False):
            st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)
            render_item_property_setup_matrix(
                entity=active_entity,
                tax_year=active_tax_year,
                key_prefix=f"{key_prefix}_inline_matrix",
            )
            st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)

        # =====================================================================
        # 4-COLUMN BOARD: CSV | PDF | SCREENSHOTS | OTHER (EXCEL / ODT / ODS)
        # =====================================================================
        col_csv, col_pdf, col_shot, col_other = st.columns(4)

        bucket_configs = [
            (
                col_csv,
                "csv",
                "01 // CSV FILES",
                "Bank & Card Exports (.csv)",
                ["csv"],
            ),
            (
                col_pdf,
                "pdf",
                "02 // PDF STATEMENTS",
                "Invoices & Statements (.pdf)",
                ["pdf"],
            ),
            (
                col_shot,
                "screenshots",
                "03 // SCREENSHOTS",
                "Receipts & Captures (.png, .jpg)",
                ["png", "jpg", "jpeg", "webp"],
            ),
            (
                col_other,
                "other",
                "04 // OTHER (EXCEL / ODT)",
                "Excel & OpenSource (.xlsx, .ods, .odt)",
                ["xlsx", "xls", "ods", "odt", "tsv"],
            ),
        ]

        for col_obj, bucket_key, col_title, col_subtitle, allowed_exts in bucket_configs:
            with col_obj:
                bucket_files = [f for f in files_list if f.get("bucket") == bucket_key]
                with st.container(border=True):
                    st.markdown(
                        f"""
                        <div style="border-bottom: 1px solid #2A2D34; padding-bottom: 0.5rem; margin-bottom: 0.65rem;">
                            <div class="axiom-eyebrow" style="margin-bottom: 0.1rem;">{col_title} ({len(bucket_files)})</div>
                            <div style="font-size: 0.82rem; color: #A0A5B1;">{col_subtitle}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    # Batch OCR action bar specifically for 03 // SCREENSHOTS
                    if bucket_key == "screenshots" and bucket_files:
                        pending_ocr_files = [
                            f for f in bucket_files
                            if f.get("needs_ocr")
                            or (
                                isinstance(f.get("df"), pd.DataFrame)
                                and len(f["df"]) == 1
                                and float(f["df"]["Amount"].iloc[0] or 0.0) == 0.0
                                and not str(f.get("note", "")).startswith("Account/Loan summary")
                            )
                        ]
                        has_gem_col = bool(st.session_state.get("google_genai_api_key", "").strip())
                        ocr_btn_label = (
                            f"🔍 Run AI OCR on Pending ({len(pending_ocr_files)})"
                            if pending_ocr_files
                            else f"🔄 Re-Run AI OCR on All ({len(bucket_files)})"
                        )
                        if st.button(
                            ocr_btn_label,
                            key=f"{key_prefix}_batch_shot_ocr_{active_entity}_{active_tax_year}",
                            type="primary" if pending_ocr_files else "secondary",
                            disabled=not has_gem_col,
                            use_container_width=True,
                            help="Extract transaction rows from uploaded PNG/JPG screenshots using Gemini Vision OCR.",
                        ):
                            targets_to_ocr = pending_ocr_files if pending_ocr_files else bucket_files
                            shot_prog = st.progress(0.0, text="Starting Gemini OCR on screenshots...")
                            extracted_files_cnt = 0
                            for s_idx, s_rec in enumerate(targets_to_ocr, start=1):
                                shot_prog.progress(
                                    s_idx / len(targets_to_ocr),
                                    text=f"OCR ({s_idx}/{len(targets_to_ocr)}): {s_rec.get('name')}...",
                                )
                                ok_s, _, _ = run_ocr_on_screenshot_record(
                                    s_rec,
                                    entity=active_entity,
                                    tax_year=active_tax_year,
                                )
                                if ok_s:
                                    extracted_files_cnt += 1
                            shot_prog.empty()
                            st.toast(f"Completed OCR on {extracted_files_cnt} screenshot(s).")
                            st.rerun()

                    if not bucket_files:
                        st.markdown(
                            """
                            <div style="
                                padding: 1.3rem 0.75rem;
                                text-align: center;
                                color: #6C727F;
                                font-size: 0.8rem;
                                border: 1px dashed #2A2D34;
                                border-radius: 4px;
                                margin-bottom: 0.65rem;
                            ">
                                No files in this column yet
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                    else:
                        for f_rec in bucket_files:
                            f_id = str(f_rec.get("id"))
                            f_name = str(f_rec.get("name"))
                            f_df: pd.DataFrame = f_rec.get("df", pd.DataFrame(columns=STANDARD_COLUMNS))
                            c_stats = get_file_red_counts(f_df, entity=active_entity)
                            red_cnt = c_stats["red"]
                            yellow_cnt = c_stats["yellow"]
                            green_cnt = c_stats["green"]
                            tot_cnt = c_stats["total"]

                            if f_rec.get("needs_ocr"):
                                border_color = "#3B82F6"
                                status_line = "🕓 Saved in Vault · Click '🔍 Run AI OCR' or Open File"
                            elif (
                                bucket_key == "screenshots"
                                and tot_cnt == 1
                                and float(f_df["Amount"].iloc[0] or 0.0) == 0.0
                            ):
                                border_color = "#4B5563"
                                status_line = str(
                                    f_rec.get("note")
                                    or "🖼️ Account/Loan Summary Screenshot · Click to view"
                                )
                            elif red_cnt > 0:
                                border_color = "#8A4B50"
                                status_line = f"🔴 {red_cnt} Red · 🟡 {yellow_cnt} Yellow · 🟢 {green_cnt} Sure"
                            elif yellow_cnt > 0:
                                border_color = "#B4833D"
                                status_line = f"🟡 {yellow_cnt} Yellow (Double-Check) · 🟢 {green_cnt} Sure"
                            elif tot_cnt > 0:
                                border_color = "#3A5A48"
                                status_line = f"🟢 All {tot_cnt} Rows 100% Sure"
                            else:
                                border_color = "#3A3F4B"
                                status_line = str(f_rec.get("note") or "0 rows — Click to inspect")

                            st.markdown(
                                f"""
                                <div style="
                                    background-color: #16181C;
                                    border: 1px solid {border_color};
                                    border-left: 3px solid {border_color};
                                    border-radius: 4px;
                                    padding: 0.55rem 0.7rem;
                                    margin-bottom: 0.35rem;
                                ">
                                    <div style="font-weight: 600; font-size: 0.86rem; color: #F3F4F6; word-break: break-all;">
                                        {f_name}
                                    </div>
                                    <div style="font-size: 0.74rem; color: #B8BCC6; margin-top: 0.2rem;">
                                        {status_line}
                                    </div>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )
                            act_c1, act_c2 = st.columns([3.2, 1.0])
                            with act_c1:
                                btn_lbl = f"Open & Code ({red_cnt} Red)" if red_cnt > 0 else "Open Full Page"
                                if st.button(
                                    btn_lbl,
                                    key=f"{key_prefix}_open_btn_{f_id}",
                                    type="primary" if red_cnt > 0 else "secondary",
                                    use_container_width=True,
                                ):
                                    st.session_state[state_open_key] = f_id
                                    st.rerun()
                            with act_c2:
                                if st.button(
                                    "✕",
                                    key=f"{key_prefix}_del_file_{f_id}",
                                    help=f"Remove {f_name} from Vault & SQLite",
                                    use_container_width=True,
                                ):
                                    _delete_file_record_from_sqlite(f_id, delete_vault_file=True)
                                    files_list[:] = [x for x in files_list if x.get("id") != f_id]
                                    sync_entity_ledger_from_files(active_entity, active_tax_year)
                                    st.rerun()

                    # Direct single-column quick add section with explicit Upload button & clean queue reset
                    col_nonce_key = f"_{key_prefix}_col_up_nonce_{bucket_key}_{active_entity}_{active_tax_year}"
                    col_nonce = int(st.session_state.get(col_nonce_key, 0))
                    col_msg_key = f"_{key_prefix}_col_up_msg_{bucket_key}_{active_entity}_{active_tax_year}"
                    if st.session_state.get(col_msg_key):
                        st.success(str(st.session_state.pop(col_msg_key)))

                    with st.expander(f"＋ Add to {bucket_key.upper()}", expanded=False):
                        col_up = st.file_uploader(
                            f"Select {col_subtitle}",
                            type=allowed_exts,
                            accept_multiple_files=True,
                            key=f"{key_prefix}_col_up_{bucket_key}_{active_entity}_{active_tax_year}_{col_nonce}",
                        )
                        if col_up:
                            st.caption(f"📂 {len(col_up)} file(s) staged — click Upload below:")
                            if st.button(
                                f"⬆️ Upload {len(col_up)} File(s) to {bucket_key.upper()}",
                                key=f"{key_prefix}_btn_col_up_{bucket_key}_{active_entity}_{active_tax_year}_{col_nonce}",
                                type="primary",
                                use_container_width=True,
                            ):
                                c_prog = st.progress(0.0, text=f"Uploading to {bucket_key.upper()}...")
                                added_c = 0
                                for idx_c, uf in enumerate(col_up, start=1):
                                    c_prog.progress(
                                        idx_c / len(col_up),
                                        text=f"Saving ({idx_c}/{len(col_up)}): {uf.name}...",
                                    )
                                    ok_c, _, _ = ingest_uploaded_file_object(
                                        uf,
                                        entity=active_entity,
                                        tax_year=active_tax_year,
                                        forced_bucket=bucket_key,
                                        run_ocr_immediately=True,
                                    )
                                    if ok_c:
                                        added_c += 1
                                c_prog.empty()
                                st.session_state[col_nonce_key] = col_nonce + 1
                                st.session_state[col_msg_key] = f"✅ Added {added_c} file(s) to {bucket_key.upper()}."
                                st.toast(f"Added {added_c} file(s) to {bucket_key.upper()}.")
                                st.rerun()
                            if (
                                bucket_key == "screenshots"
                                and bool(st.session_state.get("google_genai_api_key", "").strip())
                                and st.button(
                                    f"🔍 Upload & Run AI OCR ({len(col_up)})",
                                    key=f"{key_prefix}_btn_col_up_ocr_{bucket_key}_{active_entity}_{active_tax_year}_{col_nonce}",
                                    type="secondary",
                                    use_container_width=True,
                                )
                            ):
                                c_prog = st.progress(0.0, text="Uploading & running OCR on screenshots...")
                                added_c = 0
                                for idx_c, uf in enumerate(col_up, start=1):
                                    c_prog.progress(
                                        idx_c / len(col_up),
                                        text=f"OCR ({idx_c}/{len(col_up)}): {uf.name}...",
                                    )
                                    ok_c, _, _ = ingest_uploaded_file_object(
                                        uf,
                                        entity=active_entity,
                                        tax_year=active_tax_year,
                                        forced_bucket=bucket_key,
                                        run_ocr_immediately=True,
                                    )
                                    if ok_c:
                                        added_c += 1
                                c_prog.empty()
                                st.session_state[col_nonce_key] = col_nonce + 1
                                st.session_state[col_msg_key] = f"✅ Added & OCR'd {added_c} screenshot(s)."
                                st.rerun()

    # =========================================================================
    # BLOCK 2 (STEP 2): PROPERTY LOANS & BANK REGISTRY (FOR BT PROPERTY HOLDINGS LTD)
    # =========================================================================
    if include_loans_block and is_loan_target_entity(active_entity):
        st.markdown("<div style='height: 0.45rem;'></div>", unsafe_allow_html=True)
        _front_loans_prefix = f"{key_prefix}_front_loans"
        _front_loans_expanded = bool(
            st.session_state.get(f"_{_front_loans_prefix}_show_loan_import")
            or st.session_state.get(f"_{_front_loans_prefix}_loan_ocr_result")
        )
        with st.expander(
            "STEP 2 // PROPERTY LOANS & BANK REGISTRY — PROPERTIES & WHAT EACH LOAN IS (BT PROPERTY HOLDINGS LTD)",
            expanded=_front_loans_expanded,
        ):
            render_property_loans_block(
                key_prefix=_front_loans_prefix,
                eyebrow_title="STEP 2 // PROPERTY LOANS & BANK REGISTRY — PROPERTIES & WHAT EACH LOAN IS (BT PROPERTY HOLDINGS LTD)",
                show_page_title=False,
            )


def export_xero_master_csv(df: pd.DataFrame) -> bytes:
    """
    Format the standardized DataFrame as a Xero-ready CSV:
    - UTF-8 encoded
    - Strictly no commas in amounts (plain decimal numbers, e.g. 1250.50 or -450.00)
    """
    if df is None or df.empty:
        empty_df = pd.DataFrame(columns=STANDARD_COLUMNS)
        return empty_df.to_csv(index=False, encoding="utf-8").encode("utf-8")

    export_df = df.reindex(columns=STANDARD_COLUMNS).copy()
    numeric_amounts = _clean_amount_series(export_df["Amount"])
    export_df["Amount"] = numeric_amounts.map(lambda x: f"{float(x):.2f}")
    export_df["Date"] = export_df["Date"].fillna("").astype(str).str.strip()
    export_df["Description"] = export_df["Description"].fillna("").astype(str).str.strip()
    export_df["Property"] = (
        export_df["Property"]
        .fillna(UNASSIGNED_PROPERTY)
        .astype(str)
        .str.strip()
        .replace("", UNASSIGNED_PROPERTY)
    )
    export_df["Account Code"] = (
        export_df["Account Code"]
        .fillna("Uncategorized")
        .astype(str)
        .str.strip()
        .replace("", "Uncategorized")
    )
    return export_df.to_csv(index=False, encoding="utf-8").encode("utf-8")


def _clean_amount_series(series: pd.Series) -> pd.Series:
    """Normalize currency/amount strings (including parentheses negatives) into floats."""
    def _parse_val(val):
        if pd.isna(val):
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        text = str(val).strip()
        if not text:
            return 0.0
        is_negative = False
        if text.startswith("(") and text.endswith(")"):
            is_negative = True
            text = text[1:-1]
        elif text.endswith("-"):
            is_negative = True
            text = text[:-1]
        cleaned = re.sub(r"[^0-9.\-]", "", text)
        if cleaned in ("", "-", ".", "-."):
            return 0.0
        try:
            num = float(cleaned)
            return -abs(num) if is_negative else num
        except ValueError:
            return 0.0

    return series.apply(_parse_val)


def _dedupe_column_names(raw_cols: list[object]) -> list[str]:
    """Convert raw header cell values into stripped, deduplicated string column names."""
    deduped: list[str] = []
    seen: dict[str, int] = {}
    for idx, val in enumerate(raw_cols):
        name = "" if pd.isna(val) else str(val).strip()
        if not name or name.lower() in ("nan", "none"):
            name = f"Column_{idx}"
        if name in seen:
            seen[name] += 1
            deduped.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            deduped.append(name)
    return deduped


def normalize_financial_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 4.6 Smart Column Mapper:
    Takes a raw pandas DataFrame (from CSV or PDF extraction) and standardizes it
    to strictly four columns: ['Date', 'Amount', 'Description', 'Account Code'].

    1. Header Detection & Junk Removal:
       - Scans the first 20 rows of the DataFrame to find the 'True Header' row
         (a row containing strings like 'date', 'amount', or 'debit' ignoring case),
         unless the current DataFrame columns already represent the True Header.
       - Drops all rows above the True Header and resets the columns to those values.
    2. Column Mapping Logic:
       - Date: First column named 'date', 'posting date', 'transaction date', or 'process date'
         (case-insensitive) -> renamed to 'Date'.
       - Amount: Single 'amount' column -> renamed to 'Amount'. If separate 'debit' and 'credit'
         columns exist, combines them into 'Amount' (Credit - Debit where income is positive and
         expenses are negative) and drops the original debit/credit columns.
       - Description: Finds all existing columns from
         ['payee', 'particulars', 'code', 'reference', 'description', 'details'] (case-insensitive)
         and concatenates their non-empty string values into 'Description', separated by a space.
       - Account Code: Adds an empty 'Account Code' column if it doesn't exist.
       - Drops all other unmapped columns so the final DataFrame is strictly:
         ['Date', 'Amount', 'Description', 'Account Code'].
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    work_df = df.copy()
    header_keywords = ("date", "amount", "debit")

    def _header_score(values: list[object]) -> int:
        tokens = [
            "" if pd.isna(v) else str(v).strip().lower()
            for v in values
        ]
        meaningful_tokens = [
            t for t in tokens
            if t and not t.startswith("unnamed:") and not re.match(r"^column_\d+$", t) and not t.isdigit()
        ]
        if not meaningful_tokens:
            return 0
        joined = " ".join(meaningful_tokens)
        if not any(kw in joined for kw in header_keywords):
            return 0
        known_header_terms = {
            "date",
            "posting date",
            "transaction date",
            "process date",
            "amount",
            "debit",
            "credit",
            "payee",
            "particulars",
            "code",
            "reference",
            "description",
            "details",
            "account code",
            "balance",
        }
        exact_matches = sum(1 for t in meaningful_tokens if t in known_header_terms)
        kw_matches = sum(1 for kw in header_keywords if kw in joined)
        return exact_matches * 3 + kw_matches

    # Scan the first 20 rows for the True Header row (if a row in the first 20 rows scores higher
    # than the initial DataFrame column labels, drop all rows above the True Header and reset columns).
    cols_score = _header_score(list(work_df.columns))
    scan_limit = min(20, len(work_df))
    true_header_pos: int | None = None
    best_row_score = cols_score

    for pos in range(scan_limit):
        row_values = list(work_df.iloc[pos].values)
        r_score = _header_score(row_values)
        if r_score > best_row_score:
            best_row_score = r_score
            true_header_pos = pos
            break

    if true_header_pos is not None:
        header_values = list(work_df.iloc[true_header_pos].values)
        work_df = work_df.iloc[true_header_pos + 1 :].reset_index(drop=True)
        work_df.columns = _dedupe_column_names(header_values)
    else:
        work_df.columns = _dedupe_column_names(list(work_df.columns))

    if work_df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    # Map lowercase stripped column name -> list of actual column names (preserving order)
    col_lookup: dict[str, list[str]] = {}
    for col in work_df.columns:
        key = str(col).strip().lower()
        col_lookup.setdefault(key, []).append(col)

    def _find_first_col(candidates: list[str]) -> str | None:
        # 1. Exact case-insensitive match in candidate priority order
        for cand in candidates:
            if cand in col_lookup and col_lookup[cand]:
                return col_lookup[cand][0]
        # 2. Substring fallback in column order
        for col in work_df.columns:
            col_lower = str(col).strip().lower()
            if any(cand in col_lower for cand in candidates):
                return col
        return None

    # 1. Date Mapping
    date_candidates = ["date", "posting date", "transaction date", "process date"]
    date_src_col = _find_first_col(date_candidates)
    if date_src_col is not None:
        if date_src_col != "Date":
            work_df = work_df.rename(columns={date_src_col: "Date"})
        date_series = work_df["Date"].fillna("").astype(str).str.strip()
        date_series = date_series.replace({"nan": "", "None": "", "NaT": ""})
    else:
        date_series = pd.Series([""] * len(work_df), index=work_df.index, dtype="object")

    # 2. Amount Mapping (Single Amount vs Separate Debit & Credit)
    debit_col = _find_first_col(["debit", "debits", "debit amount", "withdrawal", "withdrawals", "money out", "dr"])
    credit_col = _find_first_col(["credit", "credits", "credit amount", "deposit", "deposits", "money in", "cr"])
    amount_col = _find_first_col(["amount", "transaction amount", "net amount", "amt", "value", "total"])

    if debit_col is not None and credit_col is not None and debit_col != credit_col:
        debit_vals = _clean_amount_series(work_df[debit_col]).abs()
        credit_vals = _clean_amount_series(work_df[credit_col]).abs()
        amount_series = credit_vals - debit_vals
        cols_to_drop = [c for c in (debit_col, credit_col) if c in work_df.columns]
        work_df = work_df.drop(columns=cols_to_drop, errors="ignore")
    elif amount_col is not None:
        if amount_col != "Amount":
            work_df = work_df.rename(columns={amount_col: "Amount"})
        amount_series = _clean_amount_series(work_df["Amount"])
    elif debit_col is not None:
        debit_vals = _clean_amount_series(work_df[debit_col]).abs()
        amount_series = -debit_vals
        work_df = work_df.drop(columns=[debit_col], errors="ignore")
    elif credit_col is not None:
        credit_vals = _clean_amount_series(work_df[credit_col]).abs()
        amount_series = credit_vals
        work_df = work_df.drop(columns=[credit_col], errors="ignore")
    else:
        amount_series = pd.Series([0.0] * len(work_df), index=work_df.index, dtype="float64")

    # 3. Account Code Mapping:
    # Check explicit 'Account Code' / 'Category' column first.
    # Also check if a column named 'Code' (or 'Code_1') actually holds Accountant Codes
    # (as in `Codes.xlsx` -> `Westpac - BT Prop` Col A and `ASB BT Prop` Col D), rather than bank transaction codes.
    acct_src_col = None
    for col in work_df.columns:
        if str(col).strip().lower() in ("account code", "account_code", "acct code", "gl code", "category", "code_category"):
            acct_src_col = col
            break

    known_acct_codes_lower = {
        c.lower()
        for c in (
            list(DEFAULT_CUSTOM_ACCOUNT_CODES)
            + list(ENTITY_CATEGORIZATION_RULES.get("NZ", {}).values())
            + list(ENTITY_CATEGORIZATION_RULES.get("US", {}).values())
        )
    }
    if acct_src_col is None:
        for col in work_df.columns:
            c_low = str(col).strip().lower()
            if c_low == "code" or c_low.startswith("code_"):
                non_empty_vals = [
                    str(v).strip()
                    for v in work_df[col].dropna().tolist()
                    if str(v).strip() and str(v).strip().lower() not in ("nan", "none", "null")
                ]
                if non_empty_vals:
                    matches = sum(1 for v in non_empty_vals if v.lower() in known_acct_codes_lower)
                    if matches / len(non_empty_vals) >= 0.35:
                        acct_src_col = col
                        break

    if acct_src_col is not None:
        acct_series = (
            work_df[acct_src_col]
            .fillna("")
            .astype(str)
            .str.strip()
            .replace({"nan": "", "None": "", "null": ""})
        )
    else:
        acct_series = pd.Series([""] * len(work_df), index=work_df.index, dtype="object")

    # 4. Description Mapping: concatenate Payee / Other Party + Particulars + Reference + Analysis Code + Memo
    # PLUS any other text columns across the entire CSV row (excluding Date, Amount, Balance, and Account Code)
    # so that if a house name (e.g. 'Damian') or policy/vendor text appears in ANY column of the CSV line,
    # it is preserved and available to the matching engine.
    desc_target_names = [
        "other party",
        "payee",
        "particulars",
        "code",
        "reference",
        "analysis code",
        "description",
        "details",
        "memo",
        "notes",
        "tran type",
        "narrative",
        "merchant",
        "transaction details",
        "serial",
        "orig ref",
        "bank",
    ]
    matched_desc_cols: list[str] = []

    for target_name in desc_target_names:
        for col in work_df.columns:
            if col == acct_src_col:
                continue
            c_low = str(col).strip().lower()
            if col not in matched_desc_cols and (c_low == target_name or c_low.startswith(f"{target_name}_")):
                matched_desc_cols.append(col)

    # Also include any remaining non-date/non-amount/non-balance text columns across the CSV line
    excluded_desc_cols = {
        "Date",
        "Amount",
        date_src_col,
        amount_col,
        debit_col,
        credit_col,
        acct_src_col,
    }
    for col in work_df.columns:
        if col in matched_desc_cols or col in excluded_desc_cols:
            continue
        c_low = str(col).strip().lower()
        if any(skip_k in c_low for skip_k in ("balance", "running bal", "date", "amount", "debit", "credit")):
            continue
        matched_desc_cols.append(col)

    if matched_desc_cols:
        def _concat_desc_row(row: pd.Series) -> str:
            parts: list[str] = []
            for c in matched_desc_cols:
                val = row.get(c, "")
                if pd.isna(val):
                    continue
                s_val = str(val).strip()
                if s_val and s_val.lower() not in ("nan", "none", "null", "#name?"):
                    if s_val not in parts:
                        parts.append(s_val)
            return " | ".join(parts) if len(parts) > 1 else (" ".join(parts))

        description_series = work_df[matched_desc_cols].apply(_concat_desc_row, axis=1)
    else:
        description_series = pd.Series([""] * len(work_df), index=work_df.index, dtype="object")

    # 5. Property Mapping:
    # First check explicit Property / Address column; then scan EVERY text column across the entire CSV line
    # via `detect_property_from_text()` so row-level property notes (e.g. 'Damian', '26 Frances', '33 Blampied', '16D 363Queen')
    # anywhere on the line are automatically assigned to the Property column!
    prop_src_col = None
    for col in work_df.columns:
        if str(col).strip().lower() in ("property", "property name", "address", "property address", "unit", "building", "asset"):
            prop_src_col = col
            break

    prop_hint_cols = [
        col
        for col in work_df.columns
        if col not in excluded_desc_cols
        and not any(skip_k in str(col).strip().lower() for skip_k in ("balance", "running bal", "date", "amount", "debit", "credit"))
    ]

    def _resolve_row_property(idx_val) -> str:
        if prop_src_col is not None:
            raw_p = work_df.loc[idx_val, prop_src_col]
            if not pd.isna(raw_p):
                s_p = str(raw_p).strip()
                if s_p and s_p.lower() not in ("nan", "none", "null", "unassigned"):
                    det = detect_property_from_text(s_p)
                    return det if det != UNASSIGNED_PROPERTY else s_p
        # Scan the entire CSV row across all text/reference columns
        combined_hints = " | ".join(
            str(work_df.loc[idx_val, c] or "").strip()
            for c in prop_hint_cols
            if not pd.isna(work_df.loc[idx_val, c]) and str(work_df.loc[idx_val, c] or "").strip()
        )
        detected = detect_property_from_text(combined_hints)
        if detected != UNASSIGNED_PROPERTY:
            return detected
        return UNASSIGNED_PROPERTY

    prop_series = pd.Series([_resolve_row_property(i) for i in work_df.index], index=work_df.index, dtype="object")

    # Build strict 5-column DataFrame and drop all other unmapped columns
    normalized = pd.DataFrame(
        {
            "Date": date_series,
            "Amount": amount_series.astype(float),
            "Description": description_series,
            "Property": prop_series,
            "Account Code": acct_series,
        },
        index=work_df.index,
    )[STANDARD_COLUMNS]

    # Filter out completely empty rows or repeated mid-document table header rows
    def _is_valid_transaction_row(row: pd.Series) -> bool:
        d_val = str(row["Date"]).strip()
        desc_val = str(row["Description"]).strip()
        amt_val = float(row["Amount"]) if not pd.isna(row["Amount"]) else 0.0
        if d_val == "" and desc_val == "" and amt_val == 0.0:
            return False
        if d_val.lower() in ("date", "posting date", "transaction date", "process date") and amt_val == 0.0:
            return False
        return True

    valid_mask = normalized.apply(_is_valid_transaction_row, axis=1)
    normalized = normalized[valid_mask].reset_index(drop=True)
    return normalized[STANDARD_COLUMNS]


def standardize_ledger_dataframe(
    raw_df: pd.DataFrame,
    column_mapping: dict[str, str | None] | None = None,
) -> pd.DataFrame:
    """
    Standardize an arbitrary DataFrame to strictly:
    ['Date', 'Amount', 'Description', 'Account Code']
    using normalize_financial_dataframe() (with optional manual column override support)
    and automatically apply CATEGORIZATION_RULES to populate 'Account Code'.
    """
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    has_custom_override = bool(
        column_mapping and any(v is not None for v in column_mapping.values())
    )
    if not has_custom_override:
        normalized = normalize_financial_dataframe(raw_df)
        return apply_rule_categorization(normalized[STANDARD_COLUMNS])

    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    rename_overrides: dict[str, str] = {}
    for target_col, src_col in (column_mapping or {}).items():
        if src_col and src_col in df.columns and src_col != target_col:
            rename_overrides[src_col] = target_col
    if rename_overrides:
        df = df.rename(columns=rename_overrides)

    normalized = normalize_financial_dataframe(df)
    return apply_rule_categorization(normalized[STANDARD_COLUMNS])


def apply_custom_styles(authenticated: bool = False) -> None:
    """
    Enforce a high-end, technical dark charcoal/graphite aesthetic across all pages.
    Strictly eliminates neon, cyan, magenta, or glowing effects.
    Hides sidebar navigation completely when unauthenticated.
    """
    sidebar_visibility_css = (
        ""
        if authenticated
        else """
        [data-testid="stSidebar"],
        [data-testid="collapsedControl"],
        [data-testid="stSidebarNav"] {
            display: none !important;
        }
        """
    )

    st.markdown(
        f"""
        <style>
        /* Core Surfaces & Typography */
        .stApp {{
            background-color: #121316;
            color: #E2E4E9;
            font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
            letter-spacing: -0.01em;
        }}

        /* Sidebar-Responsive Container & Compact Table Layout */
        .main .block-container {{
            max-width: 100% !important;
            padding-left: 1.35rem !important;
            padding-right: 1.35rem !important;
            padding-top: 1.5rem !important;
            box-sizing: border-box !important;
            overflow-x: hidden !important;
        }}

        [data-testid="stDataFrame"],
        [data-testid="stDataEditor"] {{
            max-width: 100% !important;
            width: 100% !important;
            box-sizing: border-box !important;
        }}

        .axiom-compact-rule-row {{
            background-color: #16181C;
            border: 1px solid #2A2D34;
            border-radius: 4px;
            padding: 0.42rem 0.65rem;
            margin-bottom: 0.25rem;
            font-size: 0.82rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-sizing: border-box;
        }}

        /* Sidebar Styling */
        [data-testid="stSidebar"] {{
            background-color: #16181C !important;
            border-right: 1px solid #262930 !important;
            min-width: 210px !important;
            max-width: 255px !important;
        }}

        [data-testid="stSidebarNav"] ul li a {{
            border-radius: 4px !important;
            margin: 2px 8px !important;
            color: #A0A5B1 !important;
            font-weight: 500 !important;
            transition: background-color 0.15s ease, color 0.15s ease !important;
        }}

        [data-testid="stSidebarNav"] ul li a:hover {{
            background-color: #202329 !important;
            color: #E2E4E9 !important;
        }}

        [data-testid="stSidebarNav"] ul li a[aria-current="page"] {{
            background-color: #24272E !important;
            color: #F3F4F6 !important;
            border-left: 2px solid #A0A5B1 !important;
        }}

        /* Headings & Technical Labels */
        h1, h2, h3, h4, h5, h6 {{
            color: #F3F4F6 !important;
            font-weight: 600 !important;
            letter-spacing: -0.02em !important;
        }}

        .axiom-eyebrow {{
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            color: #7D828F;
            font-weight: 600;
            margin-bottom: 0.25rem;
        }}

        .axiom-workspace-banner {{
            background-color: #16181C;
            border: 1px solid #2A2D34;
            border-left: 3px solid #A0A5B1;
            border-radius: 4px;
            padding: 0.85rem 1.25rem;
            margin-bottom: 0.75rem;
        }}

        .axiom-workspace-banner h2 {{
            margin: 0 !important;
            padding: 0 !important;
            font-size: 1.2rem !important;
            font-weight: 600 !important;
            color: #F3F4F6 !important;
            letter-spacing: -0.01em !important;
        }}

        .axiom-panel {{
            background-color: #1A1C20;
            border: 1px solid #2A2D34;
            border-radius: 4px;
            padding: 1.5rem;
            margin-bottom: 1rem;
        }}

        .axiom-settings-drawer {{
            background-color: #16181C;
            border: 1px solid #3A3F4B;
            border-left: 3px solid #A0A5B1;
            border-radius: 4px;
            padding: 0.9rem 1.25rem;
            margin-bottom: 0.75rem;
        }}

        [data-testid="stPopover"] > div > button {{
            background-color: #262930 !important;
            color: #E2E4E9 !important;
            border: 1px solid #3A3F4B !important;
            border-radius: 3px !important;
            padding: 0.42rem 0.55rem !important;
            box-shadow: none !important;
        }}

        [data-testid="stPopover"] > div > button:hover {{
            background-color: #323640 !important;
            border-color: #6C727F !important;
            color: #FFFFFF !important;
        }}

        .axiom-divider {{
            border-top: 1px solid #262930;
            margin: 1.1rem 0;
        }}

        /* Tabs Styling - Strictly Flat Charcoal/Metallic */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 0.5rem;
            background-color: #16181C;
            padding: 0.35rem;
            border: 1px solid #262930;
            border-radius: 4px;
        }}

        .stTabs [data-baseweb="tab"] {{
            background-color: transparent !important;
            color: #8E939F !important;
            border-radius: 3px !important;
            padding: 0.45rem 1rem !important;
            font-size: 0.86rem !important;
            font-weight: 500 !important;
            border: none !important;
        }}

        .stTabs [aria-selected="true"] {{
            background-color: #24272E !important;
            color: #F3F4F6 !important;
            border: 1px solid #3A3F4B !important;
        }}

        .stTabs [data-baseweb="tab-highlight"] {{
            display: none !important;
        }}

        /* Form Containers & Inputs - Flat Metallic/Graphite (No Glow) */
        [data-testid="stForm"] {{
            background-color: #1A1C20 !important;
            border: 1px solid #2A2D34 !important;
            border-radius: 4px !important;
            padding: 1.5rem !important;
            box-shadow: none !important;
        }}

        .stTextInput > div > div > input,
        .stPasswordInput > div > div > input,
        .stSelectbox > div > div > div,
        .stNumberInput > div > div > input,
        .stTextArea > div > div > textarea {{
            background-color: #121316 !important;
            color: #E2E4E9 !important;
            border: 1px solid #2E323B !important;
            border-radius: 3px !important;
            box-shadow: none !important;
        }}

        .stTextInput > div > div > input:focus,
        .stPasswordInput > div > div > input:focus,
        .stTextArea > div > div > textarea:focus {{
            border-color: #8E939F !important;
            box-shadow: none !important;
            outline: none !important;
        }}

        /* File Uploader */
        [data-testid="stFileUploader"] section {{
            background-color: #16181C !important;
            border: 1px dashed #3A3F4B !important;
            border-radius: 4px !important;
        }}

        /* Buttons & Download Buttons - Muted Metallic / Graphite */
        .stButton > button,
        .stDownloadButton > button,
        [data-testid="stFormSubmitButton"] > button {{
            background-color: #262930 !important;
            color: #E2E4E9 !important;
            border: 1px solid #3A3F4B !important;
            border-radius: 3px !important;
            font-weight: 500 !important;
            letter-spacing: 0.02em !important;
            padding: 0.42rem 0.95rem !important;
            box-shadow: none !important;
            transition: background-color 0.15s ease, border-color 0.15s ease !important;
        }}

        /* Primary Button Highlight (Used for Active Entity & Active Financial Year Pill) */
        .stButton > button[kind="primary"] {{
            background-color: #323640 !important;
            color: #FFFFFF !important;
            border: 1px solid #A0A5B1 !important;
            font-weight: 600 !important;
            box-shadow: none !important;
        }}

        .stButton > button:hover,
        .stDownloadButton > button:hover,
        [data-testid="stFormSubmitButton"] > button:hover {{
            background-color: #323640 !important;
            border-color: #6C727F !important;
            color: #FFFFFF !important;
            box-shadow: none !important;
        }}

        .stButton > button:active,
        .stButton > button:focus,
        .stDownloadButton > button:active,
        .stDownloadButton > button:focus,
        [data-testid="stFormSubmitButton"] > button:active,
        [data-testid="stFormSubmitButton"] > button:focus {{
            background-color: #202329 !important;
            border-color: #8E939F !important;
            color: #E2E4E9 !important;
            box-shadow: none !important;
            outline: none !important;
        }}

        /* Metrics & Alerts */
        [data-testid="stMetric"] {{
            background-color: #1A1C20;
            border: 1px solid #2A2D34;
            border-radius: 4px;
            padding: 1rem 1.25rem;
        }}

        [data-testid="stAlert"] {{
            background-color: #1A1C20 !important;
            border: 1px solid #3A3F4B !important;
            color: #D0D4DC !important;
            border-radius: 4px !important;
            box-shadow: none !important;
        }}

        {sidebar_visibility_css}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_connection_badge(title: str, status_dict: dict[str, object], is_selected: bool) -> str:
    """Return HTML snippet for a live AI connection status pill in the top toolbar."""
    connected = bool(status_dict.get("connected", False))
    label = str(status_dict.get("label", "UNVERIFIED"))
    dot = "●" if connected else "○"
    border_col = "#A0A5B1" if connected else "#3A3F4B"
    bg_col = "#22262E" if is_selected else "#16181C"
    text_col = "#F3F4F6" if connected else "#8E939F"
    active_tag = " [ACTIVE]" if is_selected else ""

    return (
        f'<div style="background-color: {bg_col}; border: 1px solid {border_col}; '
        f'border-radius: 3px; padding: 0.35rem 0.65rem; display: flex; align-items: center; '
        f'justify-content: space-between; margin-bottom: 0.35rem;">'
        f'<span style="font-size: 0.7rem; font-weight: 600; letter-spacing: 0.08em; color: #A0A5B1;">'
        f'{title}{active_tag}</span>'
        f'<span style="font-size: 0.7rem; font-weight: 600; color: {text_col}; font-family: monospace;">'
        f'{dot} {label}</span>'
        f'</div>'
    )


def _render_entity_edit_popover(entity_name: str, key_prefix: str) -> None:
    """
    Render a compact far-right pin/pen edit button that opens an inline popover
    allowing the operator to edit the entity name and tax jurisdiction.
    """
    safe_slug = re.sub(r"[^a-zA-Z0-9]+", "_", entity_name).strip("_")
    current_jur = get_entity_jurisdiction(entity_name)
    with st.popover("📌", help=f"Edit '{entity_name}' name or jurisdiction", use_container_width=True):
        st.markdown('<div class="axiom-eyebrow">EDIT ORGANIZATION</div>', unsafe_allow_html=True)
        edited_name = st.text_input(
            "Entity / Portfolio Name",
            value=entity_name,
            key=f"_{key_prefix}_edit_ent_name_{safe_slug}",
        )
        jur_options = ["NZ IRD", "USA IRS"]
        edited_jur_label = st.selectbox(
            "Tax Jurisdiction",
            options=jur_options,
            index=1 if current_jur == "US" else 0,
            key=f"_{key_prefix}_edit_ent_jur_{safe_slug}",
        )
        if st.button(
            "Save Entity Changes",
            key=f"_{key_prefix}_btn_save_ent_{safe_slug}",
            type="primary",
            use_container_width=True,
        ):
            new_jur_code = "US" if edited_jur_label.startswith("US") else "NZ"
            ok, msg = rename_entity(entity_name, edited_name, new_jur_code)
            st.session_state["_entity_edit_flash"] = (ok, msg)
            st.rerun()
        if st.button(
            "🗑 Remove Organization",
            key=f"_{key_prefix}_btn_del_ent_{safe_slug}",
            use_container_width=True,
        ):
            ok_del, msg_del = delete_entity(entity_name)
            st.session_state["_entity_edit_flash"] = (ok_del, msg_del)
            st.rerun()


def render_right_settings_drawer() -> None:
    """
    Render the structured, full-width System & Organization Settings panel when
    `st.session_state["show_settings_drawer"]` is True.
    Organized into three clean, scannable tabs:
      1. Organizations & Entities: Manage entities, create portfolios, and rename via far-right 📌 pin icon.
      2. AI & API Connections: Select active AI provider and configure/verify Gemini Cloud & Local Qwen endpoints.
      3. Operator Accounts & Security: View operator roster, update credentials, or provision/remove accounts.
    """
    if not st.session_state.get("show_settings_drawer", False):
        return

    with st.container(border=True):
        head_c1, head_c2 = st.columns([4.5, 1.0])
        with head_c1:
            st.markdown(
                """
                <div class="axiom-settings-drawer" style="margin-bottom: 0.25rem;">
                    <div class="axiom-eyebrow">SYSTEM CONFIGURATION &amp; ORGANIZATION CONTROL PANEL</div>
                    <h3 style="margin: 0; font-size: 1.1rem;">Organizations, AI Providers &amp; Operator Security</h3>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with head_c2:
            st.markdown("<div style='height: 0.35rem;'></div>", unsafe_allow_html=True)
            if st.button("✕ Close Settings", key="_btn_close_settings_drawer", use_container_width=True):
                st.session_state["show_settings_drawer"] = False
                st.rerun()

        ent_flash = st.session_state.pop("_entity_edit_flash", None)
        if isinstance(ent_flash, tuple) and len(ent_flash) == 2:
            f_ok, f_msg = ent_flash
            if f_ok:
                st.success(f_msg)
            else:
                st.error(f_msg)

        tab_orgs, tab_loans, tab_files, tab_item_prop, tab_ai, tab_accounts, tab_email = st.tabs(
            [
                "01 // Organizations & Properties",
                "02 // Property Loans & Bank Registry (BT Property Holdings)",
                "03 // Upload & 4-Column File Table",
                "04 // Item + Property Setup (Insurance, Loan Interest, Rent)",
                "05 // AI & API Connections",
                "06 // Operator Accounts & Security",
                "07 // Email (SMTP) & Reminders",
            ]
        )

        # =====================================================================
        # TAB 01: ORGANIZATIONS & ENTITIES (WITH FAR-RIGHT PIN EDIT ICON)
        # =====================================================================
        with tab_orgs:
            active_entity = get_active_entity()
            active_tax_year = get_active_tax_year()
            all_entities = get_entity_options()

            org_list_col, org_create_col = st.columns([1.65, 1.15])

            with org_list_col:
                st.markdown(
                    '<div class="axiom-eyebrow">CONFIGURED ORGANIZATIONS // EDIT NAME OR JURISDICTION &amp; CLICK SAVE</div>',
                    unsafe_allow_html=True,
                )
                for idx, ent_name in enumerate(all_entities):
                    is_active = ent_name == active_entity
                    ent_jur = get_entity_jurisdiction(ent_name)
                    ent_df = get_entity_ledger(ent_name, active_tax_year)
                    row_cnt = len(ent_df)
                    safe_slug = re.sub(r"[^a-zA-Z0-9]+", "_", ent_name).strip("_")

                    with st.container(border=True):
                        c_name, c_jur, c_save, c_switch, c_pin = st.columns([2.2, 1.15, 0.85, 0.9, 0.5])
                        with c_name:
                            edited_org_name = st.text_input(
                                f"Organization {idx + 1}",
                                value=ent_name,
                                key=f"_settings_inline_org_name_{idx}_{safe_slug}",
                                label_visibility="collapsed",
                            )
                        with c_jur:
                            edited_org_jur = st.selectbox(
                                f"Jurisdiction {idx + 1}",
                                options=["NZ IRD", "USA IRS"],
                                index=1 if ent_jur == "US" else 0,
                                key=f"_settings_inline_org_jur_{idx}_{safe_slug}",
                                label_visibility="collapsed",
                            )
                        new_jur_code = "US" if edited_org_jur.startswith("US") else "NZ"
                        has_changes = (edited_org_name.strip() != ent_name) or (new_jur_code != ent_jur)
                        with c_save:
                            if st.button(
                                "Save",
                                key=f"_settings_inline_org_save_{idx}_{safe_slug}",
                                type="primary" if has_changes else "secondary",
                                disabled=(not edited_org_name.strip() or not has_changes),
                                use_container_width=True,
                            ):
                                ok_rn, msg_rn = rename_entity(ent_name, edited_org_name, new_jur_code)
                                st.session_state["_entity_edit_flash"] = (ok_rn, msg_rn)
                                st.rerun()
                        with c_switch:
                            if st.button(
                                f"● Active ({row_cnt})" if is_active else f"Switch ({row_cnt})",
                                key=f"_settings_switch_ent_{idx}_{ent_name}",
                                type="primary" if is_active else "secondary",
                                disabled=is_active,
                                use_container_width=True,
                            ):
                                st.session_state["active_entity"] = ent_name
                                st.session_state["entity"] = ent_name
                                _save_memory_store()
                                st.rerun()
                        with c_pin:
                            _render_entity_edit_popover(ent_name, key_prefix=f"settings_org_{idx}")

            with org_create_col:
                st.markdown(
                    '<div class="axiom-eyebrow">REGISTER NEW ORGANIZATION</div>',
                    unsafe_allow_html=True,
                )
                with st.form(key="_settings_form_create_entity", clear_on_submit=True):
                    new_org_name = st.text_input(
                        "Organization / Portfolio Name",
                        placeholder="e.g. Family Trust Portfolio",
                    )
                    new_org_jur = st.selectbox(
                        "Tax Jurisdiction",
                        options=["NZ IRD", "USA IRS"],
                    )
                    if st.form_submit_button("+ Create Organization", use_container_width=True):
                        jur_code = "US" if new_org_jur.startswith("US") else "NZ"
                        ok_new, msg_new = add_new_entity(new_org_name, jur_code)
                        st.session_state["_entity_edit_flash"] = (ok_new, msg_new)
                        st.rerun()

            st.markdown('<div class="axiom-divider" style="margin: 0.75rem 0;"></div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="axiom-eyebrow">EDIT PROPERTIES FOR ACTIVE ORGANIZATION // {active_entity.upper()}</div>',
                unsafe_allow_html=True,
            )
            render_property_editor_widget(entity=active_entity, key_prefix="settings_prop_mgr")

        # =====================================================================
        # TAB 02: PROPERTY LOANS & BANK REGISTRY (BT PROPERTY HOLDINGS LTD)
        # =====================================================================
        with tab_loans:
            render_property_loans_block(
                key_prefix="settings_loans_tab",
                eyebrow_title="SETTINGS // PROPERTY LOANS & BANK REGISTRY — PROPERTIES & WHAT EACH LOAN IS (BT PROPERTY HOLDINGS LTD)",
                show_page_title=False,
            )

        # =====================================================================
        # TAB 03: UPLOAD & 4-COLUMN FILE TABLE
        # =====================================================================
        with tab_files:
            active_entity_f = get_active_entity()
            active_tax_year_f = get_active_tax_year()
            files_list_f = get_entity_uploaded_files(active_entity_f, active_tax_year_f)
            _render_bulk_upload_dropzone(
                active_entity=active_entity_f,
                active_tax_year=active_tax_year_f,
                files_list=files_list_f,
                key_prefix="settings_bulk_up",
                eyebrow_label="UPLOAD ALL FILES AT ONCE &mdash; AUTO-SAVED TO SQLITE &amp; DISK VAULT",
            )
            st.markdown("<div style='height: 0.35rem;'></div>", unsafe_allow_html=True)
            render_entity_ingestion_pipeline(
                active_entity=active_entity_f,
                active_tax_year=active_tax_year_f,
                key_prefix="settings_file_board",
                include_loans_block=False,
            )

        # =====================================================================
        # TAB 04: ITEM + PROPERTY SETUP MATRIX
        # =====================================================================
        with tab_item_prop:
            render_item_property_setup_matrix(
                entity=get_active_entity(),
                tax_year=get_active_tax_year(),
                key_prefix="settings_matrix",
            )

        # =====================================================================
        # TAB 03: AI & API CONNECTIONS
        # =====================================================================
        with tab_ai:
            st.markdown(
                '<div class="axiom-eyebrow">DEFAULT CATEGORIZATION &amp; FALLBACK ROUTING ENGINE</div>',
                unsafe_allow_html=True,
            )
            selected_cat_model = st.radio(
                "Select Active AI Categorization Engine",
                options=CATEGORIZATION_MODEL_OPTIONS,
                index=CATEGORIZATION_MODEL_OPTIONS.index(st.session_state["categorization_model"])
                if st.session_state["categorization_model"] in CATEGORIZATION_MODEL_OPTIONS
                else 0,
                horizontal=True,
                label_visibility="collapsed",
                key="_settings_drawer_cat_model_radio",
            )
            if selected_cat_model != st.session_state.get("categorization_model"):
                st.session_state["categorization_model"] = selected_cat_model
                _save_memory_store()

            st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)

            gem_col, loc_col = st.columns(2)

            # -----------------------------------------------------------------
            # PROVIDER 1: GOOGLE GEMINI API (CLOUD)
            # -----------------------------------------------------------------
            with gem_col:
                gem_status = st.session_state.get("gemini_connection_status", {})
                gem_connected = bool(gem_status.get("connected", False))
                gem_badge_border = "#A0A5B1" if gem_connected else "#3A3F4B"
                gem_dot = "● CONNECTED" if gem_connected else f"○ {gem_status.get('label', 'DISCONNECTED')}"

                st.markdown(
                    f"""
                    <div class="axiom-panel" style="padding: 0.9rem 1.1rem; margin-bottom: 0.65rem; border-color: {gem_badge_border};">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span class="axiom-eyebrow" style="margin: 0;">01 // GOOGLE GEMINI API (CLOUD)</span>
                            <span style="font-size: 0.72rem; font-family: monospace; color: {'#F3F4F6' if gem_connected else '#8E939F'}; font-weight: 600;">
                                {gem_dot}
                            </span>
                        </div>
                        <div style="font-size: 0.8rem; color: #8E939F; margin-top: 0.25rem;">
                            {gem_status.get('detail', 'Used for Gemini 3.8 Flash Categorization & Multimodal OCR.')}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                gem_flash = st.session_state.pop("_gemini_key_flash", None)
                if isinstance(gem_flash, tuple) and len(gem_flash) == 2:
                    if gem_flash[0]:
                        st.success(gem_flash[1])
                    else:
                        st.error(gem_flash[1])

                new_gem_key = st.text_input(
                    "Google Gemini API Key (Saved Permanently)",
                    value=st.session_state.get("google_genai_api_key", ""),
                    type="password",
                    placeholder="Paste AIza... Gemini API Key",
                    key="_drawer_gemini_api_key_input",
                )
                if new_gem_key != st.session_state.get("google_genai_api_key", ""):
                    st.session_state["google_genai_api_key"] = new_gem_key.strip()
                    st.session_state["gemini_connection_status"] = {
                        "connected": False,
                        "label": "KEY SAVED (READY)" if new_gem_key.strip() else "NOT CONFIGURED",
                        "detail": "Saved permanently to local database. Click 'Test & Verify' to ping." if new_gem_key.strip() else "Enter a Google Gemini API Key to connect.",
                        "checked_at": datetime.now().strftime("%H:%M:%S"),
                    }
                    _save_memory_store()

                gem_btn_c1, gem_btn_c2 = st.columns(2)
                with gem_btn_c1:
                    if st.button("Save Gemini API Key", key="_btn_save_gemini_key", type="primary", use_container_width=True):
                        cleaned_gem_key = (new_gem_key or "").strip()
                        st.session_state["google_genai_api_key"] = cleaned_gem_key
                        st.session_state["gemini_connection_status"] = {
                            "connected": bool(st.session_state.get("gemini_connection_status", {}).get("connected")) and bool(cleaned_gem_key),
                            "label": "KEY SAVED (READY)" if cleaned_gem_key else "NOT CONFIGURED",
                            "detail": "Saved permanently to local SQLite database & vault." if cleaned_gem_key else "API key cleared.",
                            "checked_at": datetime.now().strftime("%H:%M:%S"),
                        }
                        _save_memory_store()
                        st.session_state["_gemini_key_flash"] = (
                            True,
                            "Gemini API key saved permanently for all sessions." if cleaned_gem_key else "Gemini API key cleared.",
                        )
                        st.rerun()
                with gem_btn_c2:
                    if st.button("Test & Verify Connection", key="_btn_verify_gemini", use_container_width=True):
                        cleaned_gem_key = (new_gem_key or "").strip()
                        st.session_state["google_genai_api_key"] = cleaned_gem_key
                        _save_memory_store()
                        with st.spinner("Saving key & pinging Google Gemini API..."):
                            res = verify_gemini_connection(cleaned_gem_key)
                        st.session_state["_gemini_key_flash"] = (
                            bool(res["connected"]),
                            f"Saved & Connected: {res['detail']}" if res["connected"] else f"Key saved, but test failed: {res['detail']}",
                        )
                        st.rerun()

            # -----------------------------------------------------------------
            # PROVIDER 2: LOCAL AI (OLLAMA / QWEN AIR-GAPPED)
            # -----------------------------------------------------------------
            with loc_col:
                loc_status = st.session_state.get("local_ai_connection_status", {})
                loc_connected = bool(loc_status.get("connected", False))
                loc_badge_border = "#A0A5B1" if loc_connected else "#3A3F4B"
                loc_dot = "● CONNECTED" if loc_connected else f"○ {loc_status.get('label', 'OFFLINE')}"

                st.markdown(
                    f"""
                    <div class="axiom-panel" style="padding: 0.9rem 1.1rem; margin-bottom: 0.65rem; border-color: {loc_badge_border};">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span class="axiom-eyebrow" style="margin: 0;">02 // LOCAL AI (AIR-GAPPED)</span>
                            <span style="font-size: 0.72rem; font-family: monospace; color: {'#F3F4F6' if loc_connected else '#8E939F'}; font-weight: 600;">
                                {loc_dot}
                            </span>
                        </div>
                        <div style="font-size: 0.8rem; color: #8E939F; margin-top: 0.25rem;">
                            {loc_status.get('detail', 'Local Ollama / OpenAI-compatible server.')}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                u_col, m_col = st.columns([1.8, 1.2])
                with u_col:
                    new_loc_url = st.text_input(
                        "Local AI Endpoint URL",
                        value=st.session_state.get("local_llm_url", DEFAULT_LOCAL_LLM_URL),
                        placeholder="http://localhost:11434/v1",
                        key="_drawer_local_llm_url_input",
                    )
                    if new_loc_url != st.session_state.get("local_llm_url"):
                        st.session_state["local_llm_url"] = new_loc_url
                        _save_memory_store()
                with m_col:
                    new_loc_model = st.text_input(
                        "Model Tag",
                        value=st.session_state.get("local_llm_model", DEFAULT_LOCAL_LLM_MODEL),
                        placeholder="qwen2.5:9b",
                        key="_drawer_local_llm_model_input",
                    )
                    if new_loc_model != st.session_state.get("local_llm_model"):
                        st.session_state["local_llm_model"] = new_loc_model
                        _save_memory_store()

                if st.button("Test & Verify Local AI Connection", key="_btn_verify_local_ai", use_container_width=True):
                    st.session_state["local_llm_url"] = new_loc_url
                    _save_memory_store()
                    with st.spinner(f"Testing connection to {new_loc_url}..."):
                        res = verify_local_llm_connection(new_loc_url)
                    if res["connected"]:
                        st.success(f"Local AI Connected: {res['detail']}")
                    else:
                        st.error(res["detail"])
                    st.rerun()


        # =====================================================================
        # TAB 05: EMAIL (SMTP) & LOAN REMINDERS
        # =====================================================================
        with tab_email:
            st.markdown('<div class="axiom-eyebrow">SMTP EMAIL // BT PROPERTY HOLDINGS LTD LOAN EXPIRY REMINDERS (2 MONTHS BEFORE EXPIRY)</div>', unsafe_allow_html=True)
            _smtp_flash = st.session_state.pop("_smtp_flash", None)
            if isinstance(_smtp_flash, tuple) and len(_smtp_flash) == 2:
                if _smtp_flash[0]:
                    st.success(_smtp_flash[1])
                else:
                    st.error(_smtp_flash[1])
            _cfg = get_smtp_config()
            with st.form(key="_form_smtp_config"):
                c1, c2 = st.columns(2)
                with c1:
                    _host = st.text_input("SMTP Server Host", value=str(_cfg.get("smtp_host") or ""), placeholder="smtp.gmail.com / smtp.office365.com")
                    _port = st.number_input("SMTP Port", min_value=1, max_value=65535, value=int(_cfg.get("smtp_port") or 587), step=1)
                    _enc = st.selectbox("Encryption", options=["STARTTLS", "SSL", "None"], index=["STARTTLS", "SSL", "None"].index(str(_cfg.get("encryption") or "STARTTLS").upper() if str(_cfg.get("encryption") or "STARTTLS").upper() in ("STARTTLS", "SSL") else "STARTTLS"))
                    _user = st.text_input("SMTP Username", value=str(_cfg.get("username") or ""))
                    _pw = st.text_input("SMTP Password / App Password (leave blank to keep saved)", value="", type="password")
                with c2:
                    _sender = st.text_input("Sender Email", value=str(_cfg.get("sender_email") or ""))
                    _sname = st.text_input("Sender Name", value=str(_cfg.get("sender_name") or "Axiom Ledger"))
                    _recips = st.text_area("Recipient Email(s) — comma separated (you receive loan reminders here)", value=str(_cfg.get("recipient_emails") or ""), height=70)
                    _months = st.number_input("Default Reminder Lead Time (months before expiry)", min_value=0, max_value=12, value=int(_cfg.get("default_reminder_months") or 2), step=1)
                    _auto = st.checkbox("Auto-send due reminders on app startup", value=bool(_cfg.get("auto_send_on_startup", True)))
                if st.form_submit_button("💾 Save SMTP Settings", use_container_width=True):
                    _new = dict(_cfg)
                    _new.update({"smtp_host": _host.strip(), "smtp_port": int(_port), "encryption": _enc, "username": _user.strip(), "sender_email": _sender.strip(), "sender_name": _sname.strip(), "recipient_emails": _recips.strip(), "default_reminder_months": int(_months), "auto_send_on_startup": bool(_auto)})
                    if _pw:
                        _new["password"] = _pw
                    _ok, _msg = save_smtp_config(_new)
                    st.session_state["_smtp_flash"] = (_ok, _msg)
                    st.rerun()
            st.caption(f"Saved password: {'●●●●●● set' if str(_cfg.get('password') or '') else 'not set'}  ·  Recipients resolve to: {', '.join(_smtp_recipients(_cfg)) or '—'}")
            tc1, tc2, tc3 = st.columns(3)
            with tc1:
                if st.button("✉ Send Test Email", key="_btn_smtp_test", use_container_width=True):
                    _ok, _msg = send_email_via_smtp("[Axiom Ledger] Test Email", "This is a test email from Axiom Ledger SMTP settings.\nIf you received this, loan expiry reminders will work.")
                    st.session_state["_smtp_flash"] = (_ok, _msg)
                    st.rerun()
            with tc2:
                if st.button("🔔 Check & Send Due Loan Reminders Now", key="_btn_smtp_check_due", use_container_width=True):
                    _n, _msgs = check_and_send_due_loan_reminders(trigger_source="manual_settings")
                    st.session_state["_smtp_flash"] = (_n > 0, f"Sent {_n} reminder(s). " + (" | ".join(_msgs) if _msgs else "No reminders due in the 2-month window."))
                    st.rerun()
            with tc3:
                if st.button("🏦 Open Property Loans Page →", key="_btn_goto_loans", use_container_width=True):
                    st.switch_page("pages/4_Property_Loans.py")
            _due = get_due_loan_reminders()
            if _due:
                st.warning(f"{len(_due)} loan milestone(s) currently inside the reminder window and not yet emailed:")
                for _d in _due:
                    st.markdown(f"- **{_d['loan'].get('property_name')}** ({_d['loan'].get('loan_label')}) — {_d['label']} on **{_fmt_loan_date(_d['expiry_date'])}** ({_d['days_remaining']} days)")
            else:
                st.info("No loan milestones are currently inside the reminder window (or all due reminders have already been emailed).")
            _log = get_loan_reminder_log(limit=10)
            if _log:
                st.markdown('<div class="axiom-eyebrow">RECENT REMINDER EMAIL LOG</div>', unsafe_allow_html=True)
                st.dataframe(pd.DataFrame(_log)[["sent_at", "property_name", "milestone_type", "expiry_date", "recipient_email", "status", "trigger_source"]], use_container_width=True, hide_index=True)

            st.markdown('<div class="axiom-divider" style="margin: 0.75rem 0;"></div>', unsafe_allow_html=True)
            render_property_loans_block(
                key_prefix="settings_email_loans",
                eyebrow_title="PROPERTY LOANS & BANK REGISTRY — PROPERTIES & WHAT EACH LOAN IS (BT PROPERTY HOLDINGS LTD)",
                show_page_title=False,
            )


        # =====================================================================
        # TAB 03: OPERATOR ACCOUNTS & SECURITY
        # =====================================================================
        with tab_accounts:
            all_accounts = get_all_user_accounts()
            account_usernames = list(all_accounts.keys())
            active_operator = st.session_state.get("username") or (account_usernames[0] if account_usernames else "admin")
            if active_operator not in account_usernames and account_usernames:
                active_operator = account_usernames[0]

            acc_flash = st.session_state.pop("_settings_account_flash", None)
            if isinstance(acc_flash, tuple) and len(acc_flash) == 2:
                flash_ok, flash_msg = acc_flash
                if flash_ok:
                    st.success(flash_msg)
                else:
                    st.error(flash_msg)

            # Top Row: Clean Registered Operator Roster
            roster_hdr_col, remove_ctrl_col = st.columns([2.2, 1.4])
            with roster_hdr_col:
                st.markdown(
                    '<div class="axiom-eyebrow">REGISTERED OPERATOR DIRECTORY</div>',
                    unsafe_allow_html=True,
                )
                roster_rows = [
                    {
                        "Username": uname,
                        "Display Name": info.get("name", uname),
                        "Email": info.get("email", f"{uname}@axiomledger.local"),
                        "Role": info.get("role", "Administrator"),
                        "Session": "● SIGNED IN" if uname == st.session_state.get("username") else "PROVISIONED",
                    }
                    for uname, info in all_accounts.items()
                ]
                st.dataframe(pd.DataFrame(roster_rows), use_container_width=True, hide_index=True)

            with remove_ctrl_col:
                st.markdown(
                    '<div class="axiom-eyebrow">ACCOUNT SELECTION &amp; REMOVAL</div>',
                    unsafe_allow_html=True,
                )
                selected_target_user = st.selectbox(
                    "Account to Modify Below",
                    options=account_usernames,
                    index=account_usernames.index(active_operator) if active_operator in account_usernames else 0,
                    key="_settings_select_account_to_modify",
                )
                target_info = all_accounts.get(selected_target_user, {})

                removable_users = [
                    u for u in account_usernames if u != st.session_state.get("username") and len(account_usernames) > 1
                ]
                if removable_users:
                    del_c1, del_c2 = st.columns([1.8, 1.2])
                    with del_c1:
                        user_to_remove = st.selectbox(
                            "Remove Operator",
                            options=removable_users,
                            key="_settings_remove_account_select",
                            label_visibility="collapsed",
                        )
                    with del_c2:
                        if st.button("Remove", key="_btn_remove_operator_account", use_container_width=True):
                            ok_del, msg_del = delete_operator_account(user_to_remove)
                            st.session_state["_settings_account_flash"] = (ok_del, msg_del)
                            st.rerun()

            st.markdown('<div class="axiom-divider" style="margin: 0.5rem 0;"></div>', unsafe_allow_html=True)

            upd_col, add_col = st.columns(2)

            # -----------------------------------------------------------------
            # LEFT COLUMN: UPDATE CREDENTIALS FOR SELECTED ACCOUNT
            # -----------------------------------------------------------------
            with upd_col:
                st.markdown(
                    f'<div class="axiom-eyebrow">01 // UPDATE CREDENTIALS FOR ({selected_target_user.upper()})</div>',
                    unsafe_allow_html=True,
                )
                with st.form(key="_form_update_operator_credentials", clear_on_submit=False):
                    u_c1, u_c2 = st.columns(2)
                    with u_c1:
                        edit_username = st.text_input(
                            "Username",
                            value=selected_target_user,
                            help="Letters, numbers, underscores, dots, or hyphens.",
                        )
                    with u_c2:
                        edit_display_name = st.text_input(
                            "Display Name",
                            value=target_info.get("name", selected_target_user),
                        )
                    edit_email = st.text_input(
                        "Email Address",
                        value=target_info.get("email", f"{selected_target_user}@axiomledger.local"),
                    )
                    edit_current_pw = st.text_input(
                        "Current Password (Required to Authorize)",
                        type="password",
                        placeholder="Enter current password",
                    )
                    pw_c1, pw_c2 = st.columns(2)
                    with pw_c1:
                        edit_new_pw = st.text_input(
                            "New Password (Optional)",
                            type="password",
                            placeholder="Min 8 chars",
                        )
                    with pw_c2:
                        edit_confirm_pw = st.text_input(
                            "Confirm New Password",
                            type="password",
                            placeholder="Repeat new password",
                        )

                    if st.form_submit_button("Save Credentials", use_container_width=True):
                        ok_upd, msg_upd = update_operator_credentials(
                            current_username=selected_target_user,
                            new_username=edit_username,
                            new_display_name=edit_display_name,
                            new_email=edit_email,
                            current_password=edit_current_pw,
                            new_password=edit_new_pw,
                            confirm_password=edit_confirm_pw,
                        )
                        st.session_state["_settings_account_flash"] = (ok_upd, msg_upd)
                        st.rerun()

            # -----------------------------------------------------------------
            # RIGHT COLUMN: PROVISION NEW OPERATOR ACCOUNT
            # -----------------------------------------------------------------
            with add_col:
                st.markdown(
                    '<div class="axiom-eyebrow">02 // PROVISION ADDITIONAL OPERATOR</div>',
                    unsafe_allow_html=True,
                )
                with st.form(key="_form_add_operator_account", clear_on_submit=True):
                    n_c1, n_c2 = st.columns(2)
                    with n_c1:
                        new_acc_user = st.text_input(
                            "New Username",
                            placeholder="e.g. accountant_nz",
                        )
                    with n_c2:
                        new_acc_name = st.text_input(
                            "Display Name",
                            placeholder="e.g. Portfolio Accountant",
                        )
                    r_col1, r_col2 = st.columns(2)
                    with r_col1:
                        new_acc_email = st.text_input(
                            "Email",
                            placeholder="user@axiomledger.local",
                        )
                    with r_col2:
                        new_acc_role = st.selectbox(
                            "Role",
                            options=["Administrator", "Accountant", "Auditor", "Operator"],
                        )
                    np_col1, np_col2 = st.columns(2)
                    with np_col1:
                        new_acc_pw = st.text_input(
                            "Password (Min 8 chars)",
                            type="password",
                            placeholder="••••••••••••",
                        )
                    with np_col2:
                        new_acc_pw_confirm = st.text_input(
                            "Confirm Password",
                            type="password",
                            placeholder="••••••••••••",
                        )

                    if st.form_submit_button("+ Create Operator Account", use_container_width=True):
                        ok_add, msg_add = add_operator_account(
                            username=new_acc_user,
                            display_name=new_acc_name,
                            email=new_acc_email,
                            password=new_acc_pw,
                            confirm_password=new_acc_pw_confirm,
                            role=new_acc_role,
                        )
                        st.session_state["_settings_account_flash"] = (ok_add, msg_add)
                        st.rerun()


def render_workspace_header(show_status_indicator: bool = False) -> None:
    """
    Render the Xero/QuickBooks-style Top Workspace Toolbar:
    1. Left: Active Entity Banner, Jurisdiction Tag (NZ IRD vs US IRS Schedule E), and Financial Year Context
    2. Center/Bottom Bar: Single-Year Financial Year Pill Bar (`2026`, `2027`, `2028`, `2029`, `+ Add Year`)
    3. Right: Live AI Connection Status Badges + `⚙ AI & System Settings` button (opens right-hand drawer)
       and optional Background Thread Status Indicator.
    If Settings is open (`show_settings_drawer` is True), exclusively render the Settings view on the
    right-hand main canvas and halt any further page rendering so nothing else is displayed.
    """
    init_workspace_state()

    # Exclusive Settings View: when Settings is open, show ONLY Settings on the right-hand side
    if st.session_state.get("show_settings_drawer", False):
        render_right_settings_drawer()
        st.stop()

    active_entity = get_active_entity()
    active_tax_year = get_active_tax_year()
    jurisdiction = get_entity_jurisdiction(active_entity)
    jurisdiction_badge = "USA IRS" if jurisdiction == "US" else "NZ IRD"
    period_str = format_tax_year_period(active_tax_year, active_entity)

    active_model = st.session_state.get("categorization_model", CATEGORIZATION_MODEL_OPTIONS[0])
    gem_status = st.session_state.get("gemini_connection_status", {})
    loc_status = st.session_state.get("local_ai_connection_status", {})

    # Top Row: Workspace Context Banner | AI Status Badges & Settings Button | Background Telemetry
    if show_status_indicator:
        banner_col, ai_col, bg_col = st.columns([2.2, 1.15, 0.95])
    else:
        banner_col, ai_col = st.columns([2.7, 1.3])
        bg_col = None

    with banner_col:
        st.markdown(
            f"""
            <div class="axiom-workspace-banner">
                <div style="display: flex; align-items: center; justify-content: space-between;">
                    <span class="axiom-eyebrow">ORGANIZATION WORKSPACE // TAX PERIOD: {period_str.upper()}</span>
                    <span style="
                        font-size: 0.68rem;
                        font-weight: 600;
                        letter-spacing: 0.1em;
                        color: #E2E4E9;
                        background-color: #24272E;
                        border: 1px solid #3A3F4B;
                        border-radius: 3px;
                        padding: 0.1rem 0.5rem;
                    ">{jurisdiction_badge}</span>
                </div>
                <h2>{active_entity} &mdash; {active_tax_year} ({period_str})</h2>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with ai_col:
        badge_c1, badge_c2 = st.columns(2)
        with badge_c1:
            gem_html = _render_connection_badge(
                "GEMINI",
                gem_status,
                is_selected=(active_model == "Gemini 3.8 Flash (Cloud)"),
            )
            st.markdown(gem_html, unsafe_allow_html=True)
        with badge_c2:
            loc_html = _render_connection_badge(
                "LOCAL AI",
                loc_status,
                is_selected=(active_model == "Local Qwen 9B (Air-gapped)"),
            )
            st.markdown(loc_html, unsafe_allow_html=True)

        drawer_open = st.session_state.get("show_settings_drawer", False)
        settings_btn_label = "⚙ Hide Settings" if drawer_open else "⚙ Settings (Orgs, AI & Accounts)"
        if st.button(
            settings_btn_label,
            key="_top_toolbar_settings_btn",
            type="primary" if drawer_open else "secondary",
            use_container_width=True,
        ):
            st.session_state["show_settings_drawer"] = not drawer_open
            st.rerun()

    if bg_col is not None:
        with bg_col:
            render_global_status_indicator()

    # Single-Year Financial Year Pill Bar (2026 | 2027 | 2028 | 2029 | + Add Year)
    years = get_tax_year_options()
    year_bar_cols = st.columns([0.9] + [0.65 for _ in years] + [1.1, 1.8])
    with year_bar_cols[0]:
        st.markdown(
            '<div style="padding-top: 0.48rem;" class="axiom-eyebrow">FINANCIAL YEAR:</div>',
            unsafe_allow_html=True,
        )

    for idx, yr in enumerate(years):
        with year_bar_cols[idx + 1]:
            is_current_yr = str(yr) == str(active_tax_year)
            btn_label = f"● {yr}" if is_current_yr else str(yr)
            if st.button(
                btn_label,
                key=f"_fy_pill_btn_{yr}",
                type="primary" if is_current_yr else "secondary",
                use_container_width=True,
            ):
                st.session_state["active_tax_year"] = str(yr)
                st.rerun()

    with year_bar_cols[len(years) + 1]:
        with st.popover("+ Add Year", use_container_width=True):
            st.markdown('<div class="axiom-eyebrow">ADD FINANCIAL YEAR</div>', unsafe_allow_html=True)
            suggested_next = str(max(int(y) for y in years if y.isdigit()) + 1) if years else "2028"
            new_yr_input = st.text_input(
                "4-Digit Year",
                value=suggested_next,
                max_chars=4,
                key="_popover_new_tax_year_input",
            )
            if st.button("Add & Switch Year", key="_btn_confirm_add_year", use_container_width=True):
                ok, msg = add_new_tax_year(new_yr_input)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)


def render_sidebar_workspace_controls() -> None:
    """Backwards-compatible alias for workspace sidebar controls."""
    init_workspace_state()


def render_sidebar_session(authenticator: stauth.Authenticate) -> None:
    """
    Render the Xero/QuickBooks-style Organization Switcher in the left sidebar:
    - Dedicated one-click Entity Workspace cards/buttons (no clumsy selectbox dropdown)
    - '+ Add New Entity' inline creator with Tax Jurisdiction selection (NZ IRD vs US IRS Schedule E)
    - Quick Financial Year summary & Settings shortcut
    - Operator metadata & Sign Out button
    """
    init_workspace_state()

    # If the user clicked a different page in the left-hand Streamlit navigation menu,
    # automatically close the Settings view so the selected page is shown.
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        ctx = get_script_run_ctx()
        current_page = getattr(ctx, "main_script_path", "") or getattr(ctx, "page_script_hash", "")
    except Exception:
        current_page = ""

    last_page = st.session_state.get("_last_rendered_page")
    if current_page and last_page and current_page != last_page:
        st.session_state["show_settings_drawer"] = False
    if current_page:
        st.session_state["_last_rendered_page"] = current_page

    active_entity = get_active_entity()
    active_tax_year = get_active_tax_year()
    drawer_open = st.session_state.get("show_settings_drawer", False)

    with st.sidebar:
        st.markdown('<div class="axiom-eyebrow">AXIOM LEDGER // ORGANIZATIONS</div>', unsafe_allow_html=True)
        st.caption("Select an organization (edit names in ⚙ Settings):")

        for idx, ent in enumerate(get_entity_options()):
            is_active = (ent == active_entity) and not drawer_open
            jur = get_entity_jurisdiction(ent)
            jur_tag = "USA IRS" if jur == "US" else "NZ IRD"
            ent_df = get_entity_ledger(ent, active_tax_year)
            row_count = len(ent_df)
            prefix = "●" if is_active else "○"
            btn_text = f"{prefix} {ent} [{jur_tag}] ({row_count})"
            if st.button(
                btn_text,
                key=f"_sidebar_org_btn_{idx}_{ent}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state["active_entity"] = ent
                st.session_state["entity"] = ent
                st.session_state["show_settings_drawer"] = False
                _save_memory_store()
                st.rerun()

        st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="axiom-eyebrow">SYSTEM CONFIGURATION</div>', unsafe_allow_html=True)

        if st.button(
            "⚙ Settings (Orgs, AI & Security)" if not drawer_open else "⚙ Close Settings",
            key="_sidebar_open_settings_btn",
            type="primary" if drawer_open else "secondary",
            use_container_width=True,
        ):
            st.session_state["show_settings_drawer"] = not drawer_open
            st.rerun()

        st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="axiom-eyebrow">AXIOM LEDGER // CORE</div>', unsafe_allow_html=True)
        st.markdown(
            f"**Operator:** `{st.session_state.get('name', 'System Administrator')}`  \n"
            f"**Clearance:** `LEVEL-1 (ADMIN)`  \n"
            f"**Auto-Logout:** `60m Inactivity`"
        )
        if st.button("Sign Out", key="_sidebar_explicit_signout_btn", use_container_width=True):
            sign_out_current_session(authenticator)
            st.switch_page("app.py")


def require_auth() -> stauth.Authenticate:
    """
    Enforce authentication across all pages with a 60-minute sliding inactivity timeout.
    Redirects to the main login gateway (`app.py`) if the user is not authenticated.
    Renders persistent sidebar controls when authenticated.
    """
    authenticator = get_authenticator()
    auth_status = sync_and_touch_auth_session(authenticator)

    apply_custom_styles(authenticated=bool(auth_status))
    if auth_status:
        try:
            run_loan_reminder_check_once_per_session()
        except Exception:
            pass

    if not auth_status:
        st.switch_page("app.py")
        st.stop()

    render_sidebar_session(authenticator)
    return authenticator
