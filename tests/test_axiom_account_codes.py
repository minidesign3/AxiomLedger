"""
Tests for ``axiom_account_codes.py``: entity Chart-of-Accounts CRUD, Property
shorthand aliases, smart property-token derivation and deterministic property
detection.

Every test runs through the ``entities_env`` fixture in ``conftest.py``: a
temporary SQLite DB + vault root and an in-memory memory store, so no real user
data, ``data/`` directory or ``.axiom_memory.json`` is ever touched.
"""

from __future__ import annotations

import pandas as pd
import pytest

import axiom_account_codes
import axiom_core
import axiom_db
import axiom_rules
import utils

NZ_DEFAULTS = list(axiom_core.DEFAULT_CUSTOM_ACCOUNT_CODES)


def _sorted(codes):
    return sorted(codes, key=lambda s: s.lower())


class TestGetEntityAccountCodes:
    def test_seeds_nz_defaults(self, entities_env):
        assert axiom_account_codes.get_entity_account_codes("Alpha") == _sorted(NZ_DEFAULTS)
        assert entities_env["store"]["entity_account_codes"]["Alpha"] == NZ_DEFAULTS
        assert len(entities_env["saved"]) == 1

    def test_returns_sorted_case_insensitively(self, entities_env):
        # A US entity keeps its configured codes untouched (no NZ rent-income repair).
        entities_env["store"]["entity_account_codes"]["Beta"] = ["rates", "Bank Fee", "accountant"]
        assert axiom_account_codes.get_entity_account_codes("Beta") == ["accountant", "Bank Fee", "rates"]

    def test_adds_missing_rent_income_for_nz(self, entities_env):
        entities_env["store"]["entity_account_codes"]["Alpha"] = ["Rates"]
        result = axiom_account_codes.get_entity_account_codes("Alpha")
        assert result == ["Rates", "Rent Income", "Rent Income Raywhite"]
        assert entities_env["saved"][-1]["entity_account_codes"]["Alpha"] == result

    def test_existing_nz_codes_without_rent_income_are_not_resaved_twice(self, entities_env):
        entities_env["store"]["entity_account_codes"]["Alpha"] = ["Rates", "Rent Income", "Rent Income Raywhite"]
        axiom_account_codes.get_entity_account_codes("Alpha")
        assert entities_env["saved"] == []

    def test_seeds_us_codes_from_jurisdiction_rules(self, entities_env):
        result = axiom_account_codes.get_entity_account_codes("Beta")
        expected = sorted(set(axiom_core.ENTITY_CATEGORIZATION_RULES["US"].values()))
        assert result == _sorted(expected)

    def test_repairs_non_dict_codes_map(self, entities_env):
        entities_env["store"]["entity_account_codes"] = "garbage"
        assert axiom_account_codes.get_entity_account_codes("Alpha") == _sorted(NZ_DEFAULTS)
        assert isinstance(entities_env["store"]["entity_account_codes"], dict)

    def test_defaults_to_active_entity(self, entities_env):
        assert axiom_account_codes.get_entity_account_codes() == _sorted(NZ_DEFAULTS)
        assert "Alpha" in entities_env["store"]["entity_account_codes"]

    def test_returns_independent_list(self, entities_env):
        result = axiom_account_codes.get_entity_account_codes("Alpha")
        result.append("mutated")
        assert "mutated" not in entities_env["store"]["entity_account_codes"]["Alpha"]


