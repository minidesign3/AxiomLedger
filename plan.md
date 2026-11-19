# Axiom Ledger — Full Code Peer Review & Improvement Plan

> Planning document only. No code has been changed. Prepared as a combined
> senior-engineering + UI/UX review of the entire application.

## 1. Goal

Deliver a complete, honest peer review of the Axiom Ledger codebase and a
prioritised, low-risk roadmap to improve **architecture, performance,
correctness, security, maintainability, and UI/UX** — without changing product
behaviour or touching any real/protected data (per `AGENTS.md`).

## 2. Scope Reviewed

| File | Lines | Role |
|------|------:|------|
| `app.py` | ~110 | Login gate + front "Upload & File Board" |
| `pages/1_Dashboard.py` | ~500 | Item/Property setup, Master Ledger, Accountant pivot |
| `pages/2_Data_Ingestion.py` | ~50 | Thin wrapper over ingestion pipeline |
| `pages/3_Script_Manager.py` | ~185 | Background "thread supervisor" UI |
| `pages/4_Property_Loans.py` | ~30 | Thin wrapper over loan registry |
| `utils.py` | **11,088** | **Everything else** (DB, auth, email, AI/OCR, loans, CSS, all UI) |
| `scripts/sanitize_fixture.py` | ~325 | Data sanitiser (good, out of hot path) |

Index: 7 Python files, ~420 symbols, 193 module-level functions.

---

## 3. Analysis — Findings (Senior Dev Team)

### 3.1 Architecture (highest impact)

- **A1 — `utils.py` is an 11k-line God module.** It mixes persistence (SQLite),
  auth/session, SMTP email, LLM/OCR API calls, loan domain logic, CSS, and *all*
  Streamlit UI rendering. This is the single biggest maintainability risk.
  → Split into a package: `axiom/db.py`, `axiom/services/{llm,ocr,email}.py`,
  `axiom/domain/{loans,categorization}.py`, `axiom/ui/{components,styles}.py`,
  `axiom/state.py`.

- **A2 — Duplicated / dead persistence layer.** There are **two parallel
  implementations** of the storage layer:
  - Early set (`_db_save_file_record_and_rows` L609, `_db_load_uploaded_files`
    L697, `_db_save_master_ledger` L769, `_db_load_master_ledger` L805).
  - Later set (`_save_file_record_to_sqlite` L5820, `_load_uploaded_files_from_sqlite`
    L5930, `_save_master_ledger_to_sqlite` L6129, `_load_master_ledger_from_sqlite`
    L6169).
  The later set is what the app actually uses; most of the `_db_*` set is
  effectively dead. **`export_entity_vault_zip` is defined twice** (L856 and
  L6199) — Python keeps the second, so the first (~30 lines) is dead code.
  → Delete one layer; keep a single, tested DB module (~250+ duplicated lines removed).

- **A3 — `importlib.reload(utils)` at the top of every page/app.** This reloads
  the entire 11k-line module on *every* Streamlit rerun. It is slow, defeats any
  future caching, and can cause subtle identity bugs (two versions of the same
  class/const). → Remove; import normally.

- **A4 — No caching anywhere.** No `@st.cache_data` / `@st.cache_resource`.
  Reference data (entities, account codes, aliases, memorized rules, loans) is
  re-read from SQLite **and** `.axiom_memory.json` on every rerun.

### 3.2 Performance

- **P1 — `_ensure_sqlite_schema()` runs on every DB call** — full
  `executescript` + `PRAGMA table_info` migration scan each time. Should run once
  per process (`@st.cache_resource`).
- **P2 — New `sqlite3.connect()` per operation.** Use one cached connection /
  lightweight pool guarded by the existing lock.
- **P3 — `_load_memory_store()` writes back to SQLite + JSON on every read**
  (write amplification + disk I/O on the hot path).
- **P4 — Master ledger save = full `DELETE` + bulk re-`INSERT`** every time,
  even for a one-cell edit.
- **P5 — Row-by-row `df.iterrows()` traffic-light evaluation** (Dashboard Tab 3)
  runs a Python loop over the whole ledger on every rerun. Vectorise with pandas.
- **P6 — Script Manager `time.sleep(1.5)` + `st.rerun()` poll loop** blocks the
  server script thread; use `st.fragment(run_every=...)`.

### 3.3 Correctness / Robustness

- **C1 — Background threads stored in `st.session_state`.** Thread objects +
  `threading.Event`s live in per-session state; workers mutate `task_state`
  in place and the UI only reflects changes on manual rerun. The workers are
  cosmetic (sleep + log messages). Either make this an honest simulation or move
  to a real scheduler/process. Current pattern is fragile across reruns/sessions.
- **C2 — ~60 bare `except Exception:` blocks** (many `pass`) silently swallow
  errors across DB, file I/O, parsing, and API calls. Add structured `logging`
  and narrow the exception types.
- **C3 — Brittle jurisdiction inference:** `"US" in resolved_entity.upper()`
  misclassifies any entity whose name happens to contain "US". Rely only on the
  stored `entity_jurisdictions` map.
- **C4 — Stale comments/docstrings** reference removed legacy entities
  ("NZ Portfolio", "US Portfolio", "Mum's Data"). Clean up to avoid confusion.

### 3.4 Security

- **S1 — Hardcoded admin bcrypt hash with the plaintext password in a comment**
  (`ADMIN_BCRYPT_HASH`, `"Axiom#Ledger2026!"`). Even as a "mock", remove the
  plaintext and source it from env/secrets (`.streamlit/secrets.toml` / `.env`).
- **S2 — Hardcoded absolute path** `/home/ccjp/Desktop/Accounting CSV Parser/Codes.xlsx`
  in `pages/1_Dashboard.py`. Not portable; move to config/env and feature-flag.
- **S3 — API keys / SMTP creds flow through `st.session_state` and the JSON/SQLite
  memory store.** Confirm these never land in the vault/exports and are excluded
  from any logging.

### 3.5 Maintainability / Tooling

- **M1 — Oversized functions:** `render_property_loans_block` (~840 lines),
  `render_item_property_setup_matrix` (~700), `render_right_settings_drawer`
  (~600), `render_entity_ingestion_pipeline` (~460). Break into components.
- **M2 — No tests, no README, no linter/formatter/type-check config.** Add
  `pytest`, `ruff`, `black`, `mypy`, and a minimal CI.
- **M3 — Magic strings scattered:** model names (`"gemini-3.8-flash"`,
  `"qwen2.5:9b"`), bucket names, status labels. Centralise as enums/constants.
- **M4 — No `logging` module** — only custom in-memory log buffers.

---

## 4. Analysis — Findings (UI / UX Expert)

- **U1 — Inline HTML/CSS everywhere via `unsafe_allow_html`** plus a ~290-line
  CSS blob in `apply_custom_styles`. Centralise into one stylesheet + CSS custom
  properties (design tokens) for colour/spacing; remove per-element inline hex.
- **U2 — Full-page `st.rerun()` on every action (~60 calls)** causes flicker and
  loss of scroll position. Adopt `st.fragment` for localised updates (loan cards,
  master editor, global status indicator, live log).
- **U3 — Dual navigation model:** native multipage sidebar *and* custom
  `st.switch_page` buttons with inconsistent labels ("01 //", "02 //"). Pick one
  mental model; align page titles and in-page nav.
- **U4 — Master Ledger editor UX:** the `data_editor` key embeds filter values,
  so changing a filter resets editor state; traffic lights recompute per row each
  rerun. Large ledgers will feel laggy. Paginate / cache / vectorise.
- **U5 — Accessibility:** status conveyed only by emoji colour (🟢🟡🔴).
  Add text labels / ARIA and ensure contrast ratios; define semantic colour
  tokens instead of hardcoded greys.
- **U6 — Information density & hierarchy:** heavy ALL-CAPS "eyebrow" labels and
  dense panels throughout. Introduce consistent spacing scale, typographic
  hierarchy, and a small reusable component set (metric card, panel, badge).
- **U7 — Consistency:** mixed icon usage (▪, emoji, arrows) and inconsistent
  button sizing/casing. Standardise a component library.

---

## 5. Files to Modify / Create (if approved)

Grouped by workstream; exact edits depend on which phases you approve.

- `utils.py` → split into `axiom/` package (db, state, services, domain, ui, styles).
- `app.py`, `pages/1_Dashboard.py`..`pages/4_Property_Loans.py` → drop
  `importlib.reload`, import from new modules, extract inline HTML.
- `pages/1_Dashboard.py` → remove hardcoded desktop path; vectorise traffic lights.
- **New:** `axiom/` package modules, `assets/styles.css`, `tests/`,
  `README.md`, `pyproject.toml` (ruff/black/mypy), `.github/workflows/ci.yml`.
- `.streamlit/secrets.toml` usage for admin hash / API keys (no secrets committed).

## 6. Dependencies & Ripple Effects

- Every page imports heavily from `utils.py`; splitting it touches all imports —
  do it behind a compatibility shim (`utils.py` re-exports from `axiom/`) to keep
  one change set reversible.
- Removing `importlib.reload` changes dev hot-reload behaviour — rely on
  Streamlit's native watcher instead.
- Caching reference data changes when data appears to "refresh" — must add
  explicit cache invalidation on writes.
- DB-layer consolidation must preserve the exact table/column schema and the
  JSON↔SQLite sync semantics already in production.

## 7. Proposed Implementation Phases (incremental, low-risk first)

1. **Quick wins (no behaviour change):** remove `importlib.reload`; delete the
   duplicate `export_entity_vault_zip` + dead `_db_*` layer; remove hardcoded
   desktop path (make it config); scrub plaintext password comment; fix stale
   comments.
2. **Performance:** cache schema init + DB connection; `@st.cache_data` for
   reference lookups with invalidation; vectorise traffic-light evaluation;
   fragment-ise the live log poll.
3. **Tooling & safety net:** add `pyproject.toml` (ruff/black/mypy), a README,
   and `pytest` with tests for categorization, parsing, and the DB layer *before*
   larger refactors.
4. **Modularisation:** carve `utils.py` into the `axiom/` package behind a
   re-export shim; split the mega render functions into components.
5. **UI/UX system:** external stylesheet + design tokens; fragment-based updates;
   unify navigation; accessibility pass.
6. **Background tasks:** decide honest-simulation vs. real scheduler and rebuild
   that module accordingly.

## 8. Risks & Considerations

- **Behaviour parity:** the app persists real financial state; DB-layer and
  memory-store refactors must be byte-for-byte schema compatible and covered by
  tests first. Highest-risk area.
- **Session/thread model:** changing the background-task pattern can break the
  Script Manager page; treat as its own phase.
- **Data privacy:** all work stays within source/schema/sanitised fixtures per
  `AGENTS.md`; no reading of `data/`, `*.db`, exports, or secrets.
- **Caching bugs:** aggressive caching without invalidation could show stale
  ledgers — add explicit cache-clear on every write path.
- **Big-bang risk:** keep each phase independently shippable and reversible;
  avoid one giant refactor PR.

---

---

## 9. Implementation Status (updated)

Verification available in this environment: `py_compile`, `pytest`, `ruff`.
Full UI rendering cannot be exercised headless, so cosmetic/behavioural-UI
changes that need visual QA are **staged, not auto-applied**.

### ✅ Done & verified

**Phase 0 — DB safety net (prerequisite for the deferred refactors)**
- New Streamlit-free `axiom_db.py` owns the SQLite persistence layer: storage
  paths, `_DB_LOCK`, the once-per-process `_SCHEMA_READY` guard, `_get_sqlite_conn`,
  `_ensure_sqlite_schema`, and the canonical master-ledger / uploaded-file
  round-trips. `utils.py` imports and re-exports them, so every `from utils import …`
  / `utils.<name>` call site is unchanged.
