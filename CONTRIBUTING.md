# Contributing to Axiom Ledger

## Code layout rules (enforced in CI)

These rules exist because the project once had a single **11,000-line
`utils.py`** God-module. They prevent that from ever recurring.

1. **No Python file over 500 lines; 400 lines is the early-warning alert.**
   Enforced by `scripts/check_file_length.py` (and pre-commit + CI). Files
   over 400 lines print a non-fatal `::warning::` so growth gets caught before
   it becomes a problem; files over 500 lines **fail** the build. A file that
   must temporarily exceed 500 goes in `.axiom_file_length_allow.txt` with an
   explicit ceiling that may only ever **shrink** (run the script with
   `--tighten` to ratchet it down, never up). The goal is to empty that file.

2. **No giant functions.** Ruff enforces `C901` (complexity ≤ 15),
   `PLR0915` (≤ 80 statements), `PLR0912` (≤ 20 branches), `PLR0913`
   (≤ 8 arguments). Split UI render functions into focused sub-renderers.

3. **One responsibility per module.** Persistence, auth, parsing, OCR, a single
   UI view, etc. each live in their own `axiom_*.py`. Never mix SQLite + SMTP +
   LLM + CSS + Streamlit views in one file again.

4. **`utils.py` is a shim.** It only re-exports from the `axiom_*` modules so
   legacy `from utils import ...` call sites keep working. Put new code in a
   focused module, not in `utils.py`.

## Decomposition workflow (phases D0-D23, see `plan.md` §10)

Each phase extracts one slice of `utils.py` into a new `axiom_*` module behind
the re-export shim, then must pass the acceptance gate:

```
# once, before the first extraction (D1):
python scripts/snapshot_baseline.py

# after each extraction:
python scripts/gate.py --module axiom_<new_module>
# add --allow-changed <name> ... for intentional logic edits you reviewed
```

`scripts/gate.py` runs, in order: `py_compile`, `ruff`, the file-length guard,
the baseline **byte-identity** check (proves moved functions were not
paraphrased), an **import-order identity** check (the new module's symbols must
be the same objects as `utils.<name>` whether `utils` or the module is imported
first — catches circular-import mistakes), and `pytest`. Ship only on
`GATE PASSED`.

## Circular-import rule

A module that `utils.py` imports at its own import time, but which itself needs a
session/state helper defined in `utils.py` (e.g. `get_active_entity`), must use a
**function-body-scoped `import utils`**, never a top-level one. See
`axiom_loan_crud.py` for the canonical pattern.

## Data privacy

See `AGENTS.md`. Tests use temp SQLite / sanitised fixtures only. Never read
`data/`, `*.db`, exports, or secrets. All network clients (Gemini / OpenAI /
SMTP) are monkeypatched in tests — no real network calls.
