"""
Tests for the AI categorization layer (``axiom_categorization.py``).

Both provider adapters perform real network I/O in production (a local
OpenAI-compatible endpoint and Google Gemini). Every test here monkeypatches the
client constructors to fakes — **no real network or API call is ever made**.

The DataFrame-level passes delegate to helpers that remain in ``utils`` (the
rules engine `match_memorized_or_rule`, `is_portfolio_wide_code`,
`is_property_unassigned`). Those are monkeypatched here to deterministic stubs so
the *routing/orchestration logic in this module* is what's under test, not the
rules engine (covered separately elsewhere).
"""

from __future__ import annotations

import pandas as pd
import pytest

import axiom_categorization as ac
import utils


@pytest.fixture(autouse=True)
def _stub_utils_helpers(monkeypatch):
    """
    Replace the session-coupled helpers still living in utils with simple stubs:
    a fixed active entity and a table-driven 'memorized rule' matcher.
    """
    monkeypatch.setattr(utils, "get_active_entity", lambda entity=None: entity or "BT Property Holdings Ltd")
    monkeypatch.setattr(utils, "get_entity_jurisdiction", lambda entity=None: "NZ")
    monkeypatch.setattr(utils, "is_portfolio_wide_code", lambda code, entity=None: str(code).startswith("P-"))
    monkeypatch.setattr(
        utils,
        "is_property_unassigned",
        lambda prop, code=None: str(prop).strip() in ("", "Unassigned"),
    )
    monkeypatch.setattr(utils, "get_property_loans", lambda entity=None: [])


class TestSystemPrompt:
    def test_nz_jurisdiction_returns_nz_prompt(self, monkeypatch):
        monkeypatch.setattr(utils, "get_entity_jurisdiction", lambda entity=None: "NZ")
        prompt = ac.get_categorization_system_prompt("BT Property Holdings Ltd")
        assert "New Zealand Property Accountant" in prompt

    def test_us_jurisdiction_returns_us_prompt(self, monkeypatch):
        monkeypatch.setattr(utils, "get_entity_jurisdiction", lambda entity=None: "US")
        prompt = ac.get_categorization_system_prompt("BT Investments NZ, LLC")
        assert "US Certified Public Accountant" in prompt

    def test_nz_prompt_appends_coding_loans(self, monkeypatch):
        # axiom_categorization imports this directly from axiom_loan_crud, so patch it there.
        monkeypatch.setattr(
            ac,
            "get_property_loans",
            lambda entity=None: [
                {
                    "property_name": "303 Weymouth",
                    "use_for_coding": True,
                    "bank_name": "ANZ",
                    "account_number": "12-3456-7890123-00",
                    "coding_trigger_keywords": "anz loan",
                    "interest_account_code": "I-100",
                    "principal_account_code": "P-200",
                }
            ],
        )
        prompt = ac.get_categorization_system_prompt("BT Property Holdings Ltd")
        assert "Known NZ property loans" in prompt
        assert "12-3456-7890123-00" in prompt
        assert "I-100" in prompt


class TestProviderAdapters:
    def test_local_llm_empty_description_short_circuits(self):
        assert ac.guess_ird_category_local_llm("") == "Uncategorized"
        assert ac.guess_ird_category_local_llm(None) == "Uncategorized"

    def test_local_llm_calls_client_with_system_and_user_messages(self, monkeypatch):
        captured = {}

        class _Msg:
            content = "Repairs and Maintenance"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        class _Completions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return _Resp()

        class _Chat:
            completions = _Completions()

        class _FakeClient:
            def __init__(self, **kwargs):
                captured["client_kwargs"] = kwargs
                self.chat = _Chat()

        monkeypatch.setattr(ac, "OpenAI", _FakeClient)

        result = ac.guess_ird_category_local_llm("ANZ Mortgage Interest", model_name="qwen2.5:9b")
        assert result == "Repairs and Maintenance"
        assert captured["model"] == "qwen2.5:9b"
        roles = [m["role"] for m in captured["messages"]]
        assert roles == ["system", "user"]
        assert captured["messages"][1]["content"] == "ANZ Mortgage Interest"
        assert "New Zealand Property Accountant" in captured["messages"][0]["content"]

    def test_gemini_requires_api_key(self, monkeypatch):
        monkeypatch.setattr(ac.st, "session_state", {})  # bare dict, no key
        with pytest.raises(ValueError, match="API Key is required"):
            ac.guess_ird_category_gemini("Some transaction", api_key="")

    def test_gemini_calls_models_generate_content(self, monkeypatch):
        captured = {}

        class _Resp:
            text = "Interest"

        class _Models:
            def generate_content(self, **kwargs):
                captured.update(kwargs)
                return _Resp()

        class _FakeClient:
            def __init__(self, api_key=None):
                captured["api_key"] = api_key
                self.models = _Models()

        monkeypatch.setattr(ac.genai, "Client", _FakeClient)

        result = ac.guess_ird_category_gemini("Loan interest", api_key="test-key")
        assert result == "Interest"
        assert captured["api_key"] == "test-key"
        assert captured["model"] == ac.GEMINI_OCR_MODEL
        assert "Loan interest" in captured["contents"]


