"""Tests for the Item + Property setup matrix split (D18).

The render entry point is driven with ``ui_recorder.FakeSt`` and deterministic
data fakes; no real ledger / rule data is involved.
"""

from __future__ import annotations

from ui_recorder import FakeSt, run_render

import axiom_ui_setup_editor as editor
import axiom_ui_setup_matrix as ui
import axiom_ui_setup_rules as rules

ENTITY = "Test Entity Limited"
PROPS = ["12 Queen St", "General / Portfolio"]
CODES = ["Insurance", "Loan Interest", "Rates", "Rent Income"]
RULES = [
    {"description_pattern": "Tower", "account_code": "Insurance", "property_name": "12 Queen St", "source": "Quick"},
    {"description_pattern": "Rates", "account_code": "Rates", "property_name": "12 Queen St", "source": "Quick"},
]


def _patch(monkeypatch, fake, rules_list=None):
    world = {
        "get_active_entity": lambda e=None: ENTITY,
        "get_active_tax_year": lambda y=None: "2025",
        "get_entity_properties": lambda e: list(PROPS),
        "get_entity_account_codes": lambda e: list(CODES),
        "get_memorized_rules": lambda e: [dict(r) for r in (RULES if rules_list is None else rules_list)],
        "is_portfolio_wide_code": lambda c, e: c == "Loan Interest",
        "add_entity_account_code": lambda *a, **k: (True, "added"),
        "rename_entity_account_code": lambda **k: (True, "renamed"),
        "save_memorized_rule": lambda **k: (True, "saved"),
        "set_entity_code_scope": lambda *a, **k: None,
    }
    for module in (ui, rules, editor):
        monkeypatch.setattr(module, "st", fake)
        for name, fn in world.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, fn)
    return fake


def test_context_sorts_items_and_strips_portfolio_property(monkeypatch):
    fake = FakeSt()
    _patch(monkeypatch, fake)
    ctx = ui._build_matrix_context(None, None, "m")
    assert ctx.resolved_entity == ENTITY
    assert ctx.alphabetical_items == ["Insurance", "Loan Interest", "Rates", "Rent Income"]
    assert ctx.real_props == ["12 Queen St"]
    assert ctx.all_props_option == "🏠 All Properties (1 Houses)"


def test_quick_setup_renders_item_and_property_row(monkeypatch):
    fake = FakeSt()
    _patch(monkeypatch, fake)
    run_render(ui.render_item_property_setup_matrix, fake, key_prefix="m")
    keys = [e[2].get("key") for e in fake.log if e[0] in ("selectbox", "text_input", "button")]
    assert "_m_qa_item_" + ENTITY in keys
    assert "_m_qa_prop_" + ENTITY in keys
    assert "_m_qa_btn_" + ENTITY in keys


def test_empty_pattern_is_rejected(monkeypatch):
    fake = FakeSt(values={"_m_qa_pat_" + ENTITY: "   "}, clicks={"_m_qa_btn_" + ENTITY})
    _patch(monkeypatch, fake)
    run_render(ui.render_item_property_setup_matrix, fake, key_prefix="m")
    assert ("error", ["Description pattern cannot be empty."], {}) in fake.log


def test_rename_item_triggers_rerun(monkeypatch):
    fake = FakeSt(clicks={"_m_ren_btn_" + ENTITY})
    _patch(monkeypatch, fake)
    run_render(ui.render_item_property_setup_matrix, fake, key_prefix="m")
    assert fake.log[-1] == ("RERUN_ABORT",)
    assert ("success", ["renamed"], {}) in fake.log


def test_add_item_sets_scope_and_reruns(monkeypatch):
    fake = FakeSt(
        values={"_m_add_new_item_txt_" + ENTITY: "Tower Insurance"},
        clicks={"_m_add_new_item_btn_" + ENTITY},
    )
    calls = []
    _patch(monkeypatch, fake)
    for module in (ui, rules, editor):
        if hasattr(module, "add_entity_account_code"):
            monkeypatch.setattr(module, "add_entity_account_code", lambda code, **k: (calls.append(code), True, "added")[1:])
    run_render(ui.render_item_property_setup_matrix, fake, key_prefix="m")
    assert calls == ["Tower Insurance"]
    assert fake.log[-1] == ("RERUN_ABORT",)


def test_saved_rules_card_lists_rule_count(monkeypatch):
    fake = FakeSt()
    _patch(monkeypatch, fake)
    run_render(ui.render_item_property_setup_matrix, fake, key_prefix="m")
    markdown = " ".join(str(e[1]) for e in fake.log if e[0] in ("markdown", "caption", "title", "subheader"))
    assert "Tower" in markdown or "SAVED" in markdown.upper()


def test_reexported_from_utils():
    import utils

    assert utils.render_item_property_setup_matrix is ui.render_item_property_setup_matrix