class TestAddEntityAccountCode:
    def test_rejects_blank_name(self, entities_env):
        ok, msg = axiom_account_codes.add_entity_account_code("   ", "Alpha")
        assert not ok and "empty" in msg.lower()
        assert entities_env["saved"] == []

    def test_rejects_uncategorized_marker(self, entities_env):
        ok, _ = axiom_account_codes.add_entity_account_code("uncategorized", "Alpha")
        assert not ok

    def test_rejects_case_insensitive_duplicate(self, entities_env):
        entities_env["store"]["entity_account_codes"]["Alpha"] = ["Rates"]
        ok, msg = axiom_account_codes.add_entity_account_code("RATES", "Alpha")
        assert not ok and "already exists" in msg

    def test_appends_and_sorts(self, entities_env):
        # US entity: no NZ rent-income repair is applied underneath.
        entities_env["store"]["entity_account_codes"]["Beta"] = ["Rates", "Watercare"]
        ok, msg = axiom_account_codes.add_entity_account_code("  Insurance  ", "Beta")
        assert ok and "Insurance" in msg
        assert entities_env["store"]["entity_account_codes"]["Beta"] == ["Insurance", "Rates", "Watercare"]


class TestRemoveEntityAccountCode:
    def test_removes_case_insensitively(self, entities_env):
        entities_env["store"]["entity_account_codes"]["Beta"] = ["Rates", "Insurance"]
        ok, msg = axiom_account_codes.remove_entity_account_code("rates", "Beta")
        assert ok and "rates" in msg
        assert entities_env["store"]["entity_account_codes"]["Beta"] == ["Insurance"]

    def test_removing_absent_code_is_a_noop(self, entities_env):
        entities_env["store"]["entity_account_codes"]["Beta"] = ["Rates"]
        ok, _ = axiom_account_codes.remove_entity_account_code("Missing", "Beta")
        assert ok
        assert entities_env["store"]["entity_account_codes"]["Beta"] == ["Rates"]


