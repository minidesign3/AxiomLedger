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