- `tests/test_db_roundtrip.py`: 11 tests exercising save/load/upsert/delete and the
  schema guard on a **temporary** SQLite file (never `data/axiom_ledger.db`),
  satisfying `AGENTS.md`.

**Phase 2b — Dual persistence layer consolidated (now covered by DB tests)**
- Removed the legacy duplicate `_db_save_master_ledger` and repointed its one live
  call site to the canonical `_save_master_ledger_to_sqlite`.
- Moved `_save_file_record_to_sqlite`, `_delete_file_record_from_sqlite`,
  `_save_master_ledger_to_sqlite`, `_load_master_ledger_from_sqlite` (and the
  `STANDARD_COLUMNS` / `UNASSIGNED_PROPERTY` constants → `axiom_core`) into the
  single `axiom_db`/`axiom_core` home; `utils` no longer duplicates them.
- **Bug fix:** `_save_file_record_to_sqlite` now accepts and persists `sort_order`
  (a caller passed `sort_order=` into a signature that lacked it, so the call
  raised `TypeError` and was silently swallowed by `except: continue`).

**Phase 4c — Email/SMTP service extracted (A1, first `axiom/` services slice)**
- New `axiom_email.py` (Streamlit-free) holds the email notification service:
  `get_smtp_config`/`save_smtp_config` (SQLite `kv_settings`, via the shared
  `axiom_db` primitives), `_smtp_recipients` (pure parsing), `send_email_via_smtp`
  (the one function doing real network I/O), and `build_loan_reminder_email`
  (pure string formatting). `utils.py` imports and re-exports all of them, so
  every existing call site is unchanged.
- The pure loan-date helpers `_parse_loan_date` / `_fmt_loan_date` / `_loan_date_iso`
  moved to `axiom_core.py` (needed by `build_loan_reminder_email`, and avoids a
  circular import between `utils` and `axiom_email`); re-exported from `utils`
  as before.
- Tests: `tests/test_axiom_core.py` gained date-helper coverage (parsing
  multiple formats, blank/null handling); new `tests/test_axiom_email.py` (12
  tests) covers recipient parsing, a temp-SQLite config round-trip, email body
  formatting, and `send_email_via_smtp` with `smtplib.SMTP`/`SMTP_SSL`
  monkeypatched to a fake — **no real network call is ever made**. (One early
  test draft accidentally exercised a real DNS lookup via the sender-email
  recipient-fallback path before being fixed — now guarded against.)

**Phase 4e — Property-loan CRUD + reminder orchestration extracted (A1, biggest `axiom/` slice yet)**
- New `axiom_loan_crud.py` (502 lines) holds all 16 remaining loan-domain
  functions: `get_loan_target_entity`, `is_loan_target_entity`,
  `ensure_seeded_property_loans`, `get_property_loans`, `get_property_loan_by_id`,
  `save_property_loan`, `delete_property_loan`, `create_property_loan`,
  `log_loan_rate_history`, `get_loan_rate_history`, `match_loan_from_text`,
  `has_reminder_been_sent`, `get_loan_reminder_log`, `get_due_loan_reminders`,
  `send_loan_reminder_email`, `check_and_send_due_loan_reminders`,
  `run_loan_reminder_check_once_per_session`, plus the `LOAN_TARGET_ENTITY_DEFAULT`
  constant. `utils.py` imports and re-exports all of them.
- **Solved the circular-import blocker** flagged in the previous round:
  `get_loan_target_entity`/`get_property_loans`/`save_property_loan` need
  `utils.get_active_entity`/`get_entity_options`/`get_entity_account_codes`/
  `add_entity_account_code`. Fix: a **local** `import utils` inside each
  function body that needs it — never at `axiom_loan_crud.py`'s module top
  level. (An initial attempt with a top-level `import utils` worked when
  `utils.py` was imported first, but broke when a test imported
  `axiom_loan_crud` directly before `utils` — Python re-entered `utils.py`'s
  own partially-finished import of `axiom_loan_crud` and the `from
  axiom_loan_crud import LOAN_TARGET_ENTITY_DEFAULT` failed because that
  module hadn't finished defining anything yet. The local, function-scoped
  import has no such ordering dependency.) Verified by compiling/importing
  both orders explicitly.
- Tests: `tests/test_axiom_loan_crud.py` (22 tests) against a temp SQLite DB +
  temp JSON memory-store fallback — seeding/idempotency, CRUD round-trips,
  rate-history auto-detection on change (two test assumptions about default
  behaviour were wrong on first write and corrected to match the *actual*
  original semantics — `get_property_loan_by_id` doesn't auto-seed, and a
  brand-new loan's first save doesn't log history unless `log_rate_history=True`
  is passed explicitly), loan-text matching, due-reminder detection, and the
  reminder-email path with no SMTP host configured (fails fast on its own
  "host not configured" guard — **no real network call is made** in any test).

**Phase 4f — AI categorization layer extracted (A1, `axiom/` service slice)**
- New `axiom_categorization.py` (333 lines) holds the whole AI categorization
  layer: `get_categorization_system_prompt` (jurisdiction-aware prompt builder +
  NZ loan-coding appendix), `guess_ird_category_local_llm` (OpenAI-compatible
  local Qwen client), `guess_ird_category_gemini` (`google-genai` SDK call),
  `categorize_description_dynamic` (router: rules → configured provider),
  `match_rule_category`, and the DataFrame passes `apply_rule_categorization`
  and `apply_ai_fallback_categorization` (incl. per-description AI-result
  memoization). `utils.py` imports and re-exports all seven; every call site
  unchanged.
- **Single-source constants:** `US_IRS_SYSTEM_PROMPT`, `NZ_IRD_SYSTEM_PROMPT`,
  `DEFAULT_LOCAL_LLM_MODEL`, and `CATEGORIZATION_MODEL_OPTIONS` moved into
  `axiom_core.py` (canonical definition) and are re-exported by `utils.py`, so
  there is exactly one copy of each — no drift risk between the prompt used by
  the router and the prompt displayed/used anywhere else. `DEFAULT_LOCAL_LLM_URL`
  and `GEMINI_OCR_MODEL` were already there. This also removes the need for
  `axiom_categorization` to reach back into `utils` for the model menu.
- **Circular import:** the handlers that still need session/memory-store
  helpers use the established **local `import utils` inside the function body**
  pattern (`get_active_entity`, `get_entity_jurisdiction`,
  `is_portfolio_wide_code`, `is_property_unassigned`, `match_memorized_or_rule`,
  `get_property_loans`), not module-level imports. Verified by importing
  `utils`-first **and** `axiom_categorization`-first; `utils.match_rule_category
  is axiom_categorization.match_rule_category`.
- **Transcription accuracy:** the moved bodies were diffed against the original
  source rather than reconstructed from memory — two early errors were caught
  and fixed (`GEMINI_OCR_MODEL` must be used instead of a `"gemini-3.8-flash"`
  literal; and the duplicated constants listed above would otherwise have
  shadowed the `axiom_core` definitions).
- Tests: `tests/test_axiom_categorization.py` (17 tests) — prompt selection by
  jurisdiction + loan-appendix injection, provider adapters with the OpenAI and
  `genai` clients monkeypatched to fakes (**no network/API call in any test**),
  Gemini's missing-key guard, router precedence (rules win over AI; default
  local vs. selected Gemini), DataFrame rule-fill/unassigned/portfolio-wide
  handling, AI-fallback dedupe + blank-description skip, and re-export identity
  checks.
- **Left in `utils.py` (deliberate), next slices:** the heavy rules engine
  `match_memorized_or_rule` (~200 lines), `parse_gemini_ocr_output` /
  `standardize_ledger_dataframe` / the screenshot-OCR + vault pipeline, and the
  Streamlit render functions.
- **Also fixed this round:** a latent **test-isolation** bug surfaced (not a
  product regression) — `axiom_llm.verify_gemini_connection()` legitimately
  writes `google_genai_api_key` into process-global `st.session_state`, which
  leaked into `test_memory_store_cache` when test order changed. Added
  `tests/conftest.py` with an autouse fixture that snapshots/restores the
  memory-store-related session-state keys around every test. Suite is now
  order-independent (verified both orders + each file alone).

