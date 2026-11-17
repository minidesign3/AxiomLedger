"""
DB round-trip tests for the Streamlit-free ``axiom_db`` persistence layer.

These tests import only ``axiom_db`` (never ``utils``), so they carry no
Streamlit or network dependency. Every test runs against a **temporary** SQLite
file created in a pytest ``tmp_path``; no real user/financial database
(``data/axiom_ledger.db``) is ever opened, read, or written — satisfying the
repository data-privacy rules in ``AGENTS.md``.
"""

from __future__ import annotations

import threading

import pandas as pd
import pytest

import axiom_db
from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Point axiom_db at an isolated temp SQLite file + vault, with a fresh schema guard."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(axiom_db, "DATA_DIR", data_dir)
    monkeypatch.setattr(axiom_db, "SQLITE_DB_PATH", data_dir / "axiom_ledger.db")
    monkeypatch.setattr(axiom_db, "VAULT_ROOT_DIR", data_dir / "vault")
    # Force schema (re)creation against the temp DB for this test.
    monkeypatch.setattr(axiom_db, "_SCHEMA_READY", threading.Event())
    # Fresh connection pool per test: avoids leaking/reusing a handle from a
    # previous test's (now-deleted) temp DB path.
    monkeypatch.setattr(axiom_db, "_CONN_CACHE", {})
    yield axiom_db


def _ledger_df(rows):
    return pd.DataFrame(rows, columns=STANDARD_COLUMNS)


class TestMasterLedgerRoundTrip:
    def test_save_then_load_preserves_rows_and_order(self, temp_db):
        df = _ledger_df(
            [
                ["2026-01-05", -1234.56, "Mortgage interest", "303 Weymouth", "Loan Interest"],
                ["2026-02-05", 2000.00, "Rent received", "33 Blampied", "Rental Income"],
                ["2026-03-05", -45.00, "Bank fee", UNASSIGNED_PROPERTY, "Uncategorized"],
            ]
        )
        temp_db._save_master_ledger_to_sqlite("BT Property Holdings Ltd", "2026", df)
        loaded = temp_db._load_master_ledger_from_sqlite("BT Property Holdings Ltd", "2026")

        assert list(loaded.columns) == STANDARD_COLUMNS
        pd.testing.assert_frame_equal(loaded.reset_index(drop=True), df.reset_index(drop=True))

    def test_save_is_full_replace(self, temp_db):
        first = _ledger_df([["2026-01-01", 10.0, "A", UNASSIGNED_PROPERTY, "Uncategorized"]])
        second = _ledger_df([["2026-01-02", 20.0, "B", UNASSIGNED_PROPERTY, "Uncategorized"]])
        temp_db._save_master_ledger_to_sqlite("E", "2026", first)
        temp_db._save_master_ledger_to_sqlite("E", "2026", second)
        loaded = temp_db._load_master_ledger_from_sqlite("E", "2026")
        assert len(loaded) == 1
        assert loaded.iloc[0]["Description"] == "B"

    def test_entities_and_years_are_isolated(self, temp_db):
        temp_db._save_master_ledger_to_sqlite(
            "E1", "2026", _ledger_df([["2026-01-01", 1.0, "x", UNASSIGNED_PROPERTY, "Uncategorized"]])
        )
        temp_db._save_master_ledger_to_sqlite(
            "E2", "2026", _ledger_df([["2026-01-01", 2.0, "y", UNASSIGNED_PROPERTY, "Uncategorized"]])
        )
        temp_db._save_master_ledger_to_sqlite(
            "E1", "2025", _ledger_df([["2025-01-01", 3.0, "z", UNASSIGNED_PROPERTY, "Uncategorized"]])
        )
        assert temp_db._load_master_ledger_from_sqlite("E1", "2026").iloc[0]["Amount"] == 1.0
        assert temp_db._load_master_ledger_from_sqlite("E2", "2026").iloc[0]["Amount"] == 2.0
        assert temp_db._load_master_ledger_from_sqlite("E1", "2025").iloc[0]["Amount"] == 3.0

    def test_empty_ledger_returns_empty_frame_with_schema(self, temp_db):
        loaded = temp_db._load_master_ledger_from_sqlite("Nobody", "1999")
        assert loaded.empty
        assert list(loaded.columns) == STANDARD_COLUMNS

    def test_none_cells_default_to_unassigned_and_uncategorized(self, temp_db):
        # Explicit None cells are coerced to the canonical defaults on save.
        df = _ledger_df([["2026-01-01", 5.0, "partial", None, None]])
        temp_db._save_master_ledger_to_sqlite("E", "2026", df)
        loaded = temp_db._load_master_ledger_from_sqlite("E", "2026")
        assert loaded.iloc[0]["Property"] == UNASSIGNED_PROPERTY
        assert loaded.iloc[0]["Account Code"] == "Uncategorized"


