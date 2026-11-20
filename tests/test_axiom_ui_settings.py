"""Tests for the System & Organization Settings drawer split (D21).

The drawer is driven with ``ui_recorder.FakeSt`` and deterministic fakes; no
Streamlit runtime or real configuration/database is touched.
"""

from __future__ import annotations

import pytest
from ui_recorder import FakeSt, run_render

import axiom_ui_settings as settings
import axiom_ui_settings_accounts as accounts
import axiom_ui_settings_ai as ai
import axiom_ui_settings_email as email

ENTITY = "BT Property Holdings Ltd"
YEAR = "2025"
CAT_MODELS = ["Gemini 3.8 Flash (Cloud)", "Local Qwen 2.5 (Air-Gapped)", "Rules Engine Only"]


def _status(ok: bool, detail: str = "ok") -> dict:
    return {"connected": ok, "label": "CONNECTED" if ok else "DISCONNECTED", "detail": detail}


BASE_SESSION = {
    "show_settings_drawer": True,
    "categorization_model": CAT_MODELS[0],
    "username": "owner",
    "gemini_connection_status": _status(True, "Gemini ready"),
    "local_ai_connection_status": _status(False, "Ollama offline"),
    "google_genai_api_key": "key-123",
    "local_llm_url": "http://localhost:11434/v1",
    "local_llm_model": "qwen2.5:9b",
}


def _event(name, result, calls):
    def _call(*args, **kwargs):
        calls[name] = (args, kwargs)
        return result

    return _call


def patch_settings(monkeypatch, fake, calls=None, *, gem_ok=True, loc_ok=True):
    calls = calls if calls is not None else {}
    deps = {
        "st": fake,
        "get_active_entity": lambda: ENTITY,
        "get_active_tax_year": lambda: YEAR,
        "get_entity_options": lambda: [ENTITY, "Marine Trust"],
        "get_entity_jurisdiction": lambda e: "US" if e == "Marine Trust" else "NZ",
        "get_entity_ledger": lambda e, y: [{"id": 1}, {"id": 2}],
        "get_entity_uploaded_files": lambda e, y: [{"id": "f1", "name": "bank.csv"}],
        "rename_entity": _event("rename", (True, "renamed"), calls),
        "add_new_entity": _event("add_entity", (True, "added"), calls),
        "_save_memory_store": lambda: calls.setdefault("save_memory", True),
        "_render_entity_edit_popover": lambda *a, **k: calls.__setitem__("popover", (a, k)),
        "render_property_editor_widget": lambda *a, **k: calls.__setitem__("editor", (a, k)),
        "render_property_loans_block": lambda *a, **k: calls.setdefault("loans_calls", []).append((a, k)),
        "render_item_property_setup_matrix": lambda *a, **k: calls.__setitem__("matrix", (a, k)),
        "_render_bulk_upload_dropzone": lambda *a, **k: calls.__setitem__("dropzone", (a, k)),
        "render_entity_ingestion_pipeline": lambda *a, **k: calls.__setitem__("pipeline", (a, k)),
        "CATEGORIZATION_MODEL_OPTIONS": CAT_MODELS,
        "DEFAULT_LOCAL_LLM_URL": "http://localhost:11434/v1",
        "DEFAULT_LOCAL_LLM_MODEL": "qwen2.5:9b",
        "verify_gemini_connection": lambda key: _status(gem_ok, f"gemini {key}"),
        "verify_local_llm_connection": lambda url: _status(loc_ok, f"local {url}"),
        "_fmt_loan_date": lambda d: f"fmt:{d}",
        "is_loan_target_entity": lambda entity=None: (entity or ENTITY) == ENTITY,
        "get_loan_target_entity": lambda: ENTITY,
        "_smtp_recipients": lambda cfg: ["ops@example.com"],
        "get_smtp_config": lambda: {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "encryption": "STARTTLS",
            "username": "ops",
            "password": "secret",
            "sender_email": "ops@example.com",
            "sender_name": "Axiom Ledger",
            "recipient_emails": "ops@example.com",
            "default_reminder_months": 2,
            "auto_send_on_startup": True,
        },
        "save_smtp_config": _event("save_smtp", (True, "saved"), calls),
        "send_email_via_smtp": _event("send_email", (True, "sent"), calls),
        "check_and_send_due_loan_reminders": _event("check_due", (2, ["a", "b"]), calls),
        "get_due_loan_reminders": lambda: [
            {
                "loan": {"property_name": "12 Queen St", "loan_label": "Main"},
                "label": "Fixed rate expiry",
                "expiry_date": "2026-03-01",
                "days_remaining": 30,
            }
        ],
        "get_loan_reminder_log": lambda limit=10: [
            {
                "sent_at": "2025-01-01 12:00",
                "property_name": "12 Queen St",
                "milestone_type": "fixed_expiry",
                "expiry_date": "2026-03-01",
                "recipient_email": "ops@example.com",
                "status": "sent",
                "trigger_source": "manual_settings",
            }
        ],
        "get_all_user_accounts": lambda: {
            "owner": {"name": "Owner", "email": "owner@example.com", "role": "Administrator"},
            "accountant": {"name": "Accountant", "email": "acc@example.com", "role": "Accountant"},
        },
        "add_operator_account": _event("add_account", (True, "added"), calls),
        "delete_operator_account": _event("delete_account", (True, "removed"), calls),
        "update_operator_credentials": _event("update_credentials", (True, "updated"), calls),
    }
    for module in (settings, ai, email, accounts):
        for name, value in deps.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
        monkeypatch.setattr(module, "st", fake)
    return calls


def texts(log, kind):
    return [entry[1][0] for entry in log if entry[0] == kind and entry[1] and isinstance(entry[1][0], str)]