**Phase 4d — Pure loan domain calculations extracted (A1, second `axiom/` domain slice)**
- New `axiom_loans.py` (pure, zero DB/Streamlit) holds the loan domain
  calculation engine: `_loan_row_to_dict` (SQL row → typed dict), `get_loan_trigger_tokens`
  (account-number/keyword matching tokens for auto-categorization),
  `loan_milestones` (expiry-date + reminder-window computation), `_loan_rate_status_tuple`
  (traffic-light colour/label for a loan's fixed-rate expiry), and
  `_loan_dossier_summary_label` (one-line property-setup-matrix summary).
  `utils.py` imports and re-exports all of them; every call site unchanged.
- **Deliberately left in `utils.py`:** the DB CRUD (`get_property_loans`,
  `save_property_loan`, ... — 10 SQL write sites), `_render_loan_card_html`/
  `_nz_bank_logo_svg` (pure but HTML-markup-producing, better suited to the
  Phase 5 UI/stylesheet slice), and the email/reminder orchestration
  (`send_loan_reminder_email`, `check_and_send_due_loan_reminders`) which mixes
  DB + `axiom_email` + session-state "once per session" gating. These need
  either call-signature changes (to avoid a circular import with `utils.get_active_entity`)
  or a deferred-import pattern, and are left for a dedicated future slice.
- Tests: `tests/test_axiom_loans.py` (19 tests) covers row normalization edge
  cases, trigger-token generation (4-part/2-part NZ account formats, shared
  customer-base exclusion, keyword filtering/sorting), milestone computation
  (enabled vs. unparseable dates, day-count accuracy), rate-status thresholds
  (expired/refix-window/warning/green), and dossier label formatting.

**Phase 2d — Script Manager poll fragment-ised (P6/U2)**
- `pages/3_Script_Manager.py`'s live log terminal is now its own `@st.fragment`;
  when "Live Poll" is on and a task is running, Streamlit auto-reruns only that
  fragment (`run_every=1.5`) instead of the previous `time.sleep(1.5); st.rerun()`,
  which blocked the server script thread and forced a full-page rerun/flicker
  every cycle. Start/Pause/Stop/Error buttons and metrics are unaffected (still
  full reruns on click, as before).

**Phase 2e — Pooled SQLite connection (P2)**
- `axiom_db._get_sqlite_conn()` now returns one connection per resolved DB path,
  reused for the life of the process (a small dict-based pool guarded by its own
  lock, distinct from `_DB_LOCK` to avoid self-deadlock) instead of opening a
  fresh `sqlite3.connect()` + re-issuing the WAL/synchronous PRAGMAs on every
  call. Covered by `TestConnectionPooling` in `tests/test_db_roundtrip.py`
  (identity reuse, per-path isolation, repeated-operation stability). This
  benefits every raw-SQL call site in `utils.py` (loans, kv_settings, etc.) for
  free, with zero cache-invalidation risk since it doesn't change read freshness.

**Phase 2f — Memory-store reference-data cache (P1/P3/A4)**
- The hottest reference-data read, `_load_memory_store()` (entities,
  jurisdictions, properties, memorized rules, AI/model settings), used to
  re-read SQLite + re-normalize defaults + write back on **every** call. Split
  into `_load_memory_store_from_storage()` (wrapped in `st.cache_data`, no args
  → one shared cache entry) + a thin session-state wrapper. `_save_memory_store()`
  calls `_invalidate_memory_store_cache()` after persisting, so every write path
  that goes through it (all ~20+ existing call sites — `add/remove/rename_entity_property`,
  memorized-rule training, entity/jurisdiction edits, AI key/model settings,
  etc.) is covered automatically with no per-call-site changes needed.
  `tests/test_memory_store_cache.py` (5 tests) proves: cold-load bootstraps +
  persists; a second call is a genuine cache hit (external DB tampering is
  invisible until invalidated); each call returns an independent copy (safe for
  mutate-then-save callers); and both a direct `_save_memory_store()` call and a
  real write path (`add_entity_property`) correctly invalidate the cache.
- **Deferred (not yet cached):** `get_account_code_options()` / `get_property_loans()`
  read `property_loans` directly and have **10** separate SQL write sites feeding
  them. Caching those needs a wider invalidation sweep I can't visually verify
  headlessly, and a stale loan balance/interest rate is a worse failure mode than
  a slightly slower page — left for a dedicated, live-QA'd slice.

**Phase 2c — Traffic-light evaluation vectorised (P5)**
- New `compute_traffic_light_columns(df, entity)` replaces the per-row
  `df.iterrows()` + `evaluate_row_traffic_light` loop in `pages/1_Dashboard.py`
  Tab 3. It resolves the active entity once and memoizes the expensive
  memorized-rule matcher per *unique* description; cheap red/yellow rows skip the
  matcher entirely. `tests/test_traffic_light.py` proves byte-identical output vs.
  the row-by-row path plus the memoization/skip behaviour.

**Phase 1 — Quick wins**
- Removed `importlib.reload(utils)` (+ now-unused `import utils`/`importlib`) from
  `app.py` and all 4 pages.
- Removed dead/duplicate code in `utils.py`: the overridden first
  `export_entity_vault_zip` and 4 zero-caller `_db_*` helpers (~250 lines).
- `Codes.xlsx` path now via `AXIOM_CODES_XLSX` env var.
- Admin bcrypt hash via `AXIOM_ADMIN_BCRYPT_HASH`; plaintext password removed.
- Stale legacy-entity comments cleaned.

**Phase 2a — Performance (safe subset)**
- `_ensure_sqlite_schema()` now runs once per process via a double-checked
  `_SCHEMA_READY` guard (was re-running `executescript` + PRAGMA migrations on
  every DB call).

**Phase 3 — Tooling & tests**
- `pyproject.toml` (ruff + black + mypy + pytest config), `README.md`,
  `requirements-dev.txt`.
- `tests/test_axiom_core.py`: 47 passing unit tests for the pure helpers.
- Lint cleaned on new/edited files (safe fixes only; legacy unsafe fixes left).

**Phase 4 — Modularisation (first slice)**
- New `axiom_core.py` holds pure, stateless, Streamlit-free helpers +
  `UNCATEGORIZED_MARKERS`; `utils.py` imports/re-exports them so all existing
  `from utils import ...` call sites are unchanged.
- **Bug fix:** `infer_nz_bank_from_account` had a duplicate `"15"` prefix
  (Westpac then TSB); the Westpac mapping was dead. `"15"` now correctly → TSB,
  covered by a regression test.

**Phase 6 — Background tasks**
- Script Manager now clearly labels the worker pipelines as demonstration-mode
  (heartbeat/log only, not wired to live extraction/OCR/categorization).

### ⏸ Staged / deferred (needs live QA or a DB test harness first)

- **Phase 2 (rest):** `@st.cache_data` for the `property_loans` reads
  (`get_account_code_options`, `get_property_loans`) — deferred because they're
  fed by **10** separate SQL write sites; caching needs a wider invalidation
  sweep validated live before shipping (a stale loan balance is a worse failure
  mode than a slower page). *(Traffic-light vectorisation, the pooled DB
  connection, the memory-store cache + invalidation, and the Script Manager
  fragment are now all done — see Phase 2c/2d/2e/2f above.)*
- **Phase 4 (rest):** the DB layer (`axiom_db`), email/SMTP service
  (`axiom_email`), pure loan calculations (`axiom_loans`), the full
  property-loan CRUD + reminder orchestration (`axiom_loan_crud`, Phase 4e),
  and the AI categorization layer (`axiom_categorization`, Phase 4f) are all
  extracted behind the re-export shim. `utils.py` is down to **9,565 lines**
  (from the original ~11,088). What remains: the heavy rules engine
  `match_memorized_or_rule` (~200 lines), the screenshot/OCR parsing + vault
  pipeline (`parse_gemini_ocr_output`, `standardize_ledger_dataframe`,
  `get_screenshot_ocr_*`), and the Streamlit UI render functions —
  `render_property_loans_block` (~840 lines) is now the single biggest target,
  followed by `render_item_property_setup_matrix` (~700),
  `render_right_settings_drawer` (~600), `render_entity_ingestion_pipeline`
  (~460). These are the true bulk of `utils.py`'s remaining size and the
  highest-risk/most-visual-QA-dependent part of the modularisation — continue
  one slice at a time, each gated by tests.
- **Phase 5 (UI/UX):** external stylesheet + design tokens, replacing full-page
  `st.rerun()` with fragments, unifying the dual navigation model, and the
  accessibility pass. Cosmetic-risk changes that require visual QA in a browser.

### Verification snapshot

- `pytest`: **383 collected** (58 core incl. date helpers + 11 DB round-trip + 4
  traffic-light + 3 connection-pooling + 5 memory-store cache + 12 email
  service + 19 loan calculations + 22 loan CRUD/reminders + 17 AI
  categorization + rules engine/CRUD + entity/property CRUD + 66 account-code
  and property-detection + 15 other existing). Suite is **order-independent**
  (autouse session-state isolation fixture in `tests/conftest.py`).
- `py_compile`: all source files compile (incl. `axiom_db.py`, `axiom_email.py`,
  `axiom_loans.py`, `axiom_loan_crud.py`, `axiom_categorization.py`, `app.py`,
  all `pages/*.py`).
- `import utils`: OK (re-exports resolve across `axiom_core` + `axiom_db` +
  `axiom_email` + `axiom_loans` + `axiom_loan_crud` + `axiom_categorization` +
  `axiom_account_codes`;
  `infer_nz_bank_from_account('15-...')` → `TSB`;
  `utils.get_smtp_config`/`utils.build_loan_reminder_email` resolve to
  `axiom_email`; `utils._fmt_loan_date` resolves to `axiom_core`;
  `utils.loan_milestones`/`utils._loan_row_to_dict` resolve to `axiom_loans`;
  `utils.get_property_loans`/`save_property_loan` resolve to `axiom_loan_crud`;
  `utils.match_rule_category`/`utils.apply_rule_categorization` resolve to
  `axiom_categorization`). **Both import orders verified explicitly** (`import
  utils` first, and `import axiom_categorization` first — identical objects) —
  and `app.py` imports cleanly end-to-end.
- `ruff` (whole repo): clean on every file touched this round; only
  pre-existing legacy hints remain in the never-touched
  `scripts/sanitize_fixture.py` (`I001`, `F401`) — the 3 long-standing
  `utils.py` hints (`F841`×2, `B007`) are untouched/unchanged.



---
---

# 10. Decomposition Roadmap — `utils.py` → modules of ≤ 500 lines

> Added 2026-11-17. This section is the authoritative, self-contained plan for
> finishing the modularisation. Each **phase below is sized to become its own
> task** (one branch / one review / one green test run). Earlier sections (1–9)
> remain the historical record; this section supersedes the "Phase 4 (rest)"
> bullet in §9 with a concrete, file-by-file breakdown.

## 10.1 Direct answers to the brief

**How big is the file right now?** `utils.py` is **9,566 lines** (verified via
`wc -l`). The seven already-extracted helper modules (`axiom_core` 214,
`axiom_db` 416, `axiom_email` 168, `axiom_llm` 137, `axiom_loans` 171,
`axiom_loan_crud` 502, `axiom_categorization` 333) total ~1,941 lines that used
to live here, so the original God module was ~11.5k lines.

**Is a 9.5k-line module normal senior practice?** No. It is a textbook
**God Object / God Module** anti-pattern and violates the Single Responsibility
Principle. A senior engineer keeps modules cohesive and small enough to hold in
one's head. There is no universal law, but common, defensible conventions are:

- **Files:** a soft cap of ~300–500 lines per module; hard-stop around 500–800.
- **Functions:** ~40–60 lines typical, hard-stop ~80–100 statements.
- **One reason to change per module** (persistence, auth, parsing, a single UI
  view, …). Mixing SQLite + SMTP + LLM + CSS + every Streamlit view in one file,
  as here, is exactly what to avoid.

So: targeting **≤ 500 lines/file** is a reasonable, industry-normal guardrail,
and the work below brings every file (including the big UI render functions)
under it by **extraction + internal component decomposition**, not by moving
blobs around.

**Why it is safe to do incrementally:** the established **re-export shim**
pattern (`utils.py` does `from axiom_x import …`) means every existing
`from utils import …` / `utils.<name>` call site keeps working unchanged, so each
phase is independently shippable and reversible, gated by `pytest` + both
import-order checks + `ruff`.

## 10.2 Guardrail — "this must never happen again" (do this FIRST)

> **STATUS: D0 DONE (2026-11-17).** Shipped: ruff size/complexity rules in
> `pyproject.toml`; `scripts/check_file_length.py` (500-line cap +
> shrink-only `.axiom_file_length_allow.txt`, seeded with `utils.py` 9565 /
> `pages/1_Dashboard.py` 534); `scripts/snapshot_baseline.py` +
> `scripts/check_extraction.py` (AST byte-identity vs frozen baseline);
> `scripts/gate.py` (one-command acceptance gate incl. both-import-order
> identity); `.pre-commit-config.yaml`; `.github/workflows/ci.yml`;
> `CONTRIBUTING.md`; `AGENTS.md` code-layout rule;
> `docs/DECOMP_PHASE_TEMPLATE.md` (cheap-model prompt). `axiom_loan_crud.py`
> trimmed 502→≤500. Verified: `scripts/gate.py --module axiom_loans` →
> `GATE PASSED` (py_compile, ruff, file-length, extraction 245 symbols,
> identity, pytest 166). **Run `python scripts/snapshot_baseline.py` once
> before D1** (already frozen in this environment).

**Phase D0 — Enforce size limits in tooling + docs.** Without a mechanical gate,
the file will re-bloat. Add, in one task:

1. **Function-size lint (already available in ruff/pylint rules):** enable in
   `pyproject.toml` under `[tool.ruff.lint]`:
   - `PLR0915` (too-many-statements), `PLR0912` (too-many-branches),
     `PLR0913` (too-many-arguments), `C901` (mccabe complexity) with
     `max-complexity = 12` and sensible `pylint` maxes.
2. **File-length gate (ruff has no built-in module-line rule):** add a tiny
   `scripts/check_file_length.py` that fails if any tracked `*.py` exceeds
   **500 lines** (configurable; allow a short grandfather allow-list that must
   only ever shrink — seed it with the current `utils.py` so CI stays green and
   each phase lowers the ceiling).
3. **Wire it in:** add `.pre-commit-config.yaml` (ruff + black + the length
   check) and a `.github/workflows/ci.yml` step running `ruff`, the length
   check, and `pytest`. (Note: §5 already lists a CI file that was never added —
   this closes that gap.)
4. **Document the rule:** add a `CONTRIBUTING.md` (or a "Code layout" section in
   `README.md`) stating the ≤500-line/module + ≤80-stmt/function convention and
   the `axiom_*` module-per-responsibility + `utils.py`-is-only-a-shim rule.
   Add a short line to `AGENTS.md` so agents respect it too.
5. **Fix the one current over-limit module:** `axiom_loan_crud.py` is **502**
   lines — trim/split the 2 lines over (or move the reminder-orchestration trio
   into a sibling `axiom_loan_reminders.py`) so the gate passes from day one.

**Acceptance for every later phase:** `scripts/check_file_length.py` passes with
the touched files removed from the grandfather list; `pytest` green;
`ruff` clean on touched files; `import utils` and importing the new module
first both resolve to identical objects.

## 10.3 Target module map (every file ≤ 500 lines)

Flat `axiom_*.py` convention (consistent with the existing seven modules). Line
estimates are from current source ranges in `utils.py`; the ≤500 cap is the hard
acceptance criterion enforced by D0.

| New module | Responsibility | Functions (current `utils.py` lines) | ~Lines |
|---|---|---|---:|
| `axiom_state.py` | Session/workspace state, active entity/year getters | `get_entity_options`, `get_tax_year_options`, `get_active_entity`, `get_active_tax_year`, `get_entity_jurisdiction`, `format_tax_year_period`, `init_workspace_state` (431–496, 4221–4371) | ~220 |
| `axiom_memory.py` | `.axiom_memory.json` + SQLite memory store load/save/cache | `_load_memory_store_from_storage`, `_invalidate_memory_store_cache`, `_load_memory_store`, `_save_memory_store_unconditional`, `_save_memory_store` (543–708) | ~165 |
| `axiom_vault.py` | Vault paths, raw-file read/write, zip export | `get_vault_dir`, `save_raw_file_to_vault`, `get_vault_file_bytes`, `_save_raw_file_to_vault`, `export_entity_vault_zip` (497–543, 4655–4915) | ~180 |
| `axiom_rules.py` | Memorized-rule store + pattern engine | `get_memorized_rules`, `save_memorized_rule`, `delete_memorized_rule`, `replace_memorized_rules`, `_pattern_matches_row_text`, `match_memorized_or_rule` (944–1137, 5010–5233) | ~420 |
| `axiom_entities.py` | Entity lifecycle + tax-year add | `add_new_entity`, `rename_entity`, `delete_entity`, `add_new_tax_year` (708–875, 3590–3857) | ~300 |
| `axiom_properties.py` | Property-list CRUD, rename/migration, code scope | `get_entity_properties`, `add/remove/rename_entity_property`, `is_portfolio_wide_code`, `set_entity_code_scope`, `PORTFOLIO_WIDE_CATEGORIES` (1137–1182, and 708–875 property block) | ~270 |
| `axiom_account_codes.py` | Account-code CRUD + options + aliases + detection | `get/add/remove/rename_entity_account_code`, `get_account_code_options`, `get_entity_rules`, `get_property_aliases`, `save_property_aliases`, `_derive_smart_property_tokens`, `detect_property_from_text`, `_GENERIC_STREET_SUFFIXES` (1900–2314) | ~415 |
| `axiom_auth.py` | Accounts, bcrypt, auth sessions, authenticator | `_hash/_verify_password_bcrypt`, `_get_admin_password_hash`, `_load/_save_accounts_store`, `get_all_user_accounts`, `update_operator_credentials`, `add/delete_operator_account`, `get_auth_config`, `_load/_save/_clear_active_auth_session`, `sync_and_touch_auth_session`, `sign_out_current_session`, `get_authenticator`, `require_auth` (3857–4221, 9544–9566) | ~400 |
| `axiom_tasks.py` | Background task supervisor (non-UI) | `_append_task_log`, `_background_worker_loop`, `start/pause/stop/trigger_background_task*`, `get_background_status_summary` (4371–4604) | ~235 |
| `axiom_ledger.py` | Ledger get/set/append, uploaded-file load, traffic lights | `get/set/append_entity_ledger`, `is_property_unassigned`, `_load_uploaded_files_from_sqlite`, `evaluate_row_traffic_light`, `compute_traffic_light_columns`, `get_row_status_badge`, `is_row_red_or_unsure` (4687–4997, 5233–5390) | ~460 |
| `axiom_dataframe.py` | Normalisation + Xero export | `export_xero_master_csv`, `_clean_amount_series`, `_dedupe_column_names`, `normalize_financial_dataframe`, `standardize_ledger_dataframe` (7935–8369) | ~435 |
| `axiom_parsing.py` | PDF / XLSX / CSV readers | `extract_pdf_tables`, `parse_xlsx_without_openpyxl`, `extract_other_document_tables`, `_read_bank_csv_bytes` (5651–5794, 6271–6421) | ~295 |
| `axiom_workbook.py` | Coded-workbook sheet parsing + import | `parse_coded_workbook_sheets`, `import_codes_workbook_into_entity` (5794–6271) | ~480 |
| `axiom_ocr.py` | Screenshot/statement OCR (Gemini) | `parse_gemini_ocr_output`, `get_screenshot_ocr_md_path`, `read/save_screenshot_ocr_markdown`, `extract_screenshot_structured_gemini`, `extract_transactions_from_image_gemini`, `run_ocr_on_screenshot_record` (5390–5651, 6595–6674) | ~340 |
| `axiom_loan_ocr.py` | Loan-screenshot extraction | `GEMINI_LOAN_SCREENSHOT_MODELS`, `save_loan_screenshot_to_vault`, `_match_known_btph_property`, `parse_loan_screenshot_json`, `extract_loan_details_from_screenshot_gemini` (2314–2581) | ~270 |
| `axiom_ingestion.py` | Ingestion orchestration (non-UI) | `_reconcile_vault_and_broken_csvs`, `get_entity_uploaded_files`, `sync_entity_ledger_from_files`, `ingest_uploaded_file_object`, `train_memory_from_coded_dataframe`, `get_file_red_counts` (6421–6923) | ~480 |
| `axiom_ui_styles.py` | `apply_custom_styles` + external `assets/styles.css` | `apply_custom_styles` (8369–8660) | ~130 after CSS externalised |
| `axiom_ui_components.py` | Shared small UI widgets | `render_property_editor_widget`, `render_global_status_indicator`, `_render_connection_badge`, `_render_entity_edit_popover`, `_nz_bank_logo_svg`, `_render_loan_card_html`, `get_background_status_summary` badge bits (875–944, 2581–2750, 4604–4655, 8660–8723) | ~450 |
| `axiom_ui_setup_matrix.py` | Item/Property setup matrix view (decomposed) | `render_item_property_setup_matrix` split into ≥2 private sub-renderers (1182–1900) | ≤500 across file |
| `axiom_ui_loans.py` | Property-loans block view (decomposed) | `render_property_loans_block` split: overview / dossiers / editor / screenshot-importer sub-renderers (2750–3590) | ≤500 across file (likely split into 2) |
| `axiom_ui_ingestion.py` | Ingestion/review views (decomposed) | `_render_full_page_file_reviewer`, `_render_bulk_upload_dropzone`, `render_entity_ingestion_pipeline` split (6923–7935) | ≤500 across file (likely split into 2) |
| `axiom_ui_settings.py` | Right settings drawer (decomposed) | `render_right_settings_drawer` split into tabbed sub-renderers (8723–9331) | ≤500 across file |
| `axiom_ui_chrome.py` | Header + sidebar chrome | `render_workspace_header`, `render_sidebar_workspace_controls`, `render_sidebar_session` (9331–9544) | ~220 |

**End state:** `utils.py` becomes a **thin re-export shim** (imports + `__all__`,
well under 500 lines). Optionally a final phase flips `app.py`/`pages/*` to import
directly from the `axiom_*` modules and deletes the shim entirely.

## 10.4 Phased task list (one phase = one task)

Ordered low-risk → high-risk. Pure/logic modules first; Streamlit UI views last
(they need visual QA and internal decomposition). Every phase: extract behind the
shim, add/port unit tests, run `pytest` + both import orders + `ruff` + the
file-length gate.

> **STATUS: D23 DONE (2026-11-17, Opus).** `utils.py` is now a pure
> re-export shim (484 → **410 lines**): `require_auth` moved to
> `axiom_ui_chrome`, `ADMIN_BCRYPT_HASH` to `axiom_auth`, the
> `CATEGORIZATION_RULES` NZ alias to `axiom_core` (all byte-identical, no new
> extraction waiver). `streamlit` is still re-exported as `utils.st` for tests.
> Gate green (`--module axiom_ui_chrome`): py_compile, ruff, file-length,
> extraction, both-import-order identity, **617 tests**. Standing waiver list
> must include `render_right_settings_drawer` (a D21 waiver missing from the
> D20/D22 quote). Shim deletion / page repointing left as optional; the sole
> remaining grandfather entry is `pages/1_Dashboard.py` (534). **Decomposition
> of `utils.py` is complete.**
>
> **STATUS: D16–D22 DONE (2026-11-17).** UI phases split into
> `axiom_ui_styles`, `axiom_ui_components`, `axiom_ui_setup_matrix` +
> `_rules`, `axiom_ui_loans` + `_import` + `_dossiers`, the six-module
> `axiom_ui_ingestion*` family, the four-module `axiom_ui_settings*` family and
> `axiom_ui_chrome`. `utils.py` 4481 → 484 (only `require_auth` remains above
> the shim); all files ≤500 lines and ruff C901/PLR clean. Golden-master logs
> are identical for every phase (D20/D21 after de-duenting multi-line HTML;
> D22 raw). 617 tests pass. Allow-list ratcheted to 484.
> **Next: D23** (finish the `utils.py` shim / retire `require_auth`).
>
> **STATUS: D7–D15 DONE (2026-11-17).** Eleven new modules plus 2,862 lines /
> 161 tests across eleven new `tests/test_axiom_*.py` files (540 tests total):
> `axiom_auth`, `axiom_tasks`, `axiom_ledger`, `axiom_traffic`,
> `axiom_dataframe`, `axiom_parsing`, `axiom_workbook`, `axiom_ocr`,
> `axiom_loan_ocr`, `axiom_ingestion`, `axiom_upload`. Two phases had to be
> split (D9 → ledger + traffic, D15 → ingestion + upload) to stay under the
> 500-line cap; D12's `import_codes_workbook_into_entity` landed in
> `axiom_upload`. Gate waivers now: `get_entity_jurisdiction`,
> `get_account_code_options`, `match_memorized_or_rule`,
> `extract_other_document_tables`, `compute_traffic_light_columns`,
> `get_entity_ledger`. `utils.py` 7672 → 4481; allow-list ratcheted to 4481.
> `tests/test_traffic_light.py` was superseded by `tests/test_axiom_traffic.py`.
> One new ruff per-file ignore: `B007` (unused loop var) in `axiom_tasks.py`.
> **Next: D23** (see the D16–D22 status above).
>
> **STATUS: D2 + D1 DONE (2026-11-17).** `axiom_memory.py` (202 lines) and
> `axiom_state.py` (252 lines) extracted with byte-identical bodies. Config
> constants (`ENTITY_OPTIONS`, `DEFAULT_ENTITY_JURISDICTIONS`,
> `TAX_YEAR_OPTIONS`, `DEFAULT_PROPERTIES`, `MEMORY_STORE_PATH`,
> `BACKGROUND_TASK_CATALOG`) moved verbatim into `axiom_core` so both modules
> can import them without a cycle. D2 ran before D1 because `axiom_state`
> imports `_load_memory_store` from `axiom_memory` (no lazy `import utils`
> needed). C3 landed in D1 (`--allow-changed get_entity_jurisdiction`: the
> `"US" in name.upper()` sniff is gone, unknown entities default to `"NZ"`).
> `utils.py` 9565 → 9164; gate green for `axiom_memory` + `axiom_state`.
>
> **Waiver is permanent until the baseline is refreshed:** every gate run must
> include `--allow-changed get_entity_jurisdiction`, otherwise
> `scripts/check_extraction.py` reports C3 as an unwaived change (exit ≠ 0).
> D3 gate command:
> `.venv/bin/python scripts/gate.py --module axiom_vault --allow-changed get_entity_jurisdiction`
> then ratchet with `.venv/bin/python scripts/check_file_length.py --tighten`.
> Bash here needs `export PATH=/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin` first.

- **D0 — Guardrails** (§10.2): ruff size rules, `scripts/check_file_length.py`,
  pre-commit, CI, `CONTRIBUTING.md`, AGENTS note; fix `axiom_loan_crud` to ≤500.
- **D1 — `axiom_state.py`** (session/workspace getters). Low risk, pure-ish. **DONE**
- **D2 — `axiom_memory.py`** (memory store + cache). Port `test_memory_store_cache`. **DONE**
- **D3 — `axiom_vault.py`** (vault IO; `export_entity_vault_zip` deferred to D9/D10).
  Tests on a temp vault dir. **DONE** — `axiom_vault.py` (104 lines) holds
  `get_vault_dir`, `save_raw_file_to_vault`, `get_vault_file_bytes`,
  `_save_raw_file_to_vault` byte-identically; `tests/test_axiom_vault.py` (23
  tests, temp vault root) covers path building, both write paths and the
  raw-bytes read fallbacks. `utils.py` 9164 → 9102; allow-list ratcheted to
  9102.
- **D4 — `axiom_rules.py`** (memorized-rule engine, `match_memorized_or_rule`).
  The ~200-line rules engine flagged in §9; high-value, well-testable. **DONE** —
  `axiom_rules.py` (462 lines) holds `get_memorized_rules`, `save_memorized_rule`,
  `delete_memorized_rule`, `replace_memorized_rules`, `_pattern_matches_row_text`
  and `match_memorized_or_rule`. Three functions keep their bodies byte-for-byte;
  `save_memorized_rule`, `replace_memorized_rules` and `match_memorized_or_rule`
  call the D5/D6-owned helpers (`detect_property_from_text`,
  `get_entity_account_codes`, `get_entity_rules`, `is_portfolio_wide_code`,
  `is_property_unassigned`) through a function-body `import utils` seam — the
  same pattern as `axiom_categorization`/`axiom_loan_crud` — so they need the
  reviewed waivers `--allow-changed save_memorized_rule replace_memorized_rules
  match_memorized_or_rule` until D5/D6 land. `tests/test_axiom_rules.py` (65
  tests, in-memory store, no disk/DB) covers the four scoring tiers, CRUD/upsert/
  dedup/vendor-auto-rule paths, and all engine priorities (loan > memorized >
  keyword). `match_memorized_or_rule` (C901 36 / PLR0912 36 / PLR0915 89) has a
  narrow `pyproject.toml` per-file ignore, like `axiom_state`; decomposing it is
  a separate reviewed change. `utils.py` 9102 → 8699; allow-list ratcheted to
  8699. **Next: D5.**
- **D5 — entity/property/tax-year CRUD. DONE** — split in two because the ten
  functions total ~461 body lines and a single module with imports/docstring
  lands ~550 (> 500 cap): `axiom_entities.py` (298 lines) holds `add_new_entity`,
  `rename_entity`, `delete_entity`, `add_new_tax_year`; `axiom_properties.py`
  (268 lines) holds `get_entity_properties`, `add/remove/rename_entity_property`,
  `is_portfolio_wide_code`, `set_entity_code_scope` and
  `PORTFOLIO_WIDE_CATEGORIES`. All ten bodies are byte-identical to the
  baseline — the dependencies now come from `axiom_core`/`axiom_db`/
  `axiom_memory`/`axiom_state`, so there is **no** `import utils` seam.
  `axiom_rules`' lazy `utils.is_portfolio_wide_code` call stays untouched (the
  rule-engine tests patch `utils`; rewiring belongs to D6). `rename_entity`
  (C901 23 / PLR0912 23) and `rename_entity_property` (C901 16) carry narrow
  `pyproject.toml` per-file ignores like `axiom_state`/`axiom_rules`.
  `tests/test_axiom_entities.py` (327 lines) and
  `tests/test_axiom_properties.py` (314 lines) share a new `entities_env`
  fixture (temp SQLite DB + vault root, in-memory store, two-entity session
  state) and cover defaults, guards, migrations (store/rules/ledgers/SQLite/
  vault paths), last-entity/last-property guards and re-export identity.
  `utils.py` 8699 → 8221; allow-list ratcheted to 8221. **Next: D6.**
