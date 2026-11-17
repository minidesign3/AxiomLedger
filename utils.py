# utils.py — thin re-export shim (decomposition phase D23 complete).
#
# The former ~11k-line god-module has been fully decomposed into focused
# ``axiom_*`` modules (see plan.md §10). This file now only re-imports those
# symbols so every historical ``from utils import ...`` / ``utils.<name>`` call
# site (app.py, pages/*, tests) keeps working unchanged. Add new code to the
# owning ``axiom_*`` module, never here.

# ``streamlit`` re-exported as ``utils.st`` for backward compatibility: a few
# tests reach ``utils.st.session_state`` directly.
import streamlit as st  # noqa: F401  (re-exported for compatibility)

# Pure, stateless helpers + constants live in axiom_core and are re-exported here
# so existing `from utils import ...` call sites keep working unchanged.
# Entity Chart-of-Accounts CRUD, Property shorthand aliases and deterministic
# property detection live in axiom_account_codes (phase D6). Imported and
# re-exported here so all existing `from utils import ...` / `utils.<name>`
# call sites keep working unchanged.
from axiom_account_codes import (  # noqa: F401  (re-exported for compatibility)
    _GENERIC_STREET_SUFFIXES,
    _derive_smart_property_tokens,
    add_entity_account_code,
    detect_property_from_text,
    get_account_code_options,
    get_entity_account_codes,
    get_entity_rules,
    get_property_aliases,
    remove_entity_account_code,
    rename_entity_account_code,
    save_property_aliases,
)

# Operator accounts (bcrypt), streamlit-authenticator credentials and the
# persisted SQLite auth session with its 60-minute inactivity timeout live in
# axiom_auth (phase D7). require_auth stays here because it wires page chrome.
from axiom_auth import (  # noqa: F401  (re-exported for compatibility)
    ACCOUNTS_STORE_PATH,
    ADMIN_BCRYPT_HASH,
    SESSION_INACTIVITY_TIMEOUT_SEC,
    _clear_active_auth_session,
    _get_admin_password_hash,
    _hash_password_bcrypt,
    _load_accounts_store,
    _load_active_auth_session,
    _save_accounts_store,
    _save_active_auth_session,
    _verify_password_bcrypt,
    add_operator_account,
    delete_operator_account,
    get_all_user_accounts,
    get_auth_config,
    get_authenticator,
    sign_out_current_session,
    sync_and_touch_auth_session,
    update_operator_credentials,
)

# The AI categorization layer (jurisdiction prompt builder, local-LLM & Gemini
# adapters, dynamic router, rule/AI DataFrame passes) lives in
# axiom_categorization. Imported and re-exported here so all existing
# `from utils import ...` / `utils.<name>` call sites keep working unchanged.
from axiom_categorization import (  # noqa: F401  (re-exported for compatibility)
    apply_ai_fallback_categorization,
    apply_rule_categorization,
    categorize_description_dynamic,
    get_categorization_system_prompt,
    guess_ird_category_gemini,
    guess_ird_category_local_llm,
    match_rule_category,
)
from axiom_core import (
    BACKGROUND_TASK_CATALOG,  # noqa: F401  (re-exported for compatibility)
    CATEGORIZATION_MODEL_OPTIONS,  # noqa: F401  (re-exported for compatibility)
    CATEGORIZATION_RULES,  # noqa: F401  (NZ rules alias, re-exported for compatibility)
    DEFAULT_CUSTOM_ACCOUNT_CODES,  # noqa: F401  (re-exported for compatibility)
    DEFAULT_ENTITY_JURISDICTIONS,  # noqa: F401  (re-exported for compatibility)
    DEFAULT_LOCAL_LLM_MODEL,  # noqa: F401  (re-exported for compatibility)
    DEFAULT_PROPERTIES,  # noqa: F401  (re-exported for compatibility)
    DEFAULT_PROPERTY_ALIASES,  # noqa: F401  (re-exported for compatibility)
    ENTITY_CATEGORIZATION_RULES,  # noqa: F401  (re-exported for compatibility)
    ENTITY_OPTIONS,  # noqa: F401  (re-exported for compatibility)
    GEMINI_OCR_MODEL,  # noqa: F401  (re-exported for compatibility)
    MEMORY_STORE_PATH,  # noqa: F401  (re-exported for compatibility)
    NZ_IRD_SYSTEM_PROMPT,  # noqa: F401  (re-exported for compatibility)
    STANDARD_COLUMNS,  # noqa: F401  (re-exported for compatibility)
    TAX_YEAR_OPTIONS,  # noqa: F401  (re-exported for compatibility)
    UNASSIGNED_PROPERTY,  # noqa: F401  (re-exported for compatibility)
    UNCATEGORIZED_MARKERS,  # noqa: F401  (re-exported for compatibility)
    US_IRS_SYSTEM_PROMPT,  # noqa: F401  (re-exported for compatibility)
    _clean_ird_category_response,  # noqa: F401  (re-exported for compatibility)
    _clean_loan_money,  # noqa: F401  (re-exported for compatibility)
    _clean_loan_rate,  # noqa: F401  (re-exported for compatibility)
    _loan_date_iso,  # noqa: F401  (re-exported for compatibility)
    _parse_loan_date,  # noqa: F401  (re-exported for compatibility)
    _safe_vault_segment,  # noqa: F401  (re-exported for compatibility)
    _subtract_months,  # noqa: F401  (re-exported for compatibility)
    classify_upload_bucket,  # noqa: F401  (re-exported for compatibility)
    infer_nz_bank_from_account,  # noqa: F401  (re-exported for compatibility)
    is_uncategorized,  # noqa: F401  (re-exported for compatibility)
)

