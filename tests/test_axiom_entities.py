"""
Tests for ``axiom_entities.py``: entity (organization) create/rename/delete
across session state, the File Vault and SQLite, plus financial-year creation.

Every test runs through the ``entities_env`` fixture in ``conftest.py``: a
temporary SQLite DB + vault root and an in-memory memory store, so no real user
data, ``data/`` directory or ``.axiom_memory.json`` is ever touched.
"""

from __future__ import annotations

import pandas as pd
import pytest

import axiom_core
import axiom_db
import axiom_entities
import utils

DEFAULT_PROPS = list(axiom_core.DEFAULT_PROPERTIES)


def _seed_entity_rows(entity: str) -> None:
    file_id = f"{entity}-f1"
    loan_id = f"{entity}-l1"
    axiom_db._ensure_sqlite_schema()
    with axiom_db._get_sqlite_conn() as conn:
        conn.execute(
            "INSERT INTO uploaded_files "
            "(file_id, entity, tax_year, signature, name, bucket, uploaded_at, vault_path) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (file_id, entity, "2026", "sig", "s.csv", "Bank Statements", "now", f"/vault/{entity}/2026/s.csv"),
        )
        conn.execute(
            "INSERT INTO file_transactions (file_id, row_index) VALUES (?,?)", (file_id, 0)
        )
        conn.execute(
            "INSERT INTO master_ledgers (entity, tax_year, row_index) VALUES (?,?,?)",
            (entity, "2026", 0),
        )
        conn.execute(
            "INSERT INTO property_loans (loan_id, entity, property_name) VALUES (?,?,?)",
            (loan_id, entity, "P"),
        )
        conn.execute(
            "INSERT INTO property_loan_rate_history (loan_id, entity, property_name) VALUES (?,?,?)",
            (loan_id, entity, "P"),
        )
        conn.execute(
            "INSERT INTO loan_reminder_log "
            "(loan_id, entity, property_name, milestone_type, expiry_date, recipient_email, sent_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (loan_id, entity, "P", "fixed", "2026-01-01", "ops@example.com", "now"),
        )


ENTITY_TABLES = (
    "uploaded_files",
    "master_ledgers",
    "property_loans",
    "property_loan_rate_history",
    "loan_reminder_log",
)


def _count(table: str, entity: str) -> int:
    with axiom_db._get_sqlite_conn() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE entity = ?", (entity,)).fetchone()
    return int(row["n"])


def _file_transaction_entities() -> list[str]:
    with axiom_db._get_sqlite_conn() as conn:
        return [
            r["entity"]
            for r in conn.execute(
                "SELECT u.entity FROM file_transactions t JOIN uploaded_files u "
                "ON u.file_id = t.file_id"
            )
        ]


class TestAddNewEntity:
    def test_rejects_blank_name(self, entities_env):
        ok, msg = axiom_entities.add_new_entity("   ")
        assert not ok
        assert "empty" in msg.lower()
        assert entities_env["saved"] == []

    def test_rejects_case_insensitive_duplicate(self, entities_env):
        ok, msg = axiom_entities.add_new_entity("alpha")
        assert not ok
        assert "already exists" in msg
        assert entities_env["saved"] == []

    def test_creates_and_activates_entity(self, entities_env):
        import streamlit as st

        ok, msg = axiom_entities.add_new_entity("Gamma", jurisdiction="us")
        assert ok
        assert "US" in msg
        assert st.session_state["entity_options"] == ["Alpha", "Beta", "Gamma"]
        assert st.session_state["entity_jurisdictions"]["Gamma"] == "US"
        assert st.session_state["active_entity"] == "Gamma"
        assert st.session_state["entity"] == "Gamma"

    def test_initialises_empty_ledgers_for_every_year(self, entities_env):
        import streamlit as st

        assert axiom_entities.add_new_entity("Gamma")[0]
        year_ledgers = st.session_state["entity_year_ledgers"]["Gamma"]
        assert set(year_ledgers) == set(axiom_core.TAX_YEAR_OPTIONS)
        assert all(df.empty for df in year_ledgers.values())
        assert st.session_state["entity_ledgers"]["Gamma"].empty

    def test_seeds_properties_and_rules_in_store(self, entities_env):
        assert axiom_entities.add_new_entity("Gamma")[0]
        store = entities_env["store"]
        assert store["entity_properties"]["Gamma"] == DEFAULT_PROPS
        assert store["memorized_rules"]["Gamma"] == []
        assert len(entities_env["saved"]) == 1

    def test_non_us_jurisdiction_defaults_to_nz(self, entities_env):
        import streamlit as st

        assert axiom_entities.add_new_entity("Gamma", jurisdiction="Australia")[0]
        assert st.session_state["entity_jurisdictions"]["Gamma"] == "NZ"


