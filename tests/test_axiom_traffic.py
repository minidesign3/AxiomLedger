"""Traffic-light engine tests (phase D9).

Ported from the original ``test_traffic_light.py`` (batch/row-by-row
equivalence, matcher memoization) and extended to cover the single-row
graders. The session/DB-backed matcher and entity resolver are stubbed, so no
real data, SQLite store or network is touched.
"""

from __future__ import annotations

import pandas as pd
import pytest

import axiom_traffic
from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY


@pytest.fixture()
def patched_matcher(monkeypatch):
    """Deterministic matcher keyed on description; records call count for memoization checks."""
    calls = {"n": 0}

    def fake_match(description, entity=None):
        calls["n"] += 1
        d = str(description).strip().lower()
        if "rent" in d:
            return ("x", "y", "Memorized")
        if "insurance" in d:
            return ("x", "y", "Keyword Rule")
        if "loan" in d:
            return ("x", "y", "Loan Match")
        if "partial" in d:
            return ("x", "y", "Memorized Partial")
        return ("x", "y", "Unknown")

    monkeypatch.setattr(axiom_traffic, "match_memorized_or_rule", fake_match)
    monkeypatch.setattr(axiom_traffic, "get_active_entity", lambda e=None: e or "E")
    monkeypatch.setattr(
        axiom_traffic, "is_property_unassigned", lambda prop, code=None: prop == UNASSIGNED_PROPERTY
    )
    return calls


def _df(rows):
    return pd.DataFrame(rows, columns=STANDARD_COLUMNS)


# --------------------------------------------------------------------------- #
# evaluate_row_traffic_light
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("prop", "code", "desc", "tier", "badge_fragment"),
    [
        (UNASSIGNED_PROPERTY, "Uncategorized", "Mystery", "red", "Unsure"),
        (UNASSIGNED_PROPERTY, "", "Mystery", "red", "Unsure"),
        ("9 Damian", "Uncategorized", "Groceries", "yellow", "House Found"),
        (UNASSIGNED_PROPERTY, "Office Expense", "Coffee", "yellow", "Item Found"),
        ("9 Damian", "Rent Income", "Rent received", "green", "Matched Rule"),
        ("9 Damian", "Insurance", "Tower insurance", "yellow", "Keyword Match"),
        ("9 Damian", "Loan Interest", "Loan repayment", "green", "Matched Rule"),
        ("9 Damian", "Repairs", "Partial thing", "green", "Rule + House"),
        ("9 Damian", "Repairs", "Unknown payee", "green", "100% Coded"),
        ("9 Damian", "Repairs", "", "green", "100% Coded"),
        ("9 Damian", "Repairs", None, "green", "100% Coded"),
    ],
)
def test_evaluate_row_tiers(patched_matcher, prop, code, desc, tier, badge_fragment):
    got_tier, badge = axiom_traffic.evaluate_row_traffic_light(prop, code, description=desc, entity="E")
    assert got_tier == tier
    assert badge_fragment in badge


def test_get_row_status_badge_matches_evaluate(patched_matcher):
    badge = axiom_traffic.get_row_status_badge("9 Damian", "Rent Income", "Rent received", entity="E")
    assert badge == axiom_traffic.evaluate_row_traffic_light("9 Damian", "Rent Income", "Rent received", "E")[1]


@pytest.mark.parametrize(
    ("prop", "code", "desc", "expected"),
    [
        (UNASSIGNED_PROPERTY, "Uncategorized", "x", True),
        (UNASSIGNED_PROPERTY, "Office Expense", "x", True),
        ("9 Damian", "Uncategorized", "x", True),
        ("9 Damian", "Insurance", "Tower insurance", True),  # keyword guess is not green
        ("9 Damian", "Rent Income", "Rent received", False),
        ("9 Damian", "Repairs", "", False),
    ],
)
def test_is_row_red_or_unsure(patched_matcher, prop, code, desc, expected):
    assert axiom_traffic.is_row_red_or_unsure(prop, code, desc, entity="E") is expected


# --------------------------------------------------------------------------- #
# compute_traffic_light_columns (batch)
# --------------------------------------------------------------------------- #
def test_matches_row_by_row_output(patched_matcher):
    df = _df(
        [
            ["2026-01-01", 100.0, "Rent received", "303 Weymouth", "Rental Income"],
            ["2026-01-02", -50.0, "Tower insurance", "33 Blampied", "Insurance"],
            ["2026-01-03", -10.0, "Mystery", UNASSIGNED_PROPERTY, "Uncategorized"],
            ["2026-01-04", -10.0, "Coffee", UNASSIGNED_PROPERTY, "Office Expense"],
            ["2026-01-05", -10.0, "Groceries", "9 Damian", "Uncategorized"],
            ["2026-01-06", -10.0, "Loan repayment", "9 Damian", "Loan Interest"],
            ["2026-01-07", -10.0, "Partial thing", "9 Damian", "Repairs"],
            ["2026-01-08", -10.0, "Unknown payee", "9 Damian", "Repairs"],
        ]
    )

    tiers, badges = axiom_traffic.compute_traffic_light_columns(df, entity="E")

    exp_tiers, exp_badges = [], []
    for _, r in df.iterrows():
        t, b = axiom_traffic.evaluate_row_traffic_light(
            property_val=r["Property"],
            account_code=r["Account Code"],
            description=r["Description"],
            entity="E",
        )
        exp_tiers.append(t)
        exp_badges.append(b)

    assert tiers == exp_tiers
    assert badges == exp_badges


def test_matcher_memoized_per_unique_description(patched_matcher):
    df = _df(
        [
            ["2026-01-01", 1.0, "Rent received", "A", "Rental Income"],
            ["2026-01-02", 1.0, "Rent received", "B", "Rental Income"],
            ["2026-01-03", 1.0, "Rent received", "C", "Rental Income"],
            ["2026-01-04", 1.0, "Loan repayment", "D", "Loan Interest"],
        ]
    )
    axiom_traffic.compute_traffic_light_columns(df, entity="E")
    assert patched_matcher["n"] == 2


def test_cheap_rows_skip_matcher(patched_matcher):
    df = _df(
        [
            ["2026-01-01", 1.0, "Rent received", UNASSIGNED_PROPERTY, "Uncategorized"],
            ["2026-01-02", 1.0, "Rent received", "A", "Uncategorized"],
        ]
    )
    axiom_traffic.compute_traffic_light_columns(df, entity="E")
    assert patched_matcher["n"] == 0


def test_empty_and_non_frame_inputs_return_empty_lists(patched_matcher):
    assert axiom_traffic.compute_traffic_light_columns(pd.DataFrame(columns=STANDARD_COLUMNS)) == ([], [])
    assert axiom_traffic.compute_traffic_light_columns(None) == ([], [])


def test_batch_tolerates_missing_columns(patched_matcher, monkeypatch):
    monkeypatch.setattr(
        axiom_traffic, "is_property_unassigned", lambda prop, code=None: not str(prop or "").strip()
    )
    tiers, badges = axiom_traffic.compute_traffic_light_columns(pd.DataFrame({"Amount": [1.0]}), entity="E")
    assert tiers == ["red"]
    assert "Unsure" in badges[0]
