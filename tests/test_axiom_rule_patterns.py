from __future__ import annotations

import pytest

import axiom_rules
from axiom_rule_patterns import collapse_duplicate_rules, normalize_rule_pattern

BCA = "BCA Ltd Westp | A/P Quest Hotel Apt 1604 Body Corp | A/P"


class TestNormalizeRulePattern:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (f"{BCA} | 2025041101", BCA),
            (f"{BCA} | 2026031101", BCA),
            ("Rent 2025-04-11 | 33 Blampied", "Rent | 33 Blampied"),
            ("Watercare 11/04/2025", "Watercare"),
            ("6851Tower Insurance | 9 Damian", "6851Tower Insurance | 9 Damian"),
            ("Apt 1604 / 363 Queen", "Apt 1604 / 363 Queen"),
            ("12-3456-7890123-00", "12-3456-7890123-00"),
            ("2025041101", "2025041101"),
            ("  ", ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_rule_pattern(raw) == expected


class TestCollapseDuplicateRules:
    def test_merges_monthly_refs_keeping_first(self):
        rules = [
            {"pattern": f"{BCA} | 20250{m}1101", "property": "1604 / 363 Queen", "account_code": "Bodycorp"}
            for m in range(4, 10)
        ] + [{"pattern": "Tower", "property": "9 Damian", "account_code": "Insurance"}]
        collapsed, removed = collapse_duplicate_rules(rules)
        assert removed == 5
        assert [r["pattern"] for r in collapsed] == [BCA, "Tower"]

    def test_keeps_different_properties(self):
        rules = [
            {"pattern": "Tower | 2025041101", "property": "9 Damian", "account_code": "Insurance"},
            {"pattern": "Tower | 2025051101", "property": "33 Blampied", "account_code": "Insurance"},
        ]
        _, removed = collapse_duplicate_rules(rules)
        assert removed == 0


class TestSaveMemorizedRuleDedup:
    def test_monthly_lines_share_one_rule(self, rules_env):
        for month in ("04", "05", "06"):
            ok, _ = axiom_rules.save_memorized_rule(f"{BCA} | 2025{month}1101", "1604 / 363 Queen", "Bodycorp")
            assert ok
        rules = rules_env["store"]["memorized_rules"]["Test Entity"]
        assert [r["pattern"] for r in rules if r["property"] == "1604 / 363 Queen"] == [BCA]

    def test_skips_line_already_resolved_by_existing_rule(self, rules_env):
        axiom_rules.save_memorized_rule("Tower", "9 Damian", "Insurance")
        ok, msg = axiom_rules.save_memorized_rule("Tower Insurance Pol 88", "9 Damian", "Insurance")
        assert ok and "already covered" in msg.lower()
        assert len(rules_env["store"]["memorized_rules"]["Test Entity"]) == 1

    def test_still_saves_when_existing_rule_gives_different_code(self, rules_env):
        axiom_rules.save_memorized_rule("Tower", "9 Damian", "Insurance")
        axiom_rules.save_memorized_rule("Tower Rates", "9 Damian", "Rates")
        assert len(rules_env["store"]["memorized_rules"]["Test Entity"]) == 2

    def test_collapse_memorized_rules_persists(self, rules_env):
        rules_env["store"]["memorized_rules"]["Test Entity"] = [
            {"pattern": f"{BCA} | 2025041101", "property": "Q", "account_code": "Bodycorp"},
            {"pattern": f"{BCA} | 2025051101", "property": "Q", "account_code": "Bodycorp"},
        ]
        ok, msg = axiom_rules.collapse_memorized_rules()
        assert ok and "removed 1" in msg
        assert rules_env["store"]["memorized_rules"]["Test Entity"][0]["pattern"] == BCA
        assert len(rules_env["saved"]) == 1
