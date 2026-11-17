"""
axiom_ocr
=========

Screenshot OCR: Gemini/CSV response parsing, the ``.ocr.md`` markdown sidecars
written next to vault screenshots and the Gemini structured-extraction entry
points used by the ingestion pipeline.

The Gemini client is reached through the same optional ``genai`` import guard as
``utils.py``; tests monkeypatch it and never hit the network.

Extracted from ``utils.py`` (phase D13) with byte-identical bodies and
re-exported there for existing call sites.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

from axiom_categorization import (
    apply_ai_fallback_categorization,
    apply_rule_categorization,
)
from axiom_core import (
    GEMINI_OCR_MODEL,
    STANDARD_COLUMNS,
    UNASSIGNED_PROPERTY,
    is_uncategorized,
)
from axiom_dataframe import standardize_ledger_dataframe


def parse_gemini_ocr_output(
    raw_text: str,
    run_dynamic_ai_fallback: bool = False,
) -> pd.DataFrame:
    """
    Parse the CSV text block (with headers `Date,Amount,Description`) returned by `gemini-3.8-flash`
    into a Pandas DataFrame and run it through the Categorization Engine.
    """
    if not raw_text or not raw_text.strip():
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:csv|json|text)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    parsed_df: pd.DataFrame | None = None

    # Primary path: parse CSV text block with headers Date,Amount,Description
    try:
        csv_candidate = pd.read_csv(io.StringIO(cleaned))
        if not csv_candidate.empty or len(csv_candidate.columns) >= 2:
            parsed_df = csv_candidate
    except Exception:
        parsed_df = None

    # Fallback if model returned JSON array
    if parsed_df is None:
        try:
            json_payload = json.loads(cleaned)
            if isinstance(json_payload, dict):
                for key in ("transactions", "data", "items", "rows", "entries"):
                    if key in json_payload and isinstance(json_payload[key], list):
                        json_payload = json_payload[key]
                        break
                else:
                    json_payload = [json_payload]
            if isinstance(json_payload, list):
                parsed_df = pd.DataFrame(json_payload)
        except Exception:
            parsed_df = pd.DataFrame(columns=STANDARD_COLUMNS)

    standardized = standardize_ledger_dataframe(parsed_df)
    categorized = apply_rule_categorization(standardized)

    if run_dynamic_ai_fallback and not categorized.empty:
        has_unresolved = categorized["Account Code"].apply(is_uncategorized).any()
        if has_unresolved:
            try:
                categorized, _ = apply_ai_fallback_categorization(categorized)
            except Exception:
                # Keep rule-categorized DataFrame if the configured AI fallback endpoint is unreachable
                pass

    return categorized

def get_screenshot_ocr_md_path(vault_path: str | Path) -> Path | None:
    """Return the `.ocr.md` sidecar Path for a screenshot's vault path."""
    raw = str(vault_path or "").strip()
    if not raw:
        return None
    vp = Path(raw)
    if not vp.is_absolute():
        vp = Path(__file__).resolve().parent / vp
    return Path(f"{vp}.ocr.md")

def read_screenshot_ocr_markdown(file_rec: dict[str, object]) -> tuple[str, str]:
    """Read the saved `.ocr.md` sidecar for a screenshot record if it exists."""
    md_path = get_screenshot_ocr_md_path(str(file_rec.get("vault_path", "")))
    if md_path is None or not md_path.exists() or not md_path.is_file():
        return "", ""
    try:
        rel_path = str(md_path.relative_to(Path(__file__).resolve().parent))
    except ValueError:
        rel_path = str(md_path)
    try:
        return rel_path, md_path.read_text(encoding="utf-8")
    except Exception:
        return rel_path, ""

def save_screenshot_ocr_markdown(
    file_rec: dict[str, object],
    entity: str,
    tax_year: str,
    ocr_meta: dict[str, object] | None = None,
) -> str:
    """
    Save extracted screenshot OCR details + transactions to a human- and AI-readable
    Markdown sidecar file (`<vault_path>.ocr.md`) alongside the original image in the Vault.
    """
    md_path = get_screenshot_ocr_md_path(str(file_rec.get("vault_path", "")))
    if md_path is None:
        return ""
    md_path.parent.mkdir(parents=True, exist_ok=True)

    meta = ocr_meta or {}
    fname = str(file_rec.get("name", "Screenshot"))
    doc_type = str(meta.get("document_type") or "screenshot").strip()
    title = str(meta.get("title") or fname).strip()
    notes = str(meta.get("notes") or file_rec.get("note") or "").strip()
    summary_fields = meta.get("summary_fields") if isinstance(meta.get("summary_fields"), dict) else {}
    df = file_rec.get("df")
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(columns=STANDARD_COLUMNS)

    lines: list[str] = [
        f"# OCR Extract: {fname}",
        "",
        f"- **Entity:** {entity}",
        f"- **Tax Year:** {tax_year}",
        f"- **Source Image:** `{fname}`",
        f"- **OCR Model:** `{GEMINI_OCR_MODEL}`",
        f"- **Document Type:** `{doc_type}`",
        f"- **Title / Subject:** {title}",
        f"- **Extracted At:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary & Extracted Details",
        "",
    ]

    if summary_fields:
        for k, v in summary_fields.items():
            val_str = str(v).strip()
            if val_str:
                lines.append(f"- **{str(k).strip()}:** {val_str}")
    else:
        lines.append("- _No key-value summary fields detected._")

    if notes:
        lines.extend(["", f"**Notes:** {notes}"])

    lines.extend(["", "## Extracted Transactions", ""])
    if not df.empty:
        lines.append("| Date | Amount | Description | Property | Account Code |")
        lines.append("| --- | ---: | --- | --- | --- |")
        for _, row in df.iterrows():
            d_val = str(row.get("Date", "")).replace("|", "\\|").strip()
            try:
                amt_val = f"{float(row.get('Amount', 0.0) or 0.0):.2f}"
            except (TypeError, ValueError):
                amt_val = str(row.get("Amount", "0.00"))
            desc_val = str(row.get("Description", "")).replace("|", "\\|").replace("\n", " ").strip()
            prop_val = str(row.get("Property", UNASSIGNED_PROPERTY)).replace("|", "\\|").strip()
            code_val = str(row.get("Account Code", "Uncategorized")).replace("|", "\\|").strip()
            lines.append(f"| {d_val} | {amt_val} | {desc_val} | {prop_val} | {code_val} |")
    else:
        lines.append("_No individual transaction rows detected in this screenshot._")

    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    try:
        return str(md_path.relative_to(Path(__file__).resolve().parent))
    except ValueError:
        return str(md_path)