- **D6 — `axiom_account_codes.py`. DONE** — 451 lines holding the ten
  account-code/property-detection symbols (`get/add/remove/rename_entity_account_code`,
  `get_account_code_options`, `get_entity_rules`, `get_property_aliases`,
  `save_property_aliases`, `_derive_smart_property_tokens`,
  `detect_property_from_text`, `_GENERIC_STREET_SUFFIXES`). Bodies are
  byte-identical to the baseline except `get_account_code_options`, which now
  imports `get_memorized_rules` from `axiom_rules` **inside the function body**
  (waived `--allow-changed`) to keep the import graph acyclic. The config data
  those symbols need (`DEFAULT_PROPERTY_ALIASES`,
  `DEFAULT_CUSTOM_ACCOUNT_CODES`, `ENTITY_CATEGORIZATION_RULES`) moved verbatim
  into `axiom_core` (AST-verified identical); `utils` re-exports all three and
  keeps `CATEGORIZATION_RULES = ENTITY_CATEGORIZATION_RULES["NZ"]`
  (`axiom_core.py` 434 lines). `axiom_rules` was rewired off its lazy
  `import utils` seam for the D5/D6-owned helpers (`detect_property_from_text`,
  `get_entity_account_codes`, `get_entity_rules`, `is_portfolio_wide_code`);
  `save_memorized_rule`/`replace_memorized_rules` now match the baseline again,
  `match_memorized_or_rule` keeps only `utils.is_property_unassigned` (D9) and
  its waiver. `tests/test_axiom_account_codes.py` (66 tests on the shared
  `entities_env` fixture) covers NZ/US seeding + rent-income repair, CRUD
  guards, rename migration (store/session ledger/uploaded files/SQLite),
  alias seeding, smart-token derivation, loan fallback and options ordering.
  `utils.py` 8221 → 7672; allow-list ratcheted to 7672. **Next: D7.**
