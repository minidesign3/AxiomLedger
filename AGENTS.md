# Repository Agent Rules — Data Privacy & Security

## Permanent Privacy Rule
**NEVER read, query, print, sample, or summarize real user, medical, therapy, financial, or scraped data.**

All AI assistants, agents, and subagents (across Code, Architect, Ask, and Agent modes) MUST adhere to the following boundaries:

1. **Prohibited Files & Directories**:
   - Secrets & keys: `.env`, `.env.*` (except `.env.example`), `**/*.pem`, `**/*.key`, `**/secrets/**`
   - Databases: `*.db`, `*.db-wal`, `*.db-shm`, `*.sqlite`, `*.sqlite3`, `pb_data/`
   - Data & documents: `data/`, `exports/`, `uploads/`, `statements/`, `logs/`, `*.csv`, `*.tsv`, `*.pdf`, `*.xlsx`, `*.xls`, `*.parquet`
2. **Permitted Inputs Only**:
   - Source code, configuration templates (`.env.example`), and schema/migration definitions
   - Sanitized fixtures matching `fixtures/sanitized_*` (generated via `scripts/sanitize_fixture.py`)
3. **Shell & Tool Discipline**:
   - Never run `sqlite3`, `strings`, `cat`, `python`, or any inspection command against real databases or data directories.
   - If sample data is needed for development or testing, instruct the user to run `scripts/sanitize_fixture.py` or work strictly from schemas and `fixtures/sanitized_*`.

## Code Layout Rule (enforced in CI)
- **No Python file may exceed 500 lines** and no giant functions (ruff `C901`/`PLR0912`/`PLR0913`/`PLR0915`). See `CONTRIBUTING.md`.
- One responsibility per `axiom_*` module; `utils.py` is a re-export shim only — never add new code to it.
- When decomposing `utils.py` (see `plan.md` §10), move function bodies **byte-for-byte** (no paraphrasing/logic changes) and verify with `python scripts/gate.py --module axiom_<name>`. Intentional edits must be reviewed and passed via `--allow-changed`.
- Modules imported by `utils.py` that need its session helpers use a **function-body-scoped `import utils`**, never top-level (circular-import rule; see `axiom_loan_crud.py`).