# Ledger DataFrame normalisation (smart header/column mapper), currency
# cleaning, column de-duping and the Xero master CSV export live in
# axiom_dataframe (phase D10).
from axiom_dataframe import (  # noqa: F401  (re-exported for compatibility)
    _clean_amount_series,
    _dedupe_column_names,
    export_xero_master_csv,
    normalize_financial_dataframe,
    standardize_ledger_dataframe,
)

# The Streamlit-free SQLite persistence layer (schema, connection, master-ledger
# and uploaded-file round-trips) lives in axiom_db. Imported and re-exported here
# so all existing `from utils import ...` / `utils.<name>` call sites keep working.
from axiom_db import (  # noqa: F401  (DATA_DIR/SQLITE_DB_PATH/_SCHEMA_READY re-exported for compatibility)
    _DB_LOCK,
    _SCHEMA_READY,
    DATA_DIR,
    SQLITE_DB_PATH,
    VAULT_ROOT_DIR,
    _delete_file_record_from_sqlite,
    _ensure_sqlite_schema,
    _get_sqlite_conn,
    _load_master_ledger_from_sqlite,
    _save_file_record_to_sqlite,
    _save_master_ledger_to_sqlite,
)

# The SMTP / email notification service lives in axiom_email. Imported and
# re-exported here so all existing `from utils import ...` / `utils.<name>`
# call sites keep working unchanged.
from axiom_email import (  # noqa: F401  (build_loan_reminder_email re-exported for compatibility)
    _smtp_recipients,
    build_loan_reminder_email,
    get_smtp_config,
    save_smtp_config,
    send_email_via_smtp,
)

# Entity (organization) lifecycle + financial-year management lives in
# axiom_entities. Property-list CRUD and Per-Property vs Portfolio-Wide code
# scoping live in axiom_properties (below). Both were moved in phase D5 and
# every function body is byte-identical to the baseline. Imported and
# re-exported here so all existing `from utils import ...` / `utils.<name>`
# call sites keep working unchanged.
from axiom_entities import (  # noqa: F401  (re-exported for compatibility)
    add_new_entity,
    add_new_tax_year,
    delete_entity,
    rename_entity,
)

# Ingestion orchestration (vault/SQLite reconciliation, uploaded-file list,
# ledger rebuild, memory training, red/unsure counts) lives in axiom_ingestion
# (phase D15a); the upload pipeline lives in axiom_upload (D15b).
from axiom_ingestion import (  # noqa: F401  (re-exported for compatibility)
    _reconcile_vault_and_broken_csvs,
    get_entity_uploaded_files,
    get_file_red_counts,
    sync_entity_ledger_from_files,
    train_memory_from_coded_dataframe,
)

# Entity + financial-year ledger IO (SQLite-backed uploaded files, vault
# auto-repair/recovery, get/set/append ledger cache, vault ZIP export) lives
# in axiom_ledger; the traffic-light engine lives in axiom_traffic (phase D9).
from axiom_ledger import (  # noqa: F401  (re-exported for compatibility)
    _load_uploaded_files_from_sqlite,
    append_entity_ledger,
    export_entity_vault_zip,
    get_entity_ledger,
    is_property_unassigned,
    set_entity_ledger,
)