- **D7 — `axiom_auth.py`. DONE** — 376 lines: operator account store
  (`.axiom_accounts.json`), bcrypt hash/verify, `streamlit-authenticator`
  config and the persisted SQLite auth session with its 60-minute sliding
  timeout. `require_auth` intentionally stays in `utils.py` (it also wires
  styles/sidebar/reminder chrome). `tests/test_axiom_auth.py` covers bcrypt,
  the temp account store, credential CRUD and session expiry.
- **D8 — `axiom_tasks.py`. DONE** — 251 lines: worker loop, start/pause/stop/
  error controls and the status summary. `tests/test_axiom_tasks.py` drives the
  loop with real `threading.Event`s and a stubbed worker target.
- **D9 — `axiom_ledger.py` + `axiom_traffic.py`. DONE** — split because the
  ledger slice (incl. `_load_uploaded_files_from_sqlite`) is ~650 lines:
  `axiom_ledger.py` (376) holds vault export, ledger get/set/append,
  `is_property_unassigned` and the SQLite file-record reader;
  `axiom_traffic.py` (172) holds `evaluate_row_traffic_light` /
  `compute_traffic_light_columns` / badge helpers. `get_entity_ledger` now
  imports `get_entity_uploaded_files` inside its body (D15 owns it) — waived.
  `tests/test_traffic_light.py` was replaced by `tests/test_axiom_traffic.py`
  (it patched `utils` globals, which no longer reach the moved functions).
- **D10 — `axiom_dataframe.py`. DONE** — 457 lines: smart column mapper,
  currency cleaning, de-duping, rule pass and Xero CSV export.
- **D11 — `axiom_parsing.py`. DONE** — 318 lines: pdfplumber tables,
  pure-Python XLSX reader, ODS/ODT/TSV extraction, bank-CSV preamble sniffer.
  `extract_other_document_tables` imports `axiom_workbook` in its body to keep
  the graph acyclic (waived).
- **D12 — `axiom_workbook.py`. DONE** — 425 lines: `Codes.xlsx` multi-sheet
  parser (codes, properties, payee rules, per-sheet transactions).
- **D13 — `axiom_ocr.py`. DONE** — 300 lines: Gemini/CSV OCR response parsing
  and the `.ocr.md` sidecars.
- **D14 — `axiom_loan_ocr.py`. DONE** — 301 lines: loan-screenshot extraction,
  model fallbacks, lenient JSON normalisation and property matching.
- **D15 — `axiom_ingestion.py` + `axiom_upload.py`. DONE** — split to stay
  under the line cap: `axiom_ingestion.py` (317) holds vault/SQLite
  reconciliation, the file list, ledger rebuild, memory training and red
  counts; `axiom_upload.py` (340) holds `ingest_uploaded_file_object`,
  `run_ocr_on_screenshot_record` and `import_codes_workbook_into_entity`.
- **D16 — `axiom_ui_styles.py` + `assets/styles.css`. DONE** — CSS externalised
  (263 lines, `{{`/`}}` unescaped) into `assets/styles.css`; `axiom_ui_styles.py`
  (~55 lines) holds `apply_custom_styles` plus `build_custom_css` (pure, testable)
  and the unauthenticated-sidebar rule. `apply_custom_styles` is an intentional
  edit → gate needs `--allow-changed apply_custom_styles` (add to the standing
  waiver list: `get_entity_jurisdiction get_account_code_options
  match_memorized_or_rule extract_other_document_tables
  compute_traffic_light_columns get_entity_ledger apply_custom_styles`).
  Whitespace-normalised output was compared with the pre-move f-string for both
  `authenticated=True/False`: identical. `tests/test_axiom_ui_styles.py` (5
  tests). `utils.py` 4481 → 4192. **Manual browser QA still required** (cannot
  be done headless). **Next: D17.**
- **D17 — `axiom_ui_components.py`. DONE** — 384 lines: `render_property_editor_widget`,
  `_nz_bank_logo_svg`, `_render_loan_card_html`, `render_global_status_indicator`,
  `_render_connection_badge`, `_render_entity_edit_popover`. All six bodies are
  byte-identical (extraction check green with no new waiver; every dependency
  comes from an already-extracted module, so no `import utils` seam).
  `tests/test_axiom_ui_components.py` covers all bank logos, badge states, card
  escaping/fallbacks and the status indicator. `utils.py` 4192 → 3844.
- **D18 — `axiom_ui_setup_matrix.py` + `axiom_ui_setup_rules.py` (382) +
  `axiom_ui_setup_editor.py` (350) + `axiom_ui_setup_common.py` (71). DONE** —
  the ~737-line matrix view is now an orchestrator (`_build_matrix_context` →
  `_render_quick_setup_card` → `render_saved_rules_card`) over ctx-based
  sub-renderers; every sub-renderer unpacks ``MatrixContext`` fields into locals
  so the moved bodies stay textually identical apart from indentation.
  **Equivalence proven against the frozen baseline**: a recording fake of
  `streamlit` (`tests/ui_recorder.py`) renders the pre-refactor
  `.axiom_baseline/utils.py` implementation and the new modules side by side over
  8 scenarios (no rules, 4 rules, edit mode, copy mode, quick-add save, item
  rename, table view, filtered view): all 127/57/40/20-call logs identical.
  `tests/test_axiom_ui_setup_matrix.py` (7 tests) keeps the ctx builder, quick-add
  row, rename/add/rerun paths and the saved-rules card covered. Extraction
  waiver: `--allow-changed render_item_property_setup_matrix`.
