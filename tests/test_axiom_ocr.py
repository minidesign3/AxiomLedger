"""Screenshot OCR parsing + sidecar tests (phase D13).

Gemini is replaced by fakes; rule categorization / property detection are
stubbed so no session, DB or network access happens.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import axiom_dataframe
import axiom_ocr
from axiom_core import STANDARD_COLUMNS


@pytest.fixture()
def ocr_env(monkeypatch):
    """Stub the rule-categorization pipeline (returns rows untouched)."""

    def identity(df):
        return df.copy()

    monkeypatch.setattr(axiom_ocr, "apply_rule_categorization", identity)
    monkeypatch.setattr(axiom_ocr, "apply_ai_fallback_categorization", identity)
    monkeypatch.setattr(axiom_dataframe, "detect_property_from_text", lambda text, entity=None: "Unassigned")
    monkeypatch.setattr(axiom_dataframe, "apply_rule_categorization", identity)
    return SimpleNamespace(identity=identity)


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeModels:
    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.text)


class _FakeClient:
    def __init__(self, text: str):
        self.models = _FakeModels(text)


def _install_fake_genai(monkeypatch, response_text: str):
    client = _FakeClient(response_text)

    class _Part:
        @staticmethod
        def from_bytes(data, mime_type):
            return {"data": data, "mime_type": mime_type}

    monkeypatch.setattr(axiom_ocr, "genai", SimpleNamespace(Client=lambda api_key: client))
    monkeypatch.setattr(axiom_ocr, "types", SimpleNamespace(Part=_Part))
    return client


# --------------------------------------------------------------------------- #
# parse_gemini_ocr_output
# --------------------------------------------------------------------------- #
def test_parse_gemini_ocr_output_empty():
    df = axiom_ocr.parse_gemini_ocr_output("   ")
    assert df.empty
    assert list(df.columns) == STANDARD_COLUMNS


def test_parse_gemini_ocr_output_fenced_csv(ocr_env):
    raw = """Here you go:
