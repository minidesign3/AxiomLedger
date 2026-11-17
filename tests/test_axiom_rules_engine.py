"""
Tests for the deterministic matching half of ``axiom_rules.py``:
loan-account priority, memorized-rule scoring/disambiguation and the built-in
jurisdiction/vendor keyword dictionary.

Uses the shared ``rules_env`` fixture (tests/conftest.py): in-memory store, no
disk/SQLite, all external helper seams stubbed.
"""

from __future__ import annotations

import pandas as pd
import pytest

import axiom_core
import axiom_rules

UNASSIGNED = axiom_core.UNASSIGNED_PROPERTY


def _set_loan(monkeypatch, loan: dict | None) -> None:
    monkeypatch.setattr(axiom_rules, "match_loan_from_text", lambda text, entity=None: loan)


class TestMatchEngineLoanPriority:
    LOAN = {
        "property_name": "9 Damian",
        "interest_account_code": "Loan Interest",
        "principal_account_code": "Principal Loan",
    }

    def test_principal_wording_wins(self, monkeypatch, rules_env):
        _set_loan(monkeypatch, self.LOAN)
        assert axiom_rules.match_memorized_or_rule("Loan repayment 88-01") == (
            "9 Damian",
            "Principal Loan",
            "Memorized",
        )

    def test_interest_wording_wins(self, monkeypatch, rules_env):
        _set_loan(monkeypatch, self.LOAN)
        assert axiom_rules.match_memorized_or_rule("Debit interest charged") == (
            "9 Damian",
            "Loan Interest",
            "Memorized",
        )

    def test_bare_transfer_defaults_to_interest(self, monkeypatch, rules_env):
        _set_loan(monkeypatch, self.LOAN)
        assert axiom_rules.match_memorized_or_rule("Transfer 88-01") == (
            "9 Damian",
            "Loan Interest",
            "Memorized",
        )

    def test_interest_code_remapped_when_entity_lacks_loan_interest(self, monkeypatch, rules_env):
        rules_env["codes"][:] = ["Interest", "Rates"]
        _set_loan(monkeypatch, self.LOAN)
        assert axiom_rules.match_memorized_or_rule("Interest charged")[1] == "Interest"

    def test_account_code_lookup_failure_keeps_default(self, monkeypatch, rules_env):
        def boom(entity=None):
            raise RuntimeError("no session")

        monkeypatch.setattr(axiom_rules, "get_entity_account_codes", boom)
        _set_loan(monkeypatch, self.LOAN)
        assert axiom_rules.match_memorized_or_rule("Interest charged")[1] == "Loan Interest"

    def test_loan_lookup_failure_falls_through(self, monkeypatch, rules_env):
        def boom(text, entity=None):
            raise RuntimeError("bad lookup")

        monkeypatch.setattr(axiom_rules, "match_loan_from_text", boom)
        assert axiom_rules.match_memorized_or_rule("Unmatched text") == (
            UNASSIGNED,
            "Uncategorized",
            "Unknown",
        )