- **D19 — `axiom_ui_loans.py` + `axiom_ui_loans_import.py` (293) +
  `axiom_ui_loans_dossiers.py` (480) + `axiom_ui_loans_common.py` (62). DONE** —
  the ~840-line loans block is now `render_property_loans_block` (orchestrator
  that keeps the single `st.container(border=True)` open) → `render_loan_header`,
  `render_loan_screenshot_importer` (container + `_render_loan_ocr_upload` /
  `_render_loan_ocr_review`), `render_loan_summary_strip`,
  `render_loan_spotlight`, `render_loan_overview_table`, `render_loan_dossiers`
  and `_render_loan_edit_form`. `LoanBlockContext` carries the per-render keys;
  the option lists moved to `axiom_ui_loans_common` and are re-exported by
  `utils.py`. The per-loan edit form is a 260-line block that is byte-identical
  to the pre-move text (checked line by line) but still must be called; it is
  kept whole because every widget is order/key sensitive.
  **Equivalence proven** with the same recorder against the pre-move `utils.py`
  over 7 scenarios (empty portfolio, compact, full overview + dossiers, importer
  at top with OCR data, due-reminder send, add-loan click): all logs identical,
  including the nested `ENTER/EXIT` container structure. `utils.py` 3144 → 2289.
  `tests/test_axiom_ui_loans.py` (9 tests). Extraction waiver:
  `--allow-changed render_property_loans_block`. Standing waiver list is now
  `get_entity_jurisdiction get_account_code_options match_memorized_or_rule
  extract_other_document_tables compute_traffic_light_columns get_entity_ledger
  apply_custom_styles render_item_property_setup_matrix
  render_property_loans_block`.
- **D20 — `axiom_ui_ingestion.py` (224) + `axiom_ui_ingestion_board.py` (372) +
  `axiom_ui_ingestion_review.py` (387) + `axiom_ui_ingestion_review_sheet.py`
  (206) + `axiom_ui_ingestion_upload.py` (144) + `axiom_ui_ingestion_common.py`
  (31). DONE** — the ~1,012-line ingestion block is now a thin pipeline
  (`render_entity_ingestion_pipeline`) that delegates the 4-column board to
  `axiom_ui_ingestion_board`, the full-page reviewer to
  `axiom_ui_ingestion_review` (nav bar, metrics, quick-fix bar) +
  `axiom_ui_ingestion_review_sheet` (filter/editor/save), and the bulk dropzone
  to `axiom_ui_ingestion_upload`. The board's 96-statement
  `_render_ingestion_board` was split into `_render_bucket_header`,
  `_render_screenshot_ocr_bar`, `_file_status_style` (pure),
  `_render_file_card`, `_render_bucket_file_rows` and
  `_render_bucket_quick_add`; widget order, keys, column specs and container
  nesting are unchanged (no extra `st.container`).
  **Equivalence proven** by a FakeSt golden master: old =
  the pre-D20 `utils.py` snapshot (`/tmp/utils_pre_d20.py`, board inline in the
  pipeline) vs new = `axiom_ui_ingestion*`, with the same dependency names
  patched in both namespaces. The same 13 scenarios were also run against the
  frozen `.axiom_baseline/utils.py` tree (PYTHONPATH resolving the baseline
  modules) and produced the identical result. The scenarios cover the closed pipeline (empty,
  stale open id, loans block on/off), all four buckets (red/yellow/green/
  needs-OCR/summary/0-row cards), quick-add with and without staged files
  (message/nonce paths, batch dropzone), the reviewer with mixed red/yellow/
  green rows, the red filter, the all-green batch message, the default-property
  quick fix, the screenshot OCR panel and the setup-matrix toggle. Every log is
  identical (44–135 calls/scenario). Raw log strings differ only in *leading
  whitespace* inside multi-line HTML f-strings — the unavoidable dedent from
  lifting the nested board/reviewer bodies to module level; after stripping
  common line indentation the logs are byte-for-byte equal, and no non-string
  argument differs.
  `tests/test_axiom_ui_ingestion.py` (9 tests) and
  `tests/test_axiom_ui_ingestion_review.py` (7 tests). `utils.py` 2289 → 1295.
  Extraction waivers: `--allow-changed render_entity_ingestion_pipeline
  _render_full_page_file_reviewer` (`_render_bulk_upload_dropzone` unchanged).
  Standing waiver list is now `get_entity_jurisdiction get_account_code_options
  match_memorized_or_rule extract_other_document_tables
  compute_traffic_light_columns get_entity_ledger apply_custom_styles
  render_item_property_setup_matrix render_property_loans_block
  render_entity_ingestion_pipeline _render_full_page_file_reviewer`.
- **D21 — `axiom_ui_settings.py` + `_ai` + `_email` + `_accounts`. DONE** — the
  608-line `render_right_settings_drawer` is now a shell
  (`_render_settings_header` + seven `st.tabs`) that delegates: Organizations &
  Properties / Loans / Files / Item+Property in `axiom_ui_settings.py` (233),
  AI providers in `axiom_ui_settings_ai.py` (185), SMTP + reminders in
  `axiom_ui_settings_email.py` (93), operator accounts in
  `axiom_ui_settings_accounts.py` (204). Tab code order and widget keys are
  unchanged. **Equivalence proven** by a FakeSt golden master: old =
  `/tmp/utils_pre_d21.py` vs new = the four settings modules, with the same 36
  dependency names patched in both namespaces; 15 scenarios (closed drawer,
  all tabs, every flash, org save/switch, add entity, Gemini/local verify
  ok/fail, AI key change, SMTP save/test/check-due, accounts remove) are
  identical (0–221 calls/scenario; raw diffs are only the same leading-HTML
  dedent as D20). `tests/test_axiom_ui_settings.py` (9 tests). `utils.py`
  1295 → 1137. Extraction waiver: `--allow-changed render_right_settings_drawer`.
- **D22 — `axiom_ui_chrome.py` (245). DONE** — `render_workspace_header`,
  `render_sidebar_workspace_controls` and `render_sidebar_session` moved
  byte-for-byte out of `utils.py` (top-level bodies, so unlike D20/D21 there is
  no dedent), with the settings drawer delegated to `axiom_ui_settings`.
  **Equivalence proven** by a FakeSt golden master: old =
  `/tmp/utils_pre_d22.py` vs new = `axiom_ui_chrome`, with the same dependency
  names patched in both namespaces; 8 scenarios (header closed, header with
  drawer open + stop, status indicator on, US/NZ jurisdiction, connected Gemini
  / local badges, year-pill switch, failed add-year, sidebar entity switcher,
  sidebar sign-out/settings) produce **raw byte-identical** call logs (no
  whitespace normalisation needed; 0–8 calls/scenario). `utils.py` 1137 → 484,
  leaving only `require_auth`; extraction and both identity checks pass with no
  D22-specific waiver, and the standing waiver list is unchanged.
- **D23 — Shim shrink / retire `require_auth`. DONE (2026-11-17, Opus)** —
  `utils.py` is now a **pure re-export shim (410 lines)**. The last three pieces
  of real content were relocated byte-for-byte: `require_auth` → `axiom_ui_chrome.py`
  (its natural page-chrome home, next to `render_sidebar_session`; it now imports
  `get_authenticator`/`sync_and_touch_auth_session` from `axiom_auth`,
  `apply_custom_styles` from `axiom_ui_styles`, `run_loan_reminder_check_once_per_session`
  from `axiom_loan_crud` — no import cycle, both orders verified);
  `ADMIN_BCRYPT_HASH` → `axiom_auth.py`; the `CATEGORIZATION_RULES` NZ alias →
  `axiom_core.py`. `utils.py` re-exports all three so every `from utils import …`
  / `utils.<name>` call site (all 4 pages import `require_auth` from `utils`) is
  unchanged; `streamlit` is still re-exported as `utils.st` for the handful of
  tests that reach `utils.st.session_state`. The `require_auth` body is
  AST-identical to the baseline (no extraction waiver needed). Gate green:
  `scripts/gate.py --module axiom_ui_chrome` → **GATE PASSED** (py_compile, ruff,
  file-length, extraction, both-import-order identity, **617 tests**). The
  standing extraction-waiver list must also include `render_right_settings_drawer`
  (a D21 waiver that was omitted from the list quoted in the D20/D22 notes).
  **Not done (deliberately out of scope):** physically deleting the shim +
  repointing `app.py`/`pages/*` to direct imports is optional and higher-churn —
  the shim is kept so all call sites stay stable. The one remaining grandfather
  allow-list entry is `pages/1_Dashboard.py` (534 lines) — a pre-existing
  Streamlit page, not part of the `utils.py` roadmap; trimming it needs browser
  QA and is tracked separately.

## 10.5 Opportunistic fixes to fold in (from §3 findings, do them in the owning phase)

- **C3** (`axiom_state.py`, D1): drop the brittle `"US" in resolved_entity.upper()`
  at `utils.py:477`; rely solely on the stored `entity_jurisdictions` map.
- **C2 / M4** (every phase): introduce a real `logging` module in each extracted
  file and narrow the ~60 bare `except Exception:` blocks as code is moved — do
  it per-slice, never as a big-bang sweep.
- **S3** (`axiom_auth.py` D7 / `axiom_memory.py` D2): assert API keys / SMTP creds
  are excluded from vault/exports and never logged; add a regression test.
- **M3** (as encountered): centralise magic strings (model names, bucket/status
  labels) into `axiom_core` constants while moving the owning function.

## 10.6 Risk & sequencing notes

- **UI phases (D16–D22) are the real work and the real risk.** Four functions
  (`render_property_loans_block` ~840, `render_item_property_setup_matrix` ~718,
  `render_right_settings_drawer` ~608, `render_entity_ingestion_pipeline` ~461)
  each exceed 500 lines alone, so they must be **split into sub-components**, not
  relocated. These need headless-compile + manual browser QA; keep each to one
  view per task.
- **Do the non-UI phases (D1–D15) first** — they carry unit-testable logic and
  retire most bare-except/logging debt with low visual-QA cost.
- **Keep the shim until D23** so each phase is reversible and all call sites stay
  untouched; only flip to direct imports once everything is under the cap.
- **Privacy:** all tests use temp SQLite / sanitised fixtures per `AGENTS.md`;
  no reads of `data/`, `*.db`, exports, or secrets; all network clients
  (Gemini/OpenAI/SMTP) monkeypatched in tests.


---

# 11. UI Cleanup — Navigation, Header & Button Declutter

## 11.1 Goal
Make the app easier to read and use:
- Give pages names that say what they do. "app" and "Dashboard" currently tell the user nothing.
- Remove duplicate pages, buttons and Settings tabs.
- Shrink the busy top-right corner to one status control.
- Give every control exactly one home.

Constraints: dark charcoal/graphite palette only, ≤ 500 lines per file, and the Settings exclusive-view rule is preserved.

## 11.2 Analysis — what's wrong today (UI expert review)

**Same thing shown in 2–3 places:**

| Thing | Where it appears now |
|---|---|
| Upload / 4-column board (`render_entity_ingestion_pipeline`) | `app.py`, `pages/2_Data_Ingestion.py` (an exact duplicate), Settings tab 03 |
| Property Loans (`render_property_loans_block`) | `pages/4_Property_Loans.py`, Settings tab 02 |
| Item + Property Setup | Dashboard tab 01, Settings tab 04 |
| ⚙ Settings button | Header (`_top_toolbar_settings_btn`) and sidebar (`_sidebar_open_settings_btn`) |
| Entity + year title | Header banner H2 and Dashboard `st.title` |