class TestRenameEntity:
    def test_rejects_unknown_entity(self, entities_env):
        ok, msg = axiom_entities.rename_entity("Missing", "Gamma")
        assert not ok
        assert "not found" in msg

    def test_rejects_blank_new_name(self, entities_env):
        ok, msg = axiom_entities.rename_entity("Alpha", "   ")
        assert not ok
        assert "empty" in msg.lower()

    def test_rejects_collision_with_other_entity(self, entities_env):
        ok, msg = axiom_entities.rename_entity("Alpha", "beta")
        assert not ok
        assert "already exists" in msg

    def test_same_name_only_updates_jurisdiction(self, entities_env):
        import streamlit as st

        ok, msg = axiom_entities.rename_entity("Alpha", "Alpha", "US")
        assert ok
        assert "updated" in msg
        assert st.session_state["entity_jurisdictions"]["Alpha"] == "US"
        assert entities_env["saved"] == [None]

    def test_renames_session_state_in_place(self, entities_env):
        import streamlit as st

        df = pd.DataFrame({"Amount": [1]})
        st.session_state["entity_year_ledgers"] = {"Alpha": {"2026": df}}
        st.session_state["entity_ledgers"] = {"Alpha": df}
        st.session_state["entity_uploaded_files"] = {"Alpha": {"2026": []}}
        st.session_state["entity_year_files"] = {"Alpha": {"2026": []}}

        ok, msg = axiom_entities.rename_entity("Alpha", "Gamma")
        assert ok and "Renamed" in msg
        assert st.session_state["entity_options"] == ["Gamma", "Beta"]
        assert "Alpha" not in st.session_state["entity_year_ledgers"]
        assert st.session_state["entity_year_ledgers"]["Gamma"]["2026"] is df
        assert st.session_state["entity_ledgers"]["Gamma"] is df
        assert st.session_state["entity_uploaded_files"]["Gamma"] == {"2026": []}
        assert st.session_state["entity_year_files"]["Gamma"] == {"2026": []}
        assert st.session_state["active_entity"] == "Gamma"
        assert st.session_state["entity"] == "Gamma"

    def test_inherits_jurisdiction_when_not_overridden(self, entities_env):
        import streamlit as st

        entities_env["store"]["entity_jurisdictions"] = {"Beta": "US"}
        assert axiom_entities.rename_entity("Beta", "Gamma")[0]
        assert st.session_state["entity_jurisdictions"]["Gamma"] == "US"

    def test_migrates_store_properties_and_rules(self, entities_env):
        entities_env["store"]["entity_properties"]["Alpha"] = ["Only"]
        entities_env["store"]["memorized_rules"]["Alpha"] = [{"pattern": "p"}]
        assert axiom_entities.rename_entity("Alpha", "Gamma")[0]
        store = entities_env["store"]
        assert store["entity_properties"] == {"Gamma": ["Only"]}
        assert store["memorized_rules"] == {"Gamma": [{"pattern": "p"}]}

    def test_seeds_store_defaults_when_entity_had_none(self, entities_env):
        assert axiom_entities.rename_entity("Alpha", "Gamma")[0]
        store = entities_env["store"]
        assert store["entity_properties"]["Gamma"] == DEFAULT_PROPS
        assert store["memorized_rules"]["Gamma"] == []

    def test_renames_vault_folder(self, entities_env):
        vault_root = entities_env["vault_root"]
        old_dir = vault_root / "Alpha" / "2026" / "Bank Statements"
        old_dir.mkdir(parents=True)
        (old_dir / "s.csv").write_bytes(b"x")

        assert axiom_entities.rename_entity("Alpha", "Gamma")[0]
        assert not (vault_root / "Alpha").exists()
        assert (vault_root / "Gamma" / "2026" / "Bank Statements" / "s.csv").read_bytes() == b"x"

    def test_migrates_sqlite_rows_and_vault_paths(self, entities_env):
        _seed_entity_rows("Alpha")
        _seed_entity_rows("Beta")

        assert axiom_entities.rename_entity("Alpha", "Gamma")[0]

        for table in ENTITY_TABLES:
            assert _count(table, "Alpha") == 0, table
            assert _count(table, "Gamma") == 1, table
        assert _count("master_ledgers", "Beta") == 1
        assert _file_transaction_entities() == ["Gamma", "Beta"]

        with axiom_db._get_sqlite_conn() as conn:
            vault_path = conn.execute(
                "SELECT vault_path FROM uploaded_files WHERE entity = 'Gamma'"
            ).fetchone()["vault_path"]
        assert vault_path == "/vault/Gamma/2026/s.csv"


