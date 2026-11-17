"""
Tests for the property-loan SQLite CRUD + reminder orchestration in
``axiom_loan_crud.py``.

These exercise the full `utils` <-> `axiom_loan_crud` circular-import fix
(`import utils` + lazy attribute access) against an isolated **temporary**
SQLite file + JSON memory-store fallback — never `data/axiom_ledger.db` or
`.axiom_memory.json` (AGENTS.md). No SMTP host is ever configured in these
tests, so `send_email_via_smtp` always fails fast on its own "host not
configured" guard — no real network call is ever made.
"""

from __future__ import annotations

import threading
from datetime import date, timedelta

import pytest

import axiom_db
import axiom_loan_crud as alc
import axiom_memory
import utils


@pytest.fixture()
def temp_loan_db(tmp_path, monkeypatch):
    """Isolate the SQLite store (axiom_db globals) and the memory-store JSON fallback (utils.MEMORY_STORE_PATH)."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(axiom_db, "DATA_DIR", data_dir)
    monkeypatch.setattr(axiom_db, "SQLITE_DB_PATH", data_dir / "axiom_ledger.db")
    monkeypatch.setattr(axiom_db, "VAULT_ROOT_DIR", data_dir / "vault")
    monkeypatch.setattr(axiom_db, "_SCHEMA_READY", threading.Event())
    monkeypatch.setattr(axiom_db, "_CONN_CACHE", {})
    monkeypatch.setattr(utils, "MEMORY_STORE_PATH", tmp_path / ".axiom_memory.json")
    monkeypatch.setattr(axiom_memory, "MEMORY_STORE_PATH", tmp_path / ".axiom_memory.json")
    utils._load_memory_store_from_storage.clear()
    yield
    utils._load_memory_store_from_storage.clear()
    utils.st.session_state.pop("_loan_reminder_checked", None)


class TestLoanTargetEntity:
    def test_resolves_to_default_from_entity_options(self, temp_loan_db):
        assert alc.get_loan_target_entity() == "BT Property Holdings Ltd"

    def test_is_loan_target_entity(self, temp_loan_db):
        assert alc.is_loan_target_entity("BT Property Holdings Ltd") is True
        assert alc.is_loan_target_entity("KK Trustees Ltd") is False


class TestSeedingAndReads:
    def test_seeds_two_known_loans(self, temp_loan_db):
        loans = alc.get_property_loans()
        names = sorted(l["property_name"] for l in loans)
        assert names == ["303 Weymouth", "33 Blampied"]

    def test_filter_by_property_name(self, temp_loan_db):
        loans = alc.get_property_loans(property_name="303 Weymouth")
        assert len(loans) == 1
        assert loans[0]["loan_id"] == "btph-303-weymouth-main"

    def test_wrong_entity_returns_empty(self, temp_loan_db):
        assert alc.get_property_loans(entity="KK Trustees Ltd") == []

    def test_seeding_is_idempotent(self, temp_loan_db):
        alc.ensure_seeded_property_loans()
        alc.ensure_seeded_property_loans()
        loans = alc.get_property_loans()
        assert len(loans) == 2  # no duplicates on repeated seeding

    def test_get_property_loan_by_id(self, temp_loan_db):
        alc.ensure_seeded_property_loans()  # get_property_loan_by_id does not auto-seed
        loan = alc.get_property_loan_by_id("btph-33-blampied-main")
        assert loan is not None
        assert loan["bank_name"] == "Westpac"
        assert alc.get_property_loan_by_id("does-not-exist") is None


class TestSaveAndDeleteLoan:
    def test_create_new_loan_round_trips(self, temp_loan_db):
        ok, msg, loan_id = alc.save_property_loan(
            {"property_name": "9 Damian", "loan_label": "Test Facility", "interest_rate_pct": "4.25%"}
        )
        assert ok
        assert loan_id
        loan = alc.get_property_loan_by_id(loan_id)
        assert loan["property_name"] == "9 Damian"
        assert loan["interest_rate_pct"] == 4.25

    def test_missing_property_name_fails(self, temp_loan_db):
        ok, msg, loan_id = alc.save_property_loan({"loan_label": "No Property"})
        assert not ok
        assert loan_id == ""

    def test_plain_save_does_not_log_history_by_default(self, temp_loan_db):
        # log_rate_history defaults to False, and a brand-new loan has no
        # "existing" record to diff against, so rate_changed is also False.
        ok, _msg, loan_id = alc.save_property_loan(
            {"property_name": "14 Raleigh", "interest_rate_pct": 5.0, "fixed_rate_review_date": "2027-01-01"}
        )
        assert ok
        assert alc.get_loan_rate_history(loan_id) == []

    def test_explicit_log_rate_history_flag_logs_on_first_save(self, temp_loan_db):
        ok, _msg, loan_id = alc.save_property_loan(
            {"property_name": "Explicit Log Prop", "interest_rate_pct": 5.0},
            log_rate_history=True,
        )
        assert ok
        assert len(alc.get_loan_rate_history(loan_id)) == 1

    def test_changing_rate_on_existing_loan_auto_logs_history(self, temp_loan_db):
        ok, _msg, loan_id = alc.save_property_loan(
            {"property_name": "14 Raleigh", "interest_rate_pct": 5.0, "fixed_rate_review_date": "2027-01-01"}
        )
        assert ok
        assert alc.get_loan_rate_history(loan_id) == []  # confirmed by the test above

        alc.save_property_loan(
            {"loan_id": loan_id, "property_name": "14 Raleigh", "interest_rate_pct": 5.75, "fixed_rate_review_date": "2027-01-01"}
        )
        hist_after = alc.get_loan_rate_history(loan_id)
        assert len(hist_after) == 1  # rate change auto-detected against the existing record and logged

    def test_delete_loan_removes_record_and_history(self, temp_loan_db):
        ok, _msg, loan_id = alc.save_property_loan({"property_name": "Temp Prop"}, log_rate_history=True)
        assert ok
        assert alc.get_property_loan_by_id(loan_id) is not None
        alc.delete_property_loan(loan_id)
        assert alc.get_property_loan_by_id(loan_id) is None
        assert alc.get_loan_rate_history(loan_id) == []

    def test_create_property_loan_uses_defaults(self, temp_loan_db):
        ok, _msg, loan_id = alc.create_property_loan("New Street")
        assert ok
        loan = alc.get_property_loan_by_id(loan_id)
        assert loan["interest_account_code"] == "Loan Interest"
        assert loan["use_for_coding"] is True
        assert loan["reminder_months_before"] == 2


class TestMatchLoanFromText:
    def test_matches_known_account_number(self, temp_loan_db):
        # 33 Blampied is seeded with account_number "03-0399-0618477-092".
        match = alc.match_loan_from_text("WESTPAC AUTOPAY 0618477-092 INTEREST")
        assert match is not None
        assert match["property_name"] == "33 Blampied"

    def test_no_match_returns_none(self, temp_loan_db):
        assert alc.match_loan_from_text("completely unrelated grocery purchase") is None

    def test_blank_text_returns_none(self, temp_loan_db):
        assert alc.match_loan_from_text("") is None
        assert alc.match_loan_from_text(None) is None


class TestDueRemindersAndLogging:
    def test_due_reminder_detected_for_near_term_expiry(self, temp_loan_db):
        near = (date.today() + timedelta(days=10)).isoformat()
        ok, _msg, loan_id = alc.save_property_loan(
            {
                "property_name": "Due Soon Prop",
                "fixed_rate_review_date": near,
                "reminder_months_before": 1,
                "email_reminder_enabled": True,
                "remind_on_fixed_expiry": True,
            }
        )
        assert ok
        due = alc.get_due_loan_reminders()
        assert any(d["loan"]["loan_id"] == loan_id for d in due)

    def test_has_reminder_been_sent_reflects_log(self, temp_loan_db):
        ok, _msg, loan_id = alc.save_property_loan(
            {"property_name": "Log Test Prop", "fixed_rate_review_date": "2026-12-25"}
        )
        assert ok
        assert alc.has_reminder_been_sent(loan_id, "fixed_rate_expiry", date(2026, 12, 25)) is False
        alc.send_loan_reminder_email(loan_id, "fixed_rate_expiry")
        log = alc.get_loan_reminder_log(limit=5)
        assert any(r["loan_id"] == loan_id for r in log)

    def test_send_reminder_without_smtp_host_fails_without_network(self, temp_loan_db):
        ok, _msg, loan_id = alc.save_property_loan(
            {"property_name": "No SMTP Prop", "fixed_rate_review_date": "2026-12-25"}
        )
        ok_send, msg = alc.send_loan_reminder_email(loan_id)
        assert not ok_send
        assert "SMTP host" in msg

    def test_check_and_send_due_loan_reminders_runs_without_network(self, temp_loan_db):
        near = (date.today() + timedelta(days=5)).isoformat()
        alc.save_property_loan(
            {
                "property_name": "Batch Due Prop",
                "fixed_rate_review_date": near,
                "reminder_months_before": 1,
                "email_reminder_enabled": True,
            }
        )
        sent, msgs = alc.check_and_send_due_loan_reminders()
        assert sent == 0  # no SMTP configured -> every attempt fails gracefully
        assert len(msgs) >= 1


class TestRunOncePerSession:
    def test_does_not_raise_and_sets_flag(self, temp_loan_db):
        utils.st.session_state.pop("_loan_reminder_checked", None)
        alc.run_loan_reminder_check_once_per_session()
        assert utils.st.session_state.get("_loan_reminder_checked") is True
        # Second call must be a safe no-op.
        alc.run_loan_reminder_check_once_per_session()
