"""
axiom_core
==========

Pure, stateless helper functions and constants extracted from the monolithic
``utils.py``.

Nothing in this module may import ``streamlit`` or touch ``st.session_state``.
Everything here is deterministic and unit-testable in isolation, which makes it
the first step of breaking the 11k-line ``utils.py`` god-module into focused,
testable components. ``utils.py`` imports (and re-exports) these symbols, so
existing ``from utils import ...`` call sites keep working unchanged.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# Values (case-insensitive) that mean "this cell has no real Account Code yet".
UNCATEGORIZED_MARKERS = {"", "uncategorized", "unassigned", "none", "nan", "null"}

# Canonical ledger schema shared across the DB layer, parsing, and UI.
STANDARD_COLUMNS = ["Date", "Amount", "Description", "Property", "Account Code"]
# Property label used when a transaction has not been assigned to a property.
UNASSIGNED_PROPERTY = "Unassigned"

# Default local (Ollama / OpenAI-compatible) LLM endpoint.
DEFAULT_LOCAL_LLM_URL = "http://localhost:11434/v1"
# Default local model tag served by that endpoint.
DEFAULT_LOCAL_LLM_MODEL = "qwen2.5:9b"
# Gemini model used for both OCR (receipt/screenshot) extraction and cloud categorization.
GEMINI_OCR_MODEL = "gemini-3.8-flash"
# Selectable categorization providers shown in the settings drawer / used by the router.
CATEGORIZATION_MODEL_OPTIONS = [
    "Local Qwen 9B (Air-gapped)",
    "Gemini 3.8 Flash (Cloud)",
]

# --- Workspace defaults (entities, financial years, properties) -------------
# Canonical copies live here so axiom_state / axiom_memory can import them
# without a circular import back through utils.py.
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

DEFAULT_PROPERTIES = [
    "303 Weymouth",
    "33 Blampied",
    "26 Frances",
    "9 Damian",
    "14 Raleigh",
    "363 Queen",
    "General / Portfolio",
]

MEMORY_STORE_PATH = Path(__file__).resolve().parent / ".axiom_memory.json"

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

# Jurisdiction-specific system prompts for the AI categorization engine. Defined
# here in axiom_core (not in utils/axiom_categorization) so exactly one canonical
# copy exists and both modules can import it without a circular dependency.
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


def _safe_vault_segment(name: str) -> str:
    """Sanitize a folder/file path segment while keeping it human-readable on disk."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip())
    return cleaned or "unnamed"


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
    # NOTE: fixed a latent bug where prefix "15" was mapped twice (Westpac then
    # TSB); the first mapping was silently dead. "15" is TSB.
    prefix_map = {
        "01": "ANZ",
        "06": "ANZ",
        "11": "ANZ",
        "02": "BNZ",
        "03": "Westpac",
        "13": "Westpac",
        "14": "Westpac",
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


def _clean_ird_category_response(raw_content: str) -> str:
    """Normalize a model response down to the exact single-line tax category string."""
    content = (raw_content or "").strip()
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    if not content:
        return "Uncategorized"
    first_line = content.splitlines()[0].strip(" \"'`.,:;!*")
    return first_line if first_line else "Uncategorized"


def is_uncategorized(account_code: object) -> bool:
    """Return True if an Account Code cell is empty or marked as Uncategorized/UNASSIGNED."""
    if pd.isna(account_code):
        return True
    cleaned = str(account_code).strip().lower()
    return cleaned in UNCATEGORIZED_MARKERS


def _parse_loan_date(val: object) -> date | None:
    """Parse a loan date from a `date`/`datetime`/various string formats, or None if unparseable."""
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


def _fmt_loan_date(val: object) -> str:
    """Format a loan date for display as `'DD Mon YYYY'`, or `'—'` if unparseable."""
    d = _parse_loan_date(val)
    return d.strftime("%d %b %Y") if d else "—"


def _loan_date_iso(val: object) -> str:
    """Format a loan date as an ISO `YYYY-MM-DD` string for storage, or `''` if unparseable."""
    d = _parse_loan_date(val)
    return d.isoformat() if d else ""

# --- Account-code / property-alias configuration ---------------------------
# Canonical copies live here so axiom_account_codes can import them without a
# circular import back through utils.py.
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

# Backwards-compatible alias pointing to the NZ rules dictionary.
CATEGORIZATION_RULES: dict[str, str] = ENTITY_CATEGORIZATION_RULES["NZ"]
