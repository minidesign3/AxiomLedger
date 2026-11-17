"""
Tests for the pure loan domain calculations in ``axiom_loans.py``.

Every function here is a deterministic transformation of a plain `dict` — no
SQLite, no Streamlit, no network. These tests import only ``axiom_loans`` (plus
stdlib), never ``utils``, keeping them fast and free of any real-data risk.
"""

from __future__ import annotations

from datetime import date, timedelta

import axiom_loans as al


class TestLoanRowToDict:
    def test_bools_floats_and_strings_normalized(self):
        row = {
            "use_for_coding": 1,
            "email_reminder_enabled": 0,
            "remind_on_fixed_expiry": 1,
            "remind_on_io_expiry": 1,
            "remind_on_term_expiry": 0,
            "next_payment_is_estimated": 1,
            "original_drawdown_amount": "-313000.00",
            "loan_limit": None,
            "current_balance": 250000,
            "available_redraw": "0",
            "interest_rate_pct": 5.49,
            "interest_accrued": None,
            "regular_repayment_amount": "1234.56",
            "account_holder": None,
            "account_product_type": None,
            "original_term_text": None,
            "remaining_term_text": None,
            "screenshot_vault_path": None,
            "reminder_months_before": "3",
        }
        d = al._loan_row_to_dict(row)
        assert d["use_for_coding"] is True
        assert d["email_reminder_enabled"] is False
        assert d["original_drawdown_amount"] == 313000.00  # abs() applied
        assert d["loan_limit"] == 0.0
        assert d["account_holder"] == ""
        assert d["reminder_months_before"] == 3

    def test_bad_reminder_months_defaults_to_2(self):
        row = {"reminder_months_before": "not-a-number"}
        d = al._loan_row_to_dict(row)
        assert d["reminder_months_before"] == 2


class TestGetLoanTriggerTokens:
    def test_four_part_nz_account_generates_customer_suffix_tokens(self):
        loan = {"account_number": "01-1234-0618477-092"}
        toks = al.get_loan_trigger_tokens(loan)
        assert "01-1234-0618477-092" in toks
        assert "0618477-092" in toks or "618477-092" in toks

    def test_two_part_account_generates_base_token(self):
        loan = {"account_number": "86131912-1003"}
        toks = al.get_loan_trigger_tokens(loan)
        assert any("86131912" in t for t in toks)

    def test_shared_customer_base_is_excluded(self):
        loan = {"account_number": "01-1234-0618477-092"}
        toks_unshared = al.get_loan_trigger_tokens(loan, shared_customer_bases=set())
        toks_shared = al.get_loan_trigger_tokens(loan, shared_customer_bases={"0618477"})
        assert "0618477" in toks_unshared
        assert "0618477" not in toks_shared

    def test_keywords_included_and_short_tokens_dropped(self):
        loan = {"account_number": "", "coding_trigger_keywords": "Insurance, ab, Tower"}
        toks = al.get_loan_trigger_tokens(loan)
        assert "insurance" in toks
        assert "tower" in toks
        assert "ab" not in toks  # shorter than 4 chars

    def test_tokens_sorted_longest_first(self):
        loan = {"coding_trigger_keywords": "abcd,abcdefgh,abcdef"}
        toks = al.get_loan_trigger_tokens(loan)
        assert toks == sorted(toks, key=len, reverse=True)


class TestLoanMilestones:
    def test_only_parseable_enabled_dates_produce_milestones(self):
        loan = {
            "fixed_rate_review_date": "2026-12-01",
            "remind_on_fixed_expiry": True,
            "interest_only_end_date": None,
            "remind_on_io_expiry": True,
            "loan_term_end_date": "garbage",
            "remind_on_term_expiry": True,
            "reminder_months_before": 2,
        }
        ms = al.loan_milestones(loan)
        assert len(ms) == 1
        assert ms[0]["milestone_type"] == "fixed_rate_expiry"
        assert ms[0]["expiry_date"] == date(2026, 12, 1)
        assert ms[0]["window_start"] == date(2026, 10, 1)

    def test_days_remaining_matches_today_delta(self):
        target = date.today() + timedelta(days=10)
        loan = {
            "fixed_rate_review_date": target.isoformat(),
            "remind_on_fixed_expiry": True,
            "reminder_months_before": 1,
        }
        ms = al.loan_milestones(loan)
        assert ms[0]["days_remaining"] == 10

    def test_no_dates_returns_empty_list(self):
        assert al.loan_milestones({}) == []


class TestLoanRateStatusTuple:
    def test_no_expiry_set(self):
        colour, status, days = al._loan_rate_status_tuple({})
        assert colour == "#8E939F"
        assert status == "No expiry set"
        assert days is None

    def test_expired(self):
        past = (date.today() - timedelta(days=5)).isoformat()
        colour, status, days = al._loan_rate_status_tuple({"fixed_rate_review_date": past})
        assert colour == "#F87171"
        assert "Expired" in status
        assert days == -5

    def test_within_refix_window(self):
        soon = (date.today() + timedelta(days=30)).isoformat()
        colour, status, days = al._loan_rate_status_tuple({"fixed_rate_review_date": soon})
        assert colour == "#FBBF24"
        assert "Refix window" in status
        assert days == 30

    def test_within_warning_window(self):
        soon = (date.today() + timedelta(days=90)).isoformat()
        colour, status, days = al._loan_rate_status_tuple({"fixed_rate_review_date": soon})
        assert colour == "#FBBF24"
        assert days == 90

    def test_far_out_is_green(self):
        far = (date.today() + timedelta(days=200)).isoformat()
        colour, status, days = al._loan_rate_status_tuple({"fixed_rate_review_date": far})
        assert colour == "#34D399"
        assert "🟢" in status


class TestLoanDossierSummaryLabel:
    def test_no_loan_recorded(self):
        label = al._loan_dossier_summary_label(None, "303 Weymouth", 0)
        assert "no loan recorded" in label
        assert "303 Weymouth" in label

    def test_single_loan_summary_contains_key_fields(self):
        loan = {
            "bank_name": "ANZ",
            "account_number": "01-1234-0000001-00",
            "current_balance": 250000.0,
            "interest_rate_pct": 5.49,
            "interest_type": "Fixed",
            "repayment_type": "Interest Only",
            "fixed_rate_review_date": "2026-12-01",
        }
        label = al._loan_dossier_summary_label(loan, "303 Weymouth", 1)
        assert "303 Weymouth" in label
        assert "ANZ" in label
        assert "$250,000" in label
        assert "5.49%" in label
        assert "+0 more" not in label

    def test_multiple_loans_shows_extra_count(self):
        loan = {"interest_rate_pct": 4.0}
        label = al._loan_dossier_summary_label(loan, "9 Damian", 3)
        assert "+2 more" in label

    def test_bank_falls_back_to_account_inference(self):
        loan = {"account_number": "12-3456-0000001-00", "interest_rate_pct": 4.0}
        label = al._loan_dossier_summary_label(loan, "Prop", 1)
        assert "ASB" in label
