"""Tests for axiom_ui_components (D17): pure HTML snippets + thin Streamlit widgets."""

from __future__ import annotations

import pytest

import axiom_ui_components as comp


@pytest.mark.parametrize(
    ("bank", "label"),
    [
        ("Westpac", "Westpac NZ"),
        ("BNZ", "BNZ"),
        ("Bank of New Zealand", "BNZ"),
        ("ANZ Bank", "ANZ NZ"),
        ("ASB", "ASB"),
        ("Kiwibank", "Kiwibank"),
        ("TSB", "TSB"),
        ("SBS Bank", "SBS Bank"),
        ("Heartland", "Heartland Bank"),
        ("Co-operative Bank", "Co-operative Bank"),
    ],
)
def test_bank_logo_known_banks(bank, label):
    svg = comp._nz_bank_logo_svg(bank)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert f'aria-label="{label}"' in svg


@pytest.mark.parametrize("bank", ["", None, "  ", "Bank not set", "Unknown Credit Union"])
def test_bank_logo_unknown_or_unset_is_empty(bank):
    assert comp._nz_bank_logo_svg(bank) == ""


def test_connection_badge_states():
    on = comp._render_connection_badge("GEMINI", {"connected": True, "label": "OK"}, True)
    assert "GEMINI [ACTIVE]" in on and "● OK" in on and "#A0A5B1" in on
    off = comp._render_connection_badge("LOCAL AI", {}, False)
    assert "LOCAL AI</span>" in off and "○ UNVERIFIED" in off and "[ACTIVE]" not in off


def _loan(**over):
    base = {
        "property_name": "14 Queen St",
        "loan_label": "Loan A",
        "interest_rate_pct": 6.25,
        "interest_type": "Fixed",
        "repayment_type": "Interest only",
        "bank_name": "ASB",
        "account_number": "12-3456-7890123-00",
        "current_balance": 500000,
        "fixed_rate_review_date": "2099-01-31",
        "email_reminder_enabled": True,
    }
    base.update(over)
    return base


def test_loan_card_html_contents_and_escaping():
    html = comp._render_loan_card_html(_loan(property_name="<b>X & Y</b>"))
    assert "&lt;b&gt;X &amp; Y&lt;/b&gt;" in html
    assert "6.25%" in html
    assert "Balance $500,000" in html
    assert 'aria-label="ASB"' in html
    assert "2mth email" in html


def test_loan_card_falls_back_to_limit_and_hides_repayment_when_zero():
    html = comp._render_loan_card_html(
        _loan(current_balance=0, loan_limit=250000, bank_name="", email_reminder_enabled=False)
    )
    assert "Balance $250,000" in html
    assert "Next debit" not in html
    assert "✉ email OFF" in html


def test_loan_card_next_debit_estimated_tag():
    html = comp._render_loan_card_html(
        _loan(regular_repayment_amount=1234.5, next_payment_is_estimated=True)
    )
    assert "Next debit: $1,234.50 (est.) (Monthly)" in html


def test_global_status_indicator_renders_summary(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        comp, "get_background_status_summary", lambda: {"overall": "Running", "headline": "2 up"}
    )
    monkeypatch.setattr(comp.st, "markdown", lambda body, **kw: calls.append(body))
    comp.render_global_status_indicator()
    assert "▪ RUNNING" in calls[0] and "2 up" in calls[0]


def test_global_status_indicator_unknown_state_defaults_online(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        comp, "get_background_status_summary", lambda: {"overall": "???", "headline": "h"}
    )
    monkeypatch.setattr(comp.st, "markdown", lambda body, **kw: calls.append(body))
    comp.render_global_status_indicator()
    assert "▪ ONLINE" in calls[0]


def test_reexported_from_utils():
    import utils

    for name in (
        "render_property_editor_widget",
        "_nz_bank_logo_svg",
        "_render_loan_card_html",
        "render_global_status_indicator",
        "_render_connection_badge",
        "_render_entity_edit_popover",
    ):
        assert getattr(utils, name) is getattr(comp, name)
