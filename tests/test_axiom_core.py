"""
Unit tests for the pure helpers in ``axiom_core``.

These tests intentionally import only ``axiom_core`` (never ``utils``), so they
carry no Streamlit, SQLite, filesystem, or network dependency and can never
touch real user/financial data.
"""

from datetime import date, datetime

import pytest

import axiom_core as ac


class TestInferNzBankFromAccount:
    def test_known_prefixes(self):
        assert ac.infer_nz_bank_from_account("01-3947-0000001-00") == "ANZ"
        assert ac.infer_nz_bank_from_account("02-1234-0000001-00") == "BNZ"
        assert ac.infer_nz_bank_from_account("03-1234-0000001-00") == "Westpac"
        assert ac.infer_nz_bank_from_account("12-1234-0000001-00") == "ASB"
        assert ac.infer_nz_bank_from_account("38-1234-0000001-00") == "Kiwibank"

    def test_prefix_15_is_tsb_not_westpac(self):
        # Regression: "15" was mapped twice (Westpac then TSB); the first was dead.
        assert ac.infer_nz_bank_from_account("15-3947-0000001-00") == "TSB"

    def test_unknown_or_malformed(self):
        assert ac.infer_nz_bank_from_account("99-1234-0000001-00") == ""
        assert ac.infer_nz_bank_from_account("not-an-account") == ""
        assert ac.infer_nz_bank_from_account("") == ""
        assert ac.infer_nz_bank_from_account(None) == ""


class TestClassifyUploadBucket:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("statement.csv", "csv"),
            ("scan.PDF", "pdf"),
            ("receipt.png", "screenshots"),
            ("receipt.JPG", "screenshots"),
            ("photo.jpeg", "screenshots"),
            ("img.webp", "screenshots"),
            ("book.xlsx", "other"),
            ("data.ods", "other"),
            ("nope.tsv", "other"),
            ("", "other"),
        ],
    )
    def test_buckets(self, name, expected):
        assert ac.classify_upload_bucket(name) == expected


class TestCleanLoanMoney:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("– $313,000.00", 313000.0),
            ("$1,155.09 (estimated)", 1155.09),
            (-250.5, 250.5),
            (1000, 1000.0),
            ("", 0.0),
            (None, 0.0),
            (True, 0.0),
            ("no digits here", 0.0),
        ],
    )
    def test_money(self, raw, expected):
        assert ac._clean_loan_money(raw) == pytest.approx(expected)


class TestCleanLoanRate:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("4.49% p.a.", 4.49),
            ("Fixed at 4.44%", 4.44),
            (5.1, 5.1),
            ("", 0.0),
            (None, 0.0),
            (False, 0.0),
        ],
    )
    def test_rate(self, raw, expected):
        assert ac._clean_loan_rate(raw) == pytest.approx(expected)


class TestSubtractMonths:
    def test_basic(self):
        assert ac._subtract_months(date(2026, 3, 15), 2) == date(2026, 1, 15)

    def test_year_rollover(self):
        assert ac._subtract_months(date(2026, 1, 10), 1) == date(2025, 12, 10)

    def test_end_of_month_clamp(self):
        # 31 May minus 3 months -> Feb has no 31st, clamp to 28.
        assert ac._subtract_months(date(2026, 5, 31), 3) == date(2026, 2, 28)

    def test_zero(self):
        assert ac._subtract_months(date(2026, 6, 1), 0) == date(2026, 6, 1)


class TestSafeVaultSegment:
    def test_sanitizes_illegal_chars(self):
        assert ac._safe_vault_segment('a/b:c*d?e"f') == "a_b_c_d_e_f"

    def test_blank_becomes_unnamed(self):
        assert ac._safe_vault_segment("") == "unnamed"
        assert ac._safe_vault_segment("   ") == "unnamed"

    def test_keeps_readable(self):
        assert ac._safe_vault_segment("BT Property Holdings Ltd") == "BT Property Holdings Ltd"


class TestCleanIrdCategoryResponse:
    def test_strips_think_block_and_punctuation(self):
        assert ac._clean_ird_category_response("<think>reasoning</think>Rates.") == "Rates"

    def test_takes_first_line(self):
        assert ac._clean_ird_category_response("Insurance\nextra commentary") == "Insurance"

    def test_empty_defaults_to_uncategorized(self):
        assert ac._clean_ird_category_response("") == "Uncategorized"
        assert ac._clean_ird_category_response("<think>only</think>") == "Uncategorized"


class TestIsUncategorized:
    @pytest.mark.parametrize("value", ["", "Uncategorized", "unassigned", "NONE", "nan", None])
    def test_uncategorized_values(self, value):
        assert ac.is_uncategorized(value) is True

    @pytest.mark.parametrize("value", ["Rates", "Insurance", "Rent Income"])
    def test_real_codes(self, value):
        assert ac.is_uncategorized(value) is False

    def test_marker_set_contents(self):
        assert "uncategorized" in ac.UNCATEGORIZED_MARKERS
        assert len(ac.UNCATEGORIZED_MARKERS) == 6


class TestParseLoanDate:
    def test_date_and_datetime_passthrough(self):
        assert ac._parse_loan_date(date(2026, 1, 5)) == date(2026, 1, 5)
        assert ac._parse_loan_date(datetime(2026, 1, 5, 9, 30)) == date(2026, 1, 5)

    @pytest.mark.parametrize(
        "raw",
        [
            "2026-01-05",
            "05 Jan 2026",
            "05 January 2026",
            "05/01/2026",
            "2026/01/05",
            "05-01-2026",
            "05 Jan 26",
        ],
    )
    def test_known_formats(self, raw):
        assert ac._parse_loan_date(raw) == date(2026, 1, 5)

    @pytest.mark.parametrize("raw", [None, "", "none", "NaT", "nan"])
    def test_blank_or_null_like(self, raw):
        assert ac._parse_loan_date(raw) is None

    def test_unparseable_falls_back_to_pandas_then_none(self):
        # pd.to_datetime can still parse some loose formats pandas understands.
        assert ac._parse_loan_date("not-a-date-at-all-xyz") is None


class TestFmtAndIsoLoanDate:
    def test_fmt_known_date(self):
        assert ac._fmt_loan_date("2026-01-05") == "05 Jan 2026"

    def test_fmt_unparseable_is_em_dash(self):
        assert ac._fmt_loan_date("garbage") == "—"
        assert ac._fmt_loan_date(None) == "—"

    def test_iso_known_date(self):
        assert ac._loan_date_iso("05 Jan 2026") == "2026-01-05"

    def test_iso_unparseable_is_empty_string(self):
        assert ac._loan_date_iso("garbage") == ""
        assert ac._loan_date_iso(None) == ""
