# Decomposition phase — prompt template (for the cheap model)

Copy this whole block into the cheap model (e.g. DeepSeek) for ONE phase. Fill
the `<<< >>>` placeholders from `plan.md` §10.3 / §10.4. Do exactly one phase per
run. Do not start the next phase until `python scripts/gate.py` prints
`GATE PASSED`.

---

## TASK

You are extracting one slice of the legacy `utils.py` God-module into a new
focused module, behind a re-export shim. This is a **mechanical move, not a
rewrite.**

**New module:** `<<< axiom_state.py >>>`
**Responsibility:** `<<< one sentence from plan.md §10.3 >>>`
**Functions/constants to MOVE (exact names, current utils.py lines):**
```
<<< get_entity_options            (431)
    get_tax_year_options          (442)
    get_active_entity             (449)
    ... >>>
```

## HARD RULES (violating any of these fails the gate)

1. **Copy bodies byte-for-byte.** Do NOT reword, "clean up", re-order, re-indent
   logic, rename variables, change string literals, or swap constants. Cut the
   exact source out of `utils.py` and paste it into the new module unchanged.
   A script compares the moved code to a frozen baseline via AST — any logic
   change is rejected unless explicitly waived.

2. **Add a re-export shim in `utils.py`.** After removing the definitions, add
   `from <new_module> import (<names>)  # noqa: F401` in the import block so
   every existing `from utils import ...` / `utils.<name>` call site still works.
   Keep the same public names.

3. **Imports:** give the new module only the imports its moved code actually
   uses (`import pandas as pd`, `import streamlit as st`, `from axiom_core import
   ...`, etc.). Put `from __future__ import annotations` at the top.

4. **Circular imports:** if any moved function calls a helper that still lives in
   `utils.py` (e.g. `get_active_entity`, `_load_memory_store`,
   `get_entity_jurisdiction`), do NOT import it at the new module's top level.
   Use a **function-body-scoped `import utils`** inside each function that needs
   it, then call `utils.<helper>()`. See `axiom_loan_crud.py` for the pattern.
   Never add `import <new_module>` at the top of `utils.py` beyond the shim line
   already described.

5. **Do not touch** any other function, any `pages/*.py`, `app.py`, tests, or
   the `scripts/` gate. Only `utils.py` (remove + shim) and the new module.

6. **No behaviour changes.** If you notice a bug, leave it. Intentional fixes are
   a separate, reviewed step (they require `--allow-changed`).

## TESTS

If `tests/` has a file exercising these functions, keep it passing. If the moved
code had no test and is pure logic, add a minimal `tests/test_<new_module>.py`
using temp SQLite / fakes only (never real `data/` or network — see `AGENTS.md`).

## ACCEPTANCE — run this and iterate until green

```
python scripts/gate.py --module <<< axiom_state >>>
```

It must print `GATE PASSED`. The gate runs, in order:
`py_compile` → `ruff` → file-length guard → **baseline byte-identity** →
**import-order identity** (module-first AND utils-first) → `pytest`.

If `extraction` fails with "CHANGED", you edited logic you were told to copy —
revert to the exact original bytes. Only if a change was truly intentional and
reviewed, re-run with `--allow-changed <name>`.

## AFTER GREEN

Lower this file's ceiling in `.axiom_file_length_allow.txt` (or run
`python scripts/check_file_length.py --tighten`) so `utils.py`'s allowance
ratchets down. Then stop. One phase per run.

---

### Notes for the human driver
- Run `python scripts/snapshot_baseline.py` **once**, before phase D1. Never
  refresh it mid-decomposition (it is the definition of "original").
- Order: do the non-UI phases **D1–D15** with the cheap model. Hand the UI
  phases **D16–D22** (and final **D23**) to a stronger model — they require
  splitting 500–840-line render functions into sub-components + visual QA, which
  the byte-identity gate intentionally will NOT bless automatically.