class TestMatchEngineMemorizedRules:
    def test_empty_or_missing_description(self, rules_env):
        assert axiom_rules.match_memorized_or_rule(None) == (UNASSIGNED, "Uncategorized", "Unknown")
        assert axiom_rules.match_memorized_or_rule(pd.NA) == (UNASSIGNED, "Uncategorized", "Unknown")
        assert axiom_rules.match_memorized_or_rule("   ") == (UNASSIGNED, "Uncategorized", "Unknown")

    def test_exact_rule_with_property_and_code(self, rules_env, monkeypatch):
        monkeypatch.setattr(
            axiom_rules,
            "get_memorized_rules",
            lambda entity=None: [
                {"pattern": "Tower", "property": "9 Damian", "account_code": "Insurance"}
            ],
        )
        assert axiom_rules.match_memorized_or_rule("Tower") == ("9 Damian", "Insurance", "Memorized")

    def test_explicit_property_overrides_rule_property(self, rules_env, monkeypatch):
        monkeypatch.setattr(
            axiom_rules,
            "get_memorized_rules",
            lambda entity=None: [
                {"pattern": "Tower", "property": "9 Damian", "account_code": "Insurance"}
            ],
        )
        rules_env["detected"]["tower"] = "33 Blampied"
        assert axiom_rules.match_memorized_or_rule("Tower") == ("33 Blampied", "Insurance", "Memorized")

    def test_property_bonus_selects_matching_rule(self, rules_env, monkeypatch):
        rules = [
            {"pattern": "Tower", "property": "9 Damian", "account_code": "Insurance A"},
            {"pattern": "Tower", "property": "33 Blampied", "account_code": "Insurance B"},
        ]
        monkeypatch.setattr(axiom_rules, "get_memorized_rules", lambda entity=None: list(rules))
        rules_env["detected"]["tower"] = "33 Blampied"
        assert axiom_rules.match_memorized_or_rule("Tower")[1] == "Insurance B"

    def test_multiple_saved_properties_without_explicit_is_partial(self, rules_env, monkeypatch):
        rules = [
            {"pattern": "Tower", "property": "9 Damian", "account_code": "Insurance"},
            {"pattern": "Tower", "property": "33 Blampied", "account_code": "Insurance"},
        ]
        monkeypatch.setattr(axiom_rules, "get_memorized_rules", lambda entity=None: list(rules))
        prop, code, source = axiom_rules.match_memorized_or_rule("Tower")
        assert (prop, code, source) == (UNASSIGNED, "Insurance", "Memorized Partial")

    def test_unassigned_property_rule_is_partial(self, rules_env, monkeypatch):
        monkeypatch.setattr(
            axiom_rules,
            "get_memorized_rules",
            lambda entity=None: [
                {"pattern": "Tower", "property": UNASSIGNED, "account_code": "Insurance"}
            ],
        )
        assert axiom_rules.match_memorized_or_rule("Tower")[2] == "Memorized Partial"

    def test_portfolio_wide_code_fills_general_property(self, rules_env, monkeypatch):
        rules_env["portfolio_codes"].add("Accountant")
        monkeypatch.setattr(
            axiom_rules,
            "get_memorized_rules",
            lambda entity=None: [
                {"pattern": "Fees", "property": UNASSIGNED, "account_code": "Accountant"}
            ],
        )
        assert axiom_rules.match_memorized_or_rule("Fees") == (
            "General / Portfolio",
            "Accountant",
            "Memorized",
        )

    def test_uncategorized_code_is_partial(self, rules_env, monkeypatch):
        monkeypatch.setattr(
            axiom_rules,
            "get_memorized_rules",
            lambda entity=None: [
                {"pattern": "Tower", "property": "9 Damian", "account_code": "Uncategorized"}
            ],
        )
        assert axiom_rules.match_memorized_or_rule("Tower")[2] == "Memorized Partial"

    def test_exact_beats_substring_rule(self, rules_env, monkeypatch):
        rules = [
            {"pattern": "tower insurance", "property": "9 Damian", "account_code": "Exact"},
            {"pattern": "tower", "property": "9 Damian", "account_code": "Substring"},
        ]
        monkeypatch.setattr(axiom_rules, "get_memorized_rules", lambda entity=None: list(rules))
        assert axiom_rules.match_memorized_or_rule("Tower Insurance")[1] == "Exact"

    def test_saved_rule_round_trips_through_store(self, rules_env):
        axiom_rules.save_memorized_rule("Tower", "9 Damian", "Insurance")
        prop, code, source = axiom_rules.match_memorized_or_rule("Tower")
        assert (prop, code, source) == ("9 Damian", "Insurance", "Memorized")


class TestMatchEngineKeywords:
    def test_strong_vendor_with_explicit_house(self, rules_env):
        rules_env["entity_rules"]["tower"] = "Insurance"
        rules_env["detected"]["tower insurance 9 damian"] = "9 Damian"
        assert axiom_rules.match_memorized_or_rule("Tower Insurance 9 Damian") == (
            "9 Damian",
            "Insurance",
            "Aligned Vendor+House",
        )

    def test_generic_keyword_without_house(self, rules_env):
        rules_env["entity_rules"]["tower"] = "Insurance"
        assert axiom_rules.match_memorized_or_rule("Tower Insurance") == (
            UNASSIGNED,
            "Insurance",
            "Keyword Rule",
        )

    def test_short_keyword_uses_word_boundaries(self, rules_env):
        rules_env["entity_rules"]["rent"] = "Rent Income"
        assert axiom_rules.match_memorized_or_rule("Parent payment")[2] == "Unknown"

    def test_longest_keyword_wins(self, rules_env):
        rules_env["entity_rules"].update({"rates": "Rates", "auckland council": "Rates"})
        assert axiom_rules.match_memorized_or_rule("Auckland Council payment")[2] == "Keyword Rule"
        assert (
            axiom_rules.match_memorized_or_rule("Auckland Council payment")[1] == "Rates"
        )

    @pytest.mark.parametrize(
        ("keyword", "jurisdiction_code", "entity_code"),
        [
            ("tenant deposit", "Rental Income", "Rent Income"),
            ("levy notice", "Body Corporate Levies", "Bodycorp"),
            ("meter reading", "Water Rates", "Watercare"),
        ],
    )
    def test_jurisdiction_code_remapped_to_entity_code(
        self, rules_env, keyword, jurisdiction_code, entity_code
    ):
        rules_env["codes"].append(entity_code)
        rules_env["entity_rules"][keyword] = jurisdiction_code
        assert axiom_rules.match_memorized_or_rule(keyword)[1] == entity_code

    def test_portfolio_wide_keyword_code_gets_general_property(self, rules_env):
        rules_env["portfolio_codes"].add("Accountant")
        rules_env["entity_rules"]["accountant"] = "Accountant"
        assert axiom_rules.match_memorized_or_rule("Accountant fees") == (
            "General / Portfolio",
            "Accountant",
            "Keyword Rule",
        )

    def test_no_match_returns_unknown(self, rules_env):
        assert axiom_rules.match_memorized_or_rule("Completely unrelated") == (
            UNASSIGNED,
            "Uncategorized",
            "Unknown",
        )