class TestRenameEntityAccountCode:
    def test_rejects_empty_target(self, entities_env):
        ok, msg = axiom_account_codes.rename_entity_account_code("Rates", "  ", "Alpha")
        assert not ok and "empty" in msg.lower()

    def test_same_name_is_a_noop(self, entities_env):
        ok, msg = axiom_account_codes.rename_entity_account_code("Rates", "Rates", "Alpha")
        assert ok and "unchanged" in msg
        assert entities_env["saved"] == []

    def test_renames_and_dedupes_code_list(self, entities_env):
        # rename_entity_account_code() calls init_workspace_state(), which folds the
        # stored list into the NZ defaults, so the rent-income codes are present.
        entities_env["store"]["entity_account_codes"]["Alpha"] = [
            "Rates",
            "Insurance",
            "Council Rates",
            "Rent Income",
            "Rent Income Raywhite",
        ]
        ok, _ = axiom_account_codes.rename_entity_account_code("rates", "Council Rates", "Alpha")
        assert ok
        assert entities_env["store"]["entity_account_codes"]["Alpha"] == [
            "Council Rates",
            "Insurance",
            "Rent Income",
            "Rent Income Raywhite",
        ]

    def test_appends_unknown_source_code(self, entities_env):
        entities_env["store"]["entity_account_codes"]["Alpha"] = ["Rates", "Rent Income", "Rent Income Raywhite"]
        axiom_account_codes.rename_entity_account_code("Missing", "New Code", "Alpha")
        assert entities_env["store"]["entity_account_codes"]["Alpha"] == [
            "New Code",
            "Rates",
            "Rent Income",
            "Rent Income Raywhite",
        ]

    def test_migrates_memorized_rules(self, entities_env):
        entities_env["store"]["entity_account_codes"]["Alpha"] = ["Rates"]
        entities_env["store"]["memorized_rules"]["Alpha"] = [
            {"pattern": "council", "account_code": "Rates"},
            {"pattern": "tower", "account_code": "Insurance"},
        ]
        axiom_account_codes.rename_entity_account_code("Rates", "Council Rates", "Alpha")
        rules = entities_env["store"]["memorized_rules"]["Alpha"]
        assert rules[0]["account_code"] == "Council Rates"
        assert rules[1]["account_code"] == "Insurance"

    def test_migrates_session_dataframes(self, entities_env):
        import streamlit as st

        entities_env["store"]["entity_account_codes"]["Alpha"] = ["Rates"]
        files_df = pd.DataFrame({"Description": ["a"], "Account Code": ["rates"]})
        ledger_df = pd.DataFrame({"Description": ["b"], "Account Code": ["Rates"]})
        st.session_state["entity_uploaded_files"] = {"Alpha": {"2026": [{"df": files_df}]}}
        st.session_state["entity_year_ledgers"] = {"Alpha": {"2026": ledger_df}}
        st.session_state["entity_ledgers"] = {"Alpha": ledger_df}

        axiom_account_codes.rename_entity_account_code("Rates", "Council Rates", "Alpha")

        assert files_df["Account Code"].tolist() == ["Council Rates"]
        assert ledger_df["Account Code"].tolist() == ["Council Rates"]
        assert st.session_state["entity_ledgers"]["Alpha"]["Account Code"].tolist() == ["Council Rates"]

    def test_migrates_sqlite_rows_for_entity_only(self, entities_env):
        entities_env["store"]["entity_account_codes"]["Alpha"] = ["Rates"]
        axiom_db._ensure_sqlite_schema()
        with axiom_db._get_sqlite_conn() as conn:
            for entity, file_id in (("Alpha", "f1"), ("Beta", "f2")):
                conn.execute(
                    "INSERT INTO uploaded_files (file_id, entity, tax_year, signature, name, bucket, uploaded_at, vault_path)"
                    " VALUES (?, ?, '2026', 'sig', 'x.csv', 'csv', 'now', '')",
                    (file_id, entity),
                )
                conn.execute(
                    "INSERT INTO file_transactions (file_id, row_index, account_code) VALUES (?, 0, 'Rates')",
                    (file_id,),
                )
                conn.execute(
                    "INSERT INTO master_ledgers (entity, tax_year, row_index, account_code)"
                    " VALUES (?, '2026', 0, 'Rates')",
                    (entity,),
                )

        axiom_account_codes.rename_entity_account_code("Rates", "Council Rates", "Alpha")

        with axiom_db._get_sqlite_conn() as conn:
            alpha_tx = conn.execute("SELECT account_code FROM file_transactions WHERE file_id = 'f1'").fetchone()[0]
            beta_tx = conn.execute("SELECT account_code FROM file_transactions WHERE file_id = 'f2'").fetchone()[0]
            alpha_ml = conn.execute("SELECT account_code FROM master_ledgers WHERE entity = 'Alpha'").fetchone()[0]
            beta_ml = conn.execute("SELECT account_code FROM master_ledgers WHERE entity = 'Beta'").fetchone()[0]
        assert (alpha_tx, alpha_ml) == ("Council Rates", "Council Rates")
        assert (beta_tx, beta_ml) == ("Rates", "Rates")


