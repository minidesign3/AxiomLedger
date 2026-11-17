"""
Tests for the SMTP / email notification service (``axiom_email.py``).

``_smtp_recipients`` and ``build_loan_reminder_email`` are pure and tested
directly. ``get_smtp_config`` / ``save_smtp_config`` touch SQLite and are
exercised against a **temporary** DB (never `data/axiom_ledger.db`), following
the same fixture pattern as ``tests/test_db_roundtrip.py``.
``send_email_via_smtp`` is tested with ``smtplib.SMTP``/``SMTP_SSL`` monkeypatched
to a fake — **no real network connection is ever made**.
"""

from __future__ import annotations

import threading

import pytest

import axiom_db
import axiom_email as ae


@pytest.fixture()
def temp_smtp_db(tmp_path, monkeypatch):
    """Isolate the SQLite store axiom_email reads/writes kv_settings through."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(axiom_db, "DATA_DIR", data_dir)
    monkeypatch.setattr(axiom_db, "SQLITE_DB_PATH", data_dir / "axiom_ledger.db")
    monkeypatch.setattr(axiom_db, "VAULT_ROOT_DIR", data_dir / "vault")
    monkeypatch.setattr(axiom_db, "_SCHEMA_READY", threading.Event())
    monkeypatch.setattr(axiom_db, "_CONN_CACHE", {})
    yield


class TestSmtpRecipients:
    def test_splits_on_comma_semicolon_newline(self):
        cfg = {"recipient_emails": "a@x.com, b@x.com; c@x.com\nd@x.com"}
        assert ae._smtp_recipients(cfg) == ["a@x.com", "b@x.com", "c@x.com", "d@x.com"]

    def test_falls_back_to_sender_email(self):
        cfg = {"recipient_emails": "", "sender_email": "only@x.com"}
        assert ae._smtp_recipients(cfg) == ["only@x.com"]

    def test_filters_entries_without_at_sign(self):
        cfg = {"recipient_emails": "valid@x.com, not-an-email, also valid@y.com"}
        result = ae._smtp_recipients(cfg)
        assert "not-an-email" not in result
        assert "valid@x.com" in result


class TestSmtpConfigRoundTrip:
    def test_get_defaults_when_unset(self, temp_smtp_db):
        cfg = ae.get_smtp_config()
        assert cfg["smtp_host"] == ""
        assert cfg["smtp_port"] == 587
        assert cfg["sender_name"] == "Axiom Ledger"

    def test_save_then_get_round_trips(self, temp_smtp_db):
        ok, msg = ae.save_smtp_config(
            {
                "smtp_host": "smtp.example.com",
                "smtp_port": "465",
                "encryption": "SSL",
                "username": "user@example.com",
                "sender_email": "user@example.com",
                "recipient_emails": "ops@example.com",
            }
        )
        assert ok
        cfg = ae.get_smtp_config()
        assert cfg["smtp_host"] == "smtp.example.com"
        assert cfg["smtp_port"] == 465
        assert cfg["encryption"] == "SSL"
        assert cfg["recipient_emails"] == "ops@example.com"


class _FakeSmtpContext:
    """Minimal stand-in for smtplib.SMTP/SMTP_SSL used as a context manager."""

    instances: list[_FakeSmtpContext] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.logged_in = None
        self.ehlo_called = False
        self.starttls_called = False
        self.sent_message = None
        _FakeSmtpContext.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.ehlo_called = True

    def starttls(self, context=None):
        self.starttls_called = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, msg):
        self.sent_message = msg


class TestSendEmailViaSmtp:
    def setup_method(self):
        _FakeSmtpContext.instances.clear()

    def test_missing_host_fails_fast_without_network(self):
        ok, msg = ae.send_email_via_smtp("Subject", "Body", cfg={"smtp_host": ""})
        assert not ok
        assert "SMTP host" in msg

    def test_missing_sender_fails_fast(self):
        ok, msg = ae.send_email_via_smtp(
            "Subject", "Body", cfg={"smtp_host": "smtp.example.com", "sender_email": ""}
        )
        assert not ok
        assert "Sender email" in msg

    def test_missing_recipient_fails_fast(self):
        # sender_email is non-empty (so the "sender configured" check passes)
        # but contains no "@", so it's filtered out of the recipient fallback
        # too -> recips ends up empty and we return before any network call.
        ok, msg = ae.send_email_via_smtp(
            "Subject",
            "Body",
            cfg={"smtp_host": "smtp.example.com", "sender_email": "nodomain", "recipient_emails": ""},
        )
        assert not ok
        assert "recipient" in msg.lower()

    def test_starttls_path_uses_smtp_and_logs_in(self, monkeypatch):
        monkeypatch.setattr(ae.smtplib, "SMTP", _FakeSmtpContext)
        ok, msg = ae.send_email_via_smtp(
            "Hello",
            "World",
            cfg={
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "encryption": "STARTTLS",
                "username": "user",
                "password": "pw",
                "sender_email": "user@example.com",
                "recipient_emails": "to@example.com",
            },
        )
        assert ok
        assert "to@example.com" in msg
        sent = _FakeSmtpContext.instances[0]
        assert sent.starttls_called
        assert sent.logged_in == ("user", "pw")
        assert sent.sent_message["Subject"] == "Hello"

    def test_ssl_path_uses_smtp_ssl(self, monkeypatch):
        monkeypatch.setattr(ae.smtplib, "SMTP_SSL", _FakeSmtpContext)
        ok, _msg = ae.send_email_via_smtp(
            "Hi",
            "There",
            cfg={
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "encryption": "SSL",
                "sender_email": "user@example.com",
                "recipient_emails": "to@example.com",
            },
        )
        assert ok
        assert _FakeSmtpContext.instances[0].sent_message["Subject"] == "Hi"

    def test_smtp_exception_is_caught_and_reported(self, monkeypatch):
        class _Boom:
            def __init__(self, *a, **k):
                raise OSError("connection refused")

        monkeypatch.setattr(ae.smtplib, "SMTP", _Boom)
        ok, msg = ae.send_email_via_smtp(
            "Hi",
            "There",
            cfg={
                "smtp_host": "smtp.example.com",
                "sender_email": "user@example.com",
                "recipient_emails": "to@example.com",
            },
        )
        assert not ok
        assert "SMTP send failed" in msg


class TestBuildLoanReminderEmail:
    def test_subject_and_body_contain_key_fields(self):
        loan = {
            "property_name": "303 Weymouth",
            "loan_label": "Main Facility",
            "bank_name": "ANZ",
            "account_number": "01-1234-0000001-00",
            "current_balance": 250000.0,
            "loan_limit": 300000.0,
            "interest_rate_pct": 5.49,
            "interest_type": "Fixed",
            "repayment_type": "Interest Only",
            "repayment_frequency": "Monthly",
            "fixed_rate_review_date": "2026-03-01",
        }
        milestone = {
            "label": "Fixed Rate Review",
            "expiry_date": "2026-03-01",
            "days_remaining": 45,
            "window_start": "2026-01-15",
        }
        subject, body = ae.build_loan_reminder_email(loan, milestone)
        assert "303 Weymouth" in subject
        assert "Fixed Rate Review" in subject
        assert "01 Mar 2026" in subject
        assert "ANZ" in body
        assert "$250,000.00" in body
        assert "5.49%" in body
        assert "45 days from today" in body
