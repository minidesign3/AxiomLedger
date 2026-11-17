"""
axiom_categorization
====================

The AI categorization layer extracted from the monolithic ``utils.py``: the
jurisdiction-aware system-prompt builder, the two provider adapters (local
OpenAI-compatible LLM and Google Gemini), the dynamic router, and the
DataFrame-level rule/AI categorization passes.

Three deliberately different kinds of dependency, each handled explicitly:

1. **Pure / already-extracted** — `is_uncategorized` and the two
   `*_SYSTEM_PROMPT` constants come from ``axiom_core``; the loan list for the
   NZ prompt's loan appendix comes from ``axiom_loan_crud``. No cycles.
2. **Streamlit-session / memory-store helpers still in `utils.py`** —
   `get_active_entity`, `get_entity_jurisdiction`, `is_portfolio_wide_code`,
   `is_property_unassigned`, and the heavy rules engine `match_memorized_or_rule`.
   These are reached via a **local `import utils` inside each function body that
   needs one** (never a module-level import). This has no import-ordering
   dependency: by the time any function here is *called*, both modules have
   finished importing. See ``axiom_loan_crud``'s docstring for the full
   rationale — this is the same, proven pattern.
3. **Third-party clients** — `OpenAI` (local) and `genai` (Gemini). Imported at
   module level as before; tests monkeypatch them, so no test makes a network call.

``utils.py`` imports and re-exports every public symbol here, so existing
``from utils import ...`` call sites keep working unchanged.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from google import genai

# Provider clients. Imported here (verbatim behaviour of the original code):
#   - OpenAI  : local air-gapped LLM via an OpenAI-compatible endpoint (Ollama)
#   - genai   : Google Gemini cloud categorization
from openai import OpenAI

from axiom_core import (
    DEFAULT_LOCAL_LLM_MODEL,
    DEFAULT_LOCAL_LLM_URL,
    GEMINI_OCR_MODEL,
    NZ_IRD_SYSTEM_PROMPT,
    STANDARD_COLUMNS,
    UNASSIGNED_PROPERTY,
    US_IRS_SYSTEM_PROMPT,
    _clean_ird_category_response,
    is_uncategorized,
)
from axiom_loan_crud import get_property_loans

# The model menu is UI-facing config still defined in utils.py (used heavily by
# the settings drawer). Reached lazily inside functions to avoid a cycle rather
# than duplicated here, so there is exactly one canonical list.


def get_categorization_system_prompt(entity: str | None = None) -> str:
    """
    Return the dynamic system prompt based on the active entity's jurisdiction:
    - 'US' -> US CPA (IRS Schedule E)
    - 'NZ' -> NZ Property Accountant (NZ IRD)
    """
    import utils

    if utils.get_entity_jurisdiction(entity) == "US":
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


def match_rule_category(description: object, entity: str | None = None) -> str:
    """
    Match a transaction Description against Memorized Rules first, then the active entity's
    jurisdiction rules (US IRS Schedule E rules vs NZ IRD rules).
    Returns the matched Account Code or 'Uncategorized' if no rule matches.
    """
    import utils

    _, code, _ = utils.match_memorized_or_rule(description, entity=entity)
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
    import utils

    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    resolved_entity = utils.get_active_entity(entity)
    result = df.reindex(columns=STANDARD_COLUMNS).copy()
    result["Property"] = result["Property"].fillna("").astype(str).str.strip()
    result["Account Code"] = result["Account Code"].fillna("").astype(str).str.strip()

    new_props: list[str] = []
    new_codes: list[str] = []

    for _, row in result.iterrows():
        curr_prop = str(row.get("Property", "")).strip()
        curr_code = str(row.get("Account Code", "")).strip()
        matched_prop, matched_code, _ = utils.match_memorized_or_rule(
            row.get("Description", ""),
            entity=resolved_entity,
        )

        if overwrite_existing or is_uncategorized(curr_code):
            final_code = matched_code
        else:
            final_code = curr_code

        if overwrite_existing or utils.is_property_unassigned(curr_prop, final_code):
            if not utils.is_property_unassigned(curr_prop, final_code) and not overwrite_existing:
                final_prop = curr_prop
            elif matched_prop != UNASSIGNED_PROPERTY:
                final_prop = matched_prop
            elif utils.is_portfolio_wide_code(final_code, resolved_entity):
                final_prop = "General / Portfolio"
            else:
                final_prop = UNASSIGNED_PROPERTY
        else:
            final_prop = curr_prop if curr_prop else UNASSIGNED_PROPERTY

        if utils.is_portfolio_wide_code(final_code, resolved_entity) and final_prop in ("", UNASSIGNED_PROPERTY):
            final_prop = "General / Portfolio"

        new_props.append(final_prop)
        new_codes.append(final_code)

    result["Property"] = new_props
    result["Account Code"] = new_codes
    return result


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
    import utils

    resolved_entity = utils.get_active_entity(entity)
    rule_match = match_rule_category(description, entity=resolved_entity)
    if not is_uncategorized(rule_match):
        return rule_match

    active_choice = (
        selected_model
        or st.session_state.get("categorization_model")
        or utils.CATEGORIZATION_MODEL_OPTIONS[0]
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
    import utils

    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS), 0

    resolved_entity = utils.get_active_entity(entity)
    updated = apply_rule_categorization(
        df.reindex(columns=STANDARD_COLUMNS).copy(),
        entity=resolved_entity,
    )
    updated_count = 0

    active_choice = (
        selected_model
        or st.session_state.get("categorization_model")
        or utils.CATEGORIZATION_MODEL_OPTIONS[0]
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