class TestFileRecordRoundTrip:
    def _record(self, file_id, name, df, **extra):
        rec = {
            "id": file_id,
            "name": name,
            "signature": f"{name}::{len(df)}",
            "bucket": "csv",
            "vault_path": "",
            "mime_type": "",
            "uploaded_at": "2026-01-01 00:00:00",
            "needs_ocr": False,
            "note": "",
            "df": df,
        }
        rec.update(extra)
        return rec

    def test_file_record_and_transactions_round_trip(self, temp_db):
        df = _ledger_df(
            [
                ["2026-01-05", -10.0, "Coffee", UNASSIGNED_PROPERTY, "Uncategorized"],
                ["2026-01-06", -20.0, "Fuel", UNASSIGNED_PROPERTY, "Motor Vehicle"],
            ]
        )
        rec = self._record("file_1", "statement.csv", df)
        temp_db._save_file_record_to_sqlite("E", "2026", rec)

        with temp_db._get_sqlite_conn() as conn:
            frow = conn.execute(
                "SELECT name, signature, bucket, sort_order FROM uploaded_files WHERE file_id = ?",
                ("file_1",),
            ).fetchone()
            tx = conn.execute(
                "SELECT tx_date, amount, description FROM file_transactions "
                "WHERE file_id = ? ORDER BY row_index ASC",
                ("file_1",),
            ).fetchall()
        assert frow["name"] == "statement.csv"
        assert frow["sort_order"] == 0
        assert len(tx) == 2
        assert tx[1]["description"] == "Fuel"

    def test_sort_order_is_persisted(self, temp_db):
        # Regression: _save_file_record_to_sqlite previously rejected sort_order=,
        # so the call crashed and was silently swallowed by `except: continue`.
        empty = pd.DataFrame(columns=STANDARD_COLUMNS)
        temp_db._save_file_record_to_sqlite("E", "2026", self._record("f_a", "a.csv", empty), sort_order=5)
        temp_db._save_file_record_to_sqlite("E", "2026", self._record("f_b", "b.csv", empty), sort_order=2)
        with temp_db._get_sqlite_conn() as conn:
            rows = conn.execute(
                "SELECT file_id FROM uploaded_files WHERE entity = ? AND tax_year = ? "
                "ORDER BY sort_order ASC",
                ("E", "2026"),
            ).fetchall()
        assert [r["file_id"] for r in rows] == ["f_b", "f_a"]

    def test_upsert_replaces_metadata_and_transactions(self, temp_db):
        df1 = _ledger_df([["2026-01-01", 1.0, "old", UNASSIGNED_PROPERTY, "Uncategorized"]])
        df2 = _ledger_df(
            [
                ["2026-02-01", 2.0, "new1", UNASSIGNED_PROPERTY, "Uncategorized"],
                ["2026-02-02", 3.0, "new2", UNASSIGNED_PROPERTY, "Uncategorized"],
            ]
        )
        temp_db._save_file_record_to_sqlite("E", "2026", self._record("f1", "v1.csv", df1))
        temp_db._save_file_record_to_sqlite(
            "E", "2026", self._record("f1", "v2.csv", df2, note="updated")
        )
        with temp_db._get_sqlite_conn() as conn:
            frow = conn.execute(
                "SELECT name, note FROM uploaded_files WHERE file_id = ?", ("f1",)
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM file_transactions WHERE file_id = ?", ("f1",)
            ).fetchone()["c"]
        assert frow["name"] == "v2.csv"
        assert frow["note"] == "updated"
        assert count == 2  # old single row replaced by two new rows

    def test_delete_removes_record_and_transactions(self, temp_db):
        df = _ledger_df([["2026-01-01", 1.0, "x", UNASSIGNED_PROPERTY, "Uncategorized"]])
        temp_db._save_file_record_to_sqlite("E", "2026", self._record("f_del", "d.csv", df))
        temp_db._delete_file_record_from_sqlite("f_del", delete_vault_file=False)
        with temp_db._get_sqlite_conn() as conn:
            frow = conn.execute(
                "SELECT 1 FROM uploaded_files WHERE file_id = ?", ("f_del",)
            ).fetchone()
            tx = conn.execute(
                "SELECT COUNT(*) AS c FROM file_transactions WHERE file_id = ?", ("f_del",)
            ).fetchone()["c"]
        assert frow is None
        assert tx == 0

    def test_record_without_id_is_ignored(self, temp_db):
        df = pd.DataFrame(columns=STANDARD_COLUMNS)
        temp_db._save_file_record_to_sqlite("E", "2026", self._record("", "noid.csv", df))
        with temp_db._get_sqlite_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM uploaded_files").fetchone()["c"]
        assert count == 0


class TestConnectionPooling:
    def test_repeated_calls_return_same_connection(self, temp_db):
        conn1 = temp_db._get_sqlite_conn()
        conn2 = temp_db._get_sqlite_conn()
        assert conn1 is conn2

    def test_different_db_path_gets_its_own_connection(self, temp_db, tmp_path, monkeypatch):
        conn_a = temp_db._get_sqlite_conn()
        other_dir = tmp_path / "other_data"
        monkeypatch.setattr(temp_db, "DATA_DIR", other_dir)
        monkeypatch.setattr(temp_db, "SQLITE_DB_PATH", other_dir / "axiom_ledger.db")
        monkeypatch.setattr(temp_db, "VAULT_ROOT_DIR", other_dir / "vault")
        conn_b = temp_db._get_sqlite_conn()
        assert conn_a is not conn_b

    def test_pooled_connection_survives_many_operations(self, temp_db):
        # Repeated save/load cycles must keep working against the single pooled
        # connection (no stale-transaction or "closed connection" errors).
        for i in range(5):
            df = _ledger_df([[f"2026-01-0{i + 1}", float(i), f"row{i}", UNASSIGNED_PROPERTY, "Uncategorized"]])
            temp_db._save_master_ledger_to_sqlite("E", "2026", df)
            loaded = temp_db._load_master_ledger_from_sqlite("E", "2026")
            assert loaded.iloc[0]["Description"] == f"row{i}"


class TestSchemaGuard:
    def test_schema_runs_once_then_short_circuits(self, temp_db):
        assert not temp_db._SCHEMA_READY.is_set()
        temp_db._ensure_sqlite_schema()
        assert temp_db._SCHEMA_READY.is_set()
        # Second call must be a no-op (idempotent).
        temp_db._ensure_sqlite_schema()
        with temp_db._get_sqlite_conn() as conn:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert {"kv_settings", "uploaded_files", "file_transactions", "master_ledgers"} <= tables