**What "app" vs "Dashboard" actually are:**
- **"app"** (`app.py`) is the login gate plus the Upload & 4-column File Board. It is where files come in. Streamlit names it after the filename.
- **"Dashboard"** (`pages/1_Dashboard.py`) is review and bookkeeping: Item/Rule setup, Property matching, the Master Ledger with red/yellow/green alerts, and the Accountant export. It is not really a dashboard.
- **"Script Manager"** is a demo-only thread supervisor, and it labels itself as not doing real work.

**Top-right corner today (per page):**
- Two badges: `GEMINI` and `LOCAL AI`.
- The `⚙ Settings (Orgs, AI & Accounts)` button.
- The `BACKGROUND DAEMONS` card, which reports the status of the demo threads.
- Streamlit's own ⋮ toolbar menu. This is probably the "tool" icon you meant. It comes from `toolbarMode="minimal"` in `.streamlit/config.toml`.
- Below these, a full-width financial-year pill bar (`2026 … 2029 | + Add Year`).

## 11.3 Control inventory & verdicts

| Control | Location | Verdict |
|---|---|---|
| Workspace banner (eyebrow + pill + H2) | `render_workspace_header` | **Slim** to one line: `Entity · NZ IRD · FY2026 (period)` |
| GEMINI + LOCAL AI badges | `_render_connection_badge` | **Merge** into a single `● AI ▾` status popover |
| BACKGROUND DAEMONS card | `render_global_status_indicator` | **Move** into the same popover |
| Header ⚙ Settings button | `_top_toolbar_settings_btn` | **Remove.** The sidebar button becomes the only entry |
| Year pill bar + "+ Add Year" | header `year_bar_cols` | **Move** to the sidebar as `st.selectbox` + a small `＋` popover |
| Streamlit ⋮ toolbar | `config.toml` | **Hide extras** with `toolbarMode="viewer"` |
| Sidebar org buttons `● Name [NZ IRD] (rows)` | `render_sidebar_session` | **Keep, simplified.** Show name + muted row count, drop the `[jur]` text |
| "SYSTEM CONFIGURATION" / "CORE" eyebrows | sidebar | **Remove** |
| Operator / Clearance / Auto-Logout block | sidebar | **Collapse** to `👤 name  [Sign out]`. The hard-coded "Clearance" goes; Auto-Logout info moves to the Accounts tab |
| "Open Item + Property Setup & Master Ledger →" | `app.py`, `2_Data_Ingestion.py` | **Remove.** Navigation replaces it |
| "← Back to 4-Column File Upload Board" | `1_Dashboard.py` | **Remove** |

## 11.4 Proposed page structure (`st.navigation` / `st.Page`, streamlit 1.67)

```
app.py  = router only (set_page_config, auth gate, chrome, st.navigation(...).run())
  Workspace
    📥 Inbox            views/inbox.py      ← was app.py board ("app")
    ✅ Review & Ledger  views/ledger.py     ← was pages/1_Dashboard.py ("Dashboard")
    🏦 Loans            views/loans.py      ← was pages/4_Property_Loans.py
  (debug only, AXIOM_DEBUG=1)
    🛠 Background Jobs  views/jobs.py       ← was pages/3_Script_Manager.py
DELETED: pages/2_Data_Ingestion.py (duplicate of Inbox)
```

- **Review & Ledger:** the metrics row stays on top. Reorder the tabs to Master Ledger → Accountant Export → Properties → Rules & Items, and drop the `01 //` numbering.
- **Before login:** `st.navigation([login_page])`. The nav is hidden naturally, which replaces the `stSidebarNav {display:none}` CSS hack in `axiom_ui_styles.py`.
- **Alternative name for "Dashboard":** "Ledger". For "app": "Upload". Your choice.

## 11.5 Wireframes

**Header (every page):**
```
┌──────────────────────────────────────────────────────────────────────┐
│ BT Property Holdings · NZ IRD · FY2026 (Apr 25–Mar 26)       ● AI ▾  │
├──────────────────────────────────────────────────────────────────────┤
│ Review & Ledger                                                      │
│ [Net $…] [🟢 Sure …] [🟡 Check …] [🔴 Missing …]                     │
```

**The `● AI ▾` popover** shows one dot: graphite when OK, muted red `#8A6B70` on error. Its contents:
```
 Gemini      ● Connected  (active)
 Local AI    ○ Offline
 Background  ▪ Idle
 [Open AI settings →]
```

**Sidebar:**
```
AXIOM LEDGER
─ Workspace ─────
 📥 Inbox
 ✅ Review & Ledger
 🏦 Loans
─ Organization ──
 ● BT Property Holdings   1,204
 ○ Marine Trust              88
─ Financial year ─
 [ 2026        ▾ ] [＋]
 …
 ⚙ Settings
 👤 admin       [Sign out]
```

## 11.6 Settings drawer — final tabs

- **Remove** the tabs that duplicate pages: 02 Loans, 03 Upload & File Table, 04 Item + Property Setup.
- **Keep:** Organizations & Properties · AI & Connections · Accounts & Security · Email & Reminders.
- **Header:** just "Settings". Keep the exclusive view and `st.stop()`.

## 11.7 Options

| Option | Scope | Effort | Risk |
|---|---|---|---|
| **A. Minimal cleanup** | Delete Data Ingestion page. Hide Script Manager. Remove the header Settings button, the cross-nav buttons and the daemons card. Add the merged AI popover. Move the year selector to the sidebar. Rename via filenames (`1_Review_Ledger.py`). "app" keeps its name. | ~½ day | Low |
| **B. Full page-structure rework (recommended)** | A + `st.navigation` router (gives real names "Inbox" / "Review & Ledger", grouped nav, nav hidden before login) + remove the 3 duplicate Settings tabs + reorder Ledger tabs | ~1–1.5 days | Medium |
| **C. B + visual polish** | B + move inline CSS into `assets/styles.css` with CSS variables, sentence-case labels instead of ALL-CAPS eyebrows, Material icons (`:material/…:`), `st.toast` for flash messages. Covers §4 U1/U5/U6/U7 | ~2–3 days | Medium–High (many tests assert HTML) |

**Recommendation:** B, with sentence-case labels and Material icons from C on the nav and buttons only.

## 11.8 Files to modify / create (Option B)

| File | Change |
|---|---|
| `app.py` | Becomes the router: one `set_page_config`, auth gate/login page, chrome, `st.navigation(...)` |
| `views/inbox.py` (new) | Body of the old `app.py` board (`key_prefix="front_board"` kept) |
| `views/ledger.py` (new) | From `pages/1_Dashboard.py`. Cross-nav button and duplicate title removed, tabs reordered |
| `views/loans.py` (new) | From `pages/4_Property_Loans.py` |
| `views/jobs.py` (new) | From `pages/3_Script_Manager.py`. Registered only when `AXIOM_DEBUG=1` |
| `pages/*.py` | Deleted. The `pages/` dir must go, or Streamlit's auto-discovery conflicts with `st.navigation` |
| `axiom_nav.py` (new) | `st.Page` definitions + page constants (no scattered `switch_page` strings) |
| `axiom_ui_status.py` (new) | `● AI ▾` status popover (merges the badges + daemon status) |
| `axiom_ui_chrome.py` | Slim `render_workspace_header` (banner + status popover only). In `render_sidebar_session`: year selectbox + add-year popover, simplified org list, single Settings, compact user/sign-out. Page-change detection reworked (see risks) |
| `axiom_ui_settings.py` | Drop tabs 02/03/04 and their imports. Header becomes "Settings" |
| `axiom_ui_settings_email.py` | L74 `switch_page("pages/4_Property_Loans.py")` → Loans page from `axiom_nav` |
| `axiom_auth.py` / `require_auth` (chrome) | Under the router, the auth gate lives in `app.py`. `require_auth` reduces to a guard + `st.stop()` |
| `axiom_ui_styles.py` | Remove the `stSidebarNav` hide hack. Add small styles for the status dot |
| `.streamlit/config.toml` | `toolbarMode = "viewer"` |
| `tests/test_axiom_ui_chrome.py`, `tests/test_axiom_ui_settings.py` (+ new `tests/test_axiom_nav.py`, `tests/test_axiom_ui_status.py`) | Update the key/label assertions. Add nav and status tests |
| `scripts/check_file_length.py` allowlist | Update if paths change |
| `README.md` | Update the page list / run instructions |

## 11.9 Dependencies & ripple effects

- **Settings exclusive view:** must still render alone and `st.stop()` before any page body. Under the router it runs once, before `pg.run()`.
- **Close Settings on page change:** `ctx.main_script_path` is always `app.py` under `st.navigation`, so the current detection would silently stop working. Compare the `st.navigation(...)` returned page's `url_path` against `_last_rendered_page` instead. Add a test for this.
- **`switch_page` targets** in `axiom_ui_chrome.py` (sign-out, auth gate) and `axiom_ui_settings_email.py` move to `axiom_nav` constants.
- **Widget state:** the ingestion pipeline currently runs with three `key_prefix` values (`front_board`, `ing_page`, `settings_file_board`). Keep `front_board` and drop the others.
- **Sidebar cost:** `get_entity_ledger(ent, year)` runs for every org on every rerun just to show row counts. Cache it, or show the count only for the active org.
- **`utils.py`:** stays a re-export shim (~484 lines). New code goes into new `axiom_*` modules only.

## 11.10 Implementation steps (each step is a separate, testable task)

1. **Nav scaffold:** add `axiom_nav.py` and move the page bodies into `views/` (bodies moved unchanged). Turn `app.py` into the router. Delete `pages/`. Smoke-test that login, each page and sign-out work.
2. **Page-change detection:** fix it for the router and keep the Settings close-on-navigate rule. Add a test.
3. **Header slim-down:** new `axiom_ui_status.py` popover. Remove the header Settings button, the badges, the daemons card and the year pill bar from `render_workspace_header`.
4. **Sidebar rework:** year selectbox + `＋` popover, simplified org buttons, single Settings button, compact user + Sign out. Cache the row counts.
5. **Remove cross-nav buttons and duplicates:** take out the "Open…/Back…" buttons, the duplicate Ledger title and the Data Ingestion duplicate. Reorder the Ledger tabs.
6. **Settings drawer:** remove tabs 02/03/04 and retarget the email-tab Loans link.
7. **Polish (light C):** sentence-case nav/button labels, Material icons, `toolbarMode="viewer"`.
8. **Verify:** `pytest`, `ruff`, `scripts/check_file_length.py`, then a manual QA pass in the browser (login → each page → Settings open/close via sidebar click → sign out).

## 11.11 Risks & considerations

- **Tests:** many assert exact button keys and labels (`_top_toolbar_settings_btn`, `_fy_pill_btn_*`, `"{ENTITY} [NZ IRD] (2)"`, settings tab key prefixes). These are intentional test updates, not regressions.
- **Auto-discovery conflict:** `st.navigation` together with an existing `pages/` dir causes Streamlit to ignore or warn about the auto-discovered pages, so `pages/` must be removed in the same step.
- **Bookmarks:** URLs change (`/Dashboard` → `/ledger`). This is a local app, so the impact is low.
- **Behaviour change:** the year selector moves from a pill bar to a dropdown. It takes one more click but scales past 4 years.
- **Out of scope:** this is not the byte-for-byte §10 decomposition. These are deliberate UI changes, so if `scripts/gate.py` is run on the touched modules it needs `--allow-changed`.
- **Privacy:** no real data is read during implementation or QA. Use `fixtures/sanitized_*` only.