class TestGetPropertyAliases:
    def test_seeds_defaults_for_known_properties(self, entities_env):
        result = axiom_account_codes.get_property_aliases("Alpha")
        assert result["303 Weymouth"] == list(axiom_core.DEFAULT_PROPERTY_ALIASES["303 Weymouth"])
        assert result["General / Portfolio"] == list(axiom_core.DEFAULT_PROPERTY_ALIASES["General / Portfolio"])
        saved_aliases = entities_env["store"]["entity_property_aliases"]["Alpha"]
        assert saved_aliases["303 Weymouth"] == result["303 Weymouth"]

    def test_unconfigured_property_falls_back_to_lowercase_name(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["1 New Street"]
        assert axiom_account_codes.get_property_aliases("Alpha") == {"1 New Street": ["1 new street"]}

    def test_adds_missing_property_to_existing_map(self, entities_env):
        entities_env["store"]["entity_property_aliases"]["Alpha"] = {"303 Weymouth": ["weymouth"]}
        entities_env["store"]["entity_properties"]["Alpha"] = ["303 Weymouth", "9 Damian"]
        result = axiom_account_codes.get_property_aliases("Alpha")
        assert result["303 Weymouth"] == ["weymouth"]
        assert result["9 Damian"] == list(axiom_core.DEFAULT_PROPERTY_ALIASES["9 Damian"])

    def test_returns_independent_top_level_dict(self, entities_env):
        # The contract is a shallow copy: adding keys must not leak into the store.
        result = axiom_account_codes.get_property_aliases("Alpha")
        result["Extra Property"] = ["extra"]
        assert "Extra Property" not in entities_env["store"]["entity_property_aliases"]["Alpha"]

    def test_defaults_to_active_entity(self, entities_env):
        assert "303 Weymouth" in axiom_account_codes.get_property_aliases()


class TestSavePropertyAliases:
    def test_rejects_blank_property(self, entities_env):
        ok, msg = axiom_account_codes.save_property_aliases("  ", "alias", "Alpha")
        assert not ok and "empty" in msg.lower()

    def test_splits_and_dedupes_csv(self, entities_env):
        ok, msg = axiom_account_codes.save_property_aliases("9 Damian", " damian, Way ,damian,", "Alpha")
        assert ok and "2 matching" in msg
        assert entities_env["store"]["entity_property_aliases"]["Alpha"]["9 Damian"] == ["damian", "Way"]

    def test_accepts_list_and_drops_blanks(self, entities_env):
        axiom_account_codes.save_property_aliases("9 Damian", ["a", " ", "b"], "Alpha")
        assert entities_env["store"]["entity_property_aliases"]["Alpha"]["9 Damian"] == ["a", "b"]

    def test_overwrites_existing_aliases(self, entities_env):
        entities_env["store"]["entity_property_aliases"]["Alpha"] = {"9 Damian": ["old"]}
        axiom_account_codes.save_property_aliases("9 Damian", "new", "Alpha")
        assert entities_env["store"]["entity_property_aliases"]["Alpha"]["9 Damian"] == ["new"]


class TestDeriveSmartPropertyTokens:
    def test_general_portfolio_and_unassigned_have_no_tokens(self):
        for name in ("General / Portfolio", "general / portfolio-wide", " Unassigned ", ""):
            assert axiom_account_codes._derive_smart_property_tokens(name) == []

    def test_includes_default_aliases_and_house_words(self):
        tokens = axiom_account_codes._derive_smart_property_tokens("9 Damian", [])
        assert {"9 damian", "damian", "damien", "9damian"} <= set(tokens)

    def test_adds_compact_number_street_forms(self):
        tokens = axiom_account_codes._derive_smart_property_tokens("33 Blampied", [])
        assert {"33blampied", "33 blampied", "33blam", "33bla"} <= set(tokens)

    def test_adds_four_digit_unit_numbers(self):
        tokens = axiom_account_codes._derive_smart_property_tokens("1604 / 363 Queen", [])
        assert "1604" in tokens

    def test_configured_aliases_are_included(self):
        tokens = axiom_account_codes._derive_smart_property_tokens("9 Damian", ["Custom Alias"])
        assert "custom alias" in tokens

    def test_tokens_sorted_longest_first(self):
        tokens = axiom_account_codes._derive_smart_property_tokens("303 Weymouth")
        assert tokens == sorted(tokens, key=len, reverse=True)


class TestDetectPropertyFromText:
    @pytest.mark.parametrize("value", [None, float("nan"), "", "nan", "None", "#N/A"])
    def test_empty_values_are_unassigned(self, entities_env, value):
        assert axiom_account_codes.detect_property_from_text(value, "Alpha") == axiom_core.UNASSIGNED_PROPERTY

    def test_exact_property_name_wins(self, entities_env):
        assert axiom_account_codes.detect_property_from_text("33 Blampied", "Alpha") == "33 Blampied"

    def test_alias_match_is_case_insensitive(self, entities_env):
        assert axiom_account_codes.detect_property_from_text("PAYMENT TO BLAMPIED", "Alpha") == "33 Blampied"

    def test_multi_property_line_joins_matches(self, entities_env):
        result = axiom_account_codes.detect_property_from_text("33Blam 14Ral", "Alpha")
        assert result == "33 Blampied / 14 Raleigh"

    def test_general_portfolio_is_never_matched(self, entities_env):
        assert axiom_account_codes.detect_property_from_text("portfolio-wide overhead", "Alpha") == (
            axiom_core.UNASSIGNED_PROPERTY
        )

    def test_loan_trigger_fallback(self, entities_env, monkeypatch):
        monkeypatch.setattr(
            axiom_account_codes,
            "match_loan_from_text",
            lambda text, entity=None: {"property_name": "9 Damian"} if "88-01" in text else None,
        )
        assert axiom_account_codes.detect_property_from_text("Transfer 88-01", "Alpha") == "9 Damian"

    def test_loan_trigger_failure_is_swallowed(self, entities_env, monkeypatch):
        def boom(text, entity=None):
            raise RuntimeError("loan lookup failed")

        monkeypatch.setattr(axiom_account_codes, "match_loan_from_text", boom)
        assert axiom_account_codes.detect_property_from_text("unmatched text", "Alpha") == (
            axiom_core.UNASSIGNED_PROPERTY
        )

    def test_defaults_to_active_entity(self, entities_env):
        assert axiom_account_codes.detect_property_from_text("damian") == "9 Damian"


class TestGetAccountCodeOptions:
    def test_uncategorized_first_then_sorted(self, entities_env):
        entities_env["store"]["entity_account_codes"]["Alpha"] = ["Rates", "Insurance"]
        result = axiom_account_codes.get_account_code_options("Alpha")
        assert result[0] == "Uncategorized"
        assert result[1:] == _sorted(result[1:])
        assert {"Rates", "Insurance", "Rental Income", "Rent Income", "Owner Drawing"} <= set(result)

    def test_includes_meaningful_memorized_codes(self, entities_env, monkeypatch):
        entities_env["store"]["entity_account_codes"]["Alpha"] = ["Rates"]
        monkeypatch.setattr(
            axiom_rules,
            "get_memorized_rules",
            lambda entity=None: [{"account_code": "Bodycorp"}, {"account_code": "uncategorized"}, {"account_code": ""}],
        )
        result = axiom_account_codes.get_account_code_options("Alpha")
        assert "Bodycorp" in result
        assert "uncategorized" not in result

    def test_deduplicates_jurisdiction_and_entity_codes(self, entities_env):
        entities_env["store"]["entity_account_codes"]["Alpha"] = ["Rates", "Rates"]
        result = axiom_account_codes.get_account_code_options("Alpha")
        assert result.count("Rates") == 1


class TestGetEntityRules:
    def test_nz_entity_uses_nz_dictionary(self, entities_env):
        assert axiom_account_codes.get_entity_rules("Alpha") == axiom_core.ENTITY_CATEGORIZATION_RULES["NZ"]

    def test_us_entity_uses_us_dictionary(self, entities_env):
        assert axiom_account_codes.get_entity_rules("Beta") == axiom_core.ENTITY_CATEGORIZATION_RULES["US"]

    def test_unknown_entity_defaults_to_nz(self, entities_env):
        assert axiom_account_codes.get_entity_rules("Gamma") == axiom_core.ENTITY_CATEGORIZATION_RULES["NZ"]


@pytest.mark.parametrize(
    "name",
    [
        "_GENERIC_STREET_SUFFIXES",
        "_derive_smart_property_tokens",
        "add_entity_account_code",
        "detect_property_from_text",
        "get_account_code_options",
        "get_entity_account_codes",
        "get_entity_rules",
        "get_property_aliases",
        "remove_entity_account_code",
        "rename_entity_account_code",
        "save_property_aliases",
    ],
)
def test_utils_reexports_same_object(name):
    assert getattr(utils, name) is getattr(axiom_account_codes, name)