# LLM connectivity verification (Gemini cloud API + local OpenAI-compatible
# endpoint) lives in axiom_llm. verify_gemini_connection uses a local
# `import utils` (see its docstring) to persist via _save_memory_store without
# a circular import. Imported and re-exported here so all existing
# `from utils import ...` / `utils.<name>` call sites keep working unchanged.
# Property-loan SQLite CRUD + email-reminder orchestration lives in
# axiom_loan_crud. That module uses a *local* `import utils` inside each
# function body that needs get_active_entity/get_entity_options/
# get_entity_account_codes/add_entity_account_code (never at its own module
# top level) to avoid a circular import with this exact block, regardless of
# which module gets imported first — see axiom_loan_crud's module docstring.
# Imported and re-exported here so all existing `from utils import ...` /
# `utils.<name>` call sites keep working unchanged.
from axiom_loan_crud import (  # noqa: F401  (several re-exported for compatibility only)
    LOAN_TARGET_ENTITY_DEFAULT,
    check_and_send_due_loan_reminders,
    create_property_loan,
    delete_property_loan,
    ensure_seeded_property_loans,
    get_due_loan_reminders,
    get_loan_rate_history,
    get_loan_reminder_log,
    get_loan_target_entity,
    get_property_loan_by_id,
    get_property_loans,
    has_reminder_been_sent,
    is_loan_target_entity,
    log_loan_rate_history,
    match_loan_from_text,
    run_loan_reminder_check_once_per_session,
    save_property_loan,
    send_loan_reminder_email,
)

# Loan-screenshot extraction (model fallbacks, vault save, lenient JSON
# normalisation, property mapping) lives in axiom_loan_ocr (phase D14).
from axiom_loan_ocr import (  # noqa: F401  (re-exported for compatibility)
    GEMINI_LOAN_SCREENSHOT_MODELS,
    _match_known_btph_property,
    extract_loan_details_from_screenshot_gemini,
    parse_loan_screenshot_json,
    save_loan_screenshot_to_vault,
)

# Pure loan domain calculations (row normalization, expiry milestones, rate
# status, dossier summary) live in axiom_loans. Imported and re-exported here
# so all existing `from utils import ...` / `utils.<name>` call sites keep
# working unchanged.
from axiom_loans import (  # noqa: F401  (_loan_row_to_dict/get_loan_trigger_tokens re-exported for compatibility)
    _loan_dossier_summary_label,
    _loan_rate_status_tuple,
    _loan_row_to_dict,
    get_loan_trigger_tokens,
    loan_milestones,
)

# Memory-store persistence (.axiom_memory.json + SQLite kv_settings row) and
# workspace/session state getters live in axiom_memory / axiom_state.
# Imported and re-exported here so all existing `from utils import ...` /
# `utils.<name>` call sites keep working unchanged.
from axiom_memory import (  # noqa: F401  (re-exported for compatibility)
    _invalidate_memory_store_cache,
    _load_memory_store,
    _load_memory_store_from_storage,
    _save_memory_store,
    _save_memory_store_unconditional,
)

# Screenshot OCR (Gemini response parsing, OCR markdown sidecars, structured
# extraction entry points) lives in axiom_ocr (phase D13).
from axiom_ocr import (  # noqa: F401  (re-exported for compatibility)
    extract_screenshot_structured_gemini,
    extract_transactions_from_image_gemini,
    get_screenshot_ocr_md_path,
    parse_gemini_ocr_output,
    read_screenshot_ocr_markdown,
    save_screenshot_ocr_markdown,
)

# Raw document readers (PDF tables, pure-Python XLSX, Excel/ODS/ODT/TSV
# tables, bank-CSV preamble sniffing) live in axiom_parsing (phase D11).
from axiom_parsing import (  # noqa: F401  (re-exported for compatibility)
    _read_bank_csv_bytes,
    extract_other_document_tables,
    extract_pdf_tables,
    parse_xlsx_without_openpyxl,
)

# Property-list CRUD (add/remove/rename + migration of rules, ledgers and
# SQLite rows) and Per-Property vs Portfolio-Wide code scoping live in
# axiom_properties, together with the PORTFOLIO_WIDE_CATEGORIES constant.
# Imported and re-exported here so all existing `from utils import ...` /
# `utils.<name>` call sites keep working unchanged.
from axiom_properties import (  # noqa: F401  (re-exported for compatibility)
    PORTFOLIO_WIDE_CATEGORIES,
    add_entity_property,
    get_entity_properties,
    is_portfolio_wide_code,
    remove_entity_property,
    rename_entity_property,
    set_entity_code_scope,
)

# Memorized coding-rule CRUD + the deterministic matching engine live in
# axiom_rules (lazy `import utils` seams documented in that module). Imported
# and re-exported here so all existing `from utils import ...` / `utils.<name>`
# call sites keep working unchanged.
from axiom_rules import (  # noqa: F401  (re-exported for compatibility)
    _pattern_matches_row_text,
    delete_memorized_rule,
    get_memorized_rules,
    match_memorized_or_rule,
    replace_memorized_rules,
    save_memorized_rule,
)
from axiom_state import (  # noqa: F401  (re-exported for compatibility)
    format_tax_year_period,
    get_active_entity,
    get_active_tax_year,
    get_entity_jurisdiction,
    get_entity_options,
    get_tax_year_options,
    init_workspace_state,
)

