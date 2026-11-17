"""Loan-screenshot OCR tests (phase D14). No network, no real vault."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import axiom_loan_ocr


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


def _install_genai(monkeypatch, responses: dict[str, object]):
    """Install fake ``google.genai`` modules; ``responses`` maps model -> text or Exception."""

    calls: list[str] = []

    class _Models:
        def generate_content(self, model, contents):
            calls.append(model)
            result = responses.get(model)
            if isinstance(result, Exception):
                raise result
            return _FakeResponse(result or "")

    class _Client:
        def __init__(self, api_key=None):
            self.models = _Models()
            self.api_key = api_key

    class _Part:
        @staticmethod
        def from_bytes(data, mime_type):
            return {"data": data, "mime_type": mime_type}

    google_mod = ModuleType("google")
    genai_mod = ModuleType("google.genai")
    genai_mod.Client = _Client
    genai_mod.types = SimpleNamespace(Part=_Part)
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_mod.types)
    return calls


@pytest.fixture()
def loan_env(monkeypatch, tmp_path):
    monkeypatch.setattr(axiom_loan_ocr, "VAULT_ROOT_DIR", tmp_path / "vault")
    monkeypatch.setattr(axiom_loan_ocr, "get_loan_target_entity", lambda: "BT Property Holdings Ltd")
    monkeypatch.setattr(
        axiom_loan_ocr,
        "get_entity_properties",
        lambda entity=None: ["33 Blampied", "303 Weymouth", "General / Portfolio"],
    )
    monkeypatch.setattr(axiom_loan_ocr, "detect_property_from_text", lambda text, entity=None: "")
    monkeypatch.setattr(axiom_loan_ocr, "_match_known_btph_property", lambda candidate, raw="": "33 Blampied")
    monkeypatch.setattr(axiom_loan_ocr, "save_loan_screenshot_to_vault", lambda b, filename="x": "vault/shot.png")
    return tmp_path


# --------------------------------------------------------------------------- #
# vault persistence
# --------------------------------------------------------------------------- #
def test_save_loan_screenshot_to_vault(monkeypatch, tmp_path):
    monkeypatch.setattr(axiom_loan_ocr, "VAULT_ROOT_DIR", tmp_path / "vault")
    assert axiom_loan_ocr.save_loan_screenshot_to_vault(b"") == ""

    path = axiom_loan_ocr.save_loan_screenshot_to_vault(b"png-bytes", filename="../../evil name!.png")
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = Path(axiom_loan_ocr.__file__).resolve().parent / resolved
    assert resolved.exists()
    assert resolved.read_bytes() == b"png-bytes"
    assert "loans" in resolved.parts
    assert resolved.name.endswith("_evil_name_.png")


# --------------------------------------------------------------------------- #
# property matching
# --------------------------------------------------------------------------- #
def test_match_known_property_exact_and_partial(loan_env, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(axiom_loan_ocr, "get_loan_target_entity", lambda: "BT Property Holdings Ltd")
    monkeypatch.setattr(
        axiom_loan_ocr,
        "get_entity_properties",
        lambda entity=None: ["33 Blampied", "303 Weymouth", "General / Portfolio", "Unassigned"],
    )
    assert axiom_loan_ocr._match_known_btph_property("303 Weymouth") == "303 Weymouth"
    assert axiom_loan_ocr._match_known_btph_property("33 Blampied Loan") == "33 Blampied"
    assert axiom_loan_ocr._match_known_btph_property("Something else") == "Something else"


def test_match_known_property_detection_and_tokens(loan_env, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(axiom_loan_ocr, "get_loan_target_entity", lambda: "BT Property Holdings Ltd")
    monkeypatch.setattr(
        axiom_loan_ocr,
        "get_entity_properties",
        lambda entity=None: ["33 Blampied", "303 Weymouth", "General / Portfolio"],
    )
    monkeypatch.setattr(
        axiom_loan_ocr, "detect_property_from_text", lambda text, entity=None: "303 Weymouth"
    )
    assert axiom_loan_ocr._match_known_btph_property("unknown label") == "303 Weymouth"

    monkeypatch.setattr(axiom_loan_ocr, "detect_property_from_text", lambda text, entity=None: "")
    assert axiom_loan_ocr._match_known_btph_property("", "statement for 33 blampied a/c") == "33 Blampied"


# --------------------------------------------------------------------------- #
# JSON normalisation
# --------------------------------------------------------------------------- #
def test_parse_loan_screenshot_json_fenced_payload(loan_env):
    raw = """```json
    {"property_name": "33 Blampied Loan", "bank_name": "Westpac",
     "interest_type": "Variable rate", "repayment_type": "P&I table",
     "repayment_frequency": "fortnightly", "current_balance": "-313,000.00",
     "loan_limit": "0", "regular_repayment_amount": "1,155.09 (estimated)",
     "drawdown_date": "22/05/2020"}
    ```"""

    parsed = axiom_loan_ocr.parse_loan_screenshot_json(raw)

    assert parsed["property_name"] == "33 Blampied"
    assert parsed["interest_type"] == "Floating"
    assert parsed["repayment_type"] == "Principal & Interest"
    assert parsed["repayment_frequency"] == "Fortnightly"
    assert parsed["current_balance"] == 313000.0
    assert parsed["next_payment_is_estimated"] is True
    assert parsed["regular_repayment_amount"] == 1155.09
    assert parsed["drawdown_date"] == "2020-05-22"
    assert parsed["loan_label"] == "Main Facility"


def test_parse_loan_screenshot_json_list_and_noise(loan_env):
    payload = json.dumps([{"loan_label": "Split A", "interest_type": "Orbit"}])
    parsed = axiom_loan_ocr.parse_loan_screenshot_json(f"Here is the JSON: {payload} thanks")
    assert parsed["loan_label"] == "Split A"
    assert parsed["interest_type"] == "Revolving / Flexi"

    assert axiom_loan_ocr.parse_loan_screenshot_json("")["loan_label"] == "Main Facility"
    assert axiom_loan_ocr.parse_loan_screenshot_json("not json at all")["interest_rate_pct"] == 0.0


def test_parse_loan_screenshot_json_infers_bank_and_original_drawdown(loan_env):
    raw = json.dumps(
        {"account_number": "12-3456-0123456-00", "loan_limit": 500000, "original_drawdown_amount": 0}
    )
    parsed = axiom_loan_ocr.parse_loan_screenshot_json(raw, target_property="303 Weymouth")
    assert parsed["bank_name"] == "ASB"
    assert parsed["property_name"] == "303 Weymouth"
    assert parsed["original_drawdown_amount"] == 500000.0


# --------------------------------------------------------------------------- #
# Gemini extraction
# --------------------------------------------------------------------------- #
def test_extraction_requires_key(loan_env, monkeypatch):
    monkeypatch.setattr(axiom_loan_ocr.st, "session_state", {})
    monkeypatch.setattr(axiom_loan_ocr, "_load_memory_store", lambda: {})
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(ValueError):
        axiom_loan_ocr.extract_loan_details_from_screenshot_gemini(b"png")


def test_extraction_falls_back_across_models(loan_env, monkeypatch):
    calls = _install_genai(
        monkeypatch,
        {
            axiom_loan_ocr.GEMINI_LOAN_SCREENSHOT_MODELS[0]: RuntimeError("404 model not found"),
            axiom_loan_ocr.GEMINI_LOAN_SCREENSHOT_MODELS[1]: '{"loan_label": "Main Facility"}',
        },
    )

    parsed, used_model, vault_path = axiom_loan_ocr.extract_loan_details_from_screenshot_gemini(
        b"png", api_key="test-key"
    )

    assert calls == list(axiom_loan_ocr.GEMINI_LOAN_SCREENSHOT_MODELS[:2])
    assert used_model == axiom_loan_ocr.GEMINI_LOAN_SCREENSHOT_MODELS[1]
    assert vault_path == "vault/shot.png"
    assert parsed["screenshot_vault_path"] == "vault/shot.png"
    assert parsed["loan_label"] == "Main Facility"


def test_extraction_raises_when_all_models_fail(loan_env, monkeypatch):
    responses = {model: RuntimeError("404 nope") for model in axiom_loan_ocr.GEMINI_LOAN_SCREENSHOT_MODELS}
    _install_genai(monkeypatch, responses)

    with pytest.raises(RuntimeError):
        axiom_loan_ocr.extract_loan_details_from_screenshot_gemini(b"png", api_key="test-key")


def test_extraction_propagates_non_retryable_errors(loan_env, monkeypatch):
    _install_genai(monkeypatch, {axiom_loan_ocr.GEMINI_LOAN_SCREENSHOT_MODELS[0]: RuntimeError("quota")})

    with pytest.raises(RuntimeError, match="quota"):
        axiom_loan_ocr.extract_loan_details_from_screenshot_gemini(b"png", api_key="test-key")