class TestDeleteEntity:
    def test_rejects_unknown_entity(self, entities_env):
        ok, msg = axiom_entities.delete_entity("Missing")
        assert not ok
        assert "not found" in msg

    def test_refuses_to_delete_last_entity(self, entities_env):
        entities_env["store"]["entity_options"] = ["Alpha"]
        ok, msg = axiom_entities.delete_entity("Alpha")
        assert not ok
        assert "last remaining" in msg

    def test_removes_from_session_state_and_activates_first(self, entities_env):
        import streamlit as st

        st.session_state["active_entity"] = "Alpha"
        st.session_state["entity"] = "Alpha"
        st.session_state["entity_year_ledgers"] = {
            "Alpha": {"2026": pd.DataFrame()},
            "Beta": {"2026": pd.DataFrame()},
        }
        st.session_state["entity_ledgers"] = {"Alpha": pd.DataFrame(), "Beta": pd.DataFrame()}
        st.session_state["entity_uploaded_files"] = {"Alpha": {}, "Beta": {}}

        ok, msg = axiom_entities.delete_entity("Alpha")
        assert ok and "removed" in msg
        assert st.session_state["entity_options"] == ["Beta"]
        assert "Alpha" not in st.session_state["entity_year_ledgers"]
        assert "Alpha" not in st.session_state["entity_ledgers"]
        assert "Alpha" not in st.session_state["entity_uploaded_files"]
        assert st.session_state["active_entity"] == "Beta"
        assert st.session_state["entity"] == "Beta"

    def test_removes_store_maps(self, entities_env):
        store = entities_env["store"]
        store["entity_properties"]["Alpha"] = ["P"]
        store["memorized_rules"]["Alpha"] = [{"pattern": "p"}]
        store["entity_code_scopes"]["Alpha"] = {"Rates": "property"}
        store.setdefault("entity_property_aliases", {})["Alpha"] = {"P": ["p"]}
        store.setdefault("entity_account_codes", {})["Alpha"] = ["Rates"]

        assert axiom_entities.delete_entity("Alpha")[0]
        assert store["entity_options"] == ["Beta"]
        for map_key in (
            "entity_properties",
            "memorized_rules",
            "entity_code_scopes",
            "entity_property_aliases",
            "entity_account_codes",
        ):
            assert "Alpha" not in store[map_key], map_key

    def test_removes_sqlite_rows(self, entities_env):
        _seed_entity_rows("Alpha")
        _seed_entity_rows("Beta")

        assert axiom_entities.delete_entity("Alpha")[0]

        for table in ENTITY_TABLES:
            assert _count(table, "Alpha") == 0, table
            assert _count(table, "Beta") == 1, table
        assert _file_transaction_entities() == ["Beta"]


class TestAddNewTaxYear:
    @pytest.mark.parametrize("value", ["26", "abcd", "20260", "   "])
    def test_rejects_non_four_digit_year(self, entities_env, value):
        ok, msg = axiom_entities.add_new_tax_year(value)
        assert not ok
        assert "4-digit" in msg

    def test_existing_year_only_switches(self, entities_env):
        import streamlit as st

        before = list(st.session_state["tax_year_options"])
        ok, msg = axiom_entities.add_new_tax_year("2027")
        assert ok and "Switched" in msg
        assert st.session_state["active_tax_year"] == "2027"
        assert st.session_state["tax_year_options"] == before

    def test_appends_sorts_and_initialises_ledgers(self, entities_env):
        import streamlit as st

        assert axiom_entities.add_new_tax_year("2030")[0]
        assert st.session_state["tax_year_options"] == sorted(
            [*axiom_core.TAX_YEAR_OPTIONS, "2030"]
        )
        assert st.session_state["active_tax_year"] == "2030"
        for entity in ("Alpha", "Beta"):
            assert st.session_state["entity_year_ledgers"][entity]["2030"].empty

    def test_keeps_existing_ledgers_untouched(self, entities_env):
        import streamlit as st

        df = pd.DataFrame({"Amount": [1]})
        st.session_state["entity_year_ledgers"] = {"Alpha": {"2026": df}, "Beta": {}}
        assert axiom_entities.add_new_tax_year("2030")[0]
        assert st.session_state["entity_year_ledgers"]["Alpha"]["2026"] is df


def test_utils_reexports_same_objects():
    for name in ("add_new_entity", "rename_entity", "delete_entity", "add_new_tax_year"):
        assert getattr(utils, name) is getattr(axiom_entities, name)