def extract_screenshot_structured_gemini(
    image_bytes: bytes,
    mime_type: str = "image/png",
    api_key: str | None = None,
    run_dynamic_ai_fallback: bool = True,
) -> dict[str, object]:
    """
    Use `gemini-3.8-flash` via `google-genai` to extract BOTH structured summary details
    (for bank/loan summaries, receipts, balances) and any transaction rows from a screenshot.
    """
    resolved_key = (api_key or st.session_state.get("google_genai_api_key") or "").strip()
    if not resolved_key:
        raise ValueError("Google GenAI API Key is required. Open '⚙ AI & System Settings' in the top right to enter it.")

    client = genai.Client(api_key=resolved_key)

    ocr_prompt = (
        "Perform highly precise OCR on this financial/banking/receipt image. "
        "Return ONLY a single valid JSON object (no markdown fences) with these keys:\n"
        '- "document_type": one of "transactions", "account_summary", "loan_summary", "receipt", "other"\n'
        '- "title": short title (e.g. bank & account name or merchant name)\n'
        '- "summary_fields": object of key-value pairs for every important figure or label visible '
        '(e.g. Bank, Account Name, Account Number, Balance, Limit, Interest Rate, Maturity Date, Total, Tax/GST, Date)\n'
        '- "notes": 1-2 sentence concise summary of what the screenshot shows\n'
        '- "transactions": array of objects, each with {"Date": "YYYY-MM-DD", "Amount": number, "Description": string}. '
        "Format Amount as a plain number (negative for debits/expenses, positive for credits/income). "
        "If the image is purely an account/loan balance summary with no transaction list, return an empty array []."
    )

    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    response = client.models.generate_content(
        model=GEMINI_OCR_MODEL,
        contents=[image_part, ocr_prompt],
    )

    raw_output = response.text or ""
    cleaned = raw_output.strip()
    fence_match = re.search(r"```(?:json|csv|text)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    doc_type = "screenshot"
    title = ""
    summary_fields: dict[str, object] = {}
    notes = ""
    df = pd.DataFrame(columns=STANDARD_COLUMNS)

    parsed_json = None
    try:
        parsed_json = json.loads(cleaned)
    except Exception:
        parsed_json = None

    if isinstance(parsed_json, dict):
        doc_type = str(parsed_json.get("document_type") or "screenshot").strip()
        title = str(parsed_json.get("title") or "").strip()
        if isinstance(parsed_json.get("summary_fields"), dict):
            summary_fields = parsed_json["summary_fields"]
        notes = str(parsed_json.get("notes") or "").strip()
        txs = parsed_json.get("transactions")
        if isinstance(txs, list) and txs:
            raw_df = pd.DataFrame(txs)
            df = standardize_ledger_dataframe(raw_df)
            df = apply_rule_categorization(df)
            if run_dynamic_ai_fallback and not df.empty and df["Account Code"].apply(is_uncategorized).any():
                try:
                    df, _ = apply_ai_fallback_categorization(df)
                except Exception:
                    pass
    else:
        df = parse_gemini_ocr_output(raw_output, run_dynamic_ai_fallback=run_dynamic_ai_fallback)

    return {
        "df": df,
        "document_type": doc_type,
        "title": title,
        "summary_fields": summary_fields,
        "notes": notes,
        "raw_output": raw_output,
    }

def extract_transactions_from_image_gemini(
    image_bytes: bytes,
    mime_type: str = "image/png",
    api_key: str | None = None,
    run_dynamic_ai_fallback: bool = True,
) -> tuple[pd.DataFrame, str]:
    """
    Use `gemini-3.8-flash` to OCR a screenshot and return `(DataFrame, raw_output)`.
    """
    res = extract_screenshot_structured_gemini(
        image_bytes=image_bytes,
        mime_type=mime_type,
        api_key=api_key,
        run_dynamic_ai_fallback=run_dynamic_ai_fallback,
    )
    return res["df"], str(res.get("raw_output", ""))
