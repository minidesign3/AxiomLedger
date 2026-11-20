"""Tests for PM-statement row coding behaviour (synthetic descriptions only)."""

from __future__ import annotations

import pandas as pd

import axiom_core
import axiom_rules
import axiom_traffic
from axiom_core import STANDARD_COLUMNS
from axiom_pm_rows import detect_pm_double_count, is_pm_row, pm_match_text, pm_rule_pattern

UNASSIGNED = axiom_core.UNASSIGNED_PROPERTY
EXPENSE = "PM: Water Services | Water Co 02 Sep 2025 Tenancy Portion: $40.00 | Ref W-9 | PM Txn 1000004"
ANNUAL = "PM Folio Summary: Council Rates | GST incl $26.09 | Annual total"


def test_pm_row_detection_and_match_text():
    assert is_pm_row(EXPENSE) and is_pm_row(ANNUAL)
    assert is_pm_row("PM Year End Summary: Rent | 2025 Sep")
    assert not is_pm_row("PMT Sample Bank | Transfer")
    assert not is_pm_row("Tower Insurance | 9 Elm")
    assert not is_pm_row(None) and not is_pm_row(float("nan"))
    assert pm_match_text(EXPENSE) == "PM: Water Services"
    assert pm_match_text(ANNUAL) == "PM Folio Summary: Council Rates"
    assert pm_match_text("Tower Insurance | 9 Elm") == "Tower Insurance | 9 Elm"
    assert pm_rule_pattern(EXPENSE) == ("PM: Water Services", True)
    assert pm_rule_pattern("Tower") == ("Tower", False)


def test_bank_rule_and_keyword_do_not_fire_on_pm_detail(rules_env, monkeypatch):
    # A short bank-learned rule and a keyword that both appear in the PM detail text
    rules = [{"pattern": "Tenancy", "property": "9 Elm", "account_code": "Rent Income"}]
    monkeypatch.setattr(axiom_rules, "get_memorized_rules", lambda entity=None: list(rules))
    rules_env["entity_rules"]["portion"] = "Rent Income"
    assert axiom_rules.match_memorized_or_rule(EXPENSE)[1] == "Rent Income"  # full line misfires
    assert axiom_rules.match_memorized_or_rule(pm_match_text(EXPENSE)) == (UNASSIGNED, "Uncategorized", "Unknown")


def test_confirming_pm_row_learns_one_category_rule(rules_env):
    axiom_rules.save_memorized_rule(EXPENSE, "9 Elm", "Watercare")
    rules = rules_env["store"]["memorized_rules"]["Test Entity"]
    assert rules == [
        {"pattern": "PM: Water Services", "property": UNASSIGNED, "account_code": "Watercare", "source": "User Coded"}
    ]
    # A different month / property / year of the same PM category is coded by that rule
    other = "PM: Water Services | Water Co 01 Oct 2026 | Ref W-9 | PM Txn 2000001"
    assert axiom_rules.match_memorized_or_rule(pm_match_text(other))[1] == "Watercare"
    # Re-confirming the category with a new code updates the single rule
    axiom_rules.save_memorized_rule(other, "2 Oak", "Water Rates")
    rules = rules_env["store"]["memorized_rules"]["Test Entity"]
    assert len(rules) == 1 and rules[0]["account_code"] == "Water Rates"


def test_traffic_light_pm_rows_need_category_rule(monkeypatch):
    calls: list[str] = []

    def fake_match(text, entity=None):
        calls.append(text)
        return (UNASSIGNED, "Uncategorized", "Unknown")

    monkeypatch.setattr(axiom_traffic, "match_memorized_or_rule", fake_match)
    monkeypatch.setattr(axiom_traffic, "get_active_entity", lambda entity=None: "E")
    monkeypatch.setattr(axiom_traffic, "is_property_unassigned", lambda p, c=None: p == UNASSIGNED)
    tier, badge = axiom_traffic.evaluate_row_traffic_light("9 Elm", "Watercare", EXPENSE)
    assert (tier, badge) == ("yellow", axiom_traffic.PM_UNCONFIRMED_BADGE)
    assert calls == ["PM: Water Services"]
    # Non-PM rows keep the existing "pre-coded" green behaviour
    assert axiom_traffic.evaluate_row_traffic_light("9 Elm", "Insurance", "Tower | 9 Elm")[0] == "green"

    df = pd.DataFrame(
        [["2025-01-01", -1.0, EXPENSE, "9 Elm", "Watercare"], ["2025-01-01", -1.0, "Tower", "9 Elm", "Insurance"]],
        columns=STANDARD_COLUMNS,
    )
    tiers, badges = axiom_traffic.compute_traffic_light_columns(df)
    assert tiers == ["yellow", "green"] and badges[0] == axiom_traffic.PM_UNCONFIRMED_BADGE

    monkeypatch.setattr(axiom_traffic, "match_memorized_or_rule", lambda t, entity=None: (UNASSIGNED, "Watercare", "Memorized Partial"))
    assert axiom_traffic.evaluate_row_traffic_light("9 Elm", "Watercare", EXPENSE)[0] == "green"


def _ledger(rows: list[tuple[float, str, str]]) -> pd.DataFrame:
    return pd.DataFrame([["2026-03-31", a, d, "9 Elm", c] for a, d, c in rows], columns=STANDARD_COLUMNS)


def test_double_count_warning():
    pm_rent = (1000.0, "PM Folio Summary: Rental Income Received | Annual total", "")
    bank_rent = (850.0, "Sample PM Ltd | Owner payout", "Rent Income Sample")
    expense = (-50.0, "PM: Council Rates | Sample Council", "Rates")
    warning = detect_pm_double_count(_ledger([pm_rent, bank_rent, expense]))
    assert warning and "$1,000.00 gross rent" in warning and "1 bank row(s)" in warning and "$850.00" in warning
    # Either side alone is fine
    assert detect_pm_double_count(_ledger([pm_rent, expense])) is None
    assert detect_pm_double_count(_ledger([bank_rent])) is None
    # Bank payouts recoded as a transfer clear the warning
    assert detect_pm_double_count(_ledger([pm_rent, (850.0, "Sample PM Ltd", "Funds Transfer")])) is None
    # PM non-rent income (tenant reimbursements) doesn't count as rent
    reimb = (40.0, "PM Year End Summary: Tenant Reimbursement | 2025 Sep", "")
    assert detect_pm_double_count(_ledger([reimb, bank_rent])) is None
    assert detect_pm_double_count(pd.DataFrame()) is None
