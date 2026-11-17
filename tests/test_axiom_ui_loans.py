"""Tests for the Property Loans block split (D19).

The render entry points are driven with ``ui_recorder.FakeSt`` and deterministic
data fakes; no real property / loan data or Streamlit runtime is involved.
"""

from __future__ import annotations

from ui_recorder import FakeSt, run_render

import axiom_ui_loans as ui
import axiom_ui_loans_dossiers as dossiers
import axiom_ui_loans_import as importer

MILESTONE = {
    "milestone_type": "fixed_expiry",
    "label": "Fixed rate expiry",
    "expiry_date": "2026-03-01",
    "days_remaining": 30,
    "window_start": "2026-01-01",
}

LOANS = [
    {
        "loan_id": 1,
        "property_name": "12 Queen St",
        "loan_label": "Main Facility",
        "bank_name": "ANZ",
        "interest_type": "Fixed",
        "interest_rate_pct": 6.25,
        "repayment_type": "Interest Only",
        "current_balance": 500000.0,
        "loan_limit": 600000.0,
        "original_drawdown_amount": 650000.0,
        "fixed_rate_review_date": "2026-03-01",
        "account_number": "02-0100-0000000-00",
        "email_reminder_enabled": True,
        "use_for_coding": True,
    },
    {
        "loan_id": 2,
        "property_name": "45 Marine Pde",
        "loan_label": "Revolving",
        "bank_name": "ASB",
        "interest_type": "Revolving Credit / Flexi",
        "interest_rate_pct": 0.0,
        "current_balance": 120000.0,
        "loan_limit": 200000.0,
        "email_reminder_enabled": False,
        "use_for_coding": False,
    },
]

OCR = {
    "_used_model": "Gemini Test",
    "_vault_path": "vault/test.png",
    "property_name": "12 Queen St",
    "loan_label": "Imported",
    "bank_name": "ANZ",
    "interest_type": "Fixed",
    "interest_rate_pct": 6.1,
}


def _names():
    return {
        "get_loan_target_entity": lambda: "BT Property Holdings Limited",
        "get_property_loans": lambda target: list(LOANS),
        "get_entity_properties": lambda target: ["12 Queen St", "45 Marine Pde", "General / Portfolio"],
        "get_smtp_config": lambda: {"default_reminder_months": 2},
        "get_due_loan_reminders": lambda: [],
        "loan_milestones": lambda loan: [dict(MILESTONE)] if loan["loan_id"] == 1 else [],
        "get_account_code_options": lambda target: ["Loan Interest", "Principal Loan"],
        "_loan_dossier_summary_label": lambda first, prop, count: f"{prop} · {count}",
        "_loan_rate_status_tuple": lambda loan: ("amber", "OK", 30),
        "_fmt_loan_date": lambda value: value or "—",
        "_parse_loan_date": lambda value: None,
        "infer_nz_bank_from_account": lambda acct: "ANZ",
        "has_reminder_been_sent": lambda lid, kind, when: False,
        "get_loan_rate_history": lambda lid: [],
        "_render_loan_card_html": lambda loan: f"<div>card {loan['loan_id']}</div>",
        "check_and_send_due_loan_reminders": lambda trigger_source: (0, []),
        "save_property_loan": lambda payload, **kw: (True, "ok", 1),
        "create_property_loan": lambda *a, **k: 3,
    }


def _fakes(monkeypatch, fake, loans=None, due=None, clicks=None, session=None):
    world = _names()
    if loans is not None:
        world["get_property_loans"] = lambda target: list(loans)
    if due is not None:
        world["get_due_loan_reminders"] = lambda: list(due)
    for module in (ui, dossiers, importer):
        monkeypatch.setattr(module, "st", fake)
        for name, fn in world.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, fn)
    return fake


def test_compact_block_renders_header_and_spotlight_only(monkeypatch):
    fake = FakeSt()
    _fakes(monkeypatch, fake)
    run_render(ui.render_property_loans_block, fake)
    names = [entry[0] for entry in fake.log]
    assert "columns" in names and "markdown" in names
    assert "dataframe" not in names
    assert "form" not in names
    assert ("markdown", ["<div>card 1</div>"], {"unsafe_allow_html": True}) in fake.log


