from __future__ import annotations

import pytest

import axiom_core
import axiom_rules
import utils

UNASSIGNED = axiom_core.UNASSIGNED_PROPERTY


class TestGetMemorizedRules:
    def test_defaults_to_empty_list(self, rules_env):
        assert axiom_rules.get_memorized_rules("Entity A") == []
        assert rules_env["store"]["memorized_rules"]["Entity A"] == []

    def test_returns_copy_not_store_reference(self, rules_env):
        rules_env["store"]["memorized_rules"]["Entity A"] = [{"pattern": "tower"}]
        result = axiom_rules.get_memorized_rules("Entity A")
        result.append({"pattern": "mutated"})
        assert len(rules_env["store"]["memorized_rules"]["Entity A"]) == 1

    def test_repairs_non_dict_rules_map(self, rules_env):
        rules_env["store"]["memorized_rules"] = "garbage"
        assert axiom_rules.get_memorized_rules("Entity A") == []
        assert isinstance(rules_env["store"]["memorized_rules"], dict)

    def test_repairs_non_list_entity_entry(self, rules_env):
        rules_env["store"]["memorized_rules"]["Entity A"] = {"pattern": "tower"}
        assert axiom_rules.get_memorized_rules("Entity A") == []


class TestSaveMemorizedRule:
    def test_rejects_empty_pattern(self, rules_env):
        ok, msg = axiom_rules.save_memorized_rule("   ", "9 Damian", "Insurance")
        assert not ok
        assert "empty" in msg.lower()
        assert rules_env["saved"] == []

    def test_inserts_new_rule_at_front(self, rules_env):
        rules_env["store"]["memorized_rules"]["Test Entity"] = [{"pattern": "old"}]
        ok, msg = axiom_rules.save_memorized_rule("Tower", "9 Damian", "Insurance")
        rules = rules_env["store"]["memorized_rules"]["Test Entity"]
        assert ok and "Tower" in msg
        assert rules[0] == {
            "pattern": "Tower",
            "property": "9 Damian",
            "account_code": "Insurance",
            "source": "User Coded",
        }
        assert len(rules) == 2
        assert len(rules_env["saved"]) == 1

    def test_upserts_case_insensitively(self, rules_env):
        axiom_rules.save_memorized_rule("Tower", "9 Damian", "Insurance")
        axiom_rules.save_memorized_rule("tower", "9 damian", "Rates", source="Edited")
        rules = rules_env["store"]["memorized_rules"]["Test Entity"]
        assert len(rules) == 1
        assert rules[0]["pattern"] == "tower"
        assert rules[0]["account_code"] == "Rates"
        assert rules[0]["source"] == "Edited"

    def test_same_pattern_different_property_adds_second_rule(self, rules_env):
        axiom_rules.save_memorized_rule("Tower", "9 Damian", "Insurance")
        axiom_rules.save_memorized_rule("Tower", "33 Blampied", "Insurance")
        assert len(rules_env["store"]["memorized_rules"]["Test Entity"]) == 2

    def test_auto_registers_new_account_code_sorted(self, rules_env):
        axiom_rules.save_memorized_rule("Zeta", "9 Damian", "Zeta Code")
        codes = rules_env["store"]["entity_account_codes"]["Test Entity"]
        assert codes == ["Insurance", "Loan Interest", "Rates", "Zeta Code"]
        assert codes == sorted(codes, key=str.lower)

    def test_does_not_register_uncategorized_marker(self, rules_env):
        axiom_rules.save_memorized_rule("Zeta", "9 Damian", "Uncategorized")
        assert "Test Entity" not in rules_env["store"]["entity_account_codes"]

    def test_auto_registers_new_property(self, rules_env):
        axiom_rules.save_memorized_rule("Zeta", "99 New Rd", "Insurance")
        assert "99 New Rd" in rules_env["store"]["entity_properties"]["Test Entity"]

    def test_does_not_register_unassigned_property(self, rules_env):
        axiom_rules.save_memorized_rule("Zeta", UNASSIGNED, "Insurance")
        assert "Test Entity" not in rules_env["store"]["entity_properties"]

    def test_compound_pattern_adds_vendor_auto_rule(self, rules_env):
        axiom_rules.save_memorized_rule(
            "6851Tower Insurance | 9 Damian", "9 Damian", "Insurance"
        )
        rules = rules_env["store"]["memorized_rules"]["Test Entity"]
        vendor = [r for r in rules if r["pattern"] == "6851Tower Insurance"]
        assert len(vendor) == 1
        assert vendor[0]["property"] == UNASSIGNED
        assert vendor[0]["source"] == "User Coded (Vendor Auto-Rule)"

    def test_vendor_auto_rule_uses_detected_property(self, rules_env):
        rules_env["detected"]["6851tower insurance"] = "9 Damian"
        axiom_rules.save_memorized_rule(
            "6851Tower Insurance | 9 Damian", "9 Damian", "Insurance"
        )
        vendor = next(
            r
            for r in rules_env["store"]["memorized_rules"]["Test Entity"]
            if r["pattern"] == "6851Tower Insurance"
        )
        assert vendor["property"] == "9 Damian"

    def test_vendor_auto_rule_portfolio_wide(self, rules_env):
        rules_env["portfolio_codes"].add("Accountant")
        axiom_rules.save_memorized_rule(
            "Invoice 123 | Fees", "9 Damian", "Accountant"
        )
        vendor = next(
            r
            for r in rules_env["store"]["memorized_rules"]["Test Entity"]
            if r["pattern"] == "Invoice 123"
        )
        assert vendor["property"] == "General / Portfolio"

    def test_vendor_auto_rule_not_duplicated(self, rules_env):
        rules_env["store"]["memorized_rules"]["Test Entity"] = [
            {"pattern": "6851tower insurance", "property": UNASSIGNED, "account_code": "Insurance"}
        ]
        axiom_rules.save_memorized_rule(
            "6851Tower Insurance | 9 Damian", "9 Damian", "Insurance"
        )
        rules = rules_env["store"]["memorized_rules"]["Test Entity"]
        assert sum(1 for r in rules if r["pattern"].lower() == "6851tower insurance") == 1

    def test_no_vendor_auto_rule_for_uncategorized_code(self, rules_env):
        axiom_rules.save_memorized_rule("Vendor | House", "House", "Uncategorized")
        patterns = [
            r["pattern"] for r in rules_env["store"]["memorized_rules"]["Test Entity"]
        ]
        assert patterns == ["Vendor | House"]

    def test_no_vendor_auto_rule_without_pipe_separator(self, rules_env):
        axiom_rules.save_memorized_rule("Tower + Damian", "9 Damian", "Insurance")
        patterns = [
            r["pattern"] for r in rules_env["store"]["memorized_rules"]["Test Entity"]
        ]
        assert patterns == ["Tower + Damian"]