def test_drawer_closed_renders_nothing():
    fake = FakeSt(session={})
    log = run_render(settings.render_right_settings_drawer, fake)
    assert log == []


def test_drawer_open_renders_tabs_and_delegates(monkeypatch):
    fake = FakeSt(session=dict(BASE_SESSION))
    calls = patch_settings(monkeypatch, fake)
    log = run_render(settings.render_right_settings_drawer, fake)

    tabs = [entry for entry in log if entry[0] == "tabs"]
    assert len(tabs) == 1
    assert tabs[0][1] == [
        "Organizations & properties",
        "AI & connections",
        "Accounts & security",
        "Email & reminders",
    ]

    assert "editor" in calls
    # Loans, upload board and item setup have their own pages; Settings no longer embeds them.
    assert not {"loans_calls", "dropzone", "pipeline", "matrix"} & set(calls)
    assert any("SYSTEM CONFIGURATION" in text for text in texts(log, "markdown"))


def test_email_tab_links_to_loans_page(monkeypatch):
    from axiom_nav import LOANS_PAGE

    fake = FakeSt(session=dict(BASE_SESSION), clicks={"_btn_goto_loans"})
    patch_settings(monkeypatch, fake)
    log = run_render(settings.render_right_settings_drawer, fake)

    assert ("switch_page", [LOANS_PAGE], {}) in log
    loans_btn = next(e for e in log if e[0] == "button" and e[2].get("key") == "_btn_goto_loans")
    assert loans_btn[2]["disabled"] is False


def test_email_tab_disables_loans_link_for_other_entities(monkeypatch):
    fake = FakeSt(session=dict(BASE_SESSION))
    patch_settings(monkeypatch, fake)
    monkeypatch.setattr(email, "is_loan_target_entity", lambda entity=None: False)
    log = run_render(settings.render_right_settings_drawer, fake)

    loans_btn = next(e for e in log if e[0] == "button" and e[2].get("key") == "_btn_goto_loans")
    assert loans_btn[2]["disabled"] is True
    assert ENTITY in loans_btn[2]["help"]


def test_drawer_surfaces_all_flash_messages(monkeypatch):
    session = {
        **BASE_SESSION,
        "_entity_edit_flash": (True, "Entity renamed."),
        "_smtp_flash": (False, "SMTP failed."),
        "_settings_account_flash": (True, "Account updated."),
        "_gemini_key_flash": (False, "Gemini key rejected."),
    }
    fake = FakeSt(session=session)
    patch_settings(monkeypatch, fake)
    log = run_render(settings.render_right_settings_drawer, fake)

    assert "Entity renamed." in texts(log, "success")
    assert "SMTP failed." in texts(log, "error")
    assert "Account updated." in texts(log, "success")
    assert "Gemini key rejected." in texts(log, "error")


def test_org_save_calls_rename_and_reruns(monkeypatch):
    slug = ENTITY.replace(" ", "_")
    fake = FakeSt(
        session=dict(BASE_SESSION),
        values={
            f"_settings_inline_org_name_0_{slug}": "BT Property Holdings Ltd 2",
            f"_settings_inline_org_jur_0_{slug}": "USA IRS",
        },
        clicks={f"_settings_inline_org_save_0_{slug}"},
    )
    calls = patch_settings(monkeypatch, fake)
    log = run_render(settings.render_right_settings_drawer, fake)

    assert calls["rename"][0] == (ENTITY, "BT Property Holdings Ltd 2", "US")
    assert log[-1][0] == "RERUN_ABORT"


def test_org_switch_updates_active_entity(monkeypatch):
    fake = FakeSt(session=dict(BASE_SESSION), clicks={"_settings_switch_ent_1_Marine Trust"})
    calls = patch_settings(monkeypatch, fake)
    run_render(settings.render_right_settings_drawer, fake)

    assert fake.session_state["active_entity"] == "Marine Trust"
    assert fake.session_state["entity"] == "Marine Trust"
    assert calls["save_memory"] is True


@pytest.mark.parametrize("ok", [True, False])
def test_ai_verify_sets_flash(monkeypatch, ok):
    fake = FakeSt(session=dict(BASE_SESSION), clicks={"_btn_verify_gemini"})
    patch_settings(monkeypatch, fake, gem_ok=ok)
    run_render(settings.render_right_settings_drawer, fake)

    flashed_ok, message = fake.session_state["_gemini_key_flash"]
    assert flashed_ok is ok
    assert "Saved & Connected" in message if ok else "test failed" in message


def test_email_test_and_due_buttons_set_flash(monkeypatch):
    fake = FakeSt(session=dict(BASE_SESSION), clicks={"_btn_smtp_test"})
    patch_settings(monkeypatch, fake)
    run_render(settings.render_right_settings_drawer, fake)
    assert fake.session_state["_smtp_flash"] == (True, "sent")

    fake = FakeSt(session=dict(BASE_SESSION), clicks={"_btn_smtp_check_due"})
    patch_settings(monkeypatch, fake)
    run_render(settings.render_right_settings_drawer, fake)
    ok, message = fake.session_state["_smtp_flash"]
    assert ok is True
    assert message == "Sent 2 reminder(s). a | b"


def test_accounts_remove_deletes_selected_operator(monkeypatch):
    fake = FakeSt(session=dict(BASE_SESSION), clicks={"_btn_remove_operator_account"})
    calls = patch_settings(monkeypatch, fake)
    run_render(settings.render_right_settings_drawer, fake)

    assert calls["delete_account"][0] == ("accountant",)
    assert fake.session_state["_settings_account_flash"] == (True, "removed")


def test_settings_reexported_from_utils():
    import utils

    assert utils.render_right_settings_drawer is settings.render_right_settings_drawer
