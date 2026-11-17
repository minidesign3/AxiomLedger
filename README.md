# Axiom Ledger

A local, encrypted property-accounting workspace built with **Streamlit**. It
ingests bank CSVs, PDFs, screenshots and spreadsheets, auto-sorts them onto a
4-column upload board, applies deterministic + AI-assisted categorization to a
per-entity master ledger (NZ IRD / US IRS), and tracks a NZ property-loan /
mortgage registry with expiry email reminders.

## Requirements

- Python 3.10+
- A local virtual environment with the pinned dependencies in
  [`requirements.txt`](requirements.txt).

## Setup & run

```bash
python -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/streamlit run app.py   # or: bash start.sh
```

## Configuration (environment variables)

| Variable | Purpose | Default |
|----------|---------|---------|
| `AXIOM_ADMIN_BCRYPT_HASH` | Bcrypt hash for the bootstrap admin login. **Set this in production.** | built-in bootstrap hash |
| `AXIOM_CODES_XLSX` | Absolute path to an optional `Codes.xlsx` auto-import workbook. | legacy desktop path |
| AI / SMTP settings | Managed in-app via the ⚙ Settings drawer and persisted in the local store. | — |

## Project layout

```
app.py                     # Login gate + front "Upload & File Board"
pages/1_Dashboard.py       # Item/Property setup, Master Ledger, Accountant pivot
pages/2_Data_Ingestion.py  # Ingestion board (wrapper)
pages/3_Script_Manager.py  # Background worker supervisor (demonstration mode)
pages/4_Property_Loans.py  # NZ loan & mortgage registry (wrapper)
axiom_core.py              # Pure, stateless, unit-tested helpers (no Streamlit)
utils.py                   # Application module (DB, auth, AI/OCR, loans, UI)
scripts/sanitize_fixture.py# Generates sanitized fixtures for safe development
tests/                     # Pytest unit tests (pure helpers only — never touch real data)
```

> **Note:** `utils.py` is a large legacy module being incrementally decomposed.
> `axiom_core.py` is the first extracted, fully testable slice; new pure logic
> should land there (or future `axiom/` sub-modules) rather than growing `utils`.

## Testing & linting

```bash
./.venv/bin/pytest            # unit tests (pure helpers; no DB, no network)
./.venv/bin/ruff check .      # lint
./.venv/bin/black .           # format
./.venv/bin/mypy axiom_core.py
```

## Data privacy

Real user / financial data (databases, `data/`, `exports/`, `uploads/`,
`statements/`, `logs/`, secrets, spreadsheets, PDFs) must **never** be read,
printed, or committed. See [`AGENTS.md`](AGENTS.md). Development uses source,
schemas, `.env.example`, and sanitized fixtures from
`scripts/sanitize_fixture.py` only. Tests must stay DB-free.