# Non-UI background-task supervisor (worker loop, operator controls,
# aggregate status summary) lives in axiom_tasks (phase D8).
from axiom_tasks import (  # noqa: F401  (re-exported for compatibility)
    _LOG_LOCK,
    _append_task_log,
    _background_worker_loop,
    get_background_status_summary,
    pause_background_task,
    start_background_task,
    stop_background_task,
    trigger_background_task_error,
)

# Three-tier traffic-light certainty engine (evaluate/compute/badges) lives
# in axiom_traffic (phase D9, split from axiom_ledger for the 500-line cap).
from axiom_traffic import (  # noqa: F401  (re-exported for compatibility)
    compute_traffic_light_columns,
    evaluate_row_traffic_light,
    get_row_status_badge,
    is_row_red_or_unsure,
)

# Workspace chrome (phase D22) lives in axiom_ui_chrome.
from axiom_ui_chrome import (  # noqa: F401  (re-exported for compatibility)
    render_sidebar_session,
    render_sidebar_workspace_controls,
    render_workspace_header,
    require_auth,
)

# Upload pipeline (vault + SQLite persistence, OCR-on-upload, coded-workbook
# import into an entity) lives in axiom_upload (phase D15b).
# Shared small UI widgets/snippets (property editor, bank logos, loan card,
# status indicator, connection badge, entity edit popover) live in
# axiom_ui_components (D17). Re-exported so call sites keep working unchanged.
from axiom_ui_components import (  # noqa: F401  (re-exported for compatibility)
    _nz_bank_logo_svg,
    _render_connection_badge,
    _render_entity_edit_popover,
    _render_loan_card_html,
    render_global_status_indicator,
    render_property_editor_widget,
)

# Entity ingestion pipeline UI (phase D20): board shell + quick upload in
# axiom_ui_ingestion, 4-column board in _board, dropzone in _upload and the
# full-page reviewer in _review.
from axiom_ui_ingestion import (  # noqa: F401  (re-exported for compatibility)
    render_entity_ingestion_pipeline,
)
from axiom_ui_ingestion_review import (  # noqa: F401  (re-exported for compatibility)
    _render_full_page_file_reviewer,
)
from axiom_ui_ingestion_upload import (  # noqa: F401  (re-exported for compatibility)
    _render_bulk_upload_dropzone,
)

# Property Loans & Bank Registry UI (phase D19) lives in axiom_ui_loans and
# its _import / _dossiers / _common siblings.
from axiom_ui_loans import (  # noqa: F401  (re-exported for compatibility)
    render_property_loans_block,
)
from axiom_ui_loans_common import (  # noqa: F401  (re-exported for compatibility)
    LOAN_FREQUENCY_OPTIONS,
    LOAN_INTEREST_TYPE_OPTIONS,
    LOAN_REPAYMENT_TYPE_OPTIONS,
    NZ_BANK_OPTIONS,
)

# System & Organization Settings drawer (phase D21) lives in axiom_ui_settings
# and its _ai / _email / _accounts siblings.
from axiom_ui_settings import (  # noqa: F401  (re-exported for compatibility)
    render_right_settings_drawer,
)

# Item + Property setup matrix (decomposed in D18 across axiom_ui_setup_matrix /
# _rules / _editor / _common). Re-exported so call sites keep working unchanged.
from axiom_ui_setup_matrix import (  # noqa: F401  (re-exported for compatibility)
    render_item_property_setup_matrix,
)

# Global stylesheet (assets/styles.css) lives in axiom_ui_styles (D16).
from axiom_ui_styles import apply_custom_styles  # noqa: F401  (re-exported for compatibility)
from axiom_upload import (  # noqa: F401  (re-exported for compatibility)
    import_codes_workbook_into_entity,
    ingest_uploaded_file_object,
    run_ocr_on_screenshot_record,
)

# Local File Vault paths + raw-file read/write live in axiom_vault. Imported and
# re-exported here so all existing `from utils import ...` / `utils.<name>`
# call sites keep working unchanged. `export_entity_vault_zip` still lives here
# (it needs get_entity_ledger/export_xero_master_csv, which move in D9/D10).
from axiom_vault import (  # noqa: F401  (re-exported for compatibility)
    _save_raw_file_to_vault,
    get_vault_dir,
    get_vault_file_bytes,
    save_raw_file_to_vault,
)

# The multi-sheet coded-workbook parser (Codes.xlsx bank/property/pivot
# sheets + payee statistics) lives in axiom_workbook (phase D12).
from axiom_workbook import (  # noqa: F401  (re-exported for compatibility)
    parse_coded_workbook_sheets,
)

# ADMIN_BCRYPT_HASH now lives in axiom_auth, CATEGORIZATION_RULES (the NZ alias)
# in axiom_core, and require_auth in axiom_ui_chrome — all imported/re-exported
# above (phase D23). utils.py is now a pure re-export shim.