## 11.12 Implementation status (Option B, names "Inbox" / "Review & Ledger")

✅ Done & verified (pytest green, ruff clean, file-length guard OK):
- `app.py` is now the router (login gate → reminder check → `st.navigation` → sidebar → header → `page.run()`). `axiom_nav.py` holds page registry + `*_PAGE` constants; `views/{inbox,ledger,loans,jobs}.py`; `pages/` removed (old files backed up to `/tmp/axiom_pages_backup`, also in git history).
- Header: one-line banner + `axiom_ui_status.render_status_popover` ("● AI · <provider>" popover with both badges, background status, "Open AI settings"). Header Settings button, badges row, daemons card and year pills removed.
- Sidebar: org switcher (`● Name · rows`, jurisdiction in tooltip), year `selectbox` (`_sidebar_fy_select`, `on_change` callback) + `＋` add-year popover, single ⚙ Settings toggle, `👤 name · auto-lock 60 min` + Sign out (now `st.rerun()` instead of `switch_page("app.py")`).
- Settings close-on-navigate now keyed on the router's selected page (`page_identity`), tested.
- Settings drawer: 4 tabs (Organizations & properties, AI & connections, Accounts & security, Email & reminders); the embedded loans block in the email tab was also removed (the "Open Property Loans Page" button → `LOANS_PAGE`).
- Review & Ledger: cross-nav button + duplicate title removed; tabs reordered (Master ledger → Accountant → Properties → Rules & items); sentence-case metric labels.
- `toolbarMode = "viewer"`; Background Jobs page only with `AXIOM_DEBUG=1`.
- New tests: `tests/test_axiom_nav.py`, `tests/test_axiom_ui_status.py`; chrome/settings tests updated.

Deliberately kept: `_UNAUTHENTICATED_SIDEBAR_CSS` in `axiom_ui_styles.py` — unauthenticated runs stop before `st.navigation`, so the CSS is still what hides the sidebar on the login screen.

⏸ Pending: manual browser QA (login → each page → Settings open/close via nav click and org click → year switch/add → sign out). Not run by the agent because it would touch the real local DB.


---

# 12. Header AI status button: visual polish

## 12.1 Goal
The header status button ("○ AI · Local AI · offline") looks out of place next to the workspace banner. Make it read as part of the header, quiet when everything is normal and clear only when something is actually wrong.

## 12.2 Analysis (UI expert)
- **Two unrelated objects.** The banner is a bordered card with an accent stripe. The status is a bare, full-width grey button in a separate column.
- **Height and alignment.** The card is about 4rem tall and the button about 2.4rem. The banner's `margin-bottom: .75rem` also pushes the button below the card's centre line.
- **Typography.** The button uses the default button font plus Streamlit's ▾ chevron, while the banner uses an eyebrow label and an h2.
- **Wording.** `○`/`●` are text glyphs that can't be coloured separately. "AI · Local AI · offline" is redundant, and "offline" reads like an alarm even though Local AI being off is often normal.
- **Global styling.** The popover CSS is global (`[data-testid="stPopover"] > div > button`), so the fix must be scoped or it will also restyle the sidebar ＋ and the 📌 pins.
- **Demo card.** The popover shows the demo-only "BACKGROUND DAEMONS" card on every page.

## 12.3 Options
- **A. Header card owns both sides (recommended).** The header becomes one `st.container(key="ws_header")` styled as the card. Status is a small chip inside it, on the right.
  ```
  ┌▌ NZ IRD · TAX PERIOD …                          ● Local AI ▾ ┐
  │  BT Property Holdings — FY 2026                              │
  └──────────────────────────────────────────────────────────────┘
  ```
- **B. Standalone chip beside the banner.** Smaller change, but it still reads as two separate objects.
- **C. Icon-only trigger** (`:material/memory:` + dot). Quietest, but the least discoverable.

## 12.4 Implementation steps (Option A)
1. **`axiom_ui_chrome.render_workspace_header`**
   - Wrap the header in `st.container(key="ws_header")` with columns `[4, 1]`.
   - Keep the eyebrow + h2 strings, but drop the card styling from the inner HTML so there is no double border.
   - Right-align the status column.
2. **`axiom_ui_status`**
   - `summarize_status` returns `(state, label, tooltip)`, where state is `ok`, `idle` or `error`.
   - The label is just the provider name (`Local AI` / `Gemini` / `Rules only`). The state detail moves into `help=`.
   - Wrap the popover in `st.container(key=f"ws_status_{state}")` and set `use_container_width=False`.
   - States: `idle` = the active provider is offline. `error` = a background job is in error, or the active provider reports a failure.
3. **Popover contents**
   - Replace the two inline-styled badges and the daemon card with one tidy 3-row list (`.axiom-status-row`): the active provider, the other provider, and background jobs.
   - Show the background row only when `AXIOM_DEBUG` is on or a job is running, paused or in error.
   - Keep the **Open AI settings** button.
4. **`assets/styles.css`** (all rules scoped to `.st-key-ws_header`)
   - Card styles for the header container.
   - Compact chip style for the trigger: transparent background, 1px `#2A2D34` border, 0.78rem text.
   - A CSS `::before` dot:
     - ok: filled `#A0A5B1`
     - idle: hollow `#8E939F` ring
     - error: filled muted red `#8A6B70`
   - No glow. Red is used only for real failures.
   - `.axiom-status-row` styles for the popover list.
5. **Tests**
   - Update `tests/test_axiom_ui_status.py`: new labels/states, popover kwargs, list rows instead of badges, background-row visibility.
   - Update the header assertions in `tests/test_axiom_ui_chrome.py`.

## 12.5 Risks
- **Streamlit class names.** The CSS relies on `.st-key-<key>` container classes, which exist in Streamlit 1.38+ (we pin 1.67). A future Streamlit DOM change could break the selectors, but the fallback is just the plain default button.
- **Old badge helpers.** `_render_connection_badge` and `render_global_status_indicator` stay in `axiom_ui_components` (other callers and tests); the header just stops using them.
- **Verification.** Needs a quick visual check in the browser in all three states: Gemini connected, Local AI offline, background job error.

## 12.6 Implementation status
✅ Option A implemented:
- **Header card:** `axiom_ui_chrome.render_workspace_header` now renders one `st.container(key="ws_header")` card holding the title plus the status chip.
- **Status chip:** `axiom_ui_status` was rewritten.
  - `provider_state` / `summarize_status` produce the `ok` / `idle` / `error` state and a tooltip.
  - The chip is wrapped in `st.container(key="ws_status_<state>")`.
  - The popover body is a `.axiom-status-list` of escaped rows. The background row shows only in debug mode or when jobs are not idle.
- **CSS:** scoped rules in `assets/styles.css`: `.st-key-ws_header`, the chip, the `::before` dot and `.axiom-status-*`.
- **Checks:** tests updated in `tests/test_axiom_ui_status.py`; pytest, ruff and the file-length guard all pass.

⏸ Needs a visual check in the browser. Restart Streamlit, because the CSS is cached per process (`_load_base_css` uses `lru_cache`).


---

# 13. Header spacing + sidebar organization menu redesign

## 13.1 Goal
- Give the header card proper top and bottom breathing room.
- Redesign the left sidebar organization list so it looks like the page links above it: a quiet list, not a stack of boxed buttons.

## 13.2 Analysis (UI expert)
- **Org rows:** these are stock Streamlit buttons (filled `#262930`, boxed border, centred text). The `●/○ name · count` label is squeezed into a 210–255px sidebar, so long names wrap. They look unrelated to the `st.navigation` links.
- **Active org:** shown as a loud filled primary button rather than a calm "selected row".
- **Mixed styles:** the sidebar mixes five treatments (eyebrow, dividers, caption, boxed Settings, boxed Sign out). Settings and Sign out compete visually with the org list.
- **Header crowding:** `.main .block-container` has only `padding-top: 1.5rem`, and Streamlit's fixed top bar eats into it. The card's own padding is just `.7rem`.

## 13.3 Target sidebar
```
 ▣ Inbox                    ← st.navigation (unchanged)
 ▌Review & Ledger
 ▣ Loans
 ORGANIZATIONS
 ▌▢ BT Property Hol…        active: #24272E + 2px #A0A5B1 left bar
   ▢ Marine Trust           others: transparent, hover #202329
 FINANCIAL YEAR
 [ 2026          ▾ ] [＋]
 ─────────────────────────
   ⚙ Settings               quiet rows
   👤 admin · auto-lock 60 min
   ⇥ Sign out
```

## 13.4 Files & steps
1. **`axiom_ui_chrome.py`** (all widget keys unchanged)
   - Add a `_sb_label(text)` helper for section labels.
   - Wrap the org list in `st.container(key="sb_orgs")`. Each button label is just the name, with `icon=":material/domain:"`. The jurisdiction and row count move to `help=`.
   - Call `get_entity_ledger` only for the active org. This removes a per-org query on every rerun.
   - Year row: wrap in `st.container(key="sb_year")`, with a "Financial year" label and `label_visibility="collapsed"`.
   - Footer: wrap in `st.container(key="sb_footer")` with one divider above it.
     - Settings uses `:material/settings:`, or `:material/close:` when Settings is open.
     - The user line is `:material/person:` + name.
     - Sign out uses `:material/logout:`.
2. **`assets/styles.css`**: append at the end so it overrides the global button rules.
   - All new rules are prefixed with `[data-testid="stSidebar"] .st-key-sb_*` so they win over the global `!important` rules.
   - Rows: transparent, left-aligned, ellipsis for long names. Hover `#202329`. Active `#24272E` with a 2px `#A0A5B1` left bar.
   - `.axiom-sb-label` for section labels.
   - Widen the sidebar to 240–280px.
   - Header spacing: block-container `padding-top` 1.5 → 2.75rem. Card padding `.7rem 1.1rem` → `1.15rem 1.5rem`, margin-bottom `.75` → `1.4rem`. Small eyebrow and h2 line-height tweaks.
3. **`tests/test_axiom_ui_chrome.py`**
   - The org label becomes the plain entity name.
   - Assert the `sb_orgs` container exists.
   - Assert the row count appears only in the active org's tooltip.

## 13.5 Risks
- **Unverified selectors:** the button selectors (`.stButton>button[kind="primary"]`, inner `div` alignment) come from reading the code, not the live page. Add `[data-testid="stBaseButton-primary"]` as a fallback selector.
- **Restart:** Streamlit must be restarted to pick up the CSS, because it is cached per process.
- **Settings close-on-click:** clicking an org must still close Settings. That behaviour is unchanged and still covered by tests.

## 13.6 Implementation status
✅ Implemented in `axiom_ui_chrome.py` (sb_orgs / sb_year / sb_footer containers, `_sb_label`, `_org_tooltip` with active-only row count, Material icons, user block) and `assets/styles.css` (header spacing, sidebar 240–280px, scoped list-row styles with `stBaseButton-primary` fallback — test id confirmed present in the bundled Streamlit 1.67 frontend). Tests updated; pytest/ruff/file-length green.
⏸ Visual check after a Streamlit restart (CSS is cached per process).

## 13.7 Follow-up tweak (user feedback)
- **Sidebar width:** widened from 240–280px to 290–360px.
- **Org rows:** names now wrap to two lines (`-webkit-line-clamp: 2`) instead of being cut off with an ellipsis. Row padding raised to `.5rem .65rem`.
- **Header card:** padding is now `1.2rem 1.6rem 1.7rem` (more space at the bottom), and margin-bottom is now `1.6rem`.
- **Markdown margin:** Streamlit's negative markdown bottom margin is cancelled inside `.st-key-ws_header`, so the bottom padding actually shows.
