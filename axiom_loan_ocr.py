"""
axiom_loan_ocr
==============

Loan-screenshot extraction: prompt-model fallbacks, saving the screenshot to
the vault, lenient JSON normalisation of the model reply and mapping the
detected property back onto the configured property loans.

Extracted from ``utils.py`` (phase D14) with byte-identical bodies and
re-exported there for existing call sites. Tests monkeypatch the Gemini client
and never hit the network.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import streamlit as st

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

from axiom_account_codes import detect_property_from_text
from axiom_core import (
    _clean_loan_money,
    _clean_loan_rate,
    _loan_date_iso,
    infer_nz_bank_from_account,
)
from axiom_db import VAULT_ROOT_DIR
from axiom_ledger import is_property_unassigned
from axiom_loan_crud import get_loan_target_entity
from axiom_memory import _load_memory_store
from axiom_properties import get_entity_properties

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