class TestRouting:
    def test_rule_match_wins_over_ai(self, monkeypatch):
        monkeypatch.setattr(ac, "match_rule_category", lambda desc, entity=None: "Rates")
        # If the AI path were taken this would explode.
        monkeypatch.setattr(ac, "guess_ird_category_local_llm", lambda **kw: pytest.fail("AI should not be called"))

        assert ac.categorize_description_dynamic("Auckland Council Rates") == "Rates"

    def test_unresolved_routes_to_local_by_default(self, monkeypatch):
        monkeypatch.setattr(ac, "match_rule_category", lambda desc, entity=None: "Uncategorized")
        called = {}
        monkeypatch.setattr(
            ac,
            "guess_ird_category_local_llm",
            lambda **kw: called.update(kw) or "Utilities",
        )
        result = ac.categorize_description_dynamic("Mystery Vendor XYZ", selected_model="Local Qwen 9B (Air-gapped)")
        assert result == "Utilities"
        assert called["local_llm_url"] is None

    def test_unresolved_routes_to_gemini_when_selected(self, monkeypatch):
        monkeypatch.setattr(ac, "match_rule_category", lambda desc, entity=None: "Uncategorized")
        monkeypatch.setattr(ac, "guess_ird_category_gemini", lambda **kw: "Insurance")
        result = ac.categorize_description_dynamic("Mystery Vendor XYZ", selected_model="Gemini 3.8 Flash (Cloud)")
        assert result == "Insurance"


class TestDataFramePasses:
    def test_apply_rule_categorization_fills_unassigned(self, monkeypatch):
        def _match(desc, entity=None):
            if "rates" in str(desc).lower():
                return ("303 Weymouth", "Rates", None)
            return ("Unassigned", "Uncategorized", None)

        monkeypatch.setattr(utils, "match_memorized_or_rule", _match)

        df = pd.DataFrame(
            [
                {"Date": "2026-04-01", "Amount": -100.0, "Description": "Council rates", "Property": "", "Account Code": "Uncategorized"},
                {"Date": "2026-04-02", "Amount": -50.0, "Description": "Random thing", "Property": "", "Account Code": ""},
            ]
        )
        out = ac.apply_rule_categorization(df)
        assert out.loc[0, "Account Code"] == "Rates"
        assert out.loc[0, "Property"] == "303 Weymouth"
        assert out.loc[1, "Account Code"] == "Uncategorized"
        assert out.loc[1, "Property"] == "Unassigned"

    def test_apply_rule_categorization_empty_df(self):
        out = ac.apply_rule_categorization(pd.DataFrame())
        assert out.empty
        assert list(out.columns) == ac.STANDARD_COLUMNS

    def test_portfolio_wide_code_becomes_general(self, monkeypatch):
        monkeypatch.setattr(utils, "match_memorized_or_rule", lambda desc, entity=None: ("Unassigned", "P-OVERHEAD", None))
        df = pd.DataFrame(
            [{"Date": "2026-04-01", "Amount": -10.0, "Description": "Bank fee", "Property": "", "Account Code": ""}]
        )
        out = ac.apply_rule_categorization(df)
        assert out.loc[0, "Property"] == "General / Portfolio"

    def test_ai_fallback_counts_and_dedupes_descriptions(self, monkeypatch):
        monkeypatch.setattr(utils, "match_memorized_or_rule", lambda desc, entity=None: ("Unassigned", "Uncategorized", None))
        monkeypatch.setattr(ac, "apply_rule_categorization", lambda df, entity=None: df.copy())

        calls = []

        def _fake_dynamic(description, **kwargs):
            calls.append(description)
            return "Utilities"

        monkeypatch.setattr(ac, "categorize_description_dynamic", _fake_dynamic)

        df = pd.DataFrame(
            [
                {"Date": "2026-04-01", "Amount": -10.0, "Description": "Mystery A", "Property": "", "Account Code": ""},
                {"Date": "2026-04-02", "Amount": -10.0, "Description": "Mystery A", "Property": "", "Account Code": ""},
                {"Date": "2026-04-03", "Amount": -10.0, "Description": "Mystery B", "Property": "", "Account Code": ""},
            ]
        )
        out, count = ac.apply_ai_fallback_categorization(df)
        assert count == 3
        # "Mystery A" appears twice but the AI is only consulted once (dedupe).
        assert calls.count("Mystery A") == 1
        assert set(out["Account Code"]) == {"Utilities"}

    def test_ai_fallback_skips_blank_descriptions(self, monkeypatch):
        monkeypatch.setattr(utils, "match_memorized_or_rule", lambda desc, entity=None: ("Unassigned", "Uncategorized", None))
        monkeypatch.setattr(ac, "apply_rule_categorization", lambda df, entity=None: df.copy())
        monkeypatch.setattr(ac, "categorize_description_dynamic", lambda **kw: pytest.fail("blank desc must not hit AI"))

        df = pd.DataFrame(
            [{"Date": "2026-04-01", "Amount": -10.0, "Description": "", "Property": "", "Account Code": ""}]
        )
        out, count = ac.apply_ai_fallback_categorization(df)
        assert count == 0
        assert out.loc[0, "Account Code"] == "Uncategorized"


class TestReExports:
    def test_utils_reexports_are_identical_objects(self):
        for name in (
            "get_categorization_system_prompt",
            "guess_ird_category_local_llm",
            "guess_ird_category_gemini",
            "categorize_description_dynamic",
            "match_rule_category",
            "apply_rule_categorization",
            "apply_ai_fallback_categorization",
        ):
            assert getattr(utils, name) is getattr(ac, name)

    def test_core_constants_have_single_source(self):
        import axiom_core

        assert utils.CATEGORIZATION_MODEL_OPTIONS is axiom_core.CATEGORIZATION_MODEL_OPTIONS
        assert utils.US_IRS_SYSTEM_PROMPT is axiom_core.US_IRS_SYSTEM_PROMPT
        assert utils.NZ_IRD_SYSTEM_PROMPT is axiom_core.NZ_IRD_SYSTEM_PROMPT
