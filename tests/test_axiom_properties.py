"""
Tests for ``axiom_properties.py``: Property-list CRUD, in-place renaming with
rule/ledger/SQLite migration, and Per-Property vs Portfolio-Wide code scoping.

Every test runs through the ``entities_env`` fixture in ``conftest.py``: a
temporary SQLite DB + vault root and an in-memory memory store, so no real user
data, ``data/`` directory or ``.axiom_memory.json`` is ever touched.
"""

from __future__ import annotations

import pandas as pd
import pytest

import axiom_core
import axiom_db
import axiom_properties
import utils

DEFAULT_PROPS = list(axiom_core.DEFAULT_PROPERTIES)


class TestGetEntityProperties:
    def test_seeds_defaults_for_unknown_entity(self, entities_env):
        assert axiom_properties.get_entity_properties("Alpha") == DEFAULT_PROPS
        assert entities_env["store"]["entity_properties"]["Alpha"] == DEFAULT_PROPS
        assert len(entities_env["saved"]) == 1

    def test_returns_copy_not_store_reference(self, entities_env):
        props = axiom_properties.get_entity_properties("Alpha")
        props.append("mutated")
        assert axiom_properties.get_entity_properties("Alpha") == DEFAULT_PROPS

    def test_uses_existing_list_without_saving(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["Only One"]
        assert axiom_properties.get_entity_properties("Alpha") == ["Only One"]
        assert entities_env["saved"] == []

    def test_replaces_empty_list_with_defaults(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = []
        assert axiom_properties.get_entity_properties("Alpha") == DEFAULT_PROPS
        assert entities_env["store"]["entity_properties"]["Alpha"] == DEFAULT_PROPS

    def test_repairs_non_dict_props_map(self, entities_env):
        entities_env["store"]["entity_properties"] = "garbage"
        assert axiom_properties.get_entity_properties("Alpha") == DEFAULT_PROPS
        assert isinstance(entities_env["store"]["entity_properties"], dict)

    def test_defaults_to_active_entity(self, entities_env):
        assert axiom_properties.get_entity_properties() == DEFAULT_PROPS
        assert "Alpha" in entities_env["store"]["entity_properties"]


class TestAddEntityProperty:
    def test_rejects_blank_name(self, entities_env):
        ok, msg = axiom_properties.add_entity_property("   ", "Alpha")
        assert not ok
        assert "empty" in msg.lower()
        assert entities_env["saved"] == []

    def test_appends_and_saves(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["Property 1"]
        ok, msg = axiom_properties.add_entity_property("99 New Rd", "Alpha")
        assert ok
        assert "99 New Rd" in msg
        assert entities_env["store"]["entity_properties"]["Alpha"] == ["Property 1", "99 New Rd"]
        assert len(entities_env["saved"]) == 1

    def test_rejects_case_insensitive_duplicate(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["Property 1"]
        ok, msg = axiom_properties.add_entity_property("property 1", "Alpha")
        assert not ok
        assert "already exists" in msg
        assert entities_env["saved"] == []

    def test_unknown_entity_starts_from_defaults(self, entities_env):
        ok, _ = axiom_properties.add_entity_property("New", "Alpha")
        assert ok
        assert entities_env["store"]["entity_properties"]["Alpha"] == [*DEFAULT_PROPS, "New"]

    def test_trims_whitespace(self, entities_env):
        ok, _ = axiom_properties.add_entity_property("  Fluffy  ", "Alpha")
        assert ok
        assert "Fluffy" in entities_env["store"]["entity_properties"]["Alpha"]


class TestRemoveEntityProperty:
    def test_rejects_unknown_property(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["A", "B"]
        ok, msg = axiom_properties.remove_entity_property("C", "Alpha")
        assert not ok
        assert "not found" in msg
        assert entities_env["saved"] == []

    def test_keeps_at_least_one_property(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["Only One"]
        ok, msg = axiom_properties.remove_entity_property("Only One", "Alpha")
        assert not ok
        assert "at least one" in msg.lower()
        assert entities_env["saved"] == []

    def test_removes_and_saves(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["A", "B"]
        ok, msg = axiom_properties.remove_entity_property("B", "Alpha")
        assert ok
        assert "Removed" in msg
        assert entities_env["store"]["entity_properties"]["Alpha"] == ["A"]

    def test_trims_whitespace(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["A", "B"]
        ok, _ = axiom_properties.remove_entity_property("  B  ", "Alpha")
        assert ok
        assert entities_env["store"]["entity_properties"]["Alpha"] == ["A"]


def _seed_sqlite_rows(entity: str, prop: str) -> None:
    file_id = f"{entity}-f1"
    loan_id = f"{entity}-l1"
    axiom_db._ensure_sqlite_schema()
    with axiom_db._get_sqlite_conn() as conn:
        conn.execute(
            "INSERT INTO uploaded_files "
            "(file_id, entity, tax_year, signature, name, bucket, uploaded_at, vault_path) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (file_id, entity, "2026", "sig", "s.csv", "Bank Statements", "now", f"/vault/{entity}/2026"),
        )
        conn.execute(
            "INSERT INTO file_transactions (file_id, row_index, property) VALUES (?,?,?)",
            (file_id, 0, prop),
        )
        conn.execute(
            "INSERT INTO master_ledgers (entity, tax_year, row_index, property) VALUES (?,?,?,?)",
            (entity, "2026", 0, prop),
        )
        conn.execute(
            "INSERT INTO property_loans (loan_id, entity, property_name) VALUES (?,?,?)",
            (loan_id, entity, prop),
        )
        conn.execute(
            "INSERT INTO property_loan_rate_history (loan_id, entity, property_name) VALUES (?,?,?)",
            (loan_id, entity, prop),
        )
        conn.execute(
            "INSERT INTO loan_reminder_log "
            "(loan_id, entity, property_name, milestone_type, expiry_date, recipient_email, sent_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (loan_id, entity, prop, "fixed", "2026-01-01", "ops@example.com", "now"),
        )


def _sqlite_props(entity: str) -> dict[str, list[str]]:
    with axiom_db._get_sqlite_conn() as conn:
        return {
            "file_transactions": [
                r["property"]
                for r in conn.execute(
                    "SELECT t.property FROM file_transactions t JOIN uploaded_files u "
                    "ON u.file_id = t.file_id WHERE u.entity = ?",
                    (entity,),
                )
            ],
            "master_ledgers": [
                r["property"]
                for r in conn.execute("SELECT property FROM master_ledgers WHERE entity = ?", (entity,))
            ],
            "property_loans": [
                r["property_name"]
                for r in conn.execute(
                    "SELECT property_name FROM property_loans WHERE entity = ?", (entity,)
                )
            ],
            "rate_history": [
                r["property_name"]
                for r in conn.execute(
                    "SELECT property_name FROM property_loan_rate_history WHERE entity = ?", (entity,)
                )
            ],
            "reminder_log": [
                r["property_name"]
                for r in conn.execute(
                    "SELECT property_name FROM loan_reminder_log WHERE entity = ?", (entity,)
                )
            ],
        }


class TestRenameEntityProperty:
    def test_rejects_blank_new_name(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["Old"]
        ok, msg = axiom_properties.rename_entity_property("Old", "  ", "Alpha")
        assert not ok
        assert "empty" in msg.lower()

    def test_rejects_unknown_source(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["Old"]
        ok, msg = axiom_properties.rename_entity_property("Missing", "New", "Alpha")
        assert not ok
        assert "not found" in msg

    def test_same_name_is_unchanged(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["Old"]
        ok, msg = axiom_properties.rename_entity_property("Old", "Old", "Alpha")
        assert ok and "unchanged" in msg
        assert entities_env["saved"] == []

    def test_rejects_case_insensitive_collision(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["Old", "Taken"]
        ok, msg = axiom_properties.rename_entity_property("Old", "taken", "Alpha")
        assert not ok
        assert "already exists" in msg

    def test_renames_property_and_memorized_rules(self, entities_env):
        store = entities_env["store"]
        store["entity_properties"]["Alpha"] = ["Old", "Other"]
        store["memorized_rules"]["Alpha"] = [
            {"pattern": "x", "property": "old", "account_code": "Rates"},
            {"pattern": "y", "property": "Other", "account_code": "Rates"},
        ]
        ok, msg = axiom_properties.rename_entity_property("Old", "New", "Alpha")
        assert ok and "Renamed" in msg
        assert store["entity_properties"]["Alpha"] == ["New", "Other"]
        assert [r["property"] for r in store["memorized_rules"]["Alpha"]] == ["New", "Other"]

    def test_migrates_session_state_frames(self, entities_env):
        import streamlit as st

        entities_env["store"]["entity_properties"]["Alpha"] = ["Old"]
        file_df = pd.DataFrame({"Property": ["Old", "Other"], "Amount": [1, 2]})
        ledger_df = pd.DataFrame({"Property": ["Old"], "Amount": [3]})
        st.session_state["entity_uploaded_files"] = {"Alpha": {"2026": [{"df": file_df}]}}
        st.session_state["entity_year_ledgers"] = {"Alpha": {"2026": ledger_df}}
        st.session_state["entity_ledgers"] = {"Alpha": ledger_df}

        assert axiom_properties.rename_entity_property("Old", "New", "Alpha")[0]
        assert list(file_df["Property"]) == ["New", "Other"]
        assert list(ledger_df["Property"]) == ["New"]

    def test_migrates_sqlite_rows(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["Old", "Other"]
        _seed_sqlite_rows("Alpha", "Old")
        _seed_sqlite_rows("Beta", "Old")

        assert axiom_properties.rename_entity_property("Old", "New", "Alpha")[0]

        props = _sqlite_props("Alpha")
        assert props["file_transactions"] == ["New"]
        assert props["master_ledgers"] == ["New"]
        assert props["property_loans"] == ["New"]
        assert props["rate_history"] == ["New"]
        assert props["reminder_log"] == ["New"]
        # only the renamed entity's rows change
        assert _sqlite_props("Beta")["master_ledgers"] == ["Old"]


class TestIsPortfolioWideCode:
    def test_nan_is_not_portfolio(self, entities_env):
        assert axiom_properties.is_portfolio_wide_code(float("nan"), "Alpha") is False

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank_is_not_portfolio(self, entities_env, value):
        assert axiom_properties.is_portfolio_wide_code(value, "Alpha") is False

    def test_falls_back_to_default_category_set(self, entities_env):
        assert axiom_properties.is_portfolio_wide_code("  Accountant ", "Alpha") is True
        assert axiom_properties.is_portfolio_wide_code("Rates", "Alpha") is False

    def test_explicit_scope_overrides_default(self, entities_env):
        entities_env["store"]["entity_code_scopes"]["Alpha"] = {
            "rates": "portfolio",
            "accountant": "property",
        }
        assert axiom_properties.is_portfolio_wide_code("Rates", "Alpha") is True
        assert axiom_properties.is_portfolio_wide_code("Accountant", "Alpha") is False

    def test_ignores_non_dict_scopes_map(self, entities_env):
        entities_env["store"]["entity_code_scopes"] = "garbage"
        assert axiom_properties.is_portfolio_wide_code("Accountant", "Alpha") is True
        assert axiom_properties.is_portfolio_wide_code("Rates", "Alpha") is False


class TestSetEntityCodeScope:
    def test_rejects_blank_code(self, entities_env):
        ok, msg = axiom_properties.set_entity_code_scope("  ", True, "Alpha")
        assert not ok
        assert "empty" in msg.lower()

    @pytest.mark.parametrize(
        ("flag", "stored"),
        [(True, "portfolio"), (False, "property")],
    )
    def test_stores_scope(self, entities_env, flag, stored):
        ok, msg = axiom_properties.set_entity_code_scope("Rates", flag, "Alpha")
        assert ok
        assert entities_env["store"]["entity_code_scopes"]["Alpha"] == {"Rates": stored}
        assert len(entities_env["saved"]) == 1
        assert ("Portfolio-Wide" if flag else "Per-Property") in msg

    def test_repairs_non_dict_scopes_map(self, entities_env):
        entities_env["store"]["entity_code_scopes"] = "garbage"
        assert axiom_properties.set_entity_code_scope("Rates", False, "Alpha")[0]
        assert entities_env["store"]["entity_code_scopes"]["Alpha"] == {"Rates": "property"}


def test_utils_reexports_same_objects():
    for name in (
        "get_entity_properties",
        "add_entity_property",
        "remove_entity_property",
        "rename_entity_property",
        "is_portfolio_wide_code",
        "set_entity_code_scope",
    ):
        assert getattr(utils, name) is getattr(axiom_properties, name)
    assert "accountant" in utils.PORTFOLIO_WIDE_CATEGORIES