```csv
Date,Amount,Description,Property,Account Code
2026-01-05,-42.50,Countdown,Unassigned,Uncategorized
```
"""
    df = axiom_ocr.parse_gemini_ocr_output(raw)

    assert len(df) == 1
    assert str(df.iloc[0]["Description"]).startswith("Countdown")
    assert float(df.iloc[0]["Amount"]) == -42.5


def test_parse_gemini_ocr_output_json_fallback_when_csv_unparsable(ocr_env, monkeypatch):
    def raise_on_csv(*args, **kwargs):
        raise ValueError("not csv")

    monkeypatch.setattr(axiom_ocr.pd, "read_csv", raise_on_csv)
    raw = '[{"Date": "2026-02-01", "Amount": -12, "Description": "Coffee"}]'

    df = axiom_ocr.parse_gemini_ocr_output(raw)

    assert len(df) == 1
    assert df.iloc[0]["Description"] == "Coffee"


def test_parse_gemini_ocr_output_dynamic_ai_fallback_runs(ocr_env, monkeypatch):
    calls: list[pd.DataFrame] = []

    def fake_ai(df):
        calls.append(df)
        out = df.copy()
        out["Account Code"] = "Groceries"
        return out, {}

    monkeypatch.setattr(axiom_ocr, "apply_ai_fallback_categorization", fake_ai)

    df = axiom_ocr.parse_gemini_ocr_output(
        "Date,Amount,Description\n2026-01-01,-5,Milk", run_dynamic_ai_fallback=True
    )

    assert len(calls) == 1
    assert df.iloc[0]["Account Code"] == "Groceries"


def test_parse_gemini_ocr_output_ai_fallback_error_is_swallowed(ocr_env, monkeypatch):
    def boom(df):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(axiom_ocr, "apply_ai_fallback_categorization", boom)

    df = axiom_ocr.parse_gemini_ocr_output(
        "Date,Amount,Description\n2026-01-01,-5,Milk", run_dynamic_ai_fallback=True
    )

    assert len(df) == 1


# --------------------------------------------------------------------------- #
# .ocr.md sidecars
# --------------------------------------------------------------------------- #
def test_get_screenshot_ocr_md_path():
    assert axiom_ocr.get_screenshot_ocr_md_path("") is None
    absolute = Path("/tmp/vault/shot.png")
    assert axiom_ocr.get_screenshot_ocr_md_path(absolute) == Path("/tmp/vault/shot.png.ocr.md")
    relative = axiom_ocr.get_screenshot_ocr_md_path("data/vault/Alpha/shot.png")
    assert relative is not None and relative.is_absolute()
    assert str(relative).endswith("data/vault/Alpha/shot.png.ocr.md")


def test_read_markdown_missing_returns_blank(tmp_path):
    assert axiom_ocr.read_screenshot_ocr_markdown({"vault_path": str(tmp_path / "nope.png")}) == ("", "")


def test_save_and_read_markdown_roundtrip(ocr_env, tmp_path):
    vault_path = tmp_path / "section" / "shot.png"
    vault_path.parent.mkdir(parents=True)
    vault_path.write_bytes(b"image")
    df = pd.DataFrame(
        [
            {
                "Date": "2026-01-02",
                "Amount": -9.5,
                "Description": "Pipe | Desc",
                "Property": "9 Damian",
                "Account Code": "Groceries",
            }
        ],
        columns=STANDARD_COLUMNS,
    )

    written = axiom_ocr.save_screenshot_ocr_markdown(
        {"vault_path": str(vault_path), "name": "shot.png", "df": df},
        entity="Alpha",
        tax_year="FY2026",
        ocr_meta={"document_type": "receipt", "title": "Countdown", "summary_fields": {"Total": "9.50"}},
    )

    assert written.endswith("shot.png.ocr.md")
    md_path = Path(written)
    if not md_path.is_absolute():
        md_path = Path(axiom_ocr.__file__).resolve().parent / md_path
    text = md_path.read_text(encoding="utf-8")
    assert "# OCR Extract: shot.png" in text
    assert "- **Entity:** Alpha" in text
    assert "- **Total:** 9.50" in text
    assert "Pipe \\| Desc" in text
    assert "| 2026-01-02 | -9.50 |" in text

    rel, read_text = axiom_ocr.read_screenshot_ocr_markdown({"vault_path": str(vault_path)})
    assert read_text == text


def test_save_markdown_without_transactions_or_vault_path(tmp_path):
    assert axiom_ocr.save_screenshot_ocr_markdown({"vault_path": ""}, "Alpha", "FY2026") == ""

    vault_path = tmp_path / "empty.png"
    axiom_ocr.save_screenshot_ocr_markdown({"vault_path": str(vault_path), "name": "empty.png"}, "Alpha", "FY2026")
    text = Path(f"{vault_path}.ocr.md").read_text(encoding="utf-8")
    assert "_No key-value summary fields detected._" in text
    assert "_No individual transaction rows detected in this screenshot._" in text


# --------------------------------------------------------------------------- #
# Gemini structured extraction
# --------------------------------------------------------------------------- #
def test_structured_extraction_requires_api_key(ocr_env, monkeypatch):
    monkeypatch.setattr(axiom_ocr.st, "session_state", {})
    with pytest.raises(ValueError):
        axiom_ocr.extract_screenshot_structured_gemini(b"png-bytes", api_key="  ")


def test_structured_extraction_parses_json_and_transactions(ocr_env, monkeypatch):
    client = _install_fake_genai(
        monkeypatch,
        """
        ```json
        {"document_type": "transactions", "title": "ASB", "notes": "Everyday account",
         "summary_fields": {"Balance": "100.00"},
         "transactions": [{"Date": "2026-01-03", "Amount": -15.0, "Description": "Fuel"}]}
        ```
        """,
    )

    result = axiom_ocr.extract_screenshot_structured_gemini(b"png", api_key="test-key")

    assert result["document_type"] == "transactions"
    assert result["title"] == "ASB"
    assert result["summary_fields"] == {"Balance": "100.00"}
    assert len(result["df"]) == 1
    assert result["df"].iloc[0]["Description"] == "Fuel"
    assert client.models.calls[0]["contents"][0] == {"data": b"png", "mime_type": "image/png"}


def test_structured_extraction_falls_back_to_csv_parser(ocr_env, monkeypatch):
    _install_fake_genai(monkeypatch, "Date,Amount,Description\n2026-01-04,-3.2,Bus")

    result = axiom_ocr.extract_screenshot_structured_gemini(b"png", api_key="test-key")

    assert result["document_type"] == "screenshot"
    assert result["title"] == ""
    assert len(result["df"]) == 1
    assert result["df"].iloc[0]["Description"] == "Bus"


def test_structured_extraction_empty_json_transactions_stays_empty(ocr_env, monkeypatch):
    _install_fake_genai(monkeypatch, '{"document_type": "account_summary", "transactions": []}')

    result = axiom_ocr.extract_screenshot_structured_gemini(b"png", api_key="test-key")

    assert result["df"].empty
    assert result["document_type"] == "account_summary"


def test_extract_transactions_from_image_wrapper(ocr_env, monkeypatch):
    _install_fake_genai(monkeypatch, "Date,Amount,Description\n2026-01-06,-1.0,Snack")

    df, raw = axiom_ocr.extract_transactions_from_image_gemini(b"png", api_key="test-key")

    assert len(df) == 1
    assert raw.startswith("Date,Amount,Description")