def test_full_block_renders_overview_and_dossiers(monkeypatch):
    fake = FakeSt()
    _fakes(monkeypatch, fake)
    run_render(ui.render_property_loans_block, fake, show_overview_and_dossiers=True)
    names = [entry[0] for entry in fake.log]
    assert "dataframe" in names
    assert names.count("form") == 2  # one edit form per loan
    assert any(entry[0] == "button" and entry[2].get("key") == "loans_addloan_0_12 Queen St" for entry in fake.log)


def test_empty_portfolio_shows_no_dossier_buttons(monkeypatch):
    fake = FakeSt()
    _fakes(monkeypatch, fake, loans=[])
    run_render(ui.render_property_loans_block, fake, show_overview_and_dossiers=True)
    keys = [entry[2].get("key") for entry in fake.log if entry[0] == "button"]
    assert "loans_addloan_0_12 Queen St" in keys
    assert "loans_del_1" not in keys


def test_importer_renders_when_session_flag_is_set(monkeypatch):
    fake = FakeSt(
        session={"_loans_show_loan_import": True, "_loans_loan_ocr_result": OCR, "_loans_loan_import_loc": "top"}
    )
    _fakes(monkeypatch, fake)
    run_render(ui.render_property_loans_block, fake)
    names = [entry[0] for entry in fake.log]
    assert "file_uploader" in names
    assert any(entry[0] == "form" and entry[2].get("key") == "loans_ocr_review_form" for entry in fake.log)


def test_due_reminder_button_sends_and_reruns(monkeypatch):
    fake = FakeSt(clicks={"loans_send_all_due"})
    _fakes(monkeypatch, fake, due=[{"loan_id": 1}])
    run_render(ui.render_property_loans_block, fake)
    assert fake.log[-1] == ("RERUN_ABORT",)
    assert fake.session_state["_loan_flash"][0] is False


def test_add_loan_button_creates_and_expands(monkeypatch):
    fake = FakeSt(clicks={"loans_addloan_0_12 Queen St"}, session={})
    _fakes(monkeypatch, fake)
    created = {}
    for module in (ui, dossiers, importer):
        if hasattr(module, "create_property_loan"):
            monkeypatch.setattr(
                module, "create_property_loan", lambda *a, **k: (created.setdefault("ok", True), "created", 3)
            )
    run_render(ui.render_property_loans_block, fake, show_overview_and_dossiers=True)
    assert created == {"ok": True}
    assert fake.session_state["_loans_expanded_prop"] == "12 Queen St"
    assert fake.log[-1] == ("RERUN_ABORT",)


def test_build_context_keys_and_property_filtering(monkeypatch):
    fake = FakeSt()
    _fakes(monkeypatch, fake)
    ctx = ui.build_loan_block_context("bt")
    assert ctx.target == "BT Property Holdings Limited"
    assert ctx.props == ["12 Queen St", "45 Marine Pde"]
    assert ctx.show_import_key == "_bt_show_loan_import"
    assert ctx.ocr_result_key == "_bt_loan_ocr_result"
    assert ctx.expanded_prop_key == "_bt_expanded_prop"


def test_open_import_handlers_set_session_state(monkeypatch):
    fake = FakeSt(session={})
    monkeypatch.setattr(ui, "st", fake)
    monkeypatch.setattr(dossiers, "st", fake)
    ui._open_import_top("_show", "_loc")
    assert fake.session_state["_show"] is True
    assert fake.session_state["_loc"] == "top"
    dossiers._open_import_for_property("_show", "_tgt", "_loc", "_exp", "45 Marine Pde")
    assert fake.session_state["_tgt"] == "45 Marine Pde"
    assert fake.session_state["_exp"] == "45 Marine Pde"


def test_reexported_from_utils():
    import axiom_ui_loans_common as common
    import utils

    assert utils.render_property_loans_block is ui.render_property_loans_block
    assert utils.NZ_BANK_OPTIONS is common.NZ_BANK_OPTIONS
    assert utils.LOAN_FREQUENCY_OPTIONS is common.LOAN_FREQUENCY_OPTIONS
