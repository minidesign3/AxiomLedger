"""Tests for one-format date normalisation (synthetic values)."""

from __future__ import annotations

from datetime import date

import pytest

from axiom_dates import (
    ISSUE_BLANK,
    ISSUE_OUTSIDE_FY,
    ISSUE_UNPARSED,
    ISSUE_YEAR_INFERRED,
    fy_bounds,
    normalize_date,
    parse_any_date,
)

NZ_FY = fy_bounds("2026", "NZ")


def test_fy_bounds():
    assert NZ_FY == (date(2025, 4, 1), date(2026, 3, 31))
    assert fy_bounds("2026", "US") == (date(2026, 1, 1), date(2026, 12, 31))


@pytest.mark.parametrize(
    "raw",
    ["05/09/25", "2025/09/05", "05/09/2025", "2025-09-05", "2025-09-05 00:00:00", "5 Sep 2025",
     "05 September 2025", "Fri 5 Sep 2025", "5-Sep-2025", "5.9.2025"],
)
def test_nz_formats_all_normalise_to_one(raw):
    assert normalize_date(raw, "NZ", NZ_FY) == ("05/09/2025", "")


def test_us_is_month_first_and_formats_mm_dd():
    assert parse_any_date("09/05/2025", "US")[0] == date(2025, 9, 5)
    assert normalize_date("2025-09-05", "US") == ("09/05/2025", "")


def test_no_year_is_inferred_inside_fy():
    assert normalize_date("Sat 9 Apr", "NZ", NZ_FY) == ("09/04/2025", ISSUE_YEAR_INFERRED)
    assert normalize_date("9 Mar", "NZ", NZ_FY) == ("09/03/2026", ISSUE_YEAR_INFERRED)


def test_blank_unparsed_and_outside_fy():
    assert normalize_date("", "NZ", NZ_FY) == ("", ISSUE_BLANK)
    assert normalize_date(None, "NZ", NZ_FY)[1] == ISSUE_BLANK
    assert normalize_date("nan", "NZ", NZ_FY)[1] == ISSUE_BLANK
    assert normalize_date("Mar", "NZ", NZ_FY) == ("Mar", ISSUE_UNPARSED)
    assert normalize_date("31/02/2026", "NZ", NZ_FY)[1] == ISSUE_UNPARSED
    assert normalize_date("2026-11-14", "NZ", NZ_FY) == ("14/11/2026", ISSUE_OUTSIDE_FY)


def test_date_objects_pass_through():
    assert normalize_date(date(2025, 12, 1), "NZ", NZ_FY) == ("01/12/2025", "")