class TestDeleteMemorizedRule:
    def test_removes_case_insensitive_and_keeps_others(self, rules_env):
        rules_env["store"]["memorized_rules"]["Test Entity"] = [
            {"pattern": "Tower"},
            {"pattern": "Watercare"},
        ]
        ok, msg = axiom_rules.delete_memorized_rule("tower")
        assert ok and "Removed" in msg
        assert [r["pattern"] for r in rules_env["store"]["memorized_rules"]["Test Entity"]] == [
            "Watercare"
        ]
        assert len(rules_env["saved"]) == 1


class TestReplaceMemorizedRules:
    def test_replaces_whole_table_skipping_blank_patterns(self, rules_env):
        rules_env["store"]["memorized_rules"]["Test Entity"] = [{"pattern": "Old"}]
        ok, msg = axiom_rules.replace_memorized_rules(
            [
                {"pattern": "Tower", "property": "9 Damian", "account_code": "Insurance"},
                {"pattern": "  ", "property": "x", "account_code": "y"},
                {"pattern": "nan", "property": "x", "account_code": "y"},
                {"pattern": None, "property": "x", "account_code": "y"},
            ]
        )
        assert ok and "1 Item + Property" in msg
        rules = rules_env["store"]["memorized_rules"]["Test Entity"]
        assert [r["pattern"] for r in rules] == ["Tower"]

    def test_auto_detect_property_becomes_unassigned(self, rules_env):
        axiom_rules.replace_memorized_rules(
            [
                {
                    "pattern": "Tower",
                    "property": "Auto-detect House from CSV Line",
                    "account_code": "Insurance",
                }
            ]
        )
        rule = rules_env["store"]["memorized_rules"]["Test Entity"][0]
        assert rule["property"] == UNASSIGNED

    def test_portfolio_wide_code_forces_general(self, rules_env):
        rules_env["portfolio_codes"].add("Accountant")
        axiom_rules.replace_memorized_rules(
            [{"pattern": "Fees", "property": "9 Damian", "account_code": "Accountant"}]
        )
        assert (
            rules_env["store"]["memorized_rules"]["Test Entity"][0]["property"]
            == "General / Portfolio"
        )

    def test_dedupes_case_insensitive_triples(self, rules_env):
        axiom_rules.replace_memorized_rules(
            [
                {"pattern": "Tower", "property": "9 Damian", "account_code": "Insurance"},
                {"pattern": "tower", "property": "9 damian", "account_code": "insurance"},
            ]
        )
        assert len(rules_env["store"]["memorized_rules"]["Test Entity"]) == 1

    def test_registers_new_codes_sorted_and_skips_uncategorized(self, rules_env):
        axiom_rules.replace_memorized_rules(
            [
                {"pattern": "A", "property": "9 Damian", "account_code": "Zeta"},
                {"pattern": "B", "property": "9 Damian", "account_code": "Alpha"},
                {"pattern": "C", "property": "9 Damian", "account_code": "Uncategorized"},
            ]
        )
        codes = rules_env["store"]["entity_account_codes"]["Test Entity"]
        assert codes == ["Alpha", "Insurance", "Loan Interest", "Rates", "Zeta"]


class TestPatternMatchesRowText:
    @pytest.mark.parametrize(
        ("pattern", "row", "expected"),
        [
            ("", "tower", (False, 0)),
            ("tower", "", (False, 0)),
            ("tower", "tower", (True, 1005)),
            ("tower", "payment tower insurance", (True, 505)),
            ("tower + damian", "tower insurance damian way", (True, 411)),
            ("tower | damian | 8821", "tower damian 8821", (True, 415)),
            ("tower + damian", "tower insurance only", (False, 0)),
            ("6851tower insurance", "tower insurance payment", (True, 335)),
            ("1234abc", "abc payment", (False, 0)),
        ],
    )
    def test_scoring_tiers(self, pattern, row, expected):
        assert axiom_rules._pattern_matches_row_text(pattern, row) == expected

    def test_case_insensitive(self):
        assert axiom_rules._pattern_matches_row_text("TOWER", "tower") == (True, 1005)


class TestReExports:
    @pytest.mark.parametrize(
        "name",
        [
            "get_memorized_rules",
            "save_memorized_rule",
            "delete_memorized_rule",
            "replace_memorized_rules",
            "_pattern_matches_row_text",
            "match_memorized_or_rule",
        ],
    )
    def test_utils_reexports_same_object(self, name):
        assert getattr(utils, name) is getattr(axiom_rules, name)
